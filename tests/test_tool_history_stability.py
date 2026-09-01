# SPDX-License-Identifier: CC0-1.0
"""Guard test: the prompt must be APPEND-ONLY between turns.

A local model reuses its cached work only for an unbroken run from the very
start of the prompt. Change one message early and everything after it is read
again from cold.

`_compact_tool_history` was breaking that on almost every turn, by design and
without anyone noticing. It kept the last `tool_history_keep` tool round-trips
verbatim and summarised the rest — recomputed from the whole conversation each
time. So every new tool call pushed one round-trip out of the window and
rewrote it, in the MIDDLE of the history, from full text into a one line
summary.

Measured on a real kin before the fix, from prompt_fingerprint.log: on three of
five consecutive turns the shared prefix was ONE message. 22,000+ tokens
re-read at about 78 tokens a second — roughly five minutes of waiting before a
reply began, on a conversation that had gained one short message. It had been
"fixed" three times, always by stabilising the system prompt, which is a
different thing one message further up.

The fix is `_compaction_frontier`: the boundary moves in steps of
`keep_window` rather than one pair at a time, so it is byte-identical between
steps and the prompt really is append-only.

What this file pins is the PROPERTY, not the arithmetic: replay a growing
conversation turn by turn and assert the prefix never changes except at the
rare, bounded step. A test of the formula alone would keep passing if someone
"simplified" it back to a sliding window.

Run: python tests/test_tool_history_stability.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="compact-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


from frame.render_mixin import RenderMixin  # noqa: E402

# A bare instance: these two are pure, but _compact_tool_history calls the
# frontier through `self`, so it needs a real object rather than None.
_SELF = type("_JustTheMixin", (RenderMixin,), {})()
compact = lambda convo, keep: RenderMixin._compact_tool_history(_SELF, convo, keep)
frontier = RenderMixin._compaction_frontier

KEEP = 5


def build(n_pairs):
    """A conversation of `n_pairs` tool round-trips with ordinary chat between,
    shaped like the real thing: assistant-with-tool_calls, tool result, reply."""
    convo = [{"role": "user", "content": "first message"}]
    for i in range(n_pairs):
        convo.append({"role": "assistant", "content": "",
                      "tool_calls": [{"id": f"c{i}", "type": "function",
                                      "function": {"name": "read_file",
                                                   "arguments": '{"path": "x"}'}}]})
        convo.append({"role": "tool", "tool_call_id": f"c{i}",
                      "content": f"result number {i} " + ("payload " * 200)})
        convo.append({"role": "assistant", "content": f"reply after call {i}"})
        convo.append({"role": "user", "content": f"question {i}"})
    return convo


def fingerprint(messages):
    """What the model would actually see, per message."""
    out = []
    for m in messages:
        tc = m.get("tool_calls")
        out.append((m.get("role"), str(m.get("content"))[:400],
                    str(tc)[:200] if tc else ""))
    return out


def shared_prefix(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


# --- the property: replay turn after turn --------------------------------

prev = None
rewrites = []
for pairs in range(1, 26):
    cur = fingerprint(compact(build(pairs), KEEP))
    if prev is not None:
        shared = shared_prefix(prev, cur)
        # A pure append means every message the model already saw is untouched.
        if shared < len(prev):
            rewrites.append((pairs, shared, len(prev)))
    prev = cur

check("most turns are pure appends — nothing earlier is rewritten",
      len(rewrites) <= 25 // KEEP + 1)
print(f"       ({len(rewrites)} rewrites across 25 turns; "
      f"the old sliding window rewrote on ~20 of them)")
# One turn AFTER the window fills: at KEEP pairs everything still fits, and
# the pair that arrives next is what pushes the frontier forward a whole step.
check("...and rewrites only happen on the step boundary",
      all((p - 1) % KEEP == 0 for p, _, _ in rewrites))
if rewrites:
    print("       rewrote at pair counts: " + ", ".join(str(p) for p, _, _ in rewrites))


# --- the newest round-trip is always intact ------------------------------
# A kin has to see the call it just made, whatever the frontier is doing.
for pairs in range(1, 15):
    out = compact(build(pairs), KEEP)
    tool_msgs = [m for m in out if m.get("role") == "tool"]
    newest_intact = any(f"result number {pairs - 1}" in (m.get("content") or "")
                        for m in tool_msgs)
    if not newest_intact:
        check(f"the newest tool result survives verbatim ({pairs} pairs)", False)
        break
else:
    check("the newest tool result always survives verbatim", True)


# --- the person's setting is a CEILING, not a suggestion -----------------
# Overshooting would grow the prompt beyond what was configured, and an
# oversized context on local Ollama returns nothing at all rather than
# degrading.
_over = []
for pairs in range(1, 40):
    kept = pairs - frontier(pairs, KEEP)
    if kept > KEEP:
        _over.append((pairs, kept))
check("never keeps more pairs verbatim than tool_history_keep allows",
      not _over)
if _over:
    print("       overshot at:", _over[:5])
check("...and always keeps at least one",
      all(pairs - frontier(pairs, KEEP) >= 1 for pairs in range(1, 40)))


# --- the old behaviours that must not change -----------------------------

check("keep_window of 0 still compacts everything",
      frontier(9, 0) == 9)
check("a conversation with no tool calls is returned untouched",
      compact([{"role": "user", "content": "hi"}], KEEP)
      == [{"role": "user", "content": "hi"}])
check("an empty conversation is still empty", compact([], KEEP) == [])
check("compaction does not mutate the input",
      (lambda c: (compact(c, 1), c == build(4))[1])(build(4)))

# Older pairs really are summarised — the point of the feature survives.
_out = compact(build(20), KEEP)
_summaries = [m for m in _out if m.get("role") == "system"
              and "earlier tool call" in (m.get("content") or "")]
check("older round-trips are still compacted to one-line markers",
      len(_summaries) >= 10)
# The marker keeps a short PREVIEW of the result on purpose — the kin should
# know roughly what came back. What must not survive is the bulk: compare
# against the ~1,600-character payload the round-trip originally carried.
_orig = len(build(1)[2]["content"])
check("...and each marker is a small fraction of the payload it replaced",
      _orig > 1000
      and all(len(m.get("content") or "") < _orig / 3 for m in _summaries))
print(f"       (markers {max(len(m.get('content') or '') for m in _summaries)} "
      f"chars at most, replacing {_orig}-char results)")


print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_tool_history_stability: all checks passed")
