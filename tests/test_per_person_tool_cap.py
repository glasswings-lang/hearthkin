# SPDX-License-Identifier: CC0-1.0
"""Standalone tests for the per-person tool-call limit (no network, no
windows). Discord and Telegram both keep per-person settings; the limit on
tool calls in one reply is one of them now, so an operator can give one
person room and keep a stranger tight without touching code.
"""

import os
import sys
import tempfile

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="hk-percap-"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools._buckets import resolve_tool_call_cap as cap, parse_tool_call_cap as parse  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def main():
    check("nobody set: a stranger gets the kin's setting", cap({}, 5, 15) == 15)
    check("nobody set: the operator is unlimited", cap({}, 5, 15, is_operator=True) == 0)
    check("a person's own number wins", cap({"5": 40}, 5, 15) == 40)
    check("...even over the operator's no-limit", cap({"5": 4}, 5, 15, is_operator=True) == 4)
    check("0 set for a person means no limit for them", cap({"5": 0}, 5, 15) == 0)
    check("a '*' entry applies to anyone without their own",
          cap({"*": 6}, 9, 15) == 6 and cap({"*": 6, "9": 30}, 9, 15) == 30)
    check("ids match whether stored as text or number", cap({"5": 8}, "5", 15) == 8)
    check("a broken stored value falls back to the kin's setting",
          cap({"5": "lots"}, 5, 15) == 15)
    check("a broken kin setting falls back to 15", cap({}, 5, "lots") == 15)

    check("blank field means 'use the kin's setting'", parse("") is None and parse("  ") is None)
    check("a number is the limit", parse("25") == 25 and parse(" 0 ") == 0)
    check("anything else isn't a number", parse("ten") is None and parse("-3") is None)

    dsrc = open(os.path.join(ROOT, "discord_bot.py"), encoding="utf-8").read()
    check("Discord works out each person's limit from their settings",
          "resolve_tool_call_cap(" in dsrc and '"user_tool_caps"' in dsrc)
    tsrc = open(os.path.join(ROOT, "telegram_bot.py"), encoding="utf-8").read()
    check("Telegram does too", "resolve_tool_call_cap(" in tsrc and '"user_tool_caps"' in tsrc)
    esrc = open(os.path.join(ROOT, "dialogs", "edit_kin.py"), encoding="utf-8").read()
    check("the settings window saves it for both surfaces",
          esrc.count('"user_tool_caps"') >= 6)

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
