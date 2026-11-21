# SPDX-License-Identifier: CC0-1.0
"""Small windows: no two controls share a shortcut letter, and the button
that saves says Save.

On 21 Nov 2026 the Telegram group window had Alt+C on both "Chat ID" and
"include this group", and Alt+M on both "Mention-only" and the mute list; the
Discord channel window had Alt+N on "Name" and "Cancel"; park settings had
three letters taken twice. Pressing the letter reached whichever control
Windows found first, so for a keyboard and NVDA user one of each pair was
unreachable that way. Saving buttons said OK in some windows and Save in
others for the same job.

Checked from source: every label in these windows that carries a shortcut
(an "&" before a letter; "&&" is a literal ampersand). Windows that show
different controls for different apps (the person window) aren't in this
list, since their alternates are never on screen together.

Run: python tests/test_dialog_mnemonics.py
"""
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


SMALL = ["telegram_group.py", "discord_channel.py", "park_settings.py",
         "heartbeat_settings.py", "room_edit.py", "edit_message.py",
         "usage_history.py"]
LABEL = re.compile(r'"((?:[^"\\]|\\.)*)"')


def letters(src):
    """Shortcut letters on label lines only (label=, wx.Button(...), the
    lines that continue a label), not in comments or docstrings."""
    lines = []
    keep = False
    for line in src.splitlines():
        st = line.strip()
        if st.startswith("#"):
            continue
        if "label=" in line or "wx.Button(" in line:
            keep = True
        elif not st.startswith('"'):
            keep = False
        if keep:
            lines.append(line)
    out = []
    for m in LABEL.finditer(chr(10).join(lines)):
        s = m.group(1).replace("&&", "")
        i = s.find("&")
        if i >= 0 and i + 1 < len(s) and s[i + 1].isalpha():
            out.append(s[i + 1].lower())
    return out


for name in SMALL:
    src = (ROOT / "dialogs" / name).read_text(encoding="utf-8")
    dup = sorted(k for k, n in Counter(letters(src)).items() if n > 1)
    check(f"{name}: every shortcut letter is used once"
          + (f" (twice: {', '.join(dup)})" if dup else ""), not dup)

for name in ("telegram_group.py", "discord_channel.py", "park_settings.py",
             "heartbeat_settings.py", "room_edit.py", "edit_message.py",
             "person_window.py"):
    src = (ROOT / "dialogs" / name).read_text(encoding="utf-8")
    check(f"{name}: the saving button says Save, not OK",
          '"&OK"' not in src and "Sa" in src and "ve\"" in src)

tg = (ROOT / "dialogs" / "telegram_group.py").read_text(encoding="utf-8")
dc = (ROOT / "dialogs" / "discord_channel.py").read_text(encoding="utf-8")
check("the Discord channel window words things as the Telegram group's does",
      "Display label" in tg and "Display label" in dc
      and "Add by &ID" in tg and "Add by &ID" in dc)

if _fails:
    print(f"{len(_fails)} FAILED: " + "; ".join(_fails))
    sys.exit(1)
print("test_dialog_mnemonics: all checks passed")
