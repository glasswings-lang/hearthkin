# SPDX-License-Identifier: CC0-1.0
"""Discord server channels work like Telegram groups (20 Nov 2026).

Before: one line of channel IDs, one answering rule for every channel, one
people list deciding who could talk everywhere, no mute list, and listing a
server silently shut off direct messages. Telegram groups worked the other
way. Now, through the real Discord message handler:

  * In a listed channel, anyone can talk, except people muted there.
  * Each channel answers on its own terms (mentions only, or everything).
  * A channel not on the list is ignored.
  * Direct messages still need the people list.
  * A server list never blocks direct messages.
  * A channel list kept the older way stays people-list-only, so nobody's
    access widens without them choosing it.

Run: python tests/test_discord_channels_like_groups.py
"""

import asyncio
import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="dcchan-", dir=(os.environ.get("HEARTHKIN_HOME") or None))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from discord_bot import DiscordBot, channel_entries  # noqa: E402

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


class _Guild:
    def __init__(self, gid):
        self.id = gid


class _Client:
    user = _User(1, bot=True)


class _Msg:
    def __init__(self, cid, author, guild=None, mention=False, text="hi"):
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
    d.answered = []

    async def gen(message, content, imgs, docs):
        d.answered.append((message.channel.id, message.author.id))
    d._generate_in_turn = gen

    async def none(m):
        return []
    d._download_images = none
    d._download_text_documents = none

    async def send(ch, t):
        ch.sent.append(t)
    d._send_chunked = send
    return d


def answered(dcfg, msg):
    d = bot(dcfg)

    async def go():
        d._loop = asyncio.get_running_loop()
        await d._on_message(_Client(), msg)
    asyncio.run(go())
    return bool(d.answered)


G = _Guild(900)
NEW = {"allow_from": ["7"], "policy": "mention_only", "guilds": ["123"],
       "channel_entries": {
           "555": {"label": "general", "policy": "mention_only",
                   "exclude": ["66"]},
           "556": {"label": "quiet", "policy": "always", "exclude": []}}}

check("a listed channel: someone not on the people list can talk",
      answered(NEW, _Msg(555, 8, G, mention=True)))
check("...unless they're muted there",
      not answered(NEW, _Msg(555, 66, G, mention=True)))
check("a mentions-only channel ignores what isn't a mention",
      not answered(NEW, _Msg(555, 8, G)))
check("an answer-everything channel answers without a mention",
      answered(NEW, _Msg(556, 8, G)))
check("a channel not on the list is ignored",
      not answered(NEW, _Msg(557, 7, G, mention=True)))
check("a direct message from someone on the people list is answered",
      answered(NEW, _Msg(700, 7, None)))
check("...even with a server list set (it used to block every DM)",
      answered(dict(NEW, channel_entries={}), _Msg(701, 7, None)))
check("a direct message from someone not on the people list isn't",
      not answered(NEW, _Msg(702, 8, None)))

# In a mentions-only channel, some things reach the kin without a mention,
# as on Telegram (audit, 20 Nov 2026).
def passes(dcfg, msg, running_for=None, waiting=False):
    d = bot(dcfg)
    if running_for is not None:
        d._active_turns[msg.channel.id] = {"author_id": str(running_for),
                                           "stop": False, "who": "x"}
    if waiting:
        d._coalescing = {(msg.channel.id, msg.author.id): {"parts": ["a"]}}
    return d._needs_no_mention(msg)


check("mentions-only: a command gets through without a mention",
      passes(NEW, _Msg(555, 8, G, text="/help")))
check("...'stop' while a reply is running here",
      passes(NEW, _Msg(555, 8, G, text="stop"), running_for=8))
check("...but not 'stop' said in passing with nothing running",
      not passes(NEW, _Msg(555, 8, G, text="stop")))
check("...but not an unmentioned follow-up from the person whose reply is "
      "running: as in Telegram groups, they may be talking to someone else",
      not passes(NEW, _Msg(555, 8, G, text="wait, also this"), running_for=8))
check("...not someone else's chatter during it",
      not passes(NEW, _Msg(555, 9, G, text="lol"), running_for=8))
check("...and the next piece of a split message",
      passes(NEW, _Msg(555, 8, G, text="rest of it"), waiting=True))
check("ordinary chatter still needs a mention",
      not passes(NEW, _Msg(555, 8, G, text="hello all")))

OLD = {"allow_from": ["7"], "policy": "always", "channels": ["555"]}
check("a channel listed the older way still lets the people list talk",
      answered(OLD, _Msg(555, 7, G)))
check("...and nobody else (access never widens unasked)",
      not answered(OLD, _Msg(555, 8, G)))
check("the older line of IDs reads as channels, marked people-list-only",
      channel_entries(OLD)["555"].get("people_only") is True)

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_discord_channels_like_groups: all checks passed")
