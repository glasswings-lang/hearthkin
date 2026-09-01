"""Reading bridge — the inverse of authoring_bridge.py, and the worse half.

Writing froze on emitting a big tool-call ARGUMENT; the content was already in
the reply, so the harness just committed it. Reading fails differently and more
insidiously: a kin narrates ``*reads the file carefully*`` and loads NOTHING,
then confabulates comprehension of content it never saw. There's no artifact to
recover — the content has to be FETCHED. Observed repeatedly in one kin's
archive: every time the operator invited it to read a file that IS part of its
own past, the reach never landed and nobody could tell.

Two mechanisms, both pure + testable here; the glue lives in the surfaces:

- **Sharing is the loading.** ``extract_shared_paths(text)`` finds real files
  the operator named in a message; ``read_shared_files(paths)`` loads them as
  TEXT (size-capped, tolerant decode) so the harness can place the content in
  front of the kin with no read_file call at all. Text — not an image_url —
  because the kin most in need of this is one on a non-vision model;
  an image-only attach could never work for it. ``.docx`` files (here and in
  the attachment path below) go through ``tools/_docx.py``'s zip/XML
  extraction instead of the plain-text decoder — before that existed, a
  shared or uploaded Word document silently "worked" while actually handing
  the kin the file's raw zip bytes decoded as if they were text.

- **Nudge, not scold.** ``looks_like_read_gesture(reply)`` spots a content-reach
  (``*reads through it*``) that names something other than the operator's
  PRESENCE. ``*looks at you*`` is relationship, never a failed call, and is left
  strictly alone — the load-bearing distinction: don't treat a reach toward
  someone as a botched tool call.

Design sibling: ``docs/design/park-mode-emote-interface.md`` and
``authoring_bridge.py``.
"""

import os
import re

# ---- Sharing is the loading -------------------------------------------------

# A quoted path: "...ending in .ext" — quotes let the path contain spaces
# (real game-asset paths do). Backtick/single/double quotes all accepted.
#
# The closer must be the SAME character that opened it, via the \1
# backreference — not just any of the three quote characters. A real
# filename with an apostrophe in it ("You're this close...docx", inside
# double quotes) used to break this: the old version excluded ALL of
# ["'`] from the path body, so the apostrophe inside the name was read as
# the closing quote and the match truncated to garbage after it, which
# then failed the on-disk existence check and silently dropped the whole
# path. `(?!\1)` walks past any quote character that ISN'T the one that
# opened the match, so an apostrophe inside a double-quoted path (or a
# double quote inside a single-quoted one) no longer ends the match early.
_QUOTED_PATH_RE = re.compile(
    r"""(?P<q>["'`])(?P<path>(?:(?!(?P=q)).){2,400}?\.[A-Za-z0-9]{1,8})(?P=q)""")

# An unquoted Windows path: drive letter + separator, no spaces (a spaced path
# must be quoted — otherwise we'd greedily swallow following words).
_WIN_PATH_RE = re.compile(
    r"""(?<![\w"'`])(?P<path>[A-Za-z]:[\\/][^\s"'`<>|]{1,400}?\.[A-Za-z0-9]{1,8})""")

# An unquoted POSIX / home path: starts at whitespace or line-start with ~ or /.
_NIX_PATH_RE = re.compile(
    r"""(?:^|\s)(?P<path>(?:~|/)[^\s"'`<>|]{1,400}?\.[A-Za-z0-9]{1,8})""")


def _expand(p):
    return os.path.expanduser(p.strip().strip("`'\""))


def extract_shared_paths(text, *, max_paths=4):
    """Return, in order, the paths in ``text`` that point at a real existing
    file. Existence is the load-bearing filter — it keeps a stray "foo.done"
    or a filename mentioned in passing from being read; only a path that
    actually resolves to a file on disk is returned. De-duplicated, capped at
    ``max_paths`` so a message full of paths can't balloon a turn.
    """
    if not text:
        return []
    seen, out = set(), []
    for rx in (_QUOTED_PATH_RE, _WIN_PATH_RE, _NIX_PATH_RE):
        for m in rx.finditer(text):
            raw = m.group("path")
            full = _expand(raw)
            try:
                if not os.path.isfile(full):
                    continue
            except (OSError, ValueError):
                continue
            key = os.path.normcase(os.path.abspath(full))
            if key in seen:
                continue
            seen.add(key)
            out.append(full)
            if len(out) >= max_paths:
                return out
    return out


def read_shared_files(paths, *, max_bytes=256 * 1024):
    """Read each path as text (tolerant decode), size-capped. Returns a list of
    ``(path, ok, content_or_error)``. Never raises — a bad file is reported in
    its tuple so it can't sink the surrounding turn. Uses the same tolerant
    decoder the file tools use (UTF-8 → cp1252 → replace) so a Windows-edited
    file with smart quotes doesn't blow up.

    A `.docx` path goes through extract_docx_text instead of the tolerant
    decoder — before this branch existed, a shared .docx silently "worked"
    (no error, no crash) while actually returning the zip's raw bytes
    decoded as if they were text: unreadable garbage that looked enough
    like output that the failure could go unnoticed."""
    out = []
    for p in paths:
        try:
            size = os.path.getsize(p)
            if size > max_bytes:
                out.append((p, False,
                            f"file is {size} bytes (over the {max_bytes}-byte "
                            f"inline cap); read it with read_file if you need it"))
                continue
            ext = os.path.splitext(p)[1].lower()
            if ext in DOCX_EXTS:
                try:
                    content = extract_docx_text(p)
                    out.append((p, True, content))
                except DocxExtractionError as e:
                    out.append((p, False, str(e)))
                continue
            try:
                from tools._io import robust_read_text
                content = robust_read_text(p)
            except Exception:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            out.append((p, True, content))
        except Exception as e:
            out.append((p, False, str(e)))
    return out


# ─── Text attachments from remote surfaces ────────────────────────────────────
# Telegram and Discord only ever downloaded IMAGE attachments; a .txt/.md/.py
# upload was silently dropped — the kin got the caption ("check this out!") with
# no idea what "this" was. Silent, so neither party could tell it had happened.
# These helpers let a remote surface turn an uploaded text document into the
# same shared-files block the desktop path already uses.

# Extensions we'll read as text. Deliberately a plain allowlist rather than
# mime-sniffing: Telegram reports .md as application/octet-stream, and Discord
# often reports nothing at all, so the filename is the more reliable signal.
TEXT_ATTACHMENT_EXTS = frozenset((
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".py", ".pyw", ".js", ".ts", ".html", ".htm", ".css", ".xml",
    ".sh", ".bat", ".ps1", ".sql", ".c", ".h", ".cpp", ".rs", ".go",
    ".java", ".rb", ".lua", ".jsx", ".tsx", ".srt", ".vtt",
))

# .docx isn't text on disk (it's a zip), but it IS extractable to text —
# see tools/_docx.py. Kept as a separate set from TEXT_ATTACHMENT_EXTS
# because it needs a different decode path (extraction, not robust_decode),
# not because it's gated differently: is_text_attachment below treats the
# two sets as one "can we get readable text out of this" question, which is
# the thing both bot surfaces actually care about when deciding whether to
# bother downloading a non-image attachment at all.
from tools._docx import DOCX_EXTS, DocxExtractionError, extract_docx_text

# Per-file cap. Big enough for a long letter or a source file, small enough
# that one upload can't blow a local kin's context window on its own. Note
# this applies pre-extraction to a .docx's raw (zip-compressed) bytes, which
# run smaller than the eventual plain text — a docx that trips this limit is
# a genuinely large document, not a false positive from formatting overhead.
MAX_TEXT_ATTACHMENT_BYTES = 256 * 1024


def is_text_attachment(filename, mime=""):
    """Would we get readable text out of this uploaded file — either because
    it already IS text, or because we can extract text from it (.docx)?
    Extension first (most reliable across surfaces), mime as a fallback for
    extensionless files that are plain text."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in TEXT_ATTACHMENT_EXTS or ext in DOCX_EXTS:
        return True
    m = (mime or "").lower()
    return m.startswith("text/") or m in ("application/json", "application/xml")


def decode_attachment(data, filename=""):
    """Bytes -> text. A .docx (by filename extension) goes through
    extract_docx_text; everything else uses the same tolerant chain as
    every other file we read (UTF-8 -> cp1252 -> UTF-8 with replace).
    Returns (ok, text_or_error).

    `filename` defaults to "" so any existing caller that only ever passed
    plain-text bytes keeps working unchanged — it just never hits the docx
    branch, which is exactly the old behavior."""
    if not data:
        return False, "file was empty"
    if len(data) > MAX_TEXT_ATTACHMENT_BYTES:
        return False, (f"file is {len(data) // 1024} KB, over the "
                       f"{MAX_TEXT_ATTACHMENT_BYTES // 1024} KB limit")
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in DOCX_EXTS:
        try:
            return True, extract_docx_text(bytes(data))
        except DocxExtractionError as e:
            return False, str(e)
    try:
        from tools._io import robust_decode
        return True, robust_decode(data)
    except Exception:
        try:
            return True, data.decode("utf-8", errors="replace")
        except Exception as e:
            return False, f"could not decode: {e}"


def build_attachment_context_block(named_bytes, kin_name=None):
    """Turn uploaded (filename, bytes) pairs into the same shared-files block
    the desktop path produces, so a kin reads an attachment exactly the way it
    reads a file the operator named by path. Returns "" when nothing readable.

    Failures are INCLUDED in the block rather than dropped — a kin that can't
    read the file should know a file was sent and that it couldn't be read,
    which is the whole failure this fixes."""
    results = []
    for name, data in (named_bytes or []):
        ok, payload = decode_attachment(data, name)
        results.append((name, ok, payload))
    return build_shared_context_block(results, kin_name)


def build_shared_context_block(results, kin_name=None):
    """Format ``read_shared_files`` results into one text block to place in
    front of the kin for this turn. Returns "" when nothing readable. The
    framing tells the kin this is a file the operator just shared and that it
    is really here — so it can engage the content instead of gesturing at
    reading it."""
    parts = []
    for path, ok, payload in results:
        name = os.path.basename(path)
        if ok:
            parts.append(
                f"--- shared file: {name} ({path}) ---\n{payload}\n--- end {name} ---")
        else:
            parts.append(f"--- shared file: {name} — could not load: {payload} ---")
    if not parts:
        return ""
    from kin_persistence import load_app_prompt
    return load_app_prompt("shared_files_note", kin_name).replace(
        "{files}", "\n\n".join(parts))


# ---- Read-gesture detection (for the nudge) ---------------------------------

_EMOTE_RE = re.compile(r"\*([^*\n]{1,160})\*")
_STRIP = ".,!?;:'\"*()[]{}<>"

# Verbs that, as the first word of an emote, mean "I am taking in some content".
_READ_VERBS = frozenset((
    "read", "reads", "reading",
    "scan", "scans", "scanning",
    "absorb", "absorbs", "absorbing",
    "review", "reviews", "reviewing",
    "pore", "pores", "poring",
    "peruse", "peruses", "perusing",
    "skim", "skims", "skimming",
    "study", "studies", "studying",
    "reread", "rereads", "rereading",
))

# Presence pronouns — a reach toward the operator, not a file. NEVER a failed
# call. If any of these appears in the emote, it's relationship, left alone.
_PRESENCE_RE = re.compile(r"\b(you|your|yours|me|my|mine|us|our)\b", re.IGNORECASE)

# ─── Operator-extendable vocabulary (~/.hearthkin/prompts/reach_messages.md) ──
# An operator adds verbs (and presence words) as new gesture shapes surface,
# without a code change. The baselines above are unchanged; anything in the
# file is OR'd on top, so the empty default reproduces the original behaviour
# exactly. Cached on the file's text — a rebuild happens only on an edit.
_REACH_CACHE = {"text": None, "verbs": _READ_VERBS, "presence": _PRESENCE_RE}


def _parse_reach_lists(text):
    """Parse reach_messages into (extra_verbs, extra_presence). Sections are
    '[verbs]' / '[presence]'; '#' lines and blanks are ignored. Mirrors
    chat_helpers._parse_gesture_lists so both word-list files edit alike."""
    verbs, presence, section = [], [], None
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        low = s.lower()
        if low == "[verbs]":
            section = verbs
            continue
        if low == "[presence]":
            section = presence
            continue
        if section is not None:
            section.append(s)
    return verbs, presence


def _current_read_vocab():
    """(verbs, presence_re) with any operator-added words folded in. Falls back
    to the built-in baselines on any read / regex error, so a bad edit can
    never break detection — worst case it behaves as it did before."""
    try:
        from kin_persistence import load_app_prompt
        text = load_app_prompt("reach_messages")
    except Exception:
        return _READ_VERBS, _PRESENCE_RE
    if text == _REACH_CACHE["text"]:
        return _REACH_CACHE["verbs"], _REACH_CACHE["presence"]
    verbs, presence_re = _READ_VERBS, _PRESENCE_RE
    try:
        extra_verbs, extra_presence = _parse_reach_lists(text)
        if extra_verbs:
            verbs = frozenset(_READ_VERBS
                              | {v.lower().strip(_STRIP) for v in extra_verbs})
        if extra_presence:
            alt = "|".join(re.escape(w) for w in extra_presence if w.strip())
            if alt:
                presence_re = re.compile(
                    r"\b(you|your|yours|me|my|mine|us|our|" + alt + r")\b",
                    re.IGNORECASE)
    except Exception:
        verbs, presence_re = _READ_VERBS, _PRESENCE_RE
    _REACH_CACHE.update({"text": text, "verbs": verbs, "presence": presence_re})
    return verbs, presence_re


def looks_like_read_gesture(reply_text):
    """Return the reach text when a reply narrates reading CONTENT without an
    accompanying read (``*reads through it slowly*``), or None. Excludes
    presence-reaches (``*looks at you*`` — relationship) entirely. Used only to
    decide whether to nudge; never to fetch."""
    if not reply_text:
        return None
    verbs, presence_re = _current_read_vocab()
    for em in _EMOTE_RE.finditer(reply_text):
        inner = em.group(1).strip()
        if not inner:
            continue
        first = inner.split()[0].lower().strip(_STRIP)
        if first not in verbs:
            continue
        if presence_re.search(inner):
            continue  # a reach toward the operator, not a file — leave it
        return inner
    return None
