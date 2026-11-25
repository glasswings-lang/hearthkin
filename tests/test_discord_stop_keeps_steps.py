# SPDX-License-Identifier: CC0-1.0
"""Stopping a Discord reply keeps the steps the kin had finished (24 Nov 2026).

The stop message says "Stopped. Keeping what was written so far." But a stop
between tool steps, before the final reply, returned without storing the
steps the kin had already finished, so they were lost while the message said
they were kept. Telegram always stored them. Now Discord does too, and says
so. Drives DiscordBot._generate with the model call stubbed.

Run: python tests/test_discord_stop_keeps_steps.py
"""

import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="dcstop-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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
    def __init__(self):
        self.author = _Author()
        self.channel = _Channel()
        self.attachments = []
        self.guild = None


class _Bot(DiscordBot):
    def __init__(self):
        self.agent_name = KIN
        self.posted = []
        self.saved = []
        self.tool_rows = []
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

    def _persist_turn(self, channel_id, role, content, share, said=None,
                      **_kw):
        self.saved.append((role, content))

    def _persist_tool_turn(self, channel_id, turn, share):
        self.tool_rows.append(turn)


STEPS = [
    {"role": "assistant", "content": "",
     "tool_calls": [{"id": "1", "function": {"name": "read_file",
                                             "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "1", "content": "the file"},
]


def run(added):
    cfg = dict(DEFAULT_AGENT_CONFIG)
    cfg.update({"model": "stub", "discord": {"allow_from": ["*"]}})
    save_agent_config(KIN, cfg)
    save_kin_tools(KIN, [])
    b = _Bot()
    b.get_config = lambda: cfg
    b.get_soul = lambda: "Your name is Bracken."
    b.get_memory = lambda: ""
    b.get_model_options = lambda: ("stub", {"num_ctx": 8192})

    def fake_run_model(*a, **kw):
        b._request_turn_stop(99)          # the person said "stop" mid-turn
        return "", "", list(added)
    b._run_model = fake_run_model

    class _Fut:
        def __init__(self, v):
            self.v = v

        def result(self, timeout=None):
            return self.v

    import asyncio
    saved = asyncio.run_coroutine_threadsafe
    asyncio.run_coroutine_threadsafe = lambda coro, loop: _Fut(coro)
    try:
        b._generate(_Message(), "read that for me", None, None)
    finally:
        asyncio.run_coroutine_threadsafe = saved
    return b


b = run(STEPS)
check("a stop after a finished tool step keeps that step",
      len(b.tool_rows) == 2)
check("...and says the finished steps are kept",
      any("finished are kept" in p for p in b.posted))

b = run([])
check("a stop before anything happened keeps nothing and says so",
      b.tool_rows == []
      and any("stopped before anything was written" in p for p in b.posted))

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_discord_stop_keeps_steps: all checks passed")
