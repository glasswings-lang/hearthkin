# SPDX-License-Identifier: CC0-1.0
"""Per-turn recall must be able to surface NOTHING, and must never dwarf the
message it accompanies.

Two kin the same afternoon looked like different problems -- one answering the
previous message, one replying to a 26-character line with 1,600 characters
about its own memory. Both were the same thing, and it was not the shape of the
block. It was the amount.

Every scorer in memory_recall normalizes to the top hit of the current turn, and
the relevance floor is a fraction OF that top hit. So when nothing matches, the
best of a bad field sets the bar and the bar drops to meet it. The engine had no
way to express "nothing here is relevant" and so it never did: replayed over 40
real user turns for each of four kin, it surfaced memory on every single turn --
160 of 160. That is a quota, not retrieval.

The second half is size. `recall_budget_pct` is a share of num_ctx; at 18% of a
32k window that is ~5,900 tokens, which is larger than some kin's entire memory
folder. It never binds, so the item cap becomes the only limit and every turn
gets the full six notes. Measured on real history, that put 3,087 characters of
memory in front of a 13-character message -- 237 times as much reference as
message. A kin describing that back is answering the bulk of what arrived.

What this file pins:
  * a message about nothing in memory recalls nothing
  * a match on ubiquitous words alone is not a match
  * the gate reads the LIVE turn, not the rolling multi-turn query
  * the block stays bounded by the size of the message it accompanies
  * a kin with only a handful of notes still gets recall at all

Every one of those is an assertion that something is ABSENT, so every one is
paired with a positive control on the same corpus proving the absence was a
decision and not a broken fixture.
"""

import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="recallgate-"))

from memory_recall import build_recall_block  # noqa: E402

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


def recall(kin, messages, **kw):
    kw.setdefault("budget_tokens", 5898)   # 18% of a 32k window, as shipped
    kw.setdefault("max_items", 6)
    kw.setdefault("semantic", False)
    return build_recall_block("Tester", messages, kin_dir=str(kin), **kw)


def user(text):
    return [{"role": "user", "content": text}]


# A corpus in the SHAPE the failure had -- a few topical logs, all of them
# naming the kin and the person constantly, the way a real depth log does --
# built out of entirely invented material. "tester" and "marlow" are ubiquitous
# here on purpose; they stand in for the two names that saturate any real kin's
# notes and therefore prove nothing when they match.
NOTES = {
    "kites.md": (
        "Tester and Marlow fly kites on the ridge when the wind comes from "
        "the west. The big delta has a torn spar that whistles.\n\n"
        "Marlow taught Tester the figure-eight turn, which Tester still "
        "cannot do."),
    "bread.md": (
        "Tester and Marlow bake bread on Thursdays. The starter is called "
        "Gerald and lives in the fridge door.\n\n"
        "Marlow prefers a dense crumb; Tester likes it full of holes."),
    "trains.md": (
        "Tester and Marlow ride the branch line to the coast. The carriages "
        "are old and the seats face each other in fours.\n\n"
        "Marlow reads timetables for pleasure, which Tester finds funny."),
    "garden.md": (
        "Tester and Marlow keep a small garden. Marlow planted a quince that "
        "has never fruited.\n\nThe greenhouse glass is cracked along one pane "
        "and nobody has mended it."),
}

with tempfile.TemporaryDirectory() as tmp:
    kin = make_kin(tmp, "Tester", NOTES)

    # ---- 1. A message about nothing in memory recalls nothing. --------------
    block, used = recall(kin, user("mmmmm. i. want. chocolate!"))
    check("1 a message about nothing in memory recalls nothing",
          block is None and used == [])

    block, used = recall(kin, user("And now? what do you see?"))
    check("1 an open question with no content words recalls nothing",
          block is None and used == [])

    # POSITIVE CONTROL: the same corpus, reachable, on a message that is
    # genuinely about one of these notes. Without this, the two checks above
    # would pass just as happily against an empty folder.
    block, used = recall(kin, user("how's the bread starter doing?"))
    check("1 CONTROL the corpus is reachable for an on-topic message",
          block is not None and used)
    check("1 CONTROL ...and it surfaces the right log",
          any(u["relpath"] == "bread.md" for u in used))

    # ---- 2. Ubiquitous words are not a match. ------------------------------
    # "Tester" and "Marlow" appear in every note. A message built only from
    # them shares plenty of words with memory and is about none of it.
    block, used = recall(kin, user("Marlow and Tester. Tester and Marlow."))
    check("2 matching only ubiquitous words recalls nothing",
          block is None and used == [])

    # POSITIVE CONTROL: the same sentence plus one distinctive word.
    block, used = recall(kin, user("Marlow and Tester. Tester and the quince."))
    check("2 CONTROL one distinctive word is enough to qualify",
          block is not None and any(u["relpath"] == "garden.md" for u in used))

    # ---- 3. The gate reads the live turn, not the rolling query. -----------
    # The multi-turn query is right for RANKING and wrong for qualifying: it
    # lets words from two turns ago pull notes into a message that has nothing
    # to do with them.
    stale = [
        {"role": "user", "content": "tell me about the delta kite on the ridge"},
        {"role": "assistant", "content": "It whistles when the wind gets up."},
        {"role": "user", "content": "mmmmm. i. want. chocolate!"},
    ]
    block, used = recall(kin, stale)
    check("3 an earlier turn's topic does not qualify notes for this one",
          block is None and used == [])

    # POSITIVE CONTROL: identical history, live turn back on topic.
    fresh = stale[:2] + [
        {"role": "user", "content": "and the torn spar, is it still there?"}]
    block, used = recall(kin, fresh)
    check("3 CONTROL the same history recalls when the LIVE turn is on topic",
          block is not None and used)

    # ---- 4. The block stays bounded by the message. ------------------------
    short = "is the starter called Gerald?"
    block, used = recall(kin, user(short))
    check("4 CONTROL a short on-topic question still gets its note",
          block is not None and used)


# A kin whose logs all circle the same subject in depth, so a short question
# about it legitimately qualifies many notes. That is the only honest place to
# test the size cap: on a message where the gate has already said yes to
# everything, the cap is the sole thing left holding the block down. Testing it
# on a message only one note qualifies for would credit the cap for the gate's
# work -- an earlier draft did exactly that, and the control caught it.
DEEP = {
    "harbour.md": (
        "The harbour songwriting sessions run late into Sunday. Two verses "
        "and a bridge came out of the last one, and the bridge is the part "
        "worth keeping. Recording happens at the kitchen table because the "
        "room there is dead enough not to need treating, and the songwriting "
        "itself mostly happens on the walk to the harbour rather than at the "
        "table at all."),
    "orchard.md": (
        "The orchard needs pruning before the first frost, which means the "
        "back half is already too late this year. Pruning the older trees "
        "takes a full weekend and the ladder is not trustworthy. The orchard "
        "was planted long before any of this and nobody now knows what half "
        "the varieties are, which makes pruning them partly guesswork."),
    "caves.md": (
        "The crystal cave is the oldest place in the shared worldbuilding and "
        "the one that keeps coming back. The cave glows from the walls rather "
        "than from anything overhead, so there are no shadows in it, and that "
        "turned out to be the detail that made the crystal feel real rather "
        "than decorative."),
}

with tempfile.TemporaryDirectory() as tmp:
    deep = make_kin(tmp, "Tester", DEEP)
    # 50 characters, naming two distinctive words from each of three logs --
    # so the gate says yes to all three and only the cap can hold it down.
    dense = "harbour songwriting, orchard pruning, crystal cave?"
    _b, capped = recall(deep, user(dense))
    import memory_recall as MR  # noqa: E402
    _saved = MR._MIN_BLOCK_CHARS
    try:
        MR._MIN_BLOCK_CHARS = 10 ** 9   # cap effectively off
        _b2, uncapped = recall(deep, user(dense))
    finally:
        MR._MIN_BLOCK_CHARS = _saved
    check("4 CONTROL the gate admits several notes for the dense message",
          len(uncapped) > 1)
    check("4 the cap, not the gate, is what bounds the block",
          len(capped) < len(uncapped))
    check("4 a short message does not attract the whole corpus",
          sum(len(u["snippet"]) for u in capped)
          < sum(len(u["snippet"]) for u in uncapped))
    check("4 ...and it still gets the note it asked for",
          len(capped) >= 1)

with tempfile.TemporaryDirectory() as tmp:
    kin = make_kin(tmp, "Tester", NOTES)

    # ---- 5. Length is proportional, not fixed. -----------------------------
    long_msg = (
        "ok so I've been thinking about the bread again, whether the "
        "starter would survive a week in the fridge door if nobody fed "
        "it, and whether we could take some to the coast on the branch "
        "line without it going strange in the carriages. Does that work, "
        "or am I reaching?")
    block_long, used_long = recall(kin, user(long_msg))
    check("5 a long on-topic message may carry more than a short one",
          len(used_long) >= len(used))

with tempfile.TemporaryDirectory() as tmp:
    # ---- 6. A young kin with three notes still has working recall. ---------
    # The distinctiveness threshold is a fraction of the corpus, and rounding
    # it DOWN makes a tiny corpus admit only words appearing in exactly one
    # chunk -- which disqualifies the very words such a kin's notes are about.
    # The kin with three notes is the one who can least afford silent recall.
    small = make_kin(tmp, "Tester", {
        "harbour.md": "The harbour project is a songwriting collaboration.",
        "orchard.md": "The orchard needs pruning before the first frost.",
        "cooking.md": "Pasta recipes and kitchen timers, unrelated.",
    })
    block, used = recall(small, user("how's the harbour songwriting going?"))
    check("6 a three-note kin still gets recall",
          block is not None and any(u["relpath"] == "harbour.md" for u in used))
    check("6 ...and still not the unrelated log",
          all(u["relpath"] != "cooking.md" for u in used))


# ---- 7. The gate must read what the PERSON said, not what we wrapped it in.
# Telegram inlines attribution into the content: "[YYYY-MM-DD HH:MM] [Name] ".
# So the live turn the gate scored carried the person's NAME on every single
# message -- and the depth log about that person is full of their name, so it
# qualified every time, whatever the message was about. Reported as a kin
# "narrating memories" with no correlation to anything, and correctly reported
# as affecting one kin only: the desktop names a speaker only when the turn
# carries one, so the same kin was fine there and only the group surface did
# it. Measured on the real turn that exposed it: 820 characters of note on a
# 173-character line.
#
# The corpus needs a PERSON-LOG for this to reproduce: a file named after the
# person and full of their name, sitting among topic logs that aren't. That
# concentration is what makes the name count as distinctive, and it is the
# ordinary shape of a kin's memory folder -- a log per person, a log per topic.
# Spread evenly across every log the name is common and qualifies nothing,
# which is why the first draft of this test could not reproduce the bug its
# control was supposed to prove.
PERSON = {
    "marlow.md": (
        "Marlow grew up by the coast and still flinches at gulls. Marlow "
        "takes tea without milk and is faintly embarrassed about it.\n\n"
        "Marlow has told that story a hundred times and it is better every "
        "time. Marlow's patience is the steady kind."),
    "kites.md": (
        "Kites fly best on the ridge when the wind comes from the west. The "
        "big delta has a torn spar that whistles."),
    "bread.md": (
        "Bread happens on Thursdays. The starter is called Gerald and lives "
        "in the fridge door."),
    "trains.md": (
        "The branch line runs to the coast. The carriages are old and the "
        "seats face each other in fours."),
}

with tempfile.TemporaryDirectory() as tmp:
    import memory_recall as MR  # noqa: E402
    kin = make_kin(tmp, "Tester", PERSON)
    PREFIX = "[2026-08-07 20:06] [Marlow] "
    # One ordinary word in common with the person-log ("times") and nothing
    # else. That is the real shape: the name from the prefix supplies the
    # DECISIVE second word, so any single coincidence anywhere in the message
    # is enough to attach the person-log. On the turn this came from, the
    # overlap was exactly {name, "times"} -- drop the name and it is one word,
    # which the gate rejects.
    plain = "*laughs* I have lost count of how many times you ask"

    block, used = recall(kin, user(PREFIX + plain),
                         speaker_names=["Marlow"])
    check("7 the harness's own prefix does not qualify a note",
          block is None and used == [])

    # POSITIVE CONTROL, and the important one: with the stripping disabled the
    # same turn DOES pull the person-log in. Without this, the check above
    # would pass just as happily if the gate were broken in some other way and
    # nothing ever qualified.
    _real = MR._strip_harness_prefix
    try:
        MR._strip_harness_prefix = lambda t, names=(): t or ""
        _b, leaked = recall(kin, user(PREFIX + plain),
                            speaker_names=["Marlow"])
    finally:
        MR._strip_harness_prefix = _real
    check("7 CONTROL without stripping, the name in the prefix pulls a note",
          bool(leaked))

    # The strip is narrow on purpose: a bracketed aside is how people and kin
    # both open a message, and eating it would remove the first words of the
    # thing being read. Only a bracket FOLLOWING a timestamp bracket goes.
    check("7 a bare leading bracket is left alone",
          MR._strip_harness_prefix("[laughs] how's the bread starter?")
          == "[laughs] how's the bread starter?")

    # The person writing may be a plural system announcing who is fronting.
    # "[SpeakerSeven] ..." is THEIR words, and it must survive even sitting right
    # behind our timestamp -- which is the case a pattern-based strip got
    # wrong, because our bracket and theirs look identical.
    check("7 a name WE did not announce survives our timestamp",
          MR._strip_harness_prefix("[2026-08-07 20:06] [SpeakerSeven] I settle here",
                                   ["Marlow"]) == "[SpeakerSeven] I settle here")
    check("7 ...and it still survives when we announced nothing at all",
          MR._strip_harness_prefix("[2026-08-07 20:06] [SpeakerSeven] I settle here")
          == "[SpeakerSeven] I settle here")
    check("7 CONTROL our own name in the same position does come off",
          MR._strip_harness_prefix("[2026-08-07 20:06] [Marlow] I settle here",
                                   ["Marlow"]) == "I settle here")
    check("7 both brackets go when we announced the outer one",
          MR._strip_harness_prefix(
              "[2026-08-07 20:06] [Marlow] [SpeakerSeven] I settle here",
              ["Marlow"]) == "[SpeakerSeven] I settle here")
    check("7 the match ignores case and padding",
          MR._strip_harness_prefix("[2026-08-07 20:06] [ marlow ] hello",
                                   ["Marlow"]) == "hello")
    check("7 CONTROL ...and such a message still recalls normally",
          bool(recall(kin, user("[laughs] how's the bread starter?"))[1]))
    check("7 a timestamp with no name is still stripped",
          MR._strip_harness_prefix("[2026-08-07 20:06] hello") == "hello")
    check("7 the person's words survive the strip intact",
          MR._strip_harness_prefix(PREFIX + plain, ["Marlow"]) == plain)


# ---- 8. Journals stay out of automatic surfacing by default. ---------------
# A dated diary entry is almost never what someone meant. Caught live: a
# sentence about tending children at a daycare matched the word "tend" in that
# day's journal, and the kin folded a note about its own memory ritual into a
# reply about somebody's garden. Journals are also the NEWEST file a kin owns,
# every day, so the recency multiplier favours them over the depth logs that
# were actually written to be remembered.
#
# Nothing is hidden by this. The kin opens its own journals with read_file
# whenever it likes, and memory_search still finds them; only the automatic
# surfacing stops.
with tempfile.TemporaryDirectory() as tmp:
    kin = make_kin(tmp, "Tester", NOTES)
    jd = pathlib.Path(kin) / "memory" / "journal"
    jd.mkdir(parents=True)
    (jd / "2026-08-07.md").write_text(
        "Today I tended the quince and thought about the starter. A slow, "
        "ordinary day, and I want to tend more of them.", "utf-8")

    # Reaches BOTH: {tend, quince} in the journal, {quince, greenhouse} in
    # the depth log. Otherwise "nothing surfaced" would prove only that the
    # question missed everything, which is not the same as the journal being
    # held back -- the control below is what caught that in an earlier draft.
    ask = user("are you going to tend the quince in the greenhouse this week?")
    _b, without = recall(kin, ask)
    _b2, with_j = recall(kin, ask, include_journals=True)

    check("8 CONTROL with journals allowed, the journal surfaces",
          any(u["relpath"].startswith("journal/") for u in with_j))
    check("8 by default no journal is surfaced",
          all(not u["relpath"].startswith("journal/") for u in without))
    check("8 CONTROL ...and the ordinary depth logs still can",
          bool(without))

    # The FOLDER is what's matched, not the word: a depth log that happens to
    # be named for journalling is a depth log.
    import memory_recall as _MR  # noqa: E402
    check("8 a depth log named like a journal is not treated as one",
          not _MR._is_journal("journalling.md")
          and _MR._is_journal("journal/2026-08-07.md"))

print()
if _fails:
    print("FAILED (%d): %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("all recall relevance-gate checks passed")
