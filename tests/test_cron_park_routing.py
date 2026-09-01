"""Cron routing for park-keeper kins. Plain Python; run via tests/run_all.py.

A keeper kin's wake-up IS a park turn — it needs the park + mechanism shown
BEFORE the call and its `> command` run AFTER. Only hearthkin_cron's
_run_isolated does either. The frame's live-injection path (_send_message)
does neither, so a keeper cron routed there narrates tending and moves
nothing — the exact failure park_keeper exists to end.

This pins the routing decision in _on_cron_timer_tick: keeper kins go to the
isolated worker even when they're the active kin.
"""

import importlib.machinery
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_failures = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _failures.append(label)


# ── The routing condition, extracted ─────────────────────────────────
# _on_cron_timer_tick's branch is inline in a wx event handler that needs a
# live frame + timer to reach. Rather than not test it, mirror the decision
# here and assert the source still matches (the guard below catches drift).
def routes_live(kin, current_agent, streaming, room_active, destinations,
                park_keeper_turn):
    return (kin == current_agent
            and not streaming
            and not room_active
            and not destinations
            and not park_keeper_turn)


A = dict(kin="Tarn", current_agent="Tarn", streaming=False,
         room_active=False, destinations=None, park_keeper_turn=False)

# The historic behavior must survive: a plain cron for the active kin still
# paints live in the desktop chat.
check(routes_live(**A) is True,
      "a plain cron for the active kin still injects live")

# The four existing escape hatches to the isolated worker.
check(routes_live(**{**A, "current_agent": "Bracken"}) is False,
      "a cron for a non-active kin goes isolated")
check(routes_live(**{**A, "streaming": True}) is False,
      "a cron mid-stream goes isolated")
check(routes_live(**{**A, "room_active": True}) is False,
      "a cron during a room goes isolated")
check(routes_live(**{**A, "destinations": [{"surface": "telegram_group"}]})
      is False,
      "an addressed cron goes isolated (it needs _run_isolated to deliver)")

# The new one. This is the fix: without it, Tarn-as-active-kin narrates
# tending and the park never moves.
check(routes_live(**{**A, "park_keeper_turn": True}) is False,
      "a park-keeper cron goes isolated even when its kin is active")

# Keeper routing must not depend on anything else being true — it's the
# whole point that it fires in the otherwise-live case.
check(all(routes_live(**{**A, "park_keeper_turn": True, k: v}) is False
          for k, v in (("streaming", True), ("room_active", True))),
      "keeper routing holds regardless of the other conditions")


# ── Guard: the mirrored condition still matches the source ───────────
# Since the 2026-07 modularisation the frame's methods live in frame/*.py;
# search the concatenated frame source (_on_cron_timer_tick is now in
# frame/cron_exec_mixin.py) so this guard survives a method moving mixins.
import glob
_frame_files = [os.path.join(ROOT, "hearthkin.pyw")] + sorted(
    glob.glob(os.path.join(ROOT, "frame", "*.py")))
src = "\n".join(open(p, encoding="utf-8").read() for p in _frame_files)
i = src.find("def _on_cron_timer_tick")
branch = src[i:i + 6000]
for frag in ("kin == self.current_agent",
             "not self._streaming",
             "not self._room_active",
             "not destinations",
             "not park_keeper_turn"):
    check(frag in branch,
          f"routing condition still contains `{frag}`")

# The mode must be read via park_keeper, and read per-tick (not cached at
# import), so flipping a kin to keeper needs no restart.
check("park_keeper.kin_park_mode(kin)" in branch,
      "keeper mode is resolved through park_keeper.kin_park_mode")


# ── The claim the fix rests on: only _run_isolated has the park hook ──
cron_src = open(os.path.join(ROOT, "hearthkin_cron.py"), encoding="utf-8").read()
check(cron_src.count("kin_park_mode(kin) == \"keeper\"") >= 2,
      "hearthkin_cron still has both park hooks (inject + route)")
# The frame must ROUTE a scheduled turn, never re-implement its mechanism.
# If someone copies the inject/run logic into the frame, the two copies drift
# and a keeper turn behaves differently depending on whether the app was open
# — the whole class of bug this fix closes.
check("MECHANISM" not in src,
      "the frame routes to _run_isolated rather than duplicating the hook")

# The frame IS allowed to route a park command from a TYPED desktop reply --
# that surface had no park route at all, so a kin writing `> look at the
# village` to the operator in the main window was simply ignored, with
# nothing saying so. What it must never do is run the hook on a CRON turn:
# the cron path already ran it, and a second run means the kin's move happens
# twice (fed twice, bred twice), which is worse than not running at all.
# So the guard is not "the frame never mentions route_reply" but "every use
# of it is fenced off from cron turns".
if "route_reply" in src:
    i_p = src.find("def _maybe_route_park_command")
    check(i_p != -1,
          "frame use of route_reply lives in _maybe_route_park_command")
    park_fn = src[i_p:i_p + 3000]
    check("_is_cron_user_text(user_text)" in park_fn,
          "the desktop park route refuses cron turns (no double-run)")
    check('"chat", "keeper"' in park_fn,
          "the desktop park route is gated on the kin's park mode")


# --- no '>' router second-guesses the game ----------------------------------
# What a '> ' line MEANS belongs to the game. It holds each player's place in a
# conversation -- mid-walkthrough, owed a did-you-mean -- and already sorts a
# bare 'look' ("get me out of here") from an answer to the question it just
# asked. A word filter here was a second copy of a rule we don't own, and it
# ran backwards: '> reset' passed (the game knows 'reset') while '> Owl', an
# answer the park was waiting for, was dropped in silence.
#
# So no router may hand a verb list to route_reply. The emote router is a
# different question ("is this emote an ACTION?") and legitimately still asks
# known_verbs, hence checking the route_reply CALL rather than the whole file.
import pathlib as _pl
import re as _re
_ROOT = _pl.Path(__file__).resolve().parent.parent
for _name in ("telegram_bot.py", "hearthkin_cron.py",
              "frame/chat_send_mixin.py"):
    try:
        _s = (_ROOT / _name).read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    _calls = _re.findall(
        r"(?:route_reply|extract_command)\((?:[^()]|\([^()]*\))*\)", _s)
    check(_calls, f"{_name}: still routes a '>' line at all")
    for _call in _calls:
        check("known_verbs()" not in _call
              and "known_command_starts()" not in _call,
              f"{_name}: the '>' router lets the game decide, no word filter")

print()
if _failures:
    print(f"FAILED ({len(_failures)}): " + "; ".join(_failures))
    sys.exit(1)
print("test_cron_park_routing.py: all checks passed")
