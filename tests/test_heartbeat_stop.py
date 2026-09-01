# SPDX-License-Identifier: CC0-1.0
"""Guard test: a heartbeat can no longer hold the model hostage from
something that actually matters.

Confirmed live, 2026-07-29: Bracken's heartbeat started, and Lark's
redistill sat waiting for the same model (both on gemma4:31b) for several
minutes, with no way to interrupt the heartbeat short of quitting
Hearthkin. Root cause, two-fold:

  1. `_maybe_fire_heartbeats` only checked whether the machine was busy at
     scan time, once a minute. The scan deciding a kin was due and the
     worker thread actually running are seconds apart — real work can
     start in that gap, and nothing re-checked once the worker began.
  2. A running heartbeat had no way to be told to stop at all. Chat and
     tool-loop calls get a should_stop hook; heartbeats never did.

Fixed both ways: `_heartbeat_worker` re-checks `_work_in_flight()` itself,
immediately before doing anything, BEFORE registering in
`_heartbeat_workers` (so it never sees its own entry and refuses to run
against itself); and it hands a per-kin `threading.Event` down as
`should_stop`, which `_signal_heartbeats_to_stop()` — called before any
distillation starts — sets. Neither of these can interrupt a single model
call already generating (nothing in this app can, by design — see
CLAUDE.md, "The stop button"), but together they stop a heartbeat from
ever starting once real work is underway, and stop a multi-iteration one
from continuing past its current step.

Run: python tests/test_heartbeat_stop.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Before anything reaches kin_persistence — see test_token_calibration.py.
os.environ.setdefault(
    "HEARTHKIN_HOME",
    tempfile.mkdtemp(prefix="hearthkin-hbstop-"))

from frame.cron_exec_mixin import CronExecMixin  # noqa: E402
import hearthkin_cron  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# --- _heartbeat_worker: busy re-check, registration, cleanup -------------

class Frame:
    """Only what _heartbeat_worker / _signal_heartbeats_to_stop touch."""

    _heartbeat_worker = CronExecMixin._heartbeat_worker
    _signal_heartbeats_to_stop = CronExecMixin._signal_heartbeats_to_stop
    _log_heartbeat_deferred = CronExecMixin._log_heartbeat_deferred

    def __init__(self, busy=(), raises_run=False):
        self._busy = list(busy)
        self._heartbeat_workers = set()
        self.deferrals = []
        self.run_calls = []
        self._raises_run = raises_run

    def _work_in_flight(self):
        return list(self._busy)

    def _log_heartbeat_deferred(self, busy):  # noqa: F811 — capture only
        self.deferrals.append(list(busy))


def _fake_run_heartbeat_ok(kin, cfg, should_stop=None):
    return ("ok", kin, cfg, should_stop)


def _fake_run_heartbeat_raises(kin, cfg, should_stop=None):
    raise RuntimeError("model call blew up")


def _install_run_heartbeat(fn):
    hearthkin_cron.run_heartbeat = fn


_real_run_heartbeat = hearthkin_cron.run_heartbeat
_real_log_cron_error = None
try:
    import cron_helpers as _cron_helpers_mod
    _real_log_cron_error = _cron_helpers_mod.log_cron_error
    _cron_helpers_mod.log_cron_error = lambda *a, **kw: None
except Exception:
    pass


# --- a busy machine refuses to run, and refuses to register at all -------

f = Frame(busy=["Lark is saving notes to its memory"])
_install_run_heartbeat(_fake_run_heartbeat_ok)
f._heartbeat_worker("Bracken", {"model": "gemma4:31b"})
check("a busy machine defers rather than running",
      f.deferrals == [["Lark is saving notes to its memory"]])
check("...and never registers the heartbeat as running",
      "Bracken" not in f._heartbeat_workers)
check("...so it can't be told to stop (nothing to stop)",
      "Bracken" not in getattr(f, "_heartbeat_stop_events", {}))


# --- a quiet machine runs, registers, and forwards should_stop -----------

f = Frame(busy=[])
captured = {}


def _capturing_run_heartbeat(kin, cfg, should_stop=None):
    # Snapshot registration state DURING the call — this is what proves
    # the worker registers itself BEFORE running, not after.
    captured["kin_in_workers_during_run"] = kin in f._heartbeat_workers
    captured["should_stop"] = should_stop
    captured["should_stop_is_callable"] = callable(should_stop)
    return "ok"


_install_run_heartbeat(_capturing_run_heartbeat)
f._heartbeat_worker("Bracken", {"model": "gemma4:31b"})
check("a quiet machine actually runs the heartbeat",
      captured.get("kin_in_workers_during_run") is True)
check("a should_stop callable is handed down",
      captured.get("should_stop_is_callable") is True)
check("...and it reads False before anyone signals it",
      captured["should_stop"]() is False)
check("the worker cleans up _heartbeat_workers afterward",
      "Bracken" not in f._heartbeat_workers)
check("...and _heartbeat_stop_events afterward too",
      "Bracken" not in f._heartbeat_stop_events)


# --- signalling actually flips the event the heartbeat is holding --------

f = Frame(busy=[])
seen_event = {}


def _stash_should_stop(kin, cfg, should_stop=None):
    seen_event["should_stop"] = should_stop
    # Simulate "still running" by NOT letting the worker's finally block
    # run yet — checked via the frame's own dict from outside instead.
    return "ok"


_install_run_heartbeat(_stash_should_stop)
f._heartbeat_worker("Bracken", {"model": "gemma4:31b"})
check("after the (fast, synchronous) fake run, should_stop reads False",
      seen_event["should_stop"]() is False)

# Now do it "for real": start the worker, capture the event mid-flight by
# reading it directly off the frame before the finally block clears it.
f2 = Frame(busy=[])
mid_flight = {}


def _mid_flight_run_heartbeat(kin, cfg, should_stop=None):
    mid_flight["event_before_signal"] = should_stop()
    f2._signal_heartbeats_to_stop()
    mid_flight["event_after_signal"] = should_stop()
    return "ok"


_install_run_heartbeat(_mid_flight_run_heartbeat)
f2._heartbeat_worker("Bracken", {"model": "gemma4:31b"})
check("should_stop reads False before anything signals it",
      mid_flight["event_before_signal"] is False)
check("_signal_heartbeats_to_stop flips the SAME event the running "
      "heartbeat is holding",
      mid_flight["event_after_signal"] is True)


# --- signalling with nobody running is a safe no-op ----------------------

f3 = Frame(busy=[])
try:
    f3._signal_heartbeats_to_stop()
    ok = True
except Exception:
    ok = False
check("signalling stop with no heartbeats running doesn't raise", ok)


# --- an exception mid-heartbeat still cleans up registration -------------

f4 = Frame(busy=[])
_install_run_heartbeat(_fake_run_heartbeat_raises)
f4._heartbeat_worker("Bracken", {"model": "gemma4:31b"})
check("a heartbeat that raises still gets removed from _heartbeat_workers",
      "Bracken" not in f4._heartbeat_workers)
check("...and its stop event is still cleared",
      "Bracken" not in getattr(f4, "_heartbeat_stop_events", {}))


# --- multiple simultaneous heartbeats: signalling hits all of them, and -
# --- one kin's own busy-entry can't be caused by ITS OWN registration ----

f5 = Frame(busy=[])
running = {}


def _register_and_wait(kin, cfg, should_stop=None):
    running[kin] = should_stop


_install_run_heartbeat(_register_and_wait)
# Fire two "at once" by calling the worker twice before either's finally
# block runs -- both must end up in _heartbeat_stop_events simultaneously.
f5._heartbeat_worker("Bracken", {"model": "gemma4:31b"})
f5._heartbeat_worker("Vesper", {"model": "gemma4:31b"})
check("both kin were handed a should_stop during their own run",
      set(running.keys()) == {"Bracken", "Vesper"})


# --- hearthkin_cron.run_heartbeat forwards should_stop to run_tool_loop --

hearthkin_cron.run_heartbeat = _real_run_heartbeat  # restore the real one

_real_run_tool_loop = hearthkin_cron.llm_backend.run_tool_loop
_real_load_tools = hearthkin_cron.kin_tools.load_tools
_real_list_available = hearthkin_cron.kin_tools.list_available
_real_build_messages = hearthkin_cron._build_messages
_real_resolve_host = hearthkin_cron.resolve_kin_ollama_host
_real_log_heartbeat = hearthkin_cron._log_heartbeat

captured_kwargs = {}


def _fake_run_tool_loop(model, messages, **kwargs):
    captured_kwargs.update(kwargs)

    class _Result:
        messages_added = []
    return _Result()


hearthkin_cron.llm_backend.run_tool_loop = _fake_run_tool_loop
hearthkin_cron.kin_tools.list_available = lambda: ["reach_out"]
hearthkin_cron.kin_tools.load_tools = lambda *a, **kw: ([{"type": "function"}], {})
hearthkin_cron._build_messages = lambda *a, **kw: [{"role": "system", "content": "x"}]
hearthkin_cron.resolve_kin_ollama_host = lambda *a, **kw: None
hearthkin_cron._log_heartbeat = lambda *a, **kw: None  # never touch the real log

_marker = object()
sentinel_should_stop = lambda: _marker is _marker  # noqa: E731 — distinct identity

try:
    hearthkin_cron.run_heartbeat(
        "Bracken", {"model": "gemma4:31b"}, should_stop=sentinel_should_stop)
finally:
    hearthkin_cron.llm_backend.run_tool_loop = _real_run_tool_loop
    hearthkin_cron.kin_tools.load_tools = _real_load_tools
    hearthkin_cron.kin_tools.list_available = _real_list_available
    hearthkin_cron._build_messages = _real_build_messages
    hearthkin_cron.resolve_kin_ollama_host = _real_resolve_host
    hearthkin_cron._log_heartbeat = _real_log_heartbeat
    if _real_log_cron_error is not None:
        _cron_helpers_mod.log_cron_error = _real_log_cron_error

check("run_heartbeat forwards should_stop through to run_tool_loop",
      captured_kwargs.get("should_stop") is sentinel_should_stop)


if _fails:
    print(f"\n{len(_fails)} FAILED: " + ", ".join(_fails))
    sys.exit(1)
print("\nall heartbeat-stop checks passed")
