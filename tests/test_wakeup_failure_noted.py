# SPDX-License-Identifier: CC0-1.0
"""A failed wake-up's message says its prompt is in the journal only when it
is (24 Nov 2026, the accuracy audit).

The phone message for a wake-up that didn't complete said "Its prompt is
noted in {kin}'s journal for today". On the active-kin hang and error paths
nothing wrote that line, and the journal line that the isolated path writes
points at cron_errors.log, which those paths didn't write either. Now the
notice writes both, and only promises the journal line when it landed.

Run: python tests/test_wakeup_failure_noted.py
"""

import datetime
import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="wakefail-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cron_helpers  # noqa: E402
import kin_persistence as kp  # noqa: E402
from frame.chat_stream_mixin import ChatStreamMixin  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Bracken"
kp.agent_dir(KIN).mkdir(parents=True, exist_ok=True)
kp.save_agent_config(KIN, dict(kp.DEFAULT_AGENT_CONFIG))

sent = []
import discord_mirror  # noqa: E402
discord_mirror.mirror_targets = lambda cfg: ([], ["somewhere"])
discord_mirror.mirror = lambda cfg, kin, msgs: sent.extend(msgs)


class _Frame(ChatStreamMixin):
    pass


prompt = "[hearthkin: scheduled wake-up — fired at 07:30 on Monday]\n\ntend"
_Frame()._notify_cron_failure(KIN, prompt, "nothing arrived in 5 minutes")
import time  # noqa: E402
for _ in range(50):
    if sent:
        break
    time.sleep(0.05)

journal = cron_helpers.kin_journal_path(KIN, datetime.date.today())
text = journal.read_text(encoding="utf-8") if journal.exists() else ""
check("the failure is written into today's journal", "Wake-up FAILED" in text
      and "07:30" in text)
log = kp.LOGS_DIR / "cron_errors.log"
check("...and into the wake-up error log the journal line points at",
      log.exists() and KIN in log.read_text(encoding="utf-8"))
check("...and the message says so", sent and "noted in" in sent[0])

sent.clear()
real = cron_helpers.append_journal_error_marker
cron_helpers.append_journal_error_marker = (
    lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
try:
    _Frame()._notify_cron_failure(KIN, prompt, "boom")
finally:
    cron_helpers.append_journal_error_marker = real
for _ in range(50):
    if sent:
        break
    time.sleep(0.05)
check("when the journal can't be written, the message doesn't claim it",
      sent and "noted in" not in sent[0])

print()
if _fails:
    print(f"test_wakeup_failure_noted: {len(_fails)} FAILED")
    sys.exit(1)
print("test_wakeup_failure_noted: all checks passed")
