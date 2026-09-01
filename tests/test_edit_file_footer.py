# SPDX-License-Identifier: CC0-1.0
"""Standalone tests for edit_file's read_file-footer healing (no network).

read_file appends an out-of-band footer to the content it returns —
"\n\n[read_file: whole file (N lines, ~N bytes).]". Smaller models routinely
copy that footer into edit_file's old_string when the text they want to change
sits near the end of a read, and the literal match then fails with "not found".
edit_file strips a trailing footer as a fallback (only after the strict match
misses), and strips the same footer from new_string so it can't leak into the
file. These cases pin that behaviour and prove the strict path is unchanged.
"""

import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.edit_file import edit_file  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def write(p, text):
    # Bytes, not text mode — keep \n exactly (Windows text mode would
    # translate to \r\n and the kin's copied old_string uses \n).
    p.write_bytes(text.encode("utf-8"))


def read(p):
    return p.read_bytes().decode("utf-8")


def main():
    tmp = tempfile.mkdtemp()
    fp = pathlib.Path(tmp) / "note.md"
    base = "line one\nline two\nthe quick brown fox\nlast line\n"
    footer = "\n\n[read_file: whole file (4 lines, ~48 bytes).]"

    # 1. Footer pasted into BOTH old and new (the realistic shape): heals,
    #    edit lands, and the footer does NOT end up embedded in the file.
    write(fp, base)
    r = edit_file(str(fp),
                  "the quick brown fox\nlast line" + footer,
                  "the quick RED fox\nlast line" + footer)
    check("healed edit reports success", "replaced 1 occurrence" in r)
    check("healed edit notes the ignored footer", "ignored a read_file footer" in r)
    check("file got the real change", "the quick RED fox" in read(fp))
    check("footer did not leak into file", "[read_file:" not in read(fp))

    # 2. Strict path unchanged — a normal edit with no footer still works and
    #    is NOT reported as healed.
    write(fp, base)
    r = edit_file(str(fp), "brown", "green")
    check("plain edit works", "replaced 1 occurrence" in r)
    check("plain edit not marked healed", "footer" not in r)

    # 3. A garbled / partial footer that can't be auto-stripped still misses,
    #    but now with a steering hint instead of a bare "not found".
    write(fp, base)
    r = edit_file(str(fp), "nope\n[read_file: whole file", "x")
    check("partial-footer miss is reported", "not found" in r)
    check("partial-footer miss gives a hint", "metadata read_file adds" in r)

    # 4. A file that LEGITIMATELY contains the footer text edits via the literal
    #    match first — no false healing, the real string is replaced.
    write(fp, "docs: [read_file: whole file (1 lines, ~5 bytes).]\nmore\n")
    r = edit_file(str(fp), "[read_file: whole file (1 lines, ~5 bytes).]", "GONE")
    check("literal footer-text edit works", "replaced 1 occurrence" in r)
    check("literal footer-text edit not marked healed", "ignored a read_file" not in r)
    check("literal footer text was replaced", "docs: GONE" in read(fp))

    # 5. fetch_url's trailing markers are part of the same family and heal too.
    write(fp, base)
    r = edit_file(str(fp),
                  "the quick brown fox\nlast line\n\n[truncated]",
                  "the quick RED fox\nlast line\n\n[truncated]")
    check("fetch_url [truncated] marker heals", "replaced 1 occurrence" in r)
    check("[truncated] did not leak into file", "[truncated]" not in read(fp))

    write(fp, base)
    r = edit_file(str(fp),
                  "last line\n\n[response body capped at 1 MB; some content may be missing]",
                  "LAST LINE\n\n[response body capped at 1 MB; some content may be missing]")
    check("fetch_url [capped] marker heals", "replaced 1 occurrence" in r)
    check("[capped] marker did not leak into file", "capped at 1 MB" not in read(fp))

    # 6. A real "[1]" footnote-style trailing bracket is NOT a known marker, so
    #    it is left strictly alone — no false strip, no data loss.
    write(fp, "see note\n\n[1]\n")
    r = edit_file(str(fp), "see note\n\n[1]", "see footnote\n\n[1]")
    check("real [1] footnote ref edits literally (not stripped)",
          "replaced 1 occurrence" in r and "ignored" not in r)
    check("real [1] footnote ref preserved", "[1]" in read(fp))

    # 7. Trailing-whitespace mismatch — the most common edit miss. The model
    #    guesses "...arrived.\n\n" where the file has a single "...arrived.\n".
    #    Heals: the edit lands, the note says so, and the file's own trailing
    #    newline is preserved (no doubled / lost blank lines).
    write(fp, "a line\nthe last opinion.\nmore stuff\n")
    r = edit_file(str(fp),
                  "the last opinion.\n\n",
                  "the last opinion.\n\nbrand new line.")
    check("trailing-newline mismatch heals", "replaced 1 occurrence" in r)
    check("heal note flags the whitespace match", "ignoring whitespace" in r)
    check("new content landed", "brand new line." in read(fp))
    check("file structure preserved", read(fp) ==
          "a line\nthe last opinion.\n\nbrand new line.\nmore stuff\n")

    # 8. Exact whitespace match still works and is NOT flagged as healed.
    write(fp, "exact\n\nblock\n")
    r = edit_file(str(fp), "exact\n\nblock", "EXACT\n\nblock")
    check("exact match unaffected by ws fallback",
          "replaced 1 occurrence" in r and "trailing whitespace" not in r)

    # 9. Ambiguity guard — if the trimmed form appears more than once, DON'T
    #    guess which span; stay a clean miss with a steering hint.
    write(fp, "dup\nmiddle\ndup\n")
    r = edit_file(str(fp), "dup\n\n", "dup-changed\n\n")
    check("ambiguous trimmed match does not heal", "not found" in r)
    check("ambiguous miss still hints at whitespace", "trailing" in r)

    # 10. Per-line indentation mismatch (multi-line): model lost the indent. The
    #     line-structure heal lands the edit; the model's new content goes in as
    #     written (faithful replace — no doubling of the file's indentation).
    write(fp, "def foo():\n    return 1\nx = 9\n")
    r = edit_file(str(fp), "def foo():\nreturn 1", "def foo():\n    return 2")
    check("indent-mismatch multiline heals", "replaced 1 occurrence" in r)
    check("indent heal lands the new content", "return 2" in read(fp))
    check("indent heal didn't double-indent",
          read(fp) == "def foo():\n    return 2\nx = 9\n")

    # 11. CRLF file, model sends LF across a multi-line block: heals AND the
    #     file's CRLF line endings are preserved (no lone LF introduced).
    write(fp, "one\r\ntwo\r\nthree\r\n")
    r = edit_file(str(fp), "one\ntwo", "ONE\nTWO")
    check("CRLF-vs-LF multiline heals", "replaced 1 occurrence" in r)
    check("CRLF preserved through heal", read(fp) == "ONE\r\nTWO\r\nthree\r\n")

    # 12. Line-structure ambiguity: the same stripped block appears twice ->
    #     refuse to guess (no heal), so we never edit the wrong span.
    write(fp, "  block\nmid\n    block\nend\n")
    r = edit_file(str(fp), "BLOCKLESS\nblock", "x\ny")  # forces line-heal path
    check("non-matching content is a clean miss", "not found" in r)

    if _fails:
        print(f"\nFAILED {len(_fails)}: " + ", ".join(_fails))
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
