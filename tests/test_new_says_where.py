# SPDX-License-Identifier: CC0-1.0
"""Telegram's /new says where the old conversation really went (24 Nov 2026).

For a chat shared with the desktop, /new saved the old conversation in the
Hearthkin-wide conversations folder, but told the person it was in "the
kin's conversations folder". They looked in the kin's folder, found nothing,
and thought it was gone. And if the copy couldn't be written, /new emptied
the conversation anyway. Pinned:

  * the file the message names exists, in the folder the message names;
  * the message no longer sends anyone to the kin's own folder for it;
  * if the copy can't be written, nothing is emptied, and the message says so;
  * the same for a chat that isn't shared (its own telegram_archive folder).

Run: python tests/test_new_says_where.py
"""

import os
import re
import sys
import tempfile
import threading
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="newwhere-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import kin_persistence as kp  # noqa: E402
import telegram_bot as tb  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Sorrel"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)


def talk(n):
    return [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"line {i}"} for i in range(n)]


def bot(shares):
    b = tb.TelegramBot.__new__(tb.TelegramBot)
    b.agent_name = KIN
    b.sent = []
    b._send_chunked = lambda chat_id, text: b.sent.append(text)
    b._user_shares_desktop = lambda uid: shares
    b._last_thinking = {}
    b._histories = {}
    b._histories_lock = threading.RLock()
    b._save_histories = lambda: None
    return b


def named_file(text):
    m = re.search(r"(\S+\.json)", text)
    return m.group(1).rstrip(",.") if m else ""


# Shared with the desktop.
kp.save_agent_conversation(KIN, talk(40))
b = bot(True)
b._cmd_new(1, 1)
msg = b.sent[-1] if b.sent else ""
name = named_file(msg)
check("the message names a file", bool(name))
check("...and that file is in the Hearthkin conversations folder",
      bool(name) and (kp.CONVOS_DIR / name).exists())
check("...which is the folder the message names",
      "conversations folder inside your Hearthkin folder" in msg)
check("...not the kin's own folder", not (kin_dir(KIN) / "conversations" / name).exists()
      and "kin's conversations folder" not in msg)
check("the conversation is empty now", kp.load_agent_conversation(KIN) == [])

# Can't save it: nothing emptied.
kp.save_agent_conversation(KIN, talk(30))
real = kp.atomic_write_json
kp.atomic_write_json = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
try:
    b = bot(True)
    b._cmd_new(1, 1)
finally:
    kp.atomic_write_json = real
msg = b.sent[-1] if b.sent else ""
check("if the old one can't be saved, nothing is emptied",
      len(kp.load_agent_conversation(KIN)) == 30)
check("...and the message says so", "nothing was emptied" in msg)

# Not shared: its own slice, archived in the kin's telegram_archive.
b = bot(False)
b._histories[tb._hkey(7)] = talk(10)
b._cmd_new(7, 7)
msg = b.sent[-1] if b.sent else ""
name = named_file(msg)
check("an unshared /new names a file in the kin's telegram_archive folder",
      bool(name) and (kin_dir(KIN) / "telegram_archive" / name).exists()
      and "telegram_archive folder" in msg)
check("...and empties only that chat", tb._hkey(7) not in b._histories)

b = bot(False)
b._histories[tb._hkey(8)] = talk(10)
b._archive_telegram_history = lambda *a: (_ for _ in ()).throw(OSError("disk full"))
b._cmd_new(8, 8)
check("an unshared /new that can't save keeps the chat",
      len(b._histories.get(tb._hkey(8)) or []) == 10
      and "nothing was emptied" in (b.sent[-1] if b.sent else ""))

print()
if _fails:
    print(f"test_new_says_where: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_new_says_where: all checks passed")
