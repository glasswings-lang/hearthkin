# SPDX-License-Identifier: CC0-1.0
"""Guard test: a Discord channel past its history cap must not shed a message
per turn, and Discord follows Telegram's one history rule.

A local model reuses its cached work only for an unbroken run from the very
start of the prompt. So if every new message pushes one off the FRONT, the
whole context is read again from cold on every turn, forever, once a channel
crosses the cap.

Since 21 Nov 2026 Discord keeps and sends its history the way Telegram does:
the channel's own store is capped by the Discord tab's "History cap"
(default 100) and trimmed a quarter at a time (history_rows.trim_front, the
same function Telegram's trim calls), and everything since the last
compaction is sent. It used to keep 200 and send only the newest 40.

What this pins is the PROPERTY: replay a growing channel through the real
store and assert the front rarely moves. The old one-at-a-time slice is
carried as a positive control, because a stability test that cannot fail is
not evidence of stability.

Run: python tests/test_discord_history_stability.py
"""

import inspect
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="dchist-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import kin_persistence as kp  # noqa: E402
from history_rows import trim_front, history_cap  # noqa: E402

KIN = "Bracken"
kp.agent_dir(KIN).mkdir(parents=True, exist_ok=True)
cfg = dict(kp.DEFAULT_AGENT_CONFIG)
cfg["discord"] = {"history_cap": 40}
kp.save_agent_config(KIN, cfg)
CAP = 40


def replay_store(turns):
    """Grow a channel's own store one message at a time through the real
    append; count how often its first message changes."""
    moves, prev_front, sizes = 0, None, []
    for i in range(turns):
        kp.append_discord_turn(KIN, "99", {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"m{i}"})
        rows = kp.load_discord_history(KIN).get("99") or []
        sizes.append(len(rows))
        front = rows[0]["content"] if rows else None
        if prev_front is not None and front != prev_front:
            moves += 1
        prev_front = front
    return moves, sizes


TURNS = 300
moves, sizes = replay_store(TURNS)
check("the Discord tab's history cap is what the store obeys",
      max(sizes) <= CAP)
check("the front of the store rarely moves, even well past the cap",
      moves <= TURNS // (CAP // 4) + 2)
check("...which is a small fraction of the turns, not most of them",
      moves < TURNS // 10)
check("...and never collapses to a scrap of context",
      min(sizes[CAP:]) >= CAP - max(1, CAP // 4))
check("it keeps the most RECENT messages",
      kp.load_discord_history(KIN)["99"][-1]["content"] == f"m{TURNS - 1}")


# Positive control: the instrument has to see the one-at-a-time slice.
def replay_slice(turns):
    convo, moves, prev = [], 0, None
    for i in range(turns):
        convo.append({"content": f"m{i}"})
        window = convo[-CAP:]
        if prev is not None and window[0]["content"] != prev:
            moves += 1
        prev = window[0]["content"]
    return moves


old_moves = replay_slice(TURNS)
check("the instrument catches the old one-at-a-time slice (positive control)",
      old_moves > TURNS // 2)
check("...and the rule is a large improvement over it",
      moves * 5 < old_moves)

# The setting.
check("no setting: Telegram's default of 100", history_cap({}) == 100)
check("0 means no cap", history_cap({"history_cap": 0}) == 0
      and len(trim_front(list(range(500)), 0)) == 500)
check("junk falls back to the default", history_cap({"history_cap": "x"}) == 100)

# One rule, really shared.
from telegram_bot import TelegramBot  # noqa: E402
from discord_bot import DiscordBot  # noqa: E402
check("Telegram's trim goes through the shared rule",
      "trim_front" in inspect.getsource(TelegramBot._trim_history))
check("Discord's store goes through the shared rule",
      "trim_front" in inspect.getsource(kp.append_discord_turn))
src = inspect.getsource(DiscordBot._channel_history)
check("Discord no longer cuts what it sends to the newest 40",
      "_stable_history_window" not in src and "[-40:]" not in src)

print()
if _fails:
    print(f"test_discord_history_stability: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_discord_history_stability: all checks passed")
