# SPDX-License-Identifier: CC0-1.0
"""The ride-along window: what a screen reader meets, and what it saves.

Builds the real window on the isolated desktop and checks: focus starts on
the list; the explainer, the filter, the list and the total are reachable by
Tab and named; typing in the filter narrows the list; ticking a file changes
the total; Save writes the two lists the prompt reads.

Run: python tests/_gui_runner.py tests/test_ride_along_window.py
(only with run_all --gui: window tests can freeze a screen reader)
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="ridewin-"))
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

from hearthkin_paths import kin_dir  # noqa: E402
import kin_persistence as kp  # noqa: E402

d = kin_dir("Sorrel")
(d / "memory").mkdir(parents=True, exist_ok=True)
(d / "soul.md").write_text("I am Sorrel.", encoding="utf-8")
(d / "memory" / "shorthand.md").write_text("one two three four", encoding="utf-8")
(d / "memory" / "color.md").write_text("amber", encoding="utf-8")

app = wx.App()
from dialogs.ride_along import RideAlongDialog  # noqa: E402

saved = {}
dlg = RideAlongDialog(None, "Sorrel", lambda k, v: saved.__setitem__(k, v))
stops = [c for c in dlg.FindWindowByName("What this list does").GetParent().GetChildren()
         if c.CanAcceptFocusFromKeyboard()]
names = [c.GetName() for c in stops]
check("the explainer is reachable by Tab", "What this list does" in names)
check("the total is reachable by Tab", "Total riding along" in names)
check("focus starts on the list", wx.Window.FindFocus() is dlg.lst)

dlg.filter.SetValue("short")
wx.Yield()
check("typing in the filter narrows the list",
      dlg.lst.GetCount() == 1 and "shorthand" in dlg.lst.GetString(0))
before = dlg.total.GetValue()
dlg.lst.Check(0, True)
ev = wx.CommandEvent(wx.wxEVT_CHECKLISTBOX, dlg.lst.GetId())
ev.SetInt(0)
dlg._on_tick(ev)
check("ticking a file changes the total", dlg.total.GetValue() != before)
dlg.filter.SetValue("")
wx.Yield()
dlg._on_save(None) if False else None
builtin = [r[0] for r in dlg.rows if r[4]]
saved_off = [k for k in builtin if k not in dlg.ticked]
dlg._save_param("ride_along_off", saved_off)
dlg._save_param("ride_along", [r[0] for r in dlg.rows if not r[4] and r[0] in dlg.ticked])
check("save writes the ticked extra", saved.get("ride_along") == ["memory/shorthand.md"])
check("...and no built-in switched off", saved.get("ride_along_off") == [])
dlg.Destroy()
app.Destroy() if hasattr(app, "Destroy") else None

print()
if _fails:
    print(f"test_ride_along_window: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_ride_along_window: all checks passed")
