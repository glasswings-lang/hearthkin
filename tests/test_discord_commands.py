# SPDX-License-Identifier: CC0-1.0
"""Discord commands (12 Nov 2026): /help, /status, /regen, /undo, /clear.

Telegram has had these all along; Discord had only "stop". Same rules as
Telegram: /help works anywhere, the rest only in a direct message, because
in a server channel the conversation belongs to everyone there.

Drives the real async message handler with real saved conversations (both
kinds: kept on Discord only, and shared with the desktop), the model and the
network stubbed. Checks that undo and regenerate cut exactly this channel's
last exchange and nothing else: not another channel, not the desktop's own
messages, not Telegram's.

Run: python tests/test_discord_commands.py
"""

import asyncio
import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="dccmd-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import discord_bot  # noqa: E402
from discord_bot import DiscordBot  # noqa: E402
import kin_persistence as kp  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Bracken"


class _User:
    def __init__(self, uid, name="Wren", bot=False):
        self.id = uid
        self.name = name
        self.display_name = name
        self.bot = bot


class _Channel:
    def __init__(self, cid=99):
        self.id = cid
        self.sent = []

    def typing(self):
        class _T:
            async def __aenter__(s):
                return s

            async def __aexit__(s, *a):
                return False
        return _T()


class _Msg:
    def __init__(self, text, channel, uid=7, guild=None):
        self.content = text
        self.author = _User(uid)
        self.channel = channel
        self.mentions = []
        self.attachments = []
        self.guild = guild


class _Client:
    user = _User(1, "bot", bot=True)


def make_bot(share):
    cfg = {"model": "stub-model",
           "discord": {"policy": "always", "allow_from": ["*"],
                       "share_desktop": share},
           "telegram": {"message_wait_secs": 0}}
    b = DiscordBot.__new__(DiscordBot)
    b.agent_name = KIN
    b._turn_lock = threading.Lock()
    b._active_turns = {}
    b._user_last_reply = {}
    b._channel_locks = {}
    b._gen_semaphore = None
    b._warned_empty_allow = False
    b.on_activity = None
    b.get_busy_label = None
    b.get_config = lambda: cfg
    b.generated = []

    async def fake_gen(message, content, imgs, docs):
        b.generated.append(content)
    b._generate_in_turn = fake_gen

    async def send(channel, text):
        channel.sent.append(text)
    b._send_chunked = send

    async def no_dl(m):
        return []
    b._download_images = no_dl
    b._download_text_documents = no_dl
    return b


def say(b, text, ch, **kw):
    async def go():
        b._loop = asyncio.get_running_loop()
        await b._on_message(_Client(), _Msg(text, ch, **kw))
    asyncio.run(go())
    return ch.sent[-1] if ch.sent else ""


def row(role, content, **kw):
    return {"role": role, "content": content, **kw}


# --- which messages count as commands ----------------------------------
P = discord_bot._parse_command
check("/undo is a command", P("/undo") == ("undo", ""))
check("!regen works too, for when Discord's own menu gets in the way",
      P("!regen") == ("regen", ""))
check("/clear confirm keeps its word", P("/clear confirm") == ("clear", "confirm"))
check("an unknown /word still goes to the kin as a message",
      P("/shrug hi") is None and P("/me waves") is None)
check("plain words are never commands", P("undo") is None and P("help me") is None)


# --- kept on Discord only ------------------------------------------------
kp.save_discord_history(KIN, {
    "99": [row("user", "[Wren] first", said="first"),
           row("assistant", "one"),
           row("user", "[Wren] second", said="second"),
           row("assistant", "", tool_calls=[{"id": "a", "function":
                                             {"name": "x", "arguments": "{}"}}]),
           row("tool", "ok", tool_call_id="a"),
           row("assistant", "two")],
    "55": [row("user", "[Ash] other channel"), row("assistant", "theirs")]})
b = make_bot(share=False)
ch = _Channel(99)

said = say(b, "/undo", ch)
h = kp.load_discord_history(KIN)
check("undo takes back the last message and everything after it",
      [m["content"] for m in h["99"]] == ["[Wren] first", "one"])
check("...says so", said.startswith("Undone."))
check("...and another channel is untouched",
      [m["content"] for m in h["55"]] == ["[Ash] other channel", "theirs"])
check("...and nothing went to the kin", b.generated == [])

say(b, "/regen", ch)
h = kp.load_discord_history(KIN)
check("regen sends your words again, without the name in front",
      b.generated == ["first"])
check("...after taking away the old reply and the message (it's re-saved "
      "when written again)", h["99"] == [])

# An older saved message, from before the plain words were kept alongside.
kp.save_discord_history(KIN, {"99": [row("user", "[Wren] hello there"),
                                     row("assistant", "hi")]})
b.generated.clear()
say(b, "/regen", ch)
check("an older message still regenerates with just its words",
      b.generated == ["hello there"])

say(b, "/undo", ch)
check("undo with nothing left says so", "Nothing to undo" in ch.sent[-1])

kp.save_discord_history(KIN, {"99": [row("user", "x"), row("assistant", "y")],
                              "55": [row("user", "z")]})
said = say(b, "/clear", ch)
h = kp.load_discord_history(KIN)
check("clear wipes this conversation at once when it's Discord-only",
      "99" not in h and said.startswith("Cleared."))
check("...and only this one", h.get("55") == [row("user", "z")])


# --- shared with the desktop ---------------------------------------------
D = "discord:99"
kp.save_agent_conversation(KIN, [
    row("user", "desktop hello"),
    row("user", "[Wren] early", said="early", source=D),
    row("assistant", "early reply", source=D),
    row("user", "[Wren] late", said="late", source=D),
    row("assistant", "a desktop line said meanwhile"),
    row("user", "from telegram", source="telegram:group:5"),
    row("assistant", "late reply", source=D),
    row("user", "[Ash] elsewhere", source="discord:55"),
])
b = make_bot(share=True)
ch = _Channel(99)
said = say(b, "/undo", ch)
conv = kp.load_agent_conversation(KIN)
check("shared: undo takes only this channel's last exchange",
      [m["content"] for m in conv] == [
          "desktop hello", "[Wren] early", "early reply",
          "a desktop line said meanwhile", "from telegram",
          "[Ash] elsewhere"])
check("...and says the desktop loses it too", "desktop" in said)

said = say(b, "/clear", ch)
check("shared: clear asks first, because the desktop loses them too",
      "/clear confirm" in said
      and len(kp.load_agent_conversation(KIN)) == 6)
said = say(b, "/clear confirm", ch)
conv = kp.load_agent_conversation(KIN)
check("...and on confirm removes only this channel's messages",
      [m["content"] for m in conv] == [
          "desktop hello", "a desktop line said meanwhile",
          "from telegram", "[Ash] elsewhere"])


# --- in a server channel ------------------------------------------------
kp.save_discord_history(KIN, {"77": [row("user", "a"), row("assistant", "b")]})
b = make_bot(share=False)
ch = _Channel(77)
said = say(b, "/undo", ch, guild=object())
check("in a server channel, undo is refused: the talk belongs to everyone",
      "direct message" in said
      and len(kp.load_discord_history(KIN)["77"]) == 2)
said = say(b, "/help", ch, guild=object())
check("...but help works anywhere", "/help" in said and "stop" in said)
said = say(b, "/help", _Channel(99))
check("help in a direct message lists them all",
      all(w in said for w in ("/status", "/regen", "/undo", "/clear")))


# --- while a reply is being written --------------------------------------
kp.save_discord_history(KIN, {"99": [row("user", "a"), row("assistant", "b")]})
b = make_bot(share=False)
ch = _Channel(99)
b._begin_turn(99, "Wren", author_id=7)
said = say(b, "/undo", ch)
check("a command during a reply waits, it doesn't cut under it",
      "still writing" in said
      and len(kp.load_discord_history(KIN)["99"]) == 2)
check("...and is never handed to the reply as more words",
      b._take_interjections(99) == [])
b._end_turn(99)


# --- status, and the ordinary message that looks like a command ---------
b = make_bot(share=True)
said = say(b, "/status", _Channel(99))
check("status names the model and says the talk is shared",
      "stub-model" in said and "shared with the Hearthkin desktop" in said)
say(b, "/shrug hi", _Channel(99))
check("an unknown /word reaches the kin as words", b.generated == ["/shrug hi"])


print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_discord_commands: all checks passed")
