# SPDX-License-Identifier: CC0-1.0
"""Standalone tests for forgiving path resolution (no network).

Small models routinely get a path ALMOST right — a phantom space after an
opening paren (`notes ( drafts, misc)` vs the real `notes (drafts, misc)`),
a doubled internal space, a case slip — and the filesystem tools then fail
hard with "no file at ...". The `_io` fuzzy-locate helpers heal that class of
fumble by matching each path component whitespace-/case-insensitively against
disk, but ONLY on an unambiguous unique match, so a read/edit never silently
touches a different file and a write never redirects onto a similar-named one.

Origin: Vesper (a local-model kin) could not read
`...\\notes (drafts, misc)\\long letter draft.txt` because it kept emitting
`notes ( drafts, misc)` — an extra space after the paren — on every attempt.
"""

import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.read_file import read_file    # noqa: E402
from tools.write_file import write_file   # noqa: E402
from tools.edit_file import edit_file      # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def main():
    root = pathlib.Path(tempfile.mkdtemp())
    real_dir = root / "notes (drafts, misc)"
    real_dir.mkdir()
    real_file = real_dir / "long letter draft.txt"
    real_file.write_text("the actual contents\n", encoding="utf-8")

    # 1. The exact Vesper fumble: phantom space after the paren heals to the
    #    real file, returns its contents, and flags the correction.
    garbled = str(root / "notes ( drafts, misc)" / "long letter draft.txt")
    r = read_file(garbled)
    check("garbled paren-space path heals to real file",
          "the actual contents" in r)
    check("heal is flagged in the result", "didn't exist exactly" in r)

    # 2. Case-only slip on a component heals too.
    r = read_file(str(root / "NOTES (DRAFTS, MISC)" / "long letter draft.txt"))
    check("case-insensitive component heals", "the actual contents" in r)

    # 3. Exact path works and is NOT flagged as healed.
    r = read_file(str(real_file))
    check("exact path works", "the actual contents" in r)
    check("exact path not flagged as healed", "didn't exist exactly" not in r)

    # 4. A genuinely-missing file (no fuzzy match) stays a clean error.
    r = read_file(str(root / "no such folder" / "nope.txt"))
    check("truly-missing path is a clean miss", "no file at" in r)

    # 5. Ambiguity guard: two sibling dirs differing ONLY by whitespace both
    #    normalize to the same form. A query that matches neither literally
    #    but both fuzzily must NOT heal. (Whitespace, not case — Windows'
    #    filesystem is case-insensitive so the OS would resolve a case slip
    #    itself before fuzzy matching ever runs; it is NOT whitespace-
    #    insensitive, so a space difference is a real, testable collision.)
    (root / "report (v1)").mkdir()
    (root / "report ( v1)").mkdir()  # extra space -> same normalized form
    (root / "report (v1)" / "x.txt").write_text("one\n", encoding="utf-8")
    r = read_file(str(root / "report(v1)" / "x.txt"))  # matches both fuzzily
    check("ambiguous parent refuses to heal", "no file at" in r)

    # 6. write_file: garbled parent folder redirects the write INTO the real
    #    folder instead of creating a mis-spelled duplicate sibling.
    r = write_file(str(root / "notes ( drafts, misc)" / "fresh.txt"), "hi\n")
    check("write into garbled parent reports success", "wrote" in r)
    check("write heal is flagged", "matched" in r)
    check("write landed in the real folder", (real_dir / "fresh.txt").exists())
    check("no mis-spelled duplicate folder was created",
          not (root / "notes ( drafts, misc)").exists())

    # 7. write_file: a genuinely-new folder is still created normally (no
    #    false heal — nothing on disk fuzzy-matches it).
    r = write_file(str(root / "brand new folder" / "note.txt"), "yo\n")
    check("genuinely-new folder is created, not healed",
          "wrote" in r and "matched" not in r)
    check("new folder actually exists",
          (root / "brand new folder" / "note.txt").exists())

    # 8. write_file never fuzzes the FILENAME: with the parent healed, the
    #    intended (new) filename is kept verbatim, not redirected onto the
    #    similarly-named existing file.
    r = write_file(str(root / "notes ( drafts, misc)" / "long letter draf.txt"),
                   "different\n")
    check("write keeps intended filename verbatim",
          (real_dir / "long letter draf.txt").exists())
    check("write did not clobber the similar existing file",
          real_file.read_text(encoding="utf-8") == "the actual contents\n")

    # 9. edit_file heals the path the same way reads do.
    r = edit_file(str(root / "notes ( drafts, misc)" / "long letter draft.txt"),
                  "the actual contents", "the edited contents")
    check("edit_file heals garbled path", "replaced 1 occurrence" in r)
    check("edit_file flags the path heal", "ignoring whitespace/case" in r)
    check("edit_file actually changed the real file",
          "the edited contents" in real_file.read_text(encoding="utf-8"))

    if _fails:
        print(f"\nFAILED {len(_fails)}: " + ", ".join(_fails))
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
