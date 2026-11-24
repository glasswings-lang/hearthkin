# SPDX-License-Identifier: CC0-1.0
"""Writing a moment down as it happens is one cheap call (24 Nov 2026).

A kin's memory came mostly from notes written later, in a batch, at about a
fiftieth of the size of what happened, and they flattened and invented. A
note written in the moment keeps what it was like. So `note` takes
file="journal" for today's journal, and the tool and the manual both say to
write when something matters, at the moment it happens. Pinned:

  * note(file="journal") appends to memory/journal/<today>.md, timestamped;
  * a second note adds to it, never replaces it;
  * the tool's own description tells the kin how and when;
  * so does the kin manual.

Run: python tests/test_journal_now.py
"""

import datetime
import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="journalnow-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hearthkin_paths import kin_dir  # noqa: E402
from tools.note import note  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Sorrel"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)
today = kin_dir(KIN) / "memory" / "journal" / f"{datetime.date.today().isoformat()}.md"

r = note("We found the color tonight.", file="journal", agent_name=KIN)
check("note(file='journal') writes today's journal", today.exists() and "journal" in r)
note("Then it was quiet, the good kind.", file="Journal", agent_name=KIN)
text = today.read_text(encoding="utf-8") if today.exists() else ""
check("...and a second note adds to it", "the color" in text and "the good kind" in text)
check("...each with its time", text.count("\n## ") >= 2)

doc = note.__doc__ or ""
check("the tool tells the kin how", 'file="journal"' in doc)
check("...and when: at the moment it happens", "at the" in doc and "moment it happens" in doc)

manual = (ROOT / "docs" / "kin_manual.md").read_text(encoding="utf-8")
check("the manual says so too", 'file="journal"' in manual and "at the moment it happens" in manual)

print()
if _fails:
    print(f"test_journal_now: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_journal_now: all checks passed")
