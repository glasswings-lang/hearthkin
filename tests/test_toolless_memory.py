# SPDX-License-Identifier: CC0-1.0
"""What memory does a kin with NO tools actually get?

Runs the real functions against a sandboxed kin, so the answer is measured
rather than reasoned about. Two halves, deliberately:

  A. READING works with no tools. memory.md reaches the system prompt, and
     the depth logs reach the live turn via per-turn recall. No tool call
     anywhere in either path.

  B. WRITING is severed. The summarizer's notes land in staging/, and then
     nothing can move them: the only consumers are read_staging /
     archive_staging / write_file / edit_file, and a tool-less kin has none.
     Its memory is frozen at whatever memory.md said the day the tools went.

Every claim in half B is paired with a POSITIVE CONTROL on the same call
with tools enabled — otherwise "nothing happened" is indistinguishable from
"the test didn't look properly."
"""

import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="toolless-"))

import tools as kin_tools  # noqa: E402
from kin_persistence import (  # noqa: E402
    append_staging, archive_staging, build_system_prompt, create_agent,
    load_agent_config, load_staging, save_agent_config, save_kin_tools,
    load_kin_tools, save_memory, save_soul, staging_status_line,
)
from hearthkin_paths import kin_dir  # noqa: E402
from memory_recall import inject_into_messages  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Toolless"
SOUL = "You are Toolless. You are steady and you notice things."
INDEX = ("# What I remember\n\n"
         "- SpeakerFifteen: works the late shift at the harbour.\n")
DEPTH = ("SpeakerFifteen came by after the late shift again.\n\n"
         "SpeakerFifteen's harbour rota changed to four nights on, three off.\n")

create_agent(KIN)
save_soul(KIN, SOUL)
save_memory(KIN, INDEX)
mem = kin_dir(KIN) / "memory"
mem.mkdir(parents=True, exist_ok=True)
(mem / "speakerfifteen.md").write_text(DEPTH, encoding="utf-8")
# Unrelated logs so the corpus resembles a real kin's. Not decoration: BM25's
# IDF needs terms to be RARE to score, and in a two-chunk corpus every term
# appears everywhere, so nothing can rank above anything else.
(mem / "orchard.md").write_text(
    "The orchard needs pruning before the first frost.\n\n"
    "Windfalls to clear from the top row.\n", encoding="utf-8")
(mem / "kitchen.md").write_text(
    "Pasta timings and which pans are worth keeping.\n\n"
    "The oven runs hot on the left side.\n", encoding="utf-8")
(mem / "weather.md").write_text(
    "A cold snap forecast for the middle of the month.\n\n"
    "The gutters need looking at before it turns.\n", encoding="utf-8")

# The kin under test has NOTHING enabled.
save_kin_tools(KIN, [])
check("the kin genuinely has zero tools", load_kin_tools(KIN) == [])


# ---------------------------------------------------------------- A. reading

# A1. The index reaches the model, with tool gating at its strictest ([] means
# "gate, and this kin has zero tools" — every <!--tools:--> section drops out).
prompt = build_system_prompt(SOUL, INDEX, enabled_tools=[], kin_name=KIN)
check("A1 memory.md reaches the system prompt with no tools",
      "SpeakerFifteen: works the late shift" in prompt)
check("A1 the soul reaches it too", "You are Toolless" in prompt)

# A2. Depth logs reach the model beside the live turn, with no tool call. Not
# a system message (it would be hoisted to the front of the prompt) and not
# inside the person's words (see tests/test_recall_block_shape.py).
convo = [{"role": "user", "content": "how's SpeakerFifteen's rota looking these days?"}]
out, used = inject_into_messages(convo, KIN, num_ctx=8000)
check("A2 per-turn recall surfaces the depth log",
      any(u["relpath"] == "speakerfifteen.md" for u in used))
check("A2 it rides as a user turn, not a system message",
      out[-2]["role"] == "user" and "four nights on" in out[-2]["content"])
check("A2 the person's actual question arrives untouched",
      out[-1]["content"] == "how's SpeakerFifteen's rota looking these days?")

# A2-control: the same call with recall off must produce nothing. Proves the
# assertion above is reading a real block and not matching on the query text.
cfg = load_agent_config(KIN)
cfg["recall_enabled"] = False
save_agent_config(KIN, cfg)
off, off_used = inject_into_messages(convo, KIN, num_ctx=8000)
check("A2-control recall off means no block (the detector is real)",
      off_used == [] and off[-1]["content"] == convo[-1]["content"])
cfg["recall_enabled"] = True
save_agent_config(KIN, cfg)


# ---------------------------------------------------------------- B. writing

# B1. The summarizer's output lands in staging. This half needs no tools and
# works fine — which is exactly what makes the next findings matter.
append_staging(KIN, "desktop", "SpeakerFifteen's rota changed again; four on, three off.",
               source_label="test")
check("B1 distilled notes land in staging/", bool(load_staging(KIN, "desktop")))

# B2. A tool-less kin is handed the COUNT of pending notes, never the content —
# so it cannot act on them even though they are sitting on disk.
status = staging_status_line(KIN)
check("B2 the status line says notes are pending", "desktop" in status)
check("B2 but it does NOT contain the note itself",
      "four on, three off" not in status)

# B3. The tools that would read and clear staging do not load for this kin.
schemas, executor = kin_tools.load_tools(
    load_kin_tools(KIN), context={"agent_name": KIN})
names = {s.get("function", {}).get("name") for s in schemas}
check("B3 read_staging is unavailable", "read_staging" not in names)
check("B3 archive_staging is unavailable", "archive_staging" not in names)
check("B3 write_file / edit_file / note are unavailable",
      not ({"write_file", "edit_file", "note"} & names))

# B3-control: the same call for a kin that HAS them. Without this, "not in
# names" could just mean load_tools returned nothing at all.
ctl_schemas, _ = kin_tools.load_tools(
    ["read_staging", "archive_staging", "write_file"],
    context={"agent_name": KIN})
ctl = {s.get("function", {}).get("name") for s in ctl_schemas}
check("B3-control the same call DOES load them when enabled",
      {"read_staging", "archive_staging", "write_file"} <= ctl)

# B4. The authoring bridge — the one write path that needs no tool call — is
# gated shut. The kin can author perfectly good file content in text and the
# harness will refuse to look at it.
import authoring_bridge  # noqa: E402

REPLY = ("I want to keep this one.\n\n"
         "```memory/speakerfifteen.md\n"
         "SpeakerFifteen's rota: four nights on, three off.\n"
         "```\n")
writes = authoring_bridge.extract_authoring_writes(REPLY)
check("B4 the kin's fenced block IS a committable write",
      len(writes) == 1 and writes[0].path == "memory/speakerfifteen.md")

# This is the live gate, copied from chat_send_mixin._maybe_run_authoring_bridge
# and telegram_bot's equivalent: both return early on exactly this condition.
enabled = set(load_kin_tools(KIN) or [])
bridge_open = bool({"write_file", "edit_file"} & enabled)
check("B4 the bridge is gated SHUT for a tool-less kin", not bridge_open)
check("B4-control it opens for a kin with write_file",
      bool({"write_file", "edit_file"} & {"write_file"}))

# B5. The consequence, stated as a file on disk: nothing the kin could do this
# turn changes memory.md or the depth log, and staging stays pending forever.
before = (kin_dir(KIN) / "memory.md").read_text(encoding="utf-8")
# Everything a tool-less kin can do with that reply: produce the text. Do it.
if bridge_open:  # never true here; present so the test reads as a real branch
    authoring_bridge.commit_authoring_writes(KIN, writes)
after = (kin_dir(KIN) / "memory.md").read_text(encoding="utf-8")
check("B5 memory.md is unchanged after the kin authored a memory", before == after)
check("B5 the staging note is STILL pending (nothing archived it)",
      bool(load_staging(KIN, "desktop")))

# B5-control: the archive path itself works — the note is stuck because nothing
# can call it, not because archiving is broken.
check("B5-control archive_staging works when something calls it",
      archive_staging(KIN, "desktop") is not None
      and not load_staging(KIN, "desktop"))


# ------------------------------------------------- C. the loop closes again

import toolless_memory as tlm  # noqa: E402

NOTE = "SpeakerFifteen mentioned the rota is now four on, three off."
append_staging(KIN, "desktop", NOTE, source_label="test")

# C1. The kin is handed the notes THEMSELVES, inlined into the live turn.
turn = [{"role": "user", "content": "anything you want to hold onto?"}]
inj, shown = tlm.inject(turn, KIN, enabled_tools=[], tending=True)
block = inj[-1]["content"]
check("C1 the pending note reaches the kin verbatim", NOTE in block)
check("C1 it is inlined in the user turn (cache-safe placement)",
      inj[-1]["role"] == "user")
check("C1 the person's question is still underneath",
      block.strip().endswith("anything you want to hold onto?"))
check("C1 the scope is reported back for archiving", shown == ["desktop"])

# C1-control: a kin that HAS the tools is left completely alone — no behaviour
# change for any existing kin.
same, none_shown = tlm.inject(turn, KIN, enabled_tools=["write_file"], tending=True)
check("C1-control a kin with write_file is untouched",
      same == turn and none_shown == [])
same2, _ = tlm.inject(turn, KIN, enabled_tools=["read_staging"], tending=True)
check("C1-control a kin with read_staging is untouched", same2 == turn)

# C2. The kin replies in plain text — no tool call anywhere — and the write
# lands. `append:` so one new line doesn't require reproducing the whole log.
REPLY_KEEP = (
    "That's worth keeping.\n\n"
    "```append:memory/speakerfifteen.md\n"
    "Rota is four nights on, three off.\n"
    "```\n")
results, archived = tlm.commit(KIN, REPLY_KEEP, [], shown_scopes=shown)
log_now = (mem / "speakerfifteen.md").read_text(encoding="utf-8")
check("C2 the write landed", any(ok for (_p, ok, _d) in results))
check("C2 the new line is in the depth log",
      "four nights on, three off." in log_now)
check("C2 append did NOT clobber what was already there",
      "SpeakerFifteen came by after the late shift again." in log_now)
check("C2 the tended scope was archived", archived == ["desktop"])
check("C2 staging is now clear", not load_staging(KIN, "desktop"))

# C3. The receipt tells the kin the truth about its own memory.
rec = tlm.receipt(KIN, results, archived)
check("C3 a receipt is produced", "speakerfifteen.md" in rec and "archived" in rec)

# C4. Confinement: the bridge fires on any filename-shaped fence, so a kin
# discussing code must not be able to write code.
OUTSIDE = "here's the script\n\n```main.py\nprint('hi')\n```\n"
res_out, arch_out = tlm.commit(KIN, OUTSIDE, [], shown_scopes=[])
check("C4 a write outside memory/ is refused",
      res_out and not any(ok for (_p, ok, _d) in res_out))
check("C4 nothing was created outside memory/",
      not (kin_dir(KIN) / "main.py").exists())
check("C4-control memory.md itself IS an allowed target",
      tlm._is_memory_path("memory.md")
      and tlm._is_memory_path("memory/journal/2026-08-07.md")
      and not tlm._is_memory_path("../escape.md")
      and not tlm._is_memory_path("C:/Windows/system32/x.md"))

# C5. A kin that keeps nothing loses nothing — notes stay pending.
append_staging(KIN, "telegram-dm", "Something else worth a look.", "test")
_, arch_none = tlm.commit(KIN, "just thinking out loud, nothing to save.",
                          [], shown_scopes=["telegram-dm"])
check("C5 no write means no archive", arch_none == [])
check("C5 the notes are still pending", bool(load_staging(KIN, "telegram-dm")))

# C6. The payoff: the memory the kin just wrote is now findable by the same
# tool-less reading path. The loop is closed — this is what B5 could not do.
ask = [{"role": "user", "content": "remind me about SpeakerFifteen's rota"}]
back, back_used = inject_into_messages(ask, KIN, num_ctx=8000)
check("C6 the newly-written memory comes back through recall",
      any("four nights on, three off." in m["content"] for m in back))
check("C6 sourced from the kin's own depth log",
      any(u["relpath"] == "speakerfifteen.md" for u in back_used))


# ------------------------------------------------------- D. the 3am wake-up

# Cron is the surface this matters most on: tending is meant to happen
# unattended, nightly, with nobody there to notice it going wrong. Two hazards
# specific to it, both real before this:
#   * the wake-up prompt (including the one shipped by default) instructs
#     read_staging / edit_file / archive_staging — a kin without them is woken
#     and told to do the one thing it can't;
#   * the "cron-isolated" branch never committed authored writes at all, so a
#     tend there evaporated even when the kin did everything right.
import hearthkin_cron as HC  # noqa: E402

append_staging(KIN, "desktop", "SpeakerFifteen swapped shifts with someone on Thursday.",
               source_label="test")

WAKE = [{"role": "system", "content": "you are Toolless"},
        {"role": "user", "content": "Tonight's tending. Call `read_staging` to "
                                    "see what's pending, then archive_staging."}]

# D1. The tool-less wake-up hands over the notes AND corrects the instruction.
msgs = list(WAKE)
had_work, scopes = HC._inject_staging_status(KIN, msgs, [])
whole = "\n".join(m["content"] for m in msgs)
# Both pending scopes surface — the one just staged, and the one C5 left
# untended because the kin kept nothing that turn. A wake-up is exactly when
# that backlog should come back round.
check("D1 staging work is reported, including the scope C5 left pending",
      had_work and scopes == ["desktop", "telegram-dm"])
check("D1 the notes are in the wake-up itself",
      "swapped shifts with someone on Thursday" in whole)
check("D1 the impossible instruction is corrected, not left standing",
      "You don't have them tonight" in whole)
check("D1 and the correction teaches the fence instead",
      "append:" in whole and "memory/" in whole)

# D1-control: a kin WITH the tools gets the old count-only line and no
# correction — the nightly tend for every existing kin is untouched.
msgs_ctl = list(WAKE)
had_ctl, scopes_ctl = HC._inject_staging_status(
    KIN, msgs_ctl, ["read_staging", "write_file", "archive_staging"])
whole_ctl = "\n".join(m["content"] for m in msgs_ctl)
check("D1-control a tooled kin is unchanged",
      had_ctl and scopes_ctl == []
      and "swapped shifts" not in whole_ctl
      and "You don't have them tonight" not in whole_ctl)

# D2. The kin replies at 3am with a fence. It has to land with nobody watching.
NIGHT_REPLY = (
    "Worth keeping that one.\n\n"
    "```append:memory/speakerfifteen.md\n"
    "Swapped Thursday's shift with someone.\n"
    "```\n")
note, confirm = HC._maybe_authoring_bridge_cron(KIN, NIGHT_REPLY, [], scopes)
check("D2 the write landed on the cron path",
      "Swapped Thursday's shift" in (mem / "speakerfifteen.md").read_text(encoding="utf-8"))
check("D2 the kin gets a receipt in its own history", bool(note))
check("D2 the operator gets a basename-only line for the journal",
      bool(confirm) and "speakerfifteen.md" in confirm and str(kin_dir(KIN)) not in confirm)
check("D2 the tended scope was archived", not load_staging(KIN, "desktop"))

# D3. Nothing pending: the kin must NOT be handed an empty tending correction,
# or it mimes a tend it has no work for. It gets the ordinary empty-staging line.
msgs_empty = list(WAKE)
had_empty, scopes_empty = HC._inject_staging_status(KIN, msgs_empty, [])
check("D3 an empty staging yields no scopes to archive",
      not had_empty and scopes_empty == [])
check("D3 and no tending correction is injected",
      "You don't have them tonight"
      not in "\n".join(m["content"] for m in msgs_empty))

# D4. The retry loop is gated on tools. A tool-less kin must never be
# re-prompted for a read_staging call it cannot make — that would be the
# nightly wake-up telling it, repeatedly, that it failed.
_src = (pathlib.Path(__file__).resolve().parents[1] / "hearthkin_cron.py"
        ).read_text(encoding="utf-8")
check("D4 both tend-retry loops require tools to be present",
      _src.count("if (schemas and tend_retry > 0 and staging_had_work") == 2)

# D5. The cron-isolated branch commits the write too — it used to journal the
# reply and drop the file, so a good tend left nothing behind.
# D6. The third route: cron firing while Hearthkin is open and the kin is the
# active one. That wake-up goes through the ordinary desktop send (which inlines
# the notes), so it must NOT also get the count-only line — that would announce
# two pending scopes directly above the two, already present.
from frame.cron_exec_mixin import CronExecMixin  # noqa: E402

append_staging(KIN, "desktop", "One more for the pile.", source_label="test")
_suffix = CronExecMixin._cron_staging_suffix(None, KIN)
# The count line names the scopes it is counting, so a scope name is its
# signature — and it must not appear beside notes the send path already inlined.
check("D6 the live-injection wake-up gets the correction, not the count",
      "You don't have them tonight" in _suffix and "desktop" not in _suffix)
_suffix_ctl = None
save_kin_tools(KIN, ["read_staging", "write_file", "archive_staging"])
_suffix_ctl = CronExecMixin._cron_staging_suffix(None, KIN)
save_kin_tools(KIN, [])
check("D6-control a tooled kin still gets the ordinary status line",
      "You don't have them tonight" not in _suffix_ctl and _suffix_ctl.strip())

# Matched loosely on purpose. What this check is FOR is that the isolated
# branch still commits, rather than journalling the reply and dropping the
# file. It is not for how many arguments the call happens to take. Pinning
# the exact argument list made it fail the moment a parameter was added,
# which is a test reporting on its own literal instead of on the behaviour.
check("D5 the cron-isolated branch commits no-tools writes",
      re.search(r"_toolless_memory_cron\(\s*kin, reply, safe_tools,"
                r"\s*toolless_scopes", _src) is not None)


# --------------------------- F. notes belong at a tending moment, not always

# Staging holds summarised PREVIOUS conversation. The first version of this
# module inlined it in front of the live message on EVERY turn, so a small
# model read a wodge of old material before the new one and answered that
# instead. Reported from a real chat as the kin being "a message behind" — on a
# kin with 3,024 characters of notes pending, on an 8k context. And because a
# tool-less kin only clears staging by successfully writing a file, it would
# have kept happening on every turn indefinitely.

append_staging(KIN, "desktop", "Notes from an earlier conversation.", "test")
CHAT = [{"role": "user", "content": "how was your morning?"}]

out_f, sc_f = tlm.inject(CHAT, KIN, [])
check("F ordinary chat carries NO staging notes",
      out_f == CHAT and sc_f == [])
check("F ...so the live message is what the kin reads first",
      out_f[-1]["content"] == "how was your morning?")

# A scheduled wake-up IS the moment.
out_t, sc_t = tlm.inject(CHAT, KIN, [], tending=True)
check("F a scheduled wake-up does carry them", sc_t == ["desktop"])

# And so is being asked, in words, because a tool-less kin cannot answer that
# ask by calling read_staging — the harness has to notice on its behalf.
for ask in ("would you tend your staging notes?",
            "can you go through your pending notes tonight",
            "please tend your memory before bed"):
    o, s = tlm.inject([{"role": "user", "content": ask}], KIN, [])
    check(f"F asking works: {ask[:34]!r}", s == ["desktop"])

# Control: ordinary talk that merely mentions memory must not trip it.
for chat in ("do you remember the harbour?",
             "my notes are a mess lately",
             "I read a book about staging plays",
             "what did you have for tea?"):
    o, s = tlm.inject([{"role": "user", "content": chat}], KIN, [])
    check(f"F-control not tending: {chat[:32]!r}", s == [])

archive_staging(KIN, "desktop")


# ------------------------------------- E. when the kin doesn't say it exactly

# The whole loop rests on the kin producing a fenced block with a filename on
# it. Small models mostly don't. Measured against realistic replies, the taught
# form was a MINORITY of what comes back, and the misses were SILENT — the kin
# thanked itself for a save that never happened. That is the worst outcome
# available here, because it compounds: the kin builds on a memory it never had
# and nobody finds out.
#
# Two defences. Near-misses are recovered. Everything else is TOLD.

def landed(reply):
    res, _a = tlm.commit(KIN, reply, [], shown_scopes=[])
    return any(ok for (_p, ok, _d) in res), res

# E1. The near-miss shapes a kin actually produces.
ok1, _ = landed("*writes it into memory/speakerfifteen.md*\n\n```\nRota is four on.\n```")
check("E1 emote naming the file, then a plain fence", ok1)

ok2, _ = landed("*saves speakerfifteen.md*\n\n```\nRota is four on.\n```")
check("E1 a bare filename means its own memory, not a refusal", ok2)
check("E1 ...and it landed under memory/",
      (mem / "speakerfifteen.md").exists() and not (kin_dir(KIN) / "speakerfifteen.md").exists())

ok3, _ = landed("I'll put this in memory/speakerfifteen.md:\n\n```markdown\nRota.\n```")
check("E1 filename in the prose, language tag on the fence", ok3)

ok4, _ = landed("```\n# memory/speakerfifteen.md\nRota is four on.\n```")
check("E1 filename as a heading inside the fence", ok4)

# E1-control: the loosened matching must NOT turn ordinary conversation into
# disk writes. A fence with no save intent and no memory path writes nothing.
before_files = sorted(p.name for p in mem.rglob("*"))
ok5, res5 = landed("here's what that looks like:\n\n```python\nprint('hi')\n```")
check("E1-control example code is still not a write",
      not ok5 and sorted(p.name for p in mem.rglob("*")) == before_files)
ok6, _ = landed("that's nice to hear. how was the rest of it?")
check("E1-control ordinary conversation writes nothing", not ok6)

# E2. Everything that TRIED and failed is told plainly. This is the half that
# matters most — a silent miss is worse than a refusal.
for label, reply in [
    ("a pure gesture naming a file",
     "*writes it into memory/speakerfifteen.md* Got it, that's saved."),
    ("a gesture naming nothing", "*jots it down* I'll remember that one."),
    ("a plain statement of intent",
     "Good to know — I'll add that to my notes on SpeakerFifteen."),
    ("a fence we could not place",
     "Adding to my SpeakerFifteen log:\n\n```\nRota is four on.\n```"),
]:
    _ok, _res = landed(reply)
    check(f"E2 {label} is told nothing was saved",
          not _ok and bool(tlm.missed_write_nudge(KIN, reply, _res)))

# E2-control: a kin that saved something is NOT nudged, and a kin just talking
# is not nudged either. Otherwise the note becomes noise and stops being read.
good = "```append:memory/speakerfifteen.md\nRota is four on.\n```"
ok_good, res_good = landed(good)
check("E2-control a successful save is not nudged",
      ok_good and not tlm.missed_write_nudge(KIN, good, res_good))
chat = "that's nice to hear. how was the rest of it?"
check("E2-control ordinary conversation is not nudged",
      not tlm.missed_write_nudge(KIN, chat, []))

# E3. A miss must never archive. The notes have to survive for the retry the
# nudge asks for — otherwise the nudge tells the kin to keep something that
# has already been filed away.
append_staging(KIN, "desktop", "Still to be tended.", source_label="test")
_res, _arch = tlm.commit(KIN, "*writes it down* saved!", [],
                         shown_scopes=["desktop"])
check("E3 a missed write archives nothing", _arch == [])
check("E3 the notes are still there for the retry",
      bool(load_staging(KIN, "desktop")))


print()
if _fails:
    print(f"{len(_fails)} FAILED: " + "; ".join(_fails))
    sys.exit(1)
print("all toolless-memory checks passed")
