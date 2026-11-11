# SPDX-License-Identifier: CC0-1.0
"""A scheduled job marked `own_history` reads and writes only its own history.

Found on a real kin. A fiction-writing job ran four times a morning. Every run
was handed the kin's whole conversation -- so the person's real life leaked
into the fiction -- and every run's prompt and entry were saved back into that
conversation in the person's slot, a third of the kin's recent history. The kin
then could not tell its job's prompts from the person, and the fiction from
what had really been said.

Self-directed jobs need the opposite: they keep their continuity BY sharing the
conversation, so sharing stays the default. `own_history` is opt-in per job.

What this file pins:
  * no `own_history` -> no separate path (the default is unchanged)
  * `own_history: "name"` -> cron_history/name.jsonl; `true` -> entry-<index>
  * a job's history is read from its own file, not the conversation
  * a job's run is saved to its own file, and the conversation is untouched
  * the name can't climb out of the folder
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="cronownhist-")

import hearthkin_cron as hc  # noqa: E402
from kin_persistence import (  # noqa: E402
    create_agent, append_agent_conversation_turn, load_agent_conversation)

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Tester"
create_agent(KIN, blank_soul=True)
append_agent_conversation_turn(KIN, {"role": "user", "content": "real chat about kites"})
append_agent_conversation_turn(KIN, {"role": "assistant", "content": "kites reply"})
before = load_agent_conversation(KIN)

check("no own_history -> None", hc.own_history_path(KIN, {"prompt": "x"}, 0) is None)
p = hc.own_history_path(KIN, {"own_history": "story-journal"}, 2)
check("a named history lives in cron_history/<name>.jsonl",
      p is not None and p.name == "story-journal.jsonl"
      and p.parent.name == "cron_history")
p2 = hc.own_history_path(KIN, {"own_history": True}, 3)
check("true is named after the entry", p2 is not None and p2.name == "entry-3.jsonl")
p3 = hc.own_history_path(KIN, {"own_history": "../../escape"}, 0)
check("a name can't leave the folder",
      p3 is not None and p3.parent.name == "cron_history" and ".." not in p3.name)

# Saving a run: own file only.
hc._append_to_conversation(KIN, "job prompt", "job entry", history_path=p)
after = load_agent_conversation(KIN)
check("the conversation is untouched by an own-history run",
      len(after) == len(before))
lines = [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]
check("the run is in the job's own file",
      [m.get("content") for m in lines] == ["job prompt", "job entry"])

# Reading: own file only.
msgs = hc._build_messages(KIN, "next run", history_path=p)
contents = [m.get("content") for m in msgs if m.get("role") != "system"]
check("the job sees its own history",
      "job prompt" in contents and "job entry" in contents)
check("the job does not see the person's chat",
      "real chat about kites" not in contents)

# Positive control: the default still shares the conversation both ways.
msgs = hc._build_messages(KIN, "next run")
contents = [m.get("content") for m in msgs if m.get("role") != "system"]
check("control: a default job sees the chat", "real chat about kites" in contents)
hc._append_to_conversation(KIN, "default prompt", "default reply")
check("control: a default job's run is saved to the chat",
      len(load_agent_conversation(KIN)) == len(before) + 2)

print()
print("FAILED: %d" % len(_fails) if _fails else "ALL PASS")
sys.exit(1 if _fails else 0)
