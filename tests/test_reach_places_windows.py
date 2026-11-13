# SPDX-License-Identifier: CC0-1.0
"""The two windows that choose where a kin's words go list Discord too.

Builds the real heartbeat settings window and the real wake-up window for a
kin with a Discord person and channel, and checks both list them, and that
ticking one saves the place a send needs. Runs only on the isolated desktop.

Run: python tests/_gui_runner.py tests/test_reach_places_windows.py
"""

import os
import sys
import tempfile

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="reachwin-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


if os.environ.get("HEARTHKIN_GUI_TESTS", "").strip() not in ("1", "true", "yes"):
    print("SKIP -- builds real widgets; run on an isolated desktop with:")
    print("    python tests/_gui_runner.py " + __file__)
    sys.exit(0)

try:
    import wx
except Exception as e:                                    # pragma: no cover
    print(f"SKIP wxPython unavailable ({e})")
    sys.exit(0)

app = wx.App()
from kin_persistence import DEFAULT_AGENT_CONFIG, save_agent_config  # noqa: E402
from dialogs.cron_entry import CronEntryDialog  # noqa: E402
from dialogs.heartbeat_settings import HeartbeatSettingsDialog  # noqa: E402

cfg = dict(DEFAULT_AGENT_CONFIG)
cfg["discord"] = {"enabled": True, "bot_token": "D", "allow_from": ["333"],
                  "user_labels": {"333": "Ash"}, "channels": ["444"]}
save_agent_config("Wren", cfg)

hb = HeartbeatSettingsDialog(None, "Wren", {})
items = [hb.dest_choice.GetString(i) for i in range(hb.dest_choice.GetCount())]
check("the heartbeat's default place can be a Discord person",
      "Discord DM: Ash (333)" in items)
check("...or a Discord channel", "Discord channel: 444" in items)
i = items.index("Discord DM: Ash (333)")
hb.dest_choice.SetSelection(i)
hb.allow_list.Check(i - 1, True)       # the tick list has no Desktop row
got = hb.get_heartbeat()
check("choosing them saves where a send needs to go",
      got["destination"] == {"surface": "discord_dm", "id": "333"}
      and {"label": "Ash", "surface": "discord_dm", "id": "333"}
      in got["allowed_destinations"])
hb.Destroy()

ce = CronEntryDialog(None, "Wren", {"times": ["08:00"], "prompt": "Hello."})
rows = [r[0] for r in ce._dest_rows]
check("a wake-up can be sent to a Discord person and channel",
      "discord_dm" in rows and "discord_channel" in rows)
ce.dest_list.Check(rows.index("discord_channel"), True)
check("...and ticking one saves it",
      {"surface": "discord_channel", "id": "444", "label": "444"}
      in ce.get_entry().get("destinations", []))
ce.Destroy()

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_reach_places_windows: all checks passed")
