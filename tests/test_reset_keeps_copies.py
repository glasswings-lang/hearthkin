# SPDX-License-Identifier: CC0-1.0
"""Telegram's /reset keeps copies and only says what happened (24 Nov 2026).

From the message audit: on a private chat /reset wiped the conversation with
no copy at all; memory.md was emptied with no copy; and on a shared chat a
failed wipe was ignored and "Reset complete ... is gone" was sent over an
untouched conversation. Pinned:

  * a private /reset archives the conversation first, and says where;
  * memory.md is copied into backups/cleared before it's emptied, and the
    message names the copy;
  * if the conversation's copy can't be kept, nothing is reset, memory
    included, and the message says so;
  * a shared /reset whose copy fails resets nothing either.

Run: python tests/test_reset_keeps_copies.py
"""

import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="resetcopies-")
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
    b._user_is_privileged = lambda uid: True
    b._last_thinking = {}
    b._histories = {}
    b._histories_lock = threading.RLock()
    b._save_histories = lambda: None
    return b


mem = kin_dir(KIN) / "memory.md"
cleared = kp.cleared_copies_dir(KIN)

# Private chat.
kp.save_memory(KIN, "I remember the color.")
b = bot(False)
b._histories[tb._hkey(7)] = talk(10)
b._cmd_reset(7, 7, "confirm")
msg = b.sent[-1] if b.sent else ""
arch = list(kp.cleared_copies_dir(KIN).glob("telegram-7-*.jsonl"))
check("a private /reset archives the conversation first", len(arch) == 1)
check("...and empties it", tb._hkey(7) not in b._histories)
check("...and says where the copy is", "cleared folder" in msg)
copies = list(cleared.glob("memory-before-reset-*.md")) if cleared.exists() else []
check("memory.md is copied before it's emptied",
      len(copies) == 1 and "the color" in copies[0].read_text(encoding="utf-8"))
check("...and emptied", mem.read_text(encoding="utf-8").strip() == "")
check("...and the message names the copy", copies and copies[0].name in msg)

# Private chat, copy can't be kept: nothing is reset.
kp.save_memory(KIN, "Still here.")
b = bot(False)
b._histories[tb._hkey(8)] = talk(10)
b._archive_telegram_history = lambda *a: (_ for _ in ()).throw(OSError("disk full"))
b._cmd_reset(8, 8, "confirm")
check("if the copy can't be kept, the conversation stays",
      len(b._histories.get(tb._hkey(8)) or []) == 10)
check("...and so does memory", "Still here." in mem.read_text(encoding="utf-8"))
check("...and the message says nothing was reset", "nothing was reset" in (b.sent[-1] if b.sent else ""))

# Shared chat whose copy fails.
kp.save_agent_conversation(KIN, talk(40))
real = kp.shutil.copy2
kp.shutil.copy2 = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
try:
    b = bot(True)
    b._cmd_reset(1, 1, "confirm")
finally:
    kp.shutil.copy2 = real
check("a shared /reset whose copy fails leaves the conversation",
      len(kp.load_agent_conversation(KIN)) == 40)
check("...and memory", "Still here." in mem.read_text(encoding="utf-8"))
check("...and says so", "nothing was" in (b.sent[-1] if b.sent else ""))

print()
if _fails:
    print(f"test_reset_keeps_copies: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_reset_keeps_copies: all checks passed")
