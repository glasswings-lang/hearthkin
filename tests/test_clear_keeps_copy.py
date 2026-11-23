# SPDX-License-Identifier: CC0-1.0
"""No clear loses a conversation (23 Nov 2026).

Only Telegram's /new saved a copy before emptying a kin's conversation. The
desktop's Clear, /clear on Telegram and Discord, and anything else that
rewrote the file down to nothing kept no copy. A scare over a kin's whole
March-to-November history (/new had in fact saved it) made the gap plain.
Pinned:

  * emptying a conversation copies it into backups/cleared/ first, every word;
  * taking away more than half, or more than ten messages, copies it too;
  * undo-sized changes (one or two messages) make no copy;
  * replacing a conversation with a LONGER one still copies the old one;
  * adding on, or rewriting the same messages differently, copies nothing;
  * if the copy can't be written, nothing is emptied;
  * Discord's clear keeps a copy of what it takes away;
  * the check can fail: with the copy step removed, no copy appears.

Run: python tests/test_clear_keeps_copy.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="clearcopy-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import kin_persistence as kp  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Sorrel"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)
cleared = kp.cleared_copies_dir(KIN)


def copies():
    return sorted(cleared.glob("*")) if cleared.exists() else []


def talk(n):
    return [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"line {i}"} for i in range(n)]


kp.save_agent_conversation(KIN, talk(40))
check("saving a conversation the first time makes no copy", copies() == [])

kp.save_agent_conversation(KIN, talk(39))
kp.save_agent_conversation(KIN, talk(38))
check("undo-sized changes make no copy", copies() == [])

kp.save_agent_conversation(KIN, [])
got = copies()
check("emptying it copies it first", len(got) == 1)
if got:
    lines = [json.loads(x) for x in got[0].read_text(encoding="utf-8").splitlines() if x.strip()]
    check("...every word of it", lines == talk(38))
check("...and the conversation is empty now", kp.load_agent_conversation(KIN) == [])

kp.save_agent_conversation(KIN, talk(30))
kp.save_agent_conversation(KIN, talk(14))
check("taking away more than half copies it", len(copies()) == 2)

kp.save_agent_conversation(KIN, talk(200))
kp.save_agent_conversation(KIN, talk(180))
check("taking away more than ten copies it", len(copies()) == 3)

# Replaced by a LONGER conversation (File, Open conversation): every live
# message goes, though the file grows. Found the night it first shipped.
kp.save_agent_conversation(KIN, talk(12))
before = len(copies())
saved = [{"role": "user", "content": f"old {i}", "ts": f"2026-03-{i % 28 + 1:02d}"}
         for i in range(300)]
kp.save_agent_conversation(KIN, saved)
check("replacing a short conversation with a longer one copies the short one",
      len(copies()) == before + 1)

# Adding to the end, or the same messages with fields reordered, is no loss.
before = len(copies())
kp.save_agent_conversation(KIN, saved + talk(5))
reordered = [dict(reversed(list(m.items()))) for m in saved + talk(5)]
kp.save_agent_conversation(KIN, reordered)
check("adding on, or the same messages written differently, makes no copy",
      len(copies()) == before)

kp.save_agent_conversation(KIN, talk(200))

# A copy that can't be made: nothing is emptied.
real_copy = kp.shutil.copy2


def broken(*a, **k):
    raise OSError("disk full")


kp.shutil.copy2 = broken
try:
    try:
        kp.save_agent_conversation(KIN, [])
        refused = False
    except OSError:
        refused = True
finally:
    kp.shutil.copy2 = real_copy
check("if the copy can't be made, the clear is refused", refused)
check("...and the conversation is still all there", len(kp.load_agent_conversation(KIN)) == 200)

# Discord's clear, the person's own channel history.
import discord_bot  # noqa: E402


bot_cls = next((v for v in vars(discord_bot).values()
                if isinstance(v, type) and hasattr(v, "_clear_channel")), None)
check("found Discord's clear", bot_cls is not None)
if bot_cls is not None:
    bot = bot_cls.__new__(bot_cls)
    bot.agent_name = KIN
    kp.save_discord_history(KIN, {"123": talk(6)})
    before = len(copies())
    gone = bot._clear_channel(123, False)
    check("Discord's clear takes the channel's messages", gone == 6)
    check("...and keeps a copy of them", len(copies()) == before + 1)
    conv = talk(4) + [dict(m, source="discord:9") for m in talk(4)]
    kp.save_agent_conversation(KIN, conv)
    before = len(copies())
    gone = bot._clear_channel(9, True)
    check("Discord's clear in a shared chat takes only that channel", gone == 4
          and len(kp.load_agent_conversation(KIN)) == 4)
    check("...and keeps a copy of what it took", len(copies()) >= before + 1)

# The check can fail: without the copy step, emptying leaves no copy.
real_keep = kp.keep_copy_before_big_rewrite
kp.keep_copy_before_big_rewrite = lambda *a, **k: None
try:
    kp.save_agent_conversation(KIN, talk(50))
    before = len(copies())
    kp.save_agent_conversation(KIN, [])
    check("the check can fail: with the copy step gone, no copy appears",
          len(copies()) == before)
finally:
    kp.keep_copy_before_big_rewrite = real_keep

print()
if _fails:
    print(f"test_clear_keeps_copy: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_clear_keeps_copy: all checks passed")
