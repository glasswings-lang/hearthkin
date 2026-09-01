# SPDX-License-Identifier: CC0-1.0
"""Guard test: arrowing through a long import speaker/conversation list
must not fire a full re-parse per keystroke.

Reported live: importing a file with 100+ distinct speakers made
arrowing down the "who is the kin" list produce incredible lag.
_on_speaker_picked applied the pick immediately rather than through the
300ms debounce typing already uses, on the reasoning that "a deliberate
pick shouldn't have to wait out" the typing debounce -- true for a mouse
click, which really is one event per pick, but wx.EVT_LISTBOX (and
wx.EVT_CHOICE on a collapsed wx.Choice) fires once per ARROW KEYPRESS
too, the same "many events per intended action" shape typing already
has. Applying immediately meant every arrow press spawned a full
background re-parse of the whole source file for any format whose
parsing depends on the kin name (Skype, Kindroid) -- dozens of
overlapping parses competing for CPU/disk while someone just arrowed
past a name.

This never constructs a real wx.Dialog (see CLAUDE.md, "Never build wx
widgets in the default test run") -- it calls the two handlers as
unbound functions against a minimal stub, with wx.CallLater itself faked
so the test can assert something was SCHEDULED without waiting on a real
event loop to fire it.

Run: python tests/test_import_list_debounce.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dialogs.import_history import ImportHistoryDialog  # noqa: E402
import dialogs.import_history as _mod  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


class _FakeCallLater:
    """Records what would have been scheduled instead of scheduling it —
    same pattern as tests/test_distill_walk_resume.py's _FakeCallLater."""

    def __init__(self):
        self.calls = []
        self.stopped = []

    def __call__(self, millis, fn, *args):
        self.calls.append((millis, fn, args))
        return self

    def Stop(self):
        self.stopped.append(True)


class _FakeChoice:
    def __init__(self):
        self.value = None

    def ChangeValue(self, v):
        self.value = v

    def GetValue(self):
        return self.value or ""


class _FakeListBox:
    def __init__(self, sel):
        self._sel = sel

    def GetSelection(self):
        return self._sel


class _DialogStub:
    _on_speaker_picked = ImportHistoryDialog._on_speaker_picked
    _on_conversation_changed = ImportHistoryDialog._on_conversation_changed

    def __init__(self):
        self._speaker_rows = ["SpeakerThree", "SpeakerTwo", "SpeakerOne"]
        self.speaker_list = _FakeListBox(0)
        self.kin_name_in_source = _FakeChoice()
        self._kin_name_timer = None
        self._conv_listing = []
        self.conv_choice = _FakeListBox(0)
        self.applied = 0
        self.parsed = 0

    def _apply_kin_name(self):
        self.applied += 1

    def _start_parse_worker(self):
        self.parsed += 1


_real_wx = _mod.wx


class _FakeWx:
    CallLater = _FakeCallLater()
    NOT_FOUND = _real_wx.NOT_FOUND


_mod.wx = _FakeWx()


# --- picking a speaker updates the field instantly, but debounces apply -

d = _DialogStub()
d.speaker_list = _FakeListBox(d._speaker_rows.index("SpeakerTwo"))
d._on_speaker_picked(None)
check("the kin-name field updates instantly (no wait for the debounce)",
      d.kin_name_in_source.GetValue() == "SpeakerTwo")
check("_apply_kin_name is NOT called synchronously",
      d.applied == 0)
check("a debounced call was scheduled instead, at the same 300ms typing uses",
      _mod.wx.CallLater.calls
      and _mod.wx.CallLater.calls[-1][0] == 300
      and _mod.wx.CallLater.calls[-1][1] == d._apply_kin_name)


# --- arrowing through MANY entries schedules ONE pending call per press,
# --- but never actually RUNS the expensive work until the timer fires --

_mod.wx.CallLater.calls.clear()
_mod.wx.CallLater.calls = []
d2 = _DialogStub()
for i, name in enumerate(["SpeakerThree", "SpeakerTwo", "SpeakerOne"] * 40):  # 120 presses
    d2._speaker_rows = ["SpeakerThree", "SpeakerTwo", "SpeakerOne"]
    d2.speaker_list = _FakeListBox(d2._speaker_rows.index(name))
    d2._on_speaker_picked(None)
check("120 rapid arrow presses schedule debounced calls, not 120 real parses",
      d2.applied == 0)
check("...each press re-armed the SAME timer (Stop called before each "
      "re-schedule after the first)",
      len(_mod.wx.CallLater.stopped) >= 118)


# --- the conversation picker has the identical fix ------------------------

d3 = _DialogStub()
d3._conv_listing = [
    {"id": "1", "display_name": "SpeakerThree"},
    {"id": "2", "display_name": "SpeakerTwo"},
]
d3.conv_choice = _FakeListBox(1)
_mod.wx.CallLater.calls = []
d3._on_conversation_changed(None)
check("picking a conversation updates the kin-name field instantly",
      d3.kin_name_in_source.GetValue() == "SpeakerTwo")
check("...but debounces the actual re-parse",
      d3.parsed == 0
      and _mod.wx.CallLater.calls
      and _mod.wx.CallLater.calls[-1][1] == d3._start_parse_worker)


_mod.wx = _real_wx

if _fails:
    print(f"\n{len(_fails)} FAILED: " + ", ".join(_fails))
    sys.exit(1)
print("\nall import-list debounce checks passed")
