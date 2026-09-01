# SPDX-License-Identifier: CC0-1.0
"""Unit tests for the pure text-in/text-out park-keeping core (park_keeper.py).

No model, no game, no network — just the prompt/parse/feedback logic. Run:
    python tests/test_park_keeper.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import park_keeper as PK


def park_keeper_counts(awaiting):
    return PK.counts_against_moves(awaiting)

VERBS = {"look", "adopt", "dig", "build", "move", "care", "breed", "pet",
         "expand", "convert", "rename"}

_fails = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _fails.append(label)


# --- extract_command: the whole bridge is this one function ---
check(PK.extract_command("Time for a family.\n> breed Glade 4", VERBS)
      == "breed Glade 4", "pulls the '>' command line")

check(PK.extract_command("no command here at all", VERBS) == "",
      "no '>' line -> empty (kin just talked)")

# The LAST '>' line wins — the kin may muse about options before settling.
check(PK.extract_command("> look\nActually, let me act.\n> care for Indoor 1",
                         VERBS) == "care for Indoor 1",
      "last '>' line wins over an earlier one")

# A stray '>' that isn't a real command (quoting) must NOT fire a move.
check(PK.extract_command("She said > hello there", VERBS) == "",
      "a '>' whose first word isn't a verb is ignored")

# Without a verb list we can't validate — accept any '>' line (the caller
# chose not to guard). Still strips the marker.
check(PK.extract_command("> care for everyone") == "care for everyone",
      "no verb list -> accepts any '>' line")

# --- command_sentence: the warm words that ride the feed, minus the command ---
check(PK.command_sentence("Poppy and Bramble are ready.\n> breed Indoor 1")
      == "Poppy and Bramble are ready.",
      "command_sentence drops the '>' line, keeps the voice")

# --- a kin's prose does NOT reach the feed at all ---
# There was a `narration_of` here that shaped a kin's own words into a bounded
# line for the shared feed, and a cap on how long that line could be. Bounding
# it was the wrong fix for the wrong problem: the trouble was never length but
# that another tenant's first-person text arrived under their name at all. The
# harvester is gone; `tests/test_park_no_kin_prose_in_feed.py` holds the rule.
check(not hasattr(PK, "narration_of"),
      "no narration harvester survives to be wired back up")

# --- hit_move_ceiling: WHY the loop stopped, which decides whether to say so ---
# The person watching couldn't tell a spent allowance from a timeout, and the
# kin — which reads the chat as its own history — didn't know either, so being
# prompted back in made it start over. Announcing the OTHER stop reason would
# put a line under every ordinary ending, so the two must not be conflated.
check(PK.hit_move_ceiling("look", 6, 6),
      "the ceiling stopping a kin that still had a move is worth saying")
check(not PK.hit_move_ceiling("look", 5, 6),
      "...but not while it still has allowance left")
check(not PK.hit_move_ceiling("", 6, 6),
      "a kin that wrote no command stopped ITSELF -- silence, not a notice")
check(not PK.hit_move_ceiling("look", 99, 0),
      "0 means no ceiling, so there is no ceiling to hit")
check(not PK.hit_move_ceiling("look", 99, -3),
      "...and neither is a negative one")
check(PK.hit_move_ceiling("look", PK.DEFAULT_PARK_MOVES_MAX, "banana"),
      "an unusable ceiling falls back to the default, not to 'never'")
# The two rules must agree: anything that hits the ceiling must also be a stop.
check(not PK.should_take_another_move("look", 6, 6)
      and PK.hit_move_ceiling("look", 6, 6),
      "the two rules agree about the same situation")

# --- counts_against_moves: answering a form is not roaming the park ---
# A twelve-question make-a-creature walkthrough against a default of six moves
# could never finish in one turn. It stopped halfway every time, and the
# half-made animal was lost when the kin was prompted back in and started over.
check(park_keeper_counts(False), "an ordinary move costs the allowance")
check(not park_keeper_counts(True),
      "a move made while the game holds an open question does NOT")

# The whole point, as arithmetic: twelve answers against a ceiling of six.
_ceiling, _spent = 6, 0
for _step in range(12):
    if PK.counts_against_moves(True):       # every step is an answer
        _spent += 1
check(_spent == 0 and not PK.hit_move_ceiling("look", _spent, _ceiling),
      "twelve form answers spend nothing, so the species can finish in one turn")

# ...and the same twelve as free-roaming moves still stop at the ceiling.
_spent = sum(1 for _ in range(12) if PK.counts_against_moves(False))
check(_spent == 12 and PK.hit_move_ceiling("look", _spent, _ceiling),
      "...while twelve ordinary moves still hit it, so the limit still means something")

check(PK.ANSWER_HARD_STOP > 12 * 2,
      "the backstop sits far above any real walkthrough, so it never paces play")
check(isinstance(PK.ANSWER_HARD_STOP, int) and PK.ANSWER_HARD_STOP > 0,
      "...but it exists, so a form that never closes still ends the turn")

# ...and it is a SETTING, not a number nobody can reach. It used to be a
# constant, which put the decision about when a kin stops out of reach of the
# person whose kin it is.
check(PK.reached_hard_stop(60, 60), "the cap stops a turn when it's reached")
check(not PK.reached_hard_stop(59, 60), "...and not before")
check(not PK.reached_hard_stop(9999, 0),
      "0 means no cap at all -- the same 'off' the moves setting uses")
check(not PK.reached_hard_stop(9999, -5), "...and so does a negative one")
check(PK.reached_hard_stop(PK.ANSWER_HARD_STOP, "banana"),
      "an unusable value falls back to the default, never to 'never stop'")
check(PK.reached_hard_stop(12, 10) and not PK.reached_hard_stop(12, 20),
      "a lowered cap bites sooner, a raised one later")

# --- clean_reply: strip thinking / model label ---
check(PK.clean_reply("<thinking>hmm</thinking>\n> dig 20") == "> dig 20",
      "clean_reply strips a closed <thinking> block")
check(PK.clean_reply("<thinking>still going") == "",
      "an unclosed <thinking> reads as a pause (empty)")
check(PK.clean_reply("model\nHello there") == "Hello there",
      "a stray leading 'model' label is dropped")

# --- feedback_note: ground truth + a nudge only when refused ---
ok_note = PK.feedback_note("breed Glade 4", "A litter arrived from Glade 4!")
check("A litter arrived" in ok_note and "DIFFERENT" not in ok_note,
      "a successful move -> plain ground-truth note, no nudge")

refused = PK.feedback_note("breed Glade 4",
                           "The pairs here are resting after a recent litter.")
check("DIFFERENT" in refused,
      "a refused move -> adds the 'do something different' nudge")

check(PK.looks_refused("There's no room called 'x'. Did you mean Glade 4?"),
      "did-you-mean counts as a refusal (so the kin retargets)")

# --- build_turn_message: note + park + hint + ask, in order ---
msg = PK.build_turn_message("PARK STATE", hint="\n\nHINT", note="NOTE\n\n")
check(msg.startswith("NOTE") and "PARK STATE" in msg and "HINT" in msg
      and "'>' line" in msg,
      "build_turn_message assembles note + park + hint + the ask")

# --- hint_line degrades to '' when the game carer isn't importable ---
check(isinstance(PK.hint_line({"rooms": []}), str),
      "hint_line never raises (returns a string even with no carer)")

# --- route_reply: the whole chat/cron bridge in one call ---
ran = []
cmd, res = PK.route_reply("Time for a family.\n> breed Glade 4",
                          lambda c: ran.append(c) or "A litter arrived!", VERBS)
check(cmd == "breed Glade 4" and res == "A litter arrived!" and ran == ["breed Glade 4"],
      "route_reply runs the '>' command and returns (cmd, result)")

cmd, res = PK.route_reply("just chatting, no move here", lambda c: "x", VERBS)
check(cmd is None and res is None,
      "route_reply on a reply with no command -> (None, None), runs nothing")

def _boom(_c):
    raise RuntimeError("park exploded")
cmd, res = PK.route_reply("> dig 20", _boom, VERBS)
check(cmd == "dig 20" and "couldn't do that" in res,
      "route_reply never raises -- a game error comes back as text")

# --- kin_park_mode: reads the per-kin 'park' config, defaults off ---
import json, tempfile, os
_tmp = Path(tempfile.mkdtemp())
_kin = _tmp / ".hearthkin" / "kin" / "ModeProbe"
_kin.mkdir(parents=True)
import pathlib
# Redirect via HEARTHKIN_HOME — the documented override, which now genuinely
# reaches the park/tools layer instead of stopping at kin_persistence. This used
# to patch pathlib.Path.home, which worked only for as long as every one of ~25
# sites computed its own `Path.home() / ".hearthkin"`; that arrangement is what
# let a test asking where a kin's park lives create folders in a real home.
_saved_hh = os.environ.get("HEARTHKIN_HOME")
os.environ["HEARTHKIN_HOME"] = str(_tmp / ".hearthkin")
try:
    (_kin / "config.json").write_text('{"park": "keeper"}', encoding="utf-8")
    check(PK.kin_park_mode("ModeProbe") == "keeper", "kin_park_mode reads 'keeper'")
    (_kin / "config.json").write_text('{"park": "CHAT"}', encoding="utf-8")
    check(PK.kin_park_mode("ModeProbe") == "chat", "kin_park_mode is case-insensitive")
    (_kin / "config.json").write_text('{}', encoding="utf-8")
    check(PK.kin_park_mode("ModeProbe") == "off", "no 'park' key -> off (kins untouched)")
    (_kin / "config.json").write_text('{"park": "banana"}', encoding="utf-8")
    check(PK.kin_park_mode("ModeProbe") == "off", "unknown value -> off")
    check(PK.kin_park_mode("NoSuchKin") == "off", "missing config -> off")
finally:
    if _saved_hh is None:
        os.environ.pop("HEARTHKIN_HOME", None)
    else:
        os.environ["HEARTHKIN_HOME"] = _saved_hh

# --- co-op: the kin sees OTHER players' moves, never its own ----------------
# format_unseen_moves is the pure half of "play WITH the kin": given the shared
# feed's entries and the reading kin's name, it must surface the human's moves
# and drop the kin's own (which come back separately as ground truth).
_feed = [
    {"who": "Tarn", "text": "> pet Bisker\nBisker leans into your hand."},
    {"who": "speakerfifteen", "text": "> adopt cat\nWelcomed a pair of cats home."},
    {"who": "Tarn", "text": "> breed Glade 4\nA litter is on the way."},
]
_block = PK.format_unseen_moves(_feed, "Tarn")
check("speakerfifteen" in _block, "co-op: the human's move is shown to the kin")
check("adopt cat" in _block, "co-op: the human's move text rides along")
check("Bisker" not in _block and "Glade 4" not in _block,
      "co-op: the kin's OWN moves are filtered out (it gets those as ground truth)")
check(PK.format_unseen_moves(_feed, "speakerfifteen").count("\n") == 1,
      "co-op: from the human's seat, only the kin's two moves show")
check(PK.format_unseen_moves([], "Tarn") == "",
      "co-op: nobody else moved -> empty block")
check(PK.format_unseen_moves(
        [{"who": "Tarn", "text": "> look\n..."}], "Tarn") == "",
      "co-op: a park full of only the kin's own moves -> empty block")

# Newest-few cap: 10 foreign moves, max_entries=8 -> 8 lines, oldest-first.
_many = [{"who": "speakerfifteen", "text": "> dig %d\nfound something." % i}
         for i in range(10)]
_capped = PK.format_unseen_moves(_many, "Tarn", max_entries=8)
check(_capped.count("\n") == 7, "co-op: capped to the newest max_entries")
check("dig 9" in _capped and "dig 0" not in _capped,
      "co-op: the cap keeps the NEWEST moves, drops the oldest")

# A chatty multi-line result is flattened + capped so it can't wall the turn.
_wall = [{"who": "speakerfifteen", "text": "> care all\n" + ("blah " * 200)}]
_flat = PK.format_unseen_moves(_wall, "Tarn", per_entry_chars=120)
check("\n" not in _flat, "co-op: a multi-line move is flattened to one line")
check(_flat.endswith("…"), "co-op: an over-long move is capped with an ellipsis")

# build_turn_message threads it in — present when there are others, and the
# solo turn is byte-identical to before so nothing changed for a kin alone.
_with = PK.build_turn_message("PARK", hint="H", note="N", others="- speakerfifteen: > adopt cat")
check("While you were away" in _with and "speakerfifteen" in _with,
      "build_turn_message includes the others' block when given one")
check(PK.build_turn_message("PARK", hint="H", note="N", others="")
      == PK.build_turn_message("PARK", hint="H", note="N"),
      "build_turn_message with no others is unchanged from a solo turn")

# --- co-op I/O: GameHost.unseen_moves seen-bookmark + first-time-at-now -------
# Guarded: needs the real tff_feed (the game folder). Skips cleanly otherwise,
# so the suite stays green on a machine without the game checked out.
import tempfile as _tf
# No absolute path here on purpose: a machine-specific default makes this
# block skip on every machine but one, silently. `tff_path.txt` is the same
# file tools/tff.py reads, so pointing the app at the game points this at it.
def _tff_dirs():
    env = os.environ.get("HEARTHKIN_TFF_PATH")
    if env:
        yield env
    try:
        from hearthkin_paths import config_dir
        _pf = Path(config_dir()) / "tff_path.txt"
        if _pf.exists():
            yield _pf.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    yield str(Path.home() / "tff")
    yield str(Path.home() / "git-src" / "tff")


for _d in _tff_dirs():
    if _d and Path(_d).exists() and _d not in sys.path:
        sys.path.insert(0, _d)
try:
    import tff_feed as _feedmod
except Exception:
    _feedmod = None
if _feedmod is not None:
    _tmp2 = pathlib.Path(_tf.mkdtemp())
    _saved_hh2 = os.environ.get("HEARTHKIN_HOME")
    os.environ["HEARTHKIN_HOME"] = str(_tmp2 / ".hearthkin")
    try:
        from tools._game_host import GameHost as _GH
        _host = _GH(display_name="TFF", env_var="X", path_file="x.txt",
                    conventional_dirs=(), sentinel="tff_play.py",
                    module="tff_play", save_filename="tff.json",
                    feed_module="tff_feed")
        _sv = _host.save_path("Tarn")
        _feedmod.append(_sv, "Tarn", "> pet Bisker\nbacklog before the kin joined")
        check(_host.unseen_moves("Tarn") == "",
              "unseen_moves: first call seeds at now, no backlog dump")
        _feedmod.append(_sv, "speakerfifteen", "> adopt cat\nWelcomed a pair of cats home.")
        _feedmod.append(_sv, "Tarn", "> breed Glade 4\nA litter is on the way.")
        _r = _host.unseen_moves("Tarn")
        check("speakerfifteen" in _r and "adopt cat" in _r and "breed" not in _r,
              "unseen_moves: kin sees the human's move, not its own")
        check(_host.unseen_moves("Tarn") == "",
              "unseen_moves: nothing new -> empty, no repeat")
    finally:
        if _saved_hh2 is None:
            os.environ.pop("HEARTHKIN_HOME", None)
        else:
            os.environ["HEARTHKIN_HOME"] = _saved_hh2
else:
    check(True, "unseen_moves I/O test skipped (tff_feed not on this machine)")

# --- reset is the operator's, not the kin's ---------------------------------
# 'reset confirm' is one line and it empties a whole park -- on a shared park,
# creatures that were never this kin's. Refused before the game is touched, and
# ANSWERED rather than swallowed: a kin that gets nothing back can't tell a
# refusal from being ignored.
_reached = []
def _spy(cmd, say=""):
    _reached.append(cmd)
    return "ran"

for _line in ("> reset", "> reset confirm", "> RESET Confirm"):
    _reached.clear()
    _c, _r = PK.route_reply("all done\n" + _line, _spy, {"reset", "look"})
    check(not _reached, f"{_line!r} never reaches the park")
    check(bool(_r) and "operator" in str(_r).lower(),
          f"{_line!r} comes back as an answer, not silence")

_reached.clear()
_c, _r = PK.route_reply("having a look\n> look", _spy, {"reset", "look"})
check(_reached == ["look"], "an ordinary move still runs untouched")
check(PK.blocked_reason("") == "" and PK.blocked_reason("look") == "",
      "blocked_reason only refuses what's actually on the list")

# ── how many moves a kin gets in one turn ────────────────────────────────────
# One move per turn meant a kin could not look AND act, so it spent the only
# move it had on looking. These pin the decision that ends that.
print("\n-- should_take_another_move --")

check(PK.should_take_another_move("", 0, 6) is False,
      "no command means stop -- the kin's own signal, no new syntax needed")
check(PK.should_take_another_move("pet Robinch", 0, 6) is True,
      "a move that ran, well under the ceiling, continues")
check(PK.should_take_another_move("pet Robinch", 5, 6) is True,
      "the move BEFORE the ceiling still continues")
check(PK.should_take_another_move("pet Robinch", 6, 6) is False,
      "the ceiling stops it")
check(PK.should_take_another_move("pet Robinch", 999, 0) is True,
      "0 means no ceiling -- the operator can turn the limit off entirely")
check(PK.should_take_another_move("pet Robinch", 999, -1) is True,
      "a negative ceiling is 'off' too, not a stuck loop")

# There is deliberately NO repeat guard: whether a command was refused, and
# what to do instead, is the game's to say. A harness that counts repeats is a
# second copy of a rule it doesn't own -- the same mistake extract_command's
# old verb filter made. Same command four times running keeps going.
check(all(PK.should_take_another_move("breed Glade 4", _i, 0) is True
          for _i in range(4)),
      "no repeat guard: the same command again is the GAME's to refuse")

# A garbled ceiling must not silently mean 1. One-move-per-turn is the exact
# behaviour this setting exists to end; falling back to it on a typo would look
# identical to the feature not working at all.
check(PK.should_take_another_move("pet Robinch", 3, None) is True,
      "an unusable ceiling falls back to the default, never to one move")
check(PK.should_take_another_move("pet Robinch", 3, "six") is True,
      "a non-numeric ceiling likewise")

# The pacing lives in text an operator can edit, not in code they can't reach.
check(PK.build_turn_message("PARK", instruction=" DO A THING")
      .endswith(" DO A THING"),
      "the per-turn ask can be overridden by the editable prompt")
check(PK.build_turn_message("PARK").endswith(PK.TURN_INSTRUCTION),
      "omitted, the built-in ask is used (standalone + demo driver)")

print("\n" + "=" * 48)
# --- a BATCH of commands in one reply -------------------------------------
# Vesper wrote five '>' lines in one breath -- 'edit stellar-owl', then the
# fields to change. Only the last ran, without the edit that gave it meaning,
# and answered "I don't know the word 'birth'". The other four vanished with
# nothing said. A kin batches because a kin is slow: at six minutes a turn,
# one move at a time is forty minutes.
_batch = ("> edit stellar-owl\n> babies word: clutch\n"
          "> reactions: pulses; chimes\n> birth anomalies: Nova-Sight")
check(PK.extract_commands(_batch) == [
          "edit stellar-owl", "babies word: clutch",
          "reactions: pulses; chimes", "birth anomalies: Nova-Sight"],
      "an unbroken run of '>' lines is one batch, in the order written")

# ...and musing is still musing. Prose between the lines means the kin changed
# its mind, and only what it settled on runs -- the original rule, intact.
check(PK.extract_commands("i could look...\n> look\nno, let's breed\n> breed glade")
      == ["breed glade"],
      "prose between '>' lines still means only the last one runs")
check(PK.extract_commands("no command here") == [],
      "no '>' line -> no commands")
check(PK.extract_commands("> a\n\n> b") == ["a", "b"],
      "a blank line is spacing, not a break in the run")

# route_reply runs every command in the batch, in order, and labels each
# result -- four replies in a row are unreadable otherwise, and the kin can't
# tell which move the park was answering.
_ran = []
def _fake(cmd, say=""):
    _ran.append(cmd)
    return "did %s" % cmd

_cmd, _res = PK.route_reply(_batch, _fake)
check(_ran == ["edit stellar-owl", "babies word: clutch",
               "reactions: pulses; chimes", "birth anomalies: Nova-Sight"],
      "route_reply runs all four, in order")
check(all(("> " + c) in _res for c in _ran),
      "every result is labelled with the command that caused it")

# One command is unchanged in shape: bare command, bare result. The single-move
# surfaces (telegram, cron, the desktop window) all unpack this pair.
_ran.clear()
_cmd, _res = PK.route_reply("> breed glade", _fake)
check((_cmd, _res) == ("breed glade", "did breed glade"),
      "a single command still returns the plain (command, result) pair")

# A refused command is answered, not silently skipped, and the rest still run.
_ran.clear()
_cmd, _res = PK.route_reply("> look\n> reset\n> breed glade", _fake)
check(_ran == ["look", "breed glade"], "a blocked command doesn't run...")
check("operator's to do" in _res, "...but is answered, in the middle of a batch")

# Too many is capped, and the truncation is SAID. A dropped move nobody
# mentions is the exact failure this change exists to fix.
_ran.clear()
_many = "\n".join("> look %d" % i for i in range(12))
_cmd, _res = PK.route_reply(_many, _fake)
check(len(_ran) == PK.PARK_COMMANDS_PER_REPLY_MAX,
      "a runaway reply is capped at %d moves" % PK.PARK_COMMANDS_PER_REPLY_MAX)
check("weren't run" in _res, "...and the reply says how many were dropped")

if _fails:
    print(f"FAILED: {len(_fails)} check(s): {_fails}")
    sys.exit(1)
print("ALL CHECKS PASSED -- the text-in/text-out core holds.")
