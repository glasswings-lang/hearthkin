# SPDX-License-Identifier: CC0-1.0
"""Who said what, kept apart from the words and named the same way
everywhere (history storage in the shared core, 21 Nov 2026).

- Discord baked the speaker's name and "your person" mark into the stored
  words. Now it stores the words, the account ID and the name Discord
  showed, like Telegram; the name the person was given in Hearthkin and
  the mark are put in front when read, so a later label or a later "This
  is me" applies to old messages too. Old rows with a name baked in still
  read the same.
- Discord and wake-ups sent people's messages from other places with no
  name and no time, so a group member's words read like the kin's own
  person's. Now every surface sends "[time] [Name] words" through one rule
  (chat_helpers.person_row_text).

Run: python tests/test_speaker_names_stored_apart.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="speakers-", dir=(os.environ.get("HEARTHKIN_HOME") or None))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hearthkin_paths import kin_dir  # noqa: E402
import kin_persistence as kp  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Moss"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)
CID = "4242"
CFG = dict(kp.DEFAULT_AGENT_CONFIG)
CFG["model"] = "stub-model"
CFG["telegram"] = {"user_labels": {"5": "Jo"},
                   "user_is_operator": {"5": True},
                   "group_private_sees": {"-100": True}}
CFG["discord"] = {"user_labels": {"77": "Jo"},
                  "user_is_operator": {"77": True},
                  "channel_sees_private": {CID: True}}
kp.save_agent_config(KIN, CFG)
TS = "2026-11-21T03:00:00"

print("--- the one rule ---")
from chat_helpers import person_row_text  # noqa: E402
text, name = person_row_text(
    {"role": "user", "content": "hello", "ts": TS, "sender_id": 77,
     "sender_name": "Roswitha"}, CFG, "discord")
check("the label wins, the mark is by account, the time leads",
      text == "[2026-11-21 03:00] [Jo, your person] hello")
text, _ = person_row_text(
    {"role": "user", "content": "hi", "ts": TS, "sender_id": 99,
     "sender_name": "Jo"}, CFG, "discord")
check("someone else using the name is marked as not the person",
      "[Jo, not your person] hi" in text)
text, _ = person_row_text({"role": "user", "content": "[Ann]: old words"},
                          CFG)
check("the oldest rows' baked-in \"[Name]: \" reads as a name",
      text == "[Ann] old words")

print("--- Discord stores the words and the speaker apart ---")
import discord_bot as db  # noqa: E402
bot = db.DiscordBot.__new__(db.DiscordBot)
bot.agent_name = KIN
bot.get_config = lambda: kp.load_agent_config(KIN)
bot._dm_channels = set()
bot._persist_turn(CID, "user", "the words", False, said="the words",
                  speaker={"sender_id": "77", "sender_name": "Roswitha"},
                  ts=TS)
row = kp.load_discord_history(KIN)[CID][-1]
check("the stored words are only the words", row["content"] == "the words")
check("...with the account and the name beside them",
      row.get("sender_id") == "77" and row.get("sender_name") == "Roswitha")
kp.append_discord_turn(KIN, CID, {"role": "user", "content": "[Ann] from before",
                                  "ts": TS})
hist = bot._channel_history(CID, False)
users = [t["content"] for t in hist if t["role"] == "user"]
check("read back labelled and marked, with its time",
      users[0] == "[2026-11-21 03:00] [Jo, your person] the words")
check("an old row with the name baked in still reads the same",
      users[1] == "[2026-11-21 03:00] [Ann] from before")

print("--- a label set later applies to old messages ---")
c2 = kp.load_agent_config(KIN)
c2["discord"]["user_labels"]["77"] = "Sam"
kp.save_agent_config(KIN, c2)
hist = bot._channel_history(CID, False)
check("the new name shows on the old message",
      "[Sam, your person] the words"
      in [t["content"] for t in hist if t["role"] == "user"][0])
c2["discord"]["user_labels"]["77"] = "Jo"
kp.save_agent_config(KIN, c2)

print("--- other places' messages keep their names and times ---")
kp.save_agent_conversation(KIN, [
    {"role": "user", "content": "from my phone", "ts": TS,
     "source": "telegram:5", "sender_id": 5,
     "sender_attribution": "R (@r)"},
    {"role": "assistant", "content": "got it", "ts": TS,
     "source": "telegram:5"},
    {"role": "user", "content": "group words", "ts": TS,
     "source": "telegram:group:-100", "sender_id": 8,
     "sender_attribution": "Kai (@kai)"},
])
hist = bot._channel_history(CID, True)
users = [t["content"] for t in hist if t["role"] == "user"]
check("Discord: a Telegram message keeps its name, mark and time",
      any(u.startswith("[2026-11-21 03:00] [Jo, your person]")
          and "from my phone" in u for u in users))

import hearthkin_cron as hc  # noqa: E402
msgs = hc._build_messages(KIN, "wake")
users = [m["content"] for m in msgs if m["role"] == "user"]
check("wake-up: the person's message keeps its name and time",
      any(u.startswith("[2026-11-21 03:00] [Jo, your person]")
          and "from my phone" in u for u in users))
check("wake-up: a group member is named, not read as the person",
      any("[Kai (@kai)]" in u and "group words" in u for u in users))

print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("ALL PASSED")
