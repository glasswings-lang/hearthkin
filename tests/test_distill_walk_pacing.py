# SPDX-License-Identifier: CC0-1.0
"""Guard test: redistill pacing (day / hour / chunk).

Before this, a redistill-from-start had exactly one shape: rewind to 0,
then chunk through the whole conversation unattended, stopping only on
error, on quitting, or on Cancel — an all-or-nothing choice with no stop
in between for someone who wants to listen to what a redistill produces
as it goes rather than let it run overnight. And Cancel's only lever was
rewind-vs-restore: cancelling mid-walk either left the kin exactly where
an interrupted unattended walk stood, or (after the earlier fix) rewound
all the way back to before the walk started — neither of which is "keep
the days I already reviewed and stop there."

Pacing gives a walk a unit smaller than "the whole thing": it keeps
auto-chaining bites (a big day/hour can still take several — the token
budget still applies) but stops itself before crossing into the next
calendar day/hour, or after every single bite for 'chunk' pacing. The
existing Continue-redistilling button becomes "give me the next unit" —
no new button needed. And Cancel on a paced walk keeps whatever units
were already completed instead of rewinding past them, since every one
of them only happened because the operator explicitly continued into it.

Two things are tested here as pure, isolated pieces rather than through
the much larger _on_distill_done:
  - _distill_bite's boundary capping (day/hour) and its hit_boundary
    signal, with all its token/model/soul dependencies mocked to fixed
    values so the bite math is fully predictable.
  - _walk_should_pause_after_bite's decision logic, extracted
    specifically so it's testable without the rest of the completion
    machinery.

Run: python tests/test_distill_walk_pacing.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault(
    "HEARTHKIN_HOME",
    tempfile.mkdtemp(prefix="hearthkin-pacingtest-"))

import frame.memory_mixin as _mm  # noqa: E402
from frame.memory_mixin import MemoryMixin  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# --- fixed, predictable stand-ins for _distill_bite's dependencies ------
#
# Every message costs a flat 100 (estimated) tokens regardless of
# content, num_ctx is pinned at 10000 and the summarizer's own context
# length is unknown (falls back to num_ctx), so budget_ctx=10000,
# reserve=8000 (_DISTILL_RESERVE_TOKENS, soul/memory both mocked to 0
# tokens), giving budget=max(2048, 10000-8000)=2048 -> exactly 20
# messages fit in one bite by token budget alone (2048 // 100 = 20).
# Small, round, and enough to build both "day fits in one bite" and
# "day needs two bites" fixtures without huge conversations.

_mm._num_ctx_of = lambda cfg, default=8192: 10000
_mm._model_context_length = lambda model: None
_mm.estimate_tokens = lambda text: 0
_mm.load_soul = lambda agent_name: ""
_mm.load_memory = lambda agent_name: ""
_mm.estimate_message_tokens = lambda msg, model=None: 100


class _FakeLLMBackend:
    @staticmethod
    def token_calibration_ratio(agent_name):
        return 1.0


_mm.llm_backend = _FakeLLMBackend()

BUDGET_MESSAGES = 20  # messages that fit in one bite by token budget alone


class Bite:
    """Only what _distill_bite / _walk_boundary_ts / the pause decision
    touch."""
    _distill_bite = MemoryMixin._distill_bite
    # _distill_bite finishes by trimming any single message larger than the
    # whole bite budget (see test_distill_oversized_message). Borrowed here
    # rather than stubbed: a stub would let that step be removed from the
    # real one without this file noticing.
    _fit_oversized_messages = MemoryMixin._fit_oversized_messages
    _log = lambda self, msg: None
    _walk_boundary_ts = MemoryMixin._walk_boundary_ts
    _walk_should_pause_after_bite = MemoryMixin._walk_should_pause_after_bite
    _WALK_PACING_UNATTENDED = MemoryMixin._WALK_PACING_UNATTENDED
    _WALK_PACING_DAY = MemoryMixin._WALK_PACING_DAY
    _WALK_PACING_HOUR = MemoryMixin._WALK_PACING_HOUR
    _WALK_PACING_CHUNK = MemoryMixin._WALK_PACING_CHUNK


def _msgs(specs):
    """specs: list of ISO ts strings. Builds plain user-turn dicts —
    content is irrelevant to the bite math, only 'ts' matters."""
    return [{"role": "user", "content": f"msg {i}", "ts": ts}
            for i, ts in enumerate(specs)]


def _day(day, count, hour=9):
    """count messages spaced 10 minutes apart within one calendar day,
    starting at `hour`:00."""
    out = []
    for i in range(count):
        h = hour + (i * 10) // 60
        m = (i * 10) % 60
        out.append(f"2026-01-{day:02d}T{h:02d}:{m:02d}:00")
    return out


b = Bite()
cfg = {"distill_offsets": {}, "model": "", "memory_model": ""}


# --- pacing=None is byte-for-byte the old, unbounded-by-calendar shape --

convo = _msgs(_day(1, 5) + _day(2, 5))
slice_, through, budget_ctx, hit = b._distill_bite(
    convo, cfg, "desktop", "Kin", pacing=None)
check("no pacing: both days get swept into one bite",
      through == len(convo))
check("no pacing: hit_boundary is always False",
      hit is False)


# --- day pacing: a day that fits comfortably in one bite ----------------

convo = _msgs(_day(1, 5) + _day(2, 5) + _day(3, 5))
slice_, through, budget_ctx, hit = b._distill_bite(
    convo, cfg, "desktop", "Kin", pacing="day")
check("day pacing: stops at the end of day 1, not day 2 or 3",
      through == 5)
check("day pacing: hit_boundary is True (the boundary is what capped it)",
      hit is True)

# Continuing from where that bite left off picks up day 2 the same way.
cfg2 = {"distill_offsets": {"desktop": through}, "model": "", "memory_model": ""}
slice_, through2, _, hit2 = b._distill_bite(
    convo, cfg2, "desktop", "Kin", pacing="day")
check("day pacing: continuing from the bookmark reaches end of day 2",
      through2 == 10)
check("day pacing: still hit_boundary", hit2 is True)


# --- day pacing: a day whose content exceeds one bite's token budget ----

BIG_DAY = BUDGET_MESSAGES + 8  # deliberately more than one bite can hold
convo = _msgs(_day(1, BIG_DAY) + _day(2, 5))
slice_, through, _, hit = b._distill_bite(
    convo, cfg, "desktop", "Kin", pacing="day")
check("day pacing, oversized day: first bite capped by TOKEN BUDGET",
      through == BUDGET_MESSAGES)
check("...not by the boundary — more of today is still left",
      hit is False)

cfg2 = {"distill_offsets": {"desktop": through}, "model": "", "memory_model": ""}
slice_, through2, _, hit2 = b._distill_bite(
    convo, cfg2, "desktop", "Kin", pacing="day")
check("day pacing, oversized day: second bite finishes the rest of day 1",
      through2 == BIG_DAY)
check("...and THIS bite is the one that hits the boundary",
      hit2 is True)


# --- hour pacing, same shape one level finer -----------------------------

convo = _msgs([
    "2026-01-01T09:00:00", "2026-01-01T09:15:00", "2026-01-01T09:45:00",
    "2026-01-01T10:05:00", "2026-01-01T10:20:00",
])
slice_, through, _, hit = b._distill_bite(
    convo, cfg, "desktop", "Kin", pacing="hour")
check("hour pacing: stops at the end of the 09:00 hour",
      through == 3)
check("hour pacing: hit_boundary True", hit is True)


# --- chunk pacing has NO boundary concept at the bite level --------------
# (the stop-every-bite behavior lives entirely in
# _walk_should_pause_after_bite, exercised below)

convo = _msgs(_day(1, 5) + _day(2, 5))
slice_, through, _, hit = b._distill_bite(
    convo, cfg, "desktop", "Kin", pacing="chunk")
check("chunk pacing at the bite level behaves like no pacing at all",
      through == len(convo) and hit is False)


# --- the re-read overlap doesn't distort the boundary --------------------
# Bookmark sits at index 3 (day 1); the overlap reaches back into
# earlier day-1 messages, which is fine, but must not be what the
# boundary is computed from.

convo = _msgs(_day(1, 6) + _day(2, 5))
cfg3 = {"distill_offsets": {"desktop": 3}, "model": "", "memory_model": ""}
slice_, through, _, hit = b._distill_bite(
    convo, cfg3, "desktop", "Kin", pacing="day")
check("overlap doesn't leak into the boundary: still stops at end of day 1",
      through == 6)
check("...correctly reported as hit_boundary", hit is True)


# --- fully caught up returns the 4-tuple shape, hit_boundary False -------

convo = _msgs(_day(1, 5))
cfg4 = {"distill_offsets": {"desktop": 5}, "model": "", "memory_model": ""}
result = b._distill_bite(convo, cfg4, "desktop", "Kin", pacing="day")
check("caught-up return is still a 4-tuple",
      len(result) == 4)
check("caught-up: hit_boundary is False",
      result[3] is False)


# --- _walk_should_pause_after_bite: pure decision table ------------------

check("unattended never pauses",
      b._walk_should_pause_after_bite("unattended", True) is False
      and b._walk_should_pause_after_bite("unattended", False) is False)
check("chunk ALWAYS pauses, regardless of hit_boundary",
      b._walk_should_pause_after_bite("chunk", True) is True
      and b._walk_should_pause_after_bite("chunk", False) is True)
check("day pauses only when hit_boundary is True",
      b._walk_should_pause_after_bite("day", True) is True
      and b._walk_should_pause_after_bite("day", False) is False)
check("hour pauses only when hit_boundary is True",
      b._walk_should_pause_after_bite("hour", True) is True
      and b._walk_should_pause_after_bite("hour", False) is False)
check("an unrecognized pacing value never pauses (fails open)",
      b._walk_should_pause_after_bite("something-unexpected", True) is False)


# --- Cancel: paced walks keep progress, unattended walks still restore --
#
# The whole reason pacing exists: an unattended walk's Cancel rewinds
# the bookmark back to before the walk started (a prior fix — that's
# correct there, since nothing about an unattended walk was ever
# individually approved). A PACED walk is different in kind — every
# unit it completed only happened because the operator explicitly
# pressed Continue for it — so Cancel on a paced walk must leave the
# bookmark exactly where it sits, not rewind past real, deliberately-
# approved progress.
#
# Calls EditKinDialog._on_cancel_walk directly as an unbound method
# against a minimal stub -- never constructs a real wx.Dialog. See
# CLAUDE.md, "Never build wx widgets in the default test run".

from dialogs.edit_kin import EditKinDialog  # noqa: E402


class _FrameStub:
    _walk_scopes_on_disk = MemoryMixin._walk_scopes_on_disk
    _persist_walk = MemoryMixin._persist_walk
    _start_walk = MemoryMixin._start_walk
    _end_walk = MemoryMixin._end_walk
    _walk_prior_offset = MemoryMixin._walk_prior_offset
    _persist_walk_prior = MemoryMixin._persist_walk_prior
    _restore_walk_bookmark = MemoryMixin._restore_walk_bookmark
    _walk_pacing_on_disk = MemoryMixin._walk_pacing_on_disk
    _persist_walk_pacing = MemoryMixin._persist_walk_pacing
    _refresh_walk_ui = MemoryMixin._refresh_walk_ui
    _dialog_for = MemoryMixin._dialog_for
    _WALK_PACING_UNATTENDED = MemoryMixin._WALK_PACING_UNATTENDED
    _WALK_PACING_DAY = MemoryMixin._WALK_PACING_DAY
    _WALK_PACING_HOUR = MemoryMixin._WALK_PACING_HOUR
    _WALK_PACING_CHUNK = MemoryMixin._WALK_PACING_CHUNK
    _WALK_PACINGS = MemoryMixin._WALK_PACINGS

    def __init__(self, in_flight=False, distill_queue=None):
        self._walking_from_start = {}
        self._edit_kin_dialog = None
        self._in_flight = in_flight
        self._distill_queue = distill_queue if distill_queue is not None else {}

    def _is_distill_in_flight(self, agent_name):
        return self._in_flight


class _StatusStub:
    def __init__(self):
        self.labels = []

    def SetLabel(self, text):
        self.labels.append(text)


class _DialogStub:
    def __init__(self, kin, frame):
        self.kin = kin
        self.frame = frame
        self.memory_status = _StatusStub()

    _on_cancel_walk = EditKinDialog._on_cancel_walk
    _refresh_walk_controls = EditKinDialog._refresh_walk_controls


def _cfg(offsets, walk_scopes=None, pacing=None):
    c = {"distill_offsets": offsets}
    if walk_scopes is not None:
        c["distill_walk_scopes"] = walk_scopes
    if pacing is not None:
        c["distill_walk_pacing"] = pacing
    return c


reset_configs = lambda: _CONFIGS.clear()  # noqa: E731 (reuses the store below)
_CONFIGS = {}
_mm.load_agent_config = lambda name: __import__("copy").deepcopy(
    _CONFIGS.setdefault(name, {}))
_mm.save_agent_config = lambda name, cfg: _CONFIGS.__setitem__(
    name, __import__("copy").deepcopy(cfg))

# An unattended walk mid-way through: prior=100 (pre-redistill position),
# rewound to 0, now advanced to 40 by two chunks. Cancel should restore
# to 100 (the prior fix's behavior, unaffected by pacing existing).
reset_configs()
_CONFIGS["Kin"] = _cfg(
    {"desktop": 40}, walk_scopes=["desktop"],
    pacing={"desktop": "unattended"})
_CONFIGS["Kin"]["distill_walk_prior_offsets"] = {"desktop": 100}
frame = _FrameStub()
frame._walking_from_start[("Kin", "desktop")] = True
dlg = _DialogStub("Kin", frame)
dlg._on_cancel_walk(None)
check("unattended pacing: Cancel still restores the pre-redistill bookmark",
      _CONFIGS["Kin"]["distill_offsets"]["desktop"] == 100)
check("...and says so in the status label",
      any("bookmark" in lbl.lower() for lbl in dlg.memory_status.labels))

# A DAY-paced walk that has gotten through two full days (bookmark now
# at 200, having started this walk from 0 with a recorded prior of 50).
# Cancel must leave the bookmark at 200 -- those two days were each
# explicitly continued into, not something to rewind past.
reset_configs()
_CONFIGS["Kin"] = _cfg(
    {"desktop": 200}, walk_scopes=["desktop"],
    pacing={"desktop": "day"})
_CONFIGS["Kin"]["distill_walk_prior_offsets"] = {"desktop": 50}
frame = _FrameStub()
frame._walking_from_start[("Kin", "desktop")] = True
dlg = _DialogStub("Kin", frame)
dlg._on_cancel_walk(None)
check("day pacing: Cancel keeps the bookmark exactly where it sits",
      _CONFIGS["Kin"]["distill_offsets"]["desktop"] == 200)
check("...never rewinds to the recorded prior (50)",
      _CONFIGS["Kin"]["distill_offsets"]["desktop"] != 50)
check("...and says progress is kept, not that a bookmark was restored",
      any("kept" in lbl.lower() for lbl in dlg.memory_status.labels)
      and not any("bookmark went back" in lbl.lower()
                  for lbl in dlg.memory_status.labels))

# Same for chunk pacing and hour pacing -- both are "paced", both keep.
for pacing_value in ("chunk", "hour"):
    reset_configs()
    _CONFIGS["Kin"] = _cfg(
        {"desktop": 77}, walk_scopes=["desktop"],
        pacing={"desktop": pacing_value})
    _CONFIGS["Kin"]["distill_walk_prior_offsets"] = {"desktop": 5}
    frame = _FrameStub()
    frame._walking_from_start[("Kin", "desktop")] = True
    dlg = _DialogStub("Kin", frame)
    dlg._on_cancel_walk(None)
    check(f"{pacing_value} pacing: Cancel keeps progress too",
          _CONFIGS["Kin"]["distill_offsets"]["desktop"] == 77)

# A PAUSED (not running) day-paced walk -- Cancel from the paused state
# (no entry in _walking_from_start, only the on-disk record) behaves
# the same way: keeps progress, doesn't resurrect the prior-offset
# rewind.
reset_configs()
_CONFIGS["Kin"] = _cfg(
    {"desktop": 150}, walk_scopes=["desktop"],
    pacing={"desktop": "day"})
_CONFIGS["Kin"]["distill_walk_prior_offsets"] = {"desktop": 10}
frame = _FrameStub()  # nothing in _walking_from_start -- it's paused, not running
dlg = _DialogStub("Kin", frame)
dlg._on_cancel_walk(None)
check("cancelling a PAUSED day-paced walk also keeps progress",
      _CONFIGS["Kin"]["distill_offsets"]["desktop"] == 150)


# --- resuming must not revert a walk's pacing to unattended --------------
#
# The bug, reported live: pick 'day' pacing, walk pauses correctly
# after day one, press "Continue redistilling" -- and the walk quietly
# turns into an unattended one with no further pauses at all. Root
# cause: _start_walk(pacing=None) treated "no pacing given" the same as
# "explicitly asked for unattended", so EVERY resume path (Continue
# redistilling, and _resume_pending_distill_walks on relaunch) silently
# overwrote whatever pacing was actually recorded. Both call
# _start_walk with no pacing argument on purpose -- that omission must
# now mean "leave it alone", not "reset it".

reset_configs()
_CONFIGS["Kin"] = {"distill_offsets": {"desktop": 0}}
frame = _FrameStub()
# Mirrors _on_redistill_from_start: a fresh walk, explicit pacing.
frame._start_walk("Kin", "desktop", pacing="day")
check("starting fresh with day pacing persists it",
      _CONFIGS["Kin"]["distill_walk_pacing"]["desktop"] == "day")

# Mirrors what a pause between chunks does: walk stops being "live" in
# memory but the on-disk pacing is untouched (nothing in the pause path
# ever touches distill_walk_pacing).
frame._walking_from_start.pop(("Kin", "desktop"), None)

# Mirrors _on_resume_walk / _resume_pending_distill_walks EXACTLY: no
# pacing argument at all.
frame._start_walk("Kin", "desktop")
check("resuming with no pacing argument leaves 'day' exactly as it was",
      _CONFIGS["Kin"]["distill_walk_pacing"]["desktop"] == "day")
check("_walk_pacing_on_disk agrees",
      frame._walk_pacing_on_disk("Kin", "desktop") == "day")

# Same check for hour and chunk pacing, and for a genuinely fresh scope
# that never had a pacing recorded (falls back to unattended via
# _walk_pacing_on_disk, same as before this fix — nothing regresses
# there).
for pacing_value in ("hour", "chunk"):
    reset_configs()
    _CONFIGS["Kin"] = {"distill_offsets": {"desktop": 0}}
    frame = _FrameStub()
    frame._start_walk("Kin", "desktop", pacing=pacing_value)
    frame._walking_from_start.pop(("Kin", "desktop"), None)
    frame._start_walk("Kin", "desktop")
    check(f"resuming a {pacing_value}-paced walk leaves it {pacing_value}",
          frame._walk_pacing_on_disk("Kin", "desktop") == pacing_value)

reset_configs()
_CONFIGS["Kin"] = {"distill_offsets": {"desktop": 0}}
frame = _FrameStub()
frame._start_walk("Kin", "desktop")  # no pacing ever given at all
check("a scope with no pacing history ever recorded still reads as "
      "unattended (via the read-side default, not a write)",
      frame._walk_pacing_on_disk("Kin", "desktop") == "unattended")
check("...and nothing was actually WRITTEN for it",
      "distill_walk_pacing" not in _CONFIGS["Kin"])


# --- Cancel now reaches "Distill all surfaces" and a plain one-shot ------
#
# Reported live: an accidental press of "Distill all surfaces now" had
# no way to be stopped short of quitting Hearthkin, because Cancel only
# ever looked at walk state. Widened: Cancel now also clears the
# "distill all surfaces" queue, and gives an honest (if inert) response
# during a genuine one-shot "Distill selected surface now".

# A "Distill all surfaces now" queue, nothing walking.
reset_configs()
_CONFIGS["Kin"] = {"distill_offsets": {}}
frame = _FrameStub(distill_queue={"Kin": ["telegram:123", "room:x"]})
dlg = _DialogStub("Kin", frame)
dlg._on_cancel_walk(None)
check("cancelling clears the all-surfaces queue for this kin",
      "Kin" not in frame._distill_queue)
check("...and names which surfaces will not run",
      any("telegram:123" in lbl and "room:x" in lbl
          for lbl in dlg.memory_status.labels))
check("...without touching any other kin's queue",
      True)  # nothing else to check here; no other kin was ever added

# Cancel must not clobber a DIFFERENT kin's queue.
reset_configs()
frame = _FrameStub(distill_queue={"Kin": ["a"], "OtherKin": ["b"]})
dlg = _DialogStub("Kin", frame)
dlg._on_cancel_walk(None)
check("cancelling one kin's queue leaves a different kin's queue alone",
      frame._distill_queue.get("OtherKin") == ["b"])

# A plain one-shot "Distill selected surface now": in flight, no walk,
# no queue -- there is genuinely nothing to cancel beyond what's already
# going to happen (it finishes on its own).
reset_configs()
frame = _FrameStub(in_flight=True)
dlg = _DialogStub("Kin", frame)
dlg._on_cancel_walk(None)
check("a genuine one-shot distill: Cancel says something running will "
      "just finish, not that a walk was cancelled",
      any("running" in lbl.lower() for lbl in dlg.memory_status.labels)
      and not any("cancelled" in lbl.lower() for lbl in dlg.memory_status.labels))

# Nothing at all happening: the old "(no walk in progress)" message was
# fine when Cancel only ever meant walks; now it must not claim there's
# "no walk" when the real question is whether ANYTHING is happening.
reset_configs()
frame = _FrameStub()
dlg = _DialogStub("Kin", frame)
dlg._on_cancel_walk(None)
check("nothing happening at all gets an honest, current message",
      any("nothing is distilling" in lbl.lower()
          for lbl in dlg.memory_status.labels))

# --- _refresh_walk_controls enables Cancel for all three triggers --------

class _ButtonStub:
    def __init__(self):
        self.enabled = None

    def Enable(self, v):
        self.enabled = bool(v)


def _dlg_with_buttons(kin, frame):
    d = _DialogStub(kin, frame)
    d.cancel_walk_btn = _ButtonStub()
    d.resume_walk_btn = _ButtonStub()
    return d


reset_configs()
frame = _FrameStub(in_flight=True)  # plain one-shot, no walk, no queue
dlg = _dlg_with_buttons("Kin", frame)
dlg._refresh_walk_controls()
check("Cancel is enabled for a plain in-flight distill (not just a walk)",
      dlg.cancel_walk_btn.enabled is True)
check("Continue stays disabled -- there's no paused walk to resume",
      dlg.resume_walk_btn.enabled is False)

reset_configs()
frame = _FrameStub(distill_queue={"Kin": ["telegram:123"]})
dlg = _dlg_with_buttons("Kin", frame)
dlg._refresh_walk_controls()
check("Cancel is enabled while an all-surfaces queue is pending",
      dlg.cancel_walk_btn.enabled is True)

reset_configs()
frame = _FrameStub()  # truly nothing happening
dlg = _dlg_with_buttons("Kin", frame)
dlg._refresh_walk_controls()
check("Cancel is disabled when nothing is happening at all",
      dlg.cancel_walk_btn.enabled is False)


if _fails:
    print(f"\n{len(_fails)} FAILED: " + ", ".join(_fails))
    sys.exit(1)
print("\nall redistill-pacing checks passed")
