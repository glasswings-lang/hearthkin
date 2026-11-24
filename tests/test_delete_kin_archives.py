# SPDX-License-Identifier: CC0-1.0
"""Deleting a kin moves its folder to kin-archive; nothing is erased
(24 Nov 2026, message audit).

Delete used to erase the whole kin folder, and with it every copy Hearthkin
had told the person was "kept on the computer": cleared conversations,
backups, Telegram archives, memory, journals. Pinned:

  * the kin leaves the kin list;
  * its whole folder, copies included, is in kin-archive, named with the date;
  * deleting a second kin of the same name keeps both;
  * an unsafe name is still refused;
  * the confirm box says it moves and erases nothing.

Run: python tests/test_delete_kin_archives.py
"""

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="delkin-")
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
(d / "backups" / "cleared").mkdir(parents=True, exist_ok=True)
(d / "soul.md").write_text("I am Sorrel.", encoding="utf-8")
(d / "backups" / "cleared" / "conversation-old.jsonl").write_text("{}\n", encoding="utf-8")

moved = kp.delete_agent(KIN)
check("the kin leaves", not d.exists() and KIN not in kp.list_agents())
check("its folder is in kin-archive, dated",
      moved is not None and moved.parent.name == "kin-archive"
      and moved.name.startswith(f"{KIN}-deleted-"))
check("...with every copy that was kept",
      moved is not None and (moved / "backups" / "cleared" / "conversation-old.jsonl").exists()
      and (moved / "soul.md").exists())

time.sleep(1.1)
d.mkdir(parents=True, exist_ok=True)
(d / "soul.md").write_text("A second Sorrel.", encoding="utf-8")
moved2 = kp.delete_agent(KIN)
check("a second kin of the same name is kept too",
      moved2 is not None and moved2 != moved and moved.exists() and moved2.exists())

try:
    kp.delete_agent("../escape")
    refused = False
except ValueError:
    refused = True
check("an unsafe name is still refused", refused)

src = (ROOT / "frame" / "kin_mgmt_mixin.py").read_text(encoding="utf-8")
check("the confirm box says it moves and erases nothing",
      "kin-archive folder" in src and "Nothing is erased" in src
      and "It cannot be undone" not in src)

print()
if _fails:
    print(f"test_delete_kin_archives: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_delete_kin_archives: all checks passed")
