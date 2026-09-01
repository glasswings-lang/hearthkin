# SPDX-License-Identifier: CC0-1.0
"""Guard test: a Telegram history at its cap must not shed a message per turn.

Same failure as `test_tool_history_stability`, one layer down, and worse —
because Telegram is the surface this app is actually used through.

A local model reuses its cached work only for an unbroken run from the very
start of the prompt. The stored history was capped with `history[-cap:]` on
every append, so the moment a conversation reached the cap (100 messages by
default), every new message pushed one off the FRONT. The first message changed
every turn, so the entire prompt was read again from cold: 22,000+ tokens at
about 78 tokens a second, roughly five minutes before a reply began.

The cruelty of it is that it never settles. A conversation below the cap is
fine; a conversation at the cap is slow on every single turn forever, and gets
no better. The two look identical from a chair, and the app's own logs report
the reply as having "succeeded" either way.

The fix is to fill to the cap and then cut back by a whole step at once, so the
front of the history is stable for `step` turns at a time.

What this pins is the PROPERTY — replay an overflowing conversation and assert
the front rarely moves — not the arithmetic, which someone could "simplify"
back to a one-line slice while every formula-shaped test kept passing.

Run: python tests/test_telegram_history_stability.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="tghist-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


from telegram_bot import TelegramBot  # noqa: E402

trim = TelegramBot._trim_history
CAP = 100


class _Bot:
    _trim_history = TelegramBot._trim_history


_bot = _Bot()


def replay(cap, turns):
    """Append one message per turn, trimming as the real path does. Returns
    the number of turns on which the FIRST message changed."""
    history = []
    moves = 0
    first = None
    for i in range(turns):
        history.append({"role": "user", "content": f"message {i}"})
        history = _bot._trim_history(history, cap)
        if first is not None and history[0] != first:
            moves += 1
        first = history[0]
    return moves, len(history)


# --- the property -------------------------------------------------------

moves, final = replay(CAP, 300)
check("the front of the history rarely moves, even well past the cap",
      moves <= 300 // (CAP // 4) + 1)
print(f"       (front moved on {moves} of 300 turns; "
      f"the old every-turn slice moved on ~200)")
check("...and the history never exceeds the configured cap", final <= CAP)

# The old behaviour, for contrast — and to prove this test would catch it.
def old_replay(cap, turns):
    history, moves, first = [], 0, None
    for i in range(turns):
        history.append({"role": "user", "content": f"message {i}"})
        if cap > 0 and len(history) > cap:
            history = history[-cap:]
        if first is not None and history[0] != first:
            moves += 1
        first = history[0]
    return moves


_old = old_replay(CAP, 300)
check("the instrument catches the old behaviour (positive control)",
      _old > moves * 5)
print(f"       (old: {_old} moves, new: {moves})")


# --- the cap is still a ceiling -----------------------------------------
# Going over would push the context past what the person configured, and an
# oversized context on local Ollama returns nothing at all.
_over = []
for cap in (10, 25, 100, 250):
    for turns in (cap - 1, cap, cap + 1, cap * 3):
        _, length = replay(cap, turns)
        if length > cap:
            _over.append((cap, turns, length))
check("no cap size is ever exceeded", not _over)
if _over:
    print("       exceeded at:", _over[:4])

# ...and it must not strangle the context either.
_, length = replay(100, 500)
check("a long conversation still keeps a substantial history",
      length >= 75)
print(f"       (settles between {100 - 100 // 4} and 100 messages)")


# --- things that must not change ----------------------------------------

check("cap of 0 means no trimming at all",
      len(trim(_bot, [{"role": "user"}] * 500, 0)) == 500)
check("a history under the cap is returned untouched",
      trim(_bot, [{"role": "user", "content": "a"}], CAP)
      == [{"role": "user", "content": "a"}])
check("an empty history stays empty", trim(_bot, [], CAP) == [])
# The floor guards a tiny cap: trimming to cap-step could otherwise leave
# almost nothing.
check("a tiny cap still leaves a usable history",
      len(trim(_bot, [{"role": "user"}] * 40, 10)) >= 10)

# The newest messages are the ones kept — it trims the OLD end.
_h = [{"role": "user", "content": f"m{i}"} for i in range(200)]
_t = trim(_bot, _h, CAP)
check("it keeps the most recent messages, not the oldest",
      _t[-1]["content"] == "m199")


# --- both call sites actually use it ------------------------------------
# Source-level: driving the real append path needs a live bot object.
_src = (ROOT / "telegram_bot.py").read_text(encoding="utf-8")
check("no append path still slices the history directly",
      "history = history[-cap:]" not in _src)
check("both the DM and group paths go through the helper",
      _src.count("self._trim_history(history, cap)") == 2)


print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_telegram_history_stability: all checks passed")
