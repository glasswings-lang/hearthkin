# SPDX-License-Identifier: CC0-1.0
"""The compaction note survives every trim of history (20 Nov 2026 audit).

Three places cut history after compacting, and each cut the note off once
enough came after it: Discord's 40-row window, Telegram's stored-history trim
(for a conversation not kept with the desktop), and the heartbeat's size
budget. The kin then carried on with neither the note nor what it replaced.

Pinned through the real code for each, and the check can fail: with no note,
nothing is added.

Run: python tests/test_compaction_note_survives_trims.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="notetrim-", dir=(os.environ.get("HEARTHKIN_HOME") or None))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chat_helpers import compact_marker, keep_compaction_note  # noqa: E402
from kin_persistence import (DEFAULT_AGENT_CONFIG, save_agent_config,  # noqa: E402
                             save_agent_conversation)
from hearthkin_paths import kin_dir  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


NOTE = "Everything so far, in short: we built a shed."


def history(after):
    rows = [{"role": "user", "content": f"old {i}"} for i in range(5)]
    rows.append(compact_marker(NOTE, 0, ts="2026-11-20T10:00:00"))
    for i in range(after):
        rows.append({"role": "user", "content": f"new {i} " + "word " * 40})
        rows.append({"role": "assistant", "content": f"reply {i}"})
    return rows


def has_note(rows):
    return any(NOTE in str((r or {}).get("content") or "") for r in rows)


print("--- the shared rule ---")
full = history(3)
check("no note anywhere: nothing added",
      keep_compaction_note([{"role": "user", "content": "x"}],
                           [{"role": "user", "content": "x"}])
      == [{"role": "user", "content": "x"}])
check("a note cut off by a trim is put back at the head",
      keep_compaction_note(full, full[-2:])[0].get("compact") is True)
check("...and not doubled when it's already there",
      len(keep_compaction_note(full, full[5:])) == len(full[5:]))

print("--- Telegram's stored-history trim ---")
import telegram_bot as _tb  # noqa: E402
b = _tb.TelegramBot.__new__(_tb.TelegramBot)
trimmed = b._trim_history(history(60), 100)
check("trimmed past the note, the note is still there", has_note(trimmed))
check("...first", trimmed[0].get("compact") is True)

print("--- Discord's 40-row window ---")
KIN = "Bracken"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)
cfg = dict(DEFAULT_AGENT_CONFIG)
cfg["model"] = "stub-model"
save_agent_config(KIN, cfg)
rows = [dict(r, source="discord:555") for r in history(60)]
save_agent_conversation(KIN, rows)
from discord_bot import DiscordBot  # noqa: E402
from kin_persistence import load_agent_config  # noqa: E402
d = DiscordBot.__new__(DiscordBot)
d.agent_name = KIN
d.get_config = lambda: load_agent_config(KIN)
d._dm_channels = set()
turns = d._channel_history("555", True)
check("120 rows after the note: the window still opens with it",
      bool(turns) and NOTE in str(turns[0].get("content")))
check("...and the window is still a window", len(turns) <= 41)

print("--- the heartbeat's size budget ---")
save_agent_conversation(KIN, history(60))
import hearthkin_cron  # noqa: E402
msgs = hearthkin_cron._build_messages(KIN, "beat", history_tokens=500)
check("a small budget still carries the note", has_note(msgs))
check("...and is still small", len(msgs) < 30)

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_compaction_note_survives_trims: all checks passed")
