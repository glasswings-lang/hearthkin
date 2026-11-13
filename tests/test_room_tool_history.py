# SPDX-License-Identifier: CC0-1.0
"""A room kin keeps its own tool steps and gets the notes it gets elsewhere
(12 Nov 2026).

Found by adding rooms to the surface check. Before:
  * a room kin's tool calls and results were shown on screen and dropped,
    so next turn it couldn't see what its tools had done, and the note
    Hearthkin left about a tool call it only described went with them;
  * a file it wrote out in a fenced block was never saved (and the kin was
    never told the fence existed), and a kin without write tools was
    promised a memory save by its prompt that never happened;
  * a kin that said it read a file it never opened wasn't told;
  * a note Hearthkin left a kin came back to that kin as its OWN words.

Drives the real _on_room_kin_done and _room_history_for on a stand-in frame,
with real kin folders on disk. No window is built.

Run: python tests/test_room_tool_history.py
"""

import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="roomtools-")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


_spec = importlib.util.spec_from_loader(
    "hearthkin_mod", importlib.machinery.SourceFileLoader(
        "hearthkin_mod", os.path.join(ROOT, "hearthkin.pyw")))
_hk = importlib.util.module_from_spec(_spec)
sys.modules["hearthkin_mod"] = _hk
_spec.loader.exec_module(_hk)
Frame = _hk.Hearthkin

import kin_persistence as kp  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402

# The room module's own names: no window toolkit timers, no disk save of the
# room (the conversation is checked in memory).
_g = Frame._on_room_kin_done.__globals__


class _Wx:
    @staticmethod
    def CallLater(ms, fn, *a):
        return None

    @staticmethod
    def CallAfter(fn, *a):
        return None


_g["wx"] = _Wx
_g["save_room_conversation"] = lambda name, convo: None

for name, tools in (("Opal", ["read_file", "write_file"]),
                    ("Vesper", ["read_file"]),
                    ("Moss", [])):
    d = kin_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    kp.save_agent_config(name, dict(kp.DEFAULT_AGENT_CONFIG, model="stub"))
    kp.save_kin_tools(name, tools)


class _Display:
    def __init__(self):
        self.text = ""

    def AppendText(self, t):
        self.text += t

    def SetValue(self, t):
        self.text = t


class Room:
    config = {"user_name": "Wren"}
    _room_history_for = Frame._room_history_for
    _room_opened_by_kin = Frame._room_opened_by_kin
    _on_room_kin_done = Frame._on_room_kin_done
    _render_room_conversation = Frame._render_room_conversation
    _room_park_move = Frame._room_park_move

    def __init__(self):
        self.room_conversation = [{"role": "user", "content": "Hello all.",
                                   "ts": "2026-11-12T20:00:00"}]
        self.chat_display = _Display()
        self._closing = False
        self._stream_id = 1
        self._stream_buf = ""
        self._think_buf = ""
        self._streaming = True
        self.current_room = "Den"
        self.room_cfg = {}
        self._room_round_index = 0
        self._messages_since_distill = {}

    def _cancel_stream_watchdog_timer(self):
        pass

    def _log(self, *a):
        pass

    def _log_empty_reply(self, *a):
        pass

    def _chime(self, *a):
        pass

    def _run_next_kin_in_round(self):
        pass

    def _block_header(self, speaker, ts):
        return speaker

    def turn(self, speaker, reply, steps=()):
        self._current_room_speaker = speaker
        self._current_room_model = "stub"
        self._stream_buf = reply
        self._pending_room_tool_history = list(steps)
        self._on_room_kin_done(1)


room = Room()
steps = [
    {"role": "assistant", "content": "",
     "tool_calls": [{"id": "c1", "type": "function", "function": {
         "name": "read_file", "arguments": json.dumps({"path": "a.md"})}}]},
    {"role": "tool", "content": "THE FILE SAYS PLUMS", "tool_call_id": "c1",
     "name": "read_file"},
    {"role": "system", "content": "[hearthkin: you described a tool call "
                                  "but didn't make it]"},
]
room.turn("Opal", "It says plums.\n```write:notes/room.md\nplums, noted\n```",
          steps)

saved = kin_dir("Opal") / "notes" / "room.md"
check("a file a room kin writes out in its reply is saved",
      saved.exists() and "plums, noted" in saved.read_text(encoding="utf-8"))
check("...and the room is shown that it was saved",
      "room.md" in room.chat_display.text)

opal = room._room_history_for("Opal")
roles = [m["role"] for m in opal]
check("the kin reads back its own tool call and result, in order",
      any(m.get("tool_calls") for m in opal)
      and any(m["role"] == "tool" and "PLUMS" in m["content"] for m in opal)
      and roles.index("tool") < max(i for i, m in enumerate(opal)
                                    if m["role"] == "assistant"
                                    and "It says plums" in m["content"]))
check("...the note about a described tool call reaches it",
      any(m["role"] == "system" and "described a tool call" in m["content"]
          for m in opal))
check("...and so does the note saying what its fence saved",
      any(m["role"] == "system" and "room.md" in m["content"] for m in opal))
check("...and no note comes back to it as its own words",
      not any(m["role"] == "assistant" and "[hearthkin" in m["content"]
              for m in opal))

vesper = room._room_history_for("Vesper")
check("another kin never sees Opal's tool results or Opal's notes",
      not any("PLUMS" in (m.get("content") or "")
              or "described a tool call" in (m.get("content") or "")
              for m in vesper)
      and not any(m["role"] in ("tool", "system") or m.get("tool_calls")
                  for m in vesper))
check("...but does hear what Opal said, under Opal's name",
      any(m["role"] == "user" and "[Opal] It says" in m["content"]
          for m in vesper))

room.turn("Vesper", "*reads through the notes* They're lovely.")
vesper = room._room_history_for("Vesper")
check("a kin that says it read something it never opened is told",
      any(m["role"] == "system" for m in vesper))

room.turn("Moss", "I'll remember that.\n```memory.md\n- Wren likes plums\n```")
mem = kin_dir("Moss") / "memory.md"
check("a kin with no tools saves to its own memory from a room, as its "
      "prompt promises",
      mem.exists() and "plums" in mem.read_text(encoding="utf-8"))

room._render_room_conversation()
check("redrawing the room doesn't spill raw tool results into it",
      "PLUMS" not in room.chat_display.text
      and "It says plums" in room.chat_display.text)

old = Room()
old.room_conversation += [
    {"role": "assistant", "content": "Hi.", "speaker": "Opal"},
    {"role": "system", "content": "[hearthkin: salvaged from your tool "
                                  "call]", "speaker": "Opal"}]
h = old._room_history_for("Opal")
check("an older stored note reaches its kin as a note, not as its own words",
      h[-1] == {"role": "system",
                "content": "[hearthkin: salvaged from your tool call]"})

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_room_tool_history: all checks passed")
