# SPDX-License-Identifier: CC0-1.0
"""A distill bookmark stays true when a history loses rows off its front
(audit, 20 Nov 2026; history storage in the shared core).

Telegram's and Discord's own histories (not kept with the desktop) are
capped, and the cap takes old rows off the FRONT. The bookmark is a position
in the list, so when rows came off the front, rows that hadn't been
distilled slid under it and were never distilled: they never reached the
kin's memory. Discord lost one row off the front on every message once full.

Now each history counts the rows ever added to it (saved with it), and the
bookmark is written with how many had come off the front at that moment.
Pinned through the real bot append, the real Discord append, the real
undo, and the shared reader every surface and the Memory tab use.

Run: python tests/test_distill_bookmark_front_cut.py
"""

import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="frontcut-", dir=(os.environ.get("HEARTHKIN_HOME") or None))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hearthkin_paths import kin_dir  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import history_rows as hr  # noqa: E402
import telegram_bot as tb  # noqa: E402
import kin_persistence as kp  # noqa: E402

KIN = "Fern"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)


def turn(i):
    return [{"role": "user", "content": f"m{i}", "ts": "2026-11-21T01:00:00"},
            {"role": "assistant", "content": f"r{i}", "ts": "2026-11-21T01:00:01"}]


def make_bot(cap):
    b = tb.TelegramBot.__new__(tb.TelegramBot)
    b.agent_name = KIN
    b._histories_lock = threading.RLock()
    b._histories = tb.load_telegram_history(KIN)
    b._rows_added = tb.load_telegram_rows_added(KIN)
    b.get_config = lambda: {"history_cap": cap}
    b.on_activity = None
    return b


print("--- Telegram: the cap takes rows off the front ---")
bot = make_bot(40)
UID = 777
for i in range(20):          # 40 rows: full, no trim yet
    bot._append_turns_for(UID, turn(i))
rows, front = bot.history_with_front(str(UID))
check("40 rows, nothing off the front yet", len(rows) == 40 and front == 0)

# Distillation read up to row 20 of those 40.
cfg = {}
hr.set_scope_bookmark(cfg, "tg:user:777", 20, front)
first_undistilled = rows[20]["content"]

bot._append_turns_for(UID, turn(20))  # 42 rows -> trimmed back
rows, front = bot.history_with_front(str(UID))
check("the cap took rows off the front", front > 0 and len(rows) < 42)
convo = hr.scope_rows(rows, front)
bm = hr.scope_bookmark(cfg, "tg:user:777", convo)
check("the first undistilled row is still the next one to distill",
      rows[bm]["content"] == first_undistilled)
# What the old rule did: the stored 30, read against the shorter list.
old_bm = kp.live_distill_bookmark(20, len(rows))
check("(the old rule skipped rows: the bug this pins)",
      rows[old_bm]["content"] != first_undistilled)

print("--- the count is saved with the history ---")
bot2 = make_bot(40)
rows2, front2 = bot2.history_with_front(str(UID))
check("a restarted bot reads the same front cut", front2 == front)
rows3 = tb.load_telegram_history(KIN).get(str(UID))
added = tb.load_telegram_rows_added(KIN).get(str(UID))
check("...and so does the file, read by the app with no bot running",
      hr.front_dropped(added, len(rows3)) == front)
tb.save_telegram_history(KIN, tb.load_telegram_history(KIN))
check("a save from outside the bot keeps the counts",
      tb.load_telegram_rows_added(KIN).get(str(UID)) == added)

print("--- regenerate cuts the END, which isn't a front cut ---")
before = bot.history_with_front(str(UID))
bot._truncate_history_for(UID, len(before[0]) - 2)
after = bot.history_with_front(str(UID))
check("cutting the newest rows leaves the front count alone",
      after[1] == before[1])

print("--- /clear: everything off the front, the bookmark starts over ---")
cfg = {}
hr.set_scope_bookmark(cfg, "tg:user:777", len(after[0]), after[1])
with bot._histories_lock:
    bot._histories.pop(str(UID), None)
    bot._save_histories()
for i in range(30):
    bot._append_turns_for(UID, turn(100 + i))
rows, front = bot.history_with_front(str(UID))
bm = hr.scope_bookmark(cfg, "tg:user:777", hr.scope_rows(rows, front))
check("the new conversation is read from its start", bm == 0)

print("--- a group's history, same rule ---")
for i in range(25):
    bot._append_group_history(-1009, turn(i))
rows, front = bot.history_with_front("group:-1009")
check("group history counted too", front is not None and front > 0)

print("--- an old file with no counts: no jump, starts counting ---")
cfg = {"distill_offsets": {"tg:user:5": 7}}
check("no count yet: the stored bookmark is used as it was",
      hr.scope_bookmark(cfg, "tg:user:5", hr.scope_rows(list(range(20)), None)) == 7)
check("counts start from what a history already holds",
      (lambda c: (hr.note_added(c, "k", 50, 2), c["k"])[1])({}) == 52)

print("--- Discord: rows come off the front once full ---")
for i in range(40):
    kp.append_discord_turn(KIN, 555, {"role": "user", "content": f"d{i}"},
                           cap=40)
rows, front = kp.discord_history_with_front(KIN, 555)
check("40 rows, none off the front", len(rows) == 40 and front == 0)
cfg = {}
hr.set_scope_bookmark(cfg, "discord:555", 35, front)
nxt = rows[35]["content"]
for i in range(40, 45):
    kp.append_discord_turn(KIN, 555, {"role": "user", "content": f"d{i}"},
                           cap=40)
rows, front = kp.discord_history_with_front(KIN, 555)
bm = hr.scope_bookmark(cfg, "discord:555", hr.scope_rows(rows, front))
check("rows came off the front; the bookmark followed",
      front > 0 and rows[bm]["content"] == nxt)
check("the count never shows up as a channel",
      list((kp.load_discord_history(KIN) or {}).keys()) == ["555"])

print("--- every reader and writer goes through the shared rule ---")
src = (ROOT / "frame" / "memory_mixin.py").read_text(encoding="utf-8")
dlg = (ROOT / "dialogs" / "edit_kin.py").read_text(encoding="utf-8")
check("the app never reads a bookmark around the shared rule",
      "live_distill_bookmark(" not in src
      and "live_distill_bookmark(" not in dlg)
check("the app writes the bookmark with its front count",
      "set_scope_bookmark(bcfg, scope_key, distilled_through" in src)

print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("ALL PASSED")
