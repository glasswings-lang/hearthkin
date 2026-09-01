# SPDX-License-Identifier: CC0-1.0

"""Search a kin's memory files for a query string.

`agent_name` is injected by the framework (see tools/__init__.py
load_tools `context` parameter) — it is NOT a model-controllable
input. The schema-builder hides it from the model-facing schema.

Ranking defaults to BM25 — files scored by how relevantly their
tokens match the query, top N returned. Rare terms weight higher
than common ones; longer files are penalized so a fleeting mention
in memory.md doesn't outrank a focused daily-log entry. Caller can
pass `mode="exact"` for substring AND-match instead (every query
word must appear somewhere in the file) — useful for function names,
commit hashes, and other exact-string lookups.

The BM25 implementation is `_BM25Okapi` defined inline below, rather
than imported from the `rank_bm25` package on PyPI. That package
depends on numpy, which would add ~50MB to the PyInstaller bundle
for a couple dozen multiplies we can do directly. ~40 lines of
`math` instead of a fat dep. Math is cross-checked against
rank_bm25 v0.2.2 for parity on the synthetic test corpus (see the
commit that introduced this rewrite for the diff against the
numpy-backed reference)."""

import math
import re
from collections import Counter
from pathlib import Path

from hearthkin_paths import kin_dir as kin_folder

from ._io import robust_read_text


# Filenames excluded from the search. soul.md is the persona prompt
# itself, not memory — including it would clutter every search for the
# kin's own name.
_EXCLUDED_FILES = {"soul.md"}


def _tokenize(text):
    """Lowercase + split on non-word characters. Same scheme on query and
    corpus — BM25 only ranks well when both ends agree on what counts as
    a token."""
    return re.findall(r"\w+", text.lower())


class _BM25Okapi:
    """Pure-Python BM25Okapi. Semantics match rank_bm25.BM25Okapi v0.2.2
    exactly: same IDF formula `log(N - df + 0.5) - log(df + 0.5)` with
    the same negative-IDF floor at `epsilon * average_idf`, same scoring
    per (term, doc) of `idf * tf * (k1+1) / (tf + k1 * (1 - b + b*dl/avgdl))`.

    Reference: Robertson & Zaragoza 2009, "The Probabilistic Relevance
    Framework: BM25 and Beyond." Defaults `k1=1.5`, `b=0.75`,
    `epsilon=0.25` mirror rank_bm25's defaults.

    Rewritten in-tree to avoid pulling numpy into the PyInstaller bundle.
    For a kin's ~50 markdown files this is microseconds per query; if
    the corpus ever grows past tens of thousands of docs we should
    reconsider, but at that point the whole memory-search approach
    probably wants rethinking too."""

    def __init__(self, corpus, k1=1.5, b=0.75, epsilon=0.25):
        self.k1 = k1
        self.b = b
        self.epsilon = epsilon
        self.corpus_size = len(corpus)
        self.doc_freqs = [Counter(doc) for doc in corpus]
        self.doc_lens = [len(doc) for doc in corpus]
        self.avgdl = (
            sum(self.doc_lens) / self.corpus_size if self.corpus_size else 0
        )
        # Document frequency: how many docs each term appears in.
        df = Counter()
        for doc in corpus:
            for term in set(doc):
                df[term] += 1
        # IDF per term, with negative-IDF floor. Over-common terms (in
        # >half the docs) would get negative scores otherwise; floor them
        # at epsilon * average_idf so they contribute a small positive
        # signal instead of subtracting from the score.
        self.idf = {}
        idf_sum = 0.0
        negative_idfs = []
        for term, freq in df.items():
            idf = math.log(self.corpus_size - freq + 0.5) - math.log(freq + 0.5)
            self.idf[term] = idf
            idf_sum += idf
            if idf < 0:
                negative_idfs.append(term)
        avg_idf = idf_sum / len(self.idf) if self.idf else 0
        eps = self.epsilon * avg_idf
        for term in negative_idfs:
            self.idf[term] = eps

    def get_scores(self, query):
        scores = [0.0] * self.corpus_size
        for term in query:
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i, doc_freq in enumerate(self.doc_freqs):
                tf = doc_freq.get(term, 0)
                if tf == 0:
                    continue
                doc_len = self.doc_lens[i]
                denom = tf + self.k1 * (1 - self.b + self.b * doc_len / self.avgdl)
                scores[i] += idf * tf * (self.k1 + 1) / denom
        return scores


def memory_search(
    query: str,
    max_results: int = 5,
    mode: str = "smart",
    agent_name: str = "",
) -> str:
    """Search this kin's memory files for `query` and return ranked hits using BM25 relevance scoring. Use this BEFORE answering when you need to recall a specific name, date, decision, or topic that may be in your memory — much cheaper than scanning whole files inline. Pass `mode="exact"` for substring AND-match (function names, unique IDs, exact phrases); default `mode="smart"` ranks by BM25 relevance.

    Searches every `.md` and `.txt` file under your kin directory
    recursively (memory.md at the root, anything in memory/, anything
    in journal/, plus root-level files like AGENTS.md and USER.md, and
    any plain-text logs you've written). Your persona prompt (soul.md)
    is excluded so searches for your own name don't clutter on it.

    Modes:
      - "smart" (default): BM25 ranking. Best for natural-language
        queries like "the conversation about voice compatibility" or
        "what we decided about the backup schedule."
      - "exact": substring AND-match. Every query word must appear
        somewhere in the file (case-insensitive). Best for unique
        strings like `_flush` or commit hashes.

    Returns at most `max_results` files. Each result is formatted as
    `<relpath>:<lineno>: <context line>`, where the context line is the
    one containing the most distinct query tokens. Returns a brief
    "no matches" message when nothing hits.
    """
    if not query:
        return "memory_search: query was empty."
    if not agent_name:
        return "memory_search: no kin context (framework bug)."

    kin_dir = kin_folder(agent_name)
    if not kin_dir.exists():
        return f"memory_search: no memory directory for kin {agent_name!r}."

    # Index .md AND .txt — plain-text logs a kin writes (a person log,
    # an evidence log) are otherwise invisible to its own search, which
    # makes them look dropped even though the bytes are on disk.
    targets = sorted(
        p
        for ext in ("*.md", "*.txt")
        for p in kin_dir.rglob(ext)
        if p.name not in _EXCLUDED_FILES
    )
    if not targets:
        return f"memory_search: {agent_name!r} has no memory files yet."

    cap = max(1, int(max_results)) if isinstance(max_results, (int, float)) else 5

    # Read files once; both ranking paths reuse the same text.
    # robust_read_text falls back through UTF-8 → cp1252 → UTF-8-replace
    # so Windows smart characters don't take out the indexer.
    file_texts = []
    for path in targets:
        try:
            file_texts.append((path, robust_read_text(path)))
        except Exception:
            continue
    if not file_texts:
        return f"memory_search: no readable memory files for {agent_name!r}."

    if mode == "exact":
        hits = _rank_substring(file_texts, query, kin_dir, cap)
    else:
        hits = None
        if _semantic_enabled():
            # Semantic rerank: BM25 narrows to candidates, embeddings
            # reorder them by meaning. Returns None (→ BM25 fallback) if
            # embeddings are unavailable for any reason, so search never
            # breaks just because the embed model/host isn't set up.
            hits = _rank_semantic(
                file_texts, query, kin_dir, cap, _embed_model())
        if hits is None:
            hits = _rank_bm25(file_texts, query, kin_dir, cap)

    if not hits:
        suffix = "" if mode != "exact" else (
            " — every search word must appear in a file in exact mode; "
            "try fewer words or drop mode='exact' for fuzzy ranking."
        )
        return (
            f"memory_search: no matches for {query!r} across "
            f"{len(file_texts)} memory file(s).{suffix}"
        )
    return "\n".join(hits)


def _rank_bm25(file_texts, query, kin_dir, cap):
    """BM25 ranking. Returns top-`cap` files by relevance score, filtered
    to strictly positive scores (a zero score means no query token
    appears in the file at all)."""
    corpus_tokens = [_tokenize(text) for _, text in file_texts]
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []
    bm25 = _BM25Okapi(corpus_tokens)
    scores = bm25.get_scores(query_tokens)
    ranked = sorted(
        (
            (scores[i], file_texts[i][0], file_texts[i][1])
            for i in range(len(file_texts))
        ),
        key=lambda x: x[0],
        reverse=True,
    )
    hits = []
    for score, path, text in ranked:
        if score <= 0:
            break
        hits.append(_pick_context_line(text, query_tokens, path, kin_dir))
        if len(hits) >= cap:
            break
    return hits


def _rank_substring(file_texts, query, kin_dir, cap):
    """Substring AND match — `mode="exact"`. Every query word must appear
    somewhere in the file."""
    words = [w for w in query.lower().split() if w]
    if not words:
        return []
    hits = []
    for path, text in file_texts:
        text_lower = text.lower()
        if not all(w in text_lower for w in words):
            continue
        hits.append(_pick_context_line(text, words, path, kin_dir))
        if len(hits) >= cap:
            break
    return hits


def _pick_context_line(text, query_tokens, path, kin_dir):
    """Pick the line with the most distinct query tokens hit on it.
    Ties go to the earliest line. Falls back to a bare match-confirmation
    string if no line hits any token (can happen when BM25 ranks a file
    on token frequency but tokens are split across lines)."""
    query_set = set(query_tokens)
    if not query_set:
        return f"{path.relative_to(kin_dir).as_posix()}: (match found)"
    best_line = None
    best_score = 0
    best_lineno = None
    full_score = len(query_set)
    for i, line in enumerate(text.splitlines(), 1):
        line_lower = line.lower()
        score = sum(1 for tok in query_set if tok in line_lower)
        if score > best_score:
            best_score = score
            best_line = line
            best_lineno = i
            if score == full_score:
                break
    if best_line is None:
        return f"{path.relative_to(kin_dir).as_posix()}: (match found)"
    stripped = best_line.strip()
    if len(stripped) > 200:
        stripped = stripped[:200] + "..."
    return f"{path.relative_to(kin_dir).as_posix()}:{best_lineno}: {stripped}"


# ─── Semantic rerank (optional; Ollama embeddings) ─────────────────────────────
#
# Hybrid design: BM25 narrows to candidate files, those files are chunked,
# and the query + chunks are embedded so cosine similarity reorders by
# MEANING rather than shared keywords. Off by default (app-level config
# "semantic_memory"); falls back to plain BM25 on any embedding failure.
# Pure-Python cosine keeps the no-numpy promise the BM25 path already makes
# — for a kin's few-hundred chunks it's sub-millisecond.

# How many top-BM25 files feed the reranker, and the hard ceiling on chunks
# embedded per query (bounds latency + embed cost over the network). For a
# kin with <= _SEMANTIC_CANDIDATE_FILES files this is effectively full
# semantic search; for a large corpus it stays bounded.
_SEMANTIC_CANDIDATE_FILES = 25
_SEMANTIC_MAX_CHUNKS = 200
_SEMANTIC_CHUNK_CHARS = 600


def _semantic_enabled():
    """App-level toggle (Preferences → Connections). Lazy import so this
    tool module never imports the data layer at load time."""
    try:
        from kin_persistence import CONFIG_FILE, DEFAULT_CONFIG, load_json
        return bool(load_json(CONFIG_FILE, DEFAULT_CONFIG).get("semantic_memory"))
    except Exception:
        return False


def _embed_model():
    try:
        from kin_persistence import CONFIG_FILE, DEFAULT_CONFIG, load_json
        model = load_json(CONFIG_FILE, DEFAULT_CONFIG).get("embed_model")
        return model or "nomic-embed-text"
    except Exception:
        return "nomic-embed-text"


def _embed_keep_alive():
    """App-level keep-alive for the embedding model (`embed_keep_alive_min`,
    Preferences → semantic memory). Returns an Ollama keep_alive value so the
    embed model isn't cold-reloaded on every per-turn recall: -1 (pin
    forever), "<N>m" (N minutes), or None (0 / blank → send nothing, use the
    server default)."""
    try:
        from kin_persistence import CONFIG_FILE, DEFAULT_CONFIG, load_json
        val = int(load_json(CONFIG_FILE, DEFAULT_CONFIG).get(
            "embed_keep_alive_min", 30) or 0)
    except Exception:
        return None
    if val < 0:
        return -1
    if val == 0:
        return None
    return f"{val}m"


def _embed_host():
    """Resolved URL of the machine that runs embeddings (app-level
    `embed_host`), or "" for localhost. Threaded into embed_texts so
    semantic search embeds on the chosen box."""
    try:
        from kin_persistence import (
            CONFIG_FILE, DEFAULT_CONFIG, load_json, resolve_kin_ollama_host)
        raw = load_json(CONFIG_FILE, DEFAULT_CONFIG).get("embed_host", "")
        return resolve_kin_ollama_host(raw) or ""
    except Exception:
        return ""


def _embed_timeout():
    """App-level embedding-call timeout in SECONDS (`embed_timeout_secs`,
    Preferences → Connections). Threaded into embed_texts alongside the
    host/model so a slow / unreachable embed host degrades to keyword
    search instead of hanging. Returns None on 0 / blank / bad value, so
    embed_texts falls back to its own built-in default."""
    try:
        from kin_persistence import CONFIG_FILE, DEFAULT_CONFIG, load_json
        val = float(load_json(CONFIG_FILE, DEFAULT_CONFIG).get(
            "embed_timeout_secs", 0) or 0)
        return val if val > 0 else None
    except Exception:
        return None


def _cosine(a, b):
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / math.sqrt(na * nb)


def _first_line(text):
    """First non-empty line of a chunk, for the result label."""
    for line in text.splitlines():
        s = line.strip()
        if s:
            return s[:200] + "..." if len(s) > 200 else s
    return "(match found)"


def _chunk_text(text, max_chars=_SEMANTIC_CHUNK_CHARS):
    """Split into chunks on blank-line (paragraph) boundaries, packing
    paragraphs up to ~max_chars. Returns (start_lineno, chunk_text) pairs.
    Embeddings are sharper on a paragraph than on a whole 5 KB file, and the
    line number lets the result point at the right place. An oversized lone
    paragraph is hard-split on a char window so the embed input stays
    bounded."""
    paras = []  # (start_lineno, text)
    buf = []
    buf_start = 1
    for i, line in enumerate(text.splitlines(), 1):
        if line.strip() == "":
            if buf:
                paras.append((buf_start, "\n".join(buf).strip()))
                buf = []
            buf_start = i + 1
        else:
            if not buf:
                buf_start = i
            buf.append(line)
    if buf:
        paras.append((buf_start, "\n".join(buf).strip()))

    chunks = []  # (start_lineno, text)
    cur = []
    cur_start = None
    cur_len = 0
    for start, ptext in paras:
        if not ptext:
            continue
        if len(ptext) > max_chars:
            if cur:
                chunks.append((cur_start, "\n\n".join(cur)))
                cur, cur_start, cur_len = [], None, 0
            for j in range(0, len(ptext), max_chars):
                chunks.append((start, ptext[j:j + max_chars]))
            continue
        if cur and cur_len + len(ptext) > max_chars:
            chunks.append((cur_start, "\n\n".join(cur)))
            cur, cur_start, cur_len = [], None, 0
        if not cur:
            cur_start = start
        cur.append(ptext)
        cur_len += len(ptext) + 2
    if cur:
        chunks.append((cur_start, "\n\n".join(cur)))
    return chunks


def _rank_semantic(file_texts, query, kin_dir, cap, embed_model):
    """Rerank candidates by embedding similarity. Returns top-`cap` chunk
    hits, or None when embeddings are unavailable (caller falls back to
    BM25). Never raises — None is the universal "use keywords instead."""
    try:
        from llm_backend import embed_texts  # lazy: avoid load-time cycle
    except Exception:
        return None

    query_tokens = _tokenize(query)
    if not query_tokens:
        return None

    # Candidate files by BM25 (top-K). Small corpus → all files → full
    # semantic; large corpus → bounded embed batch.
    corpus_tokens = [_tokenize(t) for _, t in file_texts]
    bm25 = _BM25Okapi(corpus_tokens)
    scores = bm25.get_scores(query_tokens)
    order = sorted(range(len(file_texts)),
                   key=lambda i: scores[i], reverse=True)
    candidates = [file_texts[i] for i in order[:_SEMANTIC_CANDIDATE_FILES]]

    chunks = []  # (path, lineno, text)
    for path, text in candidates:
        for lineno, ctext in _chunk_text(text):
            chunks.append((path, lineno, ctext))
            if len(chunks) >= _SEMANTIC_MAX_CHUNKS:
                break
        if len(chunks) >= _SEMANTIC_MAX_CHUNKS:
            break
    if not chunks:
        return None

    vecs = embed_texts([query] + [c[2] for c in chunks], embed_model,
                       host=_embed_host() or None, timeout=_embed_timeout(),
                       keep_alive=_embed_keep_alive())
    if not vecs or len(vecs) != len(chunks) + 1:
        return None  # embeddings unavailable → BM25 fallback
    qvec = vecs[0]

    scored = sorted(
        (
            (_cosine(qvec, vecs[idx + 1]), path, lineno, ctext)
            for idx, (path, lineno, ctext) in enumerate(chunks)
        ),
        key=lambda x: x[0],
        reverse=True,
    )

    hits = []
    seen = set()
    for sim, path, lineno, ctext in scored:
        if sim <= 0:
            break
        rel = path.relative_to(kin_dir).as_posix()
        label = f"{rel}:{lineno}: {_first_line(ctext)}"
        if label in seen:
            continue
        seen.add(label)
        hits.append(label)
        if len(hits) >= cap:
            break
    return hits or None
