# SPDX-License-Identifier: CC0-1.0
"""End to end through the real kin Settings window: add and edit a person on
Telegram and on Discord, and check what lands in the kin's settings file.

The person window and the shared settings list each have their own tests.
This one checks the wiring between them and the Settings window a person
actually uses: the Add / Edit / Remove buttons' handlers open the shared
window, what's set there is saved for the right surface, the list on the tab
shows the person, and editing loads back what was saved.

The only thing stood in for is the human: the person window's ShowModal is
replaced with a function that ticks and fills controls on the REAL window,
then answers Save, exactly as clicking would.

Builds real widgets, so it runs only on an isolated desktop:
    python tests/_gui_runner.py tests/test_person_settings_flow.py
"""

import os
import sys
import tempfile

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="personflow-")
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
import dialogs.person_window as pw  # noqa: E402
from dialogs.edit_kin import EditKinDialog  # noqa: E402

KIN = "Thistle"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)
save_agent_config(KIN, dict(DEFAULT_AGENT_CONFIG))

# The "human": what to do inside the person window when it opens.
_script = []
_seen_on_open = []


def fake_show_modal(self):
    _seen_on_open.append(self.get_settings())
    act = _script.pop(0)
    act(self)
    return wx.ID_OK


real_show = pw.PersonWindow.ShowModal
pw.PersonWindow.ShowModal = fake_show_modal

class _Frame(wx.Frame):
    """Stands in for the main window. Settings asks it a lot of questions
    (is a distillation running? which bots exist?); none bear on the person
    lists, so each answers "nothing going on"."""
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
        # Some of these are methods, some are bookkeeping dicts; an empty
        # dict that can also be called answers "nothing" either way.
        if name.startswith("_"):
            return _Nothing()
        raise AttributeError(name)


class _Nothing(dict):
    def __call__(self, *a, **k):
        return None


try:
    dlg = EditKinDialog(_Frame(None), KIN)
except Exception as e:
    check(f"the kin Settings window opens ({e!r})", False)
    print("%d FAILED" % len(_fails))
    sys.exit(1)
check("the kin Settings window opens", True)

# Messages boxes would block; answer yes and record them.
_boxes = []
import dialogs.edit_kin as ek  # noqa: E402
ek.wx.MessageBox = lambda *a, **k: (_boxes.append(a[0] if a else ""), wx.YES)[1]


def add_telegram(w):
    w.id_field.SetValue("4242")
    w.label_field.SetValue("Me")
    w.is_me_check.SetValue(True)
    w._bucket_radios["full"].SetValue(True)
    w.share_desktop_check.SetValue(False)
    w.cap_field.SetValue("0")
    w.exec_choice.SetSelection(1)          # Ask me first
    w.file_choice.SetSelection(2)          # Allow outside


_script.append(add_telegram)
dlg._on_tg_add_user(None)
tg = (load_agent_config(KIN).get("telegram") or {})
check("Telegram Add: the person is on the allow list",
      "4242" in [str(x) for x in tg.get("allow_from") or []])
check("...'This is me' saved", (tg.get("user_is_operator") or {}).get("4242") is True)
check("...name, tools, limit, commands, files saved",
      (tg.get("user_labels") or {}).get("4242") == "Me"
      and (tg.get("user_tools") or {}).get("4242") == "full"
      and (tg.get("user_tool_caps") or {}).get("4242") == 0
      and (tg.get("user_exec_approval") or {}).get("4242") == "ask"
      and (tg.get("user_file_reach") or {}).get("4242") == "outside")
check("...and the Telegram tab's list shows them",
      "4242" in getattr(dlg, "_tg_user_ids_in_order", []))

# Edit: the window opens with what was saved, and a change lands.
idx = dlg._tg_user_ids_in_order.index("4242")
dlg.tg_users_list.SetSelection(idx)


def edit_telegram(w):
    w.is_me_check.SetValue(False)
    w.exec_choice.SetSelection(0)          # back to the kin's setting


_script.append(edit_telegram)
dlg._on_tg_edit_user(None)
opened = _seen_on_open[-1]
check("Telegram Edit: the window opened showing what was saved",
      opened["is_me"] is True and opened["tool_cap"] == 0
      and opened["exec_approval"] == "ask" and opened["file_reach"] == "outside")
tg = (load_agent_config(KIN).get("telegram") or {})
check("...the change landed ('This is me' off)",
      not (tg.get("user_is_operator") or {}).get("4242"))
check("...and 'use the kin's setting' is stored as no entry",
      "4242" not in (tg.get("user_exec_approval") or {}))


def add_discord(w):
    w.id_field.SetValue("777")
    w.label_field.SetValue("Friend")
    w._bucket_radios["write"].SetValue(True)
    w._webcam_radios["deny"].SetValue(True)
    w.mirror_check.SetValue(True)
    w.share_desktop_check.SetValue(True)


_script.append(add_discord)
dlg._on_dc_add_user(None)
dc = (load_agent_config(KIN).get("discord") or {})
check("Discord Add: the person is on the allow list",
      "777" in [str(x) for x in dc.get("allow_from") or []])
check("...name, tools, webcam, mirror, sharing saved",
      (dc.get("user_labels") or {}).get("777") == "Friend"
      and (dc.get("user_tools") or {}).get("777") == "write"
      and (dc.get("user_webcam_permission") or {}).get("777") == "deny"
      and (dc.get("user_mirror_desktop") or {}).get("777") is True
      and (dc.get("user_share_desktop") or {}).get("777") is True)
check("...and not 'me'", not (dc.get("user_is_operator") or {}).get("777"))
check("...and the Discord tab's list shows them",
      "777" in getattr(dlg, "_dc_user_ids_in_order", []))
check("...and nothing landed in Telegram's settings by mistake",
      "777" not in [str(x) for x in (load_agent_config(KIN).get("telegram")
                                     or {}).get("allow_from") or []])

# Remove clears everything for that person.
dlg.dc_users_list.SetSelection(dlg._dc_user_ids_in_order.index("777"))
dlg._on_dc_remove_user(None)
dc = (load_agent_config(KIN).get("discord") or {})
check("Discord Remove: gone from the list and every setting",
      "777" not in [str(x) for x in dc.get("allow_from") or []]
      and all("777" not in (v or {}) for k, v in dc.items()
              if k.startswith("user_") and isinstance(v, dict)))

# A bad limit is refused and nothing is saved.
_script.append(lambda w: (w.id_field.SetValue("888"),
                          w.cap_field.SetValue("lots")))
before = len(_boxes)
dlg._on_dc_add_user(None)
dc = (load_agent_config(KIN).get("discord") or {})
check("a non-number limit is refused with a message, and nothing is saved",
      len(_boxes) > before
      and "888" not in [str(x) for x in dc.get("allow_from") or []])

# Where Discord listens: these two lists were file-only until 12 Nov 2026.
dlg.dc_guilds_field.SetValue("123, abc 456,123")
dlg.dc_channels_field.SetValue("")
dlg._on_dc_where_changed(None)
dc = (load_agent_config(KIN).get("discord") or {})
check("Discord servers box: IDs saved, junk and repeats dropped",
      dc.get("guilds") == ["123", "456"])
check("...and the box shows what was kept",
      dlg.dc_guilds_field.GetValue() == "123, 456")
check("Discord channels box: blank saves as 'anywhere'",
      dc.get("channels") == [])
dlg.dc_channels_field.SetValue("789")
dlg._on_dc_where_changed(None)
check("...and a channel ID saves",
      (load_agent_config(KIN).get("discord") or {}).get("channels") == ["789"])
_tab_stops = [c for c in dlg.dc_guilds_field.GetParent().GetChildren()
              if c.CanAcceptFocusFromKeyboard()]
check("both boxes, and their explainer, are tab stops",
      dlg.dc_guilds_field in _tab_stops and dlg.dc_channels_field in _tab_stops
      and any(c.GetName() == "How the server and channel lists work"
              for c in _tab_stops))

pw.PersonWindow.ShowModal = real_show
dlg.Destroy()

# Reopen: the saved lists show up again.
dlg2 = EditKinDialog(_Frame(None), KIN)
check("reopening Settings shows the saved servers and channels",
      dlg2.dc_guilds_field.GetValue() == "123, 456"
      and dlg2.dc_channels_field.GetValue() == "789")
dlg2.Destroy()

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_person_settings_flow: all checks passed")
