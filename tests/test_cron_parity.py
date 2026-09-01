# SPDX-License-Identifier: CC0-1.0
"""Guard test: the five things a scheduled wake-up did not do that every
other surface did.

The surface matrix found these; this file checks they actually work, because
the matrix only proves a marker is present, not that it does anything.

All five share a shape worth naming: a scheduled wake-up is the LEAST
supervised turn this app takes, and it had the LEAST protection. Nobody is
awake to notice a kin gesturing at a tool instead of calling it, nobody
re-rolls a reply that came back in another kin's voice, and the record of what
happened is what the kin reads back tomorrow as its own words. So on this
surface every one of these failures teaches the kin something untrue about
itself, unobserved.

  1. NO ANTI-IMPERSONATION CLEANUP. A reply reached the journal, the
     conversation, and — when configured — a Telegram group, with none of the
     four passes run over it. The one surface nobody watches was the one that
     could post another kin's name into a room full of people.
  2. NO TOOL-USE HINT. The turn least able to recover from gesturing got the
     least steering.
  3. NO ROLE-PLAY CORRECTIVE. A kin that narrates filing a note is not
     contradicted, so it reads its own history back believing it filed one.
  4. NO READ NUDGE. An overnight tend is mostly reading, and a kin describing
     a file from imagination sounds exactly like a kin that read it.
  5. TURNS NEVER COUNTED TOWARD MEMORY when the app was closed. Distillation
     has two triggers; the percentage one reads off disk and always saw these,
     the every-N one counts in memory and never could. So whether a kin kept
     remembering what it did overnight depended on which setting its person
     happened to choose — which is not a thing anybody could have guessed.

Run: python tests/test_cron_parity.py
"""

import os
import sys
import json
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="cronpar-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import cron_helpers                                          # noqa: E402
import hearthkin_cron                                        # noqa: E402


# ── 1 & 2. cleanup and the tool hint reach the prompt/reply path ───────

print("--- the reply is cleaned before it goes anywhere ---")

from chat_helpers import clean_kin_reply                     # noqa: E402

# The cleanup itself is pinned by test_impersonation_cleanup; what matters
# here is that the cron path calls it, and calls it BEFORE the reply is used.
import inspect                                               # noqa: E402
run_src = inspect.getsource(hearthkin_cron)

for anchor, label in (
        ("clean_kin_reply", "the wake-up path runs the cleanup at all"),
        ("append_journal", "...and the journal is written in the same path"),
        ("_post_cron_reply_to_telegram",
         "...and so is the Telegram post, which is what made this urgent")):
    check(label, anchor in run_src)

# Scoped to the function that actually runs a wake-up. Searching the whole
# module found each helper's DEFINITION, which sits above the call site and
# made the ordering look wrong — an ordering check has to read the order
# things happen in, not the order they were declared.
reply_src = inspect.getsource(hearthkin_cron._run_isolated_inner)
cleanup_at = reply_src.find("clean_kin_reply(reply")
journal_at = reply_src.find("cron_helpers.append_journal")
telegram_at = reply_src.find("_post_cron_reply_to_telegram(kin")
persist_at = reply_src.find("_append_to_conversation(")
check("all four steps are in the one function, so the order is a real order",
      min(cleanup_at, journal_at, telegram_at, persist_at) >= 0)
check("cleanup runs BEFORE the journal entry is written",
      0 < cleanup_at < journal_at)
check("...before the reply is posted to Telegram",
      0 < cleanup_at < telegram_at)
check("...and before it is stored as the kin's own words",
      0 < cleanup_at < persist_at)

# The real chain, on the shape that motivated it. Text invented; the shape is
# what a model imitating a room transcript emits.
cleaned, impersonated = clean_kin_reply(
    "[2026-01-02 04:00] [Willow]: *checks the north gate*", "Bracken")
check("a reply opening in another kin's voice is stripped",
      "Willow" not in cleaned and "*checks the north gate*" in cleaned)
check("...and reported as impersonation, not quietly cleaned",
      impersonated is True)

print("--- the model is told which tools it may call ---")

build_src = inspect.getsource(hearthkin_cron._build_messages)
check("the wake-up appends the tool-use hint", "tool_use_hint" in build_src)
check("...naming the cron-safe tools, not the kin's whole list",
      "enabled_tools" in build_src and "{tools}" in build_src)
hint_at = build_src.find("tool_use_hint")
gate_at = build_src.find("if enabled_tools:")
check("...and only when there are tools to name",
      0 <= gate_at < hint_at)


# ── 3. the role-play corrective ────────────────────────────────────────

print("--- a kin acting out a tool call is contradicted ---")

_rp = hearthkin_cron._maybe_roleplay_corrective_cron

note = _rp("Bracken", "*writes this into memory.md*", ["write_file"], [], "m")
check("narrating a write, with no tool call, produces a corrective note",
      bool(note))

# Real tool calls fired: the kin is not stuck, it narrated alongside work.
added = [{"role": "assistant",
          "tool_calls": [{"function": {"name": "write_file"}}]}]
check("...but not when the tool actually fired — narrating alongside real "
      "work is not a fault",
      not _rp("Bracken", "*writes this into memory.md*", ["write_file"],
              added, "m"))
check("...nor when the kin has no such tool to gesture at",
      not _rp("Bracken", "*writes this into memory.md*", [], [], "m"))
check("an ordinary reply produces nothing",
      not _rp("Bracken", "Slept badly. The garden needs water.",
              ["write_file"], [], "m"))


# ── 4. the read nudge ──────────────────────────────────────────────────

print("--- a kin narrating a read it never made is noticed ---")

_rn = hearthkin_cron._maybe_read_nudge_cron
import reading_bridge                                        # noqa: E402

# The verb has to be the FIRST word inside the emote — reading_bridge checks
# that deliberately, so "opens notes.md and reads..." is not a read gesture to
# it. Use the shape its own docstring names.
_gesture = "*reads through it slowly*"
if reading_bridge.looks_like_read_gesture(_gesture):
    check("narrating a read, with no read tool call, produces a nudge",
          bool(_rn("Bracken", _gesture, ["read_file"], [])))
    read_fired = [{"role": "assistant",
                   "tool_calls": [{"function": {"name": "read_file"}}]}]
    check("...but not when read_file actually fired",
          not _rn("Bracken", _gesture, ["read_file"], read_fired))
    check("...and not when a memory search fired instead",
          not _rn("Bracken", _gesture, ["read_file"],
                  [{"role": "assistant",
                    "tool_calls": [{"function": {"name": "memory_search"}}]}]))
else:
    # Never silently skip: a gate that rejects the fixture looks exactly like
    # a detector doing its job. Say so out loud instead.
    check("FIXTURE PROBLEM: reading_bridge no longer treats the sample as a "
          "read gesture, so these checks proved nothing", False)

check("no nudge when the kin cannot read files at all",
      not _rn("Bracken", _gesture, [], []))
check("an ordinary reply produces nothing",
      not _rn("Bracken", "Quiet night. Nothing needed doing.",
              ["read_file"], []))


# ── 5. overnight turns count toward memory ─────────────────────────────

print("--- turns taken while the app was closed still count ---")

check("nothing recorded means nothing to fold",
      cron_helpers.take_unattended_turns() == [])

cron_helpers.note_unattended_turns("Bracken", "desktop", 2)
cron_helpers.note_unattended_turns("Bracken", "desktop", 2)
cron_helpers.note_unattended_turns("Willow", "desktop", 2)
got = dict(((k, s), n) for k, s, n in cron_helpers.take_unattended_turns())
check("counts accumulate across several wake-ups",
      got.get(("Bracken", "desktop")) == 4)
check("...and are kept apart per kin", got.get(("Willow", "desktop")) == 2)
check("reading CLEARS them, so a night is never counted twice",
      cron_helpers.take_unattended_turns() == [])

# A corrupt file must not stop the app starting, and must not resurrect
# itself on every tick afterwards.
cron_helpers.unattended_turns_path().write_text("{not json", encoding="utf-8")
check("a corrupt record is survived rather than raised",
      cron_helpers.take_unattended_turns() == [])
check("...and is cleared, so it can't fail on every future tick",
      not cron_helpers.unattended_turns_path().exists())

# The frame side: it must fold the count in AND ask whether that means a
# distillation is due. Folding without asking would be a counter nobody reads.
from frame.cron_exec_mixin import CronExecMixin                # noqa: E402

fold_src = inspect.getsource(CronExecMixin._fold_unattended_cron_turns)
check("the frame folds the count into the in-memory tally",
      "_messages_since_distill" in fold_src)
check("...and then actually asks whether a memory write is due",
      "_maybe_auto_distill" in fold_src)

tick_src = inspect.getsource(CronExecMixin._on_cron_timer_tick)
check("the fold is reached from the cron tick, which is guaranteed to recur",
      "_fold_unattended_cron_turns" in tick_src)


class _Frame(CronExecMixin):
    """Only what the fold touches."""

    def __init__(self):
        self._messages_since_distill = {}
        self.asked = []

    def _maybe_auto_distill(self, kin, scope_key=None):
        self.asked.append((kin, scope_key))


cron_helpers.note_unattended_turns("Bracken", "desktop", 3)
f = _Frame()
import frame.cron_exec_mixin as _cem                          # noqa: E402
_real_list = _cem.list_agents
_cem.list_agents = lambda: ["Bracken"]
try:
    f._fold_unattended_cron_turns()
finally:
    _cem.list_agents = _real_list
check("a real fold moves the count into the tally",
      f._messages_since_distill.get(("Bracken", "desktop")) == 3)
check("...and asks about that kin and that scope",
      f.asked == [("Bracken", "desktop")])

# A kin deleted between the wake-up and the fold must not resurrect a tally.
cron_helpers.note_unattended_turns("Gone", "desktop", 3)
f2 = _Frame()
_cem.list_agents = lambda: ["Bracken"]
try:
    f2._fold_unattended_cron_turns()
finally:
    _cem.list_agents = _real_list
check("a kin deleted since the wake-up is skipped, not counted",
      f2._messages_since_distill == {} and f2.asked == [])


print()
if _fails:
    print(f"test_cron_parity: {len(_fails)} FAILED")
    for f_ in _fails:
        print("  - " + f_)
    sys.exit(1)
print("test_cron_parity: all checks passed")
