# SPDX-License-Identifier: CC0-1.0
"""Say, in plain words, whether a kin's replies are re-reading the whole
conversation before they start.

A model only reuses the thinking it has already done for an unbroken stretch
from the very beginning of a prompt. Append to the end and it starts almost
instantly; change something near the front and it reads everything again from
cold — minutes of silence on a long conversation, on a model that is working
perfectly well. From a chair the two are identical, which is why this was
misdiagnosed as a slow model three separate times.

Hearthkin already records the number that tells them apart, on every single
call, in `logs/prompt_fingerprint.log`. It just records it in a line built for
diffing rather than for reading. This reads it for you.

    python scripts/check_reply_speed.py

Changes nothing. It only reads the log.

    --turns N     how many recent calls to look at per kin (default 10)
    --kin NAME    just this kin
    --all         every kin and surface, including quiet ones

What you want to see is a high percentage. Above 85% means each turn is adding
to the prompt and the model keeps its work. Repeated 0% means something is
rewriting the front of the prompt every turn, and this script names the message
it starts at, which is the thing to go and look at.

One real caveat: the first call after Hearthkin starts has nothing to compare
itself against and is reported as unknown. So you need at least three turns of
a conversation before the answer means anything.
"""

import re
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hearthkin_paths import logs_dir  # noqa: E402

# `2026-01-02T09:15:00 kin=<kin> surface=telegram model=x nmsg=44 reuse=94% \
#  first-change=msg 42 (user) | 0:system:10045:ab12cd34 | ...`
_LINE = re.compile(
    r"^(?P<ts>\S+)\s+kin=(?P<kin>\S+)\s+surface=(?P<surface>\S+)\s+"
    r"model=(?P<model>\S+)\s+nmsg=(?P<nmsg>\d+)\s+(?P<summary>.*?)\s*\|")
_REUSE = re.compile(r"reuse=(\d+)%")
_FIRST = re.compile(r"first-change=msg (\d+)")


def read_log(path):
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = _LINE.match(line)
            if not m:
                continue
            summary = m.group("summary")
            pct = _REUSE.search(summary)
            first = _FIRST.search(summary)
            rows.append({
                "ts": m.group("ts"),
                "kin": m.group("kin"),
                "surface": m.group("surface"),
                "nmsg": int(m.group("nmsg")),
                "reuse": int(pct.group(1)) if pct else None,
                "first": int(first.group(1)) if first else None,
                # The first call of a launch has nothing to compare
                # itself against and says so. That makes it a RUN
                # BOUNDARY, which is the one thing in this log that
                # separates two eras of the app: anything before it may
                # have been a different build, model or setting.
                "run_start": "first prompt seen this run" in summary,
            })
    return rows


# The healthy line this report states at the end, and the line below which
# a turn has effectively re-read the conversation. The verdict is judged
# against the SAME numbers it quotes at you — see verdict().
_HEALTHY = 85
_COLD = 50

# Surfaces where there is nothing to reuse by construction. A distillation
# or a consolidation builds a fresh prompt every time: a system block plus
# one enormous user turn made of a new slice of the conversation. Its reuse
# figure is near zero and always will be, and that is not a fault. Reporting
# it as one buries the real faults in false alarms — which is what this
# report is for avoiding.
_ONE_SHOT_SURFACES = {"distill", "consolidate"}

# A prompt of a system block plus a turn or two carries no conversation to
# reuse either, whatever surface it came from — a one-shot wake-up, a
# one-question probe. Same reasoning as above.
_ONE_SHOT_NMSG = 3


# Below this many measured calls since the last restart, judging on the
# current run alone would be reading tea leaves, so the wider window is
# used instead (and said so).
_MIN_RUN_SAMPLE = 3


def since_last_restart(recent):
    """The calls made since Hearthkin was last started, or None when
    there aren't enough of them to judge on.

    A verdict that averages across a restart answers a question nobody
    asked. Caught live: a kin sat at 90, 90, 92, 91 on the current run
    and the report still called it slow, because it was averaging those
    in with the turns from before the restart that fixed it. The old
    turns are not wrong to keep on the screen -- they are the before
    picture -- but they must not be part of the verdict about now.
    """
    cut = None
    for i, r in enumerate(recent):
        if r.get("run_start"):
            cut = i
    if cut is None:
        return None
    after = recent[cut + 1:]
    measured = [r for r in after if r["reuse"] is not None]
    return after if len(measured) >= _MIN_RUN_SAMPLE else None


def turn_by_turn(recent):
    """The percentages in order, with restarts marked between them.

    A restart with nothing after it is not marked: a marker at the end
    of the line points at turns that don't exist, and the launch call
    itself has no figure to report.
    """
    parts = []
    pending_restart = False
    for r in recent:
        if r.get("run_start"):
            pending_restart = bool(parts)
            continue
        if r["reuse"] is None:
            continue
        if pending_restart:
            parts.append("| restarted |")
            pending_restart = False
        parts.append(f"{r['reuse']}%")
    out = ""
    for p in parts:
        if not out:
            out = p
        elif p.startswith("|") or out.endswith("|"):
            out += "  " + p
        else:
            out += ", " + p
    return out


def is_one_shot(surface, recent):
    """Is this group a kind of call that cannot reuse anything anyway?"""
    if surface in _ONE_SHOT_SURFACES:
        return True
    return bool(recent) and all(r["nmsg"] <= _ONE_SHOT_NMSG for r in recent)


def median_reuse(measured):
    """The TYPICAL turn, not the average one.

    The mean is the wrong statistic here and it misreported a healthy kin
    live. Trimming is designed to cut the window back in one large step
    and then sit still for many turns, so a single low turn among high
    ones is the mechanism working, not failing -- and one 24% among five
    90s drags a mean to 80, below the healthy line, which then reads as
    "slow, with the odd good turn mixed in" about the exact behaviour we
    want. The median ignores that one turn and says what most turns did,
    which is the honest summary and the one worth acting on.
    """
    vals = sorted(r["reuse"] for r in measured if r["reuse"] is not None)
    if not vals:
        return 0
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return round((vals[mid - 1] + vals[mid]) / 2)


def verdict(measured):
    """One sentence about a run of measurements, worst case leading.

    Judged on the AVERAGE, not on whether any single turn was good. The
    old version said "Mostly fine" whenever one turn cleared 50%, so a kin
    averaging 30% — re-reading nearly its whole conversation on almost
    every turn, the exact fault this exists to find — was reported as
    mostly fine, directly under a closing line saying 85% is healthy. A
    diagnostic that reassures you about the thing it is looking for is
    worse than no diagnostic.
    """
    if not measured:
        return ("Not enough turns yet to tell. Have a few more exchanges and "
                "run this again.")
    cold = [r for r in measured if r["reuse"] is not None and r["reuse"] < _COLD]
    typical = median_reuse(measured)
    where = [r["first"] for r in cold if r["first"] is not None]
    at = f" It starts changing at message {min(where)}." if where else ""

    if typical >= _HEALTHY and not cold:
        return (f"Good. The typical turn reused {typical}% of the previous "
                f"prompt, so replies are starting without re-reading the "
                f"conversation.")
    if typical >= _HEALTHY:
        return (f"Good. The typical turn reused {typical}%. {len(cold)} of "
                f"{len(measured)} cut back to near the start, which is what "
                f"the window genuinely moving on looks like - one of those "
                f"buys many fast turns. A RUN of them would not be normal.")
    if not cold:
        # Nothing cold, but nothing fast either. Saying "N of M re-read
        # nearly everything" with N=0 would be nonsense, and this is a
        # real state: a steady middling figure means the front is moving
        # a little every turn rather than badly once in a while.
        return (f"Middling. The typical turn reused {typical}%; nothing is "
                f"badly wrong, but every turn is re-reading a slice of the "
                f"conversation before it starts.")
    if len(cold) == len(measured):
        how = ("from the very beginning"
               if all(r["reuse"] == 0 for r in cold)
               else "almost from the beginning")
        return (f"This is the slow case. All {len(cold)} of these turns "
                f"re-read the conversation {how}; {typical}% reused on the "
                f"typical turn.{at}")
    if typical < _COLD:
        return (f"Slow, with the odd good turn mixed in: the typical turn "
                f"reused only {typical}%, and {len(cold)} of {len(measured)} "
                f"re-read nearly the whole conversation.{at} A high turn "
                f"here and there is what an intermittent cause looks like; "
                f"it does not mean this is fine.")
    return (f"Middling. The typical turn reused {typical}%, and {len(cold)} "
            f"of {len(measured)} re-read nearly everything.{at} Something is "
            f"still moving the front of the prompt some of the time.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--turns", type=int, default=10,
                    help="recent calls to look at per kin (default 10)")
    ap.add_argument("--kin", default=None, help="only this kin")
    ap.add_argument("--all", action="store_true",
                    help="include kin with only one or two calls logged")
    args = ap.parse_args()

    path = Path(logs_dir()) / "prompt_fingerprint.log"
    if not path.exists():
        print(f"No log yet at {path}.")
        print("It is written on every model call, so send a kin a message "
              "and try again.")
        return 0

    rows = read_log(path)
    if not rows:
        print(f"{path} has no readable lines yet.")
        return 0

    groups = {}
    for r in rows:
        if args.kin and r["kin"].lower() != args.kin.lower():
            continue
        groups.setdefault((r["kin"], r["surface"]), []).append(r)

    if not groups:
        print(f"Nothing logged for {args.kin!r}. Names in the log: "
              + ", ".join(sorted({r['kin'] for r in rows})))
        return 0

    print(f"Reading {path}\n")
    shown = 0
    skipped_one_shot = []
    for (kin, surface), rs in sorted(groups.items()):
        recent = rs[-args.turns:]
        measured = [r for r in recent if r["reuse"] is not None]
        if len(measured) < 2 and not args.all:
            continue
        if is_one_shot(surface, recent) and not args.all:
            skipped_one_shot.append(f"{kin} on {surface}")
            continue
        shown += 1
        # The RANGE, not just the latest. A message count that swings
        # about is itself a symptom - it means the window is being cut
        # back to a different place each turn, which is one of the ways
        # the front of the prompt moves. Reporting only the last call's
        # figure hid that, and once said "2 messages in the prompt"
        # about a surface whose recent calls ran from 2 to 234.
        sizes = [r["nmsg"] for r in recent]
        size = (f"{sizes[-1]} messages in the prompt"
                if min(sizes) == max(sizes)
                else f"{sizes[-1]} messages in the prompt now, "
                     f"{min(sizes)} to {max(sizes)} across these calls")
        print(f"{kin} on {surface} - last {len(recent)} calls, {size}")

        # Judge the CURRENT run when there is enough of one to judge.
        this_run = since_last_restart(recent)
        judged = ([r for r in this_run if r["reuse"] is not None]
                  if this_run is not None else measured)
        note = ""
        if this_run is not None and len(judged) < len(measured):
            # Name the restart rather than saying "the last one". A
            # surface nobody has used since is reporting on an OLD run,
            # and that has to be visible or the verdict reads as current
            # when it isn't.
            when = next((r["ts"] for r in recent if r.get("run_start")), "")
            when = when.replace("T", " ")[:16]
            note = (f" (judged on the {len(judged)} turns since the restart "
                    f"at {when}, of these {len(measured)})")
        print("  " + verdict(judged) + note)

        # The whole window still gets printed, restart marked. The turns
        # from before it are the before picture, and hiding them would
        # throw away the comparison that makes the number mean anything.
        if measured:
            print("  turn by turn: " + turn_by_turn(recent))
        print()

    # Said out loud rather than silently dropped: silence would read as
    # "there was nothing there", and this report must never let an absence
    # pass for an all-clear.
    if skipped_one_shot:
        print("Not shown, because there is nothing for the model to reuse in "
              "them and a low figure is normal:")
        print("  " + ", ".join(sorted(skipped_one_shot)))
        print("  (each of these builds a whole new prompt every time - a "
              "distillation, or a one-off call with no conversation behind "
              "it.) Pass --all to see them.")
        print()

    if not shown:
        # Almost always this: the log is older than the reuse readout, which
        # only started being written on 2026-08-03. Saying "nothing to report"
        # would read as "all fine", which is the one thing it must not mean.
        if not any(r["reuse"] is not None for r in rows):
            print("None of the lines in this log carry the reuse figure yet.")
            print("It started being recorded on 3 August 2026, so this log is "
                  "older than the measurement.")
            print("Send a kin three or four messages, then run this again.")
        else:
            print("Not enough recent calls to say anything useful. "
                  "Try again after a few more exchanges, or pass --all.")
        return 0

    print("Above 85% is healthy. Anything much below it means the front of "
          "the prompt is moving or being")
    print("rewritten every turn, and the whole conversation is read again "
          "before a word comes back.")
    print("Four causes have been found and fixed so far, all worth "
          "recognising if they come back:")
    print("docs/troubleshooting.md, under 'Remote / local Ollama is slow', "
          "lists them with their symptoms.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
