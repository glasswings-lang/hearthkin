# SPDX-License-Identifier: CC0-1.0
"""Recall must be able to see the WHOLE memory folder, and every note it hands
over must say when it is from.

The failure this pins was found on a real kin. Recall stopped chunking the
corpus once it had 240 chunks, in folder order. With 384 note files and about
4,900 chunks, that meant the first ten files -- one week of dated daily logs,
alphabetically first -- were the only thing it ever recalled, for months. Five
hundred recalls in one month, every one from the same nine days.

And because notes arrived without a date, an eight-month-old line (about a
breakfast and a game) read as a description of today. The kin
told the person what they had done that day, and was wrong.

What this file pins:
  * a note that sorts AFTER a large block of earlier notes can still be found
  * the embed batch stays bounded (the cap now limits only the rerank)
  * a note's date comes from the nearest dated heading above it; a note that
    runs across several dated sections gives the range; a dated file name
    dates a daily log; otherwise it says no date was recorded -- never a guess
  * fenced notes are never recalled, however well they match

All material here is invented.
"""

import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="recallwhole-"))

import memory_recall  # noqa: E402
from memory_recall import build_recall_block  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def make_kin(root, notes):
    kin = pathlib.Path(root) / "kin" / "Tester"
    mem = kin / "memory"
    mem.mkdir(parents=True)
    for fname, body in notes.items():
        (mem / fname).write_text(body, "utf-8")
    return kin


def recall(kin, text, **kw):
    kw.setdefault("budget_tokens", 5898)
    kw.setdefault("max_items", 6)
    kw.setdefault("semantic", False)
    return build_recall_block("Tester", [{"role": "user", "content": text}],
                              kin_dir=str(kin), **kw)


PAD = " The tide came in and went out again, as it does." * 14  # > one chunk

notes = {}
# 400 early daily logs, all sorting before "zz-". 300 mention a heron, so a
# heron query qualifies far more chunks than the rerank cap.
for d in range(400):
    paras = [f"Ordinary day {d}, paragraph {k}. Walked, cooked, slept." + PAD
             for k in range(3)]  # each long enough to be its own chunk
    if d < 300:
        paras.append("A heron stood in the marsh reeds at dusk.")
    if d == 7:
        paras.append("Bought a crate of kumquats from the market stall.")
    notes["2025-%02d-%02d-%03d.md" % ((d % 12) + 1, (d % 28) + 1, d)] = (
        "\n\n".join(paras))
# The note that matters sorts LAST, with dated sections long enough to chunk
# separately, then one short closing section that shares a chunk.
notes["zz-lighthouse.md"] = (
    "# The lighthouse\n\n"
    "## Keeper's lamp (2026-06-14)\n\n"
    "The lighthouse keeper polished the brass lamp and trimmed the wick "
    "before the storm." + PAD + "\n\n"
    "## Fog bell (2026-09-02)\n\n"
    "The fog bell rang all night and the keeper counted the ships." + PAD)
notes["zz-pier.md"] = (
    "## Pier (2026-01-05)\n\nThe pier gulls stole a pasty.\n\n"
    "## Pier again (2026-03-09)\n\nThe pier gulls stole a second pasty.")
notes["undated-orchard.md"] = (
    "The orchard quinces ripened late; the quinces went into jam with "
    "cardamom.")
notes["fiction-diary.md"] = (
    "In the story, the lighthouse keeper polished the brass lamp too.")

root = tempfile.mkdtemp(prefix="recallwhole-kin-")
kin = make_kin(root, notes)

check("fixture is far larger than the old 240-chunk cap",
      400 * 3 > 4 * memory_recall._MAX_CHUNKS)

block, used = recall(kin, "the lighthouse keeper polished the brass lamp",
                     fence=["fiction-diary"])
check("a note sorting after 1,200 earlier chunks is found",
      "zz-lighthouse.md" in [u["relpath"] for u in used])
check("its date comes from the nearest dated heading above it",
      block is not None and "From your notes, 14 June 2026:" in block)

block, used = recall(kin, "the fog bell rang and the keeper counted ships")
check("a later section carries its own heading's date",
      block is not None and "From your notes, 2 September 2026:" in block)

block, used = recall(kin, "pier gulls stole a pasty")
check("a note spanning two dated sections gives the range",
      block is not None
      and "From your notes, 5 January 2026 to 9 March 2026:" in block)

block, used = recall(kin, "orchard quinces cardamom jam")
check("an undated note says so instead of guessing",
      block is not None and "date not recorded" in block)

block, used = recall(kin, "a crate of kumquats from the market stall")
check("a daily log is dated from its file name",
      block is not None and "From your notes, 8 August 2025:" in block)

query = "the lighthouse keeper polished the brass lamp"
check("a fenced note is never recalled",
      "fiction-diary.md" not in [u["relpath"] for u in recall(
          kin, query, fence=["fiction-diary"])[1]])
check("positive control: unfenced, the same query reaches it",
      "fiction-diary.md" in [u["relpath"] for u in recall(kin, query)[1]])

# The embed batch stays bounded: count what the rerank is handed.
seen = {}
_real = memory_recall._semantic_scores


def _fake(query, texts, *a, **k):
    seen["n"] = len(texts)
    return [1.0] * len(texts)


memory_recall._semantic_scores = _fake
try:
    recall(kin, "a heron stood in the marsh reeds", semantic=True)
finally:
    memory_recall._semantic_scores = _real
check("the rerank is handed exactly the cap when more chunks match",
      seen.get("n", 0) == memory_recall._MAX_CHUNKS)

print()
print("FAILED: %d" % len(_fails) if _fails else "ALL PASS")
sys.exit(1 if _fails else 0)
