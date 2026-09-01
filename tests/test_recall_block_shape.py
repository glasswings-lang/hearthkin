# SPDX-License-Identifier: CC0-1.0
"""Recalled memory goes BESIDE the person's words, never inside them.

For a long time the recall block was concatenated onto the front of the live
user message. The stated reason was prompt-cache reuse: a `role=system` message
gets hoisted to position 0 by both Ollama's system fold and OpenRouter's
concatenation, which moves the cached prefix every turn and costs minutes per
reply. That much is true, and it is why this can't live in the system prompt.

The conclusion drawn from it was not. A separate NON-system message sits in the
volatile tail exactly as an inlined one does -- everything before it is
byte-identical either way. Measured against a real kin's real prompt: 6,055
tokens of prefill inlined, 6,059 as its own turn. Four tokens. A page of notes
was being put inside somebody's sentence to save four tokens that were never
at risk.

What it cost instead: whatever is largest in a turn is what gets answered. A
short message behind a block of notes got a reply about the notes -- six times
out of six in sampling, describing "a glowing reference panel" and "that
sudden, bright flash of technical data". Given its own turn, and a header that
stops announcing an arrival, the same kin narrated the block zero times out of
six and used the note in all six.

What this file pins:
  * the person's message arrives byte-identical -- nothing is prepended to it
  * the notes are a separate turn, immediately before it
  * that turn is not `role=system` (it would be hoisted to the front)
  * ...and not `role=assistant` (two assistant turns is what Gemma answers
    with nothing)
  * the notes are not speaker-shaped, the impersonation attractor
  * the block still carries the recalled text intact -- positive controls, so
    a "nothing was added" assertion can't pass by the engine being broken
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="recallshape-"))

import pathlib  # noqa: E402

import kin_persistence as K  # noqa: E402
from memory_recall import _format_block, inject_into_messages  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


USED = [
    {"relpath": "speakerfifteen.md", "lineno": 1, "score": 1.0,
     "text": "SpeakerFifteen's rota changed to four nights on, three off."},
    {"relpath": "journal/2026-08-01.md", "lineno": 3, "score": 0.8,
     "text": "A long evening.\nTwo paragraphs, so indentation has to survive."},
]
block = _format_block(USED, None, None)

# 1. The block is the header and the notes. Nothing else.
frame = K.load_app_prompt("memory_recall_frame")
check("1 the block opens with the registered header", block.startswith(frame))
check("1 the recalled text is present, verbatim",
      "SpeakerFifteen's rota changed to four nights on, three off." in block)
check("1 multi-line notes survive",
      "A long evening.\nTwo paragraphs" in block)
check("1 it is NOT speaker-shaped (the impersonation attractor)",
      "[speakerfifteen]" not in block.lower() and "SpeakerFifteen:" not in block)

# The delimiting machinery is gone -- a turn boundary replaced it. These are
# absence checks, so they sit next to the presence checks above, which prove
# the block is really being built and not empty.
check("1 no wrapper tag (the turn boundary is the delimiter)",
      "<recalled_memory>" not in block)
check("1 no per-note source labels (that is what made it a file listing)",
      "source=" not in block and "speakerfifteen.md" not in block)

# 2. Placement: beside the person's turn, not inside it.
with tempfile.TemporaryDirectory() as tmp:
    kin = pathlib.Path(tmp) / "kin" / "Tester"
    mem = kin / "memory"
    mem.mkdir(parents=True)
    # More than one log, deliberately: BM25's IDF is degenerate on a
    # single-document corpus (every term scores <= 0 and nothing survives the
    # relevance floor), so a one-note fixture measures the fixture, not the
    # placement. The control below is what caught that.
    (mem / "harbour.md").write_text(
        "The harbour songwriting sessions run late into Sunday, and the "
        "bridge from the last one is the part worth keeping.", "utf-8")
    (mem / "orchard.md").write_text(
        "The orchard needs pruning before the first frost and the ladder is "
        "not trustworthy.", "utf-8")
    (mem / "cooking.md").write_text(
        "Pasta recipes, kitchen timers, and which pan holds heat.", "utf-8")

    ASK = "how did the harbour songwriting go?"
    msgs = [
        {"role": "system", "content": "You are Tester."},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": ASK},
    ]
    out, used = inject_into_messages(
        msgs, "Tester", num_ctx=32768,
        cfg={"recall_enabled": True, "recall_budget_pct": 0.18},
        kin_dir=str(kin))

    check("2 CONTROL something was actually recalled", bool(used))
    check("2 a message was added, not merged", len(out) == len(msgs) + 1)
    check("2 THE PERSON'S WORDS ARRIVE UNTOUCHED",
          out[-1]["content"] == ASK)
    check("2 the notes are the turn immediately before",
          out[-2]["content"] != ASK and "harbour" in out[-2]["content"].lower())
    check("2 CONTROL the recalled text really is in that turn",
          "the part worth keeping" in out[-2]["content"])

    # Role matters twice over.
    check("2 the notes are not role=system (it would be hoisted to the front)",
          out[-2]["role"] != "system")
    check("2 ...and not role=assistant (Gemma answers two of those with nothing)",
          out[-2]["role"] != "assistant")
    check("2 the notes ride as role=user", out[-2]["role"] == "user")

    # Nothing that would make it look like something somebody said.
    check("2 the notes turn carries no speaker metadata",
          set(out[-2].keys()) == {"role", "content"})

    # Everything before the insertion point is untouched -- this is the whole
    # prompt-cache argument, and it is cheap to assert.
    check("2 the prefix is byte-identical", out[:-2] == msgs[:-1])

    # 3. Recall off changes nothing at all.
    off, off_used = inject_into_messages(
        msgs, "Tester", num_ctx=32768,
        cfg={"recall_enabled": False}, kin_dir=str(kin))
    check("3 recall off leaves the messages exactly as they were",
          off == msgs and off_used == [])

print()
if _fails:
    print("FAILED (%d): %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("all recall-block-shape checks passed")
