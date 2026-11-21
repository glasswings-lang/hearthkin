# SPDX-License-Identifier: CC0-1.0
"""An empty reply is never saved as the kin's own words (20 Nov 2026 audit).

The desktop, rooms and wake-ups saved "[no reply produced]" as an ordinary
reply. Read back, it's a turn the kin thinks it said, and it explains or
apologises for it; in a room the other kin read "[Kin] [no reply produced]".
Telegram and Discord already avoided this on purpose. Now:

  * A wake-up saves a note that the reply came back empty, not the
    placeholder as the kin's reply (through the real save function).
  * The desktop and rooms keep the placeholder on screen only.
  * Desktop Regenerate still works on an empty turn: it sends the person's
    message again.

Run: python tests/test_empty_reply_not_saved_as_words.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="emptyreply-", dir=(os.environ.get("HEARTHKIN_HOME") or None))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kin_persistence import (DEFAULT_AGENT_CONFIG, load_agent_conversation,  # noqa: E402
                             save_agent_config)
from hearthkin_paths import kin_dir  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Bracken"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)
cfg = dict(DEFAULT_AGENT_CONFIG)
cfg["model"] = "stub"
save_agent_config(KIN, cfg)

import hearthkin_cron  # noqa: E402
hearthkin_cron._append_to_conversation(KIN, "wake up", "[no reply produced]")
rows = load_agent_conversation(KIN)
check("a wake-up's empty reply isn't saved as the kin's words",
      not any(r.get("role") == "assistant"
              and "[no reply produced]" in str(r.get("content")) for r in rows))
check("...a note that it came back empty is saved instead",
      rows and rows[-1].get("role") == "system"
      and "placeholder went out" in str(rows[-1].get("content")))
hearthkin_cron._append_to_conversation(KIN, "wake again", "Good morning.")
check("an ordinary wake-up reply is still saved as the kin's",
      load_agent_conversation(KIN)[-1].get("content") == "Good morning.")

src = (ROOT / "frame" / "chat_stream_mixin.py").read_text(encoding="utf-8")
check("the desktop keeps the placeholder on screen only",
      'if reply_to_save == "[no reply produced]":' in src)
rsrc = (ROOT / "frame" / "rooms_mixin.py").read_text(encoding="utf-8")
check("...and so does a room",
      'if reply_to_save != "[no reply produced]":' in rsrc)

from frame.chat_stream_mixin import ChatStreamMixin  # noqa: E402


class _Win(ChatStreamMixin):
    def __init__(self, convo):
        self.current_room = None
        self._streaming = False
        self.conversation = [dict(m) for m in convo]
        self.status, self.sent = [], []

    def _set_status(self, t):
        self.status.append(t)

    def _render_conversation(self):
        pass

    def _persist_current_conversation(self):
        pass

    def _send_message(self, text, regen_attachment_refs=None):
        self.sent.append(text)


note = {"role": "system", "content": "[hearthkin: nothing of yours reached...]"}
w = _Win([{"role": "user", "content": "are you there?"}, note])
w._on_regen(None)
check("Regenerate on an empty turn sends the person's message again",
      w.sent == ["are you there?"] and w.conversation == [])
w = _Win([{"role": "user", "content": "look it up"},
          {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]},
          {"role": "tool", "content": "r", "tool_call_id": "a"}, note])
w._on_regen(None)
check("...including one that used tools before coming back empty",
      w.sent == ["look it up"] and w.conversation == [])

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_empty_reply_not_saved_as_words: all checks passed")
