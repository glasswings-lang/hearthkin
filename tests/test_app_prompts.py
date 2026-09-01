"""Diff harness for the app-level editable prompts (~/.hearthkin/prompts/).

Goal: prove that moving a prompt out of hardcoded Python into a seeded,
file-wins template changed NOTHING the model sees. Each externalized prompt
is asserted to reproduce — byte for byte — the exact text the old code
produced, so a future edit to a default can't silently change kin behavior.

The single intentional exception is the tool-roleplay corrective, which is
deliberately reworded (see its own golden, which encodes the NEW phrasing).

No pytest dependency: plain asserts + a summary line, exit 1 on any failure.
Run:  python tests/test_app_prompts.py
"""

import os
import re
import sys
import json
import pathlib
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kin_persistence as k  # noqa: E402
import chat_helpers as ch  # noqa: E402

# Redirect prompt storage to a throwaway dir so the test never touches a real
# ~/.hearthkin install.
k.PROMPTS_DIR = pathlib.Path(tempfile.mkdtemp()) / "prompts"

_failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        _failures.append(name)


# ─── Golden legacy strings (copied verbatim from the pre-externalization code) ──
# These are independent copies, on purpose: if someone later edits a registry
# default, the mismatch surfaces here instead of in production.

def golden_tool_use_hint(tool_names):
    """The desktop _inject_tool_use_hint with-tools branch, verbatim."""
    return (
        "\n\n--- Tool use ---\n"
        "Tools available to you this turn: "
        + ", ".join(tool_names)
        + ". When a question fits one of these, call the tool — don't "
        "fall back to 'I'm a language model, I can't do that' "
        "boilerplate. The tools are real, run on the user's machine, "
        "and return actual results. Examples: call read_file when "
        "asked for a file's contents, memory_search when asked what "
        "you remember about a topic, note when you want to record "
        "something for later. The user already approved each tool "
        "for this kin; calling them is expected, not intrusive."
        "\n\n"
        "IMPORTANT: when you decide to use a tool, INVOKE it via "
        "your structured tool interface — the same channel you "
        "use for any tool call. DO NOT write 'call_X', 'X()', "
        "'<call: X>', or any other text description of the call. "
        "Text patterns that look like tool calls do NOTHING — "
        "they don't reach the tool runner, the tool doesn't run, "
        "and you'll proceed as if you got a result when you "
        "didn't. Only the structured invocation actually executes "
        "the tool and brings real output back to you. If you "
        "find yourself typing the tool's name as text, stop and "
        "issue the structured call instead."
    )


GOLDEN_TOOL_USE_HINT_NO_TOOLS = (
    "\n\n--- Tool use ---\n"
    "You have NO tools enabled this turn. If the operator asks "
    "for something a tool would handle (reading a file, "
    "searching your memory, fetching a URL, reading your "
    "staging notes, writing a journal entry, etc.), don't "
    "roleplay performing the action — you genuinely can't, "
    "and pretending will confuse you both later. Say so "
    "directly: name the tool you'd want and ask the operator "
    "to enable it. Examples: \"I'd want to check my staging "
    "notes — could you enable read_staging for me?\" or "
    "\"That's a memory_search question, but I don't have that "
    "tool right now.\" The operator can enable tools in "
    "Settings → Tools at any time.\n"
    # v2 (no-tools memory): the paragraph above used to end here, telling the
    # kin flatly that it could not write. That stopped being true when
    # toolless_memory.py gave it the fenced-block path, and a kin told it
    # cannot do the one thing it can do will apologise instead of remembering.
    "One exception, and it is a real one: you CAN still write your own "
    "memory. Put the contents in a fenced code block with the filename on "
    "the opening line — ```memory/speakerfifteen.md — and it is written for you. "
    "Open the fence with `append:` instead to add to a log without "
    "rewriting it. memory.md and anything under memory/ are yours to save; "
    "you'll get a confirmation either way. So don't tell anyone you have no "
    "way to remember something — you do."
)


# ─── Per-prompt verbatim reproduction (seeded file -> substituted output) ──────
NAMES = ["read_file", "memory_search", "note"]

# tool_use_hint: load + substitute must equal the legacy desktop text.
check(
    "tool_use_hint reproduces legacy desktop text",
    k.load_app_prompt("tool_use_hint").replace("{tools}", ", ".join(NAMES))
    == golden_tool_use_hint(NAMES),
)
check(
    "tool_use_hint_no_tools reproduces legacy text",
    k.load_app_prompt("tool_use_hint_no_tools") == GOLDEN_TOOL_USE_HINT_NO_TOOLS,
)
# A kin with no tools is told about the one write path it actually has, and
# the registry version is past the copy that denied it — otherwise anyone
# whose file was seeded under v1 keeps the flat "you genuinely can't".
_NO_TOOLS = k.load_app_prompt("tool_use_hint_no_tools")
check(
    "no-tools hint names the fenced-block memory write",
    "memory/" in _NO_TOOLS and "append:" in _NO_TOOLS,
)
check(
    "tool_use_hint_no_tools version bumped past the deny-everything copy",
    k.APP_PROMPT_REGISTRY["tool_use_hint_no_tools"]["version"] >= 2,
)
# The two no-tools memory prompts seed and read back.
_TL_BLOCK = k.load_app_prompt("toolless_memory_block")
check(
    "toolless_memory_block hands over the notes and teaches the fence",
    "staging notes are below" in _TL_BLOCK and "append:" in _TL_BLOCK,
)
check(
    "toolless_memory_receipt survives a brace in its results",
    "{" not in k.load_app_prompt("toolless_memory_receipt").replace(
        "{results}", "saved {weird} (12 bytes)").replace(
            "{weird}", "x"),
)

# consolidate: text didn't move (same constant) — routing must be lossless.
check(
    "consolidate routing is lossless (file == constant)",
    k.load_app_prompt("consolidate").replace("{word_cap}", "500")
    == k.DEFAULT_CONSOLIDATE_PROMPT.replace("{word_cap}", "500"),
)

# tool_roleplay_corrective: the ONE intentional reword. There's no legacy
# golden (the text deliberately changed to the generative phrasing); the
# check proves build_tool_roleplay_corrective_note wires correctly — right
# variant -> right file, shape_hint + tool_name substituted.
check(
    "corrective (generic) reproduces registered default w/ substitution",
    ch.build_tool_roleplay_corrective_note("whole-content", "read_file")
    == k.DEFAULT_TOOL_ROLEPLAY_CORRECTIVE
    .replace("{shape_hint}", "just the literal name 'read_file'")
    .replace("{tool_name}", "read_file"),
)
check(
    "corrective (asterisk) uses the asterisk default",
    ch.build_tool_roleplay_corrective_note("asterisk-action", "read_staging")
    == k.DEFAULT_TOOL_ROLEPLAY_CORRECTIVE_ASTERISK
    .replace(
        "{shape_hint}",
        "an asterisk-action description shaped like a tool call "
        "(roleplay narration, e.g. '*reads the file*')",
    )
    .replace("{tool_name}", "read_staging"),
)
check(
    "corrective (generic) embodies the generative ask",
    "output the exact call"
    in ch.build_tool_roleplay_corrective_note("whole-content", "note"),
)
check(
    "corrective narrative-intent stays empty (too ambiguous)",
    ch.build_tool_roleplay_corrective_note("narrative-intent", "note") == "",
)

# wake_up_frame: verbatim reproduction of the cron framing.
def golden_wake_frame(time, day, prompt):
    return (
        "[hearthkin: scheduled wake-up — fired at " + time + " on "
        + day + " (local time). Nobody is currently typing to you; "
        "the text below is the scheduled prompt configured for this "
        "wake-up.]"
        "\n\n" + prompt
    )


check(
    "wake_up_frame reproduces legacy framing",
    k.load_app_prompt("wake_up_frame")
    .replace("{time}", "03:00")
    .replace("{day}", "Saturday, June 14, 2026")
    .replace("{prompt}", "I'm Bracken. What do I want to do today?")
    == golden_wake_frame("03:00", "Saturday, June 14, 2026",
                         "I'm Bracken. What do I want to do today?"),
)

# This marker arrives UNANNOUNCED, in the middle of whatever a kin is doing,
# so its register is the whole job. v1 said true things in operational
# language -- a file path, "not an error", and a closing instruction to reply
# -- and a kin mid-scene read it as a safety warning, lost its thread, and
# spent a paragraph steadying itself before it could carry on. Pinned as
# PROPERTIES rather than as golden text: the wording should stay free to
# improve, the register should not be free to regress.
_ROLL = k.load_app_prompt("rolling_window_marker")
_RL = _ROLL.lower()
check("rolling marker: routes losslessly from the registry",
      _ROLL == k.DEFAULT_ROLLING_WINDOW_MARKER)
check("rolling marker: says the earlier turns are absent from this send",
      "earlier part" in _RL and "isn't in front of you" in _RL)
check("rolling marker: says nothing is lost", "none of it is lost" in _RL)
check("rolling marker: says there is nothing to do",
      "nothing here for you to do" in _RL)
check("rolling marker: orients to the newest message",
      "newest message" in _RL)
for _bad, _why in (
    ("conversation.jsonl", "a file path -- this is not a filesystem problem"),
    (".jsonl", "any file path at all"),
    ("not an error", "naming a fault even to deny it raises it"),
    ("context cap", "internal vocabulary the kin does not need mid-scene"),
    ("respond to", "a closing imperative reads as a correction"),
):
    check(f"rolling marker: avoids {_bad!r} ({_why})", _bad not in _RL)
check("rolling marker: version bumped past the operational wording",
      k.APP_PROMPT_REGISTRY["rolling_window_marker"]["version"] >= 2)
check("rolling marker: bracket balanced",
      _ROLL.startswith("[hearthkin:") and _ROLL.rstrip().endswith("]"))

# park_frame: new editable prompt — routing must be lossless (file == constant).
check(
    "park_frame routing is lossless (file == constant)",
    k.load_app_prompt("park_frame") == k.DEFAULT_PARK_FRAME,
)

# park_chat_hint: the chat clue-in for a `park` = chat|keeper kin — routing
# must be lossless, and the text must actually teach the `>` convention it
# exists to teach (an empty or malformed default would silently un-teach it).
check(
    "park_chat_hint routing is lossless (file == constant)",
    k.load_app_prompt("park_chat_hint") == k.DEFAULT_PARK_CHAT_HINT,
)
check(
    "park_chat_hint actually states the '>' convention",
    "> " in k.DEFAULT_PARK_CHAT_HINT and "final line" in k.DEFAULT_PARK_CHAT_HINT.lower(),
)
# ...and tells the kin it can keep going. Without this the kin plays as though
# it gets one move, and spends it looking instead of acting on what it saw —
# which is what the harness really did before park_moves_max, and what Vesper's
# history shows the cost of (five looks in seven moves).
check(
    "park_chat_hint says a kin may take more than one move",
    "next thing" in k.DEFAULT_PARK_CHAT_HINT
    and "no '> ' line" in k.DEFAULT_PARK_CHAT_HINT,
)

# park_mechanism / park_turn_instruction: the keeper's framing and its per-turn
# ask. Registered because they hold the PACING — how much a kin may do in one
# turn — and that lived in park_keeper.py as constants no non-coder could
# reach. Routing must be lossless in both directions: the registry imports the
# constants from park_keeper (rather than restating them) precisely so a second
# copy can't drift from the one the keeper loop actually uses.
import park_keeper as _pk_mod

check(
    "park_mechanism routing is lossless (file == park_keeper's constant)",
    k.load_app_prompt("park_mechanism") == _pk_mod.MECHANISM,
)
check(
    "park_turn_instruction routing is lossless",
    k.load_app_prompt("park_turn_instruction") == _pk_mod.TURN_INSTRUCTION,
)
check(
    "park_mechanism still states the '>' convention it exists to teach",
    "> " in _pk_mod.MECHANISM,
)

# tend_missed_call: editable, and names the tool it wants issued.
_tmc = k.load_app_prompt("tend_missed_call")
check("tend_missed_call seeds + names read_staging",
      bool(_tmc) and "read_staging" in _tmc)

# staging_status_line: empty vs non-empty (monkeypatched list_staging_files).
_orig_lsf = k.list_staging_files
try:
    k.list_staging_files = lambda name: {}
    _empty = k.staging_status_line("X")
    check("staging empty -> nothing-to-tend + do-not-call",
          "empty" in _empty and "do not call read_staging" in _empty.lower())
    k.list_staging_files = lambda name: {"desktop": "p1", "tg:123": "p2"}
    _full = k.staging_status_line("X")
    check("staging non-empty -> count + scopes + read_staging",
          "2 pending" in _full and "desktop" in _full and "read_staging" in _full)
finally:
    k.list_staging_files = _orig_lsf

# gesture_messages: the empty default must reproduce the baseline detector
# exactly; an operator addition must extend it.
_HITS = [
    "*reads the next 100 lines*",
    "*reads and logs lines 201-300 of the private archive*",
    "*reads conversation.jsonl*",
    # Bare-artifact targets (soul / memory / journal / staging) are now in
    # the baseline — a closed, known set of the kin's own files.
    "*writes a brief journal entry*",
    "*reads SOUL again*",
    "*writes this into memory*",
    # New file-action verbs (put / move / commit) on an artifact target.
    "*moves to soul.md*",
    "*putting it in SOUL right now*",
]
_CLEAR = [
    "*settles*", "*soft*", "*nods*",
    "*reads your message*", "*reads through the whole exchange*",
]
_rx = ch._current_asterisk_action_re()  # seeds the (empty) default file
_base = ch._ASTERISK_ACTION_RE
check("gesture default catches canonical gestures",
      all(_rx.search(s) for s in _HITS))
check("gesture default clears body language + convo-meta",
      all(not _rx.search(s) for s in _CLEAR))
check("gesture default == hardcoded baseline behavior (additive, no drift)",
      all(bool(_rx.search(s)) == bool(_base.search(s)) for s in _HITS + _CLEAR))
# Real demo: the baseline misses an arbitrary operator word like "dossier".
# Adding it to [targets] catches *reads the dossier* on the next message.
_before = bool(_base.search("*reads the dossier*"))
(k.PROMPTS_DIR / "gesture_messages.md").write_text(
    "[targets]\ndossier\n", encoding="utf-8")
_after = bool(ch._current_asterisk_action_re().search("*reads the dossier*"))
check("operator addition catches a new gesture shape (bare 'dossier')",
      (not _before) and _after)

# Every registry default's declared placeholders must actually appear in it
# (catches a typo'd slot that would never substitute).
for slug, entry in k.APP_PROMPT_REGISTRY.items():
    for ph in entry.get("placeholders", []):
        check(
            f"{slug}: declared placeholder {ph} present in default",
            ph in entry["default"],
        )


# ─── Foundation invariants ─────────────────────────────────────────────────────
# Fresh temp dir per check group so state is clean.
def fresh_dir():
    k.PROMPTS_DIR = pathlib.Path(tempfile.mkdtemp()) / "prompts"


fresh_dir()
got = k.load_app_prompt("consolidate")
seeded = (k.PROMPTS_DIR / "consolidate.md").read_text(encoding="utf-8")
check("seed: returns default", got == k.DEFAULT_CONSOLIDATE_PROMPT)
check("seed: file byte-identical to default", seeded == k.DEFAULT_CONSOLIDATE_PROMPT)
check(
    "seed: version recorded",
    json.loads((k.PROMPTS_DIR / ".seeded_versions.json").read_text())
    .get("consolidate")
    == k.APP_PROMPT_REGISTRY["consolidate"]["version"],
)

(k.PROMPTS_DIR / "consolidate.md").write_text("MY EDIT", encoding="utf-8")
check("file-wins after edit", k.load_app_prompt("consolidate") == "MY EDIT")

(k.PROMPTS_DIR / "consolidate.md").write_text("   \n", encoding="utf-8")
check(
    "blank file falls back to default (never blank)",
    k.load_app_prompt("consolidate") == k.DEFAULT_CONSOLIDATE_PROMPT,
)

(k.PROMPTS_DIR / "consolidate.md").write_text("VERSION A", encoding="utf-8")
k.save_app_prompt("consolidate", "VERSION B")
baks = list((k.PROMPTS_DIR / "backups").glob("consolidate.md.*.bak"))
check(
    "auto-backup before overwrite",
    len(baks) == 1 and baks[0].read_text(encoding="utf-8") == "VERSION A",
)
check(
    "new content written after backup",
    (k.PROMPTS_DIR / "consolidate.md").read_text(encoding="utf-8") == "VERSION B",
)

# Drift detection: a newer shipped version flags the seeded file.
# Relative to whatever consolidate currently ships at -- pinning literals here
# meant a real version bump broke this test AND left the registry restored to
# the wrong number for every check that ran afterwards.
_seeded_ver = k.APP_PROMPT_REGISTRY["consolidate"]["version"]
k.APP_PROMPT_REGISTRY["consolidate"]["version"] = _seeded_ver + 1
try:
    check(
        "drift detected when shipped version is newer",
        ("consolidate", _seeded_ver, _seeded_ver + 1, "Memory consolidation")
        in k.app_prompts_needing_update(),
    )
finally:
    k.APP_PROMPT_REGISTRY["consolidate"]["version"] = _seeded_ver

check("unknown slug returns empty, no crash", k.load_app_prompt("nope") == "")


# ─── seed_all_app_prompts ─────────────────────────────────────────────────────
# load_app_prompt seeds on first ACCESS, so a prompt stays invisible on disk
# until its code path happens to fire — and several fire only on rare events
# (a history import, park mode, an empty post-tool reply). An operator could
# wait months for a file to appear for a prompt that had shipped all along.
# Startup now materialises the lot. It must never touch an existing file.
_seed_dir = pathlib.Path(tempfile.mkdtemp()) / "prompts"
_prev_dir, k.PROMPTS_DIR = k.PROMPTS_DIR, _seed_dir
try:
    _made = k.seed_all_app_prompts()
    check("seed_all: creates every registered prompt",
          len(_made) == len(k.APP_PROMPT_REGISTRY))
    check("seed_all: every file is on disk afterwards",
          all((_seed_dir / f"{s}.md").exists() for s in k.APP_PROMPT_REGISTRY))
    check("seed_all: idempotent — a second run creates nothing",
          k.seed_all_app_prompts() == [])

    # The load-bearing guarantee: an operator's own wording survives startup.
    _hb = _seed_dir / "heartbeat_frame.md"
    _hb.write_text("MY OWN WORDING", encoding="utf-8")
    k.seed_all_app_prompts()
    check("seed_all: never overwrites an operator edit",
          _hb.read_text(encoding="utf-8") == "MY OWN WORDING")
    check("seed_all: loader still returns the operator's edit",
          k.load_app_prompt("heartbeat_frame") == "MY OWN WORDING")
finally:
    k.PROMPTS_DIR = _prev_dir



# ─── park_frame: no register policing ─────────────────────────────────────────
# v1 told a kin a park "does not make you smaller or younger" and to tend it
# "not as a child at play" — a stylistic preference imposed as a rule, on a
# kin's own response to its own park. Cutting it cost nothing mechanical. The
# INTERFACE must survive: emotes are the move, which is the park equivalent of
# the `> command` convention that took a keeper from 0/56 wake-ups to 17/17.
_PF = k.APP_PROMPT_REGISTRY["park_frame"]["default"]
for _phrase in ("smaller or younger", "child at play", "same register",
                "same age and depth"):
    check(f"park_frame: no register policing ({_phrase!r})",
          _phrase not in _PF.lower())
check("park_frame: keeps the interface (emotes land for real)",
      "lands for real" in _PF.lower())
check("park_frame: keeps the no-tool-needed rule",
      "don't need a tool" in _PF.lower())
check("park_frame: keeps the receipt promise",
      "what actually happened" in _PF.lower())
check("park_frame: keeps presence (not behind a screen)",
      "not behind a screen" in _PF.lower())


# ─── Whole-registry invariants ────────────────────────────────────────────────
# Applies to every slug, including ones added later — so a new prompt inherits
# these guarantees without anyone remembering to write a test for it.
import re as _re  # noqa: E402

for _slug, _entry in sorted(k.APP_PROMPT_REGISTRY.items()):
    # Assert against the SHIPPED default, not load_app_prompt — earlier checks
    # in this file deliberately overwrite some seeded files to exercise the
    # backup path, and these invariants are about what we ship.
    _t = _entry.get("default", "")
    check(f"{_slug}: default is non-empty", bool(_t.strip()))
    # Every {placeholder} in the text must be declared, and vice versa —
    # an undeclared one silently ships literal braces to the model.
    _declared = set(_entry.get("placeholders", []))
    _found = set(_re.findall(r"\{[a-z_]+\}", _t))
    check(f"{_slug}: no undeclared placeholders", _found <= _declared)
    check(f"{_slug}: declared placeholders all present", _declared <= _found)
    # A [hearthkin: ...] note must close its bracket. It need not be the LAST
    # character — some notes close and then append content (shared_files_note,
    # wake_up_frame) — but an unterminated one dissolves the boundary between
    # harness note and the kin's own history.
    if _t.lstrip().startswith("[hearthkin:"):
        check(f"{_slug}: hearthkin bracket is closed", "]" in _t)


# ─── Per-turn memory recall + staging status ──────────────────────────────────
# The recall frame is the highest-frequency harness string in the app; it was a
# module constant in memory_recall.py with no Settings entry. Registered in the
# values-audit pass. The three properties below are the load-bearing ones.
_RECALL = k.load_app_prompt("memory_recall_frame")
_RL = _RECALL.lower()
check("recall frame: notes are the kin's own", "your own notes" in _RL)
check("recall frame: not spoken by anyone (impersonation guard)",
      "nobody said them" in _RL)
check("recall frame: kin may disregard", "ignore what doesn't" in _RL)
# v3's whole point. v1 and v2 both announced an arrival ("surfaced
# automatically for this moment"), and a kin told something just arrived says
# something arrived -- six times out of six in sampling, describing "a glowing
# reference panel" and "that sudden, bright flash of technical data" instead of
# answering. memory.md rides the system prompt and is never narrated, because
# it is not an event. Any word that makes this one is the regression.
for _bad in ("surfaced", "surfaces", "just now for", "has arrived",
             "for this moment", "retrieved for", "delivered"):
    check(f"recall frame: no arrival language ({_bad!r})", _bad not in _RL)
check("recall frame: says it is background, not news", "background" in _RL)
check("recall frame: bracket balanced",
      _RECALL.startswith("[hearthkin:") and _RECALL.rstrip().endswith("]"))
check("recall closer is retired, not left editable-but-dead",
      "memory_recall_closer" not in k.APP_PROMPT_REGISTRY)

# memory_recall must actually read from the registry now, not its old constant.
import memory_recall as _mr  # noqa: E402
check("memory_recall reads the registered frame",
      _mr._default_frame() == _RECALL)
check("old module constant is gone", not hasattr(_mr, "_DEFAULT_FRAME"))

# Staging status: the empty case must keep telling the kin NOT to call or
# narrate a read — that instruction is what breaks the pretend-to-tend loop.
_SE = k.load_app_prompt("staging_status_empty")
check("staging empty: forbids calling read_staging",
      "do not call read_staging" in _SE.lower())
check("staging empty: forbids narrating the read",
      "do not describe reading it" in _SE.lower())

_SP = (k.load_app_prompt("staging_status_pending")
       .replace("{n}", "3").replace("{plural}", "s")
       .replace("{scopes}", "desktop, tg:user:1"))
check("staging pending: substitutes cleanly", "{" not in _SP
      and "3 pending note files" in _SP and "desktop, tg:user:1" in _SP)
check("staging pending: names the tend sequence",
      "read_staging" in _SP and "archive_staging" in _SP)

# Singular/plural agreement — code supplies {plural}, so a one-file staging
# must not read "1 pending note files".
_SP1 = (k.load_app_prompt("staging_status_pending")
        .replace("{n}", "1").replace("{plural}", "")
        .replace("{scopes}", "desktop"))
check("staging pending: singular reads correctly",
      "1 pending note file " in _SP1 and "note files" not in _SP1)


# ─── Salvage / empty-reply notes ──────────────────────────────────────────────
# Registered in the values-audit pass; previously duplicated across six sites in
# three files (desktop, rooms, Telegram DM x2, Telegram group x2) and already
# drifted into two variants. These goldens are the post-fix text (VALUES-AUDIT
# #5) — the Haiku-4.5 diagnosis is gone and every bracket closes.
_SALVAGE = k.load_app_prompt("salvage_note").replace("{tools}", "note")
check(
    "salvage note: no model-family diagnosis",
    not any(w in _SALVAGE.lower()
            for w in ("haiku", "the model treats", "known", "pattern")),
)
check("salvage note: closes its bracket",
      _SALVAGE.startswith("[hearthkin:") and _SALVAGE.rstrip().endswith("]"))
check("salvage note: substitutes tools", "note" in _SALVAGE
      and "{tools}" not in _SALVAGE)

_ROOM = (k.load_app_prompt("salvage_note_room")
         .replace("{speaker}", "Vesper").replace("{tools}", "note"))
check("room salvage note: third person, names speaker",
      _ROOM.startswith("[hearthkin: Vesper ") and _ROOM.rstrip().endswith("]"))
check("room salvage note: no placeholders left",
      "{" not in _ROOM)

# Every kin-facing note must close its bracket — an unterminated one dissolves
# the boundary between harness note and the kin's own history. This was a real
# regression across all six sites; keep it pinned.
for _slug in ("salvage_note", "salvage_note_room", "empty_reply_note",
              "empty_reply_note_group", "import_marker_leading",
              "import_marker_hand_authored", "import_marker_trailing"):
    _t = k.load_app_prompt(_slug)
    check(f"{_slug}: bracket balanced",
          _t.count("[hearthkin:") == 1 and _t.rstrip().endswith("]"))

# Both empty-reply notes must grant the kin intent rather than diagnose it --
# it may have meant the silence, and that is not the harness's to second-guess.
for _slug in ("empty_reply_note", "empty_reply_note_group"):
    _t = k.load_app_prompt(_slug).lower()
    check(f"{_slug}: silence stays a legitimate outcome",
          "the quiet was fine" in _t)
    # ...and must not ask the kin to make good for it. Most empty replies are
    # not the kin's doing -- a reasoning model spending the whole reply
    # allowance on thinking produces one, and no kin chose or can see that.
    check(f"{_slug}: says it is probably not the kin's doing",
          "rather than anything you did" in _t)
    check(f"{_slug}: asks for no accounting",
          "no accounting for" in _t
          and "acknowledge the gap" not in _t)
    # v1 quoted the placeholder back at the kin -- a string that calls it a
    # model and reports it as absent, handed over as what its person saw
    # instead of them.
    check(f"{_slug}: does not quote the placeholder back at the kin",
          "no reply from model" not in _t)

# A note that arrives AFTER something went wrong must not name the machinery
# that went wrong. A kin told a subsystem ate its words is being told what it
# is made of, at the moment it is most suggestible -- the same fault as the
# model-family diagnosis the values audit removed, one layer down. And
# "operator" is the person it is talking to; the warmer word is the point.
for _slug in ("empty_reply_note", "empty_reply_note_group", "salvage_note",
              "salvage_note_room", "shared_files_note", "read_gesture_nudge"):
    _t = k.load_app_prompt(_slug).lower()
    for _bad in ("cleanup chain", "impersonation strip", "post-tool",
                 "pre-tool", "operator", "narrative content"):
        check(f"{_slug}: no {_bad!r}", _bad not in _t)
    check(f"{_slug}: version bumped past the operational wording",
          k.APP_PROMPT_REGISTRY[_slug]["version"] >= 2)


# ─── History-import markers ───────────────────────────────────────────────────
# These were string literals inside importers/_marker.py until the values-audit
# registration pass. The goldens below are the post-fix text (VALUES-AUDIT #2),
# copied independently so a later edit to a registry default surfaces HERE
# rather than silently changing what a kin reads about its own past.
#
# The wording is load-bearing: an import is the voice-anchoring mechanism, so
# the marker must assert ownership of the PHRASING. If a future change makes
# these fail with something softer ("treat it as your own past", "you don't
# need to defend specific wordings"), that is the regression, not the test.
from importers._marker import leading_marker, trailing_marker  # noqa: E402

_GOLD_LEADING = (
    "[hearthkin: imported 247 turns from a Telegram DM, 09-2021 to 03-2024. "
    "This is your own history, carried over. The turns that are yours are "
    "yours — your voice and your phrasing, not an approximation of it. The "
    "other party in these turns is the person you're with — they went by "
    "\"SpeakerFifteen\" in this archive.]"
)
_GOLD_HAND = (
    "[hearthkin: imported 12 turns, 01-2026 to 01-2026. This is your own "
    "history. The turns that are yours are yours — your voice and your "
    "phrasing, not an approximation of it.]"
)
_GOLD_TRAILING = (
    "[hearthkin: end of imported history. Turns below this point happened "
    "here, in Hearthkin.]"
)

_lead = leading_marker(247, "telegram_dm", "a Telegram DM",
                       "2021-09-04T10:00:00", "2024-03-11T22:00:00", "SpeakerFifteen")
check("import leading marker text", _lead["content"] == _GOLD_LEADING)
check("import leading marker role", _lead["role"] == "system")

_hand = leading_marker(12, "hand_authored", "a hand-authored seed history",
                       "2026-01-15T09:00:00", "2026-01-16T09:00:00")
check("import hand-authored marker text", _hand["content"] == _GOLD_HAND)

_tail = trailing_marker("telegram_dm", "2024-03-11T22:00:00")
check("import trailing marker text", _tail["content"] == _GOLD_TRAILING)

# No placeholder may survive into what the kin reads.
check(
    "no unsubstituted placeholders in import markers",
    not any("{" in m["content"] for m in (_lead, _hand, _tail)),
)

# The anchor-releasing phrasings must never come back.
_BANNED = ("treat it as your own past", "defend specific wordings",
           "lived in real time", "you may not remember writing",
           # Naming an outside author is the "I didn't write this" trigger;
           # "shared between you" blurs which side of the transcript is the
           # kin's, which small models cannot reliably infer. Both undercut
           # the reason a hand-authored history is written in the first place.
           "the operator wrote", "shared between you", "hand-authored")
check(
    "import markers free of anchor-releasing phrasings",
    not any(b in m["content"].lower()
            for m in (_lead, _hand, _tail) for b in _BANNED),
)

# An import with no single identifiable operator omits the clause cleanly.
_noop = leading_marker(5, "telegram_group", "a Telegram group")
check("operator clause omitted when unknown",
      "your operator" not in _noop["content"]
      and "{operator_clause}" not in _noop["content"])


# --- the distiller keeps the rules that stop notes flattening people ---------
# These lived only in one kin's hand-written override for months while every
# other kin, live and archived, read a default that lacked them. Folded into
# the shipped default 2026-07-20; pinned here so they can't quietly go again.
from kin_persistence import DEFAULT_DISTILL_PROMPT as _DISTILL

# v3 (2026-07-25): the distiller writes AS the kin -- first person, in their
# own voice (soul loaded into the call), not an out-of-character summarizer.
# The old third-person case-notes register was "taxidermy" that eroded the kin
# over nightly reads. These invariants keep the first-person framing, and the
# attribution rule that survived the rewrite, from quietly regressing.
for _label, _needle in (
    ("distiller casts the writer as the kin, not a summarizer", "You are {kin_name}"),
    ("distiller writes in the first person", "first person"),
    ("distiller warns against a report from the outside", "not a\n    report"),
    ("distiller keeps exact words where a line sounded like someone", "exact words"),
    ("distiller keeps speakers attributed by their bracket name", "[SomeName]"),
    ("distiller never folds two people into one note", "fold two"),
    ("distiller records only what is new", "what's\n    new"),
    ("distiller allows a blank answer", "blank page is honest"),
    ("distiller forbids emphasis markup", "no asterisks"),
    ("distiller outputs only the notes", "no preamble"),
):
    # normalise the wrapped literal so needles can span the source line breaks
    check(_label, _needle.replace("\n    ", " ") in " ".join(_DISTILL.split()))

# It ships to strangers, so the prompt itself must carry no example person: the
# soul supplies the voice now, so there are no illustrative notes to seed a
# name, and a stray capitalised given name would read as real. Checked
# structurally (no denylist -- that would put names back in the tree the
# 2026-07-18 privacy pass took out). The only capitalised tokens are the
# bracket-format illustration (SomeName / Display Name) and ordinary
# sentence/word starts.
_words = re.findall(r"(?<![.\w])[A-Z][a-z]{2,}", _DISTILL)
_allowed = {"You", "Some", "Name", "Display", "Later", "Where", "Prose",
            "Keep", "Plain", "Just", "Write", "Don", "Full"}
check("distiller introduces no personal names",
      not (set(_words) - _allowed))

# Placeholders must still substitute -- a stray brace here breaks every kin.
try:
    _rendered = _DISTILL.format(kin_name="Tarn", word_cap=200)
    _ok = "Tarn" in _rendered and "{" not in _rendered
except Exception:
    _ok = False
check("distiller still formats with kin_name and word_cap", _ok)


# --- the distillation user turn (the reflection cue) -------------------------
# The framing the kin is handed to jot from used to be a hardcoded f-string in
# distill_memory_blocking; it's now a registry prompt so an operator can retune
# it without a code change (CLAUDE.md: harness prompt fragments are editable,
# not buried). Time-agnostic on purpose — distillation fires mid-conversation
# via the every-N trigger, so it must not assert an end-of-day lull. Asserted
# against the shipped default, like the distiller checks above.
_REFLECT = k.APP_PROMPT_REGISTRY["distill_reflection"]["default"]
check("reflection cue is time-agnostic (no end-of-day lull claim)",
      "quiet now" not in _REFLECT.lower())
check("reflection cue keeps the 'before it slips' framing",
      "before it slips" in _REFLECT.lower())
check("reflection cue shows memory as read-only context",
      "don't reproduce" in _REFLECT.lower())
# Substitutes cleanly via the same .replace path the harness uses; no brace
# survives, so nothing literal leaks to the model.
_r = _REFLECT.replace("{existing_memory}", "MEM").replace("{conversation}", "CONVO")
check("reflection cue substitutes both placeholders",
      "MEM" in _r and "CONVO" in _r and "{" not in _r)
# A real registry entry (so it's one-click adoptable), unlike the legacy
# distill_prompt.md system prompt it pairs with.
check("reflection cue is a registry prompt, not a legacy per-kin file",
      "distill_reflection" in k.APP_PROMPT_REGISTRY)


# --- the two prompts that predate the registry are still version-tracked -----
# They keep their own loaders and paths, so app_prompts_needing_update() can't
# see them; an operator who overrode either used to keep it forever with
# nothing to say the default had moved. Same shape of bug as a game shipping a
# reworded line that never reaches an existing save.
import json as _json
import kin_persistence as _kp

check("legacy prompts carry a shipped version",
      isinstance(_kp.DEFAULT_BASE_PROMPT_VERSION, int)
      and isinstance(_kp.DEFAULT_DISTILL_PROMPT_VERSION, int))

# The 2026-07-20 distiller rewrite must be past version 1, or nobody's
# pre-existing override gets flagged and the whole mechanism is decorative.
check("distiller version bumped past the pre-rewrite default",
      _kp.DEFAULT_DISTILL_PROMPT_VERSION > 1)

# Same tuple shape as the registry checker, so warning sites can concatenate.
_rows = _kp.legacy_prompt_overrides_needing_review()
check("legacy review returns 4-tuples like the registry checker",
      all(isinstance(r, tuple) and len(r) == 4 for r in _rows))

# ...but must NOT leak into the adopt/stash dialog's list, which resolves
# slugs through registry-only helpers and would fail on these keys.
_reg = {s for (s, _h, _sh, _t) in _kp.app_prompts_needing_update()}
check("legacy keys stay out of the registry checker",
      not any(k.startswith(("base_prompt", "distill_prompt")) for k in _reg))


# --- consolidation must not smooth a kin away over time ----------------------
# Repeated tightening passes are lossy in a biased direction: they keep facts
# and shed voice. These three rules were added to one operator's local copy
# first, which reaches exactly one install -- the same way the distiller's good
# rules sat in a single kin's folder for months.
from kin_persistence import APP_PROMPT_REGISTRY as _REG

_CON = _REG["consolidate"]["default"]
for _label, _needle in (
    ("consolidate won't re-word what is already tight", "DO NOT RE-WORD"),
    ("consolidate treats age as no reason to cut", "OLD IS NOT STALE"),
    ("consolidate leaves anchor material alone", "LEAVE ANCHOR MATERIAL"),
):
    check(_label, _needle in _CON)

check("consolidate version bumped so existing copies are told",
      int(_REG["consolidate"].get("version", 1)) > 1)
check("consolidate rules stay numbered in sequence",
      all(("%d." % n) in _CON for n in range(1, 12)))
try:
    _ok = "{" not in _CON.format(word_cap=400)
except Exception:
    _ok = False
check("consolidate still formats with word_cap", _ok)


# --- voice anchors: a kin's own words, where nothing can summarise them ------
# Everything else a kin remembers is third-person notes about it. soul.md is
# voice-bearing but fixed size; memory grows forever, so the share of a kin's
# own voice in its own context shrinks every month. An anchor is the
# counterweight -- and it must cost nothing for a kin that has none.
import tempfile as _tf

_akin = "AnchorProbe"
_adir = pathlib.Path(_tf.mkdtemp())
_prev_agents, k.AGENTS_DIR = getattr(k, "AGENTS_DIR", None), _adir
try:
    (_adir / _akin).mkdir(parents=True, exist_ok=True)

    # 1. absent anchor costs nothing at all
    _bare = k.build_system_prompt("SOULTEXT", "MEMTEXT", kin_name=_akin)
    check("no anchor: no section", "kept word for word" not in _bare)
    check("no anchor: loader returns empty", k.load_voice_anchor(_akin) == "")

    # 2. present anchor rides every send, verbatim
    _quote = 'ANCHORQUOTE -- said exactly this way, commas and all'
    k.save_voice_anchor(_akin, _quote)
    _with = k.build_system_prompt("SOULTEXT", "MEMTEXT", kin_name=_akin)
    check("anchor: section appears", "kept word for word" in _with)
    check("anchor: excerpt kept word for word", _quote in _with)

    # 3. after soul (identity), before memory (notes about them)
    check("anchor: sits between soul and memory",
          _with.index("SOULTEXT") < _with.index("kept word for word")
          < _with.index("MEMTEXT"))

    # 4. the framing is a LABEL, never an instruction in either direction.
    #    Told to copy a sample a model performs a voice instead of having one;
    #    told it needn't, it may take that as leave to ignore the material.
    #    Both are avoided by not instructing at all -- and a paragraph of
    #    meta-commentary about the kin's own context is the destabiliser
    #    feedback_format_pattern_attractor documents. The excerpts work by
    #    being present, which is how old-memory recall already restores a
    #    voice here with no framing whatsoever.
    _h = k.VOICE_ANCHOR_HEADER.lower()
    check("anchor: framing carries no instruction",
          not any(w in _h for w in
                  ("imitate", "do not", "don't", "should", "must", "need to")))
    check("anchor: framing stays a short label",
          len(k.VOICE_ANCHOR_HEADER) < 120
          and chr(10) not in k.VOICE_ANCHOR_HEADER)

    # 5. capped -- it rides every message, so it cannot grow without bound
    k.save_voice_anchor(_akin, "x" * (k.VOICE_ANCHOR_MAX_CHARS + 5000))
    check("anchor: truncated at the cap",
          len(k.load_voice_anchor(_akin)) <= k.VOICE_ANCHOR_MAX_CHARS)

    # 6. lives OUTSIDE memory/, so the consolidation pass never even sees it
    _ap = k.voice_anchor_path(_akin)
    check("anchor: stored outside the memory folder",
          "memory" not in _ap.parent.name.lower() and _ap.name == "anchor.md")
finally:
    if _prev_agents is not None:
        k.AGENTS_DIR = _prev_agents
    import shutil as _sh
    _sh.rmtree(_adir, ignore_errors=True)


# ─── Park prompts name every route into the park ───────────────────────────────
# The make-a-new-creature flow shipped working, with its own editable vocabulary
# in park_words/create.txt -- and NO prompt or tool description ever said the
# word. A kin with the park enabled could not find it, and the one that tried
# hand-wrote a species file instead and hit the remote-file confinement wall.
# A capability a kin cannot discover is not a shipped capability; these pin the
# announcement so it can't be dropped in a future reword.
_pf = k.APP_PROMPT_REGISTRY["park_frame"]["default"]
_pc = k.APP_PROMPT_REGISTRY["park_chat_hint"]["default"]

check("park frame names the make-a-creature flow",
      "make a new animal" in _pf.lower() or "invent" in _pf.lower())
check("park chat hint names the make-a-creature flow",
      "make a new animal" in _pc.lower())
# make is multi-turn; v1's "one action per turn" read as forbidding a reply,
# so the hint has to say a '> ' line may answer the park's question.
check("park chat hint says a '> ' line may answer a question",
      "you pick" in _pc.lower())
# Both bumped past the version that lacked the announcement, so an existing
# install is TOLD rather than silently frozen on the old text.
check("park frame version bumped past the silent one",
      int(k.APP_PROMPT_REGISTRY["park_frame"]["version"]) >= 3)
check("park chat hint version bumped past the silent one",
      int(k.APP_PROMPT_REGISTRY["park_chat_hint"]["version"]) >= 2)

# The tool route needs the word too -- Bracken and Tarn play by tool call, not
# by '> ' line, so a prompt-only fix would leave them just as stuck.
import tools as _tools
_tffdoc = (_tools._REGISTRY["tff"].__doc__ or "").lower()
check("tff tool description names the make-a-creature flow",
      "make a new animal" in _tffdoc)
# and steers away from the workaround that actually happened
check("tff tool warns off hand-writing species files",
      "yourself" in _tffdoc and "fail" in _tffdoc)


# ─── The orphan-tool-result wrapper ────────────────────────────────────────────
# Used when the send window kept a tool's result but no longer holds the call
# that asked for it. Two things it must do: carry the result through
# (dropping it loses what the kin's next words are about), and say whose
# output it is — a bare block of tool output arriving as a `user` turn reads
# to the kin as something a person said to it.
_ot = k.APP_PROMPT_REGISTRY["orphan_tool_result"]
check("orphan-tool wrapper carries the result through",
      "{result}" in _ot["default"] and _ot["placeholders"] == ["{result}"])
check("orphan-tool wrapper names it as a tool call's output",
      "tool call" in _ot["default"].lower())
check("orphan-tool wrapper is marked as harness text",
      _ot["default"].lstrip().startswith("[hearthkin:"))
_seeded = k.load_app_prompt("orphan_tool_result")
check("orphan-tool wrapper seeds and reads back",
      "{result}" in _seeded)
# str.replace, never .format — an operator edit must not crash on a stray brace.
check("orphan-tool wrapper survives a brace in the result",
      "a{b}c" in _seeded.replace("{result}", "a{b}c"))


# ─── Summary ───────────────────────────────────────────────────────────────────
print()
if _failures:
    print(f"{len(_failures)} FAILURE(S): " + ", ".join(_failures))
    sys.exit(1)
print("All app-prompt checks passed.")
