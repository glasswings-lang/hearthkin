# SPDX-License-Identifier: CC0-1.0
"""Claude conversations already extracted to Markdown can be imported.

A claude.ai export is JSON, and claude_json reads it. But people extract
those conversations to readable Markdown -- to read them, to keep them, to
survive an export that turned out to be lossy -- and once that is done the
JSON is frequently gone. What is left is a folder of .md files holding whole
conversations that nothing could import.

**The failure this pins was silent, which is why it needs a test.** Handed to
the plain-text parser, one real 155-message conversation came back as TWO
messages, both `user`. Every assistant reply was gone. Nothing raised, nothing
warned; the preview showed two turns and looked merely short. Zero assistant
turns is the exact shape the project's import rules already legislate against,
arrived at from a direction nothing was watching.

Run: python tests/test_claude_markdown_import.py
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="hk_clmd_"))
os.environ.setdefault("HEARTHKIN_SILENT", "1")

from importers import parse_history, claude_markdown as CM

_fails = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _fails.append(label)


work = Path(tempfile.mkdtemp(prefix="hk_clmd_src_"))


def write(name, text):
    p = work / name
    p.write_text(text, encoding="utf-8")
    return str(p)


# The two shapes actually found in a long-lived archive.
OLDER = """# Chat ID: 3a3f5986-65c5-40ee-9148-984b91e15f30

**human:**

what I said first

**assistant:**

what came back

**human:**

and then this
"""

NEWER = """# Analyzing something carefully

- Date: 2025-12-08
- ID: 013fc723-47e4-41cb-8984-0f5e45763970

---

**You:**

my opening

**Claude:**

the reply
"""

RECOVERED = """# Chat ID: d15e2099-6451-4d3c-aa7e-b4077c8f64f0

> Recovered from your local archive. The claude.ai export carried this
> conversation with dates but no message text.
> Date: 2025-09-30  -  1 messages


**human:**

a conversation exactly one turn long
"""

older = write("older-extractor.md", OLDER)
newer = write("2025-12-08 - analysing something (013fc723).md", NEWER)
recovered = write("2025-09-30 - one turn (d15e2099).md", RECOVERED)

print("\n-- both extractor shapes are read, with roles intact --")

for path, label in ((older, "older `**human:**` shape"),
                    (newer, "`**You:** / **Claude:**` shape")):
    msgs, _, fmt = parse_history(path, "Claude")
    roles = [m["role"] for m in msgs]
    check(fmt == "claude_markdown", f"{label}: routed to the right parser")
    check(roles.count("assistant") >= 1,
          f"{label}: assistant turns SURVIVE (the whole bug)")
    check(roles.count("user") >= 1, f"{label}: user turns too")

msgs, _, _ = parse_history(older, "Claude")
bodies = [m["content"] for m in msgs]
check([m["role"] for m in msgs] == ["system", "user", "assistant", "user"],
      "turns come out in order, alternating as they were written")
check("what I said first" in bodies[1] and "what came back" in bodies[2],
      "each speaker's words land under that speaker")

print("\n-- the header block is metadata and is dropped --")

msgs, _, _ = parse_history(newer, "Claude")
joined = "\n".join(m["content"] or "" for m in msgs[1:])
for noise, what in (("Chat ID", "the chat id"), ("- Date:", "the date line"),
                    ("- ID:", "the id line"), ("---", "the horizontal rule")):
    check(noise not in joined, f"{what} does not leak into the conversation")
check("my opening" in joined, "...while the actual words are all there")

msgs, _, _ = parse_history(recovered, "Claude")
joined = "\n".join(m["content"] or "" for m in msgs[1:])
check("Recovered from your local archive" not in joined,
      "a leading blockquote note is header too, and is dropped")

print("\n-- the title becomes a thread header, not a lost line --")

msgs, _, _ = parse_history(newer, "Claude")
check(msgs[0]["role"] == "system" and "Analyzing something carefully" in msgs[0]["content"],
      "the title survives as the one-line thread header")
msgs, _, _ = parse_history(older, "Claude")
check(msgs[0]["role"] == "system" and "Chat ID" not in msgs[0]["content"],
      "a file with only a Chat ID gets a header from its NAME, not the uuid")

print("\n-- dates: from the file, its name, or honestly absent --")

msgs, _, _ = parse_history(newer, "Claude")
check((msgs[1].get("ts") or "").startswith("2025-12-08"),
      "a `- Date:` line anchors the turns")
msgs, _, _ = parse_history(recovered, "Claude")
check((msgs[1].get("ts") or "").startswith("2025-09-30"),
      "a Date: inside a blockquote note works too")
undated = write("no-date-anywhere.md", OLDER)
msgs, _, _ = parse_history(undated, "Claude")
check(len(msgs) == 4,
      "no date anywhere still imports -- losing turns over a missing stamp "
      "would be the worse trade")

print("\n-- a one-turn conversation is a conversation --")

# This shipped broken: detection wanted two speaker lines, and a real
# archived exchange of a single message fell through to a parser that
# could not read it and failed outright.
msgs, _, fmt = parse_history(recovered, "Claude")
check(fmt == "claude_markdown", "one speaker line + an extractor header parses")
check([m["role"] for m in msgs] == ["system", "user"], "...as one user turn")

print("\n-- and it does NOT steal files that aren't its --")

prose = write("just-prose.md",
              "Some notes I wrote.\n\n**You:**\n\nthat was a heading, not a turn.\n")
check(not CM.detect(Path(prose).read_text(encoding="utf-8")),
      "one bold line in ordinary prose is NOT claimed")

hand = write("hand-authored.txt",
             "# 2026-01-15\nSpeakerFive: morning\nSpeakerFifteen: hello\n")
_msgs, _, fmt = parse_history(hand, "SpeakerFive")
check(fmt != "claude_markdown",
      "a hand-authored seed file still goes to the text parser")

print("\n-- nothing under the speakers is refused, not imported empty --")

hollow = write("hollow.md", "# Chat ID: abc12345\n\n**human:**\n\n\n**assistant:**\n\n\n")
try:
    parse_history(hollow, "Claude")
    check(False, "a file with speakers but no words is refused")
except Exception:
    check(True, "a file with speakers but no words is refused")

if _fails:
    print(f"\nFAILED: {len(_fails)} check(s): {_fails}")
    sys.exit(1)
print("\nALL CHECKS PASSED -- extracted Claude markdown imports with its roles.")
