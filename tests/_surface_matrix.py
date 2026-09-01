# SPDX-License-Identifier: CC0-1.0
"""The surface parity matrix: what each surface a kin speaks through can do,
and — where it can't — whether that is a gap or a closed question.

WHY THIS EXISTS

Hearthkin has four surfaces a kin actually talks through: the main window,
Telegram, Discord, and the scheduled-wake-up subprocess. They were built at
different times, and every improvement since has landed on whichever one
provoked it. Nothing anywhere held the whole picture, so the only way to find
out that a surface had missed something was to run into it — which means the
person using the app was the detector, and the detection method was
disappointment.

That produced a specific, repeated shape of bug: a feature that plainly works
in one place, is plainly missing in another, and looks identical to a fault in
the kin. "It didn't listen." "It ignored me." "It's slow on Discord." Each of
those turned out to be a surface that never got a fix another surface got
years earlier.

WHAT IT DOES

Every capability below is declared for every surface, as one of three things:

  Present  — this surface does it, and a marker proves the code is wired.
  Absent   — it does NOT, this is a real gap, and the reason is recorded.
  NotHere  — the question does not arise on this surface. This is a CLOSED
             question, not an excuse: "nobody is present to type" is NotHere,
             "we never got round to it" is Absent.

The distinction between Absent and NotHere is what stops this file rotting
into a wall of justifications. Absent is a to-do with a name on it; the test
prints them as a list every run, so the backlog is visible rather than
remembered.

THE RATCHET GOES BOTH WAYS, which is the part that actually prevents drift:

  declared Present, marker missing  -> FAIL. It regressed, or it was never
                                      really wired and the matrix flattered it.
  declared Absent, marker FOUND     -> FAIL. Somebody built it. Say so here,
                                      so the map stays true and the next
                                      person doesn't rebuild it.

So a capability cannot quietly appear OR quietly vanish. Adding a surface, or
a capability, forces an answer for every combination — the same reason
`tools/_buckets.py` makes an unbucketed tool a loud failure instead of a tool
that is silently invisible on two surfaces.

ON THE MARKERS

They are source-text probes with comments and docstrings STRIPPED, so a
comment *about* a feature can never be mistaken for the feature. This is
deliberately a coarse instrument: it answers "is this wired at all", not "is
it correct". Correctness is what the other ninety-odd test files are for.
A coarse instrument that covers everything is exactly what was missing —
nothing here was subtle, it was just unobserved.

`where` narrows a probe to one file, or to one function inside it
(`path::function`), which matters for the two bots: they are constructed a
few lines apart in the same frame method, so a file-level search cannot tell
which of them a callback was wired for.
"""

import ast
import glob
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ── the surfaces ───────────────────────────────────────────────────────
#
# A surface's own modules. Frame wiring is reached through an explicit
# `where` on the cell that needs it, never folded in here — the two bots are
# wired in the same file, so folding it in would credit each of them with
# whatever the other one got.

SURFACES = {
    "desktop": sorted(str(p.relative_to(ROOT)).replace("\\", "/")
                      for p in (ROOT / "frame").glob("*.py"))
    + ["frame_shared.py", "hearthkin.pyw"],
    "telegram": ["telegram_bot.py"],
    "discord": ["discord_bot.py"],
    "cron": ["hearthkin_cron.py", "cron_helpers.py"],
}

SURFACE_ORDER = ("desktop", "telegram", "discord", "cron")

# How each surface is described in failure text, so a message reads as a
# sentence about the app rather than a key from a dict.
SURFACE_NAMES = {
    "desktop": "the main window",
    "telegram": "Telegram",
    "discord": "Discord",
    "cron": "a scheduled wake-up",
}


# ── cell states ────────────────────────────────────────────────────────

class Present:
    """This surface has it. `markers` overrides the capability's default;
    `where` narrows the search to one file or one function."""

    state = "present"

    def __init__(self, markers=None, where=None, note=""):
        self.markers = (markers,) if isinstance(markers, str) else markers
        self.where = where
        self.note = note


class Absent:
    """A real gap. The reason is what someone reads when deciding whether to
    close it, so write what the person on that surface actually loses."""

    state = "absent"

    def __init__(self, reason, markers=None, where=None):
        self.reason = reason
        self.markers = (markers,) if isinstance(markers, str) else markers
        self.where = where
        self.note = reason


class NotHere:
    """The question does not arise here. A closed question, not a backlog
    item — so the reason must say why it can never apply, not why it hasn't
    happened yet.

    `probe=False` opts out of the reverse check, and is ONLY for cells where
    no marker could mean anything — "can a scheduled wake-up deliver to a
    scheduled wake-up" has no code that would ever indicate it. Reach for it
    rarely: the reverse check is what catches somebody quietly building a
    thing the map says is closed, so an opted-out cell is a cell nothing is
    watching. Everywhere else, name the marker that WOULD show up if this
    were built, and let it stay unmatched."""

    state = "not_here"

    def __init__(self, reason, markers=None, where=None, probe=True):
        self.reason = reason
        self.markers = (markers,) if isinstance(markers, str) else markers
        self.where = where
        self.probe = probe
        self.note = reason


class Capability:
    def __init__(self, key, what, why, markers, surfaces):
        self.key = key
        self.what = what          # one line: what it does, in plain words
        self.why = why            # one line: what it costs when it's missing
        self.markers = (markers,) if isinstance(markers, str) else markers
        self.surfaces = surfaces


# ── source reading ─────────────────────────────────────────────────────

_blob_cache = {}


def _strip(source):
    """Source with comments and DOCSTRINGS removed, but every other string
    left alone.

    Docstrings are found through the AST — the first statement of a module,
    class or function — not by guessing from token order. The first version of
    this guessed: it treated any string following a line break as a docstring,
    which inside brackets is simply a continuation line. That silently ate
    `load_app_prompt("tool_use_hint", ...)` and reported a wired feature as
    missing. A detector that produces false absences is worse than no
    detector, because absence is exactly what this file is used to claim.
    """
    try:
        tree = ast.parse(source)
    except Exception:
        return source
    doc_lines = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            doc_lines.update(
                range(first.lineno, (first.end_lineno or first.lineno) + 1))

    kept = [line for i, line in enumerate(source.splitlines(), start=1)
            if i not in doc_lines]
    rejoined = "\n".join(kept)

    # Comments are unambiguous at the token level, so they go by tokenizing.
    import io
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(rejoined).readline))
    except Exception:
        return rejoined
    return "\n".join(t.string for t in toks if t.type != tokenize.COMMENT)


def code_only(relpath):
    """The file's source with comments and docstrings removed.

    Without this the probe reads its own documentation: nearly every file
    here carries long comments naming the mechanisms it deliberately does
    NOT use, and a matrix that counted those would report the opposite of
    the truth on exactly the files that explain themselves best."""
    if relpath in _blob_cache:
        return _blob_cache[relpath]
    path = ROOT / relpath
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        _blob_cache[relpath] = ""
        return ""
    blob = _strip(source)
    _blob_cache[relpath] = blob
    return blob


def function_source(relpath, funcname):
    """Source of one function/method, comments and docstrings stripped.

    Needed because both bots are constructed within a few lines of each other
    in the frame, so 'does this file mention the callback' cannot answer
    'was it wired for Discord'."""
    path = ROOT / relpath
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return ""
    source = path.read_text(encoding="utf-8", errors="replace")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == funcname:
            seg = ast.get_source_segment(source, node)
            if not seg:
                return ""
            import textwrap
            return _strip(textwrap.dedent(seg))
    return ""


def blob_for(surface, cell):
    """The text a cell's markers are searched in."""
    where = getattr(cell, "where", None)
    if where:
        if "::" in where:
            path, func = where.split("::", 1)
            return function_source(path, func)
        return code_only(where)
    return "\n".join(code_only(p) for p in SURFACES[surface])


def markers_for(cap, cell):
    return getattr(cell, "markers", None) or cap.markers


def is_wired(cap, surface, cell):
    blob = blob_for(surface, cell)
    return any(m in blob for m in markers_for(cap, cell))


# ── the matrix ─────────────────────────────────────────────────────────
#
# Ordered roughly as a turn happens: what goes into the prompt, what comes
# back out, what the person can do about it, and what can reach them.

CAPABILITIES = [

    # --- building the prompt -------------------------------------------

    Capability(
        key="per_turn_recall",
        what="Puts relevant notes from the kin's own depth logs into the turn",
        why="Without it a kin only reaches its own writing if it thinks to go "
            "looking, which is exactly what smaller models don't do",
        markers="inject_into_messages",
        surfaces={
            "desktop": Present(),
            "telegram": Present(),
            "discord": Present(),
            "cron": Present(),
        },
    ),

    Capability(
        key="memory_index_repair",
        what="Rebuilds the pointer list of depth logs before the kin reads it",
        why="A kin whose index is stale cannot see files it wrote itself, and "
            "asks to be told again",
        markers="load_memory_for_prompt",
        surfaces={
            "desktop": Present(),
            "telegram": Present(
                where="frame/bot_integration_mixin.py::_start_bot_for"),
            "discord": Present(
                where="frame/bot_integration_mixin.py::_start_discord_bot_for"),
            "cron": Present(),
        },
    ),

    Capability(
        key="speaker_attribution",
        what="Builds the bracketed speaker prefix through the shared helper",
        why="The bracket shape is an impersonation attractor; one surface "
            "rolling its own is how the shape drifts apart again",
        markers="speaker_attribution_prefix",
        surfaces={
            "desktop": Present(),
            "telegram": Present(),
            "discord": Present(),
            "cron": NotHere(
                "A scheduled wake-up has one speaker — the prompt the person "
                "wrote in advance. There is nobody else in the turn to "
                "attribute."),
        },
    ),

    Capability(
        key="tool_use_hint",
        what="Tells the model, in an editable prompt, which tools it may call",
        why="Small models otherwise narrate using a tool instead of calling it",
        markers="tool_use_hint",
        surfaces={
            "desktop": Present(),
            "telegram": Present(),
            "discord": Present(),
            "cron": Present(),
        },
    ),

    Capability(
        key="stable_prompt_window",
        what="Keeps the front of the prompt still, so cached reading is reused",
        why="A window that sheds its oldest message every turn makes the model "
            "re-read everything from cold, permanently, with nothing on screen "
            "to say why",
        markers=("_trim_history", "_stable_history_window"),
        surfaces={
            "desktop": NotHere(
                "Keeps the whole conversation and lets the shared truncation "
                "budget in llm_backend pick the window — which has its own "
                "stability guard. There is no second per-surface cap here to "
                "get wrong."),
            "telegram": Present(),
            "discord": Present(),
            "cron": NotHere(
                "A wake-up builds a fresh prompt each time and has no prior "
                "turn to reuse, so there is no cached prefix to protect."),
        },
    ),

    # --- what comes back ------------------------------------------------

    Capability(
        key="reply_cleanup",
        what="Runs the anti-impersonation passes over the kin's reply",
        why="Without them a model writing another kin's name leaks through as "
            "though that kin had spoken",
        markers="clean_kin_reply",
        surfaces={
            "desktop": Present(),
            "telegram": Present(),
            "discord": Present(),
            "cron": Present(),
        },
    ),

    Capability(
        key="inline_thinking",
        what="Pulls reasoning out of the reply text instead of showing it",
        why="Models that emit thinking as markup otherwise have it read aloud "
            "as though it were speech",
        markers="extract_inline_thinking",
        surfaces={
            "desktop": Present(),
            "telegram": Present(),
            "discord": Present(),
            "cron": Present(),
        },
    ),

    Capability(
        key="empty_reply_diagnostics",
        what="Logs and shows it when a kin produces nothing",
        why="Silence is indistinguishable from being ignored, and leaves "
            "nothing to check afterwards",
        markers=("_log_empty_reply", "_log_empty_cron_reply"),
        surfaces={
            "desktop": Present(),
            "telegram": Present(),
            "discord": Present(),
            "cron": Present(),
        },
    ),

    Capability(
        key="intermediate_salvage",
        what="Uses what the kin said before a tool call, when the final reply "
             "comes back empty",
        why="Some models say the real thing, call a tool, then stop — throwing "
            "that away turns a good answer into silence",
        markers=("scan_intermediate_tool_content", "intermediate_seen"),
        surfaces={
            "desktop": Present(),
            "telegram": Present(),
            "discord": Present(),
            "cron": Present(),
        },
    ),

    Capability(
        key="roleplay_corrective",
        what="Notices a kin acting out a tool call instead of making one",
        why="Otherwise the kin believes it filed the note, and so does the "
            "person reading",
        # Recognises the shared implementation as well as a local one. When
        # this judgement moved into turn_steering, every surface that had it
        # suddenly read as missing — a marker has to name the capability, not
        # one particular way of reaching it.
        markers=("detect_tool_roleplay", "build_tool_roleplay_corrective_note",
                 "roleplay_corrective_note"),
        surfaces={
            "desktop": Present(),
            "telegram": Present(),
            "discord": Present(),
            "cron": Present(),
        },
    ),

    Capability(
        key="authoring_bridge",
        what="Commits a fenced write a kin produced instead of calling a tool",
        why="Small models routinely write the file out in the reply; this "
            "makes that work rather than vanish",
        markers=("authoring_bridge", "authoring_bridge_notes"),
        surfaces={
            "desktop": Present(),
            "telegram": Present(),
            "discord": Present(),
            "cron": Present(),
        },
    ),

    Capability(
        key="toolless_memory",
        what="Files a memory note for a kin whose tools can't reach memory",
        why="Otherwise a kin asked to remember something agrees to, and "
            "doesn't",
        markers=("toolless_memory", "commit_toolless", "toolless_memory_notes"),
        surfaces={
            "desktop": Present(),
            "telegram": Present(),
            "discord": Present(
                markers="authoring_bridge_notes",
                note="Reached through the shared authoring-bridge entry "
                     "point, which falls through to the toolless path when a "
                     "kin has no write tools. Named here rather than adding a "
                     "second call site: the fallthrough IS the contract, and "
                     "a surface calling both would be able to do it twice."),
            "cron": Present(),
        },
    ),

    Capability(
        key="read_nudge",
        what="Notices a kin narrating reading a file it never opened",
        why="A kin describing a file's contents from imagination is "
            "confidently wrong, and sounds exactly like a kin that read it",
        markers=("looks_like_read_gesture", "read_gesture_note"),
        surfaces={
            "desktop": Present(),
            "telegram": Present(),
            "discord": Present(),
            "cron": Present(),
        },
    ),

    Capability(
        key="tool_history_persisted",
        what="Stores tool round-trips so a kin can see what it did last turn",
        why="A kin that cannot see its own past calls repeats them, or denies "
            "making them",
        markers=("added_turns", "_pending_tool_history", "intermediate_turns"),
        surfaces={
            "desktop": Present(),
            "telegram": Present(),
            "discord": Present(markers="_persist_tool_turn"),
            "cron": Present(),
        },
    ),

    # --- what the person can do -----------------------------------------

    Capability(
        key="stop_support",
        what="Lets a reply in progress be stopped, keeping what was written",
        why="Against a slow local model the alternative is quitting the app",
        markers="should_stop",
        surfaces={
            "desktop": Present(),
            "telegram": Present(),
            "discord": Present(),
            "cron": Present(),
        },
    ),

    Capability(
        key="commands",
        what="Gives the person a way to act on the conversation itself — "
             "clear, undo, redo, check status",
        why="Without it the only available action is saying more words",
        markers=("_handle_command", "_cmd_help", "_build_menu"),
        surfaces={
            "desktop": Present(
                markers="_build_menu",
                note="Menus rather than typed commands — the same capability "
                     "in the affordance a window offers."),
            "telegram": Present(),
            "discord": Absent(
                "Nothing at all. No way to see which model is running, clear "
                "a channel, undo a bad exchange, or redo a reply."),
            "cron": NotHere(
                "Nobody is present at a scheduled wake-up to type anything."),
        },
    ),

    Capability(
        key="message_coalescing",
        what="Waits for the rest of a message split across several sends",
        why="A long paste arrives in pieces and would otherwise get a separate "
            "reply to each fragment",
        markers="_coalesce_message_parts",
        surfaces={
            "desktop": NotHere(
                "One send, when the person presses it. There is nothing to "
                "reassemble."),
            "telegram": Present(),
            "discord": Absent(
                "Discord splits long messages the same way Telegram does, and "
                "people type across two lines everywhere. Each piece gets its "
                "own reply."),
            "cron": NotHere(
                "The prompt arrives whole, from a file written in advance. "
                "There is no typing to wait for and no way for it to "
                "arrive in pieces."),
        },
    ),

    Capability(
        key="park_turn",
        what="Lets a kin take a whole turn in its park, not a single move",
        why="A kin that can look OR act, but not both, spends its only move "
            "looking",
        markers="park_keeper",
        surfaces={
            "desktop": Present(),
            "telegram": Present(),
            "discord": Absent(
                "A `> ` line now runs here (one move, same as Telegram "
                "group -- see discord_bot._route_park_command), and the tff "
                "tool remains directly callable too, but there is no "
                "play_turn LOOP: a Discord channel is guild-shaped, not "
                "DM-shaped, so an unattended multi-move turn risks the same "
                "cross-tenant voice leak route_reply's docstring warns "
                "about. One command per reply, deliberately.",
                # The capability-level default marker ("park_keeper") now
                # matches every surface, including this one -- Discord
                # imports the module for its single-move routing too. What
                # actually distinguishes "whole turn" is the LOOP call, so
                # this cell probes for that specifically rather than the
                # module import.
                markers="play_turn"),
            "cron": Present(),
        },
    ),

    # --- what can reach the person ---------------------------------------

    Capability(
        key="dictation",
        what="Lets the person SPEAK their message instead of typing it",
        why="Typing is not equally available to everyone, and a surface "
            "where the only way in is the keyboard is a surface some people "
            "cannot use",
        markers="stop_recording_and_transcribe",
        surfaces={
            "desktop": Present(
                where="frame/status_voice_mixin.py::_transcribe_worker"),
            "telegram": Absent(
                "A voice note sent to a kin here is ignored — Telegram "
                "delivers it as an OGG/OPUS file and nothing downloads or "
                "transcribes it. A phone keyboard has its own dictation, "
                "which is why this has never been noticed, but that is the "
                "phone's answer and not this app's: it does not help anyone "
                "sending a voice note, and it does not exist on a desktop "
                "Telegram client. The transcription itself is already "
                "surface-independent (stt.transcribe takes bytes), so what "
                "is missing is the download and the format conversion."),
            "discord": Absent(
                "Same gap as Telegram, same shape: a voice message arrives "
                "as an attachment and nothing transcribes it. Whichever "
                "surface gets this first should hand the other the audio "
                "fetch, since stt.transcribe is already shared."),
            "cron": NotHere(
                "A scheduled wake-up has nobody present to speak. The prompt "
                "was written in advance, by hand."),
        },
    ),

    Capability(
        key="inflight_visibility",
        what="Makes a reply in progress visible to the rest of the app",
        why="Quitting through one abandons somebody's conversation without a "
            "word",
        markers=("active_turn_label", "cron_running_kin", "_streaming"),
        surfaces={
            "desktop": Present(markers="_streaming"),
            "telegram": Present(markers="active_turn_label"),
            "discord": Present(markers="active_turn_label"),
            "cron": Present(markers="cron_running_kin"),
        },
    ),

    Capability(
        key="busy_notice",
        what="Says when a message is queued behind the app's own background "
             "work",
        why="Not knowing whether the model is free makes every message a "
            "gamble, and people stop sending",
        markers=("get_busy_label", "_own_background_on_the_model"),
        surfaces={
            "desktop": Present(),
            "telegram": Present(
                where="frame/bot_integration_mixin.py::_start_bot_for"),
            "discord": Absent(
                "The callback exists and is handed to Telegram a few lines "
                "away; Discord's constructor simply never got it. So a kin "
                "held up behind a 13-minute memory write looks, from a "
                "Discord channel, exactly like a kin ignoring you.",
                where="frame/bot_integration_mixin.py::_start_discord_bot_for"),
            "cron": NotHere(
                "Nobody is waiting on a scheduled wake-up, so there is nobody "
                "to reassure."),
        },
    ),

    # Added AFTER the audit, because the bug that started the whole audit was
    # this one and the map did not have a row for it. A setting that reaches
    # the model call but not the display is invisible in exactly the way this
    # file exists to prevent — and a matrix is only as good as the questions
    # somebody thought to ask it.
    Capability(
        key="reasoning_display",
        what="Shows the kin's reasoning when 'Show reasoning in chat' is on",
        why="The setting makes the model spend part of its reply budget "
            "thinking; a surface that then discards it charges for something "
            "it never delivers, and reads as a kin that stopped thinking "
            "rather than a setting that doesn't reach",
        markers=("reasoning_block", "💭 Reasoning"),
        surfaces={
            "desktop": Present(markers="💭 Reasoning"),
            "telegram": Present(markers="reasoning_block"),
            "discord": Present(markers="reasoning_block"),
            "cron": Absent(
                "A wake-up records the reply in the journal and posts it "
                "onward, but the reasoning behind it goes nowhere. Less "
                "pressing than the chat surfaces — nobody is reading at the "
                "time — but the setting is per-kin and does not know that, "
                "so a kin with it on still pays for thinking nobody keeps."),
        },
    ),

    Capability(
        key="recall_legibility",
        what="Shows which memories were surfaced for a reply",
        why="Otherwise a kin drawing on the wrong note is indistinguishable "
            "from a kin being odd",
        markers=("_build_recall_footer", "memories recalled"),
        surfaces={
            "desktop": Present(markers="memories recalled"),
            "telegram": Present(markers="_build_recall_footer"),
            "discord": Present(markers="_recalled"),
            "cron": NotHere(
                "Nobody is reading at the time; the journal entry is the "
                "record, and recall.log holds what was surfaced."),
        },
    ),

    # Probed against the DELIVERING code, not the receiving surface: whether a
    # wake-up can reach Discord is a fact about hearthkin_cron.py, and nothing
    # in discord_bot.py would ever show it either way.
    Capability(
        key="scheduled_wake_reaches_here",
        what="A scheduled wake-up's reply can be delivered to this surface",
        why="A kin that tends its memory at 4am should be able to say so "
            "where the person actually is",
        markers=("cron_requests", "_post_cron_reply_to_telegram"),
        surfaces={
            "desktop": Present(markers="write_request_file",
                               where="hearthkin_cron.py"),
            "telegram": Present(markers="_post_cron_reply_to_telegram",
                                where="hearthkin_cron.py"),
            "discord": Absent(
                "A scheduled wake-up cannot reach a Discord channel at all — "
                "hearthkin_cron.py has no notion of the surface. So a kin "
                "whose person lives in a Discord server tends its memory "
                "overnight and has nowhere to say so.",
                markers="discord", where="hearthkin_cron.py"),
            "cron": NotHere(
                "It is the thing doing the delivering, so there is no code "
                "anywhere that could indicate it delivering to itself, and "
                "nothing to watch for.",
                probe=False),
        },
    ),

    Capability(
        key="reach_out_delivery",
        what="A kin deciding to start a conversation can reach this surface",
        why="A proactive kin that can only reach one place is only proactive "
            "in one place",
        markers=("telegram_dm", "discord", "desktop"),
        surfaces={
            "desktop": Present(markers="desktop", where="tools/reach_out.py"),
            "telegram": Present(markers="telegram_dm",
                                where="tools/reach_out.py"),
            "discord": Absent(
                "reach_out knows the desktop and Telegram only, so a kin "
                "whose person is on Discord cannot start anything there.",
                markers="discord", where="tools/reach_out.py"),
            "cron": NotHere(
                "A wake-up is the moment a kin decides whether to reach out, "
                "not a place a message can be delivered to. Nobody is sitting "
                "in a scheduled task waiting to be spoken to.",
                markers="cron", where="tools/reach_out.py"),
        },
    ),

    # --- safety -----------------------------------------------------------

    Capability(
        key="tool_gating",
        what="Limits which tools are reachable from this surface",
        why="A kin's full toolset handed to whoever is in a channel is the "
            "whole risk of a remote surface",
        markers=("filter_tool_names", "_CRON_TOOL_DENYLIST"),
        surfaces={
            "desktop": NotHere(
                "One person, physically present, who chose the kin's tool "
                "list in the first place. The allowlist IS the gate."),
            "telegram": Present(markers="filter_tool_names"),
            "discord": Present(markers="filter_tool_names"),
            "cron": Present(markers="_CRON_TOOL_DENYLIST"),
        },
    ),

    Capability(
        key="exec_approval",
        what="Routes a shell command to a human before it runs",
        why="A command arriving over the internet must never run on somebody's "
            "machine unattended",
        markers=("wrap_exec", "_wrap_exec_executor", "_CRON_TOOL_DENYLIST"),
        surfaces={
            "desktop": Present(markers="_wrap_exec_executor"),
            "telegram": Present(markers="wrap_exec"),
            "discord": Present(markers="wrap_exec"),
            # Probed for the APPROVAL wiring, not for the denylist that makes
            # it unnecessary. If a future change hands cron an exec path, the
            # approval marker appears and this cell fails — which is exactly
            # the moment somebody should have to think about it.
            "cron": NotHere(
                "exec is removed from the tool set entirely on this surface "
                "(_CRON_TOOL_DENYLIST), so there is nothing to approve. "
                "Nobody is awake to approve it, and removing the tool is the "
                "right answer rather than a gap.",
                markers=("wrap_exec", "_wrap_exec_executor")),
        },
    ),

    Capability(
        key="webcam_approval",
        what="Routes a webcam capture to a human before it happens",
        why="It is the one tool with an effect in the room the person is "
            "sitting in",
        markers=("request_webcam_approval", "_wrap_webcam",
                 "_CRON_TOOL_DENYLIST"),
        surfaces={
            "desktop": Present(),
            "telegram": Present(),
            "discord": Present(),
            "cron": NotHere(
                "use_webcam is removed from the tool set entirely on this "
                "surface (_CRON_TOOL_DENYLIST), for the same reason as exec: "
                "a camera in somebody's room must not be reachable by a turn "
                "nobody is present for.",
                markers=("request_webcam_approval", "_wrap_webcam")),
        },
    ),

    # --- attachments -------------------------------------------------------

    Capability(
        key="image_attachments",
        what="Accepts images and shows them to a model that can see",
        why="Otherwise a picture sent to a kin is silently nothing",
        markers="model_supports_images",
        surfaces={
            "desktop": Present(),
            "telegram": Present(),
            "discord": Present(),
            "cron": NotHere(
                "A scheduled wake-up runs from a written prompt with "
                "nobody present, so there is no moment at which anyone "
                "could attach a picture to it."),
        },
    ),

    Capability(
        key="text_attachments",
        what="Reads an attached text document into the turn",
        why="Otherwise a shared file arrives with the caption and nothing else",
        # 'reading_bridge' alone is too coarse: the read-nudge imports the
        # same module, so the moment cron gained that, cron looked like it
        # accepted attachments. Probe the attachment functions themselves.
        markers=("build_attachment_context_block", "is_text_attachment",
                 "build_shared_context_block"),
        surfaces={
            "desktop": Present(markers="build_shared_context_block"),
            "telegram": Present(),
            "discord": Present(),
            "cron": NotHere(
                "Same as images: a written prompt, nobody present, no "
                "moment at which a document could be handed over."),
        },
    ),

    # --- memory ------------------------------------------------------------

    Capability(
        key="distillation_tick",
        what="Counts this surface's turns towards the next memory write",
        why="Conversation that never counts never reaches memory, so a kin "
            "used mostly here slowly stops remembering",
        markers=("on_activity", "_maybe_auto_distill", "distill"),
        surfaces={
            "desktop": Present(),
            "telegram": Present(),
            "discord": Present(),
            "cron": Present(
                markers="note_unattended_turns",
                note="Indirectly, and it has to be. The memory writer lives "
                     "in a module that imports wxPython, which this "
                     "subprocess deliberately does not, so a wake-up running "
                     "with the app closed records the turns it added and the "
                     "app folds them in on its next tick. Worth knowing why "
                     "this was invisible: distillation has two triggers, and "
                     "the percentage one reads the conversation off disk so "
                     "it always saw these turns. Only the 'every N messages' "
                     "one counts in memory. So whether a kin kept "
                     "remembering what it did overnight depended on which of "
                     "the two settings its person happened to choose."),
        },
    ),
]
