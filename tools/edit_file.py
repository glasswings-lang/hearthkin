# SPDX-License-Identifier: CC0-1.0

"""Replace a unique substring in a file."""

import re

from ._io import (
    atomic_write_text,
    backup_identity_file,
    find_existing_path,
    identity_misplacement_note,
    path_within_kin,
    resolve_kin_path,
    robust_read_text,
)


# Several tools append an out-of-band marker onto the content they return,
# in a shared "\n\n[marker]" trailing shape:
#   read_file -> "[read_file: whole file (15 lines, ~679 bytes).]" (+ its
#                line-range / truncated siblings, all "[read_file: ...]")
#   fetch_url -> "[truncated]" / "[response body capped at 1 MB; ...]"
# Those markers are metadata, NOT part of the file on disk. Models (smaller
# local ones especially) routinely copy the marker into old_string when the
# text they want to change sits near the end of a read, and the literal
# match then fails with "not found" — a confusing dead end the kin can't
# see the cause of. We strip a trailing KNOWN marker as a fallback, only
# after the strict match has already failed, so an edit to a file that
# legitimately contains that text (e.g. this project's own tool docs) is
# never silently rewritten. Deliberately matches a known set, not "any
# trailing [bracket]" — a file line ending in a real "[1]" footnote ref
# must not be eaten. read_file's marker has no internal ']', so [^\]]*
# matches the whole thing.
_TRAILING_TOOL_MARKER_RE = re.compile(
    r"\s*\[(?:read_file:[^\]]*"
    r"|truncated"
    r"|response body capped[^\]]*)\]\s*$"
)


def _find_stripped_line_span(text, old_string):
    """Locate old_string in `text` by matching whole lines with each line's
    leading/trailing whitespace (and CRLF) stripped — the heal for "model got
    the indentation or line endings slightly wrong, content right." Returns the
    (start, end) CHARACTER span of the matching run of file lines (end excludes
    the final line's newline so the file's own line breaks survive), or None.

    Only returns a span when EXACTLY ONE contiguous run of file lines matches —
    if zero or several match we don't guess. Leading/trailing blank lines in
    old_string (a model's speculative surrounding blank lines) are dropped
    before matching. The caller replaces the whole matched file lines with
    new_string verbatim, so the model's intended content/indentation is honored
    and the file's indentation is never doubled."""
    old_lines = [ln.strip() for ln in old_string.splitlines()]
    while old_lines and not old_lines[0]:
        old_lines.pop(0)
    while old_lines and not old_lines[-1]:
        old_lines.pop()
    if not old_lines:
        return None
    file_lines = text.splitlines(keepends=True)
    stripped = [ln.strip() for ln in file_lines]
    offsets = []
    pos = 0
    for ln in file_lines:
        offsets.append(pos)
        pos += len(ln)
    n = len(old_lines)
    span = None
    for i in range(len(file_lines) - n + 1):
        if stripped[i:i + n] == old_lines:
            if span is not None:
                return None  # more than one run matches — don't guess
            last = file_lines[i + n - 1]
            span = (offsets[i], offsets[i + n - 1] + len(last.rstrip("\r\n")))
    return span


def edit_file(path: str, old_string: str, new_string: str, agent_name: str = "",
              confine_paths: bool = False) -> str:
    """Replace `old_string` with `new_string` in the file at `path`.
    `old_string` must appear exactly once in the file — if it's not
    there, the call fails; if it appears more than once, the call
    fails with the count, and you should retry with more surrounding
    context in `old_string` to disambiguate.

    Paths: a relative path like `memory.md` or `notes/today.md` resolves
    inside your own kin directory (`~/.hearthkin/kin/<your name>/`).
    Absolute paths (`C:\\Users\\...` on Windows, `/home/...` or `~/...`
    on POSIX) go wherever they point. You cannot use `..` in a relative
    path to escape your folder — use an absolute path if that's really
    what you want.

    Atomic: either the whole edit lands or the original file is untouched.
    Returns a confirmation string when the edit succeeds, or a clear
    error string (no exception) when it doesn't. Use this for targeted
    edits instead of `write_file` whenever you know the existing
    surrounding text — it's much safer than rewriting the whole file
    and accidentally dropping content you didn't intend to.
    """
    if not isinstance(old_string, str) or not isinstance(new_string, str):
        return "edit_file: old_string and new_string must both be strings."
    if old_string == "":
        return "edit_file: old_string was empty (would match everywhere)."
    p, err = resolve_kin_path(path, agent_name, confine=confine_paths)
    if err:
        return f"edit_file: {err}"
    # Forgiving fallback: the model may have gotten the path ALMOST right (a
    # phantom space after a paren, a case slip). find_existing_path fuzzy-
    # matches each component against disk, but only on an unambiguous match,
    # so we never edit a different file than intended.
    path_heal_note = ""
    if not p.exists():
        healed = find_existing_path(p)
        if healed is None:
            return f"edit_file: no file at {p}"
        # Re-assert containment after healing on a confined surface (audit J1).
        if confine_paths and not path_within_kin(healed, agent_name):
            return "edit_file: that path resolves outside your kin folder."
        p = healed
        path_heal_note = f" (matched path {p}, ignoring whitespace/case)"
    if p.is_dir():
        return f"edit_file: {p} is a directory, not a file."
    try:
        # robust_read_text falls back UTF-8 → cp1252 → UTF-8-with-replace
        # so a Markdown file with Windows smart-character bytes (em-dash
        # 0x97, en-dash 0x96, smart quotes) doesn't take out the edit.
        # NOTE: the file is rewritten as UTF-8 below regardless of how it
        # was read, so a cp1252-encoded file becomes UTF-8 after the first
        # edit. For Markdown that's a quiet improvement; for files whose
        # encoding matters externally, that's worth knowing.
        text = robust_read_text(p)
    except Exception as e:
        return f"edit_file: could not read {p}: {e}"
    count = text.count(old_string)

    # Fallback heal: if the literal match failed, the kin may have copied a
    # tool's trailing marker (read_file's footer, fetch_url's [truncated])
    # into old_string. Strip it and try again. Only fires on a miss, so the
    # strict path is unchanged when the text really is in the file. If we
    # heal old_string, strip the same trailing marker from new_string too —
    # otherwise a kin that pasted the marker into both would leave it
    # embedded in the file after the replace.
    healed = False
    if count == 0:
        stripped = _TRAILING_TOOL_MARKER_RE.sub("", old_string)
        if stripped and stripped != old_string and text.count(stripped) >= 1:
            old_string = stripped
            new_string = _TRAILING_TOOL_MARKER_RE.sub("", new_string)
            count = text.count(old_string)
            healed = True

    # Fallback heal 2: trailing-whitespace mismatch. A model can't see exact
    # trailing newlines/spaces, so it guesses — old_string ends "...line.\n\n"
    # where the file has a single "...line.\n", or vice versa — and the literal
    # match misses. If trimming trailing whitespace off old_string yields a
    # UNIQUE match, edit that span and trim new_string's trailing whitespace to
    # match, so the model's speculative trailing newlines aren't injected and
    # the file's own trailing whitespace is left as-is. Unique-only: if the
    # trimmed form appears more than once we don't guess which span was meant.
    healed_ws = False
    if count == 0:
        old_trim = old_string.rstrip()
        if old_trim and old_trim != old_string and text.count(old_trim) == 1:
            old_string = old_trim
            new_string = new_string.rstrip()
            count = 1
            healed_ws = True

    # Fallback heal 3: per-line whitespace / line-ending (CRLF vs LF) mismatch.
    # The model got indentation or line endings slightly off but the content is
    # right. Match line-structure with each line stripped; on a unique run,
    # replace those whole file lines with new_string (rendered to the file's
    # line-ending style) — lenient match, faithful replace, no indent stacking.
    line_new_text = None
    if count == 0:
        span = _find_stripped_line_span(text, old_string)
        if span:
            s, e = span
            repl = new_string
            if "\r\n" in text:
                repl = repl.replace("\r\n", "\n").replace("\n", "\r\n")
            line_new_text = text[:s] + repl + text[e:]
            count = 1
            healed_ws = True

    if count == 0:
        hint = ""
        if "[read_file:" in old_string:
            hint = (
                " — your old_string contains a '[read_file: ...]' footer, "
                "which is metadata read_file adds and is NOT part of the "
                "file. Remove that footer line from old_string and retry."
            )
        elif old_string != old_string.rstrip():
            hint = (
                " — old_string ends with whitespace/newlines that may not "
                "match the file exactly. Try it without the trailing blank "
                "line(s)."
            )
        return f"edit_file: {old_string!r} not found in {p}{hint}"
    if count > 1:
        return (
            f"edit_file: {old_string!r} appears {count}x in {p}; "
            f"add more surrounding context to make it unique."
        )
    new_text = line_new_text if line_new_text is not None \
        else text.replace(old_string, new_string, 1)
    # Surgical by nature, but a wrong old_string can still swallow most of a
    # file -- and this is the soul. Cheap insurance, same as write_file.
    backup_note = backup_identity_file(p, agent_name)
    try:
        atomic_write_text(p, new_text)
    except Exception as e:
        return f"edit_file: could not write {p}: {e}"
    if healed:
        note = " (ignored a read_file footer in your old_string)"
    elif healed_ws:
        note = " (matched ignoring whitespace/indentation/line-ending differences)"
    else:
        note = ""
    return (f"edit_file: replaced 1 occurrence in {p}{note}{path_heal_note}"
            f"{backup_note}{identity_misplacement_note(p, agent_name)}")
