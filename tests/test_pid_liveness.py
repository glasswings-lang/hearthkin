# SPDX-License-Identifier: CC0-1.0
"""Guard test: "is this process still running?" must not believe a corpse.

Two things in Hearthkin ask that question about a process in a DIFFERENT
process, and both act on the answer:

  * the cron marker sweep (cron_running_kin) — "is a scheduled wake-up
    still working in the standalone subprocess?"
  * the run lock (lock_indicates_running) — "is Hearthkin already open?"

The Windows check was OpenProcess-succeeded == alive. That is not what
OpenProcess means. Windows keeps a terminated process's kernel object,
and therefore its PID, resolvable for as long as ANYONE still holds a
handle to it — and a parent, a debugger, or the Task Scheduler service
routinely does. So an exited process opens perfectly cleanly.

What that cost, observed 2026-07-28: a cron subprocess exited at 23:00
leaving its marker behind (the marker's own `finally` didn't run — a
kill or a sleep). The sweep exists precisely so that can't matter, and
it failed. For the next twenty-two hours the app believed that kin was
mid-wake-up: the confirm-on-close dialog nagged on every quit, the
repeating "still working" cue sounded every thirty seconds, and
heartbeats stood down machine-wide because something looked busy. The
symptom read as "a disabled task keeps trying to fire", which is the
worst kind of wrong — it pointed at the scheduler, and the scheduler was
innocent.

The reproduction below is the real thing rather than a mock: spawn a
child, wait for it to exit, and hold its handle open (which is exactly
what subprocess.Popen does). Before the fix this reported the dead child
as running.

Run: python tests/test_pid_liveness.py
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cron_helpers as ch  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# --- the honest cases ---------------------------------------------------

check("this very process reports as running",
      ch.pid_is_running(os.getpid()) is True)
check("a pid that was never issued reports as not running",
      ch.pid_is_running(999999) is False)
check("nonsense pids are not running, and don't raise",
      not any(ch.pid_is_running(p)
              for p in (0, -1, None, "1234", 3.5, [])))

# --- the case that actually bit -----------------------------------------
#
# `proc` stays in scope for the whole check. That is the point: Popen
# holds an open handle to the child, so the kernel keeps the PID
# resolvable after it exits, and OpenProcess still succeeds on it.

proc = subprocess.Popen([sys.executable, "-c", "pass"])
proc.wait()
check("a child that has exited is not 'running' just because its "
      "handle is still open",
      ch.pid_is_running(proc.pid) is False)

# The sweep is the thing that was actually broken, so exercise it end to
# end rather than trusting that it calls the helper.
_real_running_dir = ch.running_dir


def _sweep_with(pid):
    """Write a marker for `pid` into a scratch dir and run the real sweep
    over it. Returns (result, marker_still_there)."""
    import pathlib
    import tempfile
    d = pathlib.Path(tempfile.mkdtemp(prefix="hearthkin-marker-test-"))
    ch.running_dir = lambda: d
    try:
        (d / f"Tarn-{pid}.marker").write_text(
            f"Tarn\n23:00\n{pid}\n", encoding="utf-8")
        got = ch.cron_running_kin()
        return got, list(d.glob("*.marker"))
    finally:
        ch.running_dir = _real_running_dir


got, left = _sweep_with(proc.pid)
check("a marker left by an exited process is not reported as work",
      got == [])
check("...and the stale marker is swept off disk", left == [])

got, left = _sweep_with(os.getpid())
check("a marker for a live process IS reported",
      got == [("Tarn", "23:00")])
check("...and its marker is left alone", len(left) == 1)

if _fails:
    print("\n%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
    sys.exit(1)
print("\nall pid-liveness checks passed")
