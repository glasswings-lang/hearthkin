# SPDX-License-Identifier: CC0-1.0
"""The trim point must not move when nothing has changed.

A local model reuses its cached work only for an unbroken run from the
very start of the prompt. `_truncate_messages` already knows that — it
drops history in quantized chunks so the kept window's start stays put
for many turns at a time. It is a pure function of its budget: same
budget, same trim point, same prefix, warm cache.

The budget was the leak. It is `max_context_tokens / ratio`, where
`ratio` is a per-kin calibration updated by an EMA after EVERY
conversational call — so it moved a fraction of a percent every turn,
forever, because two real prompts of the same size genuinely tokenize a
little differently. A budget that never settles is a trim point that
never settles, and every turn re-read the whole conversation from cold.

Measured by replaying a real kin's history before the fix: with the
ratio held still the window start didn't move once in twelve turns; with
it drifting the way the EMA actually drifts, it moved on all twelve —
sometimes backwards, dragging older messages back in. A wobble of half a
percent was enough to make it oscillate between two points indefinitely.
That is the difference between a reply starting in seconds and one
spending minutes before its first word, with nothing in the window to
say why.

Two defences, and this file carries a positive control for both: the old
behaviour is run alongside and the test fails if it would have passed.

Run: python tests/test_truncation_budget_stability.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import llm_backend as lb  # noqa: E402

_failures = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _failures.append(label)


# A synthetic conversation big enough to need trimming, shaped like a
# real one: alternating turns of uneven size, growing at the end.
def conversation(turns):
    msgs = [{"role": "system", "content": "S" * 8000}]
    for i in range(turns):
        msgs.append({"role": "user", "content": "u%d " % i + "x" * (200 + (i * 37) % 900)})
        msgs.append({"role": "assistant", "content": "a%d " % i + "y" * (400 + (i * 53) % 1800)})
    return msgs


MAXCTX = 32768 - 2000
KEY = ("Testkin", "telegram-dm", MAXCTX)


def window_start(seq, budget):
    """Index of the oldest surviving conversational message."""
    out, cut = lb._truncate_messages(list(seq), budget)
    if not cut:
        return 0
    ids = {id(m): i for i, m in enumerate(seq)}
    idxs = [ids[id(m)] for m in out if id(m) in ids and i_is_convo(seq, ids[id(m)])]
    return min(idxs) if idxs else None


def i_is_convo(seq, i):
    return seq[i].get("role") != "system"


def moves(ratios, stabilize):
    """How many turns out of len(ratios)-1 moved the trim point."""
    lb._sticky_budgets.clear()
    moved = 0
    prev = None
    base = 300
    for k, ratio in enumerate(ratios):
        seq = conversation(base + k)
        raw = max(2048, int(MAXCTX / ratio))
        budget = lb._stable_truncation_budget(KEY, raw) if stabilize else raw
        start = window_start(seq, budget)
        if prev is not None and start != prev:
            moved += 1
        prev = start
    return moved


DRIFT = [1.30 - 0.008 * k for k in range(10)]        # an EMA settling
JITTER = [1.20 + 0.006 * ((-1) ** k) for k in range(10)]   # half-percent wobble

# ── positive control: the old behaviour really was this bad ──────────
check(moves(DRIFT, False) >= 8,
      "positive control: an unstabilized budget moves the trim point on "
      "nearly every turn while the ratio drifts")
check(moves(JITTER, False) >= 8,
      "positive control: half a percent of wobble is enough to move it "
      "every turn, forever")

# ── the fix ──────────────────────────────────────────────────────────
check(moves(DRIFT, True) <= 1,
      "a drifting ratio no longer moves the trim point turn by turn")
check(moves(JITTER, True) <= 1,
      "wobble around a quantum boundary can't flip the trim point back "
      "and forth")

# The one move left in each case above is the conversation genuinely
# outgrowing a chunk — the designed behaviour, one slow turn buying many
# fast ones. Assert the budget itself is now literally the same number
# every turn, which is the claim; counting trim moves alone would let a
# wobbling budget hide behind ordinary growth.
lb._sticky_budgets.clear()
seen = {lb._stable_truncation_budget(KEY, max(2048, int(MAXCTX / r)))
        for r in JITTER}
check(len(seen) == 1,
      f"the wobbling ratio yields one single budget, turn after turn "
      f"(got {sorted(seen)})")

# ── it may never hand the trim MORE room than was computed ───────────
# Too large a budget overruns num_ctx, and an oversized context on local
# Ollama returns nothing at all — strictly worse than a slow reply.
lb._sticky_budgets.clear()
overshoot = 0
for ratio in DRIFT + JITTER + [2.4, 1.1, 3.0, 1.05]:
    raw = max(2048, int(MAXCTX / ratio))
    overshoot = max(overshoot, lb._stable_truncation_budget(KEY, raw) - raw)
check(overshoot == 0,
      "the held budget never exceeds the computed one — it falls at once "
      "and only rises on a big change")

# ── a genuine, large change still gets through ───────────────────────
lb._sticky_budgets.clear()
small = lb._stable_truncation_budget(KEY, int(MAXCTX / 3.0))
back = lb._stable_truncation_budget(KEY, int(MAXCTX / 1.05))
check(back > small * 1.5,
      "a real change (a different model or tokenizer) still moves the "
      "budget, rather than pinning it forever")

# ── surfaces don't fight over one number ─────────────────────────────
lb._sticky_budgets.clear()
plain = lb._stable_truncation_budget(("K", "telegram-dm", MAXCTX), 20000)
tooled = lb._stable_truncation_budget(("K", "telegram-dm-tool", MAXCTX), 14000)
plain2 = lb._stable_truncation_budget(("K", "telegram-dm", MAXCTX), 20000)
check(plain == plain2 and tooled != plain,
      "a tool-enabled surface keeps its own budget — two surfaces "
      "alternating would otherwise reproduce the same churn")


# ── the ratio itself stops chasing noise ─────────────────────────────
def ratio_after(samples, *, start=1.20, num_ctx=32768):
    lb._token_calibration["Probe"] = start
    lb._calibration_loaded.add("Probe")
    saved = lb._save_calibration
    lb._save_calibration = lambda *a, **k: None
    try:
        for reported in samples:
            lb._update_token_calibration(
                "Probe", 10000, {"prompt_tokens": reported}, num_ctx=num_ctx)
    finally:
        lb._save_calibration = saved
    return lb._token_calibration["Probe"]


settled = ratio_after([12100, 11900, 12050, 11950] * 4)
check(abs(settled - 1.20) < 1e-9,
      "ordinary per-call variation no longer nudges the ratio at all — "
      f"it is the source of the moving budget (got {settled})")

changed = ratio_after([20000] * 5)
check(changed > 1.4,
      f"a genuinely different tokenizer still moves it (got {changed})")

capped = ratio_after([32700], num_ctx=32768)
check(capped > 1.20,
      f"a context-window overflow always gets through the deadband — "
      f"that one has to move now (got {capped})")

lb._token_calibration.pop("Probe", None)
lb._calibration_loaded.discard("Probe")
lb._sticky_budgets.clear()

print()
if _failures:
    print(f"FAILED ({len(_failures)}):")
    for f in _failures:
        print("  - " + f)
    sys.exit(1)
print("ALL PASS")
