# SPDX-License-Identifier: CC0-1.0
"""The FIRST recalled note is exempt from being dropped, not from being bounded.

The selection loop carries `and used` on its caps so that the single best note
always gets in: having qualified, it is shown, and a turn that really matched
something can never come back empty-handed. That part is right.

What it also did was skip the size cap entirely for that note, because `used`
was still empty when it was considered. So the cap applied to the second note
onward and never to the first. On a real kin whose recall returned exactly one
note on every turn, it therefore never applied at all: 820 characters of notes
against a 115-character message, seven times the person's own words -- shipped
under a commit whose subject line was "never outweigh the message".

That is not a cosmetic overrun. Whatever is largest in a turn is what gets
answered, so an unbounded note is the person's own turn being talked over by
their kin's filing, and the kin then replies to the filing.

The fix trims that note to the cap instead of dropping it, so both properties
hold at once. What this file pins:

  * a single oversized note is bounded by the cap
  * ...and is still SHOWN, not dropped (the whole point of the exemption)
  * a note that already fits is passed through byte-identical
  * the cut lands on a boundary rather than mid-word
  * the cap tracks the length of the message when that is the larger number

Each "it was trimmed" assertion is paired with a positive control proving the
note qualified in the first place -- a gate that silently rejected the fixture
would otherwise read as a cap doing its job.
"""

import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="recallcap-"))

from memory_recall import build_recall_block, _fit_to_cap  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def make_kin(root, name, notes):
    kin = pathlib.Path(root) / "kin" / name
    mem = kin / "memory"
    mem.mkdir(parents=True)
    for fname, body in notes.items():
        (mem / fname).write_text(body, "utf-8")
    return kin


def recall(kin, text, **kw):
    kw.setdefault("budget_tokens", 5898)
    kw.setdefault("max_items", 6)
    kw.setdefault("semantic", False)
    kw.setdefault("min_block_chars", 500)
    return build_recall_block("Tester", [{"role": "user", "content": text}],
                              kin_dir=str(kin), **kw)


# One note, one chunk, deliberately far over the cap. Invented whole: this repo
# is public, and a fixture built from real material is a leak waiting to
# happen. Only the SHAPE matters -- a single long unbroken passage carrying a
# distinctive word the live turn also uses.
LONG = (
    "The greenhouse thermostat has a habit of drifting overnight and nobody "
    "has ever worked out why. "
    + ("It reads two degrees low by morning and corrects itself once the sun "
       "reaches the east glass, which makes it look like a sensor fault when "
       "it behaves more like a draught. ") * 12
    + "The last word of this note is quinceberry."
)
SHORT = "The greenhouse thermostat drifts overnight; nobody knows why."

# Filler on unrelated subjects, present in BOTH corpora for one reason: BM25
# scores a term by how FEW documents hold it, so in a corpus of exactly one
# chunk every term appears everywhere and scores zero. A single-note kin
# therefore recalls nothing regardless of how well it matches -- a property of
# the maths, not a gate deciding anything, and a fixture built that way tests
# nothing while looking like a filter working.
FILLER = {
    "ferry.md": "The morning ferry leaves at ten past six and the queue for "
                "it starts forming outside the bakery about twenty minutes "
                "earlier, mostly the same faces every week.",
    "lathe.md": "The lathe in the workshop wants its belt tensioned roughly "
                "every second month, and it announces this by squealing on "
                "start-up rather than by slipping under load.",
}

root = tempfile.mkdtemp(prefix="recallcap-kin-")
kin = make_kin(root, "Tester", dict(FILLER, **{"greenhouse.md": LONG}))
kin_small = make_kin(root, "Small", dict(FILLER, **{"greenhouse.md": SHORT}))

# ── the oversized note ────────────────────────────────────────────────────
live = "what's up with the greenhouse thermostat?"
block, used = recall(kin, live)

check("the oversized note qualifies at all (positive control)",
      bool(block) and bool(used))
check("...and its opening survives, so it really is the note",
      bool(block) and "greenhouse thermostat" in block)

cap = max(500, len(live))
check("the note is bounded by the cap, not passed through whole",
      bool(block) and len(block) < len(LONG))
check("...and the tail of the note is gone",
      bool(block) and "quinceberry" not in block)
check("the kept text respects the cap (allowing for the frame around it)",
      bool(block) and len(block) <= cap + 600)
check("the cut lands on a boundary, not mid-word",
      bool(block) and not block.rstrip().endswith(("draugh", "correc", "behav")))

# ── a note that already fits is untouched ─────────────────────────────────
block_s, used_s = recall(kin_small, live)
check("a short note qualifies too (positive control)",
      bool(block_s) and bool(used_s))
check("...and is passed through whole, with no gratuitous trimming",
      bool(block_s) and SHORT in block_s)

# ── the cap follows the message when the message is the longer one ────────
long_live = ("what's up with the greenhouse thermostat, because it has been "
             "reading wrong every morning this week and I cannot tell whether "
             "that is the sensor or a draught coming in somewhere near the "
             "east glass, and I would like to stop guessing about it. ") * 3
block_l, _ = recall(kin, long_live)
check("a longer message earns a longer note",
      bool(block_l) and bool(block) and len(block_l) > len(block))

# ── the helper itself ─────────────────────────────────────────────────────
check("_fit_to_cap leaves short text alone",
      _fit_to_cap("hello there", 500) == "hello there")
check("_fit_to_cap cuts at a sentence end when there is one",
      _fit_to_cap("One. Two. Three. Four.", 12) == "One. Two.")
check("_fit_to_cap never returns more than the cap",
      len(_fit_to_cap("x" * 900, 300)) <= 300)
check("_fit_to_cap with no cap does nothing",
      _fit_to_cap("abc def", 0) == "abc def")

print()
if _fails:
    print(f"{len(_fails)} FAILED:")
    for f in _fails:
        print("  " + f)
    sys.exit(1)
print("all first-note cap checks passed")
