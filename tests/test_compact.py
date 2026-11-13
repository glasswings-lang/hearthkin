# SPDX-License-Identifier: CC0-1.0
"""Compacting a conversation (Chat -> Compact this conversation, 12 Nov 2026).

The kin writes itself a note in an ordinary message (no tools, so any kin
can), the person reads it and can edit it or have it written again, and then
a marker holding the note goes into the conversation. From then on the kin is
sent the note, the last few messages and what follows. Nothing is deleted:
the conversation on disk and the summarizer carry on over all of it.

Drives the real main-window methods on a stand-in frame, with the model and
the note window stubbed (no window is built here; the window itself has its
own test, test_compact_dialog.py), plus the shared helpers and Telegram's
shared-conversation reader.

Run: python tests/test_compact.py
"""

import importlib.machinery
import importlib.util
import os
import sys
import tempfile

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="compact-")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import chat_helpers as ch  # noqa: E402

# --- the shared helpers --------------------------------------------------
convo = [{"role": "user", "content": f"u{i}"} if i % 2 == 0
         else {"role": "assistant", "content": f"a{i}"} for i in range(10)]
check("a conversation never compacted is sent whole",
      ch.view_after_compaction(convo) == convo)
kf = ch.compact_keep_from(convo, 4)
check("keeping the last four messages starts four back", kf == 6)
marked = convo + [ch.compact_marker("my note", 4, preface="[before]")]
marked.append({"role": "user", "content": "after"})
view = ch.view_after_compaction(marked)
check("after compacting, the kin is sent its note, the kept messages and "
      "what follows",
      [m["content"] for m in view] == ["[before]\n\nmy note", "u6", "a7",
                                       "u8", "a9", "after"])
check("...the note goes as a plain note, not with the marker's bookkeeping",
      view[0] == {"role": "system", "content": "[before]\n\nmy note"})
check("...and the conversation itself is untouched", len(marked) == 12)
tooly = [{"role": "user", "content": "x"},
         {"role": "assistant", "content": "", "tool_calls": [{"id": "t"}]},
         {"role": "tool", "content": "r", "tool_call_id": "t"},
         {"role": "assistant", "content": "done"}]
check("the kept messages never start on a tool result",
      tooly[ch.compact_keep_from(tooly, 1)]["role"] != "tool")
twice = marked + [ch.compact_marker("second note", 2)]
v2 = ch.view_after_compaction(twice)
check("compacting again: only the newest note is sent",
      v2[0]["content"] == "second note"
      and not any("my note" in (m.get("content") or "") for m in v2))

# --- the main window's flow ---------------------------------------------
_spec = importlib.util.spec_from_loader(
    "hearthkin_mod", importlib.machinery.SourceFileLoader(
        "hearthkin_mod", os.path.join(ROOT, "hearthkin.pyw")))
_hk = importlib.util.module_from_spec(_spec)
sys.modules["hearthkin_mod"] = _hk
_spec.loader.exec_module(_hk)
Frame = _hk.Hearthkin

import kin_persistence as kp  # noqa: E402
import llm_backend  # noqa: E402
import dialogs.compact as dcompact  # noqa: E402
import frame.file_menu_mixin as fmm  # noqa: E402
# No window is built here: only the toolkit's answer codes and its
# call-later hook are used, taken from the module the app already loaded.
wx = fmm.wx

kp.save_agent_config("Opal", dict(kp.DEFAULT_AGENT_CONFIG, model="stub"))
spoken = []
fmm.nvda_speak = spoken.append


class _Thread:
    def __init__(self, target, daemon=True):
        self.target = target

    def start(self):
        self.target()


import threading  # noqa: E402
threading.Thread = _Thread           # the worker runs inline here
wx.CallAfter = lambda fn, *a: fn(*a)

asked = []


class _Result:
    def __init__(self, content):
        self.content = content
        self.thinking = ""


def fake_chat(model, messages, **kw):
    asked.append(messages)
    return _Result(f"<thinking>hmm</thinking>Note number {len(asked)}.")


llm_backend.chat = fake_chat

script = []
shown_notes = []


class FakeDialog:
    def __init__(self, parent, kin, note, keep=6):
        self.note = note
        shown_notes.append(note)

    def ShowModal(self):
        code, edit = script.pop(0)
        if edit:
            self.note = edit
        return code

    def get_note(self):
        return self.note

    def Destroy(self):
        pass


dcompact.CompactDialog = FakeDialog


class Win:
    _on_compact = Frame._on_compact
    _compact_say = Frame._compact_say
    _compact_user_name = Frame._compact_user_name
    _compact_messages = Frame._compact_messages
    _compact_ask = Frame._compact_ask
    _compact_show = Frame._compact_show
    _apply_compaction = Frame._apply_compaction
    _maybe_hint_compact = Frame._maybe_hint_compact
    _history_entry_for_model = Frame._history_entry_for_model
    _compact_tool_history = Frame._compact_tool_history

    def __init__(self):
        self.config = {"user_name": "Wren"}
        self.current_agent = "Opal"
        self.current_room = None
        self.agent_cfg = kp.load_agent_config("Opal")
        self._streaming = False
        self.conversation = [dict(m) for m in convo]
        self.status = []
        self.persisted = 0

    def _set_status(self, t):
        self.status.append(t)

    def _persist_current_conversation(self):
        self.persisted += 1

    def _render_conversation(self):
        pass

    def _update_token_display(self):
        pass


w = Win()
script[:] = [(wx.ID_RETRY, None), (wx.ID_OK, "Note number 2, edited by Wren.")]
w._on_compact(None)
check("the kin is asked in an ordinary message, with no tools offered",
      asked and asked[0][-1]["role"] == "user"
      and "write that note now" in asked[0][-1]["content"]
      and "Wren" in asked[0][-1]["content"])
check("...after the conversation as it's sent now",
      [m.get("content") for m in asked[0][1:-1]][-2:] == ["u8", "a9"])
check("'Write it again' asks the kin again", len(asked) == 2)
mark = w.conversation[-1]
check("compacting adds one marker and deletes nothing",
      len(w.conversation) == len(convo) + 1 and mark.get("compact")
      and w.conversation[:len(convo)] == convo and w.persisted == 1)
check("the note you're shown is the kin's words, without its thinking",
      shown_notes[0] == "Note number 1.")
check("...and what's kept is the note as you left it",
      mark["content"].endswith("Note number 2, edited by Wren."))
check("...under a line saying what it is",
      "compacted here" in mark["content"])
check("...and you're told it worked and that nothing was deleted",
      "Nothing was deleted" in w.status[-1])
sent = ch.view_after_compaction(w.conversation)
check("next send: the note, then the last six messages",
      sent[0]["role"] == "system" and len(sent) == 7
      and sent[-1]["content"] == "a9")

w2 = Win()
script[:] = [(wx.ID_CANCEL, None)]
w2._on_compact(None)
check("cancelling changes nothing", w2.conversation == convo
      and "Nothing was changed" in w2.status[-1])

w3 = Win()
w3.current_room = "Den"
w3._on_compact(None)
check("in a room, it says it's for a conversation with one kin",
      "one kin" in w3.status[-1])

# --- the heads-up ---------------------------------------------------------
w4 = Win()
llm_backend.last_reported_prompt_tokens = lambda kin: 5000
w4.agent_cfg = dict(w4.agent_cfg, num_ctx=8000, compact_hint_pct=70)
w4._maybe_hint_compact()
check("past the kin's setting, you're told once how to compact",
      w4.status and "percent full" in w4.status[-1]
      and "Control Shift K" in w4.status[-1])
n = len(w4.status)
w4._maybe_hint_compact()
check("...and only once", len(w4.status) == n)
w5 = Win()
w5.agent_cfg = dict(w5.agent_cfg, num_ctx=8000, compact_hint_pct=0)
w5._maybe_hint_compact()
check("0 means never", not w5.status)
w6 = Win()
w6.agent_cfg = dict(w6.agent_cfg, num_ctx=80000, compact_hint_pct=70)
w6._maybe_hint_compact()
check("below the setting, nothing is said", not w6.status)

# --- the real send paths use it -------------------------------------------
import ast  # noqa: E402
for path, label in (("frame/chat_send_mixin.py", "the main chat"),
                    ("frame/lifecycle_mixin.py", "the mini chat")):
    tree = ast.parse(open(os.path.join(ROOT, path), encoding="utf-8").read())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "view_after_compaction"]
    check(f"{label} sends from the latest note onward", bool(calls))

# --- Telegram, sharing the desktop conversation --------------------------
import telegram_bot  # noqa: E402
tb = telegram_bot.TelegramBot.__new__(telegram_bot.TelegramBot)
tb._user_shares_desktop = lambda uid: True
tb._load_shared_conversation_cached = lambda: w.conversation
got = tb._load_history_for(1)
check("a Telegram chat sharing the desktop conversation carries on from "
      "the note too", got and got[0]["role"] == "system"
      and "edited by Wren" in got[0]["content"] and len(got) == 7)

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_compact: all checks passed")
