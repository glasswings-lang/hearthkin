# SPDX-License-Identifier: CC0-1.0
"""Where Telegram already settled something, Discord and the desktop copy it
(20 Nov 2026).

  * Someone not on the list who messages the kin directly on Discord is told
    how to be added (at most once every ten minutes), as on Telegram; never
    in a channel.
  * Only the person a reply is for, or the kin's own person, can stop it.
    In a shared channel anyone used to be able to stop anyone's reply.
  * Discord's tool lines have Telegram's on/off setting, off by default; a
    failed call is always shown. Discord used to post every call, even in
    public channels.
  * The desktop has Undo (Chat menu, Ctrl+Shift+U), as /undo on the apps.

Run: python tests/test_discord_desktop_parity.py
"""

import asyncio
import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="parity-", dir=(os.environ.get("HEARTHKIN_HOME") or None))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from discord_bot import DiscordBot  # noqa: E402
from kin_persistence import DEFAULT_DISCORD_CONFIG  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


class _User:
    def __init__(self, uid, bot=False):
        self.id = uid
        self.bot = bot
        self.name = self.display_name = f"u{uid}"
        self.mention = f"<@{uid}>"


class _Chan:
    def __init__(self, cid):
        self.id = cid
        self.sent = []


class _Client:
    user = _User(1, bot=True)


class _Msg:
    def __init__(self, cid, author, guild=None, text="hi", mention=False):
        self.content = text
        self.author = _User(author)
        self.channel = _Chan(cid)
        self.mentions = [_Client.user] if mention else []
        self.attachments = []
        self.guild = guild


def bot(dcfg):
    d = DiscordBot.__new__(DiscordBot)
    d.agent_name = "Bracken"
    d.transcribe_voice = None
    d._turn_lock = threading.Lock()
    d._active_turns = {}
    d._user_last_reply = {}
    d._channel_locks = {}
    d._gen_semaphore = None
    d._warned_empty_allow = False
    d._dm_channels = set()
    d.on_activity = None
    d.on_status = None
    d.get_busy_label = None
    d.get_config = lambda: {"discord": dcfg, "telegram": {"message_wait_secs": 0}}

    async def send(ch, t):
        ch.sent.append(t)
    d._send_chunked = send

    async def gen(*a, **k):
        pass
    d._generate_in_turn = gen
    return d


def deliver(d, msg):
    async def go():
        d._loop = asyncio.get_running_loop()
        await d._on_message(_Client(), msg)
    asyncio.run(go())
    return msg.channel.sent


print("--- not on the list ---")
d = bot({"allow_from": ["7"], "user_is_operator": {"7": True}})
said = deliver(d, _Msg(800, 9))
check("a stranger's direct message is told how to be added, with their ID",
      len(said) == 1 and "not on Bracken's list" in said[0] and "9" in said[0])
check("...only once in ten minutes",
      deliver(d, _Msg(800, 9)) == [])


class _Guild:
    id = 1


check("...and never in a server channel",
      deliver(bot({"allow_from": ["7"]}), _Msg(801, 9, _Guild(),
                                                mention=True)) == [])

print("--- who can stop a reply ---")
d = bot({"allow_from": ["*"], "user_is_operator": {"7": True}})
d._active_turns[555] = {"who": "u8", "stop": False, "author_id": "8"}
said = deliver(d, _Msg(555, 9, text="stop"))
check("someone else can't stop a reply that isn't theirs",
      d._active_turns[555]["stop"] is False and "someone else" in said[0])
deliver(d, _Msg(555, 8, text="stop"))
check("the person it's for can", d._active_turns[555]["stop"] is True)
d._active_turns[555]["stop"] = False
deliver(d, _Msg(555, 7, text="stop"))
check("...and so can the kin's own person",
      d._active_turns[555]["stop"] is True)
check("the desktop's stop still reaches any reply",
      d._request_turn_stop(555) is True)

print("--- tool lines ---")
check("Discord's tool lines start off, like Telegram's",
      DEFAULT_DISCORD_CONFIG.get("show_tool_calls") is False)
src = (ROOT / "discord_bot.py").read_text(encoding="utf-8")
check("...the setting is read, and a failure is always shown",
      '"show_tool_calls", False)' in src and 'f"⚠️ {str(result)[:400]}"' in src)

print("--- desktop Undo ---")
from frame.chat_stream_mixin import ChatStreamMixin  # noqa: E402


class _Win(ChatStreamMixin):
    def __init__(self, convo):
        self.current_room = None
        self.current_agent = "Bracken"
        self._streaming = False
        self.conversation = [dict(m) for m in convo]
        self.status = []

    def _set_status(self, t):
        self.status.append(t)

    def _render_conversation(self):
        pass

    def _persist_current_conversation(self):
        pass


w = _Win([{"role": "user", "content": "one"},
          {"role": "assistant", "content": "reply one"},
          {"role": "user", "content": "two"},
          {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]},
          {"role": "tool", "content": "x", "tool_call_id": "a"},
          {"role": "assistant", "content": "reply two"},
          {"role": "system", "content": "[hearthkin: saved a file]"}])
w._on_undo_exchange(None)
check("Undo takes back the last message, its reply, tool steps and notes",
      [m["content"] for m in w.conversation] == ["one", "reply one"])
w = _Win([{"role": "system", "content": "note", "compact": True}])
w._on_undo_exchange(None)
check("...and never a compaction note",
      len(w.conversation) == 1 and "Nothing to undo" in w.status[-1])
msrc = (ROOT / "frame" / "menus_mixin.py").read_text(encoding="utf-8")
check("it's on the Chat menu, on Control Shift U",
      "Undo last exchange" in msrc and "_on_undo_exchange" in msrc)

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_discord_desktop_parity: all checks passed")
