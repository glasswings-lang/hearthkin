# SPDX-License-Identifier: CC0-1.0
"""Guard test: pressing "Test connection" gives an answer you can reach.

Both connection dialogs — adding an API provider, and adding an Ollama
machine — showed the result in a single-line read-only TextCtrl. On wxMSW
that control is not keyboard-focusable at all, so the answer to "did it work?"
could not be reached by Tab. Measured on an isolated desktop: the Tab walk
went Test connection -> Save and skipped the result every time. Nothing spoke
it either, and focus stays on the button, so for someone using a screen
reader, pressing Test connection produced nothing.

This builds both real dialogs and checks two things: the result box is in the
tab order, and the result is spoken when it arrives. The measure itself is
checked first against a positive control — a single-line read-only box must
report NOT focusable — because a check that reports every box as reachable
would pass on the old dialogs too.

Run it safely with:  python tests/_gui_runner.py tests/test_test_result_reachable.py
"""

import os
import sys
import tempfile

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="testresult-", dir=(os.environ.get("HEARTHKIN_HOME") or None))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# Same opt-in gate as the other widget tests: building a top-level window
# takes the foreground on the live desktop, and a screen reader follows focus.
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

import audio  # noqa: E402
from dialogs.api_providers import _ProviderEntryDialog  # noqa: E402
from dialogs.ollama_machines import _MachineEntryDialog  # noqa: E402

spoken = []
_real_speak = audio.nvda_speak
audio.nvda_speak = lambda text, *a, **k: spoken.append(text)

try:
    # Positive control: the measure must be able to see an unreachable box.
    probe = wx.Dialog(None)
    single = wx.TextCtrl(probe, style=wx.TE_READONLY)
    multi = wx.TextCtrl(probe, style=wx.TE_READONLY | wx.TE_MULTILINE)
    check("control: a single-line read-only box is NOT keyboard-focusable",
          not single.CanAcceptFocusFromKeyboard())
    check("control: a multiline read-only box IS keyboard-focusable",
          multi.CanAcceptFocusFromKeyboard())
    probe.Destroy()

    for label, dlg in (
            ("API provider dialog",
             _ProviderEntryDialog(None, name="example",
                                  url="https://example.invalid/v1")),
            ("Ollama machine dialog",
             _MachineEntryDialog(None, name="Spare laptop",
                                 url="http://example.invalid:11434"))):
        check("%s: the Test connection result is in the tab order" % label,
              dlg.test_result.CanAcceptFocusFromKeyboard())
        # Its place: right after the Test button, before Save.
        order = [c for c in dlg.GetChildren() if c.CanAcceptFocusFromKeyboard()]
        i_btn = order.index(dlg.test_btn) if dlg.test_btn in order else -1
        check("%s: ...directly after the Test connection button" % label,
              i_btn >= 0 and i_btn + 1 < len(order)
              and order[i_btn + 1] is dlg.test_result)
        spoken.clear()
        dlg._on_test_done("OK — reachable, 3 models available.")
        check("%s: the result is spoken when it arrives" % label,
              spoken == ["OK — reachable, 3 models available."])
        check("%s: ...and left in the box to re-read" % label,
              dlg.test_result.GetValue() == "OK — reachable, 3 models available.")
        check("%s: the Test button is usable again afterwards" % label,
              dlg.test_btn.IsEnabled())
        dlg.Destroy()
finally:
    audio.nvda_speak = _real_speak

if _fails:
    print("\nFAILED %d: %s" % (len(_fails), ", ".join(_fails)))
    sys.exit(1)
print("\ntest_test_result_reachable: all checks passed")
