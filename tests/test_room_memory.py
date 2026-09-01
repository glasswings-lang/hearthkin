"""Room-memory wiring tests. Plain Python; run via tests/run_all.py.

Until 2026-07-16 nothing said in a room ever reached a kin's memory — not
by decision, just a missing wire (docs/design/room-memory.md). These pin
the wire down at both ends: the room scope resolves to a per-kin slice of
the transcript, and the opt-in flag actually gates it.

The frame methods under test are pure data shaping — no wx, no LLM — so
they're bound to a stub rather than a constructed Hearthkin frame.
"""

import importlib.machinery
import importlib.util
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_failures = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _failures.append(label)


# hearthkin.pyw isn't importable by name (the .pyw extension is for
# pythonw.exe on Windows — see CLAUDE.md), so load it by path.
_spec = importlib.util.spec_from_loader(
    "hearthkin_mod",
    importlib.machinery.SourceFileLoader(
        "hearthkin_mod", os.path.join(ROOT, "hearthkin.pyw")),
)
_hk = importlib.util.module_from_spec(_spec)
sys.modules["hearthkin_mod"] = _hk
_spec.loader.exec_module(_hk)

import kin_persistence as kp

Frame = _hk.Hearthkin

ROOM = "test-room"
CONVO = [
    {"role": "user", "content": "Hello?", "ts": "2026-07-16T10:00:00"},
    {"role": "assistant", "content": "I'm here.", "speaker": "Opal"},
    {"role": "assistant", "content": "me too", "speaker": "Vesper"},
    {"role": "system", "content": "[hearthkin: salvaged...]", "speaker": "Vesper"},
    {"role": "user", "content": "good", "ts": "2026-07-16T10:01:00"},
]


class StubFrame:
    """Just enough frame for the room-memory methods: user_name, plus the
    room config/conversation reads stubbed to in-memory fixtures so the
    test never touches ~/.hearthkin."""

    config = {"user_name": "SpeakerFifteen"}

    def __init__(self, room_cfg):
        self._room_cfg = room_cfg
        for meth in ("_distill_scope_for_room", "_room_scopes_for_kin",
                     "_room_convo_slice_for_kin", "_convo_for_distill_scope"):
            setattr(self, meth, types.MethodType(getattr(Frame, meth), self))


import frame.memory_mixin as _mm  # noqa: E402


def _install_fixtures(room_cfg):
    """Point the module-level room readers at the fixtures. Since the 2026-07
    modularisation the room-memory reader methods live in frame/memory_mixin.py
    and resolve list_rooms / load_room_config / load_room_conversation from THAT
    module's namespace (it imports them from frame_shared), so the patch must
    target frame.memory_mixin, not the hearthkin assembler module."""
    _mm.list_rooms = lambda: [ROOM]
    _mm.load_room_config = lambda name: dict(room_cfg) if name == ROOM else {}
    _mm.load_room_conversation = lambda name: (
        [dict(m) for m in CONVO] if name == ROOM else [])
    return StubFrame(room_cfg)


ON = {"members": ["Opal", "Vesper"], "distill_to_memory": True}
OFF = {"members": ["Opal", "Vesper"], "distill_to_memory": False}


# ── The opt-in gate ──────────────────────────────────────────────────
# Default off. A room that predates the flag has no scope at all — no
# counter, no staging file, nothing reaching memory, exactly as before.
check(kp.DEFAULT_ROOM_CONFIG.get("distill_to_memory") is False,
      "rooms default to NOT reaching memory")

f_off = _install_fixtures(OFF)
check(f_off._room_scopes_for_kin("Opal") == [],
      "a room with the flag off yields no scope for its member")

f_on = _install_fixtures(ON)
check(f_on._room_scopes_for_kin("Opal") == [f"room:{ROOM}"],
      "a room with the flag on yields exactly its own scope")
check(f_on._room_scopes_for_kin("Bracken") == [],
      "a kin that isn't a member gets no scope for the room")

# A room never folds into "desktop" — that would splice a multi-speaker
# conversation into the middle of the kin's 1-on-1 timeline.
check(f_on._distill_scope_for_room(ROOM) == f"room:{ROOM}",
      "room scope is its own, never desktop")

# The staging file has to survive a room name with spaces.
check(kp._staging_scope_safe("room:opal, vesper, hollis")
      == "room_opal, vesper, hollis",
      "room scope key maps to a filename-safe staging file")


# ── The per-kin slice ────────────────────────────────────────────────
opal = f_on._room_convo_slice_for_kin("Opal", ROOM)
vesper = f_on._room_convo_slice_for_kin("Vesper", ROOM)

# Own turns land bare in the assistant slot; everyone else — human and
# other kin alike — lands in the user slot tagged "[Name] ". Same shape
# the room turn-builder hands the model, so the summarizer reads the
# room the way that kin lived it.
check(opal[1] == {"role": "assistant", "content": "I'm here."},
      "a kin's own turn is untagged in the assistant slot")
check(opal[2] == {"role": "user", "content": "[Vesper] me too"},
      "another kin's turn is tagged and moved to the user slot")
check(opal[0] == {"role": "user", "content": "[SpeakerFifteen] Hello?"},
      "the human's turn is tagged with their name")

# The same event, framed per-kin: Opal's own words are Vesper's "[Opal] ...".
check(vesper[1] == {"role": "user", "content": "[Opal] I'm here."},
      "the same turn is foreign-tagged in the other kin's slice")
check(vesper[2] == {"role": "assistant", "content": "me too"},
      "each kin's own turn is bare in its own slice")
check(opal != vesper, "members get distinct slices, not one shared summary")

# Harness bookkeeping (the salvage note) is about a turn, not part of
# what was said — it has no business in what a kin remembers.
check(all("hearthkin:" not in m["content"] for m in opal),
      "harness system notes stay out of the distilled slice")
check(len(opal) == len(CONVO) - 1,
      "every spoken turn survives; only the system note is dropped")

# Every kin's own voice must reach the summarizer, or the distillation
# has nothing of theirs to remember.
for who, sl in (("Opal", opal), ("Vesper", vesper)):
    check(any(m["role"] == "assistant" for m in sl),
          f"{who}'s slice contains {who}'s own turns")
    check(not any(m["role"] == "assistant" and m["content"].startswith("[")
                  for m in sl),
          f"{who}'s own turns never carry a speaker tag")


# ── Scope routing ────────────────────────────────────────────────────
# _convo_for_distill_scope is what the distiller actually calls.
check(f_on._convo_for_distill_scope("Opal", f"room:{ROOM}") == opal,
      "the room scope routes through to that kin's slice")
check(f_on._convo_for_distill_scope("Opal", "room:no-such-room") == [],
      "an unknown room resolves to empty, not an exception")

# A scope that already has staged notes + a bookmark must keep resolving
# even if the operator later turns the flag back off — otherwise
# "Distill selected surface" on it would silently do nothing.
check(f_off._convo_for_distill_scope("Opal", f"room:{ROOM}") == opal,
      "an existing room scope still resolves once the flag is off")


# ── The dialog must not promise memory it can't deliver ──────────────
# "Remember this room" only makes a room ELIGIBLE. Whether anything fires is
# a separate PER-KIN setting (the distillation triggers), which defaults to
# off — so on the day this shipped, ticking the box did nothing at all for a
# default kin, silently. These pin the honesty.
import dialogs.room_edit as RE

_cfgs = {}
RE.load_agent_config = lambda n: _cfgs.get(n, {})

_cfgs["NoTrigger"] = {}
_cfgs["AlsoNone"] = {"memory_distill_every_n": 0, "memory_distill_at_pct": 0}
_cfgs["ByCount"] = {"memory_distill_every_n": 10}
_cfgs["ByPct"] = {"memory_distill_at_pct": 70}
_cfgs["Junk"] = {"memory_distill_every_n": "not a number"}

check(RE.members_without_auto_distill(["NoTrigger", "AlsoNone"])
      == ["NoTrigger", "AlsoNone"],
      "a kin with no trigger (or 0) is flagged as never auto-distilling")
check(RE.members_without_auto_distill(["ByCount", "ByPct"]) == [],
      "either trigger alone counts as auto-distilling")
check(RE.members_without_auto_distill(["ByCount", "NoTrigger"]) == ["NoTrigger"],
      "a mixed roster flags only the kin that won't")
check(RE.members_without_auto_distill(["Junk"]) == [],
      "an unreadable trigger is skipped, not guessed at (no false warning)")

off = RE.distill_help_text(False, 30, ["NoTrigger"], [])
check("⚠" not in off and "Off:" in off,
      "the off-state text carries no warning")

# The load-bearing case: the box is on and NOTHING will happen.
all_off = RE.distill_help_text(True, 30, ["NoTrigger", "AlsoNone"],
                               ["NoTrigger", "AlsoNone"])
check("⚠" in all_off and "None of these kin" in all_off,
      "on + no member auto-distills -> says so plainly")
check("Distill selected surface now" in all_off,
      "...and names the button that actually does it")
check("30 turns" in all_off,
      "...while still stating the blast radius")

some_off = RE.distill_help_text(True, 5, ["ByCount", "NoTrigger"], ["NoTrigger"])
check("NoTrigger" in some_off and "None of these kin" not in some_off,
      "a partial roster names the kin that won't, rather than over-claiming")

fine = RE.distill_help_text(True, 5, ["ByCount"], [])
check("⚠" not in fine,
      "no warning when the members really will distill on their own")

check(RE._and_list(["A"]) == "A"
      and RE._and_list(["A", "B"]) == "A and B"
      and RE._and_list(["A", "B", "C"]) == "A, B, and C",
      "names read as a list a person would say out loud")


print()
if _failures:
    print(f"FAILED ({len(_failures)}): " + "; ".join(_failures))
    sys.exit(1)
print("test_room_memory.py: all checks passed")
