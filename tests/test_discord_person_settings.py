# SPDX-License-Identifier: CC0-1.0
"""Discord does what each per-person setting says, the way Telegram does.

Since 12 Nov 2026 both surfaces share one person window and one list of
settings. This drives a real Discord reply (DiscordBot._generate) with the
model and the tool loader stubbed, once per setting, and checks what reached
the model and the tool loop:

  - the name you gave someone is the name the kin sees;
  - sharing with the desktop marks them as you: no tool-call limit;
  - their webcam setting: refuse, take without asking, or ask you;
  - their files setting keeps file tools inside the kin folder or not;
  - a "*" entry covers anyone without their own (it used to be ignored for
    tool access).

And the Discord mirror sends to the right people, through a stubbed web call.
No network, no wx.

Run: python tests/test_discord_person_settings.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="dcperson-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import llm_backend  # noqa: E402
import tools as kin_tools  # noqa: E402
from discord_bot import DiscordBot  # noqa: E402
from kin_persistence import (DEFAULT_AGENT_CONFIG, save_agent_config,  # noqa: E402
                             save_kin_tools)
from tools._person_settings import save_person, load_person  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Bracken"
PERSON = 7


class _Author:
    id = PERSON
    name = "wren_discord"
    display_name = "Wren on Discord"
    bot = False


class _Handle:
    def __init__(self, sink, text):
        sink.append(text)
        self.sink = sink

    def edit(self, content=""):
        self.sink.append(content)
        return self


class _Channel:
    id = 99

    def __init__(self, sink):
        self.sink = sink

    def send(self, text):
        return _Handle(self.sink, text)


class _Message:
    def __init__(self, sink):
        self.author = _Author()
        self.channel = _Channel(sink)
        self.attachments = []


class _Result:
    def __init__(self, content):
        self.content = content
        self.thinking = ""
        self.stopped = False
        self.messages_added = []


class _Bot(DiscordBot):
    def __init__(self, cam_answer="allow"):
        import threading
        self.agent_name = KIN
        self.sent = []
        self.persisted = []
        self._surface_label = "discord"
        self._wrap_exec = None
        self.cam_asked = 0

        def _cam(who, uid):
            self.cam_asked += 1
            return cam_answer
        self._request_webcam_approval = _cam
        self._last_recall_used = []
        self._active_turns = {}
        self._turn_lock = threading.Lock()

    def _post_sync(self, channel, text):
        self.sent.append(text)

    def _post_from_thread(self, channel, text):
        self.sent.append(text)

    def _persist_turn(self, channel_id, role, content, share):
        self.persisted.append((role, content, share))


def run(person, kin_extra=None, uid=PERSON):
    """One Discord reply with these per-person settings. Returns what the
    tool loader and the tool loop were handed."""
    cfg = dict(DEFAULT_AGENT_CONFIG)
    cfg["model"] = "gemma4:31b"
    cfg.update(kin_extra or {})
    cfg["discord"] = save_person({}, "discord", None, str(uid), person)
    save_agent_config(KIN, cfg)
    save_kin_tools(KIN, ["read_file", "use_webcam"])
    seen = {"captured": 0}
    b = _Bot(cam_answer=person.get("_cam_answer", "allow"))
    b.get_config = lambda: cfg
    b.get_soul = lambda: "Your name is Bracken."
    b.get_memory = lambda: ""
    b.get_model_options = lambda: ("gemma4:31b", {"num_ctx": 8192})
    b._loop = object()

    def fake_load_tools(names, context=None, model=None, **kw):
        seen["names"] = list(names)
        seen["context"] = dict(context or {})

        def _cam(args):
            seen["captured"] += 1
            return json.dumps({"ok": True})
        ex = {n: (_cam if n == "use_webcam" else (lambda a: "ok"))
              for n in names}
        sch = [{"function": {"name": n, "parameters": {}}} for n in names]
        return sch, ex

    def fake_loop(model, messages, tools=None, tool_executor=None, **kw):
        seen["messages"] = messages
        seen["max_tool_calls"] = kw.get("max_tool_calls")
        if "use_webcam" in (tool_executor or {}):
            seen["cam_result"] = tool_executor["use_webcam"]({})
        return _Result("ok")

    class _Fut:
        def __init__(self, v):
            self.v = v

        def result(self, timeout=None):
            return self.v

    import asyncio
    saved = (asyncio.run_coroutine_threadsafe, llm_backend.run_tool_loop,
             kin_tools.load_tools)
    asyncio.run_coroutine_threadsafe = lambda coro, loop: _Fut(coro)
    llm_backend.run_tool_loop = fake_loop
    kin_tools.load_tools = fake_load_tools
    try:
        b._generate(_Message(b.sent), "hello there", None, None)
    finally:
        (asyncio.run_coroutine_threadsafe, llm_backend.run_tool_loop,
         kin_tools.load_tools) = saved
    seen["bot"] = b
    return seen


BASE = load_person({}, "discord", "")
FULL = dict(BASE, bucket="full")

s = run(dict(FULL, label="Alex"))
user_msgs = [m.get("content", "") for m in s.get("messages", [])
             if m.get("role") == "user"]
check("the name you gave them is what the kin sees",
      any("Alex" in m for m in user_msgs)
      and not any("Wren on Discord" in m for m in user_msgs))
s = run(dict(FULL, label=""))
check("...and with no name set, Discord's own name is used",
      any("Wren on Discord" in m.get("content", "")
          for m in s.get("messages", []) if m.get("role") == "user"))

s = run(dict(FULL, share_desktop=False), {"remote_max_tool_calls": 15})
check("someone not sharing with the desktop gets the kin's limit (15)",
      s.get("max_tool_calls") == 15)
s = run(dict(FULL, share_desktop=True), {"remote_max_tool_calls": 15})
check("sharing with the desktop does NOT lift the limit (they're separate)",
      s.get("max_tool_calls") == 15)
check("...it only puts their turns in the main conversation",
      all(p[2] for p in s["bot"].persisted))
s = run(dict(FULL, tool_cap=0), {"remote_max_tool_calls": 15})
check("a person given 0 has no limit", s.get("max_tool_calls") is None)
s = run(dict(FULL, tool_cap=4))
check("a person given a number gets that number",
      s.get("max_tool_calls") == 4)

s = run(dict(FULL, webcam="deny"))
check("webcam 'always refuse': nothing is captured, and you aren't asked",
      s["captured"] == 0 and s["bot"].cam_asked == 0
      and "refused" in (s.get("cam_result") or ""))
s = run(dict(FULL, webcam="auto"))
check("webcam 'auto-approve': captured without asking",
      s["captured"] == 1 and s["bot"].cam_asked == 0)
s = run(dict(FULL, webcam="ask", _cam_answer="allow"))
check("webcam 'ask me': you're asked, then it's captured",
      s["bot"].cam_asked == 1 and s["captured"] == 1)
s = run(dict(FULL, webcam="ask", _cam_answer="deny"))
check("...and when you say no, nothing is captured",
      s["bot"].cam_asked == 1 and s["captured"] == 0)

s = run(dict(FULL, file_reach="inside"), {"remote_unconfined_files": True})
check("files 'keep inside' confines the file tools, even with the kin box on",
      s["context"].get("confine_paths") is True)
s = run(dict(FULL, file_reach="outside"), {"remote_unconfined_files": False})
check("files 'allow outside' frees them, even with the kin box off",
      s["context"].get("confine_paths") is False)
s = run(dict(FULL, file_reach="default"), {"remote_unconfined_files": False})
check("files 'use the kin's setting' follows the kin box",
      s["context"].get("confine_paths") is True)

s = run(dict(BASE, bucket="read"), uid="*")
check("a '*' entry gives anyone without their own its tool access",
      "read_file" in s.get("names", []))

# --- the Discord mirror ------------------------------------------------
import discord_mirror  # noqa: E402

cfg = {"discord": {"enabled": True, "bot_token": "t",
                   "allow_from": ["11", "22", "*"],
                   "user_mirror_desktop": {"11": True, "22": False,
                                           "*": True, "33": True}}}
tok, ids = discord_mirror.mirror_targets(cfg)
check("mirror goes to people with it on, who are still on the list",
      ids == ["11"])
check("...and never to '*' or someone removed from the list",
      "*" not in ids and "33" not in ids)
check("no mirror when Discord is switched off",
      discord_mirror.mirror_targets(
          {"discord": dict(cfg["discord"], enabled=False)})[1] == [])

calls = []


def fake_call(token, method, path, body, timeout):
    calls.append((method, path, body))
    return {"id": "555"} if path == "/users/@me/channels" else {}


ok = discord_mirror.send_dm("t", "11", "x" * 4000, "Bracken", _call=fake_call)
check("a DM opens the person's DM channel, then sends",
      ok and calls[0] == ("POST", "/users/@me/channels",
                          {"recipient_id": "11"}))
check("...split to fit Discord's limit",
      len([c for c in calls if c[1] == "/channels/555/messages"]) == 3
      and all(len(c[2]["content"]) <= 2000 for c in calls[1:]))


def broken_call(*a, **k):
    raise OSError("no network")


check("a failed send says so and doesn't raise",
      discord_mirror.send_dm("t", "11", "hi", "Bracken",
                             _call=broken_call) is False)

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_discord_person_settings: all checks passed")
