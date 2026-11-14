# SPDX-License-Identifier: CC0-1.0
"""The listening model and the looking model are chosen in the kin's
Settings window, like any other model, not typed into a file.

Through the real Settings window, by pressing its buttons: the Tools tab shows
both fields (saying plainly when none is chosen, and that looking uses the
listening model until it has its own), each Change button opens the model
browser, what's picked is saved for that one setting and shown, and "Use the
listening model for looking" clears the looking model again. Also: no two
controls on the Tools tab claim the same Alt letter, since the new buttons
added letters and the tab's letter-picker used to leave the buttons' letters
out.

The only stand-in is the human picking in the browser: its ShowModal answers
OK with a model chosen.

Builds real widgets, so it runs only on an isolated desktop:
    python tests/_gui_runner.py tests/test_senses_model_setting.py
"""

import os
import sys
import tempfile

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="sensesmodel-")
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

from kin_persistence import (DEFAULT_AGENT_CONFIG, save_agent_config,  # noqa: E402
                             load_agent_config)
from hearthkin_paths import kin_dir  # noqa: E402
import model_browser  # noqa: E402
from dialogs.edit_kin import EditKinDialog  # noqa: E402

KIN = "Thistle"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)
save_agent_config(KIN, dict(DEFAULT_AGENT_CONFIG))

EAR = "openrouter/google/gemini-4-carbon"
EYE = "openrouter/google/gemini-3.8-flash"
CHOSEN = EAR
_opened = []


class _FakeBrowser:
    def __init__(self, parent, current_model="", ollama_host="",
                 show_machine_picker=True, **kw):
        _opened.append({"current": current_model,
                        "machine_picker": show_machine_picker})

    def ShowModal(self):
        return wx.ID_OK

    def get_selected_model(self):
        return CHOSEN

    def Destroy(self):
        pass


model_browser.ModelBrowserDialog = _FakeBrowser


class _Nothing(dict):
    def __call__(self, *a, **k):
        return None


class _Frame(wx.Frame):
    bots = {}
    discord_bots = {}
    config = {}
    current_agent = KIN
    _voice_engine = None
    _edit_kin_dialog = None

    def _is_distill_in_flight(self, *a, **k):
        return False

    def _convo_for_distill_scope(self, *a, **k):
        return []

    def _walk_scopes_on_disk(self, *a, **k):
        return []

    def _room_scopes_for_kin(self, *a, **k):
        return []

    def __getattr__(self, name):
        if name.startswith("_"):
            return _Nothing()
        raise AttributeError(name)


try:
    dlg = EditKinDialog(_Frame(None), KIN)
except Exception as e:
    check(f"the kin Settings window opens ({e!r})", False)
    print("%d FAILED" % len(_fails))
    sys.exit(1)
check("the kin Settings window opens", True)

def press(btn):
    btn.Command(wx.CommandEvent(wx.EVT_BUTTON.typeId, btn.GetId()))


ear = getattr(dlg, "listen_model_display", None)
eye = getattr(dlg, "look_model_display", None)
check("the Tools tab has a listening model field and a looking model field",
      ear is not None and eye is not None
      and ear.GetName() == "Listening model" and eye.GetName() == "Looking model")
check("...both saying plainly when none is chosen",
      ear.GetValue().startswith("(none chosen")
      and eye.GetValue().startswith("(none chosen"))

CHOSEN = EAR
press(dlg.listen_model_btn)
check("Change listening model opens the model browser", len(_opened) == 1)
check("...with no machine picker (the kin keeps its one machine)",
      _opened and _opened[0]["machine_picker"] is False)
saved = load_agent_config(KIN)
check("what's picked is saved as the listening model only",
      saved.get("listen_model") == EAR and not saved.get("look_model"))
check("...and shown in its field", ear.GetValue() == EAR)
check("the looking field says it's using the listening model",
      eye.GetValue() == f"(same as the listening model: {EAR})")

CHOSEN = EYE
press(dlg.look_model_btn)
saved = load_agent_config(KIN)
check("Change looking model saves a looking model of its own",
      saved.get("look_model") == EYE and saved.get("listen_model") == EAR)
check("...shown in its field", eye.GetValue() == EYE)

press(dlg.look_model_btn)
check("opening it again starts from the saved choice",
      _opened[-1]["current"] == EYE)

press(dlg.look_same_btn)
saved = load_agent_config(KIN)
check("'Use the listening model for looking' clears the looking model",
      saved.get("look_model") == "" and saved.get("listen_model") == EAR)
check("...and the field says so",
      eye.GetValue() == f"(same as the listening model: {EAR})")

# Every Alt letter on the Tools tab, buttons and checkboxes and radios.
tools_page = None
for i in range(dlg.notebook.GetPageCount()):
    if dlg.notebook.GetPageText(i) == "Tools":
        tools_page = dlg.notebook.GetPage(i)
letters = {}


def walk(win):
    for child in win.GetChildren():
        label = child.GetLabel() if hasattr(child, "GetLabel") else ""
        if isinstance(child, (wx.Button, wx.CheckBox, wx.RadioButton)) and "&" in label:
            i = label.index("&")
            if i + 1 < len(label) and label[i + 1] != "&":
                letters.setdefault(label[i + 1].lower(), []).append(label)
        walk(child)


if tools_page is not None:
    walk(tools_page)
dupes = {k: v for k, v in letters.items() if len(v) > 1}
check("no two controls on the Tools tab share an Alt letter "
      + (repr(dupes) if dupes else ""), tools_page is not None and not dupes)
check("the new buttons have their letters", {"h", "g", "s"} <= set(letters))

dlg.Destroy()
print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("test_senses_model_setting: all checks passed")
