# SPDX-License-Identifier: CC0-1.0
"""Rooms catch up, and the main window gets a Share a document button
(12 Nov 2026).

  * Share a document (Alt+D) writes the chosen file's path, quoted, into the
    message; sharing by path already worked but nothing on screen said so.
  * A file named in your latest room message reaches each kin for the round
    that answers it, not again after.
  * Regenerate in a room redoes the last kin's turn, taking out only its
    reply, its tool steps and Hearthkin's notes to it. It used to be switched
    off, and Ctrl+R went to the kin's one-on-one chat.
  * A `> ` park move in a room reply runs: one move, as in a Telegram group,
    and the next kin waits for it.

Drives the real main-window methods on a stand-in frame, with the park and
the window toolkit's timers stubbed. No window is built.

Run: python tests/test_room_extras.py
"""

import importlib.machinery
import importlib.util
import os
import sys
import tempfile

HOME = tempfile.mkdtemp(prefix="roomextras-")
os.environ["HEARTHKIN_HOME"] = HOME
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
import park_keeper  # noqa: E402
import reading_bridge  # noqa: E402
import tools as kin_tools  # noqa: E402

_rg = Frame._regen_room_turn.__globals__
later = []


class _Wx:
    @staticmethod
    def CallLater(ms, fn, *a):
        later.append(fn)

    @staticmethod
    def CallAfter(fn, *a, **k):
        fn(*a, **k)


class _Thread:
    def __init__(self, target, daemon=True):
        self.target = target

    def start(self):
        self.target()


class _Threading:
    Thread = _Thread


_rg["wx"] = _Wx
_rg["threading"] = _Threading
_rg["save_room_conversation"] = lambda name, convo: None
for k in ("Opal", "Vesper"):
    kp.save_agent_config(k, dict(kp.DEFAULT_AGENT_CONFIG, model="stub"))


class _Display:
    def __init__(self):
        self.text = ""

    def AppendText(self, t):
        self.text += t

    def SetValue(self, t):
        self.text = t


class _Box:
    def __init__(self, text, at):
        self.text, self.at = text, at

    def GetValue(self):
        return self.text

    def GetInsertionPoint(self):
        return self.at

    def WriteText(self, t):
        self.text = self.text[:self.at] + t + self.text[self.at:]
        self.at += len(t)

    def SetFocus(self):
        pass


class Room:
    _regen_room_turn = Frame._regen_room_turn
    _room_shared_file_block = Frame._room_shared_file_block
    _room_park_move = Frame._room_park_move
    _room_park_done = Frame._room_park_done
    _render_room_conversation = Frame._render_room_conversation
    _insert_shared_path = Frame._insert_shared_path
    _on_regen = Frame._on_regen

    def __init__(self, convo, members=("Opal", "Vesper")):
        self.room_conversation = [dict(m) for m in convo]
        self._room_active_order = list(members)
        self._room_active = False
        self._streaming = False
        self._room_auto_mode = False
        self._room_round_count = 3
        self._room_round_index = 0
        self._closing = False
        self.current_room = "Den"
        self.current_agent = "Opal"
        self.chat_display = _Display()
        self.status = []
        self.ran = []

    def _set_status(self, t):
        self.status.append(t)

    def _block_header(self, speaker, ts):
        return speaker

    def _run_next_kin_in_round(self):
        self.ran.append(list(self._room_active_order))


# --- Share a document ----------------------------------------------------
doc = os.path.join(HOME, "garden plan.txt")
with open(doc, "w", encoding="utf-8") as f:
    f.write("Beans by the fence.")
r = Room([])
r.input_box = _Box("Have a look at this", 19)
import frame.input_attach_mixin as iam  # noqa: E402
iam.nvda_speak = lambda t: None
r._insert_shared_path(doc)
check("Share a document writes the file's path, quoted, into the message",
      r.input_box.text == f'Have a look at this "{doc}" ')
check("...which is exactly what sharing by path picks up",
      reading_bridge.extract_shared_paths(r.input_box.text) == [doc])
check("...and you're told what will be shared, and with whom",
      "garden plan.txt" in r.status[-1] and "the room" in r.status[-1])

# --- a shared file in a room --------------------------------------------
convo = [{"role": "user", "content": f'thoughts? "{doc}"', "speaker": "You"}]
r = Room(convo)
check("a file named in your room message reaches the first kin",
      "Beans by the fence." in r._room_shared_file_block())
r.room_conversation.append({"role": "assistant", "content": "Lovely.",
                            "speaker": "Opal"})
check("...and the next kin in that round",
      "Beans by the fence." in r._room_shared_file_block())
r.room_conversation.append({"role": "assistant", "content": "Agreed.",
                            "speaker": "Vesper"})
check("...but not again once the round has answered it",
      r._room_shared_file_block() == "")

# --- regenerate -------------------------------------------------------------
base = [
    {"role": "user", "content": "Hello.", "speaker": "You"},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}],
     "speaker": "Opal"},
    {"role": "tool", "content": "res", "tool_call_id": "a", "speaker": "Opal"},
    {"role": "assistant", "content": "Opal here.", "speaker": "Opal"},
    {"role": "system", "content": "[note to Opal]", "speaker": "Opal"},
]
r = Room(base, members=("Vesper", "Opal"))
r._on_regen(None)
check("Regenerate in a room redoes the last kin's turn",
      r.ran == [["Opal"]])
check("...taking out its reply, its tool steps and its notes, and only those",
      r.room_conversation == [base[0]])
check("...and it isn't counted as a new round", r._room_round_count == 2)

two = base + [{"role": "assistant", "content": "Vesper here.",
               "speaker": "Vesper"}]
r = Room(two)
r._on_regen(None)
check("with another kin after it, only that later kin's turn is redone",
      r.ran == [["Vesper"]] and r.room_conversation == base)

r = Room(two)
r._room_active = True
r._on_regen(None)
check("not while a round is running", r.ran == [] and
      r.room_conversation == two and "Wait" in r.status[-1])
r = Room(two)
r._room_auto_mode = True
r._on_regen(None)
check("not with Auto-continue on (it would start a new round after)",
      r.ran == [] and "Auto-continue" in r.status[-1])
r = Room([{"role": "user", "content": "Hi", "speaker": "You"}])
r._on_regen(None)
check("nothing to redo when no kin has answered yet",
      r.ran == [] and "Nothing to regenerate" in r.status[-1])


# --- a park move ------------------------------------------------------------
class _Host:
    def __init__(self):
        self.ran = []

    def reachable(self, kin):
        return True, ""

    def run(self, kin, cmd, say=""):
        self.ran.append(cmd)
        return "Luna purrs."

    def decorate(self, kin, cmd, res):
        return res + " (you have 3 seeds)"


host = _Host()
kin_tools.get_game = lambda name: host
park_keeper.kin_park_mode = lambda kin: "chat"
r = Room([{"role": "user", "content": "go on", "speaker": "You"}])
r._room_active = True
later.clear()
took = r._room_park_move("Opal", "I'll check on her.\n> pet luna")
check("a park move in a room reply runs: one move", took
      and host.ran == ["pet luna"])
check("...the room sees what happened", "🌳 Luna purrs." in
      r.chat_display.text)
check("...the kin gets its own copy, for it alone",
      r.room_conversation[-1]["role"] == "system"
      and r.room_conversation[-1]["speaker"] == "Opal"
      and "3 seeds" in r.room_conversation[-1]["content"])
check("...and then the next kin speaks", len(later) == 1)
later.clear()
r._room_active = False
r._room_park_move("Opal", "> look")
check("if you stopped the round meanwhile, it doesn't carry on",
      later == [])
park_keeper.kin_park_mode = lambda kin: "off"
check("a kin that doesn't play from chat: nothing runs",
      not r._room_park_move("Opal", "> look") and host.ran[-1] == "look")


# --- a picture in a room ---------------------------------------------------
class _Hideable:
    def __init__(self):
        self.value, self.hidden = "x", False

    def SetValue(self, v):
        self.value = v

    def Hide(self):
        self.hidden = True


pic = os.path.join(HOME, "beans.png")
with open(pic, "wb") as f:
    f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


class PicRoom(Room):
    _send_to_room = Frame._send_to_room
    _room_history_for = Frame._room_history_for
    _room_opened_by_kin = Frame._room_opened_by_kin
    _room_fit_images = Frame._room_fit_images
    config = {"user_name": "Wren"}

    def __init__(self):
        super().__init__([])
        self._room_round_order = ["Opal", "Vesper"]
        self._pending_attachment = pic
        self._pending_attachment_rel = None
        self.attached_label = _Hideable()
        self.clear_attach_btn = _Hideable()
        self.input_box = _Box("", 0)
        self.shown = []

    def _append_block(self, who, text, when):
        self.shown.append(text)


r = PicRoom()
r._send_to_room("look at these beans")
row = r.room_conversation[-1]
from kin_persistence import attachment_abspath  # noqa: E402
check("a picture sent to a room is kept with your message",
      row.get("attachments") and len(row["attachments"]) == 1)
check("...and saved where each member's own pictures live",
      all(os.path.isfile(attachment_abspath(k, row["attachments"][0]))
          for k in ("Opal", "Vesper")))
check("...the room shows that a picture went with it",
      "[image attached]" in r.shown[-1])
check("...and it's no longer waiting to be sent",
      r._pending_attachment is None and r.attached_label.hidden)
hist = r._room_history_for("Opal")
check("each kin's history carries the picture on your message",
      any(m.get("attachments") == row["attachments"] for m in hist))
sees = Frame._room_fit_images(hist, "stub", supports=True)
check("a kin whose model can see is sent it", sees == hist)
blind = Frame._room_fit_images(hist, "stub", supports=False)
check("a kin whose model can't see is told one was shared, not sent it",
      not any(m.get("attachments") for m in blind)
      and any("shared a picture" in (m.get("content") or "") for m in blind))

# --- which memories a room kin drew on -------------------------------------
class StatusRoom:
    _compose_default_status = Frame._compose_default_status
    current_room = "Den"
    room_cfg = {"members": ["Opal", "Vesper"]}
    room_conversation = [{"role": "user", "content": "hi"}]


s = StatusRoom()
s._room_recall_used = ("Opal", ["a", "b"])
check("the room's Activity line says which kin drew on its memories",
      "Opal recalled 2 memories" in s._compose_default_status())
s._room_recall_used = ("Opal", [])
check("...and says nothing when none were",
      "recalled" not in s._compose_default_status())

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_room_extras: all checks passed")
