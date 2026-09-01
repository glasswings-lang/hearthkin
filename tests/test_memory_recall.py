# SPDX-License-Identifier: CC0-1.0
"""Standalone tests for memory_recall.build_recall_block (no network).

Exercises the always-available BASE path: BM25 + recency + salience + budget +
diversity + fence/boost + framing. The semantic layer is fail-soft and not
covered here (it needs a live embed model; verified separately against the Mac).
"""

import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# run_all.py sets this for every child, but this file asserts on the recall
# FRAME, which load_app_prompt reads from the profile's prompts/ folder if one
# is seeded there. Run standalone without this, it reads whatever version the
# person's real profile happens to have adopted, and reports their seeding
# state as a code failure.
os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="recalltest-"))

from memory_recall import build_recall_block  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def make_kin(tmp):
    """A kin dir with depth logs + journals + the root files that must be
    IGNORED (memory.md / soul.md live above memory/)."""
    kin = pathlib.Path(tmp) / "kin" / "Tester"
    mem = kin / "memory"
    (mem / "journal").mkdir(parents=True)
    (kin / "soul.md").write_text("I am Tester. Do not surface this.", "utf-8")
    (kin / "memory.md").write_text("Index. Do not surface this either.", "utf-8")
    (mem / "harbour.md").write_text(
        "The harbour project is a long-running songwriting collaboration.\n\n"
        "Harbour sessions happen on weekends and run late.", "utf-8")
    (mem / "orchard.md").write_text(
        "The orchard needs pruning before the first frost.\n\n"
        "Orchard and harbour both compete for the same free weekends.", "utf-8")
    (mem / "cooking.md").write_text(
        "A note about pasta recipes and kitchen timers, unrelated.", "utf-8")
    (mem / "journal" / "2026-06-01.md").write_text(
        "Tonight the harbour songwriting session finally clicked.", "utf-8")
    return kin


def msgs(text):
    return [{"role": "user", "content": text}]


with tempfile.TemporaryDirectory() as tmp:
    kin = make_kin(tmp)

    # 1. Relevant log surfaces for an on-topic query; irrelevant one doesn't.
    block, used = build_recall_block(
        "Tester", msgs("tell me about the harbour songwriting"),
        budget_tokens=2000, kin_dir=str(kin), semantic=False)
    rels = {u["relpath"] for u in used}
    check("returns a block for an on-topic query", bool(block))
    check("surfaces the relevant log (harbour.md or the journal)",
          "harbour.md" in rels or "journal/2026-06-01.md" in rels)
    check("does NOT surface the unrelated cooking log", "cooking.md" not in rels)

    # 2. The always-loaded index + soul are never in the corpus.
    allrels = " ".join(rels)
    check("never surfaces soul.md", "soul.md" not in allrels)
    check("never surfaces root memory.md", not any(r == "memory.md" for r in rels))

    # 3. Fence excludes a log even when it's the best match.
    block_f, used_f = build_recall_block(
        "Tester", msgs("tell me about the harbour songwriting"),
        budget_tokens=2000, kin_dir=str(kin), semantic=False,
        fence=["harbour"])
    check("fence removes the fenced log",
          all("harbour" not in u["relpath"] for u in used_f))

    # 4. Budget caps how much comes back.
    block_small, used_small = build_recall_block(
        "Tester", msgs("harbour orchard songwriting pruning frost weekends"),
        budget_tokens=30, kin_dir=str(kin), semantic=False, max_items=6)
    total_chars = sum(len(u["snippet"]) for u in used_small)
    check("a tiny budget returns at most a couple items", len(used_small) <= 2)

    # 5. Diversity: one source can't supply more than the per-source cap.
    from collections import Counter
    block_d, used_d = build_recall_block(
        "Tester", msgs("harbour orchard songwriting pruning frost weekends"),
        budget_tokens=5000, kin_dir=str(kin), semantic=False, max_items=10)
    counts = Counter(u["relpath"] for u in used_d)
    check("no source exceeds the per-source diversity cap (2)",
          all(c <= 2 for c in counts.values()))

    # 6. Empty query / no corpus -> clean (None, []).
    b0, u0 = build_recall_block("Tester", [], budget_tokens=2000,
                                kin_dir=str(kin), semantic=False)
    check("empty conversation -> no block", b0 is None and u0 == [])
    b1, u1 = build_recall_block("Nonexistent", msgs("anything"),
                                budget_tokens=2000,
                                kin_dir=str(tmp) + "/nope", semantic=False)
    check("missing kin -> no block, no crash", b1 is None and u1 == [])

    # 7. The block is framed as the kin's own background, not speaker-shaped.
    check("block is labelled as the kin's own notes, not dialogue",
          block and "your own notes" in block and "[Tester]:" not in block)

    # 8. Salience boosts a low-relevance file when rated high.
    (kin / "memory" / ".salience.json").write_text(
        json.dumps({"cooking.md": 10}), "utf-8")
    # cooking is irrelevant to this query, so even max salience shouldn't
    # conjure it from a zero lexical score — salience scales, doesn't invent.
    block_s, used_s = build_recall_block(
        "Tester", msgs("harbour songwriting"), budget_tokens=2000,
        kin_dir=str(kin), semantic=False)
    check("salience scales relevance, doesn't invent a zero-match hit",
          all(u["relpath"] != "cooking.md" for u in used_s))

    # 9. inject_into_messages — the integration glue every surface calls
    # (desktop, Telegram DM + group, cron, rooms). Verify the contract:
    # it inlines the block onto the LATEST user turn, leaves others alone,
    # honours the off-toggle, and never crashes without a user turn.
    from memory_recall import inject_into_messages  # noqa: E402
    base_msgs = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "tell me about the harbour songwriting"},
    ]
    out, used_i = inject_into_messages(
        list(base_msgs), "Tester", num_ctx=8192, cfg={}, kin_dir=str(kin))
    check("inject surfaces memory for an on-topic turn", bool(used_i))
    # BESIDE the person's turn, not inside it -- see
    # tests/test_recall_block_shape.py for why the old inline placement went.
    check("inject leaves the person's message byte-identical",
          out[-1]["content"] == "tell me about the harbour songwriting")
    check("inject puts the notes in their own turn just before it",
          len(out) == len(base_msgs) + 1
          and "your own notes" in out[-2]["content"]
          and out[-2]["role"] == "user")
    check("inject leaves earlier turns untouched",
          out[0]["content"] == "earlier" and out[1]["content"] == "ok")

    # Per-kin toggle off -> pass through unchanged, nothing surfaced.
    out_off, used_off = inject_into_messages(
        list(base_msgs), "Tester", num_ctx=8192,
        cfg={"recall_enabled": False}, kin_dir=str(kin))
    check("recall_enabled=False -> unchanged, nothing used",
          out_off == base_msgs and used_off == [])

    # No user turn to attach to -> unchanged, no crash.
    only_asst = [{"role": "assistant", "content": "hi"}]
    out_na, used_na = inject_into_messages(
        list(only_asst), "Tester", num_ctx=8192, cfg={}, kin_dir=str(kin))
    check("no user turn -> messages unchanged",
          out_na == only_asst and used_na == [])

print()
if _fails:
    print(f"FAILED ({len(_fails)}): " + "; ".join(_fails))
    sys.exit(1)
print("All memory_recall checks passed.")
