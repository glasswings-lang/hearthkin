# SPDX-License-Identifier: CC0-1.0
"""Who's fronting (21 Nov 2026), as agreed with the person it's for.

- /front <anything> sets it, exactly as typed; /front alone says who's set;
  /front off clears it. The answer is a plain line from Hearthkin
  ("Fronting: Juniper."), never the kin saying "Got it".
- Per conversation by default (a group and a private chat can have
  different ones of them at the front); one switch makes it the same
  everywhere.
- Only an account marked "This is me" (and the desktop) can set it, or gets
  it on their messages.
- It's saved on each message at the time, and shown beside the name.

Run: python tests/test_fronting.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="fronting-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fronting  # noqa: E402
from chat_helpers import person_row_text  # noqa: E402
from kin_persistence import _clean_chat_message  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# --- the setting -----------------------------------------------------------
check("set, exactly as typed",
      fronting.reply_line("Fern and Sam", "Kimi", "telegram:1")
      == "Fronting: Fern and Sam.")
check("alone, it says who's set",
      fronting.reply_line("", "Kimi", "telegram:1") == "Fronting: Fern and Sam.")
check("per conversation: the group isn't touched",
      fronting.get("Kimi", "telegram:group:-5") == "")
fronting.set("Kimi", "telegram:group:-5", "a little")
check("...and can have someone else",
      fronting.get("Kimi", "telegram:group:-5") == "a little"
      and fronting.get("Kimi", "telegram:1") == "Fern and Sam")
check("per kin falls out of it", fronting.get("Opal", "telegram:1") == "")
check("off clears it", fronting.reply_line("off", "Kimi", "telegram:1")
      == "Fronting: nobody set.")
check("never 'Got it'", "Got it" not in fronting.reply_line("Juniper", "Kimi", "x"))

fronting.set_everywhere(True)
fronting.set("Kimi", "desktop", "Juniper")
check("same everywhere: one setting counts in every conversation",
      fronting.get("Opal", "discord:9") == "Juniper"
      and fronting.get("Kimi", "telegram:group:-5") == "Juniper")
fronting.set_everywhere(False)
check("switching back keeps each conversation's own",
      fronting.get("Kimi", "telegram:group:-5") == "a little")

# --- on messages -------------------------------------------------------------
row = {"role": "user", "content": "hi", "ts": "2026-11-21T05:00:00"}
fronting.set("Kimi", "desktop", "Juniper")
fronting.stamp(row, "Kimi", "desktop")
check("stamped on the message when it's saved", row.get("fronting") == "Juniper")
check("...and the stamp survives saving",
      _clean_chat_message(row).get("fronting") == "Juniper")
check("shown beside the name",
      person_row_text(row, {})[0] == "[2026-11-21 05:00] [Juniper fronting] hi")
cfg = {"telegram": {"user_labels": {"7": "Sam"},
                    "user_is_operator": {"7": True}}}
tg_row = dict(row, sender_id=7)
check("with a name and the your-person mark",
      "[Sam, your person, Juniper fronting]" in
      person_row_text(tg_row, cfg, "telegram")[0])
fronting.set("Kimi", "desktop", "Moss")
check("a later switch doesn't rewrite what was said before",
      "Juniper fronting" in person_row_text(row, {})[0])

# --- a kin echoing the label is caught ---------------------------------------
from chat_helpers import clean_kin_reply, speakers_shown  # noqa: E402
tg_row2 = dict(tg_row, source="telegram:7")
known = speakers_shown([row, tg_row2], kin_cfg=cfg)
check("the cleanup knows the labels exactly as the kin was shown them",
      "Juniper fronting" in known
      and "Sam, your person, Juniper fronting" in known)
check("a reply opening with the desktop label has it taken off",
      clean_kin_reply("[Juniper fronting] hello", "Kimi", known_speakers=known)[0]
      == "hello")
check("...and with the full marked label",
      clean_kin_reply("[Sam, your person, Juniper fronting] hi", "Kimi",
                      known_speakers=known)[0] == "hi")
check("...but a reply that just says the name is left alone",
      clean_kin_reply("I see you, Juniper.", "Kimi", known_speakers=known)[0]
      == "I see you, Juniper.")


# --- only from the person's own account -------------------------------------
from telegram_bot import TelegramBot  # noqa: E402
sent = []
b = TelegramBot.__new__(TelegramBot)
b.agent_name = "Kimi"
b._send_chunked = lambda chat_id, t: sent.append(t)
b._user_is_me = lambda uid: uid == 7
b._handle_command("/front Juniper", 7, 7)
check("Telegram: /front from your own account sets it, plainly",
      sent[-1] == "Fronting: Juniper." and fronting.get("Kimi", "telegram:7") == "Juniper")
b._handle_command("/front Mallory", 8, -5, chat_type="group")
check("...from anyone else it doesn't",
      "Only an account" in sent[-1]
      and fronting.get("Kimi", "telegram:group:-5") == "a little")
b._handle_command("/front Juniper", 7, -5, chat_type="group")
check("...and it works in a group, for that group",
      fronting.get("Kimi", "telegram:group:-5") == "Juniper")

from discord_bot import DiscordBot  # noqa: E402


class _Ch:
    id = 55

    def __init__(self):
        self.sent = []

    async def send(self, t):
        self.sent.append(t)


class _Author:
    def __init__(self, uid):
        self.id = uid


class _Msg:
    def __init__(self, uid):
        self.author = _Author(uid)
        self.channel = _Ch()
        self.guild = object()


d = DiscordBot.__new__(DiscordBot)
d.agent_name = "Kimi"
d.get_config = lambda: {"discord": {"user_is_operator": {"7": True}}}
d._turn_lock = __import__("threading").Lock()
d._active_turns = {}
m = _Msg(7)
asyncio.run(d._handle_command(m, "front", "Fern"))
check("Discord: /front works in a channel, for that channel",
      m.channel.sent[-1] == "Fronting: Fern."
      and fronting.get("Kimi", "discord:55") == "Fern")
m = _Msg(8)
asyncio.run(d._handle_command(m, "front", "Mallory"))
check("...and not from someone else's account",
      "Only an account" in m.channel.sent[-1]
      and fronting.get("Kimi", "discord:55") == "Fern")

if _fails:
    print(f"{len(_fails)} FAILED: " + "; ".join(_fails))
    sys.exit(1)
print("test_fronting: all checks passed")
