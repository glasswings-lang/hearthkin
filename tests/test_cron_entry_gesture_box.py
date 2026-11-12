# SPDX-License-Identifier: CC0-1.0
"""The scheduled-job dialog's "one more try if no tool runs" box.

Builds the real dialog (so it only runs on the isolated desktop, never on the
live one where a screen reader follows focus) and checks that the box is
there, is a stop in the tab order with its own explainer, carries a shortcut
letter no other control uses, and round-trips through the saved entry.
"""

import os
import re
import sys
import tempfile

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="gesturebox-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


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
from dialogs.cron_entry import CronEntryDialog  # noqa: E402


def main():
    # A fresh job: the box is there and off.
    dlg = CronEntryDialog(None, "Wren", {"times": ["08:00"], "prompt": "Write today's entry."})
    box = getattr(dlg, "gesture_retry_check", None)
    check("the box exists", box is not None)
    check("it starts unticked for a new job", box is not None and not box.GetValue())
    check("unticked saves as off", dlg.get_entry().get("gesture_retry") is False)
    if box is not None:
        box.SetValue(True)
    check("ticked saves as on", dlg.get_entry().get("gesture_retry") is True)

    # Reachable by Tab, and followed by its explainer.
    stops = [c for c in dlg.GetChildren() if c.CanAcceptFocusFromKeyboard()]
    names = [c.GetName() for c in stops]
    check("the box is a tab stop", box in stops)
    check("its explainer is reachable too", "What one more try does" in names)
    if box in stops and "What one more try does" in names:
        check("...right after the box",
              names.index("What one more try does") == stops.index(box) + 1)

    # Its shortcut letter is unique among the dialog's labels.
    letters = []
    for c in dlg.GetChildren():
        lab = c.GetLabel() if hasattr(c, "GetLabel") else ""
        m = re.search(r"&(\w)", lab or "")
        if m:
            letters.append(m.group(1).lower())
    check("shortcut letters are all different", len(letters) == len(set(letters)))
    dlg.Destroy()

    # An existing job keeps its setting, and other fields survive.
    dlg = CronEntryDialog(None, "Wren", {"times": ["08:00"], "prompt": "p",
                                         "gesture_retry": True, "tend_retry": 2})
    check("an existing job opens with the box ticked", dlg.gesture_retry_check.GetValue())
    e = dlg.get_entry()
    check("...and saves it back on", e.get("gesture_retry") is True)
    check("...without touching the tending retry", e.get("tend_retry") == 2)
    dlg.Destroy()

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
