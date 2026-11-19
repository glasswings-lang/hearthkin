# SPDX-License-Identifier: CC0-1.0
"""A wake-up shows its person what it did (18 Nov 2026).

Since wake-ups gained destinations (12 Nov), a wake-up addressed to a
person's Telegram DM posted only the reply: the "_used: ..._" receipt was
dropped (it was meant for groups only) and the tool steps that Telegram chat
shows with "Show tool calls in chat" on were never wired into wake-ups at all.
Its person had no way to see what a wake-up had actually done.

What this file pins:
  * with show_tool_calls on, each tool step is posted to the wake-up's
    Telegram DM as it happens: "🔧 name(args)" then "→ result"
  * groups and channels never get tool steps
  * with show_tool_calls off, nothing extra is posted
  * an unaddressed wake-up's steps go to the mirror-enabled people
  * the steps go only to the thread running that wake-up (two kins' wake-ups
    run on separate threads in the desktop app)
  * the receipt footer goes to a person's DM, never to a group
  * nothing is left set after the wake-up
"""

import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="waketoollines-", dir=os.environ.get("HEARTHKIN_HOME"))

import hearthkin_cron as HC  # noqa: E402
import llm_backend  # noqa: E402
import reach_places  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


sent = []
reach_places.deliver = lambda kin, cfg, surface, pid, text, *a, **k: (
    sent.append((surface, str(pid), text)) or (True, ""))


def fake_loop(*args, **kwargs):
    cb = kwargs.get("on_tool_call")
    if cb:
        cb("read_file", {"path": "memory/park.md"}, "# Park\nthe egg hatched",
           False)
        cb("edit_file", {"path": "x.md"}, "edit_file: no such file", True)

    class R:
        stopped = False
        messages_added = []
    return R()


llm_backend.run_tool_loop = fake_loop

CFG = {"telegram": {"enabled": True, "bot_token": "t", "show_tool_calls": True}}
DM = [{"surface": "telegram_dm", "id": "111", "label": "Person"}]
GROUP = [{"surface": "telegram_group", "id": "-222", "label": "Group"}]

# --- DM, show tool calls on
sent.clear()
with HC._wake_tool_lines("k", CFG, DM + GROUP):
    HC._cron_tool_loop([], model="m")
dm_lines = [t for s, p, t in sent if p == "111"]
check("the call line reaches the DM",
      "🔧 read_file(path=memory/park.md)" in dm_lines)
check("its result follows", any(t.startswith("→ # Park") for t in dm_lines))
check("a failed step is marked as one",
      any(t.startswith("⚠️ edit_file:") for t in dm_lines))
check("the group gets no tool steps", not any(p == "-222" for s, p, t in sent))
check("nothing is left set after the wake-up",
      getattr(HC._WAKE_LINES, "sink", None) is None)

# --- an existing on_tool_call still runs
seen = []
sent.clear()
with HC._wake_tool_lines("k", CFG, DM):
    HC._cron_tool_loop([], model="m",
                       on_tool_call=lambda *a: seen.append(a[0]))
check("a caller's own tool callback still runs", seen == ["read_file", "edit_file"])
check("...alongside the DM lines", len(sent) == 4)

# --- show tool calls off
sent.clear()
off = {"telegram": dict(CFG["telegram"], show_tool_calls=False)}
with HC._wake_tool_lines("k", off, DM):
    HC._cron_tool_loop([], model="m")
check("with show tool calls off, nothing extra is posted", not sent)

# --- unaddressed wake-up: mirror-enabled people
sent.clear()
legacy = {"telegram": dict(CFG["telegram"],
                           user_mirror_to_telegram={"333": True, "444": False})}
with HC._wake_tool_lines("k", legacy, None):
    HC._cron_tool_loop([], model="m")
check("an unaddressed wake-up's steps go to mirror-enabled people",
      {p for s, p, t in sent} == {"333"})

# --- another thread's wake-up is not touched
sent.clear()
other = []


def other_thread():
    HC._cron_tool_loop([], model="m")
    other.append(len(sent))


with HC._wake_tool_lines("k", CFG, DM):
    t = threading.Thread(target=other_thread)
    t.start()
    t.join()
check("a wake-up on another thread posts nothing to this one's DM",
      other == [0])

# --- receipt footer: DM yes, group no
delivered = []
HC._deliver_to_destinations = lambda kin, cfg, text, dests: delivered.append(
    ([d["surface"] for d in dests], text))


class Res:
    messages_added = [{"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "read_file", "arguments": {}}}]},
        {"role": "tool", "content": "ok"}]


HC._post_cron_reply_to_telegram("k", CFG, "Morning.", Res(), [1],
                                destinations=DM + GROUP)
dm_text = next((t for s, t in delivered if s == ["telegram_dm"]), "")
grp_text = next((t for s, t in delivered if s == ["telegram_group"]), "")
check("the DM gets the reply with the receipt",
      dm_text.startswith("Morning.") and "_used: read_file_" in dm_text)
check("the group gets the reply without it", grp_text == "Morning.")

print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("all passed")
