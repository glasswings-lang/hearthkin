# SPDX-License-Identifier: CC0-1.0
"""Guard test: the dictation settings screen builds, and can be read.

Dictation is the one screen in this app that someone might be setting up
precisely BECAUSE typing is hard for them. A control on it that exists
but cannot take focus is a control that does not exist, and an
explanation written as StaticText is an explanation written to nobody.

Three things are pinned beyond "it builds":

Every explanatory paragraph is a read-only TextCtrl, not StaticText, so
it sits in the tab order and can actually be found. This screen is where
the only account of what each choice costs lives — "free, on this
computer" versus "paid, needs an API key" — along with the fact that a
graphics card is a speed-up and not a requirement. None of that is a
detail to leave somewhere unreachable.

Switching where it runs HIDES the fields that no longer apply rather
than greying them out. A disabled control stays in the tab order, so
greying it out leaves something to tab into that explains nothing about
why it does not work.

What the screen collects is a (model, host) pair, and `stt.route_for`
reads it — the same function the engine uses. The round-trip checks at
the end exist because a screen that could disagree with the engine about
where somebody's audio goes is worse than one that cannot express the
choice at all.

Run it safely (windows on an isolated desktop, no path to your foreground):
    python tests/_gui_runner.py tests/test_dictation_dialog.py
"""

import os
import sys
import tempfile

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="dictation-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_fails = []


def check(label, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + label + (f" -- {detail}" if not cond and detail else ""))
    if not cond:
        _fails.append(label)


# OPT-IN, like every widget-building test here. Constructing a top-level wx
# window takes the FOREGROUND on Windows even when it is never shown, and a
# screen reader follows focus rather than visibility. run_all.py runs this
# file itself, on an isolated desktop; this gate makes running it directly a
# deliberate act.
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

from dialogs.dictation_settings import DictationSettingsDialog  # noqa: E402
from kin_persistence import DEFAULT_CONFIG                       # noqa: E402
import stt                                                       # noqa: E402

saved = {}
config = {"dictation": dict(DEFAULT_CONFIG["dictation"])}
parent = wx.Frame(None)


def _save():
    saved["called"] = True


dlg = DictationSettingsDialog(parent, config, _save)

# --- it builds, and it opens on the free option ----------------------

check("the dialog builds", dlg is not None)
check("it opens on the free, on-this-computer route",
      dlg._current_where() == stt.ROUTE_LOCAL, dlg._current_where())
check("and says so in one readable line",
      "this computer" in dlg.current_field.GetValue(),
      dlg.current_field.GetValue())

# --- every explanation is reachable by tabbing -----------------------

texts = [w for w in dlg.GetChildren()[0].GetChildren()
         if isinstance(w, wx.TextCtrl)]
readonly = [w for w in texts if not w.IsEditable()]
check("the explanatory paragraphs are read-only TextCtrls, so they are in "
      "the tab order", len(readonly) >= 5, f"found {len(readonly)}")
check("every read-only paragraph has an accessible name",
      all((w.GetName() or "").strip() not in ("", "text") for w in readonly),
      [w.GetName() for w in readonly])

# What each choice costs, and that a graphics card is not required, are
# the two things somebody needs from this screen and cannot get anywhere
# else.
blurb_text = " ".join(w.GetValue() for w in readonly)
check("the screen says dictation runs on this computer for free",
      "free" in blurb_text and "this computer" in blurb_text)
check("...and that a graphics card is not required",
      "not needed" in blurb_text and "processor" in blurb_text,
      blurb_text[:200])

# --- the backend picker names what each option costs ------------------

where_labels = [dlg.where_choice.GetString(i)
                for i in range(dlg.where_choice.GetCount())]
check("the local option says it is free and needs no account",
      any("free" in s and "no account" in s for s in where_labels),
      where_labels)
check("naming another machine is offered as an ordinary choice",
      any("another machine" in s for s in where_labels), where_labels)
check("the paid option says it is paid",
      any("paid" in s for s in where_labels), where_labels)

# --- switching backend hides, rather than greys out -------------------

check("with the local route, the model picker is shown",
      dlg.model_choice.IsShown())
check("...and the machine address is not",
      not dlg.host_field.IsShown())

_mod = __import__("dialogs.dictation_settings", fromlist=["x"])
server_i = [k for k, _l in _mod._WHERE_CHOICES].index(stt.ROUTE_SERVER)
dlg.where_choice.SetSelection(server_i)
dlg._sync_sections()

check("switching to another machine shows the address field",
      dlg.host_field.IsShown())
check("...and offers to ask that machine what models it has",
      dlg.ask_btn.IsShown())
check("...and hides the local model picker entirely, rather than greying it "
      "out into the tab order", not dlg.model_choice.IsShown())
check("...and disables it too, so nothing can land on it",
      not dlg.model_choice.IsEnabled())

# --- the model picker says which models are already here --------------

model_labels = [dlg.model_choice.GetString(i)
                for i in range(dlg.model_choice.GetCount())]
check("the model picker says what each choice would cost to download",
      any("would download" in s for s in model_labels)
      or all("already downloaded" in s for s in model_labels),
      model_labels[:3])

# --- what it collects round-trips ------------------------------------

dlg.where_choice.SetSelection(0)
dlg._sync_sections()
dlg.host_field.SetValue("http://example:8080")
collected = dlg._collect()
check("collect returns a local model with no machine named",
      collected["model"] and not collected["host"], collected)
check("...which routes to this computer",
      stt.route_for(collected["model"], collected["host"]) == stt.ROUTE_LOCAL)
check("collect carries every key the defaults define, so saving cannot "
      "drop one", set(DEFAULT_CONFIG["dictation"]) <= set(collected),
      sorted(set(DEFAULT_CONFIG["dictation"]) - set(collected)))
# An address typed while the local route is chosen must NOT quietly
# become the destination — route_for reads the pair, and a local choice
# means no machine.
check("an address typed while running locally does not redirect the audio",
      collected["host"] == "", collected["host"])

# And the round trip the other way: choose a machine, and that is where
# it goes, with nothing needed on this computer.
dlg.where_choice.SetSelection(server_i)
dlg._sync_sections()
dlg.host_field.SetValue("http://box:8080")
dlg.server_model_field.SetValue("whisper-large-v3")
remote = dlg._collect()
check("choosing a machine sends it there",
      stt.route_for(remote["model"], remote["host"]) == stt.ROUTE_SERVER,
      remote)
check("...with the model name that machine knows",
      remote["model"] == "whisper-large-v3", remote["model"])
check("...and the summary line names the machine",
      "box:8080" in stt.describe(remote["model"], remote["host"]),
      stt.describe(remote["model"], remote["host"]))

dlg.Destroy()
parent.Destroy()

print()
if _fails:
    print(f"FAILED ({len(_fails)}): " + "; ".join(_fails))
    sys.exit(1)
print("test_dictation_dialog: all checks passed")
sys.exit(0)
