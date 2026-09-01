# SPDX-License-Identifier: CC0-1.0
"""Guard test: a backlog must not distill after every single reply.

The percent trigger asks "is the undistilled tail a big share of the context
window?" That is right for a conversation that has outgrown its notes and wrong
for a bulk history import, which buries the bookmark under thousands of messages
at once. One run cannot clear that, so the trigger is still tripped when the
next reply finishes -- and it fires again, and again, after almost every reply,
for as long as the backlog lasts.

Measured on a real kin: a 5,872-message tail, about 738,000 tokens, against a
threshold it exceeded twenty-two times over. In one day that scope spent 66
minutes distilling while the person got 24 minutes of conversation, on the same
local model, so the two were taking the model from each other. It also threw
away the prompt cache on every turn -- the exact thing that had just been fixed
one layer down.

Nothing was malfunctioning. The trigger simply cannot win the race it was asked
to run, and chasing it every turn is how it loses.

So a run that ends still behind starts a wait. What this file pins:

  - a run that leaves MORE behind than it digested starts the wait;
  - a run that finishes the job does NOT (and clears a wait left over from
    when it was behind);
  - only the automatic triggers are paced -- a walk, a queue drain, an
    on-close run and anything the person pressed are deliberate and never
    held back;
  - 0 minutes means the old behaviour exactly, for anyone who wants it.

Run: python tests/test_distill_backlog_pacing.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="backlog-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import frame.memory_mixin as mm  # noqa: E402
from frame.memory_mixin import MemoryMixin  # noqa: E402

# One kin's config, swapped per case. The mixin reads it through
# load_agent_config, so patching that reaches the real code path.
_CFG = {"distill_backlog_pace_mins": 30}
mm.load_agent_config = lambda name: dict(_CFG)


class _Frame(MemoryMixin):
    """Bare host: these two methods touch only their own state and _log."""

    def __init__(self):
        self.logged = []

    def _log(self, msg):
        self.logged.append(msg)


f = _Frame()
KEY = ("Bracken", "desktop")


# --- a backlog starts a wait ---------------------------------------------

check("nothing is paced before anything has run",
      not f._backlog_pace_holds(*KEY))

# The real shape: one run digested ~270 messages, 5,600 are still behind it.
f._note_backlog_pace("Bracken", "desktop", digested=272, remaining=5600)
check("a run that ends further behind than it got starts a wait",
      f._backlog_pace_holds(*KEY))
check("...and says so in the log, since nobody watches a kin distill",
      any("pacing the next automatic run" in m for m in f.logged))

# The wait is real time, not a counter: expire it and the gate opens.
f._backlog_distill_pause_until[KEY] = mm.time.monotonic() - 1
check("the wait ends by itself once the time is up",
      not f._backlog_pace_holds(*KEY))
check("...and the expired entry is dropped rather than left to accumulate",
      KEY not in f._backlog_distill_pause_until)


# --- an ordinary catch-up is untouched -----------------------------------

f2 = _Frame()
f2._note_backlog_pace("Bracken", "desktop", digested=300, remaining=40)
check("a run that finishes the job starts no wait at all",
      not f2._backlog_pace_holds(*KEY))

f2._note_backlog_pace("Bracken", "desktop", digested=300, remaining=300)
check("a run with exactly one more run's worth left is not a backlog",
      not f2._backlog_pace_holds(*KEY))

# Coming out the far end of a backlog must release the brake.
f3 = _Frame()
f3._note_backlog_pace("Bracken", "desktop", digested=272, remaining=5600)
f3._note_backlog_pace("Bracken", "desktop", digested=272, remaining=10)
check("the last run of a backlog clears the wait instead of leaving it set",
      not f3._backlog_pace_holds(*KEY))


# --- the knob, and the off switch ----------------------------------------

_CFG["distill_backlog_pace_mins"] = 0
f4 = _Frame()
f4._note_backlog_pace("Bracken", "desktop", digested=1, remaining=9999)
check("0 minutes restores the old behaviour exactly (no wait, ever)",
      not f4._backlog_pace_holds(*KEY))

_CFG["distill_backlog_pace_mins"] = 90
f5 = _Frame()
_before = mm.time.monotonic()
f5._note_backlog_pace("Bracken", "desktop", digested=1, remaining=9999)
_waited = f5._backlog_distill_pause_until[KEY] - _before
check("the wait is the configured number of minutes",
      89 * 60 <= _waited <= 91 * 60)
_CFG["distill_backlog_pace_mins"] = 30


# --- scopes are independent ----------------------------------------------

f6 = _Frame()
f6._note_backlog_pace("Bracken", "desktop", digested=1, remaining=9999)
check("pacing one surface does not pace another",
      f6._backlog_pace_holds("Bracken", "desktop")
      and not f6._backlog_pace_holds("Bracken", "tg:user:123"))
check("...nor another kin on the same surface",
      not f6._backlog_pace_holds("Vesper", "desktop"))


# --- bad input can never wedge a kin's memory ----------------------------

f7 = _Frame()
f7._note_backlog_pace("Bracken", "desktop", digested=None, remaining=5000)
check("a run that can't say how far it got starts no wait "
      "(a stuck brake is worse than a busy model)",
      not f7._backlog_pace_holds(*KEY))
f7._note_backlog_pace("Bracken", "desktop", digested="?", remaining="?")
check("...and rubbish input is swallowed rather than raised into the "
      "completion path",
      not f7._backlog_pace_holds(*KEY))


# --- only the automatic triggers are paced -------------------------------
#
# Read from the source of _on_distill_done rather than restating the rule:
# the point is that WALKS and deliberate runs stay unpaced, and a copy of the
# list here would keep passing after someone changed the real one.

import inspect  # noqa: E402

_src = inspect.getsource(MemoryMixin._on_distill_done)
check("the completion path paces automatic runs only",
      '_note_backlog_pace' in _src
      and 'startswith(("every-", "ctx-"))' in _src)

_gate = inspect.getsource(MemoryMixin._maybe_auto_distill)
check("...and the gate is checked before a trigger is even considered",
      "_backlog_pace_holds" in _gate
      and _gate.index("_backlog_pace_holds") < _gate.index("trigger = None"))

# The walk chains through its own path; it must not be able to trip the gate.
_walk_labels = ("walk-desktop", "manual", "on-close-desktop", "queue-desktop")
check("no deliberate run's label is mistaken for an automatic one",
      not any(l.startswith(("every-", "ctx-")) for l in _walk_labels))


print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_distill_backlog_pacing: all checks passed")
