# SPDX-License-Identifier: CC0-1.0
"""The slow-reply report must not reassure you about the fault it exists
to find.

`scripts/check_reply_speed.py` reads the per-call reuse figures and says,
in words, whether a kin's replies are re-reading the whole conversation
before they start. It closes by stating that above 85% is healthy — and
then called a kin averaging 30% "Mostly fine", because its verdict asked
only whether ANY single turn had cleared 50%. So a kin re-reading nearly
its whole conversation on seven turns out of nine was reported as fine,
four lines above the sentence saying what fine means. Run live, that is
what the report actually said.

A diagnostic that reassures you about the thing it is looking for is
worse than no diagnostic, because it also spends the attention you would
have given to looking properly.

The other half is false alarms. A distillation builds a whole new prompt
every time — a system block plus one large user turn — so its reuse
figure is near zero and always will be, and saying "this is the slow
case" about it buries the real faults among things that were never
wrong.

Run: python tests/test_reply_speed_report.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import check_reply_speed as rep  # noqa: E402

_failures = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _failures.append(label)


def turns(*pcts, first=1):
    return [{"reuse": p, "first": first, "nmsg": 100, "run_start": False}
            for p in pcts]


RESTART = {"reuse": None, "first": None, "nmsg": 50, "run_start": True,
           "ts": "2026-01-02T03:04:05"}


# ── the regression: a low average is never "fine" ────────────────────
# These are the real figures from the kin this was found on.
bad = rep.verdict(turns(0, 0, 19, 20, 20, 89, 19, 18, 87))
# The old wording, verbatim, is what must never come back. ("...it does
# not mean this is fine" is the new text and is the opposite claim, so
# match the verdict's OPENING, which is what gets read first.)
check(not bad.lower().startswith("mostly fine"),
      f"an average of 30% is not reported as mostly fine (said: "
      f"{bad[:60]}...)")
check(f"{rep.median_reuse(turns(0, 0, 19, 20, 20, 89, 19, 18, 87))}%" in bad,
      "it quotes the typical-turn figure it judged on")
check("slow" in bad.lower(),
      "it says plainly that this is the slow case")

# The stray good turn is the signature of an intermittent cause, not
# evidence that nothing is wrong — the report has to say so, because the
# reading it invites otherwise is exactly the wrong one.
check("intermittent" in bad.lower(),
      "a stray high turn among low ones is named as intermittent rather "
      "than treated as reassurance")

# ── the verdict agrees with the threshold the report prints ──────────
good = rep.verdict(turns(85, 98, 92, 65, 88))
check(good.lower().startswith("good"),
      f"a healthy run is called good (said: {good[:40]}...)")

allcold = rep.verdict(turns(0, 0, 0, 0))
check("slow case" in allcold and "very beginning" in allcold,
      "every turn at zero is the flat slow case, from the very beginning")

nearly = rep.verdict(turns(9, 11, 10, 10))
check("slow case" in nearly and "almost from the beginning" in nearly,
      "a run of 10% re-reads ALMOST from the beginning — it doesn't "
      "overclaim a zero it didn't measure")

middling = rep.verdict(turns(70, 72, 68, 71))
check(middling.startswith("Middling")
      and f"{rep.median_reuse(turns(70, 72, 68, 71))}%" in middling
      and " 0 of " not in middling,
      "a steady middling run is neither called good nor called fine, "
      "and doesn't claim 0 turns re-read everything")

# ── one designed window cut must not sink a healthy kin ──────────────
# The trim cuts the window back in one large step and then sits still
# for many turns, so a single low turn among high ones IS the mechanism
# working. Judged on the mean, one 24% among five 90s lands at 80 --
# under the healthy line -- and the report called a kin that had just
# been fixed "slow, with the odd good turn mixed in". These are its real
# figures from the turn after the fix went live.
after_fix = rep.verdict(turns(92, 24, 90, 90, 92, 91))
check(after_fix.startswith("Good"),
      f"one window cut among five fast turns reads as healthy (said: "
      f"{after_fix[:45]}...)")
check("90%" in after_fix,
      "and it quotes the TYPICAL turn, not an average one low turn "
      "dragged down")
check("RUN of them" in after_fix,
      "while still saying what would NOT be normal, so a real fault "
      "coming back is still recognisable")
check(rep.median_reuse(turns(92, 24, 90, 90, 92, 91)) >= 85
      and rep.median_reuse(turns(0, 0, 19, 20, 20, 89, 19, 18, 87)) < 50,
      "the typical-turn figure separates the two cases that the average "
      "confused")

# ── a verdict must not average across a restart ──────────────────────
# The turns before a restart may be a different build entirely -- which
# is exactly the case this was caught in, comparing a fixed app against
# its own before picture.
window = turns(18, 87, 17) + [RESTART] + turns(92, 24, 90, 90, 92, 91)
this_run = rep.since_last_restart(window)
check(this_run is not None and len(this_run) == 6,
      "the calls since the last restart are picked out")
check(all(r["reuse"] >= 24 for r in this_run),
      "and none of the pre-restart turns come with them")
check(rep.since_last_restart(turns(90, 91) + [RESTART] + turns(92, 90))
      is None,
      "with too few turns since a restart it declines to judge on them, "
      "rather than reading two calls as a trend")
check(rep.since_last_restart(turns(90, 91, 92)) is None,
      "no restart in the window means no special handling")

# ── the restart is visible in the turn-by-turn line ──────────────────
line = rep.turn_by_turn(window)
check("restarted" in line and line.index("18%") < line.index("restarted"),
      "the before picture is still printed, with the restart marked "
      f"between (got: {line})")
check(not rep.turn_by_turn(turns(90, 91) + [RESTART]).endswith("restarted |"),
      "a restart with nothing after it is not marked - the marker would "
      "point at turns that don't exist")

check(rep.verdict([]).startswith("Not enough turns"),
      "no measurements says so, rather than implying health")

# ── uncacheable calls are not reported as faults ─────────────────────
check(rep.is_one_shot("distill", [{"nmsg": 2}]),
      "a distillation is recognised as having nothing to reuse")
check(rep.is_one_shot("consolidate", [{"nmsg": 2}]),
      "so is a consolidation")
check(rep.is_one_shot("desktop-tool", [{"nmsg": 2}, {"nmsg": 3}]),
      "so is any surface whose calls are all a system block plus a turn")

# But a real conversation that merely ENDS on a short prompt is still a
# real conversation, and must keep being watched.
check(not rep.is_one_shot("desktop-tool", [{"nmsg": 234}, {"nmsg": 2}]),
      "a conversation surface whose latest call happens to be short is "
      "still reported on")

# ── the output has to survive a Windows console ──────────────────────
# It is read in a terminal that mangles anything outside cp1252 into a
# replacement character, so the report keeps its own text ASCII.
samples = [bad, good, allcold, nearly, middling, rep.verdict([])]
offenders = sorted({c for s in samples for c in s if ord(c) > 127})
check(not offenders,
      f"the spoken-aloud verdicts stay ASCII so the console can print "
      f"them (found {offenders})")

print()
if _failures:
    print(f"FAILED ({len(_failures)}):")
    for f in _failures:
        print("  - " + f)
    sys.exit(1)
print("ALL PASS")
