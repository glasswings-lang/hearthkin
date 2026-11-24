# SPDX-License-Identifier: CC0-1.0
"""Every tool a kin can be given is there to see and switch off, in the real
windows (23 Nov 2026).

The person: "Why am I not seeing message_kin anywhere in the UI itself? ...
I can't fix what I can't see." reach_out and message_kin reached kin with no
switch in any window, and the check written first only read the code. This one
opens the actual windows on the isolated desktop and looks:

  * the kin's Tools tab lists EVERY registered tool, reach_out and
    message_kin included, each with words saying what it does;
  * unticking message_kin there takes it away from heartbeats, wake-ups and
    chat with the person, and ticking it again brings it back;
  * the wake-up "Leave these tools out" list offers every tool a wake-up is
    actually given, and ticking reach_out there really leaves it out;
  * the check can fail: a kin with message_kin switched off isn't offered it.

Builds real widgets, so it runs only on an isolated desktop:
    python tests/_gui_runner.py tests/test_every_tool_in_the_windows.py
"""

import os
import sys
import tempfile

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="toolwindows-")
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

import hearthkin_cron as hc  # noqa: E402
import tools as kin_tools  # noqa: E402
from kin_persistence import (DEFAULT_AGENT_CONFIG, save_agent_config,  # noqa: E402
                             load_agent_config, save_kin_tools)
from hearthkin_paths import kin_dir  # noqa: E402
from dialogs.edit_kin import EditKinDialog  # noqa: E402
from dialogs.tool_settings import ToolSettingsDialog  # noqa: E402

KIN = "Thistle"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)
cfg = dict(DEFAULT_AGENT_CONFIG)
cfg["tool_trust"] = "full"
cfg["model"] = "test-model"
save_agent_config(KIN, cfg)
ALL = sorted(kin_tools.list_available())
PROACTIVE = set(kin_tools._PROACTIVE_TOOLS)
save_kin_tools(KIN, [t for t in ALL if t not in PROACTIVE])


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


def offered(**kw):
    """The tool names a kin is actually given, the way Hearthkin gives them."""
    s, _ = kin_tools.load_tools(ALL, context={"agent_name": KIN}, **kw)
    return {x.get("function", {}).get("name") for x in s}


# --- The Tools tab, as the person sees it ---
dlg = EditKinDialog(_Frame(None), KIN)
lst, names = dlg.tool_list, list(dlg._tool_names)
shown = [lst.GetString(i) for i in range(lst.GetCount())]
for t in ALL:
    check(f"the Tools tab shows {t}", any(row.startswith(t + " ") or row == t for row in shown))
for t in sorted(PROACTIVE):
    row = next((r for r in shown if r.startswith(t)), "")
    check(f"...and says in words what {t} is for", len(row) > len(t) + 20)

i = names.index("message_kin")
check("message_kin starts ticked, as it always worked", lst.IsChecked(i))
lst.CheckItem(i, False)
check("unticking message_kin saves at once",
      load_agent_config(KIN).get("proactive_tools_off") == ["message_kin"])
check("...and a heartbeat no longer offers it",
      "message_kin" not in offered(proactive_wake=True))
check("...nor a wake-up", "message_kin" not in offered(cron_turn=True))
check("...nor chat with the person", "message_kin" not in offered(person_present=True))
check("the check can fail: reach_out, still ticked, is still offered",
      "reach_out" in offered(proactive_wake=True))
lst.CheckItem(i, True)
check("ticking it again brings it back",
      not load_agent_config(KIN).get("proactive_tools_off")
      and "message_kin" in offered(proactive_wake=True))
dlg.Destroy()

# --- The wake-up "Leave these tools out" list ---
schemas, _ex, _n = hc._load_cron_tools(KIN)
wake_gets = sorted({x.get("function", {}).get("name") for x in schemas})
check("a wake-up is given reach_out and message_kin",
      "reach_out" in wake_gets and "message_kin" in wake_gets)


def saver(k, v):
    c = load_agent_config(KIN)
    c[k] = v
    save_agent_config(KIN, c)


tsd = ToolSettingsDialog(None, load_agent_config(KIN), saver, lambda k, v: None,
                         kin_name=KIN)
wl = tsd.wake_tools_off
wnames = [wl.GetString(i) for i in range(wl.GetCount())]
for t in wake_gets:
    check(f"the wake-up leave-out list offers {t}", t in wnames)
j = wnames.index("reach_out")
wl.CheckItem(j, True)
tsd._on_wake_tool_tick(None)
schemas, _ex, _n = hc._load_cron_tools(KIN)
after = {x.get("function", {}).get("name") for x in schemas}
check("ticking reach_out there really leaves it out of wake-ups", "reach_out" not in after)
check("...while a heartbeat still offers it (that list is for wake-ups only)",
      "reach_out" in offered(proactive_wake=True))
tsd.Destroy()

# --- "Show what reply blocks did", beside "Show tool calls" (23 Nov 2026) ---
from dialogs.telegram_settings import TelegramMessageSettingsDialog  # noqa: E402

tg_saved = {}
tgd = TelegramMessageSettingsDialog(
    None, load_agent_config(KIN), lambda k, v: None,
    lambda k, v: tg_saved.__setitem__(k, v), kin_name=KIN)
blk = getattr(tgd, "show_blocks_check", None)
check("Telegram's message settings have the reply-blocks switch", blk is not None)
if blk is not None:
    check("...starting ticked", blk.GetValue())
    check("...right after Show tool calls",
          blk.GetParent() is tgd.show_tool_calls_check.GetParent())
    blk.SetValue(False)
    blk.ProcessEvent(wx.CommandEvent(wx.wxEVT_CHECKBOX, blk.GetId()))
    check("...and unticking it saves", tg_saved.get("show_block_actions") is False)
tgd.Destroy()

dlg = EditKinDialog(_Frame(None), KIN)
dcb = getattr(dlg, "dc_blocks_check", None)
check("the Discord tab has the reply-blocks switch", dcb is not None)
if dcb is not None:
    check("...starting ticked", dcb.GetValue())
    dcb.SetValue(False)
    dcb.ProcessEvent(wx.CommandEvent(wx.wxEVT_CHECKBOX, dcb.GetId()))
    check("...and unticking it saves",
          (load_agent_config(KIN).get("discord") or {}).get("show_block_actions") is False)
dlg.Destroy()

# --- the main window's two switches, on the kin's Tools tab ---
dlg = EditKinDialog(_Frame(None), KIN)
for attr, key in (("desk_tools_check", "desktop_show_tool_calls"),
                  ("desk_blocks_check", "desktop_show_block_actions")):
    box = getattr(dlg, attr, None)
    check(f"the Tools tab has the main window's {key} switch", box is not None)
    if box is not None:
        check("...starting ticked", box.GetValue())
        box.SetValue(False)
        box.ProcessEvent(wx.CommandEvent(wx.wxEVT_CHECKBOX, box.GetId()))
        check("...and unticking it saves", load_agent_config(KIN).get(key) is False)
dlg.Destroy()

# --- a room's two switches, in the room window ---
from dialogs.room_edit import RoomEditDialog  # noqa: E402

rd = RoomEditDialog(None, title="Edit room", initial_name="Hearth",
                    initial_cfg={"members": [KIN]}, available_kin=[KIN])
for attr, key in (("show_tools_check", "show_tool_calls"),
                  ("show_blocks_check", "show_block_actions")):
    box = getattr(rd, attr, None)
    check(f"the room window has its {key} switch", box is not None)
    if box is not None:
        check("...starting ticked", box.GetValue())
        box.SetValue(False)
        check("...and what it's set to is what gets saved",
              rd.get_result().get(key) is False)
rd.Destroy()

print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("test_every_tool_in_the_windows: all checks passed")
