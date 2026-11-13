# SPDX-License-Identifier: CC0-1.0
"""The compact window: what a screen reader meets, and what it gives back.

Builds the real window on the isolated desktop and checks: focus starts on
the kin's note, the explainer and the note are reachable by Tab and named,
the three buttons say what they do with letters no other control uses, an
edit to the note is what comes back, and "Write it again" ends the window
with its own answer.

Run: python tests/_gui_runner.py tests/test_compact_dialog.py
"""

import os
import re
import sys
import tempfile

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="compactdlg-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


if os.environ.get("HEARTHKIN_GUI_TESTS", "").strip() not in ("1", "true", "yes"):
    print("SKIP -- builds real widgets; run on an isolated desktop with:")
    print("    python tests/_gui_runner.py " + __file__)
    sys.exit(0)

try:
    import wx
except Exception as e:                                    # pragma: no cover
    print(f"SKIP wxPython unavailable ({e})")
    sys.exit(0)

app = wx.App()
from dialogs.compact import CompactDialog  # noqa: E402

dlg = CompactDialog(None, "Opal", "We were planting beans.", keep=6)
stops = [c for c in dlg.GetChildren() if c.CanAcceptFocusFromKeyboard()]
names = [c.GetName() for c in stops]
check("the explainer is reachable by Tab", "What compacting does" in names)
check("the note is reachable by Tab and named for the kin",
      "The note Opal wrote" in names)
info = [c for c in stops if c.GetName() == "What compacting does"][0]
check("the explainer says nothing is deleted and how many messages stay",
      "Nothing is deleted" in info.GetValue() and "last 6" in info.GetValue())
labels = {c.GetId(): c.GetLabel() for c in dlg.GetChildren()
          if isinstance(c, wx.Button)}
check("the buttons say what they do",
      labels.get(wx.ID_OK) == "&Compact with this note"
      and labels.get(wx.ID_RETRY) == "Write it &again"
      and labels.get(wx.ID_CANCEL) == "Cancel")
letters = [m.group(1).lower() for c in dlg.GetChildren()
           for m in [re.search(r"&(\w)", c.GetLabel() or "")] if m]
check("no two controls share a shortcut letter",
      len(letters) == len(set(letters)))
dlg.note_field.SetValue("  We were planting beans, and onions.  ")
check("an edit to the note is what comes back, trimmed",
      dlg.get_note() == "We were planting beans, and onions.")

answers = []
focus_seen = []


def _while_open():
    focus_seen.append(wx.Window.FindFocus())
    wx.PostEvent(dlg.FindWindowById(wx.ID_RETRY),
                 wx.CommandEvent(wx.wxEVT_BUTTON, wx.ID_RETRY))


wx.CallAfter(_while_open)
answers.append(dlg.ShowModal())
check("focus starts on the kin's note when the window opens",
      focus_seen and focus_seen[0] is dlg.note_field)
check("'Write it again' ends the window with its own answer",
      answers == [wx.ID_RETRY])
dlg.Destroy()

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_compact_dialog: all checks passed")
