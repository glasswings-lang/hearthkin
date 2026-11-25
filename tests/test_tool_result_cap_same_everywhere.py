# SPDX-License-Identifier: CC0-1.0
"""The tool-result cap means the same on every surface (24 Nov 2026).

The setting says 0 = no limit. The desktop, rooms and Telegram did that;
Discord and wake-ups read it as `... or 8000`, so 0 quietly became 8000
there. The person: keep 8000 as the default, and make the setting mean what
it says everywhere ("unchanged behavior, transparent setting").

Run: python tests/test_tool_result_cap_same_everywhere.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kin_persistence import DEFAULT_AGENT_CONFIG, tool_result_cap_of  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


check("the default is still 8000", DEFAULT_AGENT_CONFIG["tool_result_cap"] == 8000)
check("unset reads as 8000", tool_result_cap_of({}) == 8000)
check("0 reads as no limit", tool_result_cap_of({"tool_result_cap": 0}) == 0)
check("a number is kept", tool_result_cap_of({"tool_result_cap": 12000}) == 12000)
check("a broken value falls back to 8000",
      tool_result_cap_of({"tool_result_cap": "x"}) == 8000
      and tool_result_cap_of({"tool_result_cap": None}) == 8000)

for name in ("discord_bot.py", "hearthkin_cron.py"):
    src = (ROOT / name).read_text(encoding="utf-8")
    check(f"{name} no longer turns 0 into 8000",
          '"tool_result_cap", 8000) or 8000' not in src
          and "tool_result_cap_of(cfg)" in src)

help_src = (ROOT / "dialogs" / "tool_settings.py").read_text(encoding="utf-8")
check("the help says 0 = no limit everywhere",
      "0 = no limit" in help_src and "still cut at 8000" not in help_src)

print()
if _fails:
    print(f"test_tool_result_cap_same_everywhere: {len(_fails)} FAILED")
    sys.exit(1)
print("test_tool_result_cap_same_everywhere: all checks passed")
