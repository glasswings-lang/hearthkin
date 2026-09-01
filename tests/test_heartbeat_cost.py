# SPDX-License-Identifier: CC0-1.0
"""Guard test: a heartbeat carries a bounded amount of conversation.

A heartbeat asks one question — *do you feel like saying something?* — and the
honest answer is usually no. It was loading the kin's entire conversation to
ask it. Measured on a real install: prompts built from as much as 216,000
tokens, arriving at the model at 22,000 after truncation, roughly 280 seconds
of prefill each, at 28% of all model calls. Every one of those competed for the
machine with a person waiting on a real reply — a kin's whole life re-read from
the beginning to decide whether to say hello.

The budget is in TOKENS, not turns, and that distinction is the point of this
file. A turn count sounds equivalent and fails exactly where it matters: a kin
being fed long passages has turns of 1,200+ tokens, so a twelve-turn cap was
still 20,000 of them — a limit on the number with the cost left unbounded. The
first version of this fix did precisely that, and only measuring against a real
kin mid-way through reading a book caught it.

Pinned here:
  * the budget actually bounds SIZE, including when individual turns are huge;
  * it keeps the RECENT tail, because "what just happened" is the whole input
    to the question;
  * at least one turn always survives — a heartbeat with no idea what is going
    on is worse than a slightly expensive one;
  * soul and memory are never trimmed; they are who the kin is, not history;
  * 0 restores carrying everything, for the scheduled-wake-up path that wants
    it;
  * and a broken estimator degrades to a heuristic instead of failing the
    wake-up.
"""

import os
import sys
import json
import tempfile

# Sandbox BEFORE the import that reaches kin_persistence.
os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="hb-cost-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hearthkin_cron as hc  # noqa: E402
import kin_persistence as kp  # noqa: E402
from chat_helpers import estimate_tokens as est  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def make_kin(name, turns, words_per_turn):
    d = kp.agent_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "soul.md").write_text(f"You are {name}. " + "soul " * 200, encoding="utf-8")
    (d / "memory.md").write_text("- something remembered\n" * 20, encoding="utf-8")
    with open(d / "conversation.jsonl", "w", encoding="utf-8") as f:
        for i in range(turns):
            f.write(json.dumps({
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"turn{i} " + "word " * words_per_turn}) + "\n")
    return d


def tokens(msgs):
    return est("".join(m.get("content") or "" for m in msgs))


def history_tokens(msgs):
    """Just the conversation part — everything between the system prompt and
    the final question."""
    return est("".join(m.get("content") or "" for m in msgs[1:-1]))


# --- ordinary kin: the budget binds ---------------------------------------

make_kin("Chatty", turns=400, words_per_turn=40)
full = hc._build_messages("Chatty", "heartbeat?", enabled_tools=[])
capped = hc._build_messages("Chatty", "heartbeat?", enabled_tools=[], history_tokens=2500)
check("uncapped carries the whole conversation — many times the budget",
      history_tokens(full) > 2500 * 5)
check("capped is dramatically smaller", tokens(capped) < tokens(full) / 5)
check("history stays within the budget", history_tokens(capped) <= 2500)


# --- the bug this file exists for: ENORMOUS turns -------------------------
#
# A turn-count cap passes here while leaving the cost unbounded. Only a size
# budget holds.

make_kin("Reader", turns=40, words_per_turn=1200)   # ~1,600 tokens per turn
capped = hc._build_messages("Reader", "heartbeat?", enabled_tools=[], history_tokens=2500)
check("a kin with 1,600-token turns is STILL bounded (turn counts are not)",
      history_tokens(capped) <= 2500)
check("...which means only a couple of turns come along",
      2 <= len(capped) <= 5)


# --- recency, and never nothing -------------------------------------------

msgs = hc._build_messages("Chatty", "heartbeat?", enabled_tools=[], history_tokens=2500)
check("it keeps the RECENT tail, not the beginning",
      "turn399" in msgs[-2].get("content", "") and "turn0" not in "".join(
          m.get("content", "") for m in msgs[1:-1]))
check("the question is still the last thing said",
      msgs[-1]["content"] == "heartbeat?")
check("soul and memory survive untouched — they are who the kin is",
      "You are Chatty" in msgs[0]["content"]
      and "something remembered" in msgs[0]["content"])

# One turn bigger than the entire budget must still come through: a heartbeat
# with no idea what just happened is worse than a slightly expensive one.
make_kin("Monologue", turns=3, words_per_turn=5000)
msgs = hc._build_messages("Monologue", "heartbeat?", enabled_tools=[], history_tokens=500)
check("a single oversized turn is kept rather than dropping history entirely",
      len(msgs) >= 3 and history_tokens(msgs) > 500)


# --- 0 means everything, for the scheduled-wake-up path -------------------

a = hc._build_messages("Chatty", "x", enabled_tools=[], history_tokens=0)
b = hc._build_messages("Chatty", "x", enabled_tools=[])
check("0 carries everything, as a scheduled wake-up wants",
      len(a) == len(b) and len(a) > 100)


# --- the sizer must never be the reason a wake-up fails -------------------

_real = hc._est_tokens
try:
    import chat_helpers
    _real_est = chat_helpers.estimate_tokens

    def _boom(_t):
        raise RuntimeError("estimator exploded")

    chat_helpers.estimate_tokens = _boom
    n = hc._est_tokens("some text here")
    check("a broken estimator falls back to a heuristic instead of raising",
          isinstance(n, int) and n > 0)
    msgs = hc._build_messages("Chatty", "x", enabled_tools=[], history_tokens=2500)
    check("...and a heartbeat still builds", len(msgs) >= 2)
finally:
    chat_helpers.estimate_tokens = _real_est


print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_heartbeat_cost: all checks passed")
