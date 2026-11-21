# SPDX-License-Identifier: CC0-1.0
"""A kin's reasoning settings are its own, everywhere (20 Nov 2026 audit).

  * Telegram groups and wake-ups showed reasoning cut at the default length,
    ignoring the kin's own setting; every other place passed it.
  * Discord threw away reasoning that arrived inside the reply text.
  * The pop-up mini chat decided whether to send reasoning back using the
    kin open in the main window, not its own kin.

Run: python tests/test_reasoning_settings_everywhere.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def src(p):
    return (ROOT / p).read_text(encoding="utf-8")


check("Telegram: no reasoning block is built without the kin's settings",
      "reasoning_block(thinking)" not in src("telegram_bot.py"))
check("wake-ups: the same",
      "reasoning_block(_all_thinking)" not in src("hearthkin_cron.py"))
check("Discord keeps reasoning found inside the reply text",
      "text, thinking = extract_inline_thinking(text, thinking)"
      in src("discord_bot.py"))

from frame.render_mixin import RenderMixin  # noqa: E402


class _W(RenderMixin):
    agent_cfg = {"feed_thinking": False}


row = {"role": "assistant", "content": "hi", "thinking": "pondering"}
w = _W()
check("the open kin's setting is used by default",
      "thinking" not in w._history_entry_for_model(row))
check("...and another kin's own setting when given (the mini chat)",
      w._history_entry_for_model(row, {"feed_thinking": True}).get(
          "thinking") == "pondering")
check("the mini chat gives its own kin's settings",
      "self._history_entry_for_model(_m, cfg)" in src("frame/lifecycle_mixin.py"))

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_reasoning_settings_everywhere: all checks passed")
