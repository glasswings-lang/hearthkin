"""No wx.CheckListBox anywhere in the app.

wx.CheckListBox draws its own ticks, so NVDA hears the names in it but never
whether they are ticked. For months that was put down to an "inconsistent
announce" and worked around with read-only mirrors and extra speech. It was
never inconsistent; the state simply isn't exposed. dialogs._shared.TickList
is the ordinary Windows list with checkboxes, which NVDA reads on every item
(proven through MSAA and by ear, 23 October 2026), and it takes the same
calls. This check keeps the old control from coming back.

A source scan, so it builds no windows.
Run:  python tests/test_no_checklistbox.py
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "__pycache__", "build", "dist", ".venv", "venv", "tests", "scripts"}
PATTERN = re.compile(r"\bwx\.CheckListBox\s*\(")

_failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        _failures.append(name)


found = []
scanned = 0
for here, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for f in files:
        if not f.endswith(".py"):
            continue
        path = os.path.join(here, f)
        scanned += 1
        with open(path, encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh, 1):
                if PATTERN.search(line):
                    found.append(f"{os.path.relpath(path, ROOT)}:{n}")

check(f"the scan actually read the app ({scanned} files)", scanned > 20)
check("no wx.CheckListBox is built; use dialogs._shared.TickList"
      + (" -- found at " + ", ".join(found) if found else ""), not found)

# Positive control: the pattern does catch the real thing.
check("the check can see a CheckListBox when one is there",
      bool(PATTERN.search("self.x = wx.CheckListBox(panel, choices=[])")))

if _failures:
    print(f"\ntest_no_checklistbox: {len(_failures)} failure(s)")
    sys.exit(1)
print("\ntest_no_checklistbox: all checks passed")
