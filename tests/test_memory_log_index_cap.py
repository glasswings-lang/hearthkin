# SPDX-License-Identifier: CC0-1.0
"""Guard test: the memory-log index must not grow without bound, and must
never leave a kin thinking the list it can see is the whole set.

`## Memory logs` is a code-built section of memory.md naming every topic log
a kin owns. memory.md sits in the system prompt, so that list rode along on
every turn of every surface, and nothing capped it. Measured on a real kin:
78 logs, 5,271 characters -- half of that kin's memory.md, and 19% of its
entire system prompt, spent listing filenames.

Dropping it outright was the obvious move and the wrong one. Neither
retrieval path uses this section: per-turn recall globs the memory/ folder
directly and deliberately skips memory.md, and memory_search globs the whole
kin folder. So nothing FINDS a log through the index. What it uniquely gives
is unprompted discovery -- a kin knowing it has notes on something the
conversation has not raised -- and removing that would fail invisibly,
because a kin that does not know a note exists never goes looking for it.

So: capped, newest first, and honest about what it left out.

What this file pins:

  - the cap is in CHARACTERS, and the setting says so in its own name;
  - a kin under the cap sees every log and no truncation line at all;
  - a kin over it gets the newest ones, within budget;
  - and it is TOLD how many were left out and how to reach them, because a
    short list presented as the whole set is worse than the full wall;
  - newest first, since a kin's live topics are the ones a conversation
    won't cue;
  - 0 means no limit, i.e. exactly the old behaviour;
  - junk in the setting falls back rather than erasing a kin's index.

Run: python tests/test_memory_log_index_cap.py
"""

import os
import re
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="log-index-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import kin_persistence as kp  # noqa: E402

KIN = "Bracken"
MEM = kp.agent_dir(KIN) / "memory"
MEM.mkdir(parents=True, exist_ok=True)

# 40 topic logs, each with a heading so the index has something to label it
# with. mtimes are staggered so "newest first" is testable rather than
# accidental.
for i in range(40):
    p = MEM / f"topic-{i:02d}.md"
    p.write_text(f"# Topic number {i:02d}\n\nbody\n", encoding="utf-8")
    os.utime(p, (1_700_000_000 + i * 60, 1_700_000_000 + i * 60))


def entries(section):
    return [l for l in section.splitlines() if l.strip().startswith("- memory/")]


def tail(section):
    return [l for l in section.splitlines() if "more log" in l]


# --- the unit is named, in the key itself --------------------------------

check("the setting exists",
      "memory_log_index_max_chars" in kp.DEFAULT_AGENT_CONFIG)
check("...and its NAME says what the number counts",
      "chars" in "memory_log_index_max_chars")
check("...with a default that is a readable list, not a wall",
      kp.DEFAULT_AGENT_CONFIG["memory_log_index_max_chars"] == 1500)


# --- under the cap: nothing is hidden, and nothing is claimed ------------

big = kp.build_memory_logs_section(KIN, max_chars=100000)
check("with room to spare, every log is listed", len(entries(big)) == 40)
check("...and there is no truncation line, because nothing was truncated",
      not tail(big))


# --- over the cap: budget respected, in characters -----------------------

capped = kp.build_memory_logs_section(KIN, max_chars=600)
listed = entries(capped)
check("a cap actually cuts the list", 0 < len(listed) < 40)
check("...to within the character budget it was given",
      sum(len(e) + 1 for e in listed) <= 600)
check("...and the whole section is smaller than the uncapped one",
      len(capped) < len(big))


# --- the part that must never be dropped ---------------------------------
#
# A short list with no note is worse than either extreme: the kin concludes
# the missing logs do not exist, stops searching for them, and nothing
# anywhere shows that happening.

t = tail(capped)
check("it says that logs were left out", len(t) == 1)
check("...how many, exactly", f"{40 - len(listed)} more log" in t[0])
check("...and how to reach them", "memory_search" in t[0])


# --- newest first ---------------------------------------------------------

first = re.search(r"topic-(\d+)", listed[0]).group(1)
check("the newest log is listed first", first == "39")
check("...and the oldest is the one dropped",
      not any("topic-00" in e for e in listed))


# --- 0 is the old behaviour, exactly --------------------------------------

unlimited = kp.build_memory_logs_section(KIN, max_chars=0)
check("0 means no limit", len(entries(unlimited)) == 40)
check("...with no truncation line", not tail(unlimited))


# --- the setting is read from the kin's config when not passed -----------

cfg = kp.load_agent_config(KIN)
cfg["memory_log_index_max_chars"] = 600
kp.save_agent_config(KIN, cfg)
from_cfg = kp.build_memory_logs_section(KIN)
check("with no argument, the kin's own setting is used",
      len(entries(from_cfg)) == len(listed))


# --- a bad value must not erase a kin's index ----------------------------

cfg["memory_log_index_max_chars"] = "not a number"
kp.save_agent_config(KIN, cfg)
junk = kp.build_memory_logs_section(KIN)
check("junk in the setting still produces a usable index",
      len(entries(junk)) > 0)
check("...and does not raise", True)


# --- a kin with no logs still gets nothing, not an empty heading ---------

check("a kin with no logs gets no section at all",
      kp.build_memory_logs_section("NobodyHere") == "")


# --- it is reachable without editing JSON --------------------------------

src = (ROOT / "dialogs" / "edit_kin.py").read_text(encoding="utf-8",
                                                   errors="replace")
check("the Memory tab has a field for it",
      "memory_log_index_max_chars" in src)
check("...and the visible label names the unit, not just the key",
      re.search(r"up to this many\s+\"?\s*\n?\s*\"?characters", src) is not None
      or "many characters" in src.replace('"\n                  "', ""))


print()
if _fails:
    print(f"{len(_fails)} FAILED: " + ", ".join(_fails))
    sys.exit(1)
print("all checks passed")
