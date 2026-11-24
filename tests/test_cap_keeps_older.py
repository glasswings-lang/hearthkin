# SPDX-License-Identifier: CC0-1.0
"""What a history cap takes off the front is kept, not lost (24 Nov 2026).

Telegram's and Discord's own histories (chats not shared with the desktop)
are capped, and when one overflowed its oldest messages were simply gone:
the one place left where Hearthkin dropped conversation with no copy. Now
the rows the cap takes are appended to <kin>/telegram_archive/<key>-older.jsonl
or <kin>/discord_archive/<channel>-older.jsonl first, and if that can't be
written, nothing is trimmed. Pinned:

  * trim_front hands the dropped rows to keep(), every one of them;
  * if keep() fails, nothing is trimmed;
  * a Telegram private chat over its cap keeps the older rows in its archive;
  * a Discord channel over its cap does too.

Run: python tests/test_cap_keeps_older.py
"""

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="capolder-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import history_rows  # noqa: E402
import kin_persistence as kp  # noqa: E402
import telegram_bot as tb  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def talk(n, tag="m"):
    return [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"{tag} {i}"} for i in range(n)]


got = []
h = talk(50)
kept = history_rows.trim_front(h, 40, keep=lambda rows: got.extend(rows))
check("the dropped rows go to keep()", got and len(got) + len(kept) == 50
      and got[0]["content"] == "m 0")


def broken(rows):
    raise OSError("disk full")


check("if keep() fails, nothing is trimmed",
      len(history_rows.trim_front(talk(50), 40, keep=broken)) == 50)

KIN = "Sorrel"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)
b = tb.TelegramBot.__new__(tb.TelegramBot)
b.agent_name = KIN
b._user_shares_desktop = lambda uid: False
b._histories = {}
b._histories_lock = threading.RLock()
b._save_histories = lambda: None
b._history_cap = lambda: 40
b.on_activity = None
b._append_turns_for(9, talk(50, "tg"))
older = kp.cleared_copies_dir(KIN) / f"telegram-{tb._hkey(9)}-older.jsonl"
rows = [json.loads(x) for x in older.read_text(encoding="utf-8").splitlines()] \
    if older.exists() else []
check("a Telegram private chat over its cap keeps the older rows",
      rows and rows[0]["content"] == "tg 0"
      and len(rows) + len(b._histories[tb._hkey(9)]) >= 50)

for t in talk(50, "dc"):
    kp.append_discord_turn(KIN, 77, t, cap=40)
dolder = kp.cleared_copies_dir(KIN) / "discord-77-older.jsonl"
drows = [json.loads(x) for x in dolder.read_text(encoding="utf-8").splitlines()] \
    if dolder.exists() else []
stored = kp.load_discord_history(KIN).get("77") or []
check("a Discord channel over its cap keeps the older rows",
      drows and drows[0]["content"] == "dc 0" and len(drows) + len(stored) == 50)

print()
if _fails:
    print(f"test_cap_keeps_older: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_cap_keeps_older: all checks passed")
