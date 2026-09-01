# SPDX-License-Identifier: CC0-1.0
"""Guard test: the notes a kin gets when it GESTURED instead of acting, and
the one rule that must never break — silence when it actually did the work.

Three surfaces now share `turn_steering` rather than each keeping a copy. That
is the point: a fix that lands on one surface should not need re-landing on
the others, which is how these drifted apart in the first place. It also means
one bug here is a bug everywhere, so the judgement gets tested on its own
rather than through whichever surface happens to call it.

THE GATE THAT MATTERS MOST is `_real_tools_fired`. A kin that narrates its
work AND does it is not gesturing, it is describing — and correcting that
teaches it to stop saying what it is doing, which is the opposite of what
anyone wants. Every check here is skipped when real tool calls fired, and
that is asserted for each of them separately rather than assumed.

The other rule worth pinning: a kin with NO write tools is not gesturing when
it writes a note out in text. That is the only channel it has, so the reply
falls through to the toolless path and files it, instead of being corrected
for asking the only way it can.

Run: python tests/test_turn_steering.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="steer-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import turn_steering as ts                                   # noqa: E402

KIN = "Bracken"
GESTURE = "*writes this into memory.md*"
READ_GESTURE = "*reads through it slowly*"


def fired(name):
    return [{"role": "assistant",
             "tool_calls": [{"function": {"name": name}}]}]


# ── the gate ───────────────────────────────────────────────────────────

print("--- a kin that DID the work is never corrected for saying so ---")

check("a narrated write with no tool call is corrected",
      bool(ts.roleplay_corrective_note(KIN, GESTURE, ["write_file"], [])))
check("...but silence when write_file actually fired",
      not ts.roleplay_corrective_note(
          KIN, GESTURE, ["write_file"], fired("write_file")))

check("a narrated read with no tool call is nudged",
      bool(ts.read_gesture_note(KIN, READ_GESTURE, ["read_file"], [])))
check("...but silence when read_file actually fired",
      not ts.read_gesture_note(
          KIN, READ_GESTURE, ["read_file"], fired("read_file")))
check("...and silence when a memory search fired instead",
      not ts.read_gesture_note(
          KIN, READ_GESTURE, ["read_file"], fired("memory_search")))
check("...and silence when a file was shared with it this turn",
      not ts.read_gesture_note(
          KIN, READ_GESTURE, ["read_file"], [], shared_this_turn=True))

# The gate itself, directly — a helper the callers all lean on.
check("a turn with tool calls is recognised as real work",
      ts._real_tools_fired(fired("write_file")) is True)
check("a turn with an empty tool_calls list is not",
      ts._real_tools_fired([{"role": "assistant", "tool_calls": []}]) is False)
check("plain assistant text is not", ts._real_tools_fired(
    [{"role": "assistant", "content": "hello"}]) is False)
check("junk in the turn list does not raise",
      ts._real_tools_fired([None, "x", {"role": "tool"}]) is False)


# ── nothing to gesture at ──────────────────────────────────────────────

print("--- a kin without the tool is not doing anything wrong ---")

check("no correction when the kin has no write tool at all",
      not ts.roleplay_corrective_note(KIN, GESTURE, [], []))
check("no nudge when the kin cannot read files",
      not ts.read_gesture_note(KIN, READ_GESTURE, [], []))
check("an ordinary reply produces nothing",
      not ts.roleplay_corrective_note(
          KIN, "Slept badly. Nothing needed doing.", ["write_file"], []))
check("...and no nudge either",
      not ts.read_gesture_note(
          KIN, "Slept badly. Nothing needed doing.", ["read_file"], []))


# ── the authoring bridge and its fallthrough ───────────────────────────

print("--- writing a file out in text ---")

note, confirm = ts.authoring_bridge_notes(KIN, "just talking", ["write_file"])
check("a reply with no fenced write commits nothing",
      note is None and confirm is None)

# With no write tools the reply must NOT be treated as a mistake: the
# toolless path is the only channel such a kin has.
import inspect                                               # noqa: E402
src = inspect.getsource(ts.authoring_bridge_notes)
check("a kin with no write tools falls through to the toolless path",
      "toolless_memory_notes" in src)
check("...and that fallthrough is the only route to it, so a surface cannot "
      "accidentally run both",
      inspect.getsource(ts).count("toolless_memory_notes(") == 2)


# ── it must never cost a reply ─────────────────────────────────────────

print("--- a fault in the footnote never costs the reply ---")

for fn, args in (
        (ts.roleplay_corrective_note, (KIN, GESTURE, ["write_file"], None)),
        (ts.read_gesture_note, (KIN, READ_GESTURE, ["read_file"], None)),
):
    try:
        fn(*args)
        ok = True
    except Exception:
        ok = False
    check(f"{fn.__name__} survives a None turn list", ok)

for bad in (None, 123, {"not": "a list"}):
    try:
        ts.roleplay_corrective_note(KIN, GESTURE, ["write_file"], bad)
        ok = True
    except Exception:
        ok = False
    check(f"...and junk where the turns should be ({type(bad).__name__})", ok)

try:
    ts.authoring_bridge_notes(KIN, None, None)
    ok = True
except Exception:
    ok = False
check("authoring_bridge_notes survives being handed nothing", ok)


# ── every surface reaches it through this module ───────────────────────

print("--- one implementation, not four ---")

import hearthkin_cron                                        # noqa: E402
import discord_bot                                           # noqa: E402

cron_src = inspect.getsource(hearthkin_cron)
dc_src = inspect.getsource(discord_bot)
check("the scheduled wake-up goes through the shared module",
      "turn_steering" in cron_src)
check("Discord goes through the shared module", "turn_steering" in dc_src)
check("...and neither keeps its own copy of the detector call",
      "detect_tool_roleplay" not in cron_src
      and "detect_tool_roleplay" not in dc_src)


# ── the setting that started all of this ───────────────────────────────

print("--- show reasoning in chat actually shows it ---")

# The original symptom: the box was ticked and nothing appeared on Telegram.
# It reached the model call and stopped there, so the kin spent part of its
# reply budget thinking and the thinking was discarded without a word.
check("reasoning comes back as a block to send",
      "still awake" in ts.reasoning_block("still awake, weighing it up"))
check("...labelled, so it is not mistaken for the reply itself",
      "Reasoning" in ts.reasoning_block("x"))
check("no reasoning means no block, not an empty labelled one",
      ts.reasoning_block("") == "" and ts.reasoning_block(None) == "")
check("whitespace-only reasoning is nothing too",
      ts.reasoning_block("   \n  ") == "")
check("a long block is capped rather than rejected",
      len(ts.reasoning_block("x" * 9000, cap=100)) < 200)
check("...and says it was cut, rather than just stopping",
      "truncated" in ts.reasoning_block("x" * 9000, cap=100))

# It must be its OWN message on both remote surfaces. Folded into the
# streamed reply it would either arrive after the answer it explains, or
# rewrite a message already read aloud.
tg_src = inspect.getsource(sys.modules["telegram_bot"]) if "telegram_bot" in sys.modules else ""
if not tg_src:
    import telegram_bot as _tb
    tg_src = inspect.getsource(_tb)
check("Telegram sends the reasoning separately, gated on the setting",
      "reasoning_block" in tg_src and "if show_thinking:" in tg_src)
check("Discord does the same", "reasoning_block" in dc_src)
check("...and neither folds it into the streamed message",
      "finalize(_block" not in tg_src and "finalize(_block" not in dc_src)


print()
if _fails:
    print(f"test_turn_steering: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_turn_steering: all checks passed")
