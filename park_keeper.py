# SPDX-License-Identifier: CC0-1.0

"""Text-in / text-out park keeping — the bridge that actually gets a kin to DO
things in Time for Family.

The register-switch (a kin's relational voice <-> a structured tool call, or an
emote the router has to catch) is where small models fall on their face. Two
prior experiments proved it: the ``tff`` tool ran but forced the kin out of
voice, and park mode's emote routing executed *zero* moves across the whole
histories of the kin it was tried on (one of them lives on cron, where the
emote router isn't even wired). What DID work, verified live, was the
plainest thing: show the kin the park as text, ask for a sentence or two of
voice and then ONE command on a final ``>`` line, and run that line through the
same front door the console and server use (``tff_play.command``). No tool, no
emote parsing, no structured call.

This module is the pure, testable core of that loop — the prompt framing, the
``>``-line extractor, and the ground-truth feedback note. It imports nothing
from Hearthkin (only, lazily, the game's own ``tff_carer`` for the one-move
hint), so it can be unit-tested standalone and reused from any surface. The
surfaces that wire it to a kin's real model and save are the desktop send path,
Telegram and cron; each calls ``route_reply`` the same way.
"""

import json
import re
from pathlib import Path

from hearthkin_paths import kin_dir as kin_folder


# The keeper framing appended to the kin's soul as the system prompt. It does
# two jobs: (1) reframe "petting" into KEEPING (breed / welcome / expand), which
# is what a kin left to its own devices never reaches for, and (2) state the one
# hard contract — the move is a command on a final line starting with '> '.
MECHANISM = (
    "\n\n---\n\n## Keeping the park\n"
    "You don't only pet and feed -- you KEEP a living sanctuary. When two grown "
    "creatures could start a family and there's room, you pair them. When "
    "there's space, you welcome someone new. When a home is full, you build it "
    "bigger. Petting is lovely, but a keeper also grows the place.\n"
    "They have EACH OTHER, though: partners, families, and friendships that "
    "grow on their own between any two who share a room. 'lonely' means "
    "genuinely ALONE -- nobody they know is in the room -- NOT 'nobody has "
    "petted them lately'. A room full of family is content while you're away, "
    "so you never have to pet everyone to stop them being sad.\n"
    "The things you dig up are for GIVING. A gift is permanent: it belongs to "
    "that creature and it remembers that you gave it. 'things' shows what you "
    "have to give. Giving and tending are how your own bond with a creature "
    "grows -- and a bond never fades, so being away can't cost you one.\n"
    "You can stand somewhere: 'go to <room>' steps in, and then a plain "
    "'care' tends just that room. Tending the whole park in one command is "
    "retired -- step into a room instead.\n"
    "The way you DO a thing is a command on its own FINAL line starting with "
    "'> '. Say your warm sentence or two first, then the command.\n"
    "Commands: look | go to <room> | leave | dig <n> | build <roomtype> | "
    "adopt <species> | care for <room> | pet <name> | things | "
    "give <thing> to <name> | move <name> to <room> | breed <room> | "
    "expand <room> | memorial. One command per turn. Example:\n> breed Glade 4\n"
    "IMPORTANT: don't repeat a command the park just refused. If a pair is "
    "resting or a room is full, do a DIFFERENT job -- expand a full room, "
    "welcome someone new, give someone a thing you dug up, or tend the ones "
    "running low. There's always other work; a good keeper moves on."
)

# The per-turn ask that follows the park state. Kept terse and directive — the
# kin already sees the whole park, so "make a real move, not another look."
TURN_INSTRUCTION = (
    "\n\nA sentence or two, then ONE action on a '>' line. You already see the "
    "whole park here, so make it a real move -- breed / adopt / expand / care "
    "for / pet / give / move / dig / build -- NOT another 'look'."
)

# Phrases in a park reply that mean the move didn't land (a pair resting, a room
# full, too young, ...). Used only to add a "do something different" nudge to the
# next turn's note, so the kin doesn't bang on the same refused command.
REFUSAL_MARKERS = (
    "resting", "still bonding", "still growing", "can't", "couldn't", "need ",
    "no room", "too old", "too young", "already", "give them", "don't have",
    "nowhere", "did you mean",
)

# Lines the local-model chat sometimes prepends that aren't the kin talking.
_THINK_OPEN, _THINK_CLOSE = "<thinking>", "</thinking>"


def clean_reply(text):
    """Strip a model's thinking block and any stray leading 'model' label so
    what's left is the kin's actual words. Mirrors the verified harness: if a
    reply is nothing but an unclosed <thinking>, treat it as empty (the kin
    paused) rather than leaking raw reasoning into the park feed."""
    text = (text or "").strip()
    if _THINK_CLOSE in text:
        text = text.rsplit(_THINK_CLOSE, 1)[1].strip()
    elif _THINK_OPEN in text:
        return ""
    lines = text.splitlines()
    while lines and lines[0].strip().lower() == "model":
        lines.pop(0)
    return "\n".join(lines).strip()


# How many `> ` lines one reply may run. A ceiling, not a target: a kin that
# emits fifty is a kin that has lost the thread, and the park should not wear
# it. Truncation is REPORTED, never silent -- see route_reply.
PARK_COMMANDS_PER_REPLY_MAX = 6


_FENCE = "```"


def _strip_fenced(text):
    """Drop fenced code blocks. A `>` inside a fence is quoted material by
    definition — there is nothing to weigh up."""
    out, fenced = [], False
    for line in (text or "").splitlines():
        if line.strip().startswith(_FENCE):
            fenced = not fenced
            continue
        if not fenced:
            out.append(line)
    return "\n".join(out)


# Markdown block syntax, immediately after the '>'. No park command begins
# with any of these, and a kin quoting a document reproduces them constantly.
_MD_BLOCK_STARTS = ("#", "- ", "* ", "+ ", "> ", ">", "|", "```")


def _is_prose_line(cmd):
    """A quoted SENTENCE rather than a move: ends the way a sentence ends and
    is longer than any command the park takes.

    Both halves are required, and the threshold is deliberately generous. The
    longest real commands are about five words ('give warm stone to the owl',
    'reactions: purrs when you approach') and never end in a full stop, while
    a kin mid-walkthrough may legitimately answer a park's question with a
    short descriptive sentence. Being wrong here drops a real move, so the
    test only fires well clear of anything the game asks for."""
    if not cmd.endswith((".", "!", "?", '."', ".'", ".)")):
        return False
    return len(cmd.split()) > 10


def quoted_block_reason(cmds):
    """Why this run of `>` lines is a QUOTE and not a batch of moves, or "".

    `>` is the standard markdown quote character, and this is what it cost:
    a kin wrote out proposed memory text as `>` lines to show its person
    before saving it, the router ran every line as a command, a real creature
    landed in a SHARED park that other tenants read, and the text the kin was
    trying to save was swallowed instead of reaching a file. Both halves are
    damage — one wrote something into a world, the other lost the writing.

    Decided for the RUN, not per line, because a quote is a block: half a
    quoted paragraph running as moves is the same bug. Structural signals
    only — no guessing at what a word MEANS. `extract_command` records why
    that was tried before and was exactly wrong, and this must not walk back
    into it: every destructive command IS a word the game knows, and an answer
    to the park's own question is a word it doesn't.

    A SINGLE `>` line is never treated as a quote. A quote is at least two
    lines, and a lone line is the ordinary shape of both a move and a
    walkthrough answer — so the commonest legitimate case cannot be caught by
    any of this."""
    if len(cmds) < 2:
        return ""
    for cmd in cmds:
        low = cmd.lstrip()
        if low.startswith(_MD_BLOCK_STARTS):
            return ("it uses markdown formatting (a heading, a list or a "
                    "nested quote), which no park command does")
    # Two or more sentences, not one. A batch that happens to contain a single
    # long line is still a batch; a quoted passage is prose throughout.
    if sum(1 for c in cmds if _is_prose_line(c)) >= 2:
        return "it reads as quoted sentences rather than commands"
    return ""


def extract_commands(reply_text, limit=PARK_COMMANDS_PER_REPLY_MAX):
    """The kin's move(s) this turn: the LAST UNBROKEN RUN of '>' lines.

    Scanning from the bottom is the old rule and it stays -- the command a kin
    settled on wins even if it mused about others first. Taking the whole final
    RUN is the new part, and it is the difference between these two:

        i could look around...          > edit stellar-owl
        > look                          > babies word: clutch
        no, let's breed them            > reactions: ...
        > breed the glade               > birth anomalies: ...

    On the left the kin changed its mind: only `breed the glade` runs, exactly
    as before. On the right it wrote one batch, and all four run in order.
    Prose between the lines is what separates thinking from doing; blank lines
    are just spacing and don't break a run.

    A kin wrote the right-hand shape and had four of its five commands dropped
    without a sound -- the last one ran alone, without the `edit` that gave it
    meaning, and the game answered that it didn't know the word. A kin batches
    because a kin is slow: at six minutes a turn, doing that one move at a time
    is forty minutes.
    """
    cmds, _why = extract_command_run(reply_text, limit=limit)
    return cmds


def extract_command_run(reply_text, limit=PARK_COMMANDS_PER_REPLY_MAX):
    """`(commands, quote_reason)`. When the run is a markdown quote rather
    than a batch of moves, commands is empty and quote_reason says why.

    Callers that can speak should REPORT the reason rather than dropping it:
    a kin whose move silently did nothing cannot tell that from a park that
    ignored it, and the person watching sees a kin that stopped playing."""
    out, started = [], False
    for line in reversed(_strip_fenced(reply_text).splitlines()):
        s = line.strip()
        if s.startswith(">"):
            # Only the FIRST '>' is the marker; a nested '> >' keeps its
            # second one, which is what makes it recognisable as a quote.
            cmd = s[1:].strip()
            if cmd:
                out.append(cmd)
                started = True
            continue
        if not s:
            continue                      # blank lines are spacing, not a break
        if started:
            break                         # prose above the run: thinking, not doing
    out.reverse()
    why = quoted_block_reason(out)
    if why:
        return [], why
    if limit and limit > 0:
        out = out[:limit]
    return out, ""


def extract_command(reply_text, known_verbs=None):
    """Pull the one move out of a reply: the LAST line starting with '>'.

    Scanning from the bottom means the command the kin settled on wins even if
    it mused about others first. Returns the command string (without the '>'),
    or "" if there's no command this turn.

    `known_verbs` is legacy and the routers no longer pass it. It filtered a
    line out unless its first word was one the game knew, which sounded like a
    safety measure and was the exact opposite: every destructive command IS a
    word the game knows, so '> reset' sailed through while '> Owl' — an answer
    to the park's own question — was dropped without a sound. Worse, deciding
    what a line MEANS is the game's job, not ours: it holds each player's
    place in a conversation (mid-walkthrough, owed a did-you-mean) and already
    knows a bare 'look' is someone asking to get out while anything else is an
    answer. Guessing at that from here was a second copy of a rule we don't own.
    Left in the signature so an older caller doesn't break.

    Goes through the same run-level quote guard as `extract_commands`, so a
    kin quoting a document cannot fire a move from ANY surface. This is the
    one the desktop and Telegram loops actually call, so guarding only the
    plural version would have left the guard off where it was needed most.
    """
    verbs = {v.lower() for v in known_verbs} if known_verbs else None
    cmds, _why = extract_command_run(reply_text, limit=0)
    for cmd in reversed(cmds):
        if verbs is not None:
            first = cmd.split()[0].lower().strip(".,!?;:")
            if first not in verbs:
                continue
        return cmd
    return ""


# Commands a kin must not fire from a `> ` line, whatever it meant by them.
#
# This is NOT a general answer to "a kin could send a destructive command" --
# the filter waves every real verb through and always did, because its test is
# "does the game know this word?" and destructive commands are, by definition,
# words the game knows. It is one specific shape, added because it came up:
# `reset` empties a whole park, it is a single line away, and on a SHARED park
# the creatures it takes were never the kin's to begin with. Starting a park
# over is the host's call, not a turn in the game.
#
# Kept to the concrete shape rather than anything that looks destructive --
# same rule as the exec denylist. Add to it from real near-misses, not from
# imagination. (The game's own words are editable, so a park that renames its
# reset verb slips this; the game refusing a non-host outright is the sturdier
# fix and belongs on that side.)
BLOCKED_VERBS = ("reset",)

BLOCKED_NOTE = ("(that one's the operator's to do, not yours -- starting the "
                "park over isn't a move in the game. Everything else is "
                "yours.)")


def blocked_reason(command):
    """The note to hand back instead of running `command`, or "" to let it run.

    Answers rather than swallows: a kin that gets nothing back cannot tell a
    refusal from being ignored, which is the failure this whole surface has
    been fixing all day. It is told, in words, and can do something else."""
    first = (command or "").strip().split()
    if not first:
        return ""
    return BLOCKED_NOTE if first[0].lower().strip(".,!?;:") in BLOCKED_VERBS \
        else ""


def command_sentence(reply_text):
    """The kin's words WITHOUT the '>' command line — the warm sentence that
    rides into the shared feed so watchers see character, not just the move."""
    return "\n".join(l for l in (reply_text or "").splitlines()
                     if not l.strip().startswith(">")).strip()


def looks_refused(result_text):
    low = (result_text or "").lower()
    return any(w in low for w in REFUSAL_MARKERS)


def feedback_note(command, result_text):
    """The ground-truth note fed into the NEXT turn: what the kin did and what
    the park actually said back, plus a 'do something different' nudge when the
    move was refused. This closing of the loop — the kin reacting to reality
    rather than its own narration — is what keeps it from looping."""
    result_text = (result_text or "").strip()
    nudge = (" That didn't work -- do something DIFFERENT this turn."
             if looks_refused(result_text) else "")
    return ('Last turn you did `%s` and the park said: "%s".%s\n\n'
            % (command, result_text[:180], nudge))


def format_hint(h):
    """Render one carer opportunity as the keeper line, or "" for nothing.

    Split out from hint_line because a SERVED park answers with the hint
    already computed (the server reads its own state and returns it over
    /hint), while a local park hands us raw state to compute from. Both must
    reach the kin in identical words -- a kin should not be able to tell which
    kind of park it is in from how the suggestion is phrased.
    """
    if not h:
        return ""
    try:
        text, command = h.get("text", ""), h.get("command", "")
    except AttributeError:
        return ""
    if not command:
        return ""
    return ("\n\nOne thing worth doing right now: %s. You could `%s`. (Or tend "
            "someone as you like.)" % (text, command))


def hint_line(state):
    """One concrete keeper move worth doing right now, phrased as recognize-and-
    say-one-word (the deferral fix): 'One thing worth doing right now: X. You
    could `breed Y`.' Read from the game's own ``tff_carer`` if it's importable
    (the game dir is on sys.path once GameHost has loaded tff_play); degrades to
    "" so a missing carer just means no hint, never a crash."""
    try:
        import tff_carer
    except Exception:
        return ""
    try:
        return format_hint(tff_carer.top_hint(state))
    except Exception:
        return ""


def format_unseen_moves(entries, me, max_entries=8, per_entry_chars=200):
    """Turn raw shared-feed entries into the "while you were away, others did X"
    block for a keeper turn — or "" if nobody else moved.

    `entries` is a list of ``{"who": ..., "text": ...}`` (the feed's own shape).
    `me` is the reading kin's name: its OWN announced moves are dropped, because
    the kin already gets those back as ground truth — this block is only for the
    HUMAN's (or another player's) moves, the half of co-op the kin couldn't see.
    Newest `max_entries`, oldest-first so it reads like a little log; each move's
    narration is flattened to one line and capped so a chatty result can't wall
    the turn. Pure and I/O-free so it can be tested without a park; the feed read
    and the per-kin bookmark live in ``GameHost.unseen_moves``.
    """
    others = [e for e in (entries or [])
              if str(e.get("who", "")).strip() != (me or "").strip()
              and str(e.get("text", "")).strip()]
    if not others:
        return ""
    lines = []
    for e in others[-max_entries:]:
        who = str(e.get("who", "someone")).strip() or "someone"
        text = " ".join(str(e.get("text", "")).split())   # flatten newlines
        if len(text) > per_entry_chars:
            text = text[:per_entry_chars].rstrip() + "…"
        lines.append("- %s: %s" % (who, text))
    return "\n".join(lines)


def build_turn_message(look_text, hint="", note="", others="", instruction=None):
    """Assemble the per-turn user message: last turn's ground truth (if any),
    what other players did while the kin was away (if any), the park as it
    stands now, the one-move hint (if any), then the ask.

    `others` is the block from ``format_unseen_moves`` — empty when the kin is
    tending alone, so a solo turn reads exactly as it did before this landed.

    `instruction` overrides ``TURN_INSTRUCTION``. Callers inside Hearthkin pass
    the operator-editable version (``load_app_prompt("park_turn_instruction")``);
    this module can't load it itself, because it deliberately imports nothing
    from Hearthkin — and kin_persistence now imports THIS module for the
    registry default, so reaching back would be a genuine cycle. Omitted keeps
    the built-in text, which is what the standalone tests and the demo driver
    use."""
    others_block = ""
    if others:
        others_block = ("While you were away, others in the park:\n"
                        + others + "\n\n")
    ask = TURN_INSTRUCTION if instruction is None else instruction
    return (note + others_block + "The park right now:\n" + (look_text or "")
            + (hint or "") + ask)


# ─── Per-kin mode + the shared reply router (both surfaces call this) ──────────

_MODES = ("off", "chat", "keeper")


def kin_park_mode(agent_name):
    """How this kin plays its park, from its config's ``park`` key. Three values:

      * ``"off"``    -- default: nothing happens (existing kins are untouched).
      * ``"chat"``   -- a chatter who tends: a ``> command`` in a normal reply
                        runs -- they talk AND act in one breath).
      * ``"keeper"`` -- keeping IS the job: cron wake-ups are park turns, and a
                        ``> command`` in any reply runs.

    Read fresh from disk each call (cheap, and picks up a config edit without a
    restart). Unknown / missing -> ``"off"`` so this feature is inert until a kin
    is explicitly opted in."""
    try:
        cfg = json.loads(
            (kin_folder(agent_name) / "config.json")
            .read_text(encoding="utf-8"))
    except Exception:
        return "off"
    mode = str(cfg.get("park", "off")).strip().lower()
    return mode if mode in _MODES else "off"


# How many moves a kin may take in one turn before the surface stops asking it
# for another. 0 means no ceiling — the kin plays until it stops writing a '>'
# line. Per-kin so a chatter mid-conversation and an unattended keeper can be
# paced differently; editable in Settings, never a constant in here.
DEFAULT_PARK_MOVES_MAX = 6


def kin_park_moves(agent_name):
    """This kin's per-turn move ceiling, from its config's ``park_moves_max``.

    Returns an int; 0 (or negative) means "no ceiling". Read fresh from disk
    each call like ``kin_park_mode``, so a change takes effect without a
    restart. A missing / unparseable value falls back to the default rather
    than to 1: one move per turn is the behaviour this setting exists to end,
    and silently reverting to it on a typo would be indistinguishable from the
    feature not working."""
    try:
        cfg = json.loads(
            (kin_folder(agent_name) / "config.json")
            .read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_PARK_MOVES_MAX
    try:
        return int(cfg.get("park_moves_max", DEFAULT_PARK_MOVES_MAX))
    except (TypeError, ValueError):
        return DEFAULT_PARK_MOVES_MAX


# However generous the allowance, a turn has to end. A kin answering a form
# badly enough that the form never closes would otherwise loop until something
# else stopped it. This is not a pace -- it is the backstop behind the pace, and
# it is deliberately far above any real walkthrough (the longest is twelve
# questions) so it never shapes ordinary play.
ANSWER_HARD_STOP = 60


def kin_answer_hard_stop(agent_name):
    """This kin's absolute per-turn move cap, from ``park_answer_hard_stop``.

    Separate from ``park_moves_max`` because they answer different questions.
    That one is the PACE -- how much roaming a kin does before handing back.
    This is the last resort behind it, counting every move including the free
    answers, so a kin answering a form badly enough that the form never closes
    still ends its turn.

    Read fresh from disk like the pace, so a change needs no restart. 0 (or
    negative) means no cap at all: this used to be a constant, which put a
    number nobody could reach in charge of when a kin stops."""
    try:
        cfg = json.loads(
            (kin_folder(agent_name) / "config.json")
            .read_text(encoding="utf-8"))
    except Exception:
        return ANSWER_HARD_STOP
    try:
        return int(cfg.get("park_answer_hard_stop", ANSWER_HARD_STOP))
    except (TypeError, ValueError):
        return ANSWER_HARD_STOP


def reached_hard_stop(taken, hard_stop):
    """Has this turn hit its absolute cap? 0 or less means no cap."""
    try:
        limit = int(hard_stop)
    except (TypeError, ValueError):
        limit = ANSWER_HARD_STOP
    return limit > 0 and taken >= limit


def counts_against_moves(awaiting):
    """Should the move just taken be charged to this turn's allowance?

    No, while the game is holding an open question for this kin. A kin part-way
    through the twelve-question make-a-creature walkthrough is not roaming the
    park; it is answering what it was asked. Charging those made a species
    impossible to finish in one turn against a default of six moves -- it
    stopped halfway every time, and the half-made animal was lost when the kin
    was prompted back in and started over.

    Deliberately a separate one-line rule rather than a condition inside the
    loop: it is the sentence someone will want to read when they wonder why a
    kin took thirty moves, and a decision buried in a large handler can only be
    checked by running the whole app."""
    return not awaiting


def hit_move_ceiling(cmd, moves_done, max_moves):
    """Did the CEILING stop this loop, as opposed to the kin stopping itself?

    The distinction is the whole point. `should_take_another_move` returns False
    for two completely different situations: a kin that wrote no command (it is
    done, and that is a complete answer), and a kin that had another move in it
    and ran out of allowance. Only the second is worth saying anything about --
    announcing the first would narrate every ordinary ending.

    Kept next door to the rule it mirrors so the two can't drift, and pure so
    it can be checked without a park, a model or a chat."""
    if not cmd:
        return False                      # the kin's own stop -- not a ceiling
    try:
        limit = int(max_moves)
    except (TypeError, ValueError):
        limit = DEFAULT_PARK_MOVES_MAX
    if limit <= 0:
        return False                      # no ceiling to hit
    return moves_done >= limit


def should_take_another_move(cmd, moves_done, max_moves):
    """Whether to ask this kin for another park move this turn. Pure.

    Extracted rather than inlined at each surface for the same reason
    ``_walk_should_pause_after_bite`` was: the surfaces that own this loop are
    large handlers (the Telegram DM path, the cron keeper), and a decision
    buried in one of them is a decision that can only be checked by running the
    whole app. Here it is three lines and a test.

    The rules, and what is deliberately NOT a rule:

    * No command this turn -> stop. This is the kin's OWN stop signal and it
      needs no new syntax: a reply with voice and no '>' line already returns
      ``(None, None)`` from ``route_reply``. A kin that has done what it wanted
      simply stops, and that is a complete answer, not a failure to continue.
    * ``max_moves`` <= 0 -> no ceiling. The operator can turn the limit off.
    * Otherwise stop once ``moves_done`` reaches the ceiling.

    There is NO repeat guard, and that is on purpose. An earlier design here
    stopped the loop when a kin asked for the same command twice, on the theory
    that it was stuck. But whether a command was refused, and what to try
    instead, is the GAME's to say -- it already answers a refusal in words and
    offers the closest thing it understood, exactly as it does for a person at
    the console. A harness rule that counts repeats is a second copy of a rule
    we don't own, and it is the same mistake ``extract_command`` documents
    making with its old verb filter: it sounded like a safety measure and it
    ran backwards. If a kin really does bang on a refused command, the fix
    belongs in what the park says back to it."""
    if not cmd:
        return False
    try:
        cap = int(max_moves)
    except (TypeError, ValueError):
        cap = DEFAULT_PARK_MOVES_MAX
    if cap <= 0:
        return True
    return int(moves_done) < cap


class TurnResult(object):
    """What one call to ``play_turn`` did. Three numbers and a flag, because
    every surface needs to say something different about them.

    ``moves``  -- moves charged against the allowance (free mid-walkthrough
                  answers are not counted; see ``counts_against_moves``).
    ``taken``  -- every move that ran, including the free ones. This is what
                  ``ANSWER_HARD_STOP`` counts.
    ``asked``  -- how many times the kin was asked for another reply. Zero
                  means the surface passed no ``ask`` and this was the old
                  one-move behaviour.
    ``spent_allowance`` -- the CEILING ended the turn, not the kin. Only this
                  is worth telling anyone about: a kin that simply stopped
                  writing ``>`` lines has finished, and narrating that would
                  put a line under every ordinary ending.
    """

    __slots__ = ("moves", "taken", "asked", "spent_allowance")

    def __init__(self, moves=0, taken=0, asked=0, spent_allowance=False):
        self.moves = moves
        self.taken = taken
        self.asked = asked
        self.spent_allowance = spent_allowance

    def __repr__(self):
        return ("TurnResult(moves=%r, taken=%r, asked=%r, spent_allowance=%r)"
                % (self.moves, self.taken, self.asked, self.spent_allowance))


def play_turn(agent_name, reply_text, run_move, ask=None, awaiting=None,
              cancelled=None, max_moves=None, hard_stop=None):
    """A kin's whole park TURN: run its move, put the result in front of it,
    ask what it does next, and keep going until it stops or runs out.

    This is the loop itself, lifted here so it exists ONCE. It lived inside
    the Telegram handler, and the desktop — which could not reuse a method on
    the bot — took exactly one move instead: a kin there looked, and its turn
    was over. A kin that cannot look *and* act spends its only move looking.

    Writing a second loop on the desktop would have meant two copies of the
    allowance, the mid-walkthrough exemption and the stop conditions, free to
    drift apart forever. That drift is the most common bug this project finds,
    and it is why ``_route_one_park_move`` was split out on Telegram already.
    So the surfaces now supply only what is genuinely theirs — how to run a
    move, how to ask the model, how to tell if the person pressed stop — and
    the rules live here with the counting helpers they depend on.

    The callables:

    * ``run_move(reply_text) -> bool`` -- run the ``>`` command(s) in that text,
      show the result wherever this surface shows things, and record the kin's
      own copy as ground truth. True when a move actually ran. False both stops
      the loop and ends the turn, which is right for "nothing to run" and right
      for "something went wrong": asking for another move on top of an error we
      don't understand would be guessing.
    * ``ask() -> str`` -- the kin's next words, given the result just recorded.
      Return "" to stop. **Omitted, exactly one move runs** and the turn ends —
      the old behaviour, and what a caller with no way to re-ask a model should
      pass.
    * ``awaiting() -> bool`` -- is the game holding an open question for this
      kin? Charging those moves made a twelve-question species build impossible
      to finish against a default of six.
    * ``cancelled() -> bool`` -- has the person stopped this turn? Polled
      between moves. A multi-move loop against a slow local model that nothing
      but quitting can stop is exactly the shape this app keeps closing.

    Every callable is optional and every one is individually guarded: a
    surface whose ``cancelled`` raises means "keep going", the same rule
    ``should_stop`` follows everywhere else in this codebase. A flaky check
    must never be able to truncate a healthy turn.

    Never raises. Returns a ``TurnResult``.
    """
    if max_moves is None:
        max_moves = kin_park_moves(agent_name)
    if hard_stop is None:
        hard_stop = kin_answer_hard_stop(agent_name)

    def _cancelled():
        if cancelled is None:
            return False
        try:
            return bool(cancelled())
        except Exception:
            return False          # a broken check means keep going, never stop

    def _awaiting():
        if awaiting is None:
            return False
        try:
            return bool(awaiting())
        except Exception:
            return False

    out = TurnResult()
    current = reply_text
    while True:
        try:
            ran = bool(run_move(current))
        except Exception:
            break
        if not ran:
            break
        out.taken += 1
        if counts_against_moves(_awaiting()):
            out.moves += 1
        if reached_hard_stop(out.taken, hard_stop):
            break              # a form that never closes still has to end
        cmd = extract_command(current)
        if ask is None or not should_take_another_move(cmd, out.moves, max_moves):
            out.spent_allowance = (
                ask is not None
                and hit_move_ceiling(cmd, out.moves, max_moves))
            break
        if _cancelled():
            break
        try:
            current = ask()
        except Exception:
            break
        out.asked += 1
        if not (current or "").strip():
            break
    return out


def route_reply(reply_text, run, known_verbs=None):
    """Harvest the one ``> command`` from a kin's reply and run it.

    ``run`` is a callable ``run(command) -> narrated_result`` (wrap
    ``GameHost.run(agent, cmd)`` — one locked load-act-save). Returns
    ``(command, result_text)`` when a real move ran, or ``(None, None)`` when the
    reply carried no command. Never raises: a game error comes back as the
    result text so the caller can post it, best-effort, without risking the base
    reply. This is the WHOLE chat/cron bridge — the same three lines behind both
    Picture A (ride-along) and Picture B (keeper).

    A kin's PROSE never leaves here. Only the move does. This function used to
    harvest whatever a kin wrote above its ``> command`` and send it into the
    shared feed as that player's "words", and on a park with two tenants that
    was a voice leak in both directions: each kin then read the other's
    first-person sentences, under the other's name and colon, at the top of
    every park result it got. One kin began answering to the other's name. The
    human reading the same feed got a paragraph of someone else's writing every
    time they asked what had happened in their own park.

    Length was not the flaw and capping it did not fix it — a couple of
    sentences of first-person text under a ``Name:`` label is a clean voice
    sample, and there are dozens across an evening. A feed reports what
    happened. If a kin should be able to SAY something into a shared park, that
    wants a channel of its own that a kin chooses to use, not an automatic
    harvest of everything it wrote for someone else."""
    all_lines, quote_why = extract_command_run(reply_text, limit=0)
    if quote_why:
        # ANSWERED, not swallowed. The kin needs to know its lines were read as
        # a quote — otherwise it either assumes the park ran them (which is how
        # it came to believe a creature existed that it never adopted) or that
        # the park ignored it. And the person needs to know too, because the
        # text is usually something the kin was showing them on purpose.
        return None, ("(nothing was run from that: %s. Your words are safe and "
                      "nothing was changed in the park. If you did mean to "
                      "make a move, put it on its own final line.)" % quote_why)
    cmds = all_lines[:PARK_COMMANDS_PER_REPLY_MAX]
    if not cmds:
        return None, None

    def _one(cmd):
        """Run a single command, or answer why it wasn't run. Never raises."""
        stop = blocked_reason(cmd)
        if stop:                      # refused before the game is touched, and
            return stop               # ANSWERED, so the kin knows it was refused
        try:
            # One argument, deliberately: the runners each surface passes still
            # ACCEPT a second (a human typing into the desktop park window has
            # words of their own, and that stays), so the only way to be sure a
            # kin's prose can't ride along is to never hand one over.
            return run(cmd)
        except Exception as e:
            return "(couldn't do that: %s)" % e

    if len(cmds) == 1:
        return cmds[0], (_one(cmds[0]) or "").strip()

    # A batch. Each result is labelled with the command that caused it --
    # unlabelled, four replies in a row are unreadable, and the kin can't tell
    # which of its moves the park was answering.
    parts = []
    for cmd in cmds:
        res = (_one(cmd) or "").strip()
        parts.append("> %s" % cmd + chr(10) + res)
    if len(all_lines) > len(cmds):
        # Never truncate in silence: a dropped move that nobody mentions is
        # exactly the failure this whole change is fixing.
        parts.append("(%d more commands in that reply weren't run -- %d is the "
                     "most one turn may make.)"
                     % (len(all_lines) - len(cmds), PARK_COMMANDS_PER_REPLY_MAX))
    return "; ".join(cmds), (chr(10) * 2).join(parts)


# A kin's prose does not go into the shared feed, so nothing here shapes it for
# that. `narration_of` and its length cap lived at this spot and were the whole
# mechanism of the voice leak described in `route_reply`; the cap was an attempt
# to make the leak small rather than to stop it. Deleted rather than left
# unused, so nothing wires it back up by finding it lying here. Whatever gives a
# kin a deliberate way to speak into a shared park later wants its own shaping,
# not this one.
