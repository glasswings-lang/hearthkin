# SPDX-License-Identifier: CC0-1.0
"""Telegram's /new says where the old conversation really went (24 Nov 2026).

For a chat shared with the desktop, /new saved the old conversation in the
Hearthkin-wide conversations folder, but told the person it was in "the
kin's conversations folder". They looked in the kin's folder, found nothing,
and thought it was gone. And if the copy couldn't be written, /new emptied
the conversation anyway. Then, the same day, every copy was standardised:
one shape (one message per line) in one place (the kin's backups/cleared).
Pinned:

  * the file the message names exists, in the folder the message names,
    which is the kin's backups/cleared, in the standard shape;
  * a shared /new makes ONE copy, not a second one in the conversations
    folder;
  * if the copy can't be written, nothing is emptied, and the message says so;
  * the same for a chat that isn't shared.

Run: python tests/test_new_says_where.py
"""

import json
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
cleared = kp.cleared_copies_dir(KIN)


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
    m = re.search(r"(\S+\.jsonl)", text)
    return m.group(1).rstrip(",.") if m else ""


def lines(p):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


# Shared with the desktop.
kp.save_agent_conversation(KIN, talk(40))
b = bot(True)
b._cmd_new(1, 1)
msg = b.sent[-1] if b.sent else ""
name = named_file(msg)
check("the message names a file", bool(name))
check("...and that file is in the kin's backups/cleared, every message of it",
      bool(name) and (cleared / name).exists() and len(lines(cleared / name)) == 40)
check("...which is the folder the message names",
      "backups, in the cleared folder" in msg and "Restore a kin's history" in msg)
check("...and no second copy went to the conversations folder",
      not (kp.CONVOS_DIR.exists() and list(kp.CONVOS_DIR.glob(f"{KIN}_*"))))
check("the conversation is empty now", kp.load_agent_conversation(KIN) == [])

# Can't keep a copy: nothing emptied.
kp.save_agent_conversation(KIN, talk(30))
real = kp.shutil.copy2
kp.shutil.copy2 = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
try:
    b = bot(True)
    b._cmd_new(1, 1)
finally:
    kp.shutil.copy2 = real
msg = b.sent[-1] if b.sent else ""
check("if the old one can't be kept, nothing is emptied",
      len(kp.load_agent_conversation(KIN)) == 30)
check("...and the message says so", "nothing was emptied" in msg)

# Not shared: its own slice, copied the standard way.
b = bot(False)
b._histories[tb._hkey(7)] = talk(10)
b._cmd_new(7, 7)
msg = b.sent[-1] if b.sent else ""
copies = sorted(cleared.glob("telegram-7-*.jsonl"))
check("an unshared /new keeps a standard copy named for the chat",
      len(copies) == 1 and len(lines(copies[0])) == 10)
check("...and the message names it and its folder",
      copies and copies[0].name in msg and "cleared folder" in msg)
check("...and empties only that chat", tb._hkey(7) not in b._histories)

b = bot(False)
b._histories[tb._hkey(8)] = talk(10)
b._archive_telegram_history = lambda *a: (_ for _ in ()).throw(OSError("disk full"))
b._cmd_new(8, 8)
check("an unshared /new that can't keep a copy keeps the chat",
      len(b._histories.get(tb._hkey(8)) or []) == 10
      and "nothing was emptied" in (b.sent[-1] if b.sent else ""))

print()
if _fails:
    print(f"test_new_says_where: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_new_says_where: all checks passed")
