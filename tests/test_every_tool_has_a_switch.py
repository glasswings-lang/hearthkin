# SPDX-License-Identifier: CC0-1.0
"""Every tool a kin can be given has a place where it can be switched off,
and is written up where people and kin read about tools (23 Nov 2026).

The person asked for this: "check for tools that aren't listed in the
enable/disable UIs". A tool that reaches a kin somewhere with no switch for it
there is a tool nobody can take away, and one missing from the guides is one
nobody knows to look for. For every registered tool this checks:

  1. the Tools tab: it's listed, or it's a proactive tool with its own home
     (the heartbeat and wake-up settings);
  2. Telegram and Discord: it's in a per-person tool group, or deliberately
     blocked there;
  3. wake-ups: every tool a wake-up actually receives can be ticked in
     "Leave these tools out of scheduled wake-ups";
  4. heartbeats: every tool a heartbeat receives is one the kin has turned
     on, or a proactive one;
  5. the kin manual and the user guide both mention it.

KNOWN_GAPS is for a gap found and reported but not yet fixed (empty now). The check fails on any NEW gap, and also when a known one is
fixed but still listed here, so the list only ever shrinks honestly.

Run: python tests/test_every_tool_has_a_switch.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="toolswitch-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import hearthkin_cron as hc  # noqa: E402
import kin_persistence as kp  # noqa: E402
import tools as kin_tools  # noqa: E402
from tools import _buckets  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402

# (place, tool) pairs found and reported on 23 Nov 2026.
# All five found that day were fixed the same day: reach_out and message_kin
# in the Tools tab and the wake-up list, heartbeats following the kin's own
# tools, message_kin in the user guide. The real windows are checked by
# tests/test_every_tool_in_the_windows.py.
KNOWN_GAPS = set()

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


ALL = sorted(kin_tools.list_available())
PROACTIVE = set(kin_tools._PROACTIVE_TOOLS)
check("the check can see the tools at all", len(ALL) > 10)

gaps = set()

# 1. Tools tab: built from list_available minus the proactive tools.
tab_src = (ROOT / "dialogs" / "edit_kin.py").read_text(encoding="utf-8")
check("the Tools tab lists from the registry, not a hand-kept list",
      "kin_tools.list_available()" in tab_src and "_PROACTIVE_TOOLS" in tab_src)

# 2. Telegram / Discord groups.
bucketed = set(_buckets._READ) | set(_buckets._WRITE) | set(_buckets._FULL)
for t in ALL:
    if t not in bucketed and t not in _buckets.INTENTIONALLY_TELEGRAM_BLOCKED:
        gaps.add(("telegram/discord groups", t))

# 3 and 4: a kin with every tool turned on.
KIN = "Sorrel"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)
everyday = [t for t in ALL if t not in PROACTIVE]
kp.save_kin_tools(KIN, everyday)
cfg = dict(kp.DEFAULT_AGENT_CONFIG)
cfg["tool_trust"] = "full"
cfg["model"] = "test-model"
kp.save_agent_config(KIN, cfg)

schemas, _ex, _names = hc._load_cron_tools(KIN)
wake_gets = {s.get("function", {}).get("name") for s in schemas}
offer = set(kp.wake_off_choices(kp.load_kin_tools(KIN), [],
                                kp.proactive_on_for(kp.load_agent_config(KIN))))
for t in sorted(wake_gets - offer):
    gaps.add(("wake-up off list", t))

# The heartbeat, with only a few tools on, so anything extra shows.
kp.save_kin_tools(KIN, ["read_file"])
seen = {}


def fake_load(names, context=None, **kw):
    seen["names"] = list(names)
    raise RuntimeError("stop")


real = kin_tools.load_tools
hc.kin_tools.load_tools = fake_load
try:
    hc.run_heartbeat(KIN, cfg)
finally:
    hc.kin_tools.load_tools = real
for t in seen.get("names", []):
    if t not in PROACTIVE and t not in ("read_file",):
        gaps.add(("heartbeat", t))

# 5. Written up.
manual = (ROOT / "docs" / "kin_manual.md").read_text(encoding="utf-8")
guide = (ROOT / "docs" / "user-guide.html").read_text(encoding="utf-8")
for t in ALL:
    if t not in manual:
        gaps.add(("kin manual", t))
    if t not in guide:
        gaps.add(("user guide", t))

new = sorted(gaps - KNOWN_GAPS)
fixed = sorted(KNOWN_GAPS - gaps)
print()
print("Gaps found:")
for place, t in sorted(gaps):
    print(f"  {t}: no switch or write-up in the {place}"
          + ("" if (place, t) in KNOWN_GAPS else "   <- NEW"))
check("no new gaps: " + ", ".join(f"{t} ({p})" for p, t in new), not new)
check("no fixed gaps still listed as known: " + ", ".join(f"{t} ({p})" for p, t in fixed),
      not fixed)

print()
if _fails:
    print(f"test_every_tool_has_a_switch: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_every_tool_has_a_switch: all checks passed")
