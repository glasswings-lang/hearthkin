# SPDX-License-Identifier: CC0-1.0
"""Seventh batch of fixes from the 20 Nov 2026 surface audit.

  * Saving and reading back a conversation keeps a Discord person's words
    without their name ("said", which /regen sends again), the mark on a
    message that reached a reply mid-way, and a tool result's tool name.
    Every rewrite of the file dropped all three.
  * The desktop's Stop runs the full reply cleanup on what it keeps, as
    rooms and finished replies do (only the timestamp was stripped).
  * Live Telegram and Discord messages never show reasoning tags or
    tool-call markup, and still only ever grow.

Run: python tests/test_audit_fixes_batch7.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="auditfix7-", dir=(os.environ.get("HEARTHKIN_HOME") or None))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kin_persistence import _clean_chat_message  # noqa: E402
from chat_helpers import live_visible  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


r = _clean_chat_message({"role": "user", "content": "[Wren] hi", "said": "hi",
                         "interjected": True})
check("a Discord person's own words survive a save", r.get("said") == "hi")
check("...and the mid-reply mark", r.get("interjected") is True)
t = _clean_chat_message({"role": "tool", "content": "x", "tool_call_id": "a",
                         "name": "read_file"})
check("a tool result keeps its tool name", t.get("name") == "read_file")

src = (ROOT / "frame" / "chat_stream_mixin.py").read_text(encoding="utf-8")
seg = src[src.index("cleaned_partial, extracted_think = extract_inline_thinking"):]
seg = seg[:seg.index("stopped_msg = ")]
check("the desktop's Stop runs the full cleanup", "clean_kin_reply(" in seg)

check("reasoning tags never show live",
      "<thinking>" not in live_visible("Hi <thinking>hmm")
      and "hmm" not in live_visible("Hi <thinking>hmm</thinking> there"))
check("tool-call markup never shows live",
      "tool_call" not in live_visible('Sure. <tool_call>{"name"'))
check("plain text is left alone", live_visible("Just words.") == "Just words.")
stream = "Hi <thinking>let me think</thinking> there, <tool_call>{}</tool_call>"
seen, ok = "", True
for i in range(1, len(stream) + 1):
    now = live_visible(stream[:i]).rstrip()
    if not now.startswith(seen.rstrip()):
        ok = False
        break
    seen = now
check("a live message only ever grows as the reply streams in", ok)
for p in ("telegram_bot.py", "discord_bot.py"):
    check(f"{p} uses it for live text",
          "live_visible(" in (ROOT / p).read_text(encoding="utf-8"))

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_audit_fixes_batch7: all checks passed")
