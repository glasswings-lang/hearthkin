# SPDX-License-Identifier: CC0-1.0
"""A compaction still counts after the conversation is read back from disk
(20 Nov 2026).

_clean_chat_message (every load of conversation.jsonl and of the Telegram
store) dropped the marker's "compact" and "keep" fields. The note survived as
a plain system row, but the "stop here" was gone: Telegram never compacted at
all (it always reads from disk), and the desktop stopped at the next restart.
Found because a person compacted, restarted, and was immediately told the
conversation was 84% full: the kin was still being sent everything.
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="compactreload-", dir=os.environ.get("HEARTHKIN_HOME"))

import kin_persistence as K  # noqa: E402
from chat_helpers import compact_marker, view_after_compaction  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "reloadkin"
K.create_agent(KIN)
for i in range(30):
    K.append_agent_conversation_turn(
        KIN, {"role": "user" if i % 2 == 0 else "assistant",
              "content": f"old message {i}", "ts": "2026-11-19T10:00:00",
              "source": "telegram:1"})
K.append_agent_conversation_turn(
    KIN, dict(compact_marker("my note", 2, ts="2026-11-19T11:00:00",
                             preface="[hearthkin: compacted]"),
              source="telegram:1"))
K.append_agent_conversation_turn(
    KIN, {"role": "user", "content": "new message", "ts": "2026-11-19T12:00:00",
          "source": "telegram:1"})

conv = K.load_agent_conversation(KIN)
marks = [m for m in conv if m.get("compact")]
check("the marker comes back from disk as a marker", len(marks) == 1)
check("...with how many messages it keeps", marks and marks[0].get("keep") == 2)
view = view_after_compaction(conv)
texts = [m.get("content", "") for m in view]
check("what's sent after reloading is cut at the marker",
      not any("old message 0" in t for t in texts))
check("...keeping the note", any("my note" in t for t in texts))
check("...the kept messages", "old message 29" in texts)
check("...and what came after", "new message" in texts)
check("it's small, not the whole conversation", len(view) <= 5)

# The Telegram store goes through the same cleaner.
cleaned = K._clean_chat_message(
    {"role": "system", "content": "x", "compact": True, "keep": "3"})
check("the Telegram store keeps it too", cleaned.get("compact") is True
      and cleaned.get("keep") == 3)
check("an ordinary message gains nothing",
      "compact" not in K._clean_chat_message({"role": "user", "content": "hi"}))

# Wake-ups read the same conversation and must start from the note too.
import hearthkin_cron  # noqa: E402
wm = hearthkin_cron._build_messages(KIN, "wake up", enabled_tools=[])
wtexts = [m.get("content", "") for m in wm]
check("a wake-up is sent the note", any("my note" in t for t in wtexts))
check("...and not what came before the kept messages",
      not any("old message 0" == t for t in wtexts))

print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("all passed")
