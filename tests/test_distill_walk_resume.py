# SPDX-License-Identifier: CC0-1.0
"""Guard test: a "redistill from start" survives being left alone.

Redistilling a kin's whole history runs for a long time — that is the
entire nature of it. So the one thing it must tolerate is nobody
watching: closing the Settings window, closing Hearthkin, going to bed.

It did not. The "a redistill is running" flag lived only in memory, so
quitting ended it permanently and said nothing; the progress survived on
disk, but nothing continued it and the only button on the Memory tab
reset the bookmark to zero. A chunk that errored ended it the same way,
with the only notice a line in the Activity field that isn't spoken and
is replaced four seconds later. And if an ordinary auto-distillation on
one of the kin's other surfaces slipped into the gap between chunks, the
next chunk hit the "already distilling" guard and returned silently —
flag still set, chain dead, and the Memory tab then refusing to start a
new redistill because one was supposedly still running.

All three looked identical from outside: no progress, no explanation.
The honest way to finish a long redistill was to never close the app,
and the obvious way to continue threw away everything done so far, so
one kin's history got walked from the beginning several times over.

These checks pin the three fixes: interruptions PAUSE (state on disk,
resumable) rather than dying, a collision WAITS instead of giving up,
and anything that stops the chain is SAID OUT LOUD.

Run: python tests/test_distill_walk_resume.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Before anything reaches kin_persistence — see test_token_calibration.py.
os.environ.setdefault(
    "HEARTHKIN_HOME",
    tempfile.mkdtemp(prefix="hearthkin-walktest-"))

import frame.memory_mixin as _mm  # noqa: E402
from frame.memory_mixin import MemoryMixin  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# --- stand-ins ----------------------------------------------------------
#
# The config store is a plain dict, so "did this survive a quit?" is
# answerable by throwing the frame away and building a new one against
# the same store — which is exactly what a restart is.

_CONFIGS = {}


def _fake_load_agent_config(name):
    import copy
    return copy.deepcopy(_CONFIGS.setdefault(name, {}))


def _fake_save_agent_config(name, cfg):
    import copy
    _CONFIGS[name] = copy.deepcopy(cfg)


class _FakeCallLater:
    """Records what would have been scheduled instead of scheduling it,
    so a test can assert "it scheduled another attempt" without waiting
    real seconds for it."""

    def __init__(self):
        self.calls = []

    def __call__(self, millis, fn, *args):
        self.calls.append((millis, fn, args))
        return self


class _FakeWx:
    def __init__(self):
        self.CallLater = _FakeCallLater()


class Frame:
    """Only what the walk-state methods touch."""

    _walk_scopes_on_disk = MemoryMixin._walk_scopes_on_disk
    _persist_walk = MemoryMixin._persist_walk
    _start_walk = MemoryMixin._start_walk
    _end_walk = MemoryMixin._end_walk
    _walk_prior_offset = MemoryMixin._walk_prior_offset
    _persist_walk_prior = MemoryMixin._persist_walk_prior
    _restore_walk_bookmark = MemoryMixin._restore_walk_bookmark
    _walk_pacing_on_disk = MemoryMixin._walk_pacing_on_disk
    _persist_walk_pacing = MemoryMixin._persist_walk_pacing
    _walk_boundary_ts = MemoryMixin._walk_boundary_ts
    _format_walk_pause_when = MemoryMixin._format_walk_pause_when
    _WALK_PACING_UNATTENDED = MemoryMixin._WALK_PACING_UNATTENDED
    _WALK_PACING_DAY = MemoryMixin._WALK_PACING_DAY
    _WALK_PACING_HOUR = MemoryMixin._WALK_PACING_HOUR
    _WALK_PACING_CHUNK = MemoryMixin._WALK_PACING_CHUNK
    _WALK_PACINGS = MemoryMixin._WALK_PACINGS
    _refresh_walk_ui = MemoryMixin._refresh_walk_ui
    _announce_problem = MemoryMixin._announce_problem
    _announce_walk_complete = MemoryMixin._announce_walk_complete
    _dialog_for = MemoryMixin._dialog_for
    _is_distill_in_flight = MemoryMixin._is_distill_in_flight
    _walk_next_chunk = MemoryMixin._walk_next_chunk

    def __init__(self, **kw):
        self._walking_from_start = {}
        self._distilling = {}
        self._edit_kin_dialog = None
        self._closing = False
        self.spoken = []
        self.sounded = 0
        self.kicked = []
        self._convo = []
        for k, v in kw.items():
            setattr(self, k, v)

    # The two output channels this code is judged on.
    def _set_status(self, msg, speak=False):
        if speak:
            self.spoken.append(msg)

    def _play_problem_alert(self):
        self.sounded += 1

    def _convo_for_distill_scope(self, agent_name, scope_key):
        return list(self._convo)

    def _kick_off_distillation(self, agent_name, conversation,
                               source_label="manual", scope_key="desktop"):
        self.kicked.append((agent_name, scope_key, source_label))
        # Stand in for a real chunk starting: takes the slot.
        self._distilling[agent_name] = _mm.time.time()


_real_load = _mm.load_agent_config
_real_save = _mm.save_agent_config
_real_wx = _mm.wx
_mm.load_agent_config = _fake_load_agent_config
_mm.save_agent_config = _fake_save_agent_config
_mm.wx = _FakeWx()


def reset():
    _CONFIGS.clear()
    _mm.wx.CallLater.calls.clear()


# --- a redistill outlives the process it started in ---------------------

reset()
f = Frame()
f._start_walk("Lark", "desktop")
check("starting a redistill records it on disk, not just in memory",
      _CONFIGS["Lark"]["distill_walk_scopes"] == ["desktop"])

# The restart. Nothing carries over but the config store.
f2 = Frame()
check("a fresh process still finds the unfinished redistill",
      f2._walk_scopes_on_disk("Lark") == ["desktop"])
check("...and knows it isn't running yet",
      f2._walking_from_start == {})

# --- finishing clears it; pausing does not ------------------------------

reset()
f = Frame()
f._start_walk("Lark", "desktop")
f._end_walk("Lark", "desktop")
check("a redistill that finishes leaves nothing behind to resume",
      _CONFIGS["Lark"]["distill_walk_scopes"] == [])

reset()
f = Frame()
f._start_walk("Lark", "desktop")
f._end_walk("Lark", "desktop", keep_on_disk=True)
check("one that's interrupted stays on disk, resumable",
      _CONFIGS["Lark"]["distill_walk_scopes"] == ["desktop"])
check("...and stops running in this process",
      f._walking_from_start == {})

# --- a collision waits instead of dying ---------------------------------

reset()
f = Frame(_convo=[{"role": "user", "content": "x"}] * 50)
f._start_walk("Lark", "desktop")
# Something else — an auto-distillation on one of Lark's Telegram
# surfaces, say — took the slot in the gap between chunks.
f._distilling["Lark"] = _mm.time.time()
f._walk_next_chunk("Lark", "desktop")
check("a busy slot doesn't start a chunk", f.kicked == [])
check("a busy slot schedules another attempt instead of giving up",
      len(_mm.wx.CallLater.calls) == 1)
check("...and the redistill is still considered running",
      f._walking_from_start.get(("Lark", "desktop")) is True)
check("waiting for the slot says nothing — it isn't news yet",
      f.spoken == [] and f.sounded == 0)

# The slot frees up; the next attempt gets through.
reset()
f._distilling.clear()
f._walk_next_chunk("Lark", "desktop", attempt=1)
check("once the slot frees up the chunk actually fires",
      len(f.kicked) == 1 and f.kicked[0][1] == "desktop")

# --- giving up is a pause, and is audible -------------------------------

reset()
f = Frame(_convo=[{"role": "user", "content": "x"}] * 50)
f._start_walk("Lark", "desktop")
f._distilling["Lark"] = _mm.time.time()
f._walk_next_chunk("Lark", "desktop", attempt=_mm._WALK_RETRY_MAX)
check("a slot that never frees eventually stops retrying",
      _mm.wx.CallLater.calls == [])
check("...and says so out loud", len(f.spoken) == 1)
check("...and sounds the problem cue", f.sounded == 1)
check("...naming the button that continues it",
      "Continue redistilling" in f.spoken[0])
check("...and keeps the progress, so it can be continued",
      _CONFIGS["Lark"]["distill_walk_scopes"] == ["desktop"])

# --- cancelled means cancelled ------------------------------------------

reset()
f = Frame(_convo=[{"role": "user", "content": "x"}] * 50)
f._start_walk("Lark", "desktop")
f._end_walk("Lark", "desktop")          # the Cancel button
f._walk_next_chunk("Lark", "desktop")   # a chunk that was already scheduled
check("a chunk scheduled before Cancel doesn't fire after it",
      f.kicked == [] and _mm.wx.CallLater.calls == [])

# --- quitting mid-chunk leaves it resumable, silently -------------------

reset()
f = Frame(_convo=[{"role": "user", "content": "x"}] * 50, _closing=True)
f._start_walk("Lark", "desktop")
f._walk_next_chunk("Lark", "desktop")
check("quitting doesn't start another chunk", f.kicked == [])
check("quitting says nothing — you're already leaving", f.spoken == [])
check("...but leaves the redistill on disk for next launch",
      _CONFIGS["Lark"]["distill_walk_scopes"] == ["desktop"])

# --- an empty surface ends the redistill rather than looping ------------

reset()
f = Frame(_convo=[])
f._start_walk("Lark", "desktop")
f._walk_next_chunk("Lark", "desktop")
check("a surface with nothing in it ends the redistill",
      _CONFIGS["Lark"]["distill_walk_scopes"] == [])
check("...and says it finished", any("finished" in m for m in f.spoken))

# --- cancelling must also undo the rewind -------------------------------
#
# Stopping the chain is only half of Cancel. A redistill rewinds the
# bookmark to 0; cancelling used to leave it there. That puts the kin tens
# of thousands of messages past memory_distill_at_pct, so the ORDINARY
# auto-distill trigger takes the same work over, from the same place,
# chunk after chunk — and no button reaches it, because it is not a walk.
# Reported as "I cancelled the distill but it's still going, it picked up
# from mid-chunk", which is exactly what it looks like from outside.

reset()
f = Frame(_convo=[{"role": "user", "content": "x"}] * 50)
_CONFIGS["Lark"] = {"distill_offsets": {"desktop": 12295}}
f._persist_walk_prior("Lark", "desktop", 12295)     # what the button records
_CONFIGS["Lark"]["distill_offsets"]["desktop"] = 0  # the rewind
f._start_walk("Lark", "desktop")
_CONFIGS["Lark"]["distill_offsets"]["desktop"] = 400   # a few chunks in

back_to = f._restore_walk_bookmark("Lark", "desktop")
check("cancelling puts the bookmark back where it was", back_to == 12295)
check("...on disk, not just as a return value",
      _CONFIGS["Lark"]["distill_offsets"]["desktop"] == 12295)
check("...and forgets it, so a second Cancel can't rewind again",
      f._restore_walk_bookmark("Lark", "desktop") is None)

# Progress that really happened is never thrown away.
reset()
f = Frame()
_CONFIGS["Lark"] = {"distill_offsets": {"desktop": 900}}
f._persist_walk_prior("Lark", "desktop", 500)
check("a bookmark already further along is left alone",
      f._restore_walk_bookmark("Lark", "desktop") is None)
check("...and keeps its real position",
      _CONFIGS["Lark"]["distill_offsets"]["desktop"] == 900)

# A redistill started before this existed has nothing recorded.
reset()
f = Frame()
_CONFIGS["Lark"] = {"distill_offsets": {"desktop": 77}}
check("no recorded position means Cancel changes nothing",
      f._restore_walk_bookmark("Lark", "desktop") is None)
check("...leaving the bookmark untouched",
      _CONFIGS["Lark"]["distill_offsets"]["desktop"] == 77)

# Junk in the config can't crash the Cancel path.
reset()
for junk in ("desktop", 7, None, {"desktop": "x"}, {"desktop": -3}):
    _CONFIGS["Lark"] = {"distill_walk_prior_offsets": junk,
                        "distill_offsets": {"desktop": 5}}
    check(f"hand-edited prior ({junk!r}) is ignored safely",
          Frame()._restore_walk_bookmark("Lark", "desktop") is None)

# An unwritable config must not break cancelling.
reset()
_CONFIGS["Lark"] = {"distill_offsets": {"desktop": 0}}
f = Frame()
f._persist_walk_prior("Lark", "desktop", 1000)
_mm.save_agent_config = _exploding_save_2 = lambda n, c: (_ for _ in ()).throw(
    OSError("disk is having a day"))
try:
    f._restore_walk_bookmark("Lark", "desktop")
    ok = True
except Exception:
    ok = False
_mm.save_agent_config = _fake_save_agent_config
check("a failed write while cancelling doesn't raise", ok)


# --- corrupt state can't take the Memory tab down -----------------------

reset()
for junk in ("desktop", {"desktop": True}, 7, None, ["", None, "desktop"]):
    _CONFIGS["Lark"] = {"distill_walk_scopes": junk}
    got = Frame()._walk_scopes_on_disk("Lark")
    check(f"hand-edited nonsense ({junk!r}) reads as a plain list",
          isinstance(got, list) and all(isinstance(s, str) and s for s in got))

# --- a failure to record the state must not break the redistill ---------

reset()


def _exploding_save(name, cfg):
    raise OSError("disk is having a day")


_mm.save_agent_config = _exploding_save
f = Frame()
try:
    f._start_walk("Lark", "desktop")
    ok = True
except Exception:
    ok = False
_mm.save_agent_config = _fake_save_agent_config
check("an unwritable config doesn't stop the redistill running", ok)
check("...it just falls back to the old in-memory-only behaviour",
      f._walking_from_start.get(("Lark", "desktop")) is True)


# --- the slot watchdog must not libel a slow model ----------------------
#
# The slot used to be force-released after five minutes, on an operation
# that routinely runs longer than that: a local model reading a
# 10k-token bite can spend longer than five minutes in prefill alone. So
# the watchdog fired on healthy work, announced the run "stuck", and --
# far worse -- freed the slot, letting a SECOND distillation start on the
# same kin. Mid-redistill that means two chunks against the same
# un-advanced bookmark, digesting the same turns twice.

import threading as _threading  # noqa: E402


class SlotFrame(Frame):
    _clear_wedged_distill = MemoryMixin._clear_wedged_distill
    _release_distill_slot = MemoryMixin._release_distill_slot
    _register_distill_thread = MemoryMixin._register_distill_thread

    def __init__(self, **kw):
        super().__init__(**kw)
        self._distill_threads = {}
        self._distill_dead_since = {}


reset()
f = SlotFrame()
stop = _threading.Event()
th = _threading.Thread(target=stop.wait, daemon=True)
f._register_distill_thread("Lark", th)
th.start()
# Started an hour ago and still going -- exactly the case the old
# five-minute rule got wrong.
f._distilling["Lark"] = _mm.time.time() - 3600
check("a worker still running after an hour still holds the slot",
      f._is_distill_in_flight("Lark") is True)
stop.set()
th.join(timeout=5)
check("...and the moment it ends, a grace window covers its callback",
      f._is_distill_in_flight("Lark") is True)
# Pretend the grace window elapsed with no callback: genuinely wedged.
f._distill_dead_since["Lark"] = (
    _mm.time.time() - _mm._DISTILL_CALLBACK_GRACE_SECS - 1)
check("a worker that ended without reporting back does free the slot",
      f._is_distill_in_flight("Lark") is False)
check("...and its bookkeeping is cleared with it",
      "Lark" not in f._distilling and "Lark" not in f._distill_threads)

# The normal ending drops everything together.
reset()
f = SlotFrame()
f._distilling["Lark"] = _mm.time.time()
f._register_distill_thread("Lark", _threading.Thread(target=lambda: None))
f._release_distill_slot("Lark")
check("a normal finish leaves no slot and no thread behind",
      not f._distilling and not f._distill_threads
      and not f._distill_dead_since)

# No thread recorded: the fallback clock, at a length that doesn't accuse
# a slow machine.
reset()
f = SlotFrame()
f._distilling["Lark"] = _mm.time.time() - 600      # ten minutes
check("with no worker recorded, ten minutes is not yet suspicious",
      f._is_distill_in_flight("Lark") is True)
f._distilling["Lark"] = _mm.time.time() - _mm._DISTILL_WATCHDOG_SECS - 1
check("...but the fallback does eventually let go",
      f._is_distill_in_flight("Lark") is False)

# --- restore ------------------------------------------------------------

_mm.load_agent_config = _real_load
_mm.save_agent_config = _real_save
_mm.wx = _real_wx

if _fails:
    print("\n%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
    sys.exit(1)
print("\nall redistill-resume checks passed")
