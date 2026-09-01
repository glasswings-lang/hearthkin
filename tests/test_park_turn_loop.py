# SPDX-License-Identifier: CC0-1.0
"""A kin gets a whole park TURN, on every surface that can ask it again.

The bug this pins: the desktop ran exactly ONE move and the turn ended. A kin
there looked, saw three things worth doing, and stopped — because looking is
what you do when you cannot act on what you see, and it had spent its only
move doing it. Telegram had a real loop; the desktop could not reuse a method
on the bot, so it had none.

Two halves, and the second is the one that would have caught the bug:

  1. ``play_turn`` behaviour — the rules, with no model, game or window.
  2. A STRUCTURAL check that each surface actually calls it, and passes an
     ``ask``. A missing loop cannot fail a test of what the loop does; the
     only thing that catches it is asking, of every surface, does this one
     play a turn or take a move?

Run: python tests/test_park_turn_loop.py
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import park_keeper as PK

_fails = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _fails.append(label)


class Surface(object):
    """A fake surface: records the moves it was asked to run, and hands back
    scripted kin replies. `replies` is what the kin says when asked again."""

    def __init__(self, replies=(), awaiting=False):
        self.replies = list(replies)
        self.ran = []
        self.asked = 0
        self._awaiting = awaiting

    def run_move(self, text):
        cmd = PK.extract_command(text)
        if not cmd:
            return False
        self.ran.append(cmd)
        return True

    def ask(self):
        self.asked += 1
        return self.replies.pop(0) if self.replies else ""

    def awaiting(self):
        return self._awaiting


print("\n-- one move vs a turn --")

# No `ask` at all: exactly one move. This is what a caller with no way to
# re-ask a model should get, and it is the desktop's OLD behaviour.
s = Surface(replies=["> care for Glade", "> pet Otter"])
r = PK.play_turn("k", "> look", run_move=s.run_move, max_moves=6, hard_stop=60)
check(s.ran == ["look"], "no ask -> exactly one move, the old behaviour")
check(s.asked == 0, "...and the kin is never asked again")
check(r.asked == 0, "...which the result reports")

# With `ask`: the kin looks, then acts, in the SAME turn. This is the whole
# point, and the desktop could not do it.
s = Surface(replies=["> care for Glade", "> pet Otter", "that's everyone."])
r = PK.play_turn("k", "> look", run_move=s.run_move, ask=s.ask,
                 awaiting=s.awaiting, max_moves=6, hard_stop=60)
check(s.ran == ["look", "care for Glade", "pet Otter"],
      "with ask -> the kin looks AND acts in one turn")
check(r.moves == 3, "each chosen move is charged to the allowance")
check(not r.spent_allowance, "the kin stopped itself, so nothing is announced")

print("\n-- the kin's own stop signal --")

# A reply with voice and no '>' line is a complete answer, not a failure.
s = Surface(replies=["I think they're settled now."])
r = PK.play_turn("k", "> look", run_move=s.run_move, ask=s.ask, max_moves=6)
check(s.ran == ["look"], "a reply with no '>' line ends the turn")
check(not r.spent_allowance, "...and that ending is never narrated")

print("\n-- the ceiling --")

s = Surface(replies=["> a", "> b", "> c", "> d", "> e"])
r = PK.play_turn("k", "> look", run_move=s.run_move, ask=s.ask, max_moves=3,
                 hard_stop=60)
check(len(s.ran) == 3, "the ceiling stops the turn at park_moves_max")
check(r.moves == 3, "...and the count matches")
check(r.spent_allowance,
      "a SPENT ALLOWANCE is reported -- the kin had more to do")

# 0 means no ceiling. The kin plays until it stops writing '>' lines.
s = Surface(replies=["> a", "> b", "> c", "done."])
r = PK.play_turn("k", "> look", run_move=s.run_move, ask=s.ask, max_moves=0,
                 hard_stop=60)
check(len(s.ran) == 4, "max_moves=0 means no ceiling")
check(not r.spent_allowance, "...so nothing is announced when it stops")

print("\n-- mid-walkthrough answers are free --")

# The twelve-question species build. Charging these made it impossible to
# finish against a default of six, and the half-made animal was lost.
s = Surface(replies=["> %d" % i for i in range(10)] + ["done."],
            awaiting=True)
r = PK.play_turn("k", "> make a creature", run_move=s.run_move, ask=s.ask,
                 awaiting=s.awaiting, max_moves=3, hard_stop=60)
check(len(s.ran) == 11,
      "answering the game's own question doesn't spend the allowance")
check(r.moves == 0, "...none of them are charged")
check(r.taken == 11, "...but every one is counted against the hard stop")

print("\n-- the backstop behind the pace --")

s = Surface(replies=["> x"] * 50, awaiting=True)
r = PK.play_turn("k", "> start", run_move=s.run_move, ask=s.ask,
                 awaiting=s.awaiting, max_moves=0, hard_stop=5)
check(r.taken == 5, "a form that never closes still ends, at the hard stop")

print("\n-- stopping --")

s = Surface(replies=["> a", "> b", "> c"])
r = PK.play_turn("k", "> look", run_move=s.run_move, ask=s.ask,
                 cancelled=lambda: len(s.ran) >= 2, max_moves=0, hard_stop=60)
check(len(s.ran) == 2, "cancelled() ends the turn between moves")


def _boom():
    raise RuntimeError("flaky check")


# Same rule as every should_stop in this codebase: a check that raises means
# "keep going". A flaky probe must never truncate a healthy turn.
s = Surface(replies=["> a", "> b", "stop."])
r = PK.play_turn("k", "> look", run_move=s.run_move, ask=s.ask,
                 cancelled=_boom, max_moves=0, hard_stop=60)
check(len(s.ran) == 3, "a cancelled() that RAISES means keep going")

# Likewise a broken awaiting() must not silently make every move free.
s = Surface(replies=["> a", "> b", "> c", "> d"])
r = PK.play_turn("k", "> look", run_move=s.run_move, ask=s.ask,
                 awaiting=_boom, max_moves=2, hard_stop=60)
check(r.moves == 2, "a broken awaiting() still charges the allowance")

print("\n-- failure never becomes a runaway --")

# run_move returning False stops the turn: we don't know what happened, so
# asking for another move on top of it would be guessing.
s = Surface(replies=["> a", "> b"])
r = PK.play_turn("k", "no command here", run_move=s.run_move, ask=s.ask,
                 max_moves=6, hard_stop=60)
check(s.ran == [] and s.asked == 0, "nothing to run -> no turn, no asking")


class Exploding(Surface):
    def run_move(self, text):
        raise RuntimeError("the park fell over")


s = Exploding(replies=["> a"])
r = PK.play_turn("k", "> look", run_move=s.run_move, ask=s.ask, max_moves=6)
check(r.taken == 0, "a run_move that raises ends the turn instead of looping")


def _bad_ask():
    raise RuntimeError("model unreachable")


s = Surface()
r = PK.play_turn("k", "> look", run_move=s.run_move, ask=_bad_ask,
                 max_moves=6, hard_stop=60)
check(s.ran == ["look"], "an ask that raises ends the turn after the move")

print("\n-- structural: does each surface play a TURN, or take a move? --")


def _calls_play_turn_with_ask(path, funcname):
    """Does `funcname` in `path` call park_keeper.play_turn AND hand it an
    `ask`? Without the ask, play_turn runs exactly one move — so a surface
    that calls it but never passes one still has the original bug."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != funcname:
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            f = call.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(
                f, "id", "")
            if name != "play_turn":
                continue
            if any(kw.arg == "ask" for kw in call.keywords):
                return True, True
            return True, False
    return False, False


# Positive control FIRST. A structural check that has never been seen to fail
# is not evidence of anything -- this is the detector catching both shapes of
# the original bug before it is believed about the real files.
import tempfile

_control_dir = Path(tempfile.mkdtemp(prefix="park-loop-control-"))
_no_call = _control_dir / "no_call.py"
_no_call.write_text(
    "def _start_park_turn(self):\n"
    "    cmd, res = park_keeper.route_reply(text, run)\n", encoding="utf-8")
_no_ask = _control_dir / "no_ask.py"
_no_ask.write_text(
    "def _start_park_turn(self):\n"
    "    park_keeper.play_turn(kin, text, run_move=r)\n", encoding="utf-8")

_c, _a = _calls_play_turn_with_ask(_no_call, "_start_park_turn")
check(not _c, "positive control: a surface that never loops is spotted")
_c, _a = _calls_play_turn_with_ask(_no_ask, "_start_park_turn")
check(_c and not _a,
      "positive control: calling play_turn with no `ask` is spotted too")

for path, func, label in (
        (ROOT / "telegram_bot.py", "_route_park_command", "telegram"),
        (ROOT / "frame" / "chat_send_mixin.py", "_start_park_turn", "desktop"),
):
    called, with_ask = _calls_play_turn_with_ask(path, func)
    check(called, f"{label}: {func} uses the shared park_keeper.play_turn")
    check(with_ask, f"{label}: ...and passes an `ask`, so it plays a TURN")

# The loop must exist ONCE. A surface that kept its own `while True` around
# the move counting is a second copy of the allowance rules, free to drift.
for path, func, label in (
        (ROOT / "telegram_bot.py", "_route_park_command", "telegram"),
        (ROOT / "frame" / "chat_send_mixin.py", "_start_park_turn", "desktop"),
):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    loops = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            loops = sum(1 for n in ast.walk(node)
                        if isinstance(n, (ast.While, ast.For)))
    check(loops == 0, f"{label}: no second loop left behind in {func}")

# The desktop must not block the UI thread on a model call. _start_park_turn
# is reached from _on_stream_done, on that thread.
_src = (ROOT / "frame" / "chat_send_mixin.py").read_text(encoding="utf-8")
_tree = ast.parse(_src)
_threaded = False
for node in ast.walk(_tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_start_park_turn":
        for call in ast.walk(node):
            if (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "Thread"):
                _threaded = True
check(_threaded, "desktop: the park turn runs on a worker, not the UI thread")

# Confirm-on-close has to know. A new kind of background work that isn't on
# that list is a quit that abandons a kin mid-visit, in a SHARED save.
_life = (ROOT / "frame" / "lifecycle_mixin.py").read_text(encoding="utf-8")
check("_park_workers" in _life,
      "confirm-on-close counts a park turn as work in flight")

if _fails:
    print(f"\nFAILED: {len(_fails)} check(s): {_fails}")
    sys.exit(1)
print("\nALL CHECKS PASSED -- a kin gets a whole turn, on both surfaces.")
