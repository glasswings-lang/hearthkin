# SPDX-License-Identifier: CC0-1.0
"""The Discord-person and Telegram-user windows' tool-call limit field.

Builds the real windows (isolated desktop only): the field is there, a tab
stop, followed by its explainer, starts blank for "use the kin's setting" or
at the person's number, reads back what's typed, and its shortcut letter
(L) is used by nothing else in either window.
"""

import os
import re
import sys
import tempfile

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="capwin-"))
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
from dialogs.discord_user import _DiscordUserDialog  # noqa: E402
from dialogs.telegram_user import _TelegramUserDialog  # noqa: E402


def letters_l(dlg):
    out = []
    for c in dlg.GetChildren():
        lab = c.GetLabel() if hasattr(c, "GetLabel") else ""
        m = re.search(r"&(\w)", lab or "")
        if m and m.group(1).lower() == "l":
            out.append(lab)
    return out


def main():
    for name, make in (
            ("Discord", lambda cap: _DiscordUserDialog(None, user_id="5", tool_cap=cap)),
            ("Telegram", lambda cap: _TelegramUserDialog(None, user_id="5", tool_cap=cap))):
        d = make(None)
        f = getattr(d, "cap_field", None)
        check(f"{name}: the limit field is there", f is not None)
        check(f"{name}: it starts blank (use the kin's setting)",
              f is not None and f.GetValue() == "" and d.get_tool_cap() is None)
        stops = [c for c in d.GetChildren() if c.CanAcceptFocusFromKeyboard()]
        names = [c.GetName() for c in stops]
        check(f"{name}: it's a tab stop, with its explainer right after",
              f in stops and "What the tool call limit does" in names
              and names.index("What the tool call limit does") == stops.index(f) + 1)
        check(f"{name}: only it uses Alt+L", len(letters_l(d)) == 1)
        f.SetValue("25")
        check(f"{name}: a typed number reads back", d.get_tool_cap() == 25 and d.tool_cap_is_valid())
        f.SetValue("lots")
        check(f"{name}: a non-number is flagged", not d.tool_cap_is_valid())
        d.Destroy()
        d = make(40)
        check(f"{name}: an existing person's number is shown", d.cap_field.GetValue() == "40")
        d.Destroy()
        # The commands choice (12 Nov 2026): there, a tab stop with its
        # explainer next, Alt+C used once, starts on the kin's setting.
        d = make(None)
        ch = getattr(d, "exec_choice", None)
        stops = [c for c in d.GetChildren() if c.CanAcceptFocusFromKeyboard()]
        names = [c.GetName() for c in stops]
        check(f"{name}: the commands choice is there, starting on the kin's setting",
              ch is not None and d.get_exec_approval() == "default")
        check(f"{name}: it's a tab stop, with its explainer right after",
              ch in stops and "What the commands setting does" in names
              and names.index("What the commands setting does") == stops.index(ch) + 1)
        cs = [c.GetLabel() for c in d.GetChildren()
              if re.search(r"&[cC]", (c.GetLabel() if hasattr(c, "GetLabel") else "") or "")]
        check(f"{name}: only it uses Alt+C", len(cs) == 1)
        ch.SetSelection(1)
        check(f"{name}: 'Ask me first' reads back as ask", d.get_exec_approval() == "ask")
        d.Destroy()

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
