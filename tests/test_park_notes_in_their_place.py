# SPDX-License-Identifier: CC0-1.0
"""A park move made in a Telegram group is noted in that group's history
(20 Nov 2026 audit). It was noted in the person's DM history, so the kin in
the group never saw what its move did.

Run: python tests/test_park_notes_in_their_place.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="parknotes-", dir=(os.environ.get("HEARTHKIN_HOME") or None))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import telegram_bot as _tb  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


b = _tb.TelegramBot.__new__(_tb.TelegramBot)
got = []
b._append_group_history = lambda cid, turns: got.append(("group", cid))
b._append_turns_for = lambda uid, turns: got.append(("dm", uid))
b._append_park_note(-5, 7, [{"role": "system", "content": "moved"}])
b._append_park_note(7, 7, [{"role": "system", "content": "moved"}])
check("a move in a group is noted in the group", got[0] == ("group", -5))
check("a move in a DM is noted in the DM", got[1] == ("dm", 7))
src = (ROOT / "telegram_bot.py").read_text(encoding="utf-8")
_start = src.index("    def _route_park_emotes")
_one = src.index("    def _route_one_park_move")
seg = src[_start:src.index(chr(10) + "    def ", _one + 10)]
check("every park note on the way goes through it",
      "self._append_turns_for(" not in seg and seg.count("_append_park_note(chat_id") >= 3)
check("the 'no moves left' notice reaches the kin's history, not just the chat",
      seg.count("_append_park_note(chat_id") == 4
      and "_spent = load_app_prompt" in seg)
dsrc = (ROOT / "discord_bot.py").read_text(encoding="utf-8")
check("...on Discord too",
      'self._persist_turn(channel_id, "system", _spent, share)' in dsrc)

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_park_notes_in_their_place: all checks passed")
