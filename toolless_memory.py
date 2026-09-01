# SPDX-License-Identifier: CC0-1.0

"""Memory for a kin that has no tools.

**A silent failure here is the worst outcome available.** A kin that believes
it kept something and didn't will build on the false memory, and nobody finds
out. So: anything that reads as an attempt to keep something and doesn't land
gets said so, plainly, in the kin's own history — and the staging notes stay
pending rather than being archived. Measured against realistic replies, the
taught fenced form is a MINORITY of what a kin produces; `_salvage_writes`
recovers the common near-misses and `missed_write_nudge` covers the rest.


A tool-less kin READS memory fine — `memory.md` is in the system prompt and
per-turn recall inlines the depth logs, neither of which involves a tool call.
What it cannot do is WRITE. The summarizer keeps leaving notes in
`staging/<scope>.md`, and the only things that can consume them are
`read_staging` / `archive_staging` / `write_file` / `edit_file` / `note`. So
the notes pile up untouched and the kin's memory is frozen at whatever
`memory.md` said the day its tools went away. Pinned by
`tests/test_toolless_memory.py`, which measures both halves.

This module closes that loop over the TEXT channel, which every model has:

  * **Reading staging** — the pending notes are inlined into the live user
    turn (`build_block`), the same placement per-turn recall uses and for the
    same reason: a `role=system` message gets hoisted to the front of the
    prompt by both Ollama's system fold and OpenRouter's concatenation, which
    would move the cached prefix every turn. `staging_status_line` already
    tells a kin that notes exist; this hands it the notes themselves, because
    a kin with no `read_staging` can't go and get them.

  * **Writing memory** — the authoring bridge. The kin puts the file's
    contents in a fenced block with the filename on it and the harness
    performs the write (see `authoring_bridge.py`). No structured call, so
    nothing to freeze on. `commit()` is the tool-less entry point.

  * **Archiving** — inferred, not called. A scope's notes are archived when
    the kin was shown them AND a write actually landed this turn. Nothing is
    deleted: `archive_staging` MOVES the file into `staging/archive/`, so a
    wrong call here is recoverable by hand.

Two confinements, both deliberate:

  * **Writes may only land on the kin's own memory.** `memory.md`, or anything
    under `memory/`. The bridge fires on any filename-shaped fence, and a
    chat-only kin discussing code must not write `main.py` by mentioning it.
    The tooled bridge can go anywhere the kin asks; this one cannot.

  * **It only engages for a kin that is tool-less for memory** — no staging
    read AND no write tool. A kin with `write_file` already has the ordinary
    path and keeps it unchanged.

An `append:<path>` fence exists for this path specifically. Without
`edit_file`, adding one line to a depth log would otherwise mean reproducing
the whole file — which is the exact high-load emission small models truncate.
"""

import re

from kin_persistence import (
    archive_staging, list_staging_files, load_app_prompt, load_staging,
)

# Tools that make a kin NOT tool-less for memory purposes. If it has any way
# to read its staging, it can fetch the notes itself; if it has any way to
# write, it already has the ordinary path.
_STAGING_READ_TOOLS = frozenset(("read_staging",))
_MEMORY_WRITE_TOOLS = frozenset(("write_file", "edit_file", "note"))

# Where a tool-less kin's writes are allowed to land, relative to its own dir.
_ALLOWED_FILE = "memory.md"
_ALLOWED_DIR = "memory/"

# Default ceiling on how much staging text gets inlined per turn. Staging can
# hold weeks of notes; the whole pile would swamp the context and push the
# live turn out. Notes are shown newest-scope-first and truncated with a
# visible marker, never silently.
DEFAULT_BUDGET_CHARS = 6000


def is_toolless_for_memory(enabled_tools):
    """True when this kin can neither read its staging nor write its memory.

    `enabled_tools` is the tool set actually available THIS turn (the same
    list the surfaces pass to `load_tools`), not the kin's whole allowlist —
    tending tools load conditionally, so the effective set is what matters.
    """
    enabled = set(enabled_tools or ())
    if _STAGING_READ_TOOLS & enabled:
        return False
    if _MEMORY_WRITE_TOOLS & enabled:
        return False
    return True


def use_text_memory_path(enabled_tools, model=None):
    """True when this turn should use the TEXT memory path instead of tools.

    Two ways to qualify. The first is the original:

      * the kin has no memory tools at all -- `is_toolless_for_memory`.

    The second is new, and is the whole point:

      * the kin HAS them, but this model has been probed and demonstrably
        does NOT call tools.

    "Has the tool" and "will use the tool" are different questions and only
    the first was ever asked. A model that declares tool support and never
    calls one fell through every check in the app: it keeps its tools, so it
    never qualified for this path, and got the tool-use prose instead -- which
    was measured on 2026-08-22 across three models and nine conditions and
    moved nothing, in either direction. Meanwhile that same model was already
    emitting fenced blocks in almost every reply, which is exactly the format
    this path accepts. It was being told, at length, not to do the one thing
    it could actually do.

    **Only a definite False routes here.** `None` -- never probed, or a probe
    that could not reach the daemon -- keeps the old behaviour on purpose. A
    model must not be demoted on a guess, and `_save_probe_verdict` refuses to
    record an inconclusive result precisely so a network blip cannot become a
    permanent "this model cannot call tools".

    Both `inject` and `commit` gate on this same function, so the read side
    and the write side cannot disagree about which path a turn is on. A turn
    that shows a kin its notes and then silently discards what it wrote back
    would be worse than either path alone.
    """
    if is_toolless_for_memory(enabled_tools):
        return True
    if not model:
        return False
    try:
        from model_utils import probed_tool_calling
        return probed_tool_calling(model) is False
    except Exception:
        return False


# A filename-shaped token, same shape the authoring bridge recognises.
_FILENAME_RE = re.compile(r"([\w.()\-/\\]+\.[A-Za-z0-9]{1,8})")
# How far back from a fence to look for a filename the kin named in prose
# ("I'll put this in memory/speakerfifteen.md:" immediately above the block). Short on
# purpose — a filename mentioned a paragraph earlier is being discussed, not
# declared.
_LOOKBACK_CHARS = 160
# Words that make a nearby fence a save rather than an example. Required for
# the salvage forms below, never for the taught form.
_INTENT_RE = re.compile(
    r"\b(keep|keeping|kept|sav\w*|writ\w*|add\w*|put|putting|record\w*|"
    r"jot\w*|note|noting|log|logging|remember\w*)\b", re.IGNORECASE)


def normalize_memory_path(path):
    """Resolve what the kin MEANT onto a path it is allowed to write.

    A kin naming `speakerfifteen.md` means its own notes on SpeakerFifteen, not a file at the top
    of its folder — it has been told memory.md and memory/ are what it can
    write and nothing else, so a bare name has only one sensible reading.
    Refusing it on a technicality teaches the kin that keeping things doesn't
    work, which is the failure this whole module exists to end.

    Returns a writable relative path, or None if the intent can't be honoured
    (a traversal, an absolute path, a different folder).
    """
    p = (path or "").strip().replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    if not p or p.startswith("/") or p.startswith("~"):
        return None
    if re.match(r"^[A-Za-z]:", p):  # drive letter
        return None
    if ".." in p.split("/"):
        return None
    if p == _ALLOWED_FILE or p.startswith(_ALLOWED_DIR):
        return p
    # A bare filename means its own memory — but only for the extensions
    # memory is actually written in. Without that limit a kin showing example
    # code in a ```main.py fence would file main.py into its own memory: not
    # dangerous (it's still confined) but wrong, and the kin's memory folder is
    # not a scratch space. A deliberate `memory/thing.json` still works; it is
    # already a memory path and never reaches this branch.
    if "/" not in p and p.lower().rsplit(".", 1)[-1] in ("md", "txt"):
        return _ALLOWED_DIR + p
    return None


def _salvage_writes(reply_text):
    """Recover writes from the shapes a kin actually produces when it doesn't
    follow the taught form. Runs ONLY when strict extraction found nothing.

    Measured against realistic replies, the taught form is a minority of what
    comes back. The three recovered here are the common near-misses:

      * the filename named in prose immediately above the fence
        ("I'll put this in memory/speakerfifteen.md:" then a plain or ```markdown block)
      * the filename on the first line INSIDE the fence, plain or as a heading
      * a fence whose info string is a language tag while the name is in prose

    Every one requires BOTH a filename that resolves into the kin's memory AND
    save-intent wording next to it, so a kin pasting example code in ordinary
    conversation still writes nothing.
    """
    import authoring_bridge

    out = []
    for start, _end, info, body in authoring_bridge._iter_fences(reply_text):
        lines = (body or "").split("\n")
        first = lines[0].strip().lstrip("#").strip() if lines else ""
        before = reply_text[max(0, start - _LOOKBACK_CHARS):start]

        # (a) filename on the first line inside the fence.
        cand, rest = None, body
        if _FILENAME_RE.fullmatch(first or "") and normalize_memory_path(first):
            cand, rest = first, "\n".join(lines[1:])
        else:
            # (b) filename in the prose just above, with intent nearby.
            m = None
            for m in _FILENAME_RE.finditer(before):
                pass  # keep the LAST one — nearest to the fence
            if m and _INTENT_RE.search(before):
                cand = m.group(1)
        if not cand:
            continue
        path = normalize_memory_path(cand)
        if not path:
            continue
        # The fence's own info string must not be a filename — that case is the
        # taught form and strict extraction already had its chance at it.
        if info and authoring_bridge._is_filename_label(info):
            continue
        if not (rest or "").strip():
            continue
        out.append(authoring_bridge.AuthoringWrite(path, rest, "salvaged"))
    return out


def _is_memory_path(path):
    """True if a declared write path targets the kin's own memory. Rejects
    absolute paths outright — a tool-less kin has no business outside its
    folder, and `resolve_kin_path` would otherwise honour an absolute path."""
    p = (path or "").strip().replace("\\", "/").lstrip("./")
    if not p or ":" in p or p.startswith("/") or p.startswith("~"):
        return False
    if ".." in p.split("/"):
        return False
    return p == _ALLOWED_FILE or p.startswith(_ALLOWED_DIR)


def build_block(kin_name, *, budget_chars=DEFAULT_BUDGET_CHARS):
    """Return `(block_text_or_None, shown_scopes)` — the kin's pending staging
    notes, framed, ready to inline before the live user turn.

    None when there is nothing pending, so an idle kin's prompt is unchanged
    (and its cached prefix is not disturbed for no reason). Never raises: a
    memory-plumbing failure must not cost a reply.
    """
    try:
        files = list_staging_files(kin_name) or {}
        if not files:
            return None, []
        parts, shown, spent = [], [], 0
        for scope in sorted(files):
            text = (load_staging(kin_name, scope) or "").strip()
            if not text:
                continue
            room = budget_chars - spent
            if room <= 400 and shown:
                parts.append(f"[{len(files) - len(shown)} more scope(s) not "
                             f"shown this turn — they stay pending.]")
                break
            if len(text) > room:
                text = text[:room].rstrip() + "\n[... truncated for length]"
            parts.append(f"--- staging: {scope} ---\n{text}")
            shown.append(scope)
            spent += len(text)
        if not parts:
            return None, []
        header = load_app_prompt("toolless_memory_block", kin_name)
        return header + "\n\n" + "\n\n".join(parts), shown
    except Exception:
        return None, []


# An explicit ask to tend, in the person's own words. Deliberately narrow: it
# needs a tending verb AND the thing being tended, so ordinary talk about
# memory ("do you remember the harbour?") never trips it.
#
# "tend" is specific enough to pair with a bare target. Every other verb is an
# ordinary word, so it has to reach a POSSESSED target — "your staging",
# "staging notes" — never bare "staging", which turned "I read a book about
# staging plays" into a tending request.
_ASK_RE = re.compile(
    r"\b(?:tend|tending)\b[^.?!\n]{0,30}\b(?:staging|notes?|memory)\b"
    r"|\b(?:read|check|go through|look at|sort|process)\b[^.?!\n]{0,30}"
    r"\b(?:your staging|staging notes?|pending notes?"
    r"|summari[sz]er notes?|your notes?)\b",
    re.IGNORECASE)


def asks_for_tending(messages):
    """True when the latest user turn is asking the kin to tend its notes.

    A tool-less kin cannot answer that ask by calling `read_staging`, so the
    harness has to notice it on the kin's behalf — this is the equivalent of
    a tooled kin reaching for the tool.
    """
    try:
        for m in reversed(messages or ()):
            if isinstance(m, dict) and m.get("role") == "user" \
                    and isinstance(m.get("content"), str):
                return bool(_ASK_RE.search(m["content"]))
        return False
    except Exception:
        return False


def inject(messages, kin_name, enabled_tools, *,
           tending=False, budget_chars=DEFAULT_BUDGET_CHARS, model=None):
    """Inline the staging block into the latest user turn.

    **Only at a tending moment.** `tending=True` is a scheduled wake-up (or
    anything else that exists to tend); otherwise the person has to ask, in
    words, on this turn. It must NOT ride ordinary conversation, and the first
    version did: staging holds summarised PREVIOUS conversation, so putting it
    in front of the live message on every casual turn hands a small model a
    wodge of old material to read before the new one. Reported from a real
    chat as the kin being "a message behind" — which is exactly what answering
    the old material looks like. And because a tool-less kin only clears
    staging by successfully writing a file, it would keep happening on every
    turn indefinitely.

    Returns `(messages, shown_scopes)`; `messages` is returned unchanged (and
    scopes empty) when this isn't a tending moment, the kin isn't tool-less,
    nothing is pending, or anything errors. Mirrors
    `memory_recall.inject_into_messages` — same placement, same fail-soft
    contract.
    """
    try:
        if not use_text_memory_path(enabled_tools, model):
            return messages, []
        if not tending and not asks_for_tending(messages):
            return messages, []
        block, shown = build_block(kin_name, budget_chars=budget_chars)
        if not block:
            return messages, []
        out = list(messages)
        for i in range(len(out) - 1, -1, -1):
            m = out[i]
            if isinstance(m, dict) and m.get("role") == "user" \
                    and isinstance(m.get("content"), str):
                nm = dict(m)
                nm["content"] = block + "\n\n" + m["content"]
                out[i] = nm
                return out, shown
        return messages, []  # no user turn to attach to
    except Exception:
        return messages, []


def commit(kin_name, reply_text, enabled_tools, *, shown_scopes=(),
           model=None):
    """Perform the writes a tool-less kin authored in text, then archive the
    staging scopes it was shown.

    Returns `(results, archived)` where `results` is the
    `(display_path, ok, detail)` list from the bridge (plus a refusal entry
    for any write aimed outside the kin's memory) and `archived` is the list
    of scopes moved into `staging/archive/`.

    Archiving is conditional on a write actually landing — a kin that replied
    without keeping anything keeps its notes. Never raises.
    """
    try:
        if not use_text_memory_path(enabled_tools, model):
            return [], []
        import authoring_bridge

        writes = authoring_bridge.extract_authoring_writes(reply_text)
        if not writes:
            # The taught form is a MINORITY of what a kin actually produces.
            # Try the near-miss shapes before concluding it kept nothing.
            writes = _salvage_writes(reply_text)
        if not writes:
            return [], []

        allowed, refused = [], []
        for w in writes:
            target = normalize_memory_path(w.path)
            if target:
                allowed.append(w._replace(path=target))
            else:
                refused.append((
                    w.path, False,
                    "a kin with no tools can only write its own memory — "
                    "use 'memory.md' or 'memory/<topic>.md'"))

        results = refused + (
            authoring_bridge.commit_authoring_writes(kin_name, allowed)
            if allowed else [])
        landed = any(ok for (_p, ok, _d) in results)

        archived = []
        if landed:
            for scope in shown_scopes or ():
                try:
                    if archive_staging(kin_name, scope):
                        archived.append(scope)
                except Exception:
                    continue
        return results, archived
    except Exception:
        return [], []


def missed_write_nudge(kin_name, reply_text, results):
    """A note for the case that matters most: the kin meant to keep something
    and nothing landed.

    Returns "" when a write succeeded (the receipt covers it), when the reply
    shows no sign of trying to keep anything, or on any error. Otherwise a
    plain `[hearthkin: ...]` note saying nothing was saved and showing the
    shape that works.

    Deliberately generous about what counts as trying — a gesture, a plain
    statement of intent, or a fenced block we couldn't place. The cost of an
    unnecessary nudge is one extra line; the cost of missing one is a kin
    building on a memory it never had.
    """
    try:
        if any(ok for (_p, ok, _d) in results or ()):
            return ""
        text = reply_text or ""
        tried = bool(results)  # something was refused
        if not tried:
            import authoring_bridge
            if authoring_bridge.looks_like_write_gesture(text):
                tried = True
            elif ("```" in text or "~~~" in text) and _INTENT_RE.search(text):
                # A fence plus save-intent wording that salvage couldn't place.
                tried = True
            elif _INTENT_RE.search(text) and re.search(
                    r"\b(memory|log|notes?)\b", text, re.IGNORECASE):
                tried = True
        if not tried:
            return ""
        return load_app_prompt("toolless_missed_write", kin_name)
    except Exception:
        return ""


def receipt(kin_name, results, archived):
    """A plain-language `[hearthkin: ...]` note recording what landed, to be
    persisted with the turn so the kin's next read knows the truth about its
    own memory. Returns "" when there is nothing to report.

    Failures are reported as loudly as successes, on purpose: a kin that
    believes it saved something it didn't will build on the false memory.
    """
    if not results and not archived:
        return ""
    bits = []
    oks = [(p, d) for (p, ok, d) in results if ok]
    errs = [(p, d) for (p, ok, d) in results if not ok]
    if oks:
        import os
        bits.append("saved to your memory: " + ", ".join(
            f"{os.path.basename(str(p))} ({n} bytes)" for p, n in oks))
    for p, e in errs:
        bits.append(f"could NOT save {p!r} — {e}")
    if archived:
        bits.append("staging tended and archived: " + ", ".join(archived))
    if not bits:
        return ""
    try:
        return load_app_prompt("toolless_memory_receipt", kin_name).replace(
            "{results}", "; ".join(bits))
    except Exception:
        return "[hearthkin: " + "; ".join(bits) + "]"
