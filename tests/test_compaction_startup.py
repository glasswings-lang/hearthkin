# SPDX-License-Identifier: CC0-1.0
"""After a compaction, a kin starts from its own words (24 Nov 2026).

A compacted kin was sent its note and the last few messages, and the manual
told it to "trust the note". From an outside review: a kin re-runs its
startup after every compaction, because the note orients it to the
conversation and its files orient it to itself; a note is a hint, never the
whole story. Its identity and memory already ride every turn, so what was
missing was its recent journal. Pinned:

  * the compaction row carries the kin's journal for yesterday and today;
  * a long day keeps its newest part and says where the start is;
  * it's frozen into the row: writing more journal later doesn't change it
    (the start of every later request must not move);
  * it says the full record is in conversation.jsonl, to check what matters;
  * a room's compaction carries no journal (the other kin see that row);
  * every place that compacts says whose compaction it is (goes looking);
  * the manual no longer says "trust the note".

Run: python tests/test_compaction_startup.py
"""

import datetime
import os
import re
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="cstartup-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chat_helpers as ch  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Sorrel"
j = kin_dir(KIN) / "memory" / "journal"
j.mkdir(parents=True, exist_ok=True)
today = datetime.date.today()
yday = today - datetime.timedelta(days=1)
(j / f"{yday.isoformat()}.md").write_text("Yesterday we found the color.", encoding="utf-8")
(j / f"{today.isoformat()}.md").write_text("This morning was quiet.", encoding="utf-8")

row = ch.compact_marker("My note.", 6, preface="[compacted here]", kin=KIN)
c = row["content"]
check("the row still holds the note", "My note." in c and c.startswith("[compacted here]"))
check("...and yesterday's journal", "Yesterday we found the color." in c)
check("...and today's", "This morning was quiet." in c)
check("...after the note", c.index("My note.") < c.index("Yesterday we found"))
check("...and says where the full record is", "conversation.jsonl" in c)

(j / f"{today.isoformat()}.md").write_text("This morning was quiet. And then more.", encoding="utf-8")
check("it's frozen: writing more later doesn't change the row", "And then more" not in row["content"])

long = "".join(f"line {i} of a long day. " for i in range(2000))
(j / f"{today.isoformat()}.md").write_text(long, encoding="utf-8")
c = ch.compact_marker("n", 6, kin=KIN)["content"]
check("a long day keeps its newest part", "line 1999 of a long day." in c and "line 0 of" not in c)
check("...and says where the start is", "the start of this day is in" in c)

check("no kin, no journal", "journal" not in ch.compact_marker("n", 6)["content"])

# Goes looking: every compaction outside the tests says whose it is.
missing = []
for path in ROOT.rglob("*.py"):
    rel = path.relative_to(ROOT).as_posix()
    if rel.startswith(("tests/", "build/", "dist/")):
        continue
    src = path.read_text(encoding="utf-8", errors="ignore")
    for m in re.finditer(r"compact_marker\(", src):
        if src[max(0, m.start() - 4):m.start()] == "def ":
            continue
        depth, i = 1, m.end()
        while i < len(src) and depth:
            depth += {"(": 1, ")": -1}.get(src[i], 0)
            i += 1
        args = src[m.end():i]
        if "kin=" not in args and args.strip():
            missing.append(f"{rel}:{src[:m.start()].count(chr(10)) + 1}")
check("every compaction says whose it is (missing: %s)" % (", ".join(missing) or "none"),
      not missing)

rooms = (ROOT / "frame" / "rooms_mixin.py").read_text(encoding="utf-8")
check("a room's compaction carries no journal", "preface=preface,\n                             kin=None)" in rooms)

manual = (ROOT / "docs" / "kin_manual.md").read_text(encoding="utf-8")
check("the manual no longer says 'trust the note'", "Trust the note" not in manual)

print()
if _fails:
    print(f"test_compaction_startup: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_compaction_startup: all checks passed")
