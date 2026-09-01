"""Park-mode emote-extraction tests. Plain Python; run via tests/run_all.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from park_mode import extract_park_actions

# Stand-ins for tff_play.known_verbs() / known_targets(save).
VERBS = {"feed", "feeds", "pet", "pets", "care", "hold", "holds", "cuddles",
         "dig", "digs", "adopt", "build", "move", "breed", "look"}
TARGETS = {"luna", "bis", "indoor 1", "cat", "kitties", "bunnies", "village"}

_failures = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _failures.append(label)


def verbs_known(reply):
    return [(a.text, a.known) for a in extract_park_actions(reply, VERBS, TARGETS)]


# A known-verb action-emote -> a ready-to-run move.
check(verbs_known("*feeds luna* there you go") == [("feeds luna", True)],
      "known-verb emote -> runnable move")

# Feeling-emotes are left alone.
check(verbs_known("*smiles warmly* and *settles in*") == [],
      "feeling-emotes are not routed")

# Several moves, in order.
check(verbs_known("*digs a bit* then *pets the kitties*")
      == [("digs a bit", True), ("pets the kitties", True)],
      "multiple actions preserved in order")

# Mixed: feeling dropped, action kept.
check(verbs_known("*grins* *holds the bunnies close*")
      == [("holds the bunnies close", True)],
      "mixed feeling+action keeps only the action")

# Unknown verb + a REAL target -> a teachable move (known=False).
check(verbs_known("*grooms luna gently*") == [("grooms luna gently", False)],
      "unknown verb + target -> teachable move")

# Unknown verb with NO target -> left as feeling (not teachable).
check(verbs_known("*frolics around happily*") == [],
      "unknown verb, no target -> left as feeling")

# A long presence-emote that only mentions a creature deep in a descriptive
# clause is NOT a teachable move -- it's wistful presence, not an action. This
# is the Vesper case that dragged play into a teach-drill.
check(verbs_known(
      "*reaches out gently towards screen, fingers tracing invisible air "
      "around where luna might be*") == [],
      "target buried deep in a presence-emote -> not teachable")

# But an unknown verb with the target right at the front still teaches.
check(verbs_known("*grooms luna's soft fur for a while*")
      == [("grooms luna's soft fur for a while", False)],
      "unknown verb + target near the front -> still teachable")

# A feeling word wins even when a creature is named (*smiles at luna*).
check(verbs_known("*smiles at luna*") == [],
      "feeling word + target still stays feeling")

# Short creature name doesn't false-match inside a longer word.
check(extract_park_actions("*wanders past the business*", VERBS, {"bis"}) == [],
      "single-word target matches on a word boundary, not substring")

# Plain prose / empty / guards.
check(verbs_known("just talking, no actions") == [], "plain prose -> nothing")
check(extract_park_actions("", VERBS, TARGETS) == [], "empty reply -> []")
check(extract_park_actions("*feeds luna*", set(), TARGETS) == [],
      "no known verbs -> []")
# No targets supplied -> teachable path disabled, known verbs still route.
check([a.known for a in extract_park_actions("*grooms luna*", VERBS, None)] == [],
      "no targets -> unknown-verb emote not routed")
check([a.text for a in extract_park_actions("*feeds luna*", VERBS, None)]
      == ["feeds luna"], "no targets -> known-verb emote still routes")

print()
if _failures:
    print(f"FAILED: {len(_failures)}: {_failures}")
    sys.exit(1)
print("ALL PARK-MODE CHECKS PASSED")
