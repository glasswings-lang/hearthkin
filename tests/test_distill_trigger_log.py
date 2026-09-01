# SPDX-License-Identifier: CC0-1.0
"""Guard test: every distillation must say WHICH trigger started it.

"It keeps distilling" was unanswerable. A distillation can begin four ways --
the every-N counter, the %-of-context measure, leaving a kin (or a room, or
the app), and somebody pressing a button -- and none of them left a record.
So the only way to explain a kin that seemed to distill constantly was to
theorise, and the theories were wrong three times running.

The trap that made the numbers look wrong is worth naming, because it is the
reason a log and not a cleverer guess: the leaving-a-kin trigger fires on ONE
new message, while the %-of-context trigger fires at a share of the window
(70% of 32,768 tokens on a real kin, i.e. many dozens of messages). Two
completely different thresholds, described to the reader in the same words.
A run that looked impossibly early against the 70% figure was simply the
other trigger, doing exactly what it should.

So each run now writes one line naming its trigger and how far behind the
scope actually was. What this file pins:

  - a line is written, and it names the trigger and the scope;
  - the numbers that settle the question are on it: bookmark, conversation
    length, and the gap between them;
  - every kind of trigger stays distinguishable from every other, so the
    one-message case can never again be mistaken for the 70% case;
  - the thresholds in force are recorded next to the run, since a reader
    weeks later has no way to know what the settings were at the time;
  - and it never, ever raises -- a logging fault must not cost a kin its
    memory write.

Run: python tests/test_distill_trigger_log.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="trigger-log-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


from kin_persistence import LOGS_DIR  # noqa: E402
from frame.memory_mixin import MemoryMixin  # noqa: E402

LOG = LOGS_DIR / "distill_triggers.log"


class _Frame(MemoryMixin):
    """Bare host: _log_distill_trigger touches config and the log file only."""

    def _undistilled_context_pct(self, agent_name, scope_key, convo, cfg):
        # The real one needs a loaded model profile and a calibration
        # ratio. Its VALUE is not what is under test here -- that it
        # reaches the line is.
        return 7.2


f = _Frame()


def lines():
    if not LOG.exists():
        return []
    return [ln for ln in LOG.read_text(encoding="utf-8").splitlines() if ln.strip()]


def last():
    got = lines()
    return got[-1] if got else ""


# --- the instrument fires at all ------------------------------------------
#
# Checked before anything is concluded from a later absence. A detector that
# never fires reports every trigger as missing, which is worse than no
# detector, because absence is the whole claim this file makes.

CFG = {
    "distill_offsets": {"desktop": 3703},
    "memory_distill_at_pct": 70,
    "memory_distill_every_n": 0,
    "memory_distill_on_close": True,
}
CONVO = [{"role": "user", "content": "x"}] * 3718

before = len(lines())
f._log_distill_trigger("Bracken", "ctx-desktop", "desktop", CONVO, CFG)
check("a distillation writes exactly one line", len(lines()) == before + 1)
check("...naming the kin", "[Bracken]" in last())
check("...naming the trigger", "trigger=ctx-desktop" in last())
check("...naming the scope, since scopes have independent cadences",
      "scope=desktop" in last())


# --- the numbers that answer the question ---------------------------------
#
# "How far behind was it?" is the whole question, and it cannot be
# reconstructed afterwards: the bookmark advances the moment the run
# finishes, so by the time anyone looks, the evidence is gone.

check("the bookmark it started from", "bookmark=3703" in last())
check("how long the conversation was", "turns=3718" in last())
check("and the gap between them, already worked out", "behind=15" in last())
check("the percent figure, so the % trigger can be checked against its line",
      "pct=7%" in last())


# --- the settings in force, recorded beside the run -----------------------
#
# A reader weeks later has no way to know what the thresholds were then; they
# are per-kin and editable, and reading today's config to explain last week's
# behaviour is exactly the mistake this log exists to stop.

check("the % threshold in force", "at_pct=70" in last())
check("the every-N threshold in force", "every_n=0" in last())
check("whether leaving the kin distills", "on_leave=True" in last())


# --- the confusion this exists to end -------------------------------------
#
# The on-leave trigger fires on ONE new message. Against a 70%-of-window
# figure that looks impossibly early, and it was read as a fault for weeks.
# Both must be on the record, and they must not read alike.

f._log_distill_trigger("Bracken", "on-close-desktop", "desktop",
                       [{"role": "user", "content": "x"}] * 3704, CFG)
on_leave = last()
check("leaving a kin after ONE new message is recorded as its own trigger",
      "trigger=on-close-desktop" in on_leave and "behind=1" in on_leave)
check("...and cannot be mistaken for the %-of-context trigger",
      "trigger=ctx-" not in on_leave)

# Every label a call site actually passes, so a new one that collides with
# an old one shows up here rather than in somebody's diagnosis.
LABELS = ["every-desktop", "ctx-desktop", "on-close-desktop", "manual-desktop",
          "catchup-desktop", "all-desktop", "walk-from-start-desktop"]
for label in LABELS:
    f._log_distill_trigger("Bracken", label, "desktop", CONVO, CFG)
written = [ln for ln in lines() if "trigger=" in ln][-len(LABELS):]
seen = [ln.split("trigger=", 1)[1].split(" ", 1)[0] for ln in written]
check("every kind of trigger is written back distinctly",
      seen == LABELS and len(set(seen)) == len(LABELS))


# --- a room and a DM keep their own lines ---------------------------------

f._log_distill_trigger("Bracken", "ctx-room:the kitchen", "room:the kitchen",
                       CONVO, CFG)
check("a room's run is not filed under the desktop's scope",
      "scope=room:the kitchen" in last())


# --- and it never costs a kin its memory ----------------------------------
#
# This runs immediately before the write that matters. Anything it raises
# would take the distillation down with it, so the failure it is allowed to
# have is silence.

n = len(lines())
try:
    f._log_distill_trigger("Bracken", "ctx-desktop", "desktop", None, {})
    f._log_distill_trigger(None, None, None, None, None)
    f._log_distill_trigger("Bracken", "ctx-desktop", "desktop", CONVO,
                           {"distill_offsets": "not a dict"})
    survived = True
except Exception as exc:  # noqa: BLE001 - the whole point is that this is unreachable
    survived = False
    print("      raised:", repr(exc))
check("junk input never raises -- a logging fault must not stop a memory write",
      survived)
check("...and a run it could not describe is skipped, not half-written",
      all(ln.count("trigger=") == 1 for ln in lines()[n:]))


# --- the log is always-on --------------------------------------------------
#
# Session logs are opt-in. This one is not: the question it answers is asked
# about behaviour that already happened, and a log you have to switch on
# beforehand is never on when you need it.

from kin_persistence import ALWAYS_ON_LOGS  # noqa: E402

check("distill_triggers.log is trimmed with the always-on logs, not the opt-in ones",
      "distill_triggers.log" in ALWAYS_ON_LOGS)


print()
if _fails:
    print(f"{len(_fails)} FAILED: " + ", ".join(_fails))
    sys.exit(1)
print("all checks passed")
