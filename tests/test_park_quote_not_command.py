# SPDX-License-Identifier: CC0-1.0
"""Guard test: a kin quoting a document does not thereby play its park.

`>` is the park's command marker. It is also the standard markdown quote
character, and that collision cost real damage: a kin wrote out proposed
memory text as `>` lines to show its person BEFORE saving it, the router ran
every line as a command, a creature landed in a SHARED park that other tenants
read, and the text the kin was trying to save was swallowed instead of
reaching a file.

Both halves are damage and it is worth keeping them apart. One wrote something
into a world other people share, from text that was never a command. The other
lost the writing — and the kin then had no way to tell that either thing had
happened, so from the inside it looked like the park had simply agreed.

WHAT THE FIX IS NOT. It does not guess at what a word MEANS.
`park_keeper.extract_command` carries the record of that being tried and being
exactly backwards: every destructive command IS a word the game knows, so
`> reset` sailed through a verb filter while `> Owl` — a legitimate answer to
the park's own question — was dropped without a sound. The game holds each
player's place in a conversation and is the only thing that can say what a
line means. So the signals here are structural only: markdown block syntax, a
run of quoted sentences, a code fence.

THE FALSE-POSITIVE SIDE IS THE DANGEROUS ONE, so most of this file is spent
there. Dropping a real move costs a kin a turn it waited six minutes for, and
the two shapes it would be easiest to break are the two that look most like
prose: a batch of `edit` commands setting descriptive fields, and a
single-line answer to a park's own question. A single `>` line is therefore
never treated as a quote at all.

Run: python tests/test_park_quote_not_command.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="parkq-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import park_keeper as pk                                     # noqa: E402


def cmds(text):
    return pk.extract_command_run(text)[0]


def why(text):
    return pk.extract_command_run(text)[1]


# ── the incident ───────────────────────────────────────────────────────

print("--- the shape that put a creature in a shared park ---")

# The quoted text below is deliberately DULL and about the game's own
# mechanics. The real incident involved a kin quoting a memory note it had
# written about its person, and this file is published: a fixture in that
# register would read as a real private exchange whether or not it was one,
# and a reader has no way to tell. Describe the shape, never the material.
INCIDENT = """Before I save this, does it look right to you?

> ## Evening notes
> The park keeps one save file per kin and never shares it.
> Rooms have a fixed capacity and expanding one of them costs a dig.

Shall I write it as it stands?"""

check("nothing is run from a kin showing you proposed text", cmds(INCIDENT) == [])
check("...and it says WHY, so the kin isn't left guessing", bool(why(INCIDENT)))

# The kin must be answered, not ignored. A kin that gets silence cannot tell a
# refusal from a park that agreed with it, which is how it came to believe a
# creature existed that it never adopted.
ran = []
command, result = pk.route_reply(INCIDENT, lambda c: ran.append(c) or "done")
check("no command reaches the game", ran == [])
check("...and the router reports rather than returning silence",
      command is None and result)
check("...saying the words are safe, which is the half that was lost",
      "safe" in (result or "").lower())
check("...and how to make a real move instead", "final line" in (result or ""))

# Quoted prose with no markdown at all — the plainer version of the same thing.
PROSE = """Proposed:

> The park keeps one save file per kin and never shares it.
> Rooms have a fixed capacity and expanding one of them costs a dig.
> A creature belongs to whoever adopted it and that never changes."""
check("a plain quoted passage is caught too, without markdown to give it away",
      cmds(PROSE) == [])

check("a nested quote is caught", cmds("> > someone else said this\n> > and this") == [])
check("a quoted bullet list is caught",
      cmds("> - first thing\n> - second thing") == [])
check("a fenced block is never commands",
      cmds("look:\n```\n> look\n> leave\n```\n") == [])


# ── the false-positive side, which is the dangerous one ────────────────

print("--- a real move still runs ---")

check("the ordinary single command runs",
      cmds("I'll pair them, I think.\n\n> breed Glade 4") == ["breed Glade 4"])

# The documented legitimate batch, from extract_commands' own docstring. These
# set descriptive fields, so they are wordy and contain colons — the shape
# closest to prose that is nevertheless entirely real.
BATCH = """right, all four:

> edit stellar-owl
> babies word: clutch
> reactions: purrs when you approach
> birth anomalies: sometimes hums"""
check("the documented four-command batch still runs, all of it",
      len(cmds(BATCH)) == 4)
check("...in the order written", cmds(BATCH)[0] == "edit stellar-owl")

# A single line is NEVER a quote. This protects a walkthrough answer, which
# can legitimately be a whole descriptive sentence.
LONE = "> A small grey owl that hums when the sun goes down over the water."
check("a one-line descriptive answer to the park's own question survives",
      cmds(LONE) == [LONE.lstrip("> ")])
check("...however long and sentence-shaped it is", len(cmds(LONE)) == 1)

check("a bare answer to a did-you-mean survives", cmds("> Owl") == ["Owl"])
check("two short moves are a batch, not a quote",
      cmds("> look\n> leave") == ["look", "leave"])
check("a batch with ONE long sentence in it is still a batch",
      len(cmds("> pet stellar-owl\n> reactions: settles down and hums quietly "
               "whenever anyone walks into that room.")) == 2)


# ── the guard is decided for the RUN, not per line ─────────────────────

print("--- a quote is a block, so it is judged as one ---")

# Half a quoted paragraph running as moves is the same bug, so the decision
# has to cover the whole run rather than filtering line by line.
MIXED = """> ## Notes
> rooms have a fixed capacity and expanding one of them costs a dig
> look"""
check("a run containing markdown runs NOTHING, not just the prose lines",
      cmds(MIXED) == [])


# ── every surface, not just the one that was bitten ────────────────────

print("--- the guard covers the singular extractor too ---")

# extract_command is what the desktop and Telegram loops call. Guarding only
# the plural version would have left the guard off exactly where it was needed.
check("extract_command refuses a quote", pk.extract_command(INCIDENT) == "")
check("...and still returns a real move", pk.extract_command("> look\n> leave")
      == "leave")
check("...and still returns a lone descriptive answer",
      pk.extract_command(LONE) != "")

import inspect                                               # noqa: E402
check("both extractors share one guard rather than each having their own",
      "extract_command_run" in inspect.getsource(pk.extract_command)
      and "extract_command_run" in inspect.getsource(pk.extract_commands))


# ── the detector, checked against itself ───────────────────────────────

print("--- the guard can tell the two apart, both ways ---")

# A guard that never fires and a guard that always fires both "pass" a
# one-sided test. Assert the split explicitly.
quotes = [INCIDENT, PROSE, "> - a\n> - b", "> > x\n> > y"]
moves = [BATCH, "> look\n> leave", LONE, "> Owl", "> breed Glade 4"]
check("every quoted shape is refused", all(cmds(q) == [] for q in quotes))
check("every real shape still runs", all(cmds(m) for m in moves))


print()
if _fails:
    print(f"test_park_quote_not_command: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_park_quote_not_command: all checks passed")
