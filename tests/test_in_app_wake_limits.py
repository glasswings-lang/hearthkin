# SPDX-License-Identifier: CC0-1.0
"""A wake-up the app runs itself gets the same tool limit and stop as one run
in its own process (20 Nov 2026 audit).

The limit and the stop were set only in the subprocess's main(), so a
wake-up run on one of the app's worker threads had no tool-call limit and
Ctrl+Shift+X couldn't reach it. Now in_app_wake_limits gives the thread
both, and takes them away again after.

Run: python tests/test_in_app_wake_limits.py
"""

import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="wakelimits-", dir=(os.environ.get("HEARTHKIN_HOME") or None))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import hearthkin_cron  # noqa: E402
import llm_backend  # noqa: E402
import cron_helpers  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


seen = {}


class _R:
    stopped = False
    content = "ok"


def fake_loop(*a, **k):
    seen.update(k)
    return _R()


real = llm_backend.run_tool_loop
llm_backend.run_tool_loop = fake_loop
try:
    hearthkin_cron._cron_tool_loop("m", [], [], {})
    check("outside a wake-up: no limit is forced", "max_tool_calls" not in seen)
    seen.clear()
    with hearthkin_cron.in_app_wake_limits({"remote_max_tool_calls": 7}):
        hearthkin_cron._cron_tool_loop("m", [], [], {})
    check("an in-app wake-up gets the kin's tool-call limit",
          seen.get("max_tool_calls") == 7)
    check("...and a stop check", callable(seen.get("should_stop")))
    seen.clear()
    hearthkin_cron._cron_tool_loop("m", [], [], {})
    check("...both gone again afterwards", "max_tool_calls" not in seen)

    real_req = cron_helpers.wakeup_stop_requested
    cron_helpers.wakeup_stop_requested = lambda started: True
    try:
        raised = False
        with hearthkin_cron.in_app_wake_limits({}):
            try:
                hearthkin_cron._cron_tool_loop("m", [], [], {})
            except hearthkin_cron.WakeStopped:
                raised = True
        check("a stop request reaches an in-app wake-up", raised)
    finally:
        cron_helpers.wakeup_stop_requested = real_req

    other = {}

    def on_other_thread():
        hearthkin_cron._cron_tool_loop("m", [], [], {})
        other.update(seen)
    with hearthkin_cron.in_app_wake_limits({"remote_max_tool_calls": 3}):
        seen.clear()
        t = threading.Thread(target=on_other_thread)
        t.start()
        t.join()
    check("one kin's wake-up limits don't reach another thread",
          "max_tool_calls" not in other)
finally:
    llm_backend.run_tool_loop = real

src = (ROOT / "frame" / "cron_exec_mixin.py").read_text(encoding="utf-8")
check("the app's wake-up worker uses it",
      "with hearthkin_cron.in_app_wake_limits(cfg):" in src)

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_in_app_wake_limits: all checks passed")
