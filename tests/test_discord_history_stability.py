# SPDX-License-Identifier: CC0-1.0
"""Guard test: a Discord channel past its history cap must not shed a message
per turn.

The same failure as `test_telegram_history_stability`, on the surface that
never got the fix. Telegram's version keeps a list and trimmed it on append;
Discord rebuilds the window from disk on every turn and did it with
`turns[-cap:]`, which has the identical effect and is harder to spot because
there is no trim call to look at.

A local model reuses its cached work only for an unbroken run from the very
start of the prompt. So once a channel passes 40 messages, every new message
pushes one off the FRONT, and the whole context is read again from cold. It
never settles: a channel below the cap is fast, a channel at the cap is slow
on every turn forever, and from a chair the two are indistinguishable —
"Discord is slow" rather than "this channel crossed a line three weeks ago".

What this pins is the PROPERTY — replay a growing channel and assert the front
rarely moves — not the arithmetic, which someone could "simplify" back to a
one-line slice while a formula-shaped test kept passing. The old behaviour is
carried as a positive control, because a stability test that cannot fail is
not evidence of stability.

Run: python tests/test_discord_history_stability.py
"""

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


from discord_bot import _stable_history_window, DISCORD_HISTORY_CAP  # noqa: E402

CAP = DISCORD_HISTORY_CAP


def replay(cap, turns, window_fn):
    """Grow a channel one message at a time; count how often the first
    message of the window changes. That count IS the number of turns that
    paid a full re-read."""
    moves, prev_front, sizes = 0, None, []
    convo = []
    for i in range(turns):
        convo.append({"role": "user" if i % 2 == 0 else "assistant",
                      "content": f"m{i}"})
        window = window_fn(convo, cap)
        sizes.append(len(window))
        front = window[0]["content"] if window else None
        if prev_front is not None and front != prev_front:
            moves += 1
        prev_front = front
    return moves, sizes


TURNS = 300
moves, sizes = replay(CAP, TURNS, _stable_history_window)

check("the front of the window rarely moves, even well past the cap",
      moves <= TURNS // (CAP // 4) + 2)
check("...which is a small fraction of the turns, not most of them",
      moves < TURNS // 10)
check("the window never exceeds the configured cap", max(sizes) <= CAP)
check("...and never collapses to a scrap of context",
      min(sizes[CAP:]) >= CAP - max(1, CAP // 4))


# Positive control: the instrument has to be able to see the old behaviour,
# or a passing run above means nothing.
def old_window(turns, cap):
    return list(turns[-cap:])


old_moves, _ = replay(CAP, TURNS, old_window)
check("the instrument catches the old one-at-a-time slice (positive control)",
      old_moves > TURNS // 2)
# The improvement should be about the step factor (cap // 4, so 10x at the
# default cap). Asserted at 5x rather than 10x: the exact ratio depends on
# where the replay happens to stop relative to a step boundary, and a test
# that fails when a fix is merely nine times better instead of ten is a test
# that will be muted rather than read.
check("...and the fix is a large improvement over it, not a rounding error",
      moves * 5 < old_moves)


# Edges.
check("a channel under the cap is returned untouched",
      _stable_history_window([{"content": "a"}], CAP) == [{"content": "a"}])
check("an empty channel stays empty", _stable_history_window([], CAP) == [])
check("a cap of zero means no windowing at all",
      len(_stable_history_window([{"content": str(i)} for i in range(50)], 0))
      == 50)
check("it keeps the most RECENT messages, not the oldest",
      _stable_history_window(
          [{"content": str(i)} for i in range(100)], CAP)[-1]["content"] == "99")
check("a tiny cap still leaves something usable",
      len(_stable_history_window(
          [{"content": str(i)} for i in range(100)], 4)) >= 3)

# And the caller actually uses it -- a helper nothing calls fixes nothing.
import inspect  # noqa: E402
from discord_bot import DiscordBot  # noqa: E402

src = inspect.getsource(DiscordBot._channel_history)
check("the channel-history reader goes through the helper",
      "_stable_history_window" in src)
check("...and no longer slices the list directly",
      "[-DISCORD_HISTORY_CAP:]" not in src)

print()
if _fails:
    print(f"test_discord_history_stability: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_discord_history_stability: all checks passed")
