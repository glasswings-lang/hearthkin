# SPDX-License-Identifier: CC0-1.0
"""A scheduled wake-up can run shell commands (12 Nov 2026).

It used to lose exec whenever it ran on its own, while the same wake-up
delivered into the open desktop chat kept it. Now it keeps exec either way,
under the desktop's rule minus the asking: the never-run list refuses,
background commands refuse (the wake-up process ends; nothing could stop
them), a remembered command or a trusted/full kin runs, anything else is not
run and the kin is told nobody was there to ask.

The command is a stand-in that records whether it ran.

Run: python tests/test_cron_exec.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="cronexec-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import hearthkin_cron as hc  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402
from kin_persistence import (DEFAULT_AGENT_CONFIG, save_agent_config,  # noqa: E402
                             save_kin_tools)
from tools._exec_state import add_to_allowlist  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Sorrel"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)


def run(trust, command="dir", background=False):
    cfg = dict(DEFAULT_AGENT_CONFIG)
    cfg["tool_trust"] = trust
    save_agent_config(KIN, cfg)
    seen = {"ran": False}

    def raw(args):
        seen["ran"] = True
        return "ok"

    out = hc._wrap_cron_exec(KIN, raw)({"command": command,
                                        "background": background})
    return seen["ran"], out


ran, _ = run("full")
check("a full-trust kin's command runs on a wake-up", ran)
ran, _ = run("trusted")
check("a trusted kin's command runs", ran)
ran, out = run("untrusted")
check("an untrusted kin's command doesn't run", not ran)
check("...and the kin is told nobody was there to ask, not that it was refused",
      "obody refused" in out and "nobody was there" in out)
add_to_allowlist(KIN, "echo remembered")
ran, _ = run("untrusted", command="echo remembered")
check("a command you remembered for this kin runs even when untrusted", ran)
ran, _ = run("full", command="format c:")
check("the never-run list still refuses, even for a full-trust kin", not ran)
ran, out = run("full", background=True)
check("a background command is refused, and the kin is told why",
      not ran and "background" in out)

# The real tool loader hands exec over, wrapped.
save_kin_tools(KIN, ["exec", "read_file", "use_webcam"])
schemas, executor, names = hc._load_cron_tools(KIN)
check("a wake-up's tool set now includes exec", "exec" in names
      and "exec" in executor)
check("...and it's the wrapped one", getattr(executor["exec"], "__name__", "")
      == "_wrapped")
check("the webcam still stays off on a wake-up", "use_webcam" not in names)

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_cron_exec: all checks passed")
