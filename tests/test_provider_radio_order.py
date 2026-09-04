# SPDX-License-Identifier: CC0-1.0
"""Guard test: the model browser's Provider radios keep their place.

    "I need to be able to see everything. tab order is my seeing."
    -- the requirement behind test_stable_tab_order.py

The Provider control used to be two radio buttons written out by hand. It is
now built from the provider registry and REBUILT whenever the providers
dialog closes, because a provider you just added has to appear without
reopening anything.

Rebuilding is where the danger is. Destroying controls and creating new ones
puts the new ones at the END of the sibling order, so the radios can silently
migrate to *after* the "Manage providers" button that sits beside them --
the room rearranging itself between visits, which is the exact thing the
import dialog was fixed for.

This test builds the real dialog, records the order, rebuilds the radios, and
insists nothing moved. It also checks each radio carries a keyboard
accelerator, since the hand-written pair had Alt+O and Alt+P and a generated
label is an easy place to drop them.

Run it safely with:  python tests/_gui_runner.py tests/test_provider_radio_order.py
"""

import os
import sys
import tempfile

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="radioorder-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# OPT-IN, same gate and same reasoning as test_stable_tab_order.py: building a
# top-level window takes the foreground on Windows even unshown, and a screen
# reader follows focus rather than visibility.
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

import kin_persistence as kp  # noqa: E402
from model_browser import ModelBrowserDialog  # noqa: E402

# Two providers, so the list is not the degenerate single-entry case.
kp.save_api_providers([("featherless", "https://api.featherless.ai/v1"),
                       ("openrouter", "https://openrouter.ai/api/v1")])

dlg = ModelBrowserDialog(None, current_model="", show_machine_picker=True)


def provider_area_order():
    """The focusable controls inside the Provider box, in tab order."""
    return [c for c in dlg._provider_box.GetChildren()
            if c.CanAcceptFocusFromKeyboard()]


def labels():
    # Strip the mnemonic marker before comparing: "Manage pro&viders" does not
    # contain the substring "providers", which is a fine way to write a test
    # that fails for the wrong reason.
    return [c.GetLabel().replace("&", "") for c in provider_area_order()]


baseline = labels()
check("the Provider box has radios and the manage button",
      len(baseline) >= 3)
check("Ollama is first, so the local option is where it has always been",
      baseline and "Ollama" in baseline[0])
check("the manage button is LAST, after every provider",
      baseline and "roviders" in baseline[-1])

# Every registered provider is offered, not just the two that used to be
# hard-coded.
joined = " ".join(baseline).lower()
check("openrouter is offered", "openrouter" in joined)
check("a user-added provider is offered too", "featherless" in joined)

# --- the actual regression this file exists for -------------------------
dlg._build_provider_radios()
after = labels()
check("rebuilding the radios does not reorder the Provider box",
      after == baseline)
check("...and the manage button is still last",
      after and "roviders" in after[-1])

# Rebuild twice: an off-by-one in the insert index shows up on the second
# pass rather than the first.
dlg._build_provider_radios()
check("a second rebuild is still stable", labels() == baseline)

# --- keyboard access ----------------------------------------------------
radios = [c for c in provider_area_order() if isinstance(c, wx.RadioButton)]
check("every provider has a radio", len(radios) >= 2)
missing = [r.GetLabel() for r in radios if "&" not in r.GetLabel()]
check("every radio has an Alt accelerator (was lost when generated): %r"
      % (missing,), not missing)
accels = [r.GetLabel().split("&", 1)[1][:1].lower()
          for r in radios if "&" in r.GetLabel()]
check("no two radios claim the same accelerator: %r" % (accels,),
      len(set(accels)) == len(accels))

# --- a removed provider must not leave an unset group -------------------
dlg._provider = "featherless"
kp.save_api_providers([("openrouter", "https://openrouter.ai/api/v1")])
import llm_backend  # noqa: E402
llm_backend._user_providers_cache["mtime"] = None
dlg._build_provider_radios()
selected = [r for r in provider_area_order()
            if isinstance(r, wx.RadioButton) and r.GetValue()]
check("removing the selected provider leaves exactly one radio set",
      len(selected) == 1)
check("...and it falls back to Ollama",
      selected and "Ollama" in selected[0].GetLabel())

dlg.Destroy()

if _fails:
    print("\nFAILED %d: %s" % (len(_fails), ", ".join(_fails)))
    sys.exit(1)
print("\ntest_provider_radio_order: all checks passed")
sys.exit(0)
