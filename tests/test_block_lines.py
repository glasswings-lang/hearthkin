# SPDX-License-Identifier: CC0-1.0
"""The person is told what a kin's reply blocks did, on every surface, under
one switch per surface (23 Nov 2026).

A kin on Telegram wrote to another kin with a ```to: block instead of the
message_kin tool. The message went, the KIN was told, and the person, right
there in the chat, heard nothing. Some blocks already showed (park moves, some
file saves), the rest never did. Now every kind of block gives the person one
plain line, and each surface has "Show what reply blocks did" beside "Show
tool calls" (Telegram and Discord; the desktop, which has no switch for tool
calls either, always shows them). Pinned:

  * a ```to: block gives the person a line naming who wrote to whom;
  * a refused one says it wasn't sent, and why;
  * a ```manual block gives a line naming the part;
  * the switch is on unless switched off, on Telegram and Discord;
  * Telegram sends the lines when on, and nothing when off;
  * a wake-up's post carries them under the switch of where it's going;
  * every place that acts on blocks collects the lines (a new place must too);
  * the check can fail: with no blocks, no lines.

Run: python tests/test_block_lines.py
"""

import datetime
import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="blocklines-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import kin_persistence as kp  # noqa: E402
import reply_actions  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


for k in ("Sorrel", "Wren"):
    kp.agent_dir(k).mkdir(parents=True, exist_ok=True)
    kp.save_agent_config(k, dict(kp.DEFAULT_AGENT_CONFIG))

TICK = "`" * 3

# --- the lines ---
lines = []
reply_actions.after_reply("Sorrel", f"Here.\n\n{TICK}to:Wren\nI heard the wind.\n{TICK}\n",
                          kin_mail_ok=True, prompted=True, shown=lines)
check("a to: block tells the person who wrote to whom",
      any("Sorrel wrote to Wren" in l for l in lines))

lines = []
reply_actions.after_reply("Sorrel", f"{TICK}to:Birch\nhello\n{TICK}\n",
                          kin_mail_ok=True, prompted=True, shown=lines)
check("a refused one says it wasn't sent, and why",
      any("wasn't sent" in l and "Birch" in l for l in lines))

lines = []
reply_actions.after_reply("Sorrel", f"{TICK}manual Surfaces\n{TICK}\n", shown=lines)
check("a manual block tells the person which part",
      any("manual" in l and "Surfaces" in l for l in lines))

lines = []
reply_actions.after_reply("Sorrel", "Just talking, no blocks.", kin_mail_ok=True,
                          shown=lines)
check("the check can fail: no blocks, no lines", lines == [])

# --- the switch ---
check("on unless switched off", reply_actions.block_lines_on({}) is True)
check("off when switched off",
      reply_actions.block_lines_on({"show_block_actions": False}) is False)
check("Telegram's and Discord's settings start with it on",
      kp.DEFAULT_TELEGRAM_CONFIG.get("show_block_actions") is True
      and kp.DEFAULT_DISCORD_CONFIG.get("show_block_actions") is True)

# --- Telegram sends them, or doesn't ---
import telegram_bot  # noqa: E402

bot = telegram_bot.TelegramBot.__new__(telegram_bot.TelegramBot)
bot.agent_name = "Sorrel"
sent = []
bot._send_chunked = lambda chat_id, text, *a, **k: sent.append(text)
bot._tg_section = lambda: {"show_block_actions": True}
bot._send_block_lines(1, ["📨 Sorrel wrote to Wren."])
check("Telegram tells the person when the switch is on",
      sent == ["📨 Sorrel wrote to Wren."])
sent.clear()
bot._tg_section = lambda: {"show_block_actions": False}
bot._send_block_lines(1, ["📨 Sorrel wrote to Wren."])
check("...and says nothing when it's off", sent == [])

# --- Discord's switch ---
import discord_bot  # noqa: E402

dbot = discord_bot.DiscordBot.__new__(discord_bot.DiscordBot)
dbot.agent_name = "Sorrel"
dbot.get_config = lambda: {"discord": {"show_block_actions": False}}
check("Discord reads its own switch", dbot._block_lines_on() is False)
dbot.get_config = lambda: {"discord": {}}
check("...on unless switched off", dbot._block_lines_on() is True)

# --- a wake-up's post ---
import hearthkin_cron as hc  # noqa: E402

hc._BLOCK_LINES["Sorrel"] = ["📨 Sorrel wrote to Wren."]
on = hc._block_lines_for("Sorrel", {"telegram": {}}, "telegram")
off = hc._block_lines_for("Sorrel", {"telegram": {"show_block_actions": False}},
                          "telegram")
check("a wake-up's Telegram post carries the lines", "Sorrel wrote to Wren" in on)
check("...unless Telegram's switch is off", off == "")
check("...and a Discord post follows Discord's switch",
      hc._block_lines_for("Sorrel", {"discord": {"show_block_actions": False}},
                          "discord") == "")

# --- every place that acts on blocks collects the lines ---
for f in ("telegram_bot.py", "discord_bot.py", "frame/chat_stream_mixin.py",
          "frame/rooms_mixin.py", "hearthkin_cron.py"):
    src = (ROOT / f).read_text(encoding="utf-8")
    calls = src.count("reply_actions.after_reply(")
    with_lines = src.count("shown=")
    # The heartbeat's own call is the one exception: nobody reads a
    # heartbeat, and what it sends to another kin is in Kin, Messages
    # between kin.
    allowed = 1 if f == "hearthkin_cron.py" else 0
    check(f"{f}: every block call collects lines for the person",
          calls - with_lines <= allowed)

# --- the main window and rooms, their own switches ---
from frame.chat_send_mixin import ChatSendMixin  # noqa: E402
from frame.rooms_mixin import RoomsMixin  # noqa: E402


class _W(ChatSendMixin, RoomsMixin):
    current_agent = "Sorrel"
    current_room = None


w = _W()
check("the main window shows tool calls and block lines unless switched off",
      w._desktop_shows("tool_calls") and w._desktop_shows("block_actions"))
c = kp.load_agent_config("Sorrel")
c["desktop_show_block_actions"] = False
kp.save_agent_config("Sorrel", c)
check("...and the kin's switch turns block lines off there",
      w._desktop_shows("block_actions") is False and w._desktop_shows("tool_calls"))
kp.create_room("Hearth", ["Sorrel", "Wren"]) if hasattr(kp, "create_room") else None
rc = kp.load_room_config("Hearth")
rc["show_tool_calls"] = False
kp.save_room_config("Hearth", rc)
w.current_room = "Hearth"
check("in a room, the room's own switch decides", w._shows_here("tool_calls") is False
      and w._shows_here("block_actions") is True)
check("a new room shows both, to start",
      kp.DEFAULT_ROOM_CONFIG.get("show_tool_calls") is True
      and kp.DEFAULT_ROOM_CONFIG.get("show_block_actions") is True)

print()
if _fails:
    print(f"test_block_lines: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_block_lines: all checks passed")
