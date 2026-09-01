"""Park mode — a kin's action-emotes drive its game instead of tool calls.

The register-switch (a kin's relational voice <-> a structured tool call) is
where small models gesture: they narrate ``*feeds luna*`` and never issue the
call. Park mode stops fighting that and reads the emote AS the move. This module
is the pure, testable core — pulling the action-emotes out of a reply. The
Telegram glue (mode state, running the game, posting results) lives in
``telegram_bot.py``.

Design notes (see ``docs/design/park-mode-emote-interface.md``):
- A ``*...*`` span is a move when its FIRST word is a verb the game knows.
  Feeling-emotes (``*smiles*``, ``*settles*``) are left alone — presence is not
  a failed tool call.
- An emote whose verb is UNKNOWN but which names a real target (``*grooms
  luna*`` — luna is a creature) is a *teachable* move: park mode surfaces a
  "teach grooms = pet" prompt so a brand-new emote verb slots in during play,
  grounded on the target so pure feeling (``*grins mischievously*``) stays
  quiet. A tiny stop-list of obvious feeling words is excluded even when a
  target is present.
"""

import collections
import re

# An emote is a ``*...*``-wrapped span, single line and bounded — so a whole
# italicised paragraph isn't swallowed as one giant "action".
_EMOTE_RE = re.compile(r"\*([^*\n]{1,140})\*")

# Trailing/leading punctuation to peel off the first word before matching.
_STRIP = ".,!?;:'\"*()[]"

# For the teachable path only: a real care-attempt names its target right at
# the front ("*grooms luna gently*"). A long, wistful presence-emote that only
# mentions a creature deep in a descriptive clause ("*reaches out gently towards
# screen, fingers tracing invisible air around where juna might be*") is NOT an
# action — surfacing a "teach me that word" prompt for it interrupts the kin's
# voice and drags the whole exchange into a teach-drill. So a teachable move
# requires the target to sit within the first few words of the emote.
_TEACH_FRONT_WORDS = 4

# Obvious feeling verbs — never park actions, so they don't fire a teach-prompt
# even when a creature is named (*smiles at luna*). Kept deliberately to words
# that are NOT game verbs (nuzzle/cuddle/snuggle are real care verbs, so they're
# absent here on purpose).
_FEELING_WORDS = frozenset((
    "smiles", "smile", "grins", "grin", "laughs", "laugh", "giggles", "giggle",
    "chuckles", "chuckle", "sighs", "sigh", "blushes", "blush", "winks", "wink",
    "nods", "nod", "shrugs", "shrug", "gazes", "gaze", "beams", "frowns",
    "frown", "cries", "weeps", "smirks", "smirk", "hums",
))

# One extracted emote: the inner text, its first word, and whether the game
# already knows that verb (True = run it; False = a teachable move to offer).
ParkAction = collections.namedtuple("ParkAction", "text verb known")


def _names_present(inner_low, targets):
    """True if any target name appears in the emote. Single-word targets match
    on a word boundary (so 'bis' doesn't hit 'business'); multi-word / hyphenated
    ones (a room like 'indoor 1') match as a substring."""
    words = set(re.findall(r"[a-z0-9]+", inner_low))
    for t in targets:
        if (" " in t or "-" in t):
            if t in inner_low:
                return True
        elif t in words:
            return True
    return False


def extract_park_actions(reply_text, known_verbs, known_targets=None):
    """Pull the moves out of a reply's action-emotes.

    ``known_verbs``: lower-case verb tokens the game understands (from
    ``tff_play.known_verbs()``). ``known_targets``: names the park can act on
    right now (from ``tff_play.known_targets(save)``); pass ``None``/empty to
    disable the teachable path. Returns a list of ``ParkAction`` in order —
    ``known=True`` ones are ready to run, ``known=False`` ones name a real
    target with a verb the game hasn't learned yet (offer to teach). Empty in
    ordinary conversation, so park mode stays quiet there.
    """
    if not reply_text or not known_verbs:
        return []
    verbs = {v.lower() for v in known_verbs}
    targets = {t.lower() for t in (known_targets or set()) if t}
    out = []
    for m in _EMOTE_RE.finditer(reply_text):
        inner = m.group(1).strip()
        if not inner:
            continue
        first = inner.split()[0].lower().strip(_STRIP)
        if first in verbs:
            out.append(ParkAction(inner, first, True))
        elif targets and first and first not in _FEELING_WORDS:
            # Only teachable when the emote is action-SHAPED: the target sits
            # near the front (verb + target). A creature mentioned deep inside
            # a long presence-emote is not a move — leave it alone.
            front = " ".join(inner.lower().split()[:_TEACH_FRONT_WORDS])
            if _names_present(front, targets):
                out.append(ParkAction(inner, first, False))
    return out
