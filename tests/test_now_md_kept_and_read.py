# SPDX-License-Identifier: CC0-1.0
"""now.md, the kin's own handoff, is kept and read (24 Nov 2026, from an
outside review).

Pinned:

  * every compaction keeps the now.md before it, dated, in the kin's now/
    folder (it used to keep only one, as now.md.previous);
  * the compaction request asks how the kin feels and what went unsaid,
    and its version moved so a saved copy is offered the update;
  * a wake-up that keeps its own history starts from the kin's now.md,
    since it never sees the main conversation's compaction note;
  * a wake-up that shares the main conversation doesn't get it twice.

Run: python tests/test_now_md_kept_and_read.py
"""

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="nowmd-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import hearthkin_cron as hc  # noqa: E402
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
(d / "soul.md").write_text("I am Sorrel.", encoding="utf-8")

kp.save_now_note(KIN, "First note.")
time.sleep(1.1)
kp.save_now_note(KIN, "Second note.")
time.sleep(1.1)
kp.save_now_note(KIN, "Third note.")
kept = sorted((d / "now").glob("now-*.md"))
check("every earlier now.md is kept, dated",
      [p.read_text(encoding="utf-8").strip() for p in kept] == ["First note.", "Second note."])
check("...and now.md holds the newest", (d / "now.md").read_text(encoding="utf-8").strip() == "Third note.")

req = kp.DEFAULT_COMPACT_REQUEST
check("the compaction request asks how the kin feels",
      "how you feel" in req and "wasn't said out loud" in req)
check("...and its version moved", kp.APP_PROMPT_REGISTRY["compact_request"]["version"] >= 2)

own = d / "own_history.jsonl"
own.write_text("", encoding="utf-8")
msgs = hc._build_messages(KIN, "wake up", enabled_tools=[], history_path=str(own))
text = " ".join(str(m.get("content")) for m in msgs)
check("a wake-up with its own history starts from now.md",
      "Third note." in text and "keeps its own history" in text)
msgs = hc._build_messages(KIN, "wake up", enabled_tools=[])
text = " ".join(str(m.get("content")) for m in msgs)
check("a wake-up sharing the main conversation doesn't get it added",
      "keeps its own history" not in text)

print()
if _fails:
    print(f"test_now_md_kept_and_read: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_now_md_kept_and_read: all checks passed")
