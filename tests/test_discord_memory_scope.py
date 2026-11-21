# SPDX-License-Identifier: CC0-1.0
"""Discord turns count toward the memory of the place they're stored in
(20 Nov 2026 audit).

Where a Discord turn is stored is decided per person and per channel, but
the memory counter followed the old kin-wide box. When they disagreed, the
counter ticked a scope whose history lived elsewhere, so a conversation was
distilled from the wrong place or not at all. Now the bot says where it
stored the turn, and every channel with history of its own is listed for
distilling.

Run: python tests/test_discord_memory_scope.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="dcscope-", dir=(os.environ.get("HEARTHKIN_HOME") or None))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


from frame.bot_integration_mixin import BotIntegrationMixin  # noqa: E402


class _F(BotIntegrationMixin):
    def __init__(self):
        self._messages_since_distill = {}
        self.ticked = []

    def _dialog_for(self, name):
        return None

    def _maybe_auto_distill(self, agent_name, scope_key):
        self.ticked.append(scope_key)


f = _F()
f._on_discord_activity("Bracken", 555, True)
check("a turn stored with the desktop ticks the desktop's memory",
      f.ticked == ["desktop"])
f._on_discord_activity("Bracken", 556, False)
check("a turn kept apart ticks that channel's own",
      f.ticked[-1] == "discord:556")

from kin_persistence import (DEFAULT_AGENT_CONFIG, append_discord_turn,  # noqa: E402
                             save_agent_config)
from hearthkin_paths import kin_dir  # noqa: E402
kin_dir("Bracken").mkdir(parents=True, exist_ok=True)
cfg = dict(DEFAULT_AGENT_CONFIG)
cfg["discord"] = dict(cfg.get("discord") or {}, share_desktop=True)
save_agent_config("Bracken", cfg)
append_discord_turn("Bracken", "556", {"role": "user", "content": "hi"})
msrc = (ROOT / "frame" / "memory_mixin.py").read_text(encoding="utf-8")
seg = msrc[msrc.index("Discord channels with history of their own"):]
seg = seg[:seg.index("# Rooms the kin is in")]
check("channels with their own history are listed even with the old box on",
      'get("share_desktop"' not in seg and 'keys.append(f"discord:{cid}")' in seg)

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_discord_memory_scope: all checks passed")
