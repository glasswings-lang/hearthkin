# SPDX-License-Identifier: CC0-1.0

"""Per-turn memory retrieval — close the depth gap.

Before each send, a conversational surface calls `build_recall_block()` to get a
small, scored, budgeted slice of the kin's own depth logs + journals, framed as a
system note and spliced in right before the latest user turn. No tool call: the
depth surfaces whether or not the model would ever think to `memory_search` for
it. See docs/design/per-turn-memory-retrieval.md for the full design.

The engine is deliberately layered so it always works:
  * BM25 (lexical) + recency + salience + diversity + budget is the BASE — pure
    Python, no network, fast, always available.
  * Semantic rerank (embedding cosine) is an OPTIONAL enhancement, gated on the
    app `semantic_memory` flag and fail-soft to BASE on any embedding problem.

Nothing here is persisted into conversation history — the block is regenerated
fresh each turn (like `staging_status_line`), so it can never compound or poison
the record the way a saved cascade would.

Reuses the primitives already written for `memory_search` (the BM25 impl, the
tokenizer, the paragraph chunker, the pure-Python cosine) rather than
duplicating them.
"""

import hashlib
import json
import math
import re
import time
from pathlib import Path

from hearthkin_paths import kin_dir as kin_folder

from tools.memory_search import _BM25Okapi, _tokenize, _chunk_text, _cosine
from tools._io import robust_read_text

# Estimate ~4 chars/token (matches the codebase's _est_tokens convention).
_CHARS_PER_TOKEN = 4
# Recency multiplier floor: an ancient log still counts, just less. Keeps recency
# a tiebreaker (per the design's "slight lean to relevance"), not a dominator.
_RECENCY_FLOOR = 0.6
_RECENCY_HALFLIFE_DAYS = 45.0
# Salience neutral point: a file with no rating scores as if rated 5/10.
_SALIENCE_NEUTRAL = 5.0
_SALIENCE_MIN_MULT = 0.5
_SALIENCE_MAX_MULT = 1.6
_BOOST_MULT = 1.5
# Drop chunks whose relevance is below this fraction of the top hit — a chunk
# that only matched a stopword ("about", "the") scores a hair above zero, and
# letting it in just dilutes the block. Surface the strong handful, not
# everything nonzero.
#
# RELATIVE ONLY, and that was the whole problem: it is measured against the top
# hit of THIS turn, so when nothing matches, the top hit is itself a weak match
# and the bar drops with it. Every scorer here normalizes to the top hit, so the
# engine had no way to express "nothing relevant" and never did. Replayed over
# 40 real turns each across several kin, it surfaced memory on 120 of 120 — a
# quota, not retrieval. See `_eligible_by_live_message` for the absolute gate
# that now sits in front of it.
_RELEVANCE_FLOOR = 0.18
# Bound the embed batch over the network even on a big corpus.
_MAX_CHUNKS = 240

# --- The absolute gate: does this chunk match the message actually in front of
# --- the kin? Scale-free on purpose, so it means the same thing to a kin with
# --- seven notes and a kin with three hundred.
#
# A chunk qualifies only if it shares at least _MIN_LIVE_OVERLAP distinct content
# words with the live user turn, at least one of which is DISTINCTIVE in this
# kin's own corpus (appears in no more than _DISTINCTIVE_DF_FRAC of its chunks).
# Both halves are load-bearing. Overlap alone lets a note in on two words the
# kin says constantly — its own name, the person's name — which is how a message
# on one subject pulls up a note about something unrelated. Distinctiveness alone
# lets a single rare word carry a whole note in.
#
# Scored against the LIVE TURN, not the rolling query. The query deliberately
# spans the last few turns for ranking, and that is right for ranking — but it
# means words from two turns ago can qualify a note for a message that has
# nothing to do with it. Rank on context; qualify on what was just said.
#
# These are FALLBACKS, not the setting. Both are per-kin config
# (`recall_min_overlap` / `recall_distinctive_frac`) and both are on the recall
# settings screen, because how hard a note works to earn its place is a
# judgement about a particular kin's memory, not a fact about retrieval. A kin
# whose notes are all one subject wants a looser gate than one with three
# hundred logs. These values are only what applies when nobody has said.
_MIN_LIVE_OVERLAP = 2
_DISTINCTIVE_DF_FRAC = 0.34

# The block is also bounded by the SIZE OF THE MESSAGE IT ACCOMPANIES, not only
# by the share of context the person allotted. `recall_budget_pct` is a share of
# num_ctx — for a 32k kin that is ~5,900 tokens, which is larger than some kin's
# entire memory folder, so it never binds and the item cap becomes the only
# limit. That is how a 13-character message can arrive behind 3,087 characters
# of memory: 237 times as much reference as message. A kin
# describing that back is not misreading anything, it is answering the bulk of
# what it was handed.
#
# The floor keeps a genuinely short question answerable: a few words can still
# deserve the note they match.
_MIN_BLOCK_CHARS = 500

# Stopwords stripped from the QUERY before lexical scoring. memory_search leaves
# these in (it leans on BM25's IDF to down-weight them, fine for a search the
# kin deliberately ran), but auto-recall injects without the kin choosing, so a
# false match bridged by "about"/"the"/"tell" is more costly. Strip them so
# retrieval keys on content words. If a query is ALL stopwords we fall back to
# the unfiltered tokens so it still does something.
_STOPWORDS = frozenset((
    "a an the this that these those and or but if then else so as of to in on "
    "at by for with from into onto over under is are was were be been being am "
    "do does did done have has had having i me my mine you your yours we us our "
    "he him his she her it its they them their what which who whom whose when "
    "where why how all any both each few more most other some such no nor not "
    "only own same than too very can will just should now about up down out off "
    "again here there tell told say said me'd let lets like really thing things "
    "would could get got going go want know think feel feeling"
).split())
_MAX_PER_SOURCE = 2  # diversity: one log can't hog the block


def _kin_memory_dir(kin_name, kin_dir=None):
    base = Path(kin_dir) if kin_dir else kin_folder(kin_name)
    return base / "memory"


def _load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path, obj):
    try:
        Path(path).write_text(json.dumps(obj), encoding="utf-8")
    except Exception:
        pass


def _gather_corpus(mem_dir):
    """Depth logs + journals: every .md/.txt under the kin's memory/ folder.
    Deliberately excludes the root-level memory.md (the always-loaded index)
    and soul.md (always-loaded identity) — both live ABOVE memory/, so the
    rglob never sees them. Also skips our own dotfiles (salience/embed cache)."""
    out = []
    if not mem_dir.exists():
        return out
    for ext in ("*.md", "*.txt"):
        for p in mem_dir.rglob(ext):
            if p.name.startswith("."):
                continue
            try:
                out.append((p, robust_read_text(p)))
            except Exception:
                continue
    return out


def _build_query(recent_messages, speaker_names=()):
    """The query is 'what are we talking about right now' — the last few turns,
    with the most recent USER turn weighted (repeated once) because it's the
    strongest signal of what to recall.

    Harness prefixes come off here too, not only at the gate. Left on, every
    turn's query carries the person's name and the date, so RANKING is pulled
    toward whichever log mentions them most and toward date-stamped journal
    entries — a quieter version of the same fault, and one that would have
    survived fixing only the gate."""
    tail = [m for m in (recent_messages or [])
            if isinstance(m, dict) and isinstance(m.get("content"), str)][-3:]
    parts = [_strip_harness_prefix(m["content"], speaker_names) for m in tail]
    last_user = next((_strip_harness_prefix(m["content"], speaker_names)
                      for m in reversed(tail)
                      if m.get("role") == "user"), None)
    if last_user:
        parts.append(last_user)  # weight the live ask
    return "\n".join(parts).strip()


def _live_message(recent_messages, speaker_names=()):
    """The turn the kin is actually replying to — the last user message.

    Separate from `_build_query` on purpose. The query spans the last few turns
    because that is the better ranking signal; this is the narrower question of
    what was just said, and it is what a note has to earn its place against.
    """
    for m in reversed(recent_messages or []):
        if isinstance(m, dict) and m.get("role") == "user" \
                and isinstance(m.get("content"), str) and m["content"].strip():
            return _strip_harness_prefix(m["content"], speaker_names)
    return ""


# The harness's own prefix on a user turn: "[YYYY-MM-DD HH:MM] [Name] text".
# Built by chat_helpers.format_ts_prefix + speaker_attribution_prefix, and it
# is NOT something the person said -- it is bookkeeping this app stapled on.
_TS_PREFIX_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?\]\s*")
_NAME_PREFIX_RE = re.compile(r"^\[[^\]\n]{1,80}\]\s*")


def _strip_harness_prefix(text, speaker_names=()):
    """Remove the timestamp + speaker brackets THIS APP puts on a user turn.

    Matches supplied names, never a bracket pattern — the same rule
    `chat_helpers.strip_leading_named_speaker` follows, and for the same
    reason. A bracket at the start of a message is not reliably ours. The
    person writing may be a plural system announcing who is fronting, and
    `[SpeakerSeven] I settle to your left` is that person's own words. An earlier
    draft here stripped whatever bracket followed a timestamp, which was fine
    on Telegram (the app always supplies a name there, so the first bracket
    really was ours) and wrong in a room with no user name set in Preferences:
    the timestamp was ours, the next bracket was theirs, and it went.

    Only the harness's own bracket is dropped, and only when its contents
    match a name the CALLER says it announced this turn. The timestamp is
    unambiguous — a date is never someone's name — so that always goes.

    This is why a kin in a Telegram group had one particular note attached to
    EVERY message. Telegram inlines attribution into the content itself, so the
    live turn the gate scored was not the sentence the person typed — it was
    "[2026-08-07 20:06] [Name] " with that sentence appended.
    The person's own name is in every single turn, and the depth log about that
    person is full of it, so the note qualified on every message regardless of
    what the message was about. Measured: 820 characters of note attached to a
    173-character line, 4.7 to 1.

    It is surface-specific, which is why it looked like one kin's private
    affliction: the desktop only inlines a name when the turn actually carries
    one, so the same kin was fine there and only the group surface did it. Any
    future surface that decorates a user turn owes this the same treatment —
    the gate must read what the PERSON said, never what we wrapped it in.

    This only ever affects what the MATCHER reads. What the model is sent is
    untouched, so a name that isn't stripped here costs a missed match, never
    a word taken out of anyone's mouth.
    """
    s = text or ""
    m = _TS_PREFIX_RE.match(s)
    if not m:
        return s
    s = s[m.end():]
    m2 = _NAME_PREFIX_RE.match(s)
    if m2 and speaker_names:
        inner = m2.group(0).strip()[1:-1].strip().casefold()
        for n in speaker_names:
            if n and inner == str(n).strip().casefold():
                return s[m2.end():]
    return s


def _content_tokens(text):
    """Content words only: no stopwords, nothing shorter than three characters.
    Stricter than the query tokenizer (which keeps two-character tokens) because
    a two-character coincidence is not evidence a note belongs in someone's
    conversation."""
    return set(t for t in _tokenize(text or "")
               if t not in _STOPWORDS and len(t) > 2)


def _eligible_by_live_message(chunk_token_sets, live_text,
                              min_overlap=None, distinctive_frac=None):
    """Indices of chunks that genuinely match the live turn — the absolute gate.

    Returns an empty set when nothing does, and an empty set is a real answer:
    most messages are not about anything in memory, and the correct amount of
    recalled memory for them is none. Everything downstream is relative scoring,
    which can rank but can never decline.

    `min_overlap` / `distinctive_frac` come from the kin's config; the module
    constants are only the fallback when a caller passes nothing.
    """
    try:
        min_overlap = max(1, int(min_overlap))
    except (TypeError, ValueError):
        min_overlap = _MIN_LIVE_OVERLAP
    try:
        distinctive_frac = float(distinctive_frac)
        if not 0.0 < distinctive_frac <= 1.0:
            raise ValueError
    except (TypeError, ValueError):
        distinctive_frac = _DISTINCTIVE_DF_FRAC
    live = _content_tokens(live_text)
    if not live:
        return set()
    n = len(chunk_token_sets)
    if not n:
        return set()
    df = {}
    for ts in chunk_token_sets:
        for t in ts:
            df[t] = df.get(t, 0) + 1
    # "Distinctive" is relative to this kin's own corpus, so the gate means the
    # same thing at 13 chunks and at 3,176.
    #
    # ROUNDED UP, and that matters only at the small end, where it decides
    # whether a young kin has working memory at all. Rounding down, a four-chunk
    # corpus admits a term only if it appears in exactly one chunk — so the very
    # words such a kin's few notes are ABOUT are all disqualified for being in
    # two of them, and recall stays silent forever. A kin with three notes is
    # precisely the kin who cannot afford that.
    distinctive_max = max(1, math.ceil(n * distinctive_frac))
    out = set()
    for i, ts in enumerate(chunk_token_sets):
        shared = live & ts
        if len(shared) < min_overlap:
            continue
        if any(df.get(t, 0) <= distinctive_max for t in shared):
            out.add(i)
    return out


def _recency_mult(path, now):
    try:
        age_days = max(0.0, (now - path.stat().st_mtime) / 86400.0)
    except Exception:
        return 1.0
    # Exponential-ish decay toward the floor.
    decay = 0.5 ** (age_days / _RECENCY_HALFLIFE_DAYS)
    return _RECENCY_FLOOR + (1.0 - _RECENCY_FLOOR) * decay


def _salience_mult(relpath, salience):
    score = salience.get(relpath, _SALIENCE_NEUTRAL)
    try:
        score = float(score)
    except Exception:
        score = _SALIENCE_NEUTRAL
    mult = score / _SALIENCE_NEUTRAL
    return max(_SALIENCE_MIN_MULT, min(_SALIENCE_MAX_MULT, mult))


def _is_journal(relpath):
    """A dated daily entry under memory/journal/, which auto-recall leaves
    alone unless a kin's config asks for it.

    Matches the FOLDER, not the word. A depth log called `journalling.md` is a
    depth log and stays eligible; only what the cron wake-up writes into
    memory/journal/ is covered.
    """
    return relpath.startswith("journal/") or "/journal/" in relpath


def _matches_any(relpath, patterns):
    rp = relpath.lower()
    return any(pat and pat.lower() in rp for pat in (patterns or ()))


def _normalize(scores):
    hi = max(scores) if scores else 0.0
    if hi <= 0:
        return [0.0] * len(scores)
    return [s / hi for s in scores]


def _semantic_scores(query, chunk_texts, embed_model, cache_path,
                     embed_timeout=None):
    """Cosine of query vs each chunk, with a disk embedding cache keyed by
    content hash (so unchanged logs aren't re-embedded every turn). Returns a
    list aligned to chunk_texts, or None on any failure (caller stays BM25)."""
    try:
        from llm_backend import embed_texts
    except Exception:
        return None
    try:
        from tools.memory_search import _embed_host
        _host = _embed_host() or None
    except Exception:
        _host = None
    cache = _load_json(cache_path, {})
    if not isinstance(cache, dict):
        cache = {}
    keys = [hashlib.sha1(t.encode("utf-8", "replace")).hexdigest()
            for t in chunk_texts]
    missing = [t for t, k in zip(chunk_texts, keys) if k not in cache]
    # Embed the query (always fresh) + any uncached chunks in one call.
    to_embed = [query] + missing
    vecs = embed_texts(to_embed, embed_model, host=_host, timeout=embed_timeout,
                       keep_alive=_embed_keep_alive())
    if not vecs or len(vecs) != len(to_embed):
        return None
    qvec = vecs[0]
    for t, v in zip(missing, vecs[1:]):
        cache[hashlib.sha1(t.encode("utf-8", "replace")).hexdigest()] = v
    _save_json(cache_path, cache)
    out = []
    for k in keys:
        v = cache.get(k)
        out.append(_cosine(qvec, v) if v else 0.0)
    return out


def build_recall_block(
    kin_name,
    recent_messages,
    *,
    budget_tokens,
    max_items=6,
    fence=(),
    boost=(),
    semantic=False,
    embed_model="nomic-embed-text",
    embed_timeout=None,
    kin_dir=None,
    frame=None,
    min_overlap=None,
    distinctive_frac=None,
    min_block_chars=None,
    speaker_names=(),
    include_journals=False,
):
    """Return (block_text_or_None, used_items).

    `block_text` is a framed system-note string to splice in before the latest
    user turn, or None if nothing relevant fit. `used_items` is a list of
    {relpath, lineno, score, snippet} for the "what surfaced" readout.

    Pure except for the optional embedding call (gated by `semantic`) and the
    embed-cache file. Never raises — any failure returns (None, []).

    Every dial is the caller's job to resolve from per-kin config (the surface
    knows num_ctx); keeping them as args makes the engine testable without
    touching config, and keeps the thresholds out of the code. Passing None for
    the last three uses the module fallbacks.
    """
    try:
        return _build_recall_block_inner(
            kin_name, recent_messages, budget_tokens, max_items,
            fence, boost, semantic, embed_model, embed_timeout, kin_dir, frame,
            min_overlap, distinctive_frac, min_block_chars, speaker_names,
            include_journals)
    except Exception:
        # Fail soft: a memory-retrieval bug must never break a reply.
        return None, []


def _build_recall_block_inner(kin_name, recent_messages, budget_tokens,
                              max_items, fence, boost, semantic, embed_model,
                              embed_timeout, kin_dir, frame,
                              min_overlap=None, distinctive_frac=None,
                              min_block_chars=None, speaker_names=(),
                              include_journals=False):
    if not budget_tokens or budget_tokens <= 0:
        return None, []
    mem_dir = _kin_memory_dir(kin_name, kin_dir)
    query = _build_query(recent_messages, speaker_names)
    if not query:
        return None, []
    corpus = _gather_corpus(mem_dir)
    if not corpus:
        return None, []

    # Chunk every file; carry source path + line number.
    chunks = []  # (path, lineno, text)
    for path, text in corpus:
        for lineno, ctext in _chunk_text(text):
            if ctext.strip():
                chunks.append((path, lineno, ctext))
            if len(chunks) >= _MAX_CHUNKS:
                break
        if len(chunks) >= _MAX_CHUNKS:
            break
    if not chunks:
        return None, []

    chunk_tokens = [_tokenize(c[2]) for c in chunks]

    # The absolute gate, BEFORE any scoring: a note has to match the message the
    # kin is replying to. Nothing qualifying is a legitimate outcome — most
    # messages are not about anything in memory — and it is the only point in
    # this engine that can say so. Everything below it is relative, and relative
    # scoring always fills its quota.
    live = _live_message(recent_messages, speaker_names)
    eligible = _eligible_by_live_message(
        [set(t) for t in chunk_tokens], live,
        min_overlap=min_overlap, distinctive_frac=distinctive_frac)
    if not eligible:
        return None, []

    query_tokens = _tokenize(query)
    if not query_tokens:
        return None, []
    # Drop stopwords + single chars (the stray "s" from "SpeakerFifteen's"). Fall back
    # to the raw tokens if that empties the query.
    meaningful = [t for t in query_tokens
                  if t not in _STOPWORDS and len(t) > 1]
    if meaningful:
        query_tokens = meaningful

    # Lexical base score (always available). Scored over the whole corpus, not
    # just the eligible chunks, so BM25's statistics and the relative floor keep
    # meaning what they meant — the gate decides who may be picked, not what the
    # numbers are.
    bm25 = _BM25Okapi(chunk_tokens)
    lex = _normalize(bm25.get_scores(query_tokens))

    # Optional semantic rerank, blended 50/50 when available.
    base = lex
    if semantic:
        sem = _semantic_scores(
            query, [c[2] for c in chunks], embed_model,
            mem_dir / ".recall_embeds.json", embed_timeout)
        if sem is not None:
            sem = _normalize(sem)
            base = [0.5 * l + 0.5 * s for l, s in zip(lex, sem)]

    salience = _load_json(mem_dir / ".salience.json", {})
    if not isinstance(salience, dict):
        salience = {}
    now = time.time()

    base_max = max(base) if base else 0.0
    floor_val = _RELEVANCE_FLOOR * base_max
    scored = []
    for i, ((path, lineno, ctext), b) in enumerate(zip(chunks, base)):
        if i not in eligible:
            continue  # did not match the live turn — see _eligible_by_live_message
        if b <= 0 or b < floor_val:
            continue
        try:
            rel = path.relative_to(mem_dir).as_posix()
        except Exception:
            rel = path.name
        if not include_journals and _is_journal(rel):
            continue  # see _is_journal
        if _matches_any(rel, fence):
            continue  # fenced out of auto-recall by hand
        mult = _recency_mult(path, now) * _salience_mult(rel, salience)
        if _matches_any(rel, boost):
            mult *= _BOOST_MULT
        scored.append((b * mult, rel, lineno, ctext))
    if not scored:
        return None, []
    scored.sort(key=lambda x: x[0], reverse=True)

    # Select within budget + item cap + per-source diversity + the size of the
    # message itself. The last of those is the one that keeps the person's words
    # the substance of their own turn: the configured budget is a share of
    # num_ctx, which for a kin whose whole memory is smaller than that share
    # never binds at all.
    try:
        floor_chars = max(0, int(min_block_chars))
    except (TypeError, ValueError):
        floor_chars = _MIN_BLOCK_CHARS
    char_cap = max(floor_chars, len(live))
    used = []
    per_source = {}
    spent = 0
    spent_chars = 0
    for score, rel, lineno, ctext in scored:
        if len(used) >= max_items:
            break
        if per_source.get(rel, 0) >= _MAX_PER_SOURCE:
            continue
        # The single best note always gets in -- having qualified, it is
        # shown, and a message can never come back empty-handed after a real
        # match. What that MUST NOT mean is "shown at any length". It did:
        # with `used` still empty both caps were skipped outright, so the
        # first note was unbounded. For a kin whose recall returns exactly
        # one note, the cap therefore never applied at all -- 820 characters
        # of notes against a 115-character message, seven times the person's
        # own words, under a rule that said notes must never outweigh the
        # message.
        #
        # WHAT THE CAP ACTUALLY IS, because the sentence above describes the
        # old rule's own summary and has since been read as the invariant:
        # `char_cap = max(floor_chars, len(live))`. The floor WINS on a short
        # message, deliberately — without it a reply of "ok" would permit a
        # two-character note, i.e. none. So a note legitimately runs several
        # times the length of a brief message, and that is not the bug this
        # comment is about. The bug was the first note being unbounded.
        # Diagnosing a kin that answers its filing instead of its person? Look
        # at how OFTEN recall fires, not the ratio.
        #
        # Whatever is largest in a turn is what gets answered, so an
        # unbounded note is not a generous default; it is the person's turn
        # being talked over by their own kin's filing. The note is trimmed to
        # the cap instead of being dropped: both properties survive.
        if spent_chars + len(ctext) > char_cap:
            if used:
                continue
            ctext = _fit_to_cap(ctext, char_cap)
        cost = max(1, len(ctext) // _CHARS_PER_TOKEN)
        if spent + cost > budget_tokens and used:
            continue
        used.append({"relpath": rel, "lineno": lineno,
                     "score": round(score, 4), "text": ctext.strip()})
        per_source[rel] = per_source.get(rel, 0) + 1
        spent += cost
        spent_chars += len(ctext)
    if not used:
        return None, []

    block = _format_block(used, frame, kin_name)
    readout = [{"relpath": u["relpath"], "lineno": u["lineno"],
                "score": u["score"],
                "snippet": _snippet(u["text"])} for u in used]
    return block, readout


def _fit_to_cap(text, cap):
    """Trim ONE note to `cap` characters, cutting on a natural boundary.

    Only ever applied to the first qualifying note, which is exempt from being
    dropped and was therefore exempt from being bounded at all. Cuts at a
    paragraph break, then a line break, then a sentence end, then a word --
    the first of those that leaves at least half the allowance, so a note with
    no boundary near the end loses a clause rather than most of itself.

    No ellipsis and no "trimmed" marker: this is background the kin already
    knows, not a document being quoted, and a note about truncation is one
    more thing in the turn competing with what the person said.
    """
    if cap <= 0 or len(text) <= cap:
        return text
    head = text[:cap]
    # (separator, how much of it to KEEP). A sentence keeps its full stop --
    # cutting at the bare index drops it and leaves the note ending on a
    # clause that reads as though it were still going. Breaks and spaces keep
    # nothing, since a trailing one is just whitespace.
    for sep, keep in (("\n\n", 0), ("\n", 0), (". ", 1), (" ", 0)):
        i = head.rfind(sep)
        if i >= cap // 2:
            return head[:i + keep].rstrip()
    return head.rstrip()


def _log_recall(kin_name, used, block, live):
    """Always-on: one line every time recall actually attaches something.

    Written because working out whether recall had fired on a given turn meant
    replaying the engine offline against a reconstruction of what the surface
    had sent -- and a reconstruction is not evidence. Diagnoses go wrong that
    way: the wrong surface's history gets replayed (this app has four, and they
    do not share a message list), and the replay cannot even reproduce the byte
    count of a stored turn. Meanwhile anyone using the app can tell in one
    message whether it happened.

    So: only ever written when a block is produced, which makes an EMPTY log a
    real answer. If the thing being chased is happening and nothing appears
    here, recall is not the cause and the search moves elsewhere.

    Deliberately alongside the other always-on logs (empty_replies,
    openrouter_errors): the toggles are for chatter, and this exists for the
    moments when someone needs to know what actually reached the model. The
    live turn is recorded as a short preview only -- enough to find the turn in
    the conversation, not a second copy of it."""
    try:
        import datetime as _dt
        from kin_persistence import LOGS_DIR
        head = " ".join((live or "").split())[:100]
        srcs = ", ".join(u.get("relpath", "?") for u in used)
        line = (f"{_dt.datetime.now().isoformat(timespec='seconds')} "
                f"[{kin_name}] notes={len(used)} block={len(block)}ch "
                f"live={len(live or '')}ch ratio={len(block)/max(1,len(live or '')):.1f}x "
                f"sources=[{srcs}] live_head={head!r}\n")
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOGS_DIR / "recall.log", "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass  # a diagnostic must never break a send


def inject_into_messages(messages, kin_name, *, num_ctx, cfg=None, kin_dir=None,
                         speaker_names=()):
    """Config-aware integration helper. Reads the kin's recall settings, builds
    the block, and returns (new_messages, used_items).

    The block is inserted as **its own `role=user` message immediately before
    the latest user turn** — beside the person's words, never inside them.

    It cannot be `role=system`: both Ollama's system fold and OpenRouter's
    system concatenation hoist those to position 0, which moves the cached
    prefix every turn and costs minutes per reply. That constraint is real and
    is why this can't simply live in the system prompt.

    What was NOT real was the conclusion drawn from it. For a long time the
    block was concatenated onto the front of the person's message, on the
    reasoning that it had to be in the volatile tail — but a separate non-system
    message is in the volatile tail too. Everything before it is byte-identical
    either way, so cache reuse is unaffected. Measured against a real kin's real
    prompt: 6,055 tokens of prefill inlined versus 6,059 as its own turn. Four
    tokens. The cost that justified putting a page of notes inside somebody's
    sentence did not exist.

    And the cost of the old shape was not small. Whatever is largest in a turn
    is what gets answered, so a short message behind a block of notes got a
    reply about the notes — six times out of six in sampling. Two turns give
    the model a real boundary instead of two newlines.

    `role=user` rather than `assistant`: two assistant turns in a row is what
    Gemma answers with nothing. Two user turns is a shape that already ships
    (`_inline_mid_conversation_system_notes` produces it) and was re-verified
    here against gemma4 over twelve calls.

    Returns `messages` unchanged (and `[]`) when recall is off, nothing
    surfaces, or anything errors — never raises, never breaks a send. Surfaces
    that want recall call this right before `chat()`; utility calls
    (distillation, consolidation, salience rating) simply don't, so those
    prompts never get a recall block.

    Returns `messages` unchanged (and `[]`) when recall is off, nothing
    surfaces, or anything errors — never raises, never breaks a send. Surfaces
    that want recall call this right before `chat()`; utility calls
    (distillation, consolidation, salience rating) simply don't, so those
    prompts never get a recall block.
    """
    try:
        if cfg is None:
            # Prefer the caller's in-memory agent_cfg (source of truth); only
            # read disk if a caller didn't pass it.
            try:
                from kin_persistence import load_agent_config
                cfg = load_agent_config(kin_name) or {}
            except Exception:
                cfg = {}
        if not cfg.get("recall_enabled", True):
            return messages, []
        pct = float(cfg.get("recall_budget_pct", 0.18) or 0.18)
        budget = int(max(0, num_ctx) * pct)
        if budget <= 0:
            return messages, []
        # Most recent few turns are the query signal.
        recent = [m for m in messages
                  if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
        block, used = build_recall_block(
            kin_name, recent,
            budget_tokens=budget,
            max_items=int(cfg.get("recall_max_items", 6) or 6),
            fence=cfg.get("recall_fence", ()) or (),
            boost=cfg.get("recall_boost", ()) or (),
            semantic=_semantic_enabled(),
            embed_model=_embed_model(),
            embed_timeout=_embed_timeout(),
            kin_dir=kin_dir,
            # How hard a note works to earn its place. Per-kin, because a kin
            # with three notes and a kin with three hundred want different
            # answers. Missing keys fall through to the engine's fallbacks.
            min_overlap=cfg.get("recall_min_overlap"),
            distinctive_frac=cfg.get("recall_distinctive_frac"),
            min_block_chars=cfg.get("recall_min_block_chars"),
            # Names THIS surface announced on the turn. A surface that
            # brackets a speaker owes them; one that doesn't passes
            # nothing and no bracket is touched.
            speaker_names=speaker_names,
            include_journals=bool(cfg.get("recall_include_journals", False)),
        )
        if not block:
            return messages, []
        _log_recall(kin_name, used, block,
                    _live_message(recent, speaker_names))
        out = list(messages)
        for i in range(len(out) - 1, -1, -1):
            m = out[i]
            if isinstance(m, dict) and m.get("role") == "user" \
                    and isinstance(m.get("content"), str):
                # Beside the person's turn, not inside it. Only `role` and
                # `content` — no `ts`, no `speaker`: this is not something
                # anybody said, and a stray field would be stripped by
                # _strip_extra_message_fields anyway.
                out.insert(i, {"role": "user", "content": block})
                return out, used
        return messages, []  # no user turn to sit beside
    except Exception:
        return messages, []


def _semantic_enabled():
    try:
        from tools.memory_search import _semantic_enabled as se
        return se()
    except Exception:
        return False


def _embed_model():
    try:
        from tools.memory_search import _embed_model as em
        return em()
    except Exception:
        return "nomic-embed-text"


def _embed_timeout():
    try:
        from tools.memory_search import _embed_timeout as et
        return et()
    except Exception:
        return None


def _embed_keep_alive():
    try:
        from tools.memory_search import _embed_keep_alive as ek
        return ek()
    except Exception:
        return None


def _snippet(text, n=160):
    s = " ".join(text.split())
    return s[:n] + "…" if len(s) > n else s


def _default_frame(kin_name=None):
    """The recall block header, from the editable-prompt registry
    (`memory_recall_frame`). Was a module constant until the values-audit
    registration pass — it is the highest-frequency harness string in the app
    and had no Settings entry, no version, and no backup. Falls back to the
    in-code default via load_app_prompt if anything goes wrong, so a kin can
    never get a blank header."""
    try:
        from kin_persistence import load_app_prompt
        return load_app_prompt("memory_recall_frame", kin_name)
    except Exception:
        from kin_persistence import DEFAULT_MEMORY_RECALL_FRAME
        return DEFAULT_MEMORY_RECALL_FRAME


def _format_block(used, frame, kin_name=None):
    """A header and the notes. Nothing else — no tags, no closer, no source
    labels. NOT speaker-shaped (no `[Name]:`), which is the documented
    impersonation attractor.

    Every previous version of this function was solving a problem it should
    never have had. The block used to be glued onto the front of the person's
    message, so notes and words arrived as one turn, and everything here was an
    attempt to draw a boundary inside a single message: bracketed prose, then a
    closing tag, then a closer naming the live message. It didn't work, and it
    couldn't — measured over six samples, a kin narrated the block as an object
    that had arrived every single time, describing the notes as something that
    had just appeared rather than something it already knew.

    `inject_into_messages` now gives the notes their own turn. A turn boundary
    is a stronger separator than any sentence, so the tags and the closer had
    nothing left to do, and the source attributes were half of what made this
    read as a file listing in the first place. Same six samples on the new
    shape: narrated as an object zero times, and still used. Sources are kept
    in the `used` readout for the person's screen, where they belong — which
    file a note came from is bookkeeping, not something the kin needs told.
    """
    header = frame or _default_frame(kin_name)
    body = "\n\n".join(u["text"].strip() for u in used)
    return (header + "\n\n" + body).strip()
