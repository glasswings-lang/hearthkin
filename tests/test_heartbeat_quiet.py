# SPDX-License-Identifier: CC0-1.0
"""Guard test: heartbeats wait for a quiet machine.

A heartbeat is the least urgent thing Hearthkin does — a kin being offered the
chance to speak up unprompted, free to stay silent. It costs exactly as much as
a real turn: a full prompt prefill, ~20,000 tokens on a mature kin.

On a host with few context slots that prefill evicts whatever cached context is
resident, which in practice is the conversation a person is in the middle of.
Their next reply then goes from about four seconds to about four minutes, and
nothing anywhere tells them why. Measured on the install this was written for:
heartbeats were 24% of all model calls and background work of all kinds was
65%. The machine spent most of its capacity talking to itself while a person
waited to be answered.

`_maybe_fire_heartbeats` previously gated only on the app closing, a scan
throttle, per-kin enablement, an interval and active hours. Nothing about what
the machine was doing. This pins the gate that fixes that:

  * it's MACHINE-WIDE, not per-kin — a heartbeat for one kin evicts a
    conversation with another just as effectively;
  * it DEFERS rather than skips — a kin that was due stays due, so nobody
    silently loses their turn on a busy day;
  * a pending tool approval counts as busy, deliberately, even though the
    machine is idle while it waits;
  * and it fails OPEN — a fault in the busy check must not silently disable
    heartbeats, because a feature that stops working quietly is worse than one
    that fires at an awkward moment.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from frame.cron_exec_mixin import CronExecMixin  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


class Frame:
    """Only what _maybe_fire_heartbeats touches."""

    _maybe_fire_heartbeats = CronExecMixin._maybe_fire_heartbeats
    _log_heartbeat_deferred = CronExecMixin._log_heartbeat_deferred
    # Real implementation — the active-hours window is a separate gate and
    # shouldn't be stubbed away, or this test would pass while that one broke.
    _within_active_hours = CronExecMixin._within_active_hours
    # Referenced as a thread target; the fake thread never calls it.
    _heartbeat_worker = None

    def __init__(self, busy=(), raises=False):
        self._closing = False
        self._heartbeat_last_scan = 0        # 0 = a scan is due
        self._heartbeat_last = {}
        self._busy = list(busy)
        self._raises = raises
        self.spawned = []
        self.deferrals = []

    def _work_in_flight(self):
        if self._raises:
            raise RuntimeError("busy-check exploded")
        return list(self._busy)

    def _log_heartbeat_deferred(self, busy):   # noqa: F811 - capture, don't write
        self.deferrals.append(list(busy))


# list_agents / load_agent_config / threading live in the mixin's module
# namespace; swap them so nothing real is read or spawned.
import frame.cron_exec_mixin as mod  # noqa: E402

_real = (mod.list_agents, mod.load_agent_config, mod.threading)


class _FakeThread:
    def __init__(self, target=None, args=(), daemon=None):
        self.args = args

    def start(self):
        _spawned.append(self.args[0])


_spawned = []
mod.list_agents = lambda: ["Opal", "hollis"]
mod.load_agent_config = lambda k: {"heartbeat": {"enabled": True, "every_minutes": 1}}


class _FakeThreading:
    Thread = _FakeThread


mod.threading = _FakeThreading

try:
    # --- quiet machine: heartbeats go ------------------------------------
    _spawned.clear()
    f = Frame(busy=[])
    f._maybe_fire_heartbeats()
    check("on a quiet machine, due heartbeats fire", sorted(_spawned) == ["Opal", "hollis"])
    check("...and nothing is logged as deferred", f.deferrals == [])

    # --- busy machine: heartbeats wait -----------------------------------
    _spawned.clear()
    f = Frame(busy=["Bracken is part-way through a reply in the main window"])
    f._maybe_fire_heartbeats()
    check("a reply in flight stops every heartbeat", _spawned == [])
    check("...the stand-down is logged, so it isn't a silent no-op",
          len(f.deferrals) == 1 and "Bracken" in f.deferrals[0][0])
    check("...and no kin is marked as having had its turn (deferred, not skipped)",
          f._heartbeat_last == {})

    # The gate is machine-wide: work belonging to ANY kin holds everyone.
    _spawned.clear()
    f = Frame(busy=["hollis is part-way through a reply to SpeakerSeven on Telegram"])
    f._maybe_fire_heartbeats()
    check("one kin's conversation defers a DIFFERENT kin's heartbeat", _spawned == [])

    # --- a pending approval counts as busy, deliberately ------------------
    _spawned.clear()
    f = Frame(busy=["1 tool approval waiting on your answer"])
    f._maybe_fire_heartbeats()
    check("a kin blocked awaiting your approval defers heartbeats too",
          _spawned == [])

    # --- deferral is not a lost turn -------------------------------------
    _spawned.clear()
    f = Frame(busy=["something in flight"])
    f._maybe_fire_heartbeats()
    check("busy scan spawns nothing", _spawned == [])
    f._busy = []
    f._heartbeat_last_scan = 0          # next scan comes round
    f._maybe_fire_heartbeats()
    check("...and the moment it goes quiet, the deferred heartbeats run",
          sorted(_spawned) == ["Opal", "hollis"])

    # --- fails open -------------------------------------------------------
    _spawned.clear()
    f = Frame(raises=True)
    f._maybe_fire_heartbeats()
    check("a busy-check that raises must not silently disable heartbeats",
          sorted(_spawned) == ["Opal", "hollis"])

    # --- closing still wins ----------------------------------------------
    _spawned.clear()
    f = Frame(busy=[])
    f._closing = True
    f._maybe_fire_heartbeats()
    check("shutdown still short-circuits before anything else", _spawned == [])

    # --- the throttle is untouched ---------------------------------------
    _spawned.clear()
    f = Frame(busy=[])
    f._heartbeat_last_scan = time.time()   # scanned a moment ago
    f._maybe_fire_heartbeats()
    check("the once-a-minute scan throttle still applies", _spawned == [])
finally:
    mod.list_agents, mod.load_agent_config, mod.threading = _real


print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_heartbeat_quiet: all checks passed")
