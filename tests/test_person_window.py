# SPDX-License-Identifier: CC0-1.0
"""The shared person window, built for real on Telegram and on Discord.

Checks the thing the shared window exists for: both surfaces offer the same
settings. Builds each window and compares what you can Tab to, by name. Also:
every shortcut letter is used once (the old Telegram window had Share and
Save both on Alt+S, and None and Cancel both on Alt+N), every setting's
control can take focus, and what you set reads back.

Builds real widgets, so it runs only on an isolated desktop:
    python tests/_gui_runner.py tests/test_person_window.py
"""

import os
import re
import sys
import tempfile

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="personwin-"))
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
from dialogs.person_window import PersonWindow  # noqa: E402
from tools._person_settings import SETTING_KEYS  # noqa: E402


def all_children(win):
    out = []
    for c in win.GetChildren():
        out.append(c)
        out.extend(all_children(c))
    return out


def stops(win):
    return [c for c in all_children(win)
            if c.CanAcceptFocusFromKeyboard() and c.IsShown()]


def kind_and_name(c):
    nm = c.GetName() or ""
    lab = c.GetLabel() if hasattr(c, "GetLabel") else ""
    return type(c).__name__, (nm if nm and not nm.startswith(
        ("text", "radioButton", "checkBox", "choice", "button")) else lab)


ID_HINTS = ("How to find",)
shapes = {}
for surface in ("telegram", "discord"):
    d = PersonWindow(None, surface, "", None)
    st = stops(d)
    shape = [kind_and_name(c) for c in st]
    # Wording that differs on purpose between the surfaces (the ID, sharing,
    # mirroring, commands) is compared by control type and position, not text.
    shapes[surface] = [t for t, _ in shape]

    letters = []
    for c in all_children(d):
        lab = c.GetLabel() if hasattr(c, "GetLabel") else ""
        m = re.search(r"&(\w)", lab or "")
        if m:
            letters.append(m.group(1).lower())
    dup = sorted({x for x in letters if letters.count(x) > 1})
    check(f"{surface}: every shortcut letter is used once (dupes: {dup})",
          not dup)

    for attr in ("id_field", "label_field", "share_desktop_check",
                 "mirror_check", "cap_field", "exec_choice", "file_choice",
                 "is_me_check"):
        ctrl = getattr(d, attr, None)
        check(f"{surface}: {attr} is a tab stop", ctrl in st)
    check(f"{surface}: every tool level and webcam choice is a tab stop",
          all(r in st for r in d._bucket_radios.values())
          and all(r in st for r in d._webcam_radios.values()))
    names = [c.GetName() for c in st]
    # Each explainer is the very next Tab stop after its control, so Tab
    # from a setting lands on what it does.
    for attr, hint in (("label_field", "What the display name does"),
                       ("is_me_check", "What This is me does"),
                       ("share_desktop_check", "What sharing with the desktop does"),
                       ("mirror_check", "What mirroring desktop messages does"),
                       ("cap_field", "What the tool call limit does"),
                       ("exec_choice", "What the commands setting does"),
                       ("file_choice", "What the files setting does")):
        ctrl = getattr(d, attr)
        i = st.index(ctrl) if ctrl in st else -1
        check(f"{surface}: Tab from {attr} lands on its explainer",
              0 <= i < len(st) - 1 and st[i + 1].GetName() == hint)
    check(f"{surface}: the ID's explainer comes right after the ID field",
          st[st.index(d.id_field) + 1].GetName().startswith("How to find"))
    for heading in ("Tool access for this user", "Webcam permission"):
        check(f"{surface}: the question '{heading}' is itself a tab stop",
              any(n.startswith(heading) for n in names))
    check(f"{surface}: a new person starts on the safe settings",
          d.get_settings() == {"label": "", "is_me": False, "bucket": "none",
                               "share_desktop": False, "mirror": False,
                               "webcam": "ask", "tool_cap": None,
                               "exec_approval": "default",
                               "file_reach": "default"})
    d.Destroy()

    full = {"label": "Alex", "is_me": True, "bucket": "full", "share_desktop": True,
            "mirror": True, "webcam": "deny", "tool_cap": 7,
            "exec_approval": "auto", "file_reach": "inside"}
    d = PersonWindow(None, surface, "12345", full)
    check(f"{surface}: an existing person's settings all show and read back",
          d.get_settings() == full and d.get_user_id() == "12345")
    d.cap_field.SetValue("lots")
    check(f"{surface}: a non-number limit is flagged", not d.cap_is_valid())
    d.Destroy()

check("Telegram and Discord windows offer the same controls, in the same order",
      shapes["telegram"] == shapes["discord"])

d = PersonWindow(None, "telegram", "tg: -100 200", None)
check("Telegram: a pasted 'tg:' prefix and spaces are cleaned off the ID",
      d.get_user_id() == "-100200")
d.Destroy()
d = PersonWindow(None, "discord", "*", None)
check("Discord: '*' (anyone) survives as typed", d.get_user_id() == "*")
d.Destroy()

# The old window classes still build and still answer the old way.
from dialogs.discord_user import _DiscordUserDialog  # noqa: E402
from dialogs.telegram_user import _TelegramUserDialog  # noqa: E402
d = _DiscordUserDialog(None, user_id="9", bucket="read")
check("the old Discord window name still works", d.get_values() == ("9", "read"))
d.Destroy()
d = _TelegramUserDialog(None, user_id="9", label="Sam", bucket="write",
                        share_desktop=True, webcam_permission="auto")
check("the old Telegram window name still works",
      d.get_values() == ("9", "Sam", "write", True, False, "auto"))
d.Destroy()
check("the list and the window agree on the settings",
      set(SETTING_KEYS) == set(PersonWindow(None, "discord").get_settings()))

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_person_window: all checks passed")
