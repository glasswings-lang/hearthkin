# SPDX-License-Identifier: CC0-1.0
"""A kin's practice.md, how it works in its own words, rides every turn
(24 Nov 2026).

A habit a kin learned ("I write a depth log whenever something matters,
without being asked") lasted only until its next compaction; then the person
taught it again, and ended up being the kin's memory for how to work. From
an outside review: a working-notes file the kin edits. Pinned:

  * with no practice.md, nothing is added;
  * with one, it rides right after the base prompt and before the soul;
  * past a short page, a note asks the kin to trim it, and nothing is cut;
  * distillation and consolidation get it too (they run as the whole kin);
  * every place that builds a kin's system prompt says which kin it is, so
    the file reaches every surface (this goes looking: a new call that
    forgets fails here without this test being changed);
  * the kin manual tells the kin the file exists.

Run: python tests/test_practice_file.py
"""

import os
import re
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="practice-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import kin_persistence as kp  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Sorrel"
d = kin_dir(KIN)
d.mkdir(parents=True, exist_ok=True)
SOUL = "I am Sorrel, and this is my soul."

without = kp.build_system_prompt(SOUL, "", kin_name=KIN)
check("with no practice.md, nothing is added", "practice.md" not in without)

habit = "When something matters, I write a depth log then, without being asked."
(d / "practice.md").write_text(habit, encoding="utf-8")
p = kp.build_system_prompt(SOUL, "a memory", kin_name=KIN)
base = kp.build_system_prompt("", "", kin_name="NobodyHere").strip()
check("it rides", habit in p)
check("...after the base prompt", bool(base) and p.startswith(base)
      and p.index(habit) > len(base))
check("...and before the soul", p.index(habit) < p.index(SOUL))
check("...headed as the kin's own, with the base prompt winning",
      "your own habits" in p and "the part above wins" in p)

long = "I keep this habit. " * 400
(d / "practice.md").write_text(long, encoding="utf-8")
p = kp.build_system_prompt(SOUL, "", kin_name=KIN)
check("past a short page, the kin is asked to trim it", "Trim it when you can" in p)
check("...and nothing is cut", long.strip() in p)

(d / "practice.md").write_text(habit, encoding="utf-8")
import frame_shared  # noqa: E402
(d / "soul.md").write_text(SOUL, encoding="utf-8")
check("distillation and consolidation get it too", habit in frame_shared.kin_self_prompt(KIN))

# Goes looking: every call that builds a kin's prompt names the kin.
missing = []
for path in ROOT.rglob("*.py"):
    rel = path.relative_to(ROOT).as_posix()
    if rel.startswith(("tests/", "build/", "dist/")) or "/." in rel:
        continue
    src = path.read_text(encoding="utf-8", errors="ignore")
    for m in re.finditer(r"build_system_prompt\(", src):
        if src[max(0, m.start() - 4):m.start()] == "def ":
            continue
        depth, i = 1, m.end()
        while i < len(src) and depth:
            depth += {"(": 1, ")": -1}.get(src[i], 0)
            i += 1
        if "kin_name=" not in src[m.end():i]:
            missing.append(f"{rel}:{src[:m.start()].count(chr(10)) + 1}")
check("every place that builds a kin's prompt names the kin (missing: %s)"
      % (", ".join(missing) or "none"), not missing)

manual = (ROOT / "docs" / "kin_manual.md").read_text(encoding="utf-8")
check("the kin manual tells the kin about practice.md", "**`practice.md`**" in manual)

print()
if _fails:
    print(f"test_practice_file: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_practice_file: all checks passed")
