# SPDX-License-Identifier: CC0-1.0
"""The Tools tab lists the tools as one tick list, not a checkbox per tool.

One Tab stop; arrows move through the tools; Space ticks; NVDA says checked or
not checked (the ordinary Windows list with tick boxes, see TickList). Through
the real Settings window: the list shows every everyday tool, ticked exactly
where the kin's tools file says, and a person ticking or unticking saves the
tools file at once. Its label's Alt letter clashes with nothing on the tab.

Builds real widgets, so it runs only on an isolated desktop:
    python tests/_gui_runner.py tests/test_tools_tick_list.py
"""

import os
import sys
import tempfile

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="toolsticklist-")
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

import tools as kin_tools  # noqa: E402
from kin_persistence import load_kin_tools, save_kin_tools  # noqa: E402
from dialogs._shared import TickList  # noqa: E402

lst = getattr(dlg, "tool_list", None)
names = getattr(dlg, "_tool_names", [])
check("the Tools tab has one tick list of tools", isinstance(lst, TickList))
everyday = [t for t in kin_tools.list_available() if t not in kin_tools._PROACTIVE_TOOLS]
check("it lists every everyday tool, in order", names == everyday and lst.GetCount() == len(everyday))
check("each item starts with the tool's name",
      all(lst.GetString(i).startswith(n) for i, n in enumerate(names)))
check("no separate checkbox per tool is left",
      not any(isinstance(c, wx.CheckBox) and c.GetLabel().startswith("Enable ")
              for c in lst.GetParent().GetChildren()))
dlg.Destroy()

# Reopen with two tools already on: the list shows exactly those.
on = everyday[:2]
save_kin_tools(KIN, on)
dlg = EditKinDialog(_Frame(None), KIN)
lst, names = dlg.tool_list, dlg._tool_names
check("ticks match the tools file when the window opens",
      [names[i] for i in range(lst.GetCount()) if lst.IsChecked(i)] == on)

# A person ticks a third and unticks the first: saved at once.
lst.CheckItem(2, True)
lst.CheckItem(0, False)
check("ticking and unticking saves the tools file at once",
      sorted(load_kin_tools(KIN)) == sorted([everyday[1], everyday[2]]))

tools_page = None
for i in range(dlg.notebook.GetPageCount()):
    if dlg.notebook.GetPageText(i) == "Tools":
        tools_page = dlg.notebook.GetPage(i)
letters = {}


def walk(win):
    for child in win.GetChildren():
        label = child.GetLabel() if hasattr(child, "GetLabel") else ""
        if isinstance(child, (wx.Button, wx.CheckBox, wx.RadioButton, wx.StaticText)) and "&" in label:
            i = label.index("&")
            if i + 1 < len(label) and label[i + 1] != "&":
                letters.setdefault(label[i + 1].lower(), []).append(label)
        walk(child)


walk(tools_page)
check("the list's label has Alt+O, and nothing else on the tab does",
      len(letters.get("o", [])) == 1)
dlg.Destroy()
print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("test_tools_tick_list: all checks passed")
