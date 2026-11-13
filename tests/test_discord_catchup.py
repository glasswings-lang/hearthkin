# SPDX-License-Identifier: CC0-1.0
"""Discord catches up with Telegram (12 Nov 2026):

  1. A message the person sends while their reply is running reaches that
     reply (the tool loop's `interject`), with a "Got it"; if the reply takes
     no further step, it's answered straight after, never lost.
  2. A quick second message is no longer thrown away silently: it waits out
     the cooldown and is answered. Past a few waiting, the person is told.
  3. The kin reads back its own tool steps and Hearthkin's notes, in order,
     with "Tool history kept" applied, and no orphaned tool result.
  4. An empty reply leaves the kin a note and keeps its tool steps.

Drives the real DiscordBot methods, including the async message handler,
with the model and the network stubbed.

Run: python tests/test_discord_catchup.py
"""

import asyncio
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="dccatch-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import discord_bot  # noqa: E402
from discord_bot import DiscordBot  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def bare_bot(cfg=None):
    b = DiscordBot.__new__(DiscordBot)
    b.agent_name = "Bracken"
    b._turn_lock = threading.Lock()
    b._active_turns = {}
    b._user_last_reply = {}
    b._channel_locks = {}
    b._gen_semaphore = None
    b._warned_empty_allow = False
    b.on_activity = None
    cfg = cfg or {}
    b.get_config = lambda: cfg
    return b


# --- 1. holding a mid-reply message ------------------------------------
b = bare_bot()
b._begin_turn(99, "Wren", author_id=7)
check("the person whose reply is running can add to it",
      b._hold_for_turn(99, 7, "wait, use the other file"))
check("someone else in the channel can't",
      not b._hold_for_turn(99, 8, "me too"))
check("...and nothing is held outside a running reply",
      not b._hold_for_turn(55, 7, "hello"))
got = b._take_interjections(99)
check("the reply gets it, named like any other message",
      len(got) == 1 and got[0]["role"] == "user" and got[0]["interjected"]
      and "Wren" in got[0]["content"]
      and got[0]["content"].endswith("wait, use the other file"))
check("...and only once", b._take_interjections(99) == [])
b._hold_for_turn(99, 7, "one more thing")
b._end_turn(99)
check("a message the reply never got to is kept when it ends",
      b._take_leftovers(99) == ["one more thing"])
check("...and handed out only once", b._take_leftovers(99) == [])


# --- the async message handler -----------------------------------------
class _User:
    def __init__(self, uid, name="Wren", bot=False):
        self.id = uid
        self.name = name
        self.display_name = name
        self.bot = bot


class _Channel:
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
    def __init__(self, text, channel, uid=7):
        self.content = text
        self.author = _User(uid)
        self.channel = channel
        self.mentions = []
        self.attachments = []
        self.guild = None


class _Client:
    user = _User(1, "bot", bot=True)


# message_wait_secs 0: these scenarios are about other things, so pieces
# aren't held to be joined. The joining has its own scenario below.
CFG = {"discord": {"policy": "always", "allow_from": ["*"]},
       "telegram": {"message_wait_secs": 0}}


def drive(coro):
    return asyncio.run(coro)


async def scenario_cooldown():
    b = bare_bot(CFG)
    ch = _Channel()
    answered = []

    async def fake_gen(message, content, imgs, docs):
        answered.append((content, time.monotonic()))
    b._generate_in_turn = fake_gen
    b._loop = asyncio.get_running_loop()

    async def no_dl(m):
        return []
    b._download_images = no_dl
    b._download_text_documents = no_dl

    async def send(channel, text):
        channel.sent.append(text)
    b._send_chunked = send
    old = discord_bot.DISCORD_USER_COOLDOWN_SECS
    discord_bot.DISCORD_USER_COOLDOWN_SECS = 0.3
    try:
        t0 = time.monotonic()
        await asyncio.gather(
            b._on_message(_Client(), _Msg("first", ch)),
            b._on_message(_Client(), _Msg("second, quickly", ch)))
    finally:
        discord_bot.DISCORD_USER_COOLDOWN_SECS = old
    return answered, ch.sent, t0


answered, sent, t0 = drive(scenario_cooldown())
check("a quick second message is answered, not thrown away",
      [a[0] for a in answered] == ["first", "second, quickly"])
check("...after waiting out the pause", answered[1][1] - t0 >= 0.25)


async def scenario_flood():
    b = bare_bot(CFG)
    ch = _Channel()
    answered = []

    async def slow_gen(message, content, imgs, docs):
        await asyncio.sleep(0.05)
        answered.append(content)
    b._generate_in_turn = slow_gen
    b._loop = asyncio.get_running_loop()

    async def no_dl(m):
        return []
    b._download_images = no_dl
    b._download_text_documents = no_dl

    async def send(channel, text):
        channel.sent.append(text)
    b._send_chunked = send
    old = discord_bot.DISCORD_USER_COOLDOWN_SECS
    discord_bot.DISCORD_USER_COOLDOWN_SECS = 0.05
    try:
        await asyncio.gather(*[b._on_message(_Client(), _Msg(f"m{i}", ch))
                               for i in range(6)])
    finally:
        discord_bot.DISCORD_USER_COOLDOWN_SECS = old
    return answered, ch.sent


answered, sent = drive(scenario_flood())
cap = discord_bot.DISCORD_MAX_WAITING_PER_PERSON
check(f"a flood is still bounded: at most {cap} wait at once",
      len(answered) == cap)
check("...and the rest are TOLD they were skipped, not dropped silently",
      sum("skipped this one" in s for s in sent) == 6 - cap)


async def scenario_midreply():
    b = bare_bot(CFG)
    ch = _Channel()

    async def send(channel, text):
        channel.sent.append(text)
    b._send_chunked = send
    b._begin_turn(99, "Wren", author_id=7)
    await b._on_message(_Client(), _Msg("actually, stop at February", ch))
    return b, ch.sent


b2, sent = drive(scenario_midreply())
check("a message sent mid-reply is held for the reply, with a 'Got it'",
      any(s.startswith("Got it.") for s in sent)
      and b2._take_interjections(99)[0]["content"].endswith(
          "actually, stop at February"))


async def scenario_leftover():
    b = bare_bot(CFG)
    ch = _Channel()
    answered = []

    async def gen(message, content, imgs, docs):
        answered.append(content)
        if content == "first":
            # The person adds something while this reply runs, and the
            # reply ends without taking another step.
            b._begin_turn(99, "Wren", author_id=7)
            b._hold_for_turn(99, 7, "and one more thing")
            b._end_turn(99)
    b._generate_in_turn = gen
    b._loop = asyncio.get_running_loop()

    async def no_dl(m):
        return []
    b._download_images = no_dl
    b._download_text_documents = no_dl

    async def send(channel, text):
        channel.sent.append(text)
    b._send_chunked = send
    await b._on_message(_Client(), _Msg("first", ch))
    return answered


check("a held message the reply never reached is answered straight after",
      drive(scenario_leftover()) == ["first", "and one more thing"])


# --- split messages put back together (12 Nov 2026) --------------------
async def scenario_coalesce():
    cfg = {"discord": {"policy": "always", "allow_from": ["*"]},
           "telegram": {"message_wait_secs": 0.3}}
    b = bare_bot(cfg)
    ch = _Channel()
    answered = []

    async def gen(message, content, imgs, docs):
        answered.append(content)
    b._generate_in_turn = gen
    b._loop = asyncio.get_running_loop()

    async def no_dl(m):
        return []
    b._download_images = no_dl
    b._download_text_documents = no_dl

    async def send(channel, text):
        channel.sent.append(text)
    b._send_chunked = send

    async def later(delay, msg):
        await asyncio.sleep(delay)
        await b._on_message(_Client(), msg)
    old = discord_bot.DISCORD_USER_COOLDOWN_SECS
    discord_bot.DISCORD_USER_COOLDOWN_SECS = 0
    try:
        await asyncio.gather(
            b._on_message(_Client(), _Msg("I was thinking", ch)),
            later(0.1, _Msg("about the garden", ch)),
            later(0.2, _Msg("and the beans", ch)))
        two = list(answered)
        answered.clear()
        long_piece = "x" * discord_bot.DISCORD_SPLIT_LEN
        await asyncio.gather(
            b._on_message(_Client(), _Msg(long_piece, ch)),
            later(0.1, _Msg("yz", ch)))
        three = list(answered)
        answered.clear()
        other = _Msg("from someone else", ch, uid=9)
        await asyncio.gather(
            b._on_message(_Client(), _Msg("mine", ch)),
            later(0.05, other))
        four = sorted(answered)
    finally:
        discord_bot.DISCORD_USER_COOLDOWN_SECS = old
    return two, three, four


two, three, four = drive(scenario_coalesce())
check("quick pieces of one thought are answered as ONE message",
      two == ["I was thinking\nabout the garden\nand the beans"])
check("a piece cut at Discord's length limit joins without a line break",
      len(three) == 1 and three[0].endswith("xxxyz"))
check("someone else's message is never merged into yours",
      four == ["from someone else", "mine"])


# --- the busy notice (12 Nov 2026) -------------------------------------
async def scenario_busy():
    b = bare_bot(CFG)
    ch = _Channel()
    label = ["Bracken is writing a memory note"]
    b.get_busy_label = lambda skip_bot=None: label[0]

    async def gen(message, content, imgs, docs):
        pass
    b._generate_in_turn = gen
    b._loop = asyncio.get_running_loop()

    async def no_dl(m):
        return []
    b._download_images = no_dl
    b._download_text_documents = no_dl

    async def send(channel, text):
        channel.sent.append(text)
    b._send_chunked = send
    old = discord_bot.DISCORD_USER_COOLDOWN_SECS
    discord_bot.DISCORD_USER_COOLDOWN_SECS = 0
    try:
        await b._on_message(_Client(), _Msg("one", ch))
        await b._on_message(_Client(), _Msg("two", ch))
        n_busy = sum("in the queue" in s for s in ch.sent)
        label[0] = ""
        await b._on_message(_Client(), _Msg("three", ch))
        label[0] = "Bracken is running a scheduled wake-up"
        await b._on_message(_Client(), _Msg("four", ch))
    finally:
        discord_bot.DISCORD_USER_COOLDOWN_SECS = old
    return n_busy, ch.sent


n_busy, sent = drive(scenario_busy())
check("Discord says when a message waits behind background work",
      any(s.startswith("Bracken is writing a memory note.") for s in sent))
check("...once per busy stretch, not per message", n_busy == 1)
check("...and again in the next busy stretch",
      any(s.startswith("Bracken is running a scheduled wake-up.") for s in sent))


# --- 3. reading back its own tool steps --------------------------------
from kin_persistence import append_discord_turn  # noqa: E402
KIN = "Bracken"
rows = [
    {"role": "tool", "tool_call_id": "old", "content": "orphan result"},
    {"role": "user", "content": "[Wren] read my notes"},
    {"role": "assistant", "content": "",
     "tool_calls": [{"id": "c1", "function": {"name": "read_file",
                                              "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "c1", "content": "the notes"},
    {"role": "assistant", "content": "Here they are."},
    {"role": "system", "content": "[hearthkin: a note for you]"},
]
for r in rows:
    append_discord_turn(KIN, 42, r)
b3 = bare_bot({"tool_history_keep": 5})
hist = b3._channel_history(42, share=False)
roles = [h["role"] for h in hist]
check("the kin reads back its tool call and its result",
      "tool" in roles and any(h.get("tool_calls") for h in hist))
check("...and Hearthkin's notes", any(h["role"] == "system"
                                      and "a note for you" in h["content"]
                                      for h in hist))
check("...in the order they happened",
      roles == ["user", "assistant", "tool", "assistant", "system"])
check("a tool result whose call isn't there is left out", hist[0]["role"] != "tool")
b4 = bare_bot({"tool_history_keep": 0})
hist0 = b4._channel_history(42, share=False)
check("'Tool history kept' 0 turns old tool steps into a short marker",
      not any(h["role"] == "tool" for h in hist0)
      and any("earlier tool call" in (h.get("content") or "") for h in hist0))

# --- 4 & ordering: what gets saved, through a real reply ----------------
src = (ROOT / "discord_bot.py").read_text(encoding="utf-8")
gen = src[src.find("    def _generate(self"):src.find("    def _persist_tool_turn")]
check("tool steps are saved BEFORE the final reply",
      0 < gen.find("for _turn in added_turns:")
      < gen.find('self._persist_turn(channel_id, "assistant", text, share)'))
check("an empty reply leaves the kin a note",
      'load_app_prompt("empty_reply_note_group"' in gen)
check("the tool loop is handed the mid-reply messages",
      "interject=lambda: self._take_interjections(channel_id)" in src)

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_discord_catchup: all checks passed")
