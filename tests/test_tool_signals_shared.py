# SPDX-License-Identifier: CC0-1.0
"""Telegram's two tool signals, shared with Discord, plus the one stop list.

- The "_used: tool1, tool2_" line at the end of a reply: the receipt of
  which tools really ran. Discord had none.
- A "still working" message every N seconds of a long, quiet tool run.
  Discord had none, so a long tool run there looked like a hang.
- One list of stop words: Discord's missed "Stop!", "stop." and "!cancel".

Each lives once in chat_helpers; both apps call it (21 Nov 2026).

Run: python tests/test_tool_signals_shared.py
"""
import inspect
import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="toolsig-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chat_helpers import ProgressPing, tool_summary_footer, is_stop_word  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def call(name):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": "{}"}}]}


turns = [call("read_file"), {"role": "tool", "content": "x"},
         call("note"), call("read_file")]
check("the footer names each tool once, in the order it first ran",
      tool_summary_footer(turns, {}) == "\n\n_used: read_file, note_")
check("...is off when the setting is off",
      tool_summary_footer(turns, {"show_tool_summary": False}) == "")
check("...and is empty when no tool ran", tool_summary_footer([], {}) == "")

clock = [0.0]
sent = []
p = ProgressPing(90, sent.append, now=lambda: clock[0])
p.called("read_file")
check("no ping before the interval", sent == [])
clock[0] = 95
p.called("note")
check("a ping once the interval has passed, with time and tools",
      len(sent) == 1 and "1m 35s" in sent[0] and "2 tool calls" in sent[0]
      and "read_file, note" in sent[0])
clock[0] = 100
p.called("note")
check("...and not again straight away", len(sent) == 1)
clock[0] = 300
p.called("x", may_ping=False)
check("no ping while the per-call lines are showing", len(sent) == 1)
q = ProgressPing(0, sent.append, now=lambda: 10_000)
q.called("a")
check("0 means never", len(sent) == 1 and q.count == 1)

for w in ("stop", "Stop!", "stop.", "/stop", "/stop@Bot", "!cancel", "cancel"):
    check(f"'{w}' is a stop", is_stop_word(w))
for w in ("stop that", "please stop", "stopping by", "cancel the plan"):
    check(f"'{w}' is not", not is_stop_word(w))

# Both apps call the shared pieces.
import telegram_bot  # noqa: E402
import discord_bot  # noqa: E402
tg = inspect.getsource(telegram_bot)
dc = inspect.getsource(discord_bot)
check("Telegram's footer and ping are the shared ones",
      "tool_summary_footer(" in tg and "ProgressPing(" in tg
      and "progress_state" not in tg)
check("Discord has both now", "tool_summary_footer(" in dc
      and "ProgressPing(" in dc)
check("both use the one stop list",
      "is_stop_word" in tg and "is_stop_word" in dc
      and "_STOP_WORDS = " not in dc)

# Discord's own reply cap (it shared Telegram's, shown only in Telegram's
# window).
from chat_helpers import discord_reply_cap  # noqa: E402
check("Discord's own cap wins",
      discord_reply_cap({"discord": {"reply_token_cap": 3000},
                         "telegram_token_cap": 1024}) == 3000)
check("until it's set it follows Telegram's, so nothing changes",
      discord_reply_cap({"discord": {}, "telegram_token_cap": 1024}) == 1024)
check("and 900 if neither is set", discord_reply_cap({}) == 900)
bi = (ROOT / "frame" / "bot_integration_mixin.py").read_text(encoding="utf-8")
check("the Discord bot asks for Discord's options, not Telegram's",
      'surface="discord"' in bi and "discord_reply_cap(cfg)" in bi)

if _fails:
    print(f"{len(_fails)} FAILED: " + "; ".join(_fails))
    sys.exit(1)
print("test_tool_signals_shared: all checks passed")
