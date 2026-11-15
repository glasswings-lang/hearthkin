# SPDX-License-Identifier: CC0-1.0
"""A kin can keep a tool in chat but not at its wake-ups (15 Nov 2026).

A kin's tending wake-ups spent their calls on dozens of exec searches of its
own log, which tending never needs, while exec was still wanted in chat for
real work. `cron_tools_off` in the kin's settings names tools left out of its
wake-ups only. Pinned:

  * a tool named there is not given to a wake-up;
  * the kin's other tools, the memory ones included, still are;
  * the kin's chat tools are unchanged;
  * no setting, or a malformed one, leaves the wake-up's tools as they were;
  * the check can fail: without the setting, exec is given.

Run: python tests/test_cron_tools_off.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="crontoolsoff-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import hearthkin_cron as hc  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402
from kin_persistence import (DEFAULT_AGENT_CONFIG, load_kin_tools,  # noqa: E402
                             save_agent_config, save_kin_tools)

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Sorrel"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)
TOOLS = ["exec", "read_file", "edit_file", "write_file", "read_staging", "archive_staging", "note"]
save_kin_tools(KIN, TOOLS)


def wake_tools(off):
    cfg = dict(DEFAULT_AGENT_CONFIG)
    cfg["tool_trust"] = "full"
    if off is not None:
        cfg["cron_tools_off"] = off
    save_agent_config(KIN, cfg)
    _, _, names = hc._load_cron_tools(KIN)
    return names


names = wake_tools(None)
check("the check can fail: with no setting, a wake-up is given exec", "exec" in names)

names = wake_tools(["exec"])
check("exec named in cron_tools_off isn't given to a wake-up", "exec" not in names)
check("...the memory tools still are",
      all(t in names for t in ("read_file", "edit_file", "write_file", "read_staging",
                               "archive_staging", "note")))
check("...and the kin's chat tools are unchanged", load_kin_tools(KIN) == TOOLS)

check("a malformed setting leaves the wake-up's tools as they were",
      "exec" in wake_tools("exec"))
check("an unknown tool name is ignored", "exec" in wake_tools(["no_such_tool"]))

print()
if _fails:
    print(f"test_cron_tools_off: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_cron_tools_off: all checks passed")
