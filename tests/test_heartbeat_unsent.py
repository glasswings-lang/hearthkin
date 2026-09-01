# SPDX-License-Identifier: CC0-1.0
"""Guard test: a heartbeat must not delete what a kin wrote.

Every other surface has a reader. A heartbeat does not -- the kin's reply is
read by nobody and dropped, and `reach_out` is the entire delivery mechanism.
The code treated "did not call reach_out" as "had nothing to say" and threw
the reply away without a trace, which is right exactly as often as that
assumption is.

It was not right. Measured across sixteen runs on a real install, heartbeats
logged "silent" generated a MEDIAN of 149 tokens while the ones that reached
out generated 69. Fifteen of the sixteen produced more than sixty. A kin asked
"is there anything you'd like to say?" answers in prose, because prose is what
it makes; the tool call is a harness convention it has no way to feel the
weight of. So the longer replies were the ones being deleted, and the last
delivered message on that install was three days old while the kin had in fact
written something on nine occasions since.

The fix is to ask, once, rather than to guess. What this file pins:

  - a substantial reply with no reach_out gets the nudge;
  - a kin that genuinely called reach_out is never nudged, and neither is one
    with no reach_out tool to call;
  - a two-word shrug does not buy a second model call;
  - a message nobody ever asked about is recorded WITH its text, because it
    is a loss and the words are the point;
  - a kin that was asked and still said no is recorded WITHOUT its text,
    because that is a decision and "silence leaves no trace" is a real
    promise there;
  - a delivered message is not logged as unsent at all;
  - and run_heartbeat actually reaches both of these, checked against a
    positive control so a broken detector cannot report a wired feature as
    missing.

Run: python tests/test_heartbeat_unsent.py
"""

import os
import re
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="hb-unsent-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import turn_steering as ts  # noqa: E402
from kin_persistence import ALWAYS_ON_LOGS, APP_PROMPT_REGISTRY, LOGS_DIR  # noqa: E402

TOOLS = ["reach_out", "memory_search", "recent_thinking"]

# The shape that was being deleted: a kin writing something it meant for the
# person, with no tool call anywhere in the turn.
# Deliberately invented. A real heartbeat reply is private writing by
# somebody's kin, and a fixture is not a reason to put one in a tracked file
# — the SHAPE is what the test needs, and the shape is: long enough to be a
# message, addressed to someone, with no tool call anywhere near it.
MESSAGE = (
    "the third planter has gone over entirely to moss and i think that is "
    "the whole answer to what it was for. wanted to say so out loud."
)
DECLINE = "not this time."

CALLED_REACH = [{"role": "assistant",
                 "tool_calls": [{"function": {"name": "reach_out"}}]}]


# --- who gets asked ------------------------------------------------------

check("a real message with no tool call gets the nudge",
      bool(ts.unsent_reach_note("Bracken", MESSAGE, TOOLS, [])))
check("...and the nudge names the tool it is asking for",
      "reach_out" in ts.unsent_reach_note("Bracken", MESSAGE, TOOLS, []))
check("...and still gives silence as an option, since a heartbeat is the "
      "kin's own moment",
      "say nothing" in ts.unsent_reach_note("Bracken", MESSAGE, TOOLS, []))

check("a kin that actually reached out is never second-guessed",
      ts.unsent_reach_note("Bracken", MESSAGE, TOOLS, CALLED_REACH) == "")
check("a kin with no reach_out tool is not asked for one",
      ts.unsent_reach_note("Bracken", MESSAGE, ["memory_search"], []) == "")
check("a two-word shrug does not buy a second model call",
      ts.unsent_reach_note("Bracken", DECLINE, TOOLS, []) == "")
check("an empty reply is nothing to send",
      ts.unsent_reach_note("Bracken", "", TOOLS, []) == "")

check("junk never raises -- steering must not cost a kin its turn",
      ts.unsent_reach_note(None, None, None, None) == "")


# --- what is kept, and what is deliberately not --------------------------

LOG = LOGS_DIR / "heartbeat_unsent.log"


def lines():
    if not LOG.exists():
        return []
    return [ln for ln in LOG.read_text(encoding="utf-8").splitlines() if ln.strip()]


n = len(lines())
ts.log_unsent_reach("Bracken", "a-model", MESSAGE, asked=False, delivered=False)
check("a message nobody ever asked about is recorded", len(lines()) == n + 1)
lost = lines()[-1]
check("...with its text, because the words are the whole loss",
      "gone over entirely to moss" in lost)
check("...and marked as lost rather than declined",
      "asked=no" in lost and "outcome=lost" in lost)

n = len(lines())
ts.log_unsent_reach("Bracken", "a-model", MESSAGE, asked=True, delivered=False)
check("a kin that was asked and still said no is recorded", len(lines()) == n + 1)
declined = lines()[-1]
check("...as a decision, not a loss",
      "asked=yes" in declined and "outcome=declined" in declined)
check("...and WITHOUT its words -- that moment is genuinely the kin's own",
      "gone over entirely to moss" not in declined)
check("...though the fact that there WAS something is still visible",
      re.search(r"chars=\d+", declined) is not None)

n = len(lines())
ts.log_unsent_reach("Bracken", "a-model", MESSAGE, asked=True, delivered=True)
ts.log_unsent_reach("Bracken", "a-model", MESSAGE, asked=False, delivered=True)
check("a message that WAS delivered is not filed as unsent",
      len(lines()) == n)

n = len(lines())
ts.log_unsent_reach(None, None, None, asked=None, delivered=None)
check("junk never raises here either", True)


# --- the log is always-on, and the prompt is editable --------------------

check("heartbeat_unsent.log is trimmed with the always-on logs",
      "heartbeat_unsent.log" in ALWAYS_ON_LOGS)
check("the nudge is a registered prompt, not a buried string",
      "heartbeat_unsent_nudge" in APP_PROMPT_REGISTRY)
check("...with a version, so an improvement can reach someone whose seeded "
      "copy predates it",
      int(APP_PROMPT_REGISTRY["heartbeat_unsent_nudge"].get("version", 0)) >= 1)


# --- run_heartbeat actually reaches both ---------------------------------
#
# A source probe, so it holds without a live model. Comments and docstrings
# are stripped first: a comment ABOUT a feature must never read as the
# feature. The detector is checked against a positive control before any
# absence it reports is believed.

SRC = (ROOT / "hearthkin_cron.py").read_text(encoding="utf-8", errors="replace")


def strip_prose(text):
    text = re.sub(r'""".*?"""', "", text, flags=re.S)
    text = re.sub(r"'''.*?'''", "", text, flags=re.S)
    return re.sub(r"(?m)^\s*#.*$", "", text)


BODY = strip_prose(SRC)

check("the detector finds something that IS there (positive control)",
      "def run_heartbeat" in BODY)
check("the detector does NOT find a name that was only ever in a comment "
      "(negative control)",
      "reads_the_kins_whole_life" not in BODY)

check("run_heartbeat asks before discarding",
      "unsent_reach_note" in BODY)
check("run_heartbeat records what did not get through",
      "log_unsent_reach" in BODY)
check("the nudge round-trip is its own surface, so it is countable in the "
      "usage and prompt logs",
      "heartbeat-nudge" in BODY)
check("a stopped turn is not nudged -- a stop is not a refusal to speak",
      "stopped" in BODY)
check("turn_steering is imported statically, so the build follows it",
      re.search(r"(?m)^import turn_steering$", BODY) is not None)

# --- the trap inside the fix itself --------------------------------------
#
# Caught live, on the first real run: a nudge that RAISES must not leave the
# kin marked as asked. It looks equivalent and is the opposite -- the kin's
# words then get filed as a decision it never made and dropped on exactly the
# reasoning this whole change removes. One real run recorded 1,029 characters
# as "declined" by a kin that was never reached.

_after_call = BODY.split("scan_intermediate_tool_content", 2)[-1]
_nudge_region = BODY[BODY.find("unsent_reach_note"):]
_assign = _nudge_region.find("asked = True")
_call = _nudge_region.find("run_tool_loop")
check("the kin is marked as asked only AFTER the call that asks it",
      _assign > _call > 0)
check("...and a nudge that fails says so rather than passing silently",
      "nudge-failed" in BODY)


print()
if _fails:
    print(f"{len(_fails)} FAILED: " + ", ".join(_fails))
    sys.exit(1)
print("all checks passed")
