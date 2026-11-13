# SPDX-License-Identifier: CC0-1.0
"""A kin gets a whole park turn in a Discord direct message (12 Nov 2026).

Discord used to run one `> ` move per reply anywhere, because its notes said
it had no direct messages at all. It does. Now, in a direct message, the kin
moves, sees what happened, and moves again until it stops or its allowance
runs out, through the same park_keeper.play_turn loop the desktop, Telegram
private chats and wake-ups use. A server channel keeps one move per reply,
on purpose, as Telegram groups do.

Drives a whole Discord reply (DiscordBot._generate) with the model and the
park stubbed: the kin's later words come from the stub, the park's answers
from a stand-in game host.

Run: python tests/test_discord_park_turn.py
"""

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="dcpark-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import llm_backend  # noqa: E402
import tools as kin_tools  # noqa: E402
from discord_bot import DiscordBot  # noqa: E402
from kin_persistence import (DEFAULT_AGENT_CONFIG, save_agent_config,  # noqa: E402
                             save_kin_tools)

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Bracken"


class _Author:
    id = 7
    name = "wren"
    display_name = "Wren"
    bot = False


class _Channel:
    id = 99

    def send(self, text):
        return self


class _Message:
    def __init__(self, guild=None):
        self.author = _Author()
        self.channel = _Channel()
        self.attachments = []
        self.guild = guild


class _Result:
    def __init__(self, content):
        self.content = content
        self.thinking = ""
        self.stopped = False
        self.messages_added = []


class _Host:
    """The park, as far as Discord can see it."""

    def __init__(self):
        self.ran = []

    def reachable(self, kin):
        return True, ""

    def run(self, kin, cmd, say=""):
        self.ran.append(cmd)
        return f"You {cmd}. Luna purrs."

    def decorate(self, kin, cmd, res):
        return res

    def awaiting_answer(self, kin):
        return False

    def log_unreachable(self, *a):
        pass


class _Bot(DiscordBot):
    def __init__(self):
        self.agent_name = KIN
        self.posted = []
        self.saved = []
        self._surface_label = "discord"
        self._wrap_exec = None
        self._request_webcam_approval = None
        self._last_recall_used = []
        self._active_turns = {}
        self._turn_lock = threading.Lock()
        self._loop = object()

    def _post_sync(self, channel, text):
        self.posted.append(text)

    def _post_from_thread(self, channel, text):
        self.posted.append(text)

    def _persist_turn(self, channel_id, role, content, share, said=None):
        self.saved.append((role, content))


def run(guild, later_words, stop_after_first=False):
    """One Discord reply opening with `> look`. `later_words` is what the kin
    says each time it's asked again. Returns (bot, host, what the model was
    shown on each re-ask)."""
    cfg = dict(DEFAULT_AGENT_CONFIG)
    cfg.update({"model": "stub", "park": "chat", "park_moves_max": 5,
                "discord": {"allow_from": ["*"]}})
    save_agent_config(KIN, cfg)
    save_kin_tools(KIN, [])
    b = _Bot()
    b.get_config = lambda: cfg
    b.get_soul = lambda: "Your name is Bracken."
    b.get_memory = lambda: ""
    b.get_model_options = lambda: ("stub", {"num_ctx": 8192})
    host = _Host()
    if stop_after_first:
        # The person says "stop" while the first move is running.
        _run = host.run

        def run_then_stop(kin, cmd, say=""):
            out = _run(kin, cmd, say)
            b._request_turn_stop(99)
            return out
        host.run = run_then_stop
    later = list(later_words)
    shown = []

    def fake_collect(model, messages, **kw):
        if not shown and not any(m.get("role") == "assistant"
                                 and "> look" in (m.get("content") or "")
                                 for m in messages):
            shown.append(None)        # the opening reply, not a re-ask
            return _Result("Let me see.\n> look")
        shown.append([dict(m) for m in messages])
        return _Result(later.pop(0) if later else "")

    class _Fut:
        def __init__(self, v):
            self.v = v

        def result(self, timeout=None):
            return self.v

    import asyncio
    saved = (asyncio.run_coroutine_threadsafe, llm_backend.chat_collect,
             kin_tools.get_game)
    asyncio.run_coroutine_threadsafe = lambda coro, loop: _Fut(coro)
    llm_backend.chat_collect = fake_collect
    kin_tools.get_game = lambda name: host
    try:
        b._generate(_Message(guild), "go see the park", None, None)
    finally:
        (asyncio.run_coroutine_threadsafe, llm_backend.chat_collect,
         kin_tools.get_game) = saved
    return b, host, [s for s in shown if s is not None]


# --- a direct message: the whole turn -----------------------------------
b, host, asks = run(None, ["Luna looks hungry.\n> feed luna",
                           "All done for now."])
check("in a direct message, the kin moves again after seeing the result",
      host.ran == ["look", "feed luna"])
check("...the result of each move was in front of it when it was asked",
      asks and any("Luna purrs" in (m.get("content") or "")
                   for m in asks[0] if m.get("role") == "system"))
check("...its later words are posted and saved, in order",
      "Luna looks hungry.\n> feed luna" in b.posted
      and ("assistant", "All done for now.") in b.saved)
check("...and the turn ends when it stops moving", len(asks) == 2)
check("...and nothing is left registered as running afterwards",
      b._active_turns == {})

# --- the allowance ------------------------------------------------------
b, host, asks = run(None, ["> look"] * 10)
check("the kin's own move allowance still ends a long turn",
      len(host.ran) == 5)

# --- stop -----------------------------------------------------------------
b, host, asks = run(None, ["> feed luna", "> pet luna"],
                    stop_after_first=True)
check("'stop' reaches the turn between moves", host.ran == ["look"])
check("...before the kin is even asked again", asks == [])

# --- a server channel: one move -----------------------------------------
b, host, asks = run(object(), ["> feed luna"])
check("in a server channel, one move per reply, as before",
      host.ran == ["look"] and asks == [])


print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_discord_park_turn: all checks passed")
