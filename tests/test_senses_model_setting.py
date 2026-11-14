# SPDX-License-Identifier: CC0-1.0
"""The listening-and-looking model is chosen in the kin's Settings window,
like any other model, not typed into a file.

Through the real Settings window: the Tools tab shows the field (saying
plainly when none is chosen), the Change button opens the model browser, and
what's picked there is saved for the kin and shown in the field. Also: no two
controls on the Tools tab claim the same Alt letter, since the new button
added one and the tab's letter-picker used to leave the buttons' letters out.

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

CHOSEN = "openrouter/google/gemini-4-carbon"
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

field = getattr(dlg, "senses_model_display", None)
check("the Tools tab has the listening and looking model field",
      field is not None and field.GetName() == "Listening and looking model")
check("...which says plainly when none is chosen",
      field is not None and field.GetValue().startswith("(none chosen"))

dlg._on_change_senses_model(None)
check("Change opens the model browser", len(_opened) == 1)
check("...with no machine picker (the kin keeps its one machine)",
      _opened and _opened[0]["machine_picker"] is False)
check("what's picked is saved for the kin",
      load_agent_config(KIN).get("senses_model") == CHOSEN)
check("...and shown in the field", field.GetValue() == CHOSEN)

dlg._on_change_senses_model(None)
check("opening it again starts from the saved choice",
      len(_opened) == 2 and _opened[1]["current"] == CHOSEN)

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
check("the Change button has its letter", "h" in letters)

dlg.Destroy()
print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("test_senses_model_setting: all checks passed")
