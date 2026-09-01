# SPDX-License-Identifier: CC0-1.0

"""Read a text file from disk and return its contents."""

from ._docx import DOCX_EXTS, DocxExtractionError, extract_docx_text
from ._io import (find_existing_path, path_within_kin, resolve_kin_path,
                  robust_decode)


# Hard cap so a single whole-file read can't blow the context window. The
# 64K limit is generous (~16K tokens) but bounded — bigger files get
# truncated with an explicit marker so the model knows there's more.
# This cap only applies to whole-file reads; when the kin passes
# start_line / line_count they're being explicit about what they want
# and we don't second-guess them.
_MAX_BYTES = 65536


def _sliced_result(text, total_bytes, start_line, line_count,
                    on_disk_note, heal_note):
    """Shared tail of read_file: turn decoded `text` into the line-range or
    whole-file (byte-capped) result, with its footer. `on_disk_note` names
    what `total_bytes` actually measures — the file's own bytes for a plain
    text file, or the EXTRACTED text's bytes for a .docx, since those are
    nowhere near the same number and the footer would mislead otherwise."""
    line_mode = start_line > 0 or line_count > 0
    if line_mode:
        lines = text.splitlines(keepends=True)
        total_lines = len(lines)
        start = max(0, (start_line or 1) - 1)
        if start >= total_lines:
            return (
                f"read_file: start_line={start_line} is past the end of "
                f"this file ({total_lines:,} lines total, ~{total_bytes:,} "
                f"{on_disk_note})." + heal_note
            )
        end = total_lines if line_count <= 0 else min(
            total_lines, start + int(line_count))
        slice_text = "".join(lines[start:end])
        hit_eof = end >= total_lines
        footer = (
            f"\n\n[read_file: returned lines {start + 1}-{end} of "
            f"{total_lines:,}"
            + (" (end of file)" if hit_eof else "")
            + f"; full file is ~{total_bytes:,} {on_disk_note}.]"
        )
        return slice_text + footer + heal_note

    truncated = total_bytes > _MAX_BYTES
    total_lines = len(text.splitlines())
    if truncated:
        # Truncate on the decoded text, not raw bytes, so a multi-byte
        # character never gets split mid-sequence.
        text = text.encode("utf-8")[:_MAX_BYTES].decode("utf-8", "ignore")
    if truncated:
        text += (
            f"\n\n[read_file: showing first {_MAX_BYTES:,} bytes of "
            f"~{total_bytes:,} {on_disk_note}"
            + (f" ({total_lines:,} lines total)" if total_lines else "")
            + "; pass start_line and line_count to fetch a specific slice.]"
        )
    else:
        text += (
            f"\n\n[read_file: whole file ({total_lines:,} lines, "
            f"~{total_bytes:,} {on_disk_note}).]"
        )
    return text + heal_note


def read_file(
    path: str,
    start_line: int = 0,
    line_count: int = 0,
    agent_name: str = "",
    confine_paths: bool = False,
) -> str:
    """Read the text file at `path` and return its contents. Use this
    when you need to see what's actually in a file before reasoning
    about it — don't claim a file's contents without reading them.

    Word documents (`.docx`) are supported too — you'll get the real
    document text (paragraphs, not markup), not the file's raw zip
    bytes. There's no need to reach for a shell command to unzip or
    convert one first; pass the `.docx` path straight to this tool.
    Legacy `.doc` (pre-2007 binary format) is not supported — you'll
    get a clear error, not garbage.

    Paths: a relative path like `memory.md` or `notes/today.md` is read
    from inside your own kin directory (`~/.hearthkin/kin/<your
    name>/`). Absolute paths (`C:\\Users\\...` on Windows, `/home/...`
    or `~/...` on POSIX) read from wherever they point. You cannot use
    `..` in a relative path to escape your folder — use an absolute
    path if that's really what you want.

    Line ranges (for big files like `conversation.jsonl`):

    - `start_line` — 1-indexed line to start reading from. Pass 0 (the
      default) to start from the beginning.
    - `line_count` — how many lines to read from `start_line`. Pass 0
      (the default) to read until end-of-file (or the byte cap, on a
      whole-file read).

    When you pass any line argument, the byte cap doesn't apply — you've
    asked for a specific slice and we trust you to ask for one that
    fits. Useful patterns:

    - First 50 lines of a file: `read_file("conversation.jsonl",
      line_count=50)`.
    - Lines 200-249: `read_file("conversation.jsonl", start_line=200,
      line_count=50)`.
    - Whole file (default): `read_file("memory.md")`. Capped at 64K
      bytes with a truncation marker if the file is larger.

    The returned text includes a brief footer noting the file's total
    line count, the byte size on disk, and whether the slice you asked
    for hit the end of the file — so you can plan further reads
    without guessing.

    Returns a brief error message instead of raising on missing files
    or read failures, so the model gets actionable feedback.
    """
    p, err = resolve_kin_path(path, agent_name, confine=confine_paths)
    if err:
        return f"read_file: {err}"
    # Forgiving fallback: if the path isn't there exactly, the model may have
    # gotten it ALMOST right (a phantom space after a paren, a case slip).
    # find_existing_path fuzzy-matches each component against disk, but only
    # when the match is unambiguous — so we heal the common fumble without
    # ever silently reading a different file than intended.
    heal_note = ""
    if not p.exists():
        healed = find_existing_path(p)
        if healed is None:
            return f"read_file: no file at {p}"
        # On a confined surface, re-assert containment after healing — a
        # symlink inside the kin dir could otherwise redirect the healed path
        # back out (audit J1).
        if confine_paths and not path_within_kin(healed, agent_name):
            return "read_file: that path resolves outside your kin folder."
        p = healed
        heal_note = (
            f"\n[read_file: note — the path you gave didn't exist exactly; "
            f"matched {p} (whitespace/case differences ignored). Use that "
            f"exact path next time.]"
        )
    if not p.is_file():
        return f"read_file: {p} is not a regular file."

    # A .docx is a zip archive, not text — the plain-text decoder below would
    # turn its raw bytes into unreadable garbage that *looks* like a read
    # rather than announcing itself as one. Route it through the same
    # extractor reading_bridge.py uses for a shared/uploaded .docx, so a kin
    # calling read_file on its own initiative gets the same real text a
    # human-named or uploaded .docx already gets, not raw zip bytes.
    if p.suffix.lower() in DOCX_EXTS:
        try:
            text = extract_docx_text(p)
        except DocxExtractionError as e:
            return f"read_file: {e}"
        total_bytes = len(text.encode("utf-8"))
        return _sliced_result(text, total_bytes, start_line, line_count,
                              "bytes of extracted text (the .docx itself is "
                              "smaller on disk, it's a compressed zip)",
                              heal_note)

    try:
        data = p.read_bytes()
    except Exception as e:
        return f"read_file: could not read {p}: {e}"

    total_bytes = len(data)

    # Decide the read shape: line-range or whole-file. Any non-zero
    # line argument switches to line-range mode.
    line_mode = start_line > 0 or line_count > 0

    if line_mode:
        # Decode the whole file once; line-mode trusts the kin to ask
        # for a fitting slice and skips the byte cap. robust_decode
        # handles UTF-8 → cp1252 → UTF-8-with-replace gracefully.
        text = robust_decode(data)
        return _sliced_result(text, total_bytes, start_line, line_count,
                              "bytes", heal_note)

    # Whole-file mode: apply the byte cap to keep context safe, working on
    # the raw bytes first (matches the on-disk size the footer reports).
    truncated = total_bytes > _MAX_BYTES
    total_lines = len(data.splitlines())
    if truncated:
        data = data[:_MAX_BYTES]
    text = robust_decode(data)
    if truncated:
        text += (
            f"\n\n[read_file: showing first {_MAX_BYTES:,} bytes of "
            f"~{total_bytes:,} on disk"
            + (f" ({total_lines:,} lines total)" if total_lines else "")
            + "; pass start_line and line_count to fetch a specific slice.]"
        )
    else:
        text += (
            f"\n\n[read_file: whole file ({total_lines:,} lines, "
            f"~{total_bytes:,} bytes).]"
        )
    return text + heal_note
