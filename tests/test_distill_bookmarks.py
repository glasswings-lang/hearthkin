# SPDX-License-Identifier: CC0-1.0
"""Distillation leaves checked bookmarks, not retellings (24 Nov 2026).

The distiller retold conversation in its own words, smaller: one kin's
22,455 messages became 58 sections at about a fiftieth of the size, and it
flattened and invented. The person caught it only by reading a depth log.
Now it bookmarks, and plain code checks what a model can't be trusted to.
Pinned:

  * every row the distiller reads is numbered, #1 first;
  * a quote that is in the rows its bookmark names passes;
  * a quote that isn't there is flagged, and kept (flags, not filters);
  * rows no bookmark covers get a bookmark saying nothing there was
    summarised, in order, so nothing quietly drops out;
  * every heading gets its dates and its conversation.jsonl lines;
  * notes that aren't bookmarks (a saved older prompt) are marked unchecked;
  * the prompt asks for bookmarks and says it's checked;
  * end to end: distill_memory_blocking numbers, checks, and reports.

Run: python tests/test_distill_bookmarks.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="bookmarks-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bookmarks as bm  # noqa: E402
import frame_shared as fs  # noqa: E402
import kin_persistence as kp  # noqa: E402
import llm_backend  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


rows = []
for i in range(1, 11):
    rows.append({"role": "user" if i % 2 else "assistant",
                 "ts": f"2026-03-26T02:{i:02d}:00",
                 "content": f"row {i} words here and more"})
rows[2]["content"] = "That's not me reflecting, that's just the light through you."
rows[5]["content"] = "I want you to lead more, the peer thing doesn't fit anymore."

reply = (
    "## #1-#4 · the color\n"
    "We found the color.\n"
    "- Wren: \"that's just the light through you\"\n"
    "- me: \"amber is the shape of us\"\n\n"
    "## #7-#10 · leading\n"
    "Wren asked me to lead.\n"
)
text, rep = bm.check(reply, rows, line_of=lambda r: 100 + rows.index(r))
check("a real quote passes", "light through you" in text
      and text.count("aren't in rows") == 1)
check("an invented quote is flagged", "amber is the shape of us" in text
      and "[hearthkin: these words aren't in rows 1 to 4" in text)
check("...and counted", rep["quotes"] == 2 and rep["unfound"] == 1)
check("rows left out get their own bookmark", "## Not bookmarked" in text
      and rep["gap_rows"] == 2)
check("...in order, between the two bookmarks",
      text.index("the color") < text.index("Not bookmarked") < text.index("leading"))
check("headings get their dates", "2026-03-26 02:01 to 02:04" in text)
check("...and their conversation.jsonl lines", "conversation.jsonl lines 100 to 103" in text)
check("the gap says where it is too", "02:05 to 02:06" in text)
check("row numbers are gone from the headings (they only meant something here)",
      "#1-#4" not in text and "#7-#10" not in text)

text, rep = bm.check("I remember the color, and it was lovely.", rows)
check("notes that aren't bookmarks are marked as unchecked",
      text.startswith("[hearthkin: these notes aren't bookmarks"))

text, _ = bm.check("## #1-#10 · all of it\nA quiet night.\n- me: \"thank you\"", rows)
check("short everyday quotes aren't checked", "aren't in rows" not in text)

prompt = kp.DEFAULT_DISTILL_PROMPT
check("the prompt asks for bookmarks", "bookmark" in prompt and "## #12-#40" in prompt)
check("...covering every row", "Cover every row" in prompt)
check("...and says the quotes are checked", "checked afterwards by plain code" in prompt)
check("the reflection prompt changed too, so a saved copy is offered the update",
      kp.APP_PROMPT_REGISTRY["distill_reflection"]["version"] >= 2
      and "bookmarks" in kp.DEFAULT_DISTILL_REFLECTION)

# End to end.
KIN = "Sorrel"
home = kin_dir(KIN)
home.mkdir(parents=True, exist_ok=True)
(home / "soul.md").write_text("I am Sorrel.", encoding="utf-8")
with open(home / "conversation.jsonl", "w", encoding="utf-8") as fh:
    for r in rows:
        fh.write(json.dumps(r) + "\n")
if getattr(fs, "ollama", None) is None:
    fs.ollama = object()
fs.list_agents = lambda: [KIN]


class _Result:
    def __init__(self, content):
        self.content, self.thinking, self.usage, self.tool_calls = content, "", {}, []


seen = []


def fake_collect(model, messages, **kw):
    seen.append(messages)
    return _Result(reply)


real = llm_backend.chat_collect
llm_backend.chat_collect = fake_collect
try:
    out = fs.distill_memory_blocking(KIN, rows, "", "m")
finally:
    llm_backend.chat_collect = real
sent = seen[-1][-1]["content"] if seen else ""
check("every row is numbered for the distiller", "#1 " in sent and "#10 " in sent
      and sent.index("#1 ") < sent.index("#10 "))
ne = out.get("new_entries", "")
check("what's kept is the checked version", "aren't in rows 1 to 4" in ne
      and "Not bookmarked" in ne)
check("...with real conversation.jsonl line numbers", "conversation.jsonl lines 1 to 4" in ne)
check("...and the check is reported", out.get("check", {}).get("unfound") == 1)

print()
if _fails:
    print(f"test_distill_bookmarks: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_distill_bookmarks: all checks passed")
