"""Guard: no module defines the same thing twice at top level.

Python lets a file define `def foo` twice without complaint — the last one
wins, silently. So a file that has been mangled by tooling (a bad slice, a
patch applied twice, a botched merge) can double in size, carry two copies of
everything, and still import, still run, and still pass every other test.

That happened here on 2026-07-20: a patch script searched for a string that
wasn't there, `str.find` returned -1, and the slice that followed appended the
whole file to itself. kin_persistence.py went from 5,287 lines to 10,183 with
109 duplicated definitions, and the full suite stayed green through four
subsequent commits. Nothing was functionally wrong — the surviving copy won —
but half the file was a ghost, and the next person to edit it would have been
reading whichever copy they happened to land on.

This is cheap and it catches that whole class instantly.

Run:  python tests/test_no_duplicate_defs.py
"""

import collections
import io
import os
import pathlib
import sys

ROOT = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        _failures.append(name)


def top_level_names(path):
    """Names defined at column 0 — module-level defs and classes only.

    Indented ones are methods and nested helpers, where repetition across
    different classes is normal and fine.
    """
    names = []
    for line in io.open(path, encoding="utf-8"):
        if line.startswith(("def ", "class ")):
            head = line.split(None, 1)[1]
            names.append(head.split("(")[0].split(":")[0].strip())
    return names


def main():
    # Every module at the repo root, plus the packages that carry real logic.
    targets = sorted(ROOT.glob("*.py"))
    for sub in ("tools", "frame", "dialogs"):
        targets += sorted((ROOT / sub).glob("*.py"))

    print(f"checking {len(targets)} module(s) for duplicated top-level names\n")
    worst = 0
    for path in targets:
        try:
            names = top_level_names(path)
        except Exception as exc:
            check(f"{path.name}: readable", False)
            print("      " + str(exc))
            continue
        dupes = [n for n, c in collections.Counter(names).items() if c > 1]
        if dupes:
            worst = max(worst, len(dupes))
            check(f"{path.relative_to(ROOT)}: no duplicated definitions", False)
            print("      duplicated: " + ", ".join(sorted(dupes)[:8])
                  + (" ..." if len(dupes) > 8 else ""))
    if not worst:
        check(f"all {len(targets)} modules define each name once", True)

    print()
    if _failures:
        print(f"{len(_failures)} FAILURE(S)")
        print("A doubled file still imports and still passes every other test "
              "— check whether a patch or merge duplicated the content.")
        return 1
    print("No duplicate top-level definitions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
