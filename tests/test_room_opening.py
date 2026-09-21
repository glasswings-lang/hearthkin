"""Letting the kin begin a room. Plain Python; run via tests/run_all.py.

A room's Continue button used to sit disabled in an empty room, so the only way
to have a kin open the conversation was ticking auto-continue, which started a
round as a side effect. The kin that opened was then sent a system prompt and
no turn at all. Now an empty room offers "Let them begin" on the same button,
and a room a kin began always starts with an editable opening note, put back at
the top on every turn so the front of the prompt never changes.

The frame methods under test are pure data shaping, bound to a stub rather than
a constructed Hearthkin frame, as in test_room_memory.py.
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
OPENING = kp.DEFAULT_ROOM_OPENING_FRAME


class StubFrame:
    config = {"user_name": "SpeakerOne"}
    LET_THEM_BEGIN_LABEL = Frame.LET_THEM_BEGIN_LABEL
    CONTINUE_LABEL = Frame.CONTINUE_LABEL

    def __init__(self, convo, paused=True, active=False, members=("Opal", "Vesper"),
                 rounds=0):
        self.room_conversation = list(convo)
        self._room_paused = paused
        self._room_active = active
        self._room_round_order = list(members)
        self._room_round_count = rounds

    _room_history_for = Frame._room_history_for
    _room_opened_by_kin = Frame._room_opened_by_kin
    _continue_button_state = Frame._continue_button_state


def contents(history):
    return [m["content"] for m in history]


# --- the opening note ------------------------------------------------------

check("room_opening_frame" in kp.APP_PROMPT_REGISTRY,
      "the opening note is an editable app prompt")
check(kp.load_app_prompt("room_opening_frame") == OPENING,
      "it loads its default wording")

empty = StubFrame([])
h = empty._room_history_for("Opal")
check(h == [{"role": "user", "content": OPENING}],
      "the kin opening an empty room gets the note, not an empty conversation")

kin_first = [
    {"role": "assistant", "content": "Morning, all.", "speaker": "Opal"},
    {"role": "assistant", "content": "Morning.", "speaker": "Vesper"},
]
room = StubFrame(kin_first)
for who in ("Opal", "Vesper"):
    got = room._room_history_for(who)
    check(got and got[0] == {"role": "user", "content": OPENING},
          f"in a room a kin began, {who} sees the note at the top on later turns too")

opal = room._room_history_for("Opal")
check([m["role"] for m in opal] == ["user", "assistant", "user"],
      "and the opener's own first line follows it, so its history never starts "
      "on an assistant turn")
first = room._room_history_for("Vesper")
room.room_conversation.append({"role": "assistant", "content": "Tea?", "speaker": "Opal"})
later = room._room_history_for("Vesper")
check(later[:len(first)] == first,
      "a new turn only adds to the end; the front of the prompt is unchanged")

human_first = [
    {"role": "user", "content": "Hello?", "ts": "2026-09-20T10:00:00"},
    {"role": "assistant", "content": "Hi.", "speaker": "Opal"},
]
h = StubFrame(human_first)._room_history_for("Vesper")
check(OPENING not in contents(h),
      "a room the human began gets no note; nothing about it changes")

salvage_first = [
    {"role": "system", "content": "[hearthkin: salvaged]", "speaker": "Opal"},
    {"role": "user", "content": "Hi there"},
]
check(not StubFrame(salvage_first)._room_opened_by_kin(),
      "a stored system note before the human's first line doesn't count as a kin opening")

# --- the button ------------------------------------------------------------

label, enabled = StubFrame([])._continue_button_state()
check(label == "Let them begi&n" and enabled,
      "an empty room offers Let them begin, and it's usable")
check("&n" in label.lower() and "&n" in Frame.CONTINUE_LABEL.lower(),
      "both labels keep Alt+N, so the shortcut doesn't move")
label, enabled = StubFrame([], members=())._continue_button_state()
check(not enabled, "not with no valid members to speak")
label, enabled = StubFrame([], paused=False, active=True)._continue_button_state()
check(not enabled, "not while a round is running")

label, enabled = StubFrame(human_first, rounds=0)._continue_button_state()
check(label == "Co&ntinue round" and not enabled,
      "reopening a room with a conversation behaves as before: Continue, off "
      "until a round happens")
label, enabled = StubFrame(human_first, rounds=1)._continue_button_state()
check(label == "Co&ntinue round" and enabled, "and on after a round")


if _failures:
    print(f"\nFAILED {len(_failures)}: " + "; ".join(_failures))
    sys.exit(1)
print("\ntest_room_opening: all checks passed")
