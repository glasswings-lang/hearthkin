# SPDX-License-Identifier: CC0-1.0
"""Guard test: the Memory tab's distillation-progress line.

That line is the only instrument an operator has for whether distillation
is alive. The old version printed a bare "N msgs undistilled" — identical
whether a walk was chewing through chunks, had finished, had stalled
twenty minutes earlier, or was reporting a bookmark it had failed to read.
A number that is merely stale is indistinguishable from one that is wrong,
and an operator with no second instrument has no way to tell.

These checks pin what the line must always disclose: the bookmark beside
the total (not only their difference), whether a walk is running, when the
position last moved, when the bookmark was silently reset for being past
the end, and — when the position couldn't be read at all — that fact
rather than a number nobody can vouch for.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dialogs.edit_kin import distill_progress_parts  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def line(**kw):
    kw.setdefault("stored", kw.get("bookmark", 0))
    kw.setdefault("trusted", True)
    kw.setdefault("walking", False)
    kw.setdefault("advanced_at", None)
    return " · ".join(distill_progress_parts(**kw))


def main():
    # The bookmark is visible, not hidden inside a subtraction.
    s = line(bookmark=100, total=1000, advanced_at="2026-07-26T02:43:16")
    check("shows the bookmark beside the total", "100 of 1000" in s)
    check("still shows what's left", "900 to go" in s)

    # Freshness. A live walk and one that died must not read alike.
    check("says when it last advanced", "last advanced 02:43:16" in s)
    check("a stalled walk looks different from a running one",
          line(bookmark=100, total=1000, walking=True) !=
          line(bookmark=100, total=1000, walking=False))
    check("says so plainly while redistilling",
          "redistilling now" in line(bookmark=100, total=1000, walking=True))
    # In the words on the button, not the words in the code. "Walk" is
    # this module's name for the from-start chain and has never appeared
    # anywhere a reader could learn it — the button beside this line says
    # redistill, so the line says redistill.
    check("doesn't say 'walking' at a reader",
          "walking" not in line(bookmark=100, total=1000, walking=True))
    check("admits when the advance time isn't recorded",
          "not recorded" in line(bookmark=100, total=1000))

    # Paused: recorded as unfinished on disk, nothing running. Closing
    # Hearthkin part-way, or one chunk erroring, both land here. It used
    # to be indistinguishable from "nobody ever started one", and the
    # only remedy on offer was the button that starts over from zero —
    # which is how the same history got redistilled from the beginning
    # several times.
    paused = line(bookmark=100, total=1000, paused=True)
    check("a paused redistill says it's paused", "paused" in paused)
    check("and points at the button that continues it",
          "Continue redistilling" in paused)
    check("paused doesn't read like running",
          paused != line(bookmark=100, total=1000, walking=True))
    check("paused doesn't read like idle",
          paused != line(bookmark=100, total=1000))
    # Running wins: while the chain is live, the on-disk record is also
    # set, and reporting both would be a contradiction read aloud.
    running = line(bookmark=100, total=1000, walking=True, paused=True)
    check("running beats paused when both are set",
          "redistilling now" in running and "paused" not in running)

    # An unreadable position must never render as a number.
    bad = line(bookmark=0, total=1000, trusted=False)
    check("an unreadable position says so", "couldn't read" in bad)
    check("an unreadable position never claims a bookmark",
          "distilled" not in bad and "to go" not in bad)
    check("an unreadable position still reports the total", "1000" in bad)

    # The silent reset gets named.
    reset = line(stored=4905, bookmark=0, total=491)
    check("a bookmark past the end is explained",
          "past the end" in reset and "4905" in reset)
    check("and the reset itself is still shown",
          "nothing distilled yet" in reset)

    # Nothing distilled yet reads as that, not as "0 of N".
    fresh = line(bookmark=0, total=1000)
    check("a fresh scope reads plainly", "nothing distilled yet" in fresh)
    check("a fresh scope doesn't claim a stale advance time",
          "last advanced" not in fresh)

    # ---- the auto-fire claim ---------------------------------------
    # Auto-fire is gated on _is_distill_in_flight, so while a walk runs it
    # cannot fire no matter how far over threshold the backlog sits.
    # Claiming otherwise — on every refresh, for the whole length of a
    # walk — is simply false.
    mid = line(bookmark=400, total=1000, walking=True, pct=880.0, at_pct=70)
    check("doesn't promise auto-fire while a walk is running",
          "fires on the next turn" not in mid)
    check("explains that auto-fire is held instead",
          "held until the walk finishes" in mid)

    # ...but "held, over threshold" must not be claimed when the backlog
    # is actually UNDER threshold — e.g. the walk's final chunk, where
    # everything is distilled and the tail is empty.
    tail_end = line(bookmark=1000, total=1000, walking=True, pct=0.0, at_pct=70)
    check("doesn't claim over-threshold when the backlog is empty",
          "over the 70% threshold" not in tail_end)
    check("still notes auto-fire is held while walking",
          "held while walking" in tail_end)

    idle = line(bookmark=400, total=1000, walking=False, pct=880.0, at_pct=70)
    check("does say it'll fire when nothing's walking",
          "fires on the next turn" in idle)
    under = line(bookmark=950, total=1000, walking=False, pct=12.0, at_pct=70)
    check("under threshold says when it would fire",
          "auto-fires at 70%" in under)

    # The percentage must come from the same bookmark as the counts; when
    # it can't be computed, say so rather than printing a stale figure.
    unknown = line(bookmark=400, total=1000, pct=None, at_pct=70)
    check("an uncomputable percentage says so",
          "context share unknown" in unknown)
    check("and makes no threshold claim without a percentage",
          "threshold" not in unknown and "auto-fires at" not in unknown)

    # A finished walk.
    done = line(bookmark=1000, total=1000, advanced_at="2026-07-26T03:10:00")
    check("a finished scope shows zero to go", "0 to go" in done)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
        return 1
    print("all distill-progress-display checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
