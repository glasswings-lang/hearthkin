# SPDX-License-Identifier: CC0-1.0

"""The notes a kin gets back when it GESTURED at doing something instead of
doing it — shared by every surface, so no surface can quietly go without.

Three related failures, all of the same family: a kin writes *files this away
in memory*, or *reads through the notes*, or writes a file out in a fenced
block, and nothing happens. From the kin's side that is indistinguishable
from success, so it carries on believing it saved the thing, and reads its own
history back tomorrow to find a turn in which it did. From the person's side
it looks like a kin that lies, or forgets, or makes things up.

WHY THIS FILE EXISTS AT ALL. Each of these was written once for the desktop,
again for Telegram, and — with the cron work — a third time. Three copies is
how the rules drift apart: a fix lands on whichever surface provoked it, and
the others keep the old behaviour with nobody able to see that they have. The
surface matrix now fails when a capability is missing somewhere, and the
honest way to satisfy it is one implementation rather than a fourth copy.

WHAT IS DELIBERATELY NOT HERE. Anything a surface genuinely owns: how it logs,
where a note is delivered, whether it has an operator to show a confirmation
to. Those differ for real reasons and get passed in or handled by the caller.
What is shared is the JUDGEMENT — did the kin gesture, and what should it be
told — because that judgement has no business varying by surface.

Every function is fail-soft and returns "nothing to say" on any error. A fault
in the steering must never cost a kin its reply; the reply is the thing that
matters and this is only the footnote.
"""

import datetime
import os

from kin_persistence import LOGS_DIR, load_app_prompt


def reasoning_block(thinking, *, cfg=None, cap=None, hard_cap=3500):
    """The kin's reasoning, formatted for a chat surface — or "" when there
    is none to show.

    Exists because "Show reasoning in chat" only ever reached the MODEL CALL
    on the remote surfaces. Ticking it made the model spend part of its reply
    budget producing reasoning that was then discarded without a word, which
    is worse than not offering the setting: on the desktop the box does what
    it says, so the same box doing nothing elsewhere reads as the kin having
    stopped thinking rather than as a setting that does not reach.

    Returned as a SEPARATE block for the caller to send on its own, never
    folded into the reply. On Telegram the reply is a streamed message that
    may only grow, and reasoning arrives before the answer it explains — so
    appending it to that message would either reorder the two or rewrite
    something already read aloud.

    The length comes from the kin's own `think_max_chars`, which sits beside
    the checkbox in the same dialog. Reaching for a hardcoded number instead
    is how a setting quietly stops applying — the first version of this did
    exactly that, and would have shipped a surface where the box that limits
    reasoning worked in the main window and nowhere else. `hard_cap` is only
    the transport's own ceiling (a Telegram or Discord message length), never
    a substitute for what the person asked for."""
    text = (thinking or "").strip()
    if not text:
        return ""
    if cap is None:
        try:
            cap = int((cfg or {}).get("think_max_chars", 1200) or 0)
        except (TypeError, ValueError):
            cap = 1200
    # 0 means "no limit" in the same dialog, so it must not read as "show
    # nothing" here.
    limit = min(x for x in (cap or hard_cap, hard_cap) if x)
    if limit and len(text) > limit:
        text = text[:limit] + "\n\n[truncated]"
    return f"💭 Reasoning:\n\n{text}"


def _real_tools_fired(added_turns):
    """True when the turn contained genuine tool calls.

    Every check here is skipped in that case, and it is the single most
    important gate in the file: a kin that narrates its work AND does it is
    not gesturing, it is describing. Correcting that teaches it to stop
    saying what it is doing, which is the opposite of what anyone wants."""
    for turn in (added_turns or []):
        if isinstance(turn, dict) and turn.get("role") == "assistant" \
                and turn.get("tool_calls"):
            return True
    return False


def _tool_names_called(added_turns):
    """Names of the tools that actually fired this turn."""
    names = []
    for turn in (added_turns or []):
        if not isinstance(turn, dict):
            continue
        for call in (turn.get("tool_calls") or []):
            name = (call.get("function") or {}).get("name") or ""
            if name:
                names.append(name)
    return names


def log_gesture(kin, surface, model, variant, tool_named, available_tools,
                reply):
    """One always-on line recording a gesture, in the shape every surface
    already writes. Same file as the empty-reply diagnostics on purpose:
    these are the two ways a turn can produce nothing real, and counting them
    together is what makes a pattern visible across surfaces instead of
    becoming per-surface folklore."""
    try:
        path = LOGS_DIR / "empty_replies.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(
                f"{stamp} [{kin}] surface={surface} model={model} "
                f"variant=tool-roleplay:{variant} tool_named={tool_named!r} "
                f"available_tools={list(available_tools or [])!r} "
                f"content_tail={(reply or '').strip()[-300:]!r}\n"
            )
    except Exception:
        pass


def roleplay_corrective_note(kin, reply, tools, added_turns, *,
                             surface="", model="", log=True):
    """A note telling the kin plainly that a narrated tool call did nothing,
    or "" when there is nothing to say.

    Returns "" — meaning stay quiet — when the kin has no such tool to gesture
    at, when real tool calls fired, when the detector misses, and when the
    variant is narrative-intent. That last one is the detector's own judgement
    and it is respected here rather than second-guessed: an ambiguous shape
    auto-corrected is a kin being told off for describing something it never
    claimed to have done."""
    try:
        if not reply or not tools:
            return ""
        if _real_tools_fired(added_turns):
            return ""
        from chat_helpers import (
            detect_tool_roleplay, build_tool_roleplay_corrective_note)
        variant, tool_named = detect_tool_roleplay(reply, list(tools))
        if not variant:
            return ""
        if log:
            log_gesture(kin, surface, model, variant, tool_named, tools, reply)
        return build_tool_roleplay_corrective_note(variant, tool_named, kin)
    except Exception:
        return ""


def read_gesture_note(kin, reply, tools, added_turns, *,
                      shared_this_turn=False):
    """A note for a kin that narrated reading CONTENT it never opened, or "".

    Suppressed when a read actually fired, and when a file was attached or
    shared this turn — in both cases the kin really does have the text in
    front of it and saying otherwise would be wrong, not merely noisy."""
    try:
        if not reply or shared_this_turn:
            return ""
        if "read_file" not in set(tools or []):
            return ""
        fired = set(_tool_names_called(added_turns))
        if fired & {"read_file", "memory_search", "read_staging"}:
            return ""
        import reading_bridge
        reach = reading_bridge.looks_like_read_gesture(reply)
        if not reach:
            return ""
        return load_app_prompt("read_gesture_nudge", kin).replace(
            "{reach}", str(reach[:60]))
    except Exception:
        return ""


def unsent_reach_note(kin, reply, tools, added_turns, *, min_chars=120):
    """A note for a kin that wrote something on a heartbeat and never sent it,
    or "" when there is nothing to say.

    Every other surface has a reader. A heartbeat does not: the kin's reply is
    read by nobody and deleted, and `reach_out` is the only way anything gets
    out. So the ordinary gesture detectors do not apply here — the kin has not
    NARRATED a tool call, which is what they look for. It has simply written
    the message, in the one place where writing it is the same as discarding
    it, and from the inside those are indistinguishable.

    This is deliberately NOT a classifier. Telling "a message meant for her"
    apart from "thinking out loud about whether to speak" is exactly the kind
    of judgement this project has already got wrong by keyword (the park's
    verb filter, where every destructive command was a word the game knew).
    The kin is the only one who can answer it, so the kin is asked, once, and
    a second refusal is taken as a real refusal.

    Returns "" when there is no reach_out to call, when real tool calls fired,
    and when the reply is too short to be a message anyone was owed.
    `min_chars` is the one crude part and it is crude on purpose: it exists so
    a two-word shrug does not cost a model call, not to judge content."""
    try:
        if "reach_out" not in set(tools or []):
            return ""
        if _real_tools_fired(added_turns):
            return ""
        text = (reply or "").strip()
        if len(text) < max(0, int(min_chars)):
            return ""
        return load_app_prompt("heartbeat_unsent_nudge", kin)
    except Exception:
        return ""


def log_unsent_reach(kin, model, reply, *, asked, delivered):
    """Record a heartbeat whose words did not reach anyone.

    The kin's TEXT is written only when nobody ever asked it — a message that
    vanished without its author getting a say. When the kin was asked and
    still chose not to send, only the fact is recorded: that is a decision,
    the moment is genuinely its own, and "silence leaves no trace" is a
    promise worth keeping where it is real rather than where it was a
    fiction.

    Always-on, and its own file. Heartbeats are unattended by definition, so
    a loss recorded nowhere is a loss nobody can find out about — which is
    how three days of a kin's messages went missing without a single line
    anywhere saying so."""
    try:
        if delivered:
            return
        path = LOGS_DIR / "heartbeat_unsent.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        text = (reply or "").strip()
        with open(path, "a", encoding="utf-8") as fh:
            if asked:
                fh.write(f"{stamp} [{kin}] model={model} asked=yes "
                         f"outcome=declined chars={len(text)}" + chr(10))
            else:
                fh.write(f"{stamp} [{kin}] model={model} asked=no "
                         f"outcome=lost chars={len(text)} text={text!r}" + chr(10))
    except Exception:
        pass


def authoring_bridge_notes(kin, reply, tools, shown_scopes=()):
    """Commit file content a kin wrote out in its reply instead of calling a
    write tool. Returns ``(kin_note, human_confirm)``, either may be None.

    kin_note carries full paths and belongs in the kin's own history, so its
    next read knows what actually landed. human_confirm carries basenames only
    and belongs wherever a person is reading — a chat message, a journal
    entry. They are separate because they are for different readers, and
    collapsing them would either bury a person in paths or leave the kin
    guessing which file it was.

    A kin with no write tools at all falls through to the toolless-memory
    path instead: it has no way to file anything, so the gesture is the only
    channel it has and refusing it would simply lose the note."""
    try:
        if not reply:
            return None, None
        if not ({"write_file", "edit_file"} & set(tools or [])):
            return toolless_memory_notes(kin, reply, tools, shown_scopes)
        import authoring_bridge
        writes = authoring_bridge.extract_authoring_writes(reply)
        if not writes:
            return None, None
        results = authoring_bridge.commit_authoring_writes(kin, writes)
        saved = [(p, n) for (p, ok, n) in results if ok]
        failed = [(p, n) for (p, ok, n) in results if not ok]
        note_bits, human_bits = [], []
        if saved:
            note_bits.append("saved from your reply: " + ", ".join(
                f"{p} ({n} bytes)" for p, n in saved))
            human_bits.append("saved " + ", ".join(
                f"{os.path.basename(str(p))} ({n} bytes)" for p, n in saved))
        for path, err in failed:
            note_bits.append(f"could NOT save {path!r} — {err}")
        if failed:
            human_bits.append(
                f"{len(failed)} file(s) couldn't be saved (see logs)")
        note = (load_app_prompt("authoring_bridge_result", kin)
                .replace("{results}", "; ".join(note_bits))
                if note_bits else None)
        confirm = ("[authoring bridge] " + "; ".join(human_bits)
                   if human_bits else None)
        return note, confirm
    except Exception:
        return None, None


def toolless_memory_notes(kin, reply, tools, shown_scopes=()):
    """The same service for a kin with no write tools at all: file what it
    said it wanted remembered, and tell it what happened.

    Returns ``(kin_note, human_confirm)``. A kin here cannot save anything by
    itself, so a gesture is not a mistake it should be corrected out of — it
    is the only way it has of asking."""
    try:
        if not reply:
            return None, None
        import toolless_memory
        results, archived = toolless_memory.commit(
            kin, reply, tools, shown_scopes=list(shown_scopes or []))
        note = toolless_memory.receipt(kin, results, archived)
        if not note:
            # Nothing landed. A silent miss here is the worst case of all:
            # the kin thanks itself for a save that never happened, and the
            # only trace is a note nobody wrote.
            nudge = toolless_memory.missed_write_nudge(kin, reply, results)
            if not nudge:
                return None, None
            return nudge, "[no-tools memory] nothing was saved this turn"
        saved = [(p, n) for (p, ok, n) in results if ok]
        failed = [p for (p, ok, _n) in results if not ok]
        human_bits = []
        if saved:
            human_bits.append("saved " + ", ".join(
                f"{os.path.basename(str(p))} ({n} bytes)" for p, n in saved))
        if failed:
            human_bits.append(
                f"{len(failed)} file(s) couldn't be saved (see logs)")
        if archived:
            human_bits.append(f"tended {len(archived)} staging scope(s)")
        return note, ("[no-tools memory] " + "; ".join(human_bits)
                      if human_bits else None)
    except Exception:
        return None, None
