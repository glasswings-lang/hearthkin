# SPDX-License-Identifier: CC0-1.0
"""Guard test: the cron entry -> fire-times expansion.

`cron_helpers.cron_entry_fire_times(entry)` is the single place that turns one
cron entry into the list of HH:MM times it fires at. The scheduler
(schtasks_sync_kin), the cross-kin collision check, and the Settings UI all key
off it, so a silent change here would ripple to all three — a routine could
quietly stop firing, or fire at the wrong times, with no error.

Pins the three entry shapes (explicit times list / interval / legacy single
time), the normalisation (dedup + sort + zero-pad), and the edge cases
(missing bounds, empty window, garbage in). Also pins the composite task-name
scheme that lets one entry own several fire-time tasks.

Added 2026-07-09 alongside the multi-time-cron feature — the pure, bug-prone
logic worth pinning, per the "test each thing that bites" approach in the
resilience track.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cron_helpers import cron_entry_fire_times, schtasks_task_name  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def eq(label, got, want):
    check(f"{label}  (got {got!r}, want {want!r})", got == want)


def main():
    f = cron_entry_fire_times

    # --- explicit times list: normalise, de-dup, sort ---
    eq("times list normalises + sorts + dedups",
       f({"times": ["9:00", "21:00", "09:00", "15:00"]}),
       ["09:00", "15:00", "21:00"])
    eq("times list tolerates surrounding whitespace",
       f({"times": [" 9:00 ", "15:00"]}), ["09:00", "15:00"])
    eq("invalid times are dropped, valid ones kept",
       f({"times": ["09:00", "nope", "25:99", "15:00"]}),
       ["09:00", "15:00"])

    # --- legacy single time still works ---
    eq("legacy single time", f({"time": "08:00"}), ["08:00"])
    eq("legacy single time normalises", f({"time": "8:5"}), [])  # 8:5 invalid
    eq("legacy single time zero-pads hour", f({"time": "8:05"}), ["08:05"])

    # --- times takes precedence over a stale legacy time ---
    eq("times wins over legacy time",
       f({"times": ["10:00"], "time": "08:00"}), ["10:00"])

    # --- interval shape ---
    eq("interval every 180m in a window (inclusive of end)",
       f({"every_minutes": 180, "active_start": "09:00", "active_end": "21:00"}),
       ["09:00", "12:00", "15:00", "18:00", "21:00"])
    eq("interval hits the end boundary exactly",
       f({"every_minutes": 60, "active_start": "22:00", "active_end": "23:00"}),
       ["22:00", "23:00"])
    eq("interval with no bounds spans the whole day",
       f({"every_minutes": 720}), ["00:00", "12:00"])
    eq("interval with end before start yields nothing (no wrap-around)",
       f({"every_minutes": 60, "active_start": "23:00", "active_end": "01:00"}),
       [])
    eq("interval with a non-positive step yields nothing",
       f({"every_minutes": 0}), [])
    eq("interval with garbage step yields nothing",
       f({"every_minutes": "lots"}), [])

    # --- empty / malformed ---
    eq("empty dict", f({}), [])
    eq("None entry", f(None), [])
    eq("non-dict entry", f("09:00"), [])
    eq("empty times list falls through to nothing", f({"times": []}), [])

    # --- composite task names (one entry can own several fire-time tasks) ---
    eq("single-time task name (legacy shape)",
       schtasks_task_name("Tarn", 0), "Hearthkin-Tarn-Cron-0")
    eq("per-time task name carries the time index",
       schtasks_task_name("Tarn", 0, 2), "Hearthkin-Tarn-Cron-0-2")
    # A 3-time entry's names must be distinct so schtasks doesn't collapse them.
    _names = [schtasks_task_name("Tarn", 0, i)
              for i in range(len(f({"times": ["09:00", "15:00", "21:00"]})))]
    check("a 3-time entry produces 3 distinct task names",
          len(_names) == 3 and len(set(_names)) == 3)

    if _fails:
        print(f"\nFAILED {len(_fails)}: " + ", ".join(_fails))
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
