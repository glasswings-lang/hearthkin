# SPDX-License-Identifier: CC0-1.0
"""Guard test: the import dialog's tab order never changes shape.

    "I need to be able to see everything. tab order is my seeing."

That is the whole requirement. This dialog is read by tabbing through it, so
the tab order *is* the layout — not a convenience over the layout, the thing
itself. A control that appears when you pick a second file and vanishes when
you pick one changes the shape of the room between visits, and nothing
announces that it moved. You would find it by tabbing past where it wasn't.

Two controls used to do exactly that: the conversation picker (shown only for
Skype JSON exports) and, briefly, the combine-mode choice added alongside
multi-select. Both now stay put and say what is true instead — either the
conversations found, or that this source holds no choice to make.

Hiding was reached for because of a real earlier complaint about greyed-out
controls. That complaint was about DISABLED: a control announcing "unavailable"
tells you nothing about why. The fix for that was never to make things vanish.
Present-and-currently-inconsequential costs one Tab press. A layout that
rearranges costs the map.

This is a runtime test, not a source scan, because the previous check for it
was a static narrator report that lists hidden controls as tab stops and
cannot know which is true when the thing is actually running. It reported
"FINDINGS: none" on the version that hid two controls. Building the dialog and
counting what can genuinely take focus is the only honest way to ask.
"""

import os
import sys
import tempfile

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="taborder-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# OPT-IN, like every widget-building test here. Constructing a top-level wx
# window takes the FOREGROUND on Windows even though it is never shown, and a
# screen reader follows focus rather than visibility — so an ungated run drags
# NVDA into an invisible dialog mid-task. `tests/run_all.py` also refuses to
# start this file without the flag; this gate is what makes running it directly
# a deliberate act too.
if os.environ.get("HEARTHKIN_GUI_TESTS", "").strip() not in ("1", "true", "yes"):
    print("SKIP -- builds real widgets, which take the foreground on the "
          "live desktop, and a screen reader follows focus. Run it safely on "
          "an isolated desktop with:")
    print("    python tests/_gui_runner.py " + __file__)
    sys.exit(0)

try:
    import wx
except Exception as e:                                    # pragma: no cover
    print(f"SKIP wxPython unavailable ({e})")
    sys.exit(0)

app = wx.App()
from dialogs.import_history import ImportHistoryDialog  # noqa: E402

dlg = ImportHistoryDialog(None)


def focusable():
    """Everything that can genuinely take keyboard focus right now."""
    panel = dlg.GetChildren()[0]
    return [c for c in panel.GetChildren() if c.CanAcceptFocusFromKeyboard()]


def names():
    return [c.GetName() for c in focusable()]


baseline = names()
check("the dialog has a tab order to begin with", len(baseline) > 5)

# Every state a person can put this dialog into by choosing files.
states = {
    "several files": ["a.txt", "b.txt", "c.txt"],
    "one file": ["only.txt"],
    "cleared": [],
    "several again": ["x.txt", "y.txt"],
}
for label, paths in states.items():
    try:
        dlg._set_sources(paths)
        raised = None
    except Exception as e:
        raised = e
    check(f"selecting {label} raises nothing", raised is None)
    check(f"...and the tab order is unchanged ({label})", names() == baseline)

# The two controls that used to come and go must be reachable in every state.
#
# Checked by widget identity, not by name. This used to look them up through
# wx's GetName(), which meant the check quietly depended on a SetName() call --
# and SetName() does nothing for a screen reader on wxMSW, so those calls were
# removed once real StaticText labels went in. The test then "failed" while the
# dialog was strictly better, which is a test measuring the wrong thing: what
# matters here is whether the control can be tabbed to, and that is a property
# of the widget, not of what it happens to be called.
for want, widget in (("How to combine", dlg.combine_choice),
                     ("Conversation to import", dlg.conv_choice)):
    present = True
    for paths in states.values():
        dlg._set_sources(paths)
        if widget not in focusable():
            present = False
    check(f"{want!r} is reachable in every state", present)

# Never disabled either -- "unavailable" is announced without saying why,
# which is the failure that made hiding look attractive in the first place.
dlg._set_sources(["single.txt"])
disabled = [c.GetName() for c in focusable() if not c.IsEnabled()]
check("nothing in the tab order announces as unavailable", disabled == [])

# And the picker must never be an empty combo -- a control with nothing in it
# is as uninformative as one that isn't there.
conv = dlg.conv_choice if dlg.conv_choice in focusable() else None
check("the conversation picker exists with no Skype export loaded", conv is not None)
if conv is not None:
    check("...and is never an empty control", conv.GetCount() >= 1)
    # It must never contradict the rest of the screen. An earlier version said
    # "this source has only one conversation" while the same dialog reported 43
    # files selected -- a UI arguing with itself in front of the person using
    # it. Whatever it shows for a batch has to reflect the batch.
    dlg._set_sources([f"f{i}.txt" for i in range(43)])
    shown = conv.GetString(conv.GetSelection())
    check("...and for a 43-file batch it does not claim one conversation",
          "43" in shown and "one conversation" not in shown.lower())

dlg.Destroy()
app.Destroy()

print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_stable_tab_order: all checks passed")
