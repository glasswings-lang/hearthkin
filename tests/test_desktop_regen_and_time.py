# SPDX-License-Identifier: CC0-1.0
"""Two desktop fixes from the 20 Nov 2026 audit.

  * Regenerate steps past every note Hearthkin left after the reply (a
    salvage note, a saved-file note, a park move). It recognised one exact
    wording, which even the salvage note didn't start with, so after any of
    them the person got "Nothing to regenerate". It never steps past a
    compaction note.
  * The message being sent carries its time, like every earlier message and
    like every other surface's.

Run: python tests/test_desktop_regen_and_time.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="regentime-", dir=(os.environ.get("HEARTHKIN_HOME") or None))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from frame.chat_stream_mixin import ChatStreamMixin  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


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


base = [{"role": "user", "content": "draw me a tree"},
        {"role": "assistant", "content": "Here's a tree."}]
for label, notes in (
        ("a salvage note", ["[hearthkin: you called note, and the reply after it came back empty]"]),
        ("a saved-file note and a park move", ["[hearthkin: saved tree.md]", "🌳 moved north"]),
        ("no note at all", [])):
    w = _Win(base + [{"role": "system", "content": n} for n in notes])
    w._on_regen(None)
    check(f"Regenerate works after {label}",
          w.sent == ["draw me a tree"]
          and not any(m.get("role") == "system" for m in w.conversation))

w = _Win(base + [{"role": "system", "content": "the note", "compact": True}])
w._on_regen(None)
check("...but never steps past a compaction note",
      w.sent == [] and w.conversation[-1].get("compact") is True)

src = (ROOT / "frame" / "chat_send_mixin.py").read_text(encoding="utf-8")
check("the desktop's outgoing message carries its time",
      "format_ts_prefix(\n            _dt.datetime.now()" in src.replace("\r\n", "\n"))

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_desktop_regen_and_time: all checks passed")
