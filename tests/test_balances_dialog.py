# SPDX-License-Identifier: CC0-1.0
"""Guard test: the Balances window builds, fills in once, and is readable.

Pinned: the report box is named by its label for a screen reader; the
answer lands as one block, not line by line; the buttons carry distinct
shortcut letters; and the window takes no focus while it isn't shown.

Run safely (windows on an isolated desktop, no path to your foreground):
    python tests/_gui_runner.py tests/test_balances_dialog.py
"""

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="balances-dlg-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# OPT-IN, like every widget-building test here. Constructing a top-level wx
# window takes the FOREGROUND on Windows even when it is never shown, and a
# screen reader follows focus rather than visibility — so an ungated run drags
# NVDA into an invisible dialog mid-task. run_all.py runs this file itself, on
# an isolated desktop; this gate makes running it directly a deliberate act.
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

from dialogs.balances import BalancesDialog  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


LINES = ["OpenRouter: $4.12 left ($15.88 spent of $20.00).",
         "ollama-cloud (Ollama cloud): $7.25 of $20.00 included credit left."]
changes = []

dlg = BalancesDialog(None, check=lambda: LINES)
dlg.report_field.Bind(wx.EVT_TEXT, lambda e: (changes.append(1), e.Skip()))

deadline = time.time() + 10
while dlg._busy and time.time() < deadline:
    wx.Yield()
    time.sleep(0.02)
wx.Yield()

check("the answer arrived", not dlg._busy)
check("it reads as one line per service", dlg.report_field.GetValue() == "\n".join(LINES))
check("it was set in one go, not line by line", len(changes) == 1)
check("Check again is usable again afterwards", dlg.again_btn.IsEnabled())

kids = list(dlg.report_field.GetParent().GetChildren())
i = kids.index(dlg.report_field)
check("the box's label sits right before it, so NVDA names it",
      isinstance(kids[i - 1], wx.StaticText) and kids[i - 1].GetLabel() == "&Balances:")
check("the box is read-only", not dlg.report_field.IsEditable())

letters = [b.GetLabel().split("&")[1][0].lower()
           for b in (dlg.again_btn, dlg.copy_btn, dlg.close_btn, kids[i - 1])]
check("no two shortcut letters the same", len(letters) == len(set(letters)))

check("not shown, so it never took focus", not dlg.IsShown())

dlg2 = BalancesDialog(None, check=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
deadline = time.time() + 10
while dlg2._busy and time.time() < deadline:
    wx.Yield()
    time.sleep(0.02)
check("a check that blows up is said, not swallowed",
      "The check failed: boom" in dlg2.report_field.GetValue())

dlg.Destroy()
dlg2.Destroy()

# The watch settings, saved on close only when they changed.
cfg = {"balance_check_mins": 30, "balance_warn_usd": 1.0}
saves = []
d3 = BalancesDialog(None, check=lambda: [], config=cfg, on_save=saves.append)
check("settings start from the config",
      d3.watch_box.GetValue() and d3.warn_field.GetValue() == 1.0)
check("untouched: nothing saved", d3.save_settings() is False and saves == [])
d3.warn_field.SetValue(2.5)
check("a new amount is saved", d3.save_settings() and cfg["balance_warn_usd"] == 2.5
      and len(saves) == 1)
d3.watch_box.SetValue(False)
check("unticking turns the watch off", d3.save_settings() and cfg["balance_check_mins"] == 0)
d3.watch_box.SetValue(True)
check("ticking again restores half an hour", d3.save_settings()
      and cfg["balance_check_mins"] == 30)
kids3 = list(d3.warn_field.GetParent().GetChildren())
j = kids3.index(d3.warn_field)
check("the amount box's label sits right before it",
      isinstance(kids3[j - 1], wx.StaticText) and "dollars" in kids3[j - 1].GetLabel())
all_letters = []
for w in kids3:
    lab = w.GetLabel() if isinstance(w, (wx.Button, wx.CheckBox, wx.StaticText)) else ""
    if "&" in lab:
        all_letters.append(lab.split("&")[1][0].lower())
check("still no two shortcut letters the same", len(all_letters) == len(set(all_letters)))
d3.Destroy()

print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_balances_dialog: all checks passed")
