"""list_directory tests. Plain Python; run via tests/run_all.py.

Before this tool existed, a kin's only way to see what's in a folder was
`exec` + a hand-typed shell command — and real filenames (apostrophes,
commas, spaces) make PowerShell quoting brutally easy to get wrong. This
tool exists so "what's in this folder" is as reliable as read_file already
is for a single file.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.list_directory import list_directory

_failures = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _failures.append(label)


with tempfile.TemporaryDirectory() as td:
    with open(os.path.join(td, "a.txt"), "w", encoding="utf-8") as f:
        f.write("hello")
    with open(os.path.join(td, "z.txt"), "w", encoding="utf-8") as f:
        f.write("world!!")
    os.makedirs(os.path.join(td, "sub"))
    with open(os.path.join(td, "sub", "nested.txt"), "w", encoding="utf-8") as f:
        f.write("deep")

    # --- non-recursive: one level only ---------------------------------
    flat = list_directory(td)
    check("a.txt" in flat and "z.txt" in flat, "top-level files are listed")
    check("sub/" in flat, "a subfolder is listed, marked with a trailing /")
    check("nested.txt" not in flat,
          "non-recursive does not descend into subfolders")
    check("(5 bytes)" in flat, "a file's size is shown")

    # --- recursive: descends into subfolders ----------------------------
    deep = list_directory(td, recursive=True)
    check("sub/nested.txt" in deep,
          "recursive listing descends into a subfolder")
    check("(4 bytes)" in deep, "a nested file's size is shown too")

    # --- ordering: alphabetical, case-insensitive -----------------------
    check(flat.index("a.txt") < flat.index("z.txt"),
          "entries are sorted alphabetically")

    # --- a file with an apostrophe in the name, the case that broke exec ---
    with open(os.path.join(td, "You're here.txt"), "w", encoding="utf-8") as f:
        f.write("x")
    apostrophe_listing = list_directory(td)
    check("You're here.txt" in apostrophe_listing,
          "a filename with an apostrophe is listed correctly, no quoting to get wrong")

    # --- errors: missing folder, and a file mistaken for a folder -------
    missing = list_directory(os.path.join(td, "does-not-exist"))
    check(missing.startswith("list_directory:") and "no folder at" in missing,
          "a missing folder fails with a clear reason, not a crash")

    on_a_file = list_directory(os.path.join(td, "a.txt"))
    check("is a file, not a folder" in on_a_file,
          "pointing it at a file (not a folder) is refused with a clear reason")

    # --- an empty folder says so plainly ---------------------------------
    empty_dir = os.path.join(td, "empty")
    os.makedirs(empty_dir)
    check("is empty" in list_directory(empty_dir),
          "an empty folder is reported as empty, not a blank result")

# --- the entry cap: a folder degrades gracefully, never hangs or floods ---
with tempfile.TemporaryDirectory() as td:
    for i in range(30):
        with open(os.path.join(td, f"file{i:03d}.txt"), "w", encoding="utf-8") as f:
            f.write("x")
    # tools/__init__.py's `from .list_directory import list_directory`
    # shadows the submodule name on the `tools` package with the function
    # itself, so `import tools.list_directory` would hand back the
    # function, not the module, and its private constant would be
    # unreachable. The module is still registered in sys.modules under
    # its real name regardless — reach it there instead.
    ld = sys.modules["tools.list_directory"]
    old_cap = ld._MAX_ENTRIES
    ld._MAX_ENTRIES = 10
    try:
        capped = list_directory(td)
    finally:
        ld._MAX_ENTRIES = old_cap
    check(capped.count(".txt") == 10, "a folder over the cap returns exactly the cap")
    check("more" in capped and "Narrow the path" in capped,
          "a truncated listing says more exists, rather than looking complete")

print()
if _failures:
    print(f"FAILED: {len(_failures)}: {_failures}")
    sys.exit(1)
print("ALL LIST_DIRECTORY CHECKS PASSED")
