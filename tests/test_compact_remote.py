# SPDX-License-Identifier: CC0-1.0
"""/compact on Telegram and in Discord direct messages (12 Nov 2026).

The desktop's Chat -> Compact this conversation, as a command: /compact has
the kin write itself a note (no tools), shows it, and "/compact confirm"
puts a marker with it into that person's conversation. From then on the kin
reads the note and the last few messages; nothing is deleted. /compact again
writes a new note; anything else leaves things as they were.

Drives the real command handlers with real saved conversations, the model
stubbed.

Run: python tests/test_compact_remote.py
"""

import asyncio
import os
import sys
import tempfile
import threading

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="compactremote-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kin_persistence as kp  # noqa: E402
import llm_backend  # noqa: E402
import telegram_bot  # noqa: E402
from discord_bot import DiscordBot  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Tensor"
kp.save_agent_config(KIN, dict(kp.DEFAULT_AGENT_CONFIG, model="stub"))
asked = []


class _R:
    def __init__(self, c):
        self.content = c
        self.thinking = ""


def fake_collect(model, messages, **kw):
    asked.append(messages)
    return _R(f"<thinking>x</thinking>Note {len(asked)}: we fixed Discord.")


llm_backend.chat_collect = fake_collect

# --- Telegram, a conversation kept on Telegram --------------------------
tb = telegram_bot.TelegramBot.__new__(telegram_bot.TelegramBot)
tb.agent_name = KIN
tb._histories_lock = threading.RLock()
tb._histories = {}
tb.get_config = lambda: {"allow_from": ["1"]}
tb.get_soul = lambda: "You are Tensor."
tb.get_memory = lambda: ""
tb.get_model_options = lambda: ("stub", {"num_ctx": 8192})
tb._user_shares_desktop = lambda uid: False
tb.on_activity = None
tg_sent = []
tb._send_chunked = lambda chat_id, text: tg_sent.append(text)
rows = []
for i in range(10):
    rows.append({"role": "user" if i % 2 == 0 else "assistant",
                 "content": f"m{i}"})
tb._histories[telegram_bot._hkey(1)] = [dict(r) for r in rows]

tb._handle_command("/compact", 1, 1)
check("Telegram: /compact asks the kin, with no tools, after the "
      "conversation", asked and asked[-1][-1]["role"] == "user"
      and "write that note now" in asked[-1][-1]["content"]
      and asked[-1][-2]["content"] == "m9")
check("...and shows you the note, without the kin's thinking",
      "Note 1: we fixed Discord." in tg_sent[-1] and "<thinking>" not in
      tg_sent[-1] and "/compact confirm" in tg_sent[-1])
check("...changing nothing yet",
      len(tb._histories[telegram_bot._hkey(1)]) == 10)
tb._handle_command("/compact again", 1, 1)
check("/compact again writes a new note", "Note 2" in tg_sent[-1])
tb._handle_command("/compact confirm", 1, 1)
hist = tb._histories[telegram_bot._hkey(1)]
check("/compact confirm adds the marker and deletes nothing",
      len(hist) == 11 and hist[-1].get("compact")
      and hist[-1]["content"].endswith("Note 2: we fixed Discord.")
      and "Nothing was deleted" in tg_sent[-1])
seen = tb._load_history_for(1)
check("...and from then on the kin reads its note and the last six",
      seen[0]["role"] == "system" and "Note 2" in seen[0]["content"]
      and [m["content"] for m in seen[1:]] == [f"m{i}" for i in range(4, 10)])
tb._handle_command("/compact confirm", 1, 1)
check("confirming with no note waiting says so",
      "no note waiting" in tg_sent[-1] and len(tb._histories[
          telegram_bot._hkey(1)]) == 11)
tb._cmd_help(1)
check("/compact is in /help", "/compact" in tg_sent[-1])


# --- Discord, a direct message ------------------------------------------
class _User:
    id = 7
    name = "wren"
    display_name = "Wren"
    bot = False


class _Chan:
    id = 99

    def __init__(self):
        self.sent = []

    def typing(self):
        class _T:
            async def __aenter__(s):
                return s

            async def __aexit__(s, *a):
                return False
        return _T()


class _Msg:
    def __init__(self, text, ch):
        self.content = text
        self.author = _User()
        self.channel = ch
        self.mentions = []
        self.attachments = []
        self.guild = None


class _Client:
    user = type("U", (), {"id": 1, "bot": True})()


cfg = dict(kp.DEFAULT_AGENT_CONFIG, model="stub",
           discord={"policy": "always", "allow_from": ["*"]},
           telegram={"message_wait_secs": 0})
db = DiscordBot.__new__(DiscordBot)
db.agent_name = KIN
db._turn_lock = threading.Lock()
db._active_turns = {}
db._user_last_reply = {}
db._channel_locks = {}
db._gen_semaphore = None
db._warned_empty_allow = False
db.on_activity = None
db.get_busy_label = None
db.get_config = lambda: cfg
db.get_soul = lambda: "You are Tensor."
db.get_memory = lambda: ""
db.get_model_options = lambda: ("stub", {"num_ctx": 8192})


async def send(channel, text):
    channel.sent.append(text)
db._send_chunked = send
kp.save_discord_history(KIN, {"99": [dict(r) for r in rows]})
ch = _Chan()


def say(text):
    async def go():
        db._loop = asyncio.get_running_loop()
        await db._on_message(_Client(), _Msg(text, ch))
    asyncio.run(go())
    return ch.sent[-1]


asked.clear()
out = say("/compact")
check("Discord: /compact shows the kin's note first",
      "Note 1: we fixed Discord." in out and "/compact confirm" in out)
out = say("/compact confirm")
saved = kp.load_discord_history(KIN)["99"]
check("...confirm adds the marker and deletes nothing",
      len(saved) == 11 and saved[-1].get("compact")
      and "Nothing was deleted" in out)
read = db._channel_history(99, False)
check("...and the kin reads its note and the last six",
      read[0]["role"] == "system" and "Note 1" in read[0]["content"]
      and [m["content"] for m in read[1:]] == [f"m{i}" for i in range(4, 10)])

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_compact_remote: all checks passed")
