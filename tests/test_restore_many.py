# SPDX-License-Identifier: CC0-1.0
"""Guard test: restoring several archives at once.

Restore is the door for a kin's OWN turns coming home, and unlike Import
its default mode weaves the incoming rows against what the kin already
has, by timestamp. That is the half that was already right.

What was missing was more than one file. An archive split across several
logs meant one pass per file — and passing them one at a time is not
merely tedious, it is WRONG in a specific way: `restore_rows` builds its
duplicate-detection set from the kin's existing turns and then extends it
as it walks the incoming rows. Hand it everything at once and it dedupes
across the files as well as against the kin. Loop over it instead and two
archives that overlap EACH OTHER both land in full.

Overlapping archives is the normal case. That is what having several
backups of one conversation means.

Run: python tests/test_restore_many.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault(
    "HEARTHKIN_HOME", tempfile.mkdtemp(prefix="hearthkin-restoremany-"))

from importers import restore_from_files  # noqa: E402
from importers._canonical import restore_rows  # noqa: E402
from kin_persistence import (  # noqa: E402
    create_agent, load_agent_conversation)

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def turn(ts, role, text):
    return {"role": role, "content": text, "ts": ts, "source": "import:openclaw"}


def write(rows, name):
    d = tempfile.mkdtemp(prefix="hearthkin-arch-")
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return p


# Two archives that OVERLAP each other — the middle turn is in both.
early = write([
    turn("2026-03-21T16:05:00", "user", "Hi?"),
    turn("2026-03-21T16:05:32", "assistant", "Hi! I'm here."),
    turn("2026-03-22T09:00:00", "user", "shared turn"),
], "conversation.jsonl")
later = write([
    turn("2026-03-22T09:00:00", "user", "shared turn"),
    turn("2026-03-23T03:30:00", "assistant", "later thought"),
], "conversation (2).jsonl")

create_agent("SpeakerTwo")
res = restore_from_files([later, early], "SpeakerTwo", mode="merge")
rows = load_agent_conversation("SpeakerTwo")

check("all the distinct turns came back", res["written"] == 4)
check("the turn present in BOTH archives landed once, not twice",
      sum(1 for r in rows if r.get("content") == "shared turn") == 1)
check("order is chronological, not the order the files were passed",
      [r["content"] for r in rows]
      == ["Hi?", "Hi! I'm here.", "shared turn", "later thought"])
check("provenance survives — restore doesn't relabel",
      all(r.get("source") == "import:openclaw" for r in rows))
check("no import markers were added; these are the kin's own turns",
      not any("hearthkin:" in (r.get("content") or "") for r in rows))

# The claim the single call rests on, stated directly.
existing = []
merged, stats = restore_rows(
    existing, [turn("2026-03-22T09:00:00", "user", "dupe"),
               turn("2026-03-22T09:00:00", "user", "dupe")], mode="merge")
check("a duplicate inside one batch is caught even with nothing existing",
      stats["skipped_duplicates"] == 1 and len(merged) == 1)

# Restoring the same thing twice is a no-op, so a nervous second press
# costs nothing.
again = restore_from_files([early, later], "SpeakerTwo", mode="merge")
check("restoring the same archives again adds nothing",
      again["written"] == 0)
check("...and the conversation is unchanged",
      len(load_agent_conversation("SpeakerTwo")) == len(rows))

# An unreadable file is reported, not swallowed, and doesn't cost the rest.
create_agent("SpeakerThree")
missing = os.path.join(tempfile.mkdtemp(), "nope.jsonl")
report = []
res2 = restore_from_files([early, missing], "SpeakerThree", mode="merge",
                          report=report)
check("one unreadable file doesn't cost you the others",
      res2["written"] == 3)
check("...and it is handed back rather than silently skipped",
      len(report) == 1 and report[0][0] == missing)

# Nothing readable at all is an error, not a silent empty success.
try:
    restore_from_files([missing], "SpeakerThree")
    raised = False
except Exception:
    raised = True
check("nothing readable at all raises rather than reporting success", raised)

if _fails:
    print("\n%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
    sys.exit(1)
print("\nall multi-file restore checks passed")
