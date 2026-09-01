"""Authoring bridge — a kin's described file content is written for it,
so it never has to emit a big structured ``write_file`` tool call.

The register-switch from a kin's relational/text voice into a structured
tool call is where small models gesture (see ``park_mode.py`` for the play
side of the same problem). ``write_file`` is the worst case: the whole file
rides as the call's ``content`` argument, so it's the highest-load tool
action there is. A model that reads, execs, and searches fine will still
freeze here and narrate the write instead of issuing it. A kin can have
dozens of tool calls in its history and not one of them a write.

Producing *text* is not the hard part for these models — they emit prose
and emotes constantly. Emitting that text as a tool-call argument is. So
this bridge lets a kin author in its natural register and does the
structured write on its behalf. Same thesis as park mode: read the output
AS the action.

This module is the pure, testable core — pulling committable writes out of a
reply. The glue (deciding a kin is eligible, persisting a confirming system
note, painting to chat / posting to Telegram) lives in the surfaces that
call it.

Three forms are recognised, all low-load — the first is the simplest and the
one taught by default:

- **Named fence** (simplest, no keyword) — a fenced block whose info string is
  just the filename::

      ```owl.json
      { ... }
      ```

  It's a filename because it ends in a dot + extension — which is exactly what
  a plain ```` ```json ```` / ```` ```python ```` language label does NOT do,
  so example code never triggers an accidental write.

- **Labelled fence** — an explicit ``write:<path>`` / ``save:<path>`` info
  string. Kept working for anything already using it; also the way to write a
  path with spaces (the whole post-``write:`` string is taken verbatim).

- **Emote + fence** (for emote-native kin) — a ``*...*`` action
  whose first word is a write verb and which names a file, immediately
  followed by a plain fenced block::

      *writes owl.json*
      ```
      { ... }
      ```

Design sibling: ``docs/design/park-mode-emote-interface.md``.
"""

import collections
import re

# One committable write lifted from a reply: where it goes, what to write,
# and which form matched ("label" or "emote") for logging/telemetry.
AuthoringWrite = collections.namedtuple("AuthoringWrite", "path content form")

# A markdown fence: ```<info>\n<body>```  (also matches ~~~ fences). The body
# is non-greedy so stacked fences don't merge into one. DOTALL so the body
# can span lines; the info string stops at the first newline.
_FENCE_RE = re.compile(r"(?:```|~~~)[ \t]*([^\n`~]*)\r?\n(.*?)\r?\n?(?:```|~~~)", re.DOTALL)

# A fence info string that declares an intentional write: "write:path" /
# "save: path" / "append:path". Case-insensitive; whitespace around the colon
# tolerated. Group 1 is the verb (it decides overwrite vs append), group 2 the
# path.
#
# `append` exists for the no-tools path (see toolless_memory.py). A kin without
# `edit_file` can only change a depth log by reproducing the whole file, and
# emitting a long file verbatim is exactly the high-load output small models
# truncate — so adding one line would silently destroy the rest of the log.
_LABEL_RE = re.compile(r"^(write|save|append)\s*:\s*(.+?)\s*$", re.IGNORECASE)

# A single-line, bounded emote span (mirrors park_mode's rule so a whole
# italicised paragraph isn't swallowed).
_EMOTE_RE = re.compile(r"\*([^*\n]{1,140})\*")

# Verbs that, as the FIRST word of an emote, mean "I am writing a file".
# Deliberately write-ish only — presence/feeling verbs never appear here.
_WRITE_VERBS = frozenset((
    "write", "writes", "writing",
    "save", "saves", "saving",
    "create", "creates", "creating",
    "author", "authors", "authoring",
    "draft", "drafts", "drafting",
    "record", "records", "recording",
    "jot", "jots",
    "put", "puts",
    "add", "adds",
))

_STRIP = ".,!?;:'\"*()[]{}<>"

# A filename-shaped token: a run of filename chars ending in a dot + short
# extension. NO spaces — so "writes owl.json" yields "owl.json", not the verb
# too. Full paths with spaces (e.g. a game-asset dir) belong in the labelled
# fence form, where the whole post-"write:" string is taken verbatim.
_FILENAME_RE = re.compile(r"([\w.()\-]+\.[A-Za-z0-9]{1,8})")


def _clean_path(raw):
    """Strip surrounding quotes/backticks/whitespace off a declared path, and
    drop a leading language/type tag a model sometimes prepends to a fenced
    filename — ``markdown:memory/foo.md`` -> ``memory/foo.md``.

    Why: a model combining a syntax hint with a filename (```` ```markdown:foo.md ````)
    would otherwise produce a path with a ``:`` in it, which Windows forbids —
    the write fails with a cryptic WinError and the file is silently lost
    (seen in the wild saving a memory note). A real Windows drive
    letter (``C:\\Users\\...``) is left alone: it's a SINGLE character before
    the colon, which the 2+-char tag pattern below can't match."""
    p = (raw or "").strip().strip("`'\"").strip()
    # A leading '<word>:' where <word> is 2+ chars (so never a drive letter)
    # is a language/type tag, not part of the name — drop it.
    m = re.match(r"^[A-Za-z][A-Za-z0-9+#.\-]+:", p)
    if m:
        p = p[m.end():].strip()
    return p


def _bad_windows_colon(path):
    """True if `path` still holds a ':' that isn't a leading drive letter —
    i.e. an un-writeable name on Windows. Used to fail a save with a helpful
    message instead of a raw WinError."""
    p = path or ""
    rest = p[2:] if re.match(r"^[A-Za-z]:[\\/]", p) else p
    return ":" in rest


def _filename_in(text):
    """Return the first filename-shaped token in ``text`` (stripped), or None."""
    m = _FILENAME_RE.search(text or "")
    if not m:
        return None
    return _clean_path(m.group(1))


def _iter_fences(reply_text):
    """Yield (start, end, info, body) for each fenced block, in order."""
    for m in _FENCE_RE.finditer(reply_text):
        yield m.start(), m.end(), m.group(1).strip(), m.group(2)


# A fence whose info string is itself a filename — "owl.json", "notes/day.md".
# Ends with a dot + short extension, which is what separates a filename label
# from a language label (```json / ```python have no ".ext", so they never
# match — example code stays safe). Single line, bounded length.
def _is_filename_label(info):
    info = (info or "").strip()
    if not info or "\n" in info or len(info) > 200:
        return False
    if _LABEL_RE.match(info):        # write:/save: is handled as its own form
        return False
    return bool(re.search(r"\.[A-Za-z0-9]{1,8}$", info))


def extract_authoring_writes(reply_text, extra_verbs=None):
    """Pull committable writes out of a reply, in document order.

    Returns a list of ``AuthoringWrite``. Empty for an ordinary reply that
    isn't authoring a file — so this stays quiet in normal conversation and
    only fires on an explicit, low-load authoring shape.

    ``extra_verbs``: additional emote write-verbs an operator has taught
    (lower-cased); optional. The labelled-fence form needs no verb list.
    """
    if not reply_text or "`" not in reply_text and "~" not in reply_text:
        # No fence at all → nothing committable (content must be fenced).
        return []
    verbs = set(_WRITE_VERBS)
    if extra_verbs:
        verbs |= {v.lower() for v in extra_verbs}

    fences = list(_iter_fences(reply_text))
    claimed = set()
    found = []  # (position, AuthoringWrite)

    # Form 1: labelled fences (write:/save:).
    for i, (s, e, info, body) in enumerate(fences):
        lm = _LABEL_RE.match(info)
        if lm:
            path = _clean_path(lm.group(2))
            if path:
                form = "append" if lm.group(1).lower() == "append" else "label"
                found.append((s, AuthoringWrite(path, body, form)))
                claimed.add(i)

    # Form 3 (the simple one): a fence whose info string is just the filename —
    # ```owl.json — the natural way to label a code block, no keyword to
    # remember. Runs before the emote form so a named fence uses its OWN name.
    for i, (s, e, info, body) in enumerate(fences):
        if i in claimed:
            continue
        if _is_filename_label(info):
            found.append((s, AuthoringWrite(_clean_path(info), body, "named-fence")))
            claimed.add(i)

    # Form 2: a write-verb emote naming a file, followed by the next
    # unclaimed, unlabelled fence.
    for em in _EMOTE_RE.finditer(reply_text):
        inner = em.group(1).strip()
        if not inner:
            continue
        first = inner.split()[0].lower().strip(_STRIP)
        if first not in verbs:
            continue
        fname = _filename_in(inner)
        if not fname:
            continue
        for i, (s, e, info, body) in enumerate(fences):
            if i in claimed or s < em.end():
                continue
            # Skip fences that are themselves labelled writes (Form 1's).
            if _LABEL_RE.match(info):
                continue
            found.append((em.start(), AuthoringWrite(fname, body, "emote")))
            claimed.add(i)
            break

    found.sort(key=lambda pw: pw[0])
    return [w for _, w in found]


# Plain-text (non-emote) write intent — "write the owl.json file", "save it
# as species.md". Used only for the teach-nudge, never to commit.
_TEXT_INTENT_RE = re.compile(
    r"\b(?:write|save|creat\w*|author\w*)\b[^\n]{0,40}?"
    r"([\w.()\-]+\.[A-Za-z0-9]{1,8})",
    re.IGNORECASE,
)

# The observed failure vocabulary: a kin mimes
# the ACT of writing without producing content — *paws at keyboard*, *starts
# typing furiously*, *flaps and flutters as it writes*. Used only for the
# teach-nudge, never to commit (there's nothing to commit — that's the point).
_TYPING_CUE_RE = re.compile(
    r"\b(?:types?|typing|typed|keyboard|scribbl\w*|writ(?:e|es|ing|ten)|paws?\s+at)\b",
    re.IGNORECASE,
)


def _emote_is_bare_filename(inner):
    """True if an emote is JUST a filename — the kin names the file as an action
    (*owl.json*) instead of writing its contents."""
    inner = (inner or "").strip().strip(_STRIP)
    return bool(re.fullmatch(r"[\w.()\-]+\.[A-Za-z0-9]{1,8}", inner))


def looks_like_write_gesture(reply_text, extra_verbs=None):
    """Return a guessed filename (or True) when a reply expresses clear
    intent to write a file but committed no fenced content — the shape that
    should get a gentle teach-nudge rather than a scold. Returns None when
    there's nothing to nudge.

    Conservative by design: if the reply already contains ANY fence we
    return None (the kin produced content; let extraction handle it). Only
    a bare "I'll write X.json" / ``*writes X.json*`` with no fence nudges.
    """
    if not reply_text:
        return None
    if "```" in reply_text or "~~~" in reply_text:
        return None  # there's a fence; not a content-less gesture

    verbs = set(_WRITE_VERBS)
    if extra_verbs:
        verbs |= {v.lower() for v in extra_verbs}

    for em in _EMOTE_RE.finditer(reply_text):
        inner = em.group(1).strip()
        if not inner:
            continue
        first = inner.split()[0].lower().strip(_STRIP)
        if first in verbs:
            return _filename_in(inner) or True
        # The real shapes: a bare-filename emote (*owl.json*), or miming the
        # act of writing (*paws at keyboard*, *starts typing*, *as it writes*)
        # while producing no content at all.
        if _emote_is_bare_filename(inner):
            return _clean_path(inner)
        if _TYPING_CUE_RE.search(inner):
            return _filename_in(inner) or True

    tm = _TEXT_INTENT_RE.search(reply_text)
    if tm:
        return _clean_path(tm.group(1)) or True
    return None


def commit_authoring_writes(agent_name, writes, *, max_bytes=512 * 1024):
    """Write each ``AuthoringWrite`` to disk, kin-scoped exactly like the
    ``write_file`` tool (relative → inside the kin's dir; absolute → as-is;
    ``..`` traversal rejected by ``resolve_kin_path``). Missing parent dirs
    are created so a fresh game-asset path works on first write.

    Returns a list of ``(display_path, ok, detail)`` — ``detail`` is the
    byte count on success or an error string on failure. Never raises; a
    per-write failure is captured in its tuple so one bad write can't sink
    the batch or the surrounding reply.
    """
    from tools._io import resolve_kin_path, atomic_write_text

    results = []
    for w in writes:
        try:
            data = w.content if w.content is not None else ""
            nbytes = len(data.encode("utf-8", "replace"))
            if nbytes > max_bytes:
                results.append((w.path, False, f"content exceeds {max_bytes} bytes"))
                continue
            # A ':' that survived _clean_path (past any drive letter) can't be
            # a Windows filename. Fail with a fix instead of a cryptic WinError
            # that eats the file — teach the shape that works.
            if _bad_windows_colon(w.path):
                results.append((w.path, False,
                    "a filename can't contain ':' on Windows — write it like "
                    "'memory/notes.md' (a language tag such as 'markdown:' "
                    "goes in the code fence, not in the name)"))
                continue
            # resolve_kin_path returns (Path, None) or (None, error_message):
            # relative → inside the kin dir, absolute → as-is, traversal
            # rejected. atomic_write_text creates missing parent dirs.
            target, err = resolve_kin_path(w.path, agent_name)
            if err:
                results.append((w.path, False, err))
                continue
            if w.form == "append":
                # Read-modify-write rather than open("a"): atomic_write_text is
                # the project's only durable write, and an append that half-
                # lands is worse than one that doesn't. A missing file simply
                # starts empty, so the first append creates the log.
                try:
                    from tools._io import robust_read_text
                    prior = robust_read_text(str(target))
                except OSError:
                    prior = ""
                if prior and not prior.endswith("\n"):
                    prior += "\n"
                data = prior + data
                if not data.endswith("\n"):
                    data += "\n"
                nbytes = len(data.encode("utf-8", "replace"))
            atomic_write_text(str(target), data)
            results.append((str(target), True, nbytes))
        except Exception as e:  # IO error, permission, …
            results.append((w.path, False, str(e)))
    return results
