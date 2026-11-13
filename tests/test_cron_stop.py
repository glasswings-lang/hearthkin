# SPDX-License-Identifier: CC0-1.0
"""Stopping a scheduled wake-up that runs on its own (12 Nov 2026).

A wake-up that runs while Hearthkin is closed, or open but not sending
wake-ups into the chat, is a separate process. Nothing could stop it, not
even Ctrl+Shift+X, and it had no tool-call limit. Now:

  - each running wake-up leaves a marker, so the app can name whose it stops;
  - Ctrl+Shift+X writes a stop that every wake-up already running obeys
    before its next tool call; one that starts afterwards is unaffected;
  - a stopped wake-up sends nothing half-finished, logs why, notes it in the
    journal, and clears its marker;
  - the kin's "Most tool calls in one reply" limit applies.

Driven through the real main() with the model stubbed; nothing leaves the
machine.

Run: python tests/test_cron_stop.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="cronstop-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cron_helpers  # noqa: E402
import hearthkin_cron  # noqa: E402
import llm_backend  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# --- the markers --------------------------------------------------------
m = cron_helpers.mark_wakeup_running("Wren")
check("a running wake-up is listed by kin name",
      cron_helpers.list_running_wakeups() == ["Wren"])
cron_helpers.clear_wakeup_running(m)
check("...and gone once cleared", cron_helpers.list_running_wakeups() == [])

# A marker from a process that died is cleaned up, never reported.
dead = subprocess.Popen([sys.executable, "-c", "pass"])
dead.wait()
cron_helpers.mark_wakeup_running("Ghost", pid=dead.pid)
check("a marker left by a dead process isn't reported, and is removed",
      cron_helpers.list_running_wakeups() == []
      and not list(cron_helpers.running_wakeups_dir().glob("*.json")))

t0 = time.time()
check("no stop asked yet", not cron_helpers.wakeup_stop_requested(t0))
cron_helpers.request_wakeup_stop(now=t0 + 1)
check("a stop asked after a wake-up started reaches it",
      cron_helpers.wakeup_stop_requested(t0))
check("...but not a wake-up that starts after the stop",
      not cron_helpers.wakeup_stop_requested(t0 + 2))

# --- a whole wake-up, stopped part-way ---------------------------------
cron_helpers.wakeup_stop_file().unlink()     # the stop set above was in the future
KIN = "Wren"
k = kin_dir(KIN)
k.mkdir(parents=True, exist_ok=True)
(k / "config.json").write_text(json.dumps({
    "model": "stub-model", "remote_max_tool_calls": 7,
    "cron_entries": [{"prompt": "Tend your notes.", "enabled": True,
                      "time": "08:00"}]}), encoding="utf-8")
(k / "tools.json").write_text(json.dumps({"enabled": ["note", "read_file"]}),
                              encoding="utf-8")
(k / "soul.md").write_text("A small kin.", encoding="utf-8")


class R:
    def __init__(self, content, stopped=False):
        self.content = content
        self.thinking = ""
        self.stopped = stopped
        self.messages_added = []
        self.tool_calls_made = []


seen = {}
delivered = []


def fake_loop(model, messages, **kw):
    seen["max_tool_calls"] = kw.get("max_tool_calls")
    seen["listed_mid_run"] = cron_helpers.list_running_wakeups()
    # Someone presses Ctrl+Shift+X while the kin is working...
    cron_helpers.request_wakeup_stop()
    # ...and the loop sees it before its next tool call.
    stop = kw.get("should_stop")
    seen["loop_saw_stop"] = bool(stop and stop())
    return R("I read the first note and was about to", stopped=seen["loop_saw_stop"])


real = (llm_backend.run_tool_loop, hearthkin_cron._maybe_post_telegram,
        hearthkin_cron._deliver_to_destinations,
        cron_helpers.lock_indicates_running)
llm_backend.run_tool_loop = fake_loop
hearthkin_cron._maybe_post_telegram = lambda *a, **k: delivered.append(a)
hearthkin_cron._deliver_to_destinations = lambda *a, **k: delivered.append(a)
cron_helpers.lock_indicates_running = lambda: False
time.sleep(0.01)
try:
    rc = hearthkin_cron.main(["--kin", KIN, "--entry-index", "0", "--run-now"])
finally:
    (llm_backend.run_tool_loop, hearthkin_cron._maybe_post_telegram,
     hearthkin_cron._deliver_to_destinations,
     cron_helpers.lock_indicates_running) = real

check("the wake-up was listed as running while it ran",
      seen.get("listed_mid_run") == [KIN])
check("the kin's tool-call limit reached the wake-up's tool loop",
      seen.get("max_tool_calls") == 7)
check("the tool loop was handed a stop it could see",
      seen.get("loop_saw_stop") is True)
check("a stopped wake-up ends cleanly", rc == 0)
check("...sends nothing half-finished anywhere", delivered == [])
log = cron_helpers.cron_error_log_path().read_text(encoding="utf-8")
check("...logs that the operator stopped it", "stopped by the operator" in log)
journal = cron_helpers.kin_journal_path(KIN)
check("...notes it in the journal",
      journal.exists() and "Stopped by the operator"
      in journal.read_text(encoding="utf-8"))
check("...and its running marker is gone",
      cron_helpers.list_running_wakeups() == [])

# A wake-up that starts AFTER a stop runs normally.
calls = []


def ok_loop(model, messages, **kw):
    calls.append(kw.get("should_stop")())
    return R("All tended.")


llm_backend.run_tool_loop = ok_loop
hearthkin_cron._maybe_post_telegram = lambda *a, **k: delivered.append(a)
cron_helpers.lock_indicates_running = lambda: False
time.sleep(0.01)
try:
    rc = hearthkin_cron.main(["--kin", KIN, "--entry-index", "0", "--run-now"])
finally:
    (llm_backend.run_tool_loop, hearthkin_cron._maybe_post_telegram,
     hearthkin_cron._deliver_to_destinations,
     cron_helpers.lock_indicates_running) = real
check("a wake-up that starts after an old stop isn't stopped by it",
      calls == [False] and rc == 0 and len(delivered) == 1)

# The app's Ctrl+Shift+X names running wake-ups and asks them to stop.
src = (ROOT / "frame" / "lifecycle_mixin.py").read_text(encoding="utf-8")
check("Ctrl+Shift+X reaches wake-ups",
      "list_running_wakeups" in src and "request_wakeup_stop" in src)

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_cron_stop: all checks passed")
