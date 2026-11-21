# SPDX-License-Identifier: CC0-1.0
"""The two sharing switches in the Telegram group window and the new Discord
channel window (20 Nov 2026).

Pinned here:
  * A group kept with the desktop before the switches existed opens with
    "your private chats include this group" ticked and "this group gets your
    private chats" not: what the old box promised, and nothing more.
  * A new group opens with both off.
  * Either switch on keeps the group with the desktop; the old setting is
    never switched back off by the window (it would strand history).
  * The Discord channel window gives back its ID as digits only, its name,
    and both switches.
  * Each switch says in its own label what it does, so a screen reader
    reading only the label hears the whole of it.

Builds real windows, so the runner puts it on a separate desktop.

Run: python tests/test_sharing_switch_windows.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="sharewin-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if os.environ.get("HEARTHKIN_GUI_TESTS", "").strip() not in ("1", "true", "yes"):
    print("SKIP -- builds real widgets; run on an isolated desktop with:")
    print("    python tests/_gui_runner.py " + __file__)
    sys.exit(0)

import wx  # noqa: E402

from dialogs.telegram_group import _TelegramGroupDialog  # noqa: E402
from dialogs.discord_channel import _DiscordChannelDialog  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


app = wx.App(False)
frame = wx.Frame(None)

d = _TelegramGroupDialog(frame, chat_id="-100", label="Code club",
                         share_desktop=True)
check("an old shared group opens with 'your private chats include it' on",
      d.private_sees_check.GetValue() is True)
check("...and 'it gets your private chats' off",
      d.sees_private_check.GetValue() is False)
check("...the labels say what each does",
      "marked with this group's name" in d.private_sees_check.GetLabel()
      and "private talk in front of it" in d.sees_private_check.GetLabel())
d.private_sees_check.SetValue(False)
cid, label, policy, share, exclude = d.get_values()
check("unticking both never switches the old storage off",
      share is True and d.get_sharing() == (False, False))
d.Destroy()

d = _TelegramGroupDialog(frame, chat_id="-200")
check("a new group opens with both switches off",
      d.get_sharing() == (False, False) and d.get_values()[3] is False)
d.sees_private_check.SetValue(True)
check("either switch on keeps the group with the desktop",
      d.get_values()[3] is True and d.get_sharing() == (False, True))
d.Destroy()

d = _DiscordChannelDialog(frame)
check("a new Discord channel starts with both switches off, as a new "
      "Telegram group does",
      d.private_sees_check.GetValue() is False
      and d.sees_private_check.GetValue() is False)
d.id_field.SetValue(" 12 34x5 ")
d.name_field.SetValue(" general ")
d.private_sees_check.SetValue(True)
d.sees_private_check.SetValue(True)
check("the Discord channel window gives back digits, name and both switches",
      d.get_values() == ("12345", "general", True, True))
d.Destroy()

frame.Destroy()
print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_sharing_switch_windows: all checks passed")
