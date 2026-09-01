# SPDX-License-Identifier: CC0-1.0
"""Guard test: the Discord per-person dialog builds, and every part of it can
be reached by tabbing.

This screen is new, and it replaced a plain text box. The text box was worse
in two ways that both mattered more than they looked: it dropped the "*" that
means "anyone", so the open-to-a-server setting had no way in from the UI at
all; and it had nowhere to put per-person tool access, so every Discord user
sat on the default bucket forever while the tab's own notes said the kin used
its normal tools there.

What replaced it is only better if you can actually read it. So the checks
here are about reachability, not appearance: a control that exists but cannot
take focus is a control that does not exist, and an explainer written as
StaticText is an explainer written to nobody.

The radio group is the specific thing worth pinning. Radios name themselves
from their own labels and ignore a preceding StaticText, so the QUESTION they
answer has to be in the tab order in its own right — otherwise you arrow
through four options without ever hearing what they are options for.

Run it safely (windows on an isolated desktop, no path to your foreground):
    python tests/_gui_runner.py tests/test_discord_user_dialog.py
"""

import os
import sys
import tempfile

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="dcuser-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


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
from dialogs.discord_user import _DiscordUserDialog        # noqa: E402
from tools._buckets import BUCKET_ORDER                    # noqa: E402

dlg = _DiscordUserDialog(None, user_id="", bucket="none")

focusable = [c for c in dlg.GetChildren() if c.CanAcceptFocusFromKeyboard()]
names = [(c.GetName() or "") for c in focusable]
labels = [(c.GetLabel() or "") for c in focusable]
joined = " | ".join(names + labels)

check("the dialog builds at all", dlg is not None)
check("everything on it can be reached by tabbing — no orphaned controls",
      len(focusable) >= 4 + len(BUCKET_ORDER))

check("the ID field is there", any("User ID" in x for x in labels + names)
      or any(isinstance(c, wx.TextCtrl) for c in focusable))
check("the question the radios answer is itself a tab stop",
      any("Tool access for this user" in n for n in names))
check("...so it isn't a StaticText spoken to nobody",
      not any(isinstance(c, wx.StaticText) and "Tool access" in c.GetLabel()
              for c in dlg.GetChildren()))

radios = [c for c in focusable if isinstance(c, wx.RadioButton)]
check("every tool-access level is offered as its own focusable radio",
      len(radios) == len(BUCKET_ORDER))
check("...each carrying a keyboard mnemonic",
      all("&" in r.GetLabel() for r in radios))
check("...and starting on the safe one", radios[0].GetValue() is True)

check("the star is explained where the ID is asked for, not somewhere else",
      any("*" in (c.GetValue() or "") for c in focusable
          if isinstance(c, wx.TextCtrl) and c.IsEditable() is False))
check("how approval works on this surface is readable from the keyboard",
      any("approval works on Discord" in n for n in names))

# The two-way trip: what you pick is what the caller gets back.
for name in BUCKET_ORDER:
    d2 = _DiscordUserDialog(None, user_id="12345", bucket=name)
    uid, bucket = d2.get_values()
    check(f"a saved '{name}' entry comes back as '{name}'",
          (uid, bucket) == ("12345", name))
    d2.Destroy()

d3 = _DiscordUserDialog(None, user_id="*", bucket="read")
check("an existing 'anyone' entry survives a round trip through the editor",
      d3.get_values() == ("*", "read"))
d3.Destroy()

dlg.Destroy()

print()
if _fails:
    print(f"test_discord_user_dialog: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_discord_user_dialog: all checks passed")
