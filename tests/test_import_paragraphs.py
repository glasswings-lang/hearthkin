# SPDX-License-Identifier: CC0-1.0
"""A hand-written transcript keeps every paragraph of every turn.

27 Nov 2026: a "You: / Name:" transcript whose replies ran over several
paragraphs imported 843 of its 9,104 characters. A blank line ended the
turn, and each paragraph after the first was skipped as a "stray line",
with nothing said. Now a blank line is a paragraph break inside the turn,
and a turn ends only at the next speaker or date header.

Run: python tests/test_import_paragraphs.py
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from importers import parse_history  # noqa: E402

fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        fails.append(label)


TEXT = (
    "You: tell me about the lighthouse\n"
    "Wren: (The lamp turns slowly.)\n"
    "\n"
    "It has stood here a long time.\n"
    "\n"
    "\n"
    "Ships still look for it at night.\n"
    "\n"
    "You: thank you\n"
    "\n"
    "Wren: Always.\n"
    "\n"
    "# 2026-03-01 evening\n"
    "You: a new day\n"
    "\n"
    "Wren: First paragraph.\n"
    "\n"
    "Second paragraph.\n"
)

d = tempfile.mkdtemp(prefix="paragraphs-")
p = os.path.join(d, "log.txt")
with open(p, "w", encoding="utf-8", newline="\r\n") as f:
    f.write(TEXT)

msgs, _label, fmt = parse_history(p, "Wren")
check("read as a hand-written transcript", fmt == "hand_authored")
check("six turns, alternating", [m["role"] for m in msgs]
      == ["user", "assistant", "user", "assistant", "user", "assistant"])
first = msgs[1]["content"]
check("a reply keeps every paragraph",
      "lamp turns" in first and "stood here" in first and "Ships still" in first)
check("...with the paragraph breaks kept", "\n\nIt has stood" in first)
check("...and no blank lines at either end",
      first == first.strip() and msgs[3]["content"] == "Always.")
check("a date header still ends the turn before it",
      "a new day" not in msgs[3]["content"] and msgs[4]["content"] == "a new day")
check("the last reply keeps its second paragraph",
      msgs[5]["content"] == "First paragraph.\n\nSecond paragraph.")
every = "\n".join(m["content"] for m in msgs)
lost = [ln for ln in TEXT.splitlines()
        if ln.strip() and not ln.startswith("#")
        and ln.split(": ", 1)[-1] not in every]
check("no line of the transcript is lost", not lost)

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("test_import_paragraphs: all checks passed")
