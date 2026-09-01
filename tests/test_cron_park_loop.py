# SPDX-License-Identifier: CC0-1.0
"""A cron-driven keeper kin now plays a whole park TURN per wake-up, not one
move.

Before this, `hearthkin_cron._run_isolated_inner` called `park_keeper.
route_reply` once and stopped -- so a kin mid-walkthrough (e.g. the
twelve-question make-a-new-species flow) could only advance one step per
scheduled fire, which could be hours or days apart. `_run_cron_park_turn`
(split out of `_run_isolated_inner` so it can be exercised here without the
rest of a wake-up's machinery) now runs the shared `park_keeper.play_turn`
loop instead, the same one desktop and Telegram DM already use.

Cron has no person to press Cancel, so the only thing worth pinning here
beyond play_turn's own behaviour (already covered by test_park_turn_loop.py)
is that cron's OWN wiring reaches it correctly: the kin's `park_moves_max`
and the hard stop are respected, a mid-walkthrough answer doesn't cost a
move, and an unusually long run leaves a trace in cron_errors.log.

Run: python tests/test_cron_park_loop.py
"""

import ast
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="cronparkloop-")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _fails.append(label)


import cron_helpers                                          # noqa: E402
import hearthkin_cron                                         # noqa: E402
import llm_backend                                             # noqa: E402
import park_keeper                                              # noqa: E402
import tools                                                    # noqa: E402
from hearthkin_paths import kin_dir                             # noqa: E402


KIN = "Tarn"


def _write_kin_config(park_moves_max=None, hard_stop=None):
    d = kin_dir(KIN)
    d.mkdir(parents=True, exist_ok=True)
    cfg = {"park": "keeper"}
    if park_moves_max is not None:
        cfg["park_moves_max"] = park_moves_max
    if hard_stop is not None:
        cfg["park_answer_hard_stop"] = hard_stop
    (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")


class FakeHost(object):
    """Stands in for tools.GameHost: records every move run, answers
    `awaiting_answer` from a scripted flag, and is always reachable unless
    told otherwise."""

    def __init__(self, awaiting_seq=()):
        self.ran = []
        self._awaiting_seq = list(awaiting_seq)
        self.unreachable_logged = []

    def reachable(self, agent_name):
        return True, ""

    def log_unreachable(self, agent_name, detail, context=""):
        self.unreachable_logged.append((agent_name, detail, context))

    def run(self, agent_name, command, say=""):
        self.ran.append(command)
        return "ok: %s" % command

    def awaiting_answer(self, agent_name):
        return bool(self._awaiting_seq.pop(0)) if self._awaiting_seq else False


class _FakeResult(object):
    def __init__(self, content):
        self.content = content


def _scripted_chat(replies):
    """A fake llm_backend.chat that hands back each reply in turn, then an
    empty one forever (the kin's own stop signal)."""
    remaining = list(replies)

    def _chat(model, messages, **kwargs):
        if remaining:
            return _FakeResult(remaining.pop(0))
        return _FakeResult("")
    return _chat


def _patched(host, replies):
    """Context manager-ish helper: patch tools.get_game and llm_backend.chat,
    return (restore) callable."""
    orig_get_game = tools.get_game
    orig_chat = llm_backend.chat
    tools.get_game = lambda name: host if name == "tff" else orig_get_game(name)
    llm_backend.chat = _scripted_chat(replies)

    def restore():
        tools.get_game = orig_get_game
        llm_backend.chat = orig_chat
    return restore


print("\n-- a wake-up plays a whole turn, not one move --")

_write_kin_config(park_moves_max=6)
host = FakeHost()
restore = _patched(host, ["A sentence, then\n> pet Otter",
                          "Another move.\n> care for Glade",
                          "That's everyone for now."])
try:
    reply, turn = hearthkin_cron._run_cron_park_turn(
        KIN, "Welcome home.\n> look", messages=[], model="fake-model",
        options={}, cache=False, cache_ttl="auto", show_thinking=False,
        max_ctx=4096, kin_host="")
finally:
    restore()

check(host.ran == ["look", "pet Otter", "care for Glade"],
      "three moves ran in the one wake-up, not one")
check(turn is not None and turn.taken == 3, "TurnResult reports all three")
check(turn.asked == 3,
      "asked once after every move that ran, including the first")
check("ok: look" in reply and "ok: pet Otter" in reply and "ok: care for Glade" in reply,
      "every move's result folds into the reply that gets journaled/posted")
check(not turn.spent_allowance, "the kin stopped on its own, not the ceiling")


print("\n-- park_moves_max is read from the kin's own config --")

_write_kin_config(park_moves_max=2)
host = FakeHost()
restore = _patched(host, ["> pet Otter", "> pet Otter", "> pet Otter"])
try:
    reply, turn = hearthkin_cron._run_cron_park_turn(
        KIN, "> look", messages=[], model="fake-model", options={},
        cache=False, cache_ttl="auto", show_thinking=False, max_ctx=4096,
        kin_host="")
finally:
    restore()

check(turn.moves == 2, "stopped at this kin's own park_moves_max (2)")
check(turn.spent_allowance, "reported as the CEILING stopping it, not the kin")


print("\n-- a mid-walkthrough answer doesn't cost a move --")

_write_kin_config(park_moves_max=1)
host = FakeHost(awaiting_seq=[True, True, True])
restore = _patched(host, ["> Owl", "> stripes", "nothing else to add."])
try:
    reply, turn = hearthkin_cron._run_cron_park_turn(
        KIN, "> start species", messages=[], model="fake-model", options={},
        cache=False, cache_ttl="auto", show_thinking=False, max_ctx=4096,
        kin_host="")
finally:
    restore()

check(turn.taken == 3, "all three answers ran despite park_moves_max=1")
check(turn.moves == 0, "none of them were CHARGED -- the game was awaiting each one")
check(not turn.spent_allowance, "so the ceiling never fired")


print("\n-- the absolute hard stop still applies, unattended or not --")

_write_kin_config(park_moves_max=0, hard_stop=3)
host = FakeHost()
restore = _patched(host, ["> pet Otter"] * 10)
try:
    reply, turn = hearthkin_cron._run_cron_park_turn(
        KIN, "> pet Otter", messages=[], model="fake-model", options={},
        cache=False, cache_ttl="auto", show_thinking=False, max_ctx=4096,
        kin_host="")
finally:
    restore()

check(turn.taken == 3, "the hard stop ends the turn even with no move ceiling")


print("\n-- not a keeper turn at all --")

_write_kin_config(park_moves_max=6)
(kin_dir(KIN) / "config.json").write_text(
    json.dumps({"park": "off"}), encoding="utf-8")
host = FakeHost()
restore = _patched(host, ["should never be called"])
try:
    reply, turn = hearthkin_cron._run_cron_park_turn(
        KIN, "> look", messages=[], model="fake-model", options={},
        cache=False, cache_ttl="auto", show_thinking=False, max_ctx=4096,
        kin_host="")
finally:
    restore()

check(turn is None, "park mode off -> no turn played")
check(reply == "> look", "the reply is returned unchanged")
check(host.ran == [], "and nothing ran against the park")


print("\n-- an unreachable park -> no turn, not a crash --")

_write_kin_config(park_moves_max=6)


class _UnreachableHost(FakeHost):
    def reachable(self, agent_name):
        return False, "no host configured"


host = _UnreachableHost()
restore = _patched(host, ["should never be called"])
try:
    reply, turn = hearthkin_cron._run_cron_park_turn(
        KIN, "> look", messages=[], model="fake-model", options={},
        cache=False, cache_ttl="auto", show_thinking=False, max_ctx=4096,
        kin_host="")
finally:
    restore()

check(turn is None, "unreachable -> no turn played")
check(len(host.unreachable_logged) == 1, "and it was logged")


print("\n-- an unusually long turn is traced in cron_errors.log --")

_write_kin_config(park_moves_max=6)
host = FakeHost()
restore = _patched(host, ["> pet Otter", "> pet Owl", "done."])
log_path = cron_helpers.cron_error_log_path()
before = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
try:
    reply, turn = hearthkin_cron._run_cron_park_turn(
        KIN, "> look", messages=[], model="fake-model", options={},
        cache=False, cache_ttl="auto", show_thinking=False, max_ctx=4096,
        kin_host="")
    if turn is not None and turn.taken > 1:
        cron_helpers.log_cron_error(
            KIN, "park_turn",
            f"{turn.taken} move(s) this wake-up (asked {turn.asked}x)"
            + (", stopped at the move ceiling" if turn.spent_allowance else ""))
finally:
    restore()
after = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
check(len(after) > len(before) and "park_turn" in after[len(before):],
      "a multi-move turn leaves a line in the always-on cron log")


print("\n-- a single-move turn (the old, common case) reads exactly as before --")

_write_kin_config(park_moves_max=6)
host = FakeHost()
restore = _patched(host, [""])   # kin writes no further command -> stops
try:
    reply, turn = hearthkin_cron._run_cron_park_turn(
        KIN, "Just checking in.\n> look", messages=[], model="fake-model",
        options={}, cache=False, cache_ttl="auto", show_thinking=False,
        max_ctx=4096, kin_host="")
finally:
    restore()

check(reply == "Just checking in.\n> look\n\n🌳 ok: look",
      "single move keeps the old, unlabelled result format")
check(turn.taken == 1 and not turn.spent_allowance,
      "one move, kin's own stop -- ordinary, and NOT logged as unusual "
      "(taken > 1 is the gate the caller applies)")


# ── structural: cron reaches the shared play_turn loop, with an `ask` ──────

print("\n-- structural: cron plays a TURN through the shared loop --")

_src = (ROOT / "hearthkin_cron.py").read_text(encoding="utf-8")
_tree = ast.parse(_src)


def _calls_play_turn_with_ask(tree, funcname):
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != funcname:
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            f = call.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name != "play_turn":
                continue
            return True, any(kw.arg == "ask" for kw in call.keywords)
    return False, False


called, with_ask = _calls_play_turn_with_ask(_tree, "_run_cron_park_turn")
check(called, "_run_cron_park_turn calls the shared park_keeper.play_turn")
check(with_ask, "...and passes an `ask`, so it plays a TURN, not one move")

# No second copy of the loop / ceiling logic left behind.
_loops = 0
for node in ast.walk(_tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_run_cron_park_turn":
        _loops = sum(1 for n in ast.walk(node)
                     if isinstance(n, (ast.While, ast.For)))
check(_loops == 0, "no second loop -- play_turn owns the counting, once")

# _run_isolated_inner routes through the helper rather than re-implementing it.
_iso_src = None
for node in ast.walk(_tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_run_isolated_inner":
        _iso_src = ast.get_source_segment(_src, node) or ""
check(_iso_src is not None and "_run_cron_park_turn(" in _iso_src,
      "_run_isolated_inner calls the extracted helper, not its own copy")
check(_iso_src is not None and "route_reply" not in _iso_src,
      "...and no longer calls route_reply directly itself")

# The observability requirement: a long run must leave SOMETHING findable.
_helper_src = None
for node in ast.walk(_tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_run_isolated_inner":
        _helper_src = ast.get_source_segment(_src, node) or ""
check(_helper_src is not None and "log_cron_error" in _helper_src
      and "park_turn" in _helper_src,
      "an unusually long park turn is written to the always-on cron log")


if _fails:
    print(f"\nFAILED: {len(_fails)} check(s): {_fails}")
    sys.exit(1)
print("\nALL CHECKS PASSED -- a cron-driven keeper plays a whole turn.")
