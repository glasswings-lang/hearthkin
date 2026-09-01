"""Shared module-level namespace for the Hearthkin frame.

Holds every import, constant, and module-level helper the frame and its
concern mixins (frame/*.py) reference. Split out of hearthkin.pyw during the
2026-07 modularisation so the mixins have a single, import-once namespace
hub instead of the monolith's 11k-line module scope. Behavior-identical:
this is the exact module-level code that used to sit at the top (and, for
the foreground helpers, the bottom) of hearthkin.pyw, moved verbatim.
"""

import wx
import wx.adv
import wx.lib.scrolledpanel as scrolled
import json
import os
import re
import logging
import datetime
import threading
import ctypes
import sys
import time
import queue
import urllib.request
import urllib.error
from pathlib import Path

try:
    import ollama
except ImportError:
    ollama = None

# Shared backend that dispatches Ollama vs. OpenRouter on model name prefix.
# Must be copied alongside hearthkin.pyw when this script is moved.
import llm_backend
import tools as kin_tools
from tools._exec_denylist import match_denylist
from tools._exec_state import is_in_allowlist, add_to_allowlist
import cron_helpers
from telegram_bot import (
    TelegramBot,
    load_telegram_history,
    telegram_api_call,
)
from discord_bot import DiscordBot
from audio import (
    nvda_speak,
    nvda_status,
    play_alert,
    play_chime,
)
from model_utils import (
    clear_models_cache,
    find_annotated,
    get_models,
    strip_model_annotation,
    _model_context_length,
    _model_supports_thinking,
    _model_supports_tools,
    _tool_cap_cache,
)
from chat_helpers import (
    clean_kin_reply,
    estimate_tokens,
    estimate_message_tokens,
    extract_inline_thinking,
    format_ts_prefix,
    speaker_attribution_prefix,
    strip_leading_speaker_tag,
    strip_self_tag,
    strip_self_timestamp,
    strip_trailing_other_speakers,
    _extract_chunk_content,
    _extract_chunk_thinking,
    _last_sentence_end,
)
import tray
import stt
import voice as voice_module
import windows_startup
# Dialog classes import lazily from this module for the three model
# helpers above (strip_model_annotation, get_models, find_annotated).
# Importing dialogs last ensures those names are in this module's
# namespace by the time the dialog methods run.
from dialogs import (
    AgentNameDialog,
    ConfirmCloseDialog,
    EditKinDialog,
    ExecApprovalDialog,
    HealthCheckDialog,
    DictationSettingsDialog,
    SoundCuesDialog,
    ParkPlayDialog,
    RoomEditDialog,
    SearchDialog,
    UsageHistoryDialog,
    WebcamApprovalDialog,
    _IntField,
    rebuild_listbox,
)
from kin_persistence import (
    AGENTS_DIR,
    CONFIG_FILE,
    CONVOS_DIR,
    live_distill_bookmark,
    migrate_dictation_config,
    DEFAULT_CONFIG,
    DEFAULT_DISTILL_PROMPT,
    DEFAULT_DISTILL_REFLECTION,
    DEFAULT_ROOM_CONFIG,
    LOGS_DIR,
    MEMORY_CONSOLIDATE_THRESHOLD_CHARS,
    ROOMS_DIR,
    apply_memory_log_index,
    strip_memory_logs_section,
    _clean_chat_message,
    _replace_name_in_kin_files,
    _scan_name_occurrences_in_kin,
    agent_dir,
    append_failure_log,
    append_model_history,
    atomic_write_json,
    atomic_write_text,
    build_system_prompt,
    clone_agent,
    create_agent,
    create_room,
    delete_agent,
    delete_room,
    list_agents,
    list_rooms,
    load_agent_config,
    load_app_prompt,
    resolve_kin_ollama_host,
    migrate_global_ollama_host,
    append_agent_conversation_turn,
    load_agent_conversation,
    load_distill_prompt,
    load_json,
    load_kin_tools,
    load_memory,
    load_memory_for_prompt,
    load_room_config,
    load_room_conversation,
    load_soul,
    now_iso,
    rename_kin_in_rooms,
    save_agent_config,
    save_agent_conversation,
    save_agent_conversation_preserving_externals,
    save_memory,
    append_staging,
    seed_kin_manual,
    seed_bundled_game,
    save_room_config,
    save_room_conversation,
    think_effort_of,
    validate_kin_name,
)


from app_version import __version__   # built-pipeline-stamped; see app_version.py
APP_NAME = "Hearthkin"


# Marker the cron path inserts at the head of every wake-up prompt
# (see cron_helpers.frame_wake_up_prompt). Failure-notify paths check
# user_text against this so cron wake-ups can be distinguished from
# operator-typed turns — letting the watchdog / error handlers push
# a Telegram one-liner only when the failed turn was scheduled (and
# therefore the operator wasn't at the keyboard to see the error).
_CRON_USER_TEXT_MARKER = "[hearthkin: scheduled wake-up"

# Single fallback for a missing per-kin num_ctx. The codebase used to
# mix 2048 and 8192 fallbacks across read sites, so a kin with a
# missing/corrupt num_ctx silently behaved differently depending on
# which code path read it first (audit L-B35). 8192 matches the
# documented default for new kin (DEFAULT_AGENT_CONFIG).
DEFAULT_NUM_CTX = 8192


def _num_ctx_of(cfg, default=DEFAULT_NUM_CTX):
    """Tolerant per-kin num_ctx read. A corrupt config value (string,
    None, etc.) falls back to DEFAULT_NUM_CTX instead of raising
    ValueError in the middle of a send / status repaint (audit L-B34)."""
    try:
        val = int((cfg or {}).get("num_ctx", default) or default)
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


def _is_cron_user_text(user_text):
    """Was this user_text a cron wake-up rather than a typed turn?
    Match by the framed-prompt prefix so the active-kin path can tell
    cron-injected sends apart from operator-typed sends without
    threading a flag through _send_message."""
    if not user_text or not isinstance(user_text, str):
        return False
    return user_text.lstrip().startswith(_CRON_USER_TEXT_MARKER)



def _progress_collector(on_progress):
    """Wrap an `on_progress(chars_so_far)` callback as a streaming
    on_content handler, or return None when nobody is listening.

    Counts characters rather than chunks because chunk size is a provider
    detail — Ollama sends a token at a time, OpenRouter sends whatever the
    upstream provider felt like — and a cue paced off chunks would run at
    a completely different speed on the two. Characters are the same unit
    everywhere.

    Never raises into the stream: this exists to make a long call
    audible, and a broken cue must not be able to fail the call it was
    reporting on.
    """
    if on_progress is None:
        return None
    written = [0]

    def _delta(text):
        written[0] += len(text or "")
        try:
            on_progress(written[0])
        except Exception:
            pass

    return _delta


def consolidate_memory_blocking(memory_text, model, options=None, kin_name=None,
                                on_progress=None):
    """Tighten an already-distilled memory file. Returns the consolidated text.

    `on_progress(chars_written_so_far)` is reported as the rewrite streams
    back, same as distill_memory_blocking — a consolidation holds the same
    slot, takes the same tens of minutes, and is just as silent without it.
    """
    if ollama is None:
        raise RuntimeError("ollama not installed")
    if not (memory_text or "").strip():
        return memory_text or ""
    consolidate_word_cap = MEMORY_CONSOLIDATE_THRESHOLD_CHARS // 9
    # Editable prompt (~/.hearthkin/prompts/consolidate.md); default is
    # DEFAULT_CONSOLIDATE_PROMPT, seeded on first run.
    from kin_persistence import load_app_prompt
    sys_prompt = (
        load_app_prompt("consolidate", kin_name)
        .replace("{word_cap}", str(consolidate_word_cap))
    )
    user_prompt = f"Memory to consolidate:\n\n{memory_text.strip()}\n\nConsolidated memory:"
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]
    opts = dict(options or {})
    opts.setdefault("temperature", 0.2)
    opts.setdefault("num_predict", 4000)  # consolidated memory.md can run ~2000 words
    result = llm_backend.chat_collect(
        model, messages, options=opts,
        on_content=_progress_collector(on_progress),
        kin_name=kin_name, surface="consolidate",
        ollama_host=resolve_kin_ollama_host(
            (load_agent_config(kin_name) or {}).get("ollama_host_name", "")),
    )
    return apply_memory_log_index((result.content or "").strip(), kin_name)

# Incremental distillation re-reads this many already-distilled turns on
# each run, on top of the genuinely-new ones. It makes the per-scope
# bookmark safe by construction: a stale or slightly-off bookmark causes
# a few already-seen turns to be re-summarised (cheap, harmless) rather
# than any turn being skipped. Comfortably above the churn a single
# regenerate can produce.
_DISTILL_REREAD_OVERLAP = 12

# Flat reserve subtracted from the summarizer's context window when capping
# the per-run distillation slice: covers the response cap (num_predict ~6000)
# + distill prompt scaffolding + margin. The two VARIABLE pieces of fixed
# input — the kin's soul (loaded so the notes come back in-voice) and the
# existing memory.md shown as context — are measured per-kin in _distill_bite
# and reserved on TOP of this. (A flat all-in reserve under-counted once the
# soul was added: a big-souled kin overshot the window by roughly a soul's
# worth.) On a small-window summarizer (Gemma 8k etc.) the bite shrinks
# accordingly — a legacy huge undistilled tail catches up across multiple
# trigger fires of bounded size, rather than one impossible attempt that
# overruns the summarizer's window.
_DISTILL_RESERVE_TOKENS = 8000

# A redistill-from-start walk fires its next chunk on a short delay. If
# the distillation slot is busy when that moment arrives — an ordinary
# auto-distill on one of the kin's OTHER surfaces slipped into the gap,
# or a consolidation is running — the chunk used to hit the "already
# distilling" guard and return silently, which ended the walk for good:
# the flag stayed set, no further chunk was ever scheduled, and the
# Memory tab then refused to start a new walk because one was
# "already running". Waiting the collision out instead costs a few
# seconds and loses nothing.
_WALK_RETRY_SECS = 3.0
# Give up after roughly two minutes of the slot never coming free —
# long enough to outlast any healthy distillation, short enough that a
# genuinely wedged app doesn't retry until the heat death. On give-up
# the walk PAUSES (persisted state kept, said out loud) rather than
# dying, so Resume picks it back up.
_WALK_RETRY_MAX = 40

# How long a distillation worker may be gone-but-silent before its slot is
# force-released. This is a race window, not a work budget: the worker posts
# its wx.CallAfter and then the thread ends, so for a moment "thread not
# alive" doesn't mean "wedged". A queued CallAfter is processed in
# milliseconds; 30 seconds is absurdly generous and still bounded.
_DISTILL_CALLBACK_GRACE_SECS = 30.0
# Fallback watchdog for slot state with no worker thread recorded against it.
# Was 5 minutes and applied to EVERYTHING, which libelled any distillation
# slower than that — a local model reading a 10k-token bite can spend longer
# than that in prefill alone — clearing the slot mid-run and letting a second
# distillation start on the same kin. Liveness is now read from the thread
# (see _is_distill_in_flight); this only backstops state that has no thread,
# and is long enough not to accuse a slow machine.
_DISTILL_WATCHDOG_SECS = 3600.0

# Minimum gap between AUTO-fired consolidations on the same kin (the
# manual button always bypasses). Once memory.md crosses
# MEMORY_CONSOLIDATE_THRESHOLD_CHARS the auto-after-distill trigger
# would otherwise re-fire on every subsequent distillation as long as
# the file stayed near the line — which on a busy Telegram day can
# mean dozens of consolidations per hour, each a billable memory_model
# call. 30 minutes is long enough to absorb a busy burst into a
# single rewrite; short enough that an actually-growing memory still
# gets tightened reasonably promptly. Pairs with the "tend nightly"
# ritual framing in docs/design/memory-architecture-and-ritual-
# framing.md — proactive logging is the structural fix, this is the
# safety net.
_AUTO_CONSOLIDATE_COOLDOWN_SECS = 30 * 60


def distill_memory_blocking(kin_name, conversation, existing_memory, model,
                            sys_prompt_template=None, options=None,
                            on_progress=None, think_effort=None):
    """Run a single inference to update memory.md. Returns the new memory
    text, or raises on error.

    `on_progress(chars_written_so_far)` is called as the summary streams
    back. It is the only handle anything has on how a distillation is
    GOING rather than merely that it is running: these calls take tens of
    minutes, and from outside they look identical whether the model is
    still reading, writing steadily, or wedged. The frame turns it into a
    rising tone (see StatusVoiceMixin._tick_distilling_sound). Exceptions
    from it are swallowed — a cue that misfires must never cost a kin its
    memory. Blocking-shaped either way; the stream is collected here and
    the caller still gets one finished result.

    Runs AS the kin: their soul is loaded into the call so the notes come back
    in their own first-person voice, not an out-of-character summary. (The old
    third-person summarizer produced clinical "taxidermy" that eroded the kin
    over nightly reads — see DEFAULT_DISTILL_PROMPT_VERSION 3.) The per-run
    bite-sizing (_distill_bite) measures this soul and the existing memory and
    reserves window room for both, so the conversation slice shrinks to fit
    them.

    `think_effort` ("off"/"low"/"medium"/"high", or None) is the caller's
    resolved per-kin setting — same field that governs the kin's own
    conversational replies (kin_persistence.think_effort_of). It is honoured
    as given: no per-model-family guessing here. An earlier version of this
    forced thinking off for any model whose name contained "gemma", reasoning
    from a single live incident (a kin capped at num_predict=400, Ollama
    done_reason "length", content "" — the entire budget spent deliberating
    with nothing spoken). That incident was real, but the fix generalised
    from one small-budget observation to a permanent blacklist by brand name,
    at a call site that runs at num_predict=6000 — a different regime the
    original incident says nothing about. It was also the wrong axis: this
    same failure — content empty, thinking non-empty, budget exhausted in
    silence — is independently documented in run_tool_loop's retry-once
    comment against qwen36-opus, a different family entirely. Guessing which
    families can reason and excluding them by name is exactly what that
    comment already warns against ("the alternative is guessing which models
    can reason, and being wrong about that silently reopens this" —
    tests/test_ollama_think_off.py).

    So this function detects the failure instead of predicting it: if a run
    comes back with empty content and non-empty thinking, that's the
    signature, on any model, at any budget — and it retries ONCE with
    thinking forced off, same guard shape as run_tool_loop (one retry only,
    no loop). A kin whose ordinary replies benefit from thinking gets to use
    it here too; if this specific model chokes on this specific prompt's
    size, the retry recovers the run instead of a blanket rule paying the
    cost on every run whether or not it would have failed. None resolves to
    "off", matching the old hardcoded behaviour for any caller that doesn't
    pass it explicitly (e.g. the test suite).

    `sys_prompt_template` may include {kin_name} which is substituted; falls
    back to DEFAULT_DISTILL_PROMPT if None.
    """
    if ollama is None:
        raise RuntimeError("ollama not installed")

    # Format the conversation as plain text — easier for small models than chat-format
    #
    # `speaker`/`sender_attribution` on a stored turn is provenance (which
    # historical account or which other kin actually sent it), not a signal
    # about role. Two ways that used to get conflated below, both confirmed
    # against a kin with a heavily-imported multi-account history: every
    # role=="user" turn was flattened to the literal label "User:" even when
    # it carried a real name (any human in a group import, any other kin's
    # turn reaching this kin in a room) -- so a whole cast of named speakers
    # read back as one generic "user" in the summarizer's eyes, and gemma-class
    # models pattern-matched that into writing "the user" as if it were
    # someone's actual name. And role=="assistant" turns were rendered under
    # `speaker` whenever it differed from `kin_name` -- correct for another
    # kin's turn in a room (a real different name), wrong for a kin whose own
    # imported history carries several of ITS OWN past handles (a renamed
    # Skype/Telegram account, a birth name later rejected): those turns are
    # this kin's own voice, not a third party, no matter what account name is
    # stamped on them. `speaker != kin_name` can't tell the two cases apart;
    # checking against the actual roster of OTHER registered kin can.
    known_other_kins = set(list_agents()) - {kin_name}
    convo_lines = []
    for m in conversation or []:
        role = m.get("role", "?")
        content = m.get("content", "")
        speaker = m.get("sender_attribution") or m.get("speaker", "")
        # Surface tool round-trips as compact action annotations so the
        # summarizer knows the kin did something concrete, without
        # mis-attributing the tool result text as the kin's own speech.
        if role == "tool":
            tcid = m.get("tool_call_id", "")
            tool_text = (content or "").strip().splitlines()[0:1]
            preview = tool_text[0] if tool_text else ""
            if len(preview) > 120:
                preview = preview[:120] + "..."
            convo_lines.append(f"[Tool result for {tcid}]: {preview}")
            continue
        # Tool-call turns: since the v0.2.29 null-content fix, these
        # carry content="" (never None), so the old `not isinstance(
        # content, str)` test never matched and tool-call turns reached
        # the summarizer as blank "Kin:" lines (audit L-B16). Detect by
        # tool_calls presence + blank content instead.
        if role == "assistant" and m.get("tool_calls") and not (content or "").strip():
            tool_calls = m.get("tool_calls") or []
            names = ", ".join(((tc.get("function") or {}).get("name") or "?") for tc in tool_calls)
            convo_lines.append(f"[{kin_name} called tools: {names}]")
            continue
        if role == "user":
            convo_lines.append(f"{speaker or 'User'}: {content}")
        elif speaker and speaker in known_other_kins:
            convo_lines.append(f"{speaker}: {content}")
        else:
            convo_lines.append(f"{kin_name}: {content}")
    convo_text = "\n\n".join(convo_lines) if convo_lines else "(no conversation yet)"

    existing = (existing_memory or "").strip() or "(no prior memory)"

    template = sys_prompt_template or DEFAULT_DISTILL_PROMPT
    # Plain replace rather than str.format — robust against stray braces
    # in a user-supplied custom prompt. {word_cap} keeps memory.md under
    # the consolidation threshold. The '## Memory logs' section is added
    # by apply_memory_log_index after the model returns, NOT by the model
    # (a smaller summarizer can't attach pointers reliably — see that
    # function's docstring).
    distill_word_cap = MEMORY_CONSOLIDATE_THRESHOLD_CHARS // 6
    sys_prompt = (
        template
        .replace("{kin_name}", kin_name)
        .replace("{word_cap}", str(distill_word_cap))
    )

    # Load the kin's soul so this runs AS them, in their own voice, rather than
    # an out-of-character summarizer. "You are {kin_name}" alone is a name the
    # model paints over with its house style; the soul supplies the actual
    # register to anchor to. Prepended, matching the proven layout (soul, then
    # the reflection task). Empty/missing soul -> just the task prompt.
    soul = (load_soul(kin_name) or "").strip()
    if soul:
        sys_prompt = soul + "\n\n---\n\n" + sys_prompt

    # The user turn is an editable app prompt (slug "distill_reflection"), so an
    # operator can retune the framing in Notepad without a code change — the
    # per-kin -> install-wide -> built-in cascade of load_app_prompt. It's
    # time-agnostic on purpose: distillation can fire mid-conversation (the
    # every-N trigger), not only at a quiet end of day, so it must not assert a
    # lull; the reflective end-of-day ritual is *tending*, not this quick
    # jotting. Guard: if an operator edit dropped a data placeholder, fall back
    # to the shipped default rather than hand the kin a prompt with no memory or
    # no conversation in it (load_app_prompt only falls back on an empty file,
    # not a malformed one).
    reflection = load_app_prompt("distill_reflection", kin_name=kin_name)
    if "{existing_memory}" not in reflection or "{conversation}" not in reflection:
        reflection = DEFAULT_DISTILL_REFLECTION
    user_prompt = (
        reflection
        .replace("{existing_memory}", existing)
        .replace("{conversation}", convo_text)
    )

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]

    opts = dict(options or {})
    # 0.6, not the old 0.3: the register comes back richer with a little room
    # (proven in-voice at 0.8; 0.3 tends to clip it flat). Faithfulness is held
    # by the task framing, not by a cold temperature.
    opts.setdefault("temperature", 0.6)
    opts.setdefault("num_predict", 6000)  # memory.md is a multi-thousand-word index now

    resolved_host = resolve_kin_ollama_host(
        (load_agent_config(kin_name) or {}).get("ollama_host_name", ""))

    def _run(effort):
        return llm_backend.chat_collect(
            # Streamed and collected, rather than chat(stream=False): the
            # content, usage and error behaviour are identical, but a stream
            # has moments in it, and those moments are what on_progress
            # reports. A blocking call has nothing to say until it is over.
            model, messages, options=opts, think_effort=effort,
            on_content=_progress_collector(on_progress),
            kin_name=kin_name, surface="distill",
            ollama_host=resolved_host,
        )

    result = _run(think_effort or "off")
    # Thinking-model-went-silent recovery, same shape as run_tool_loop's
    # (see llm_backend.py): content empty but thinking non-empty means the
    # model spent this run's whole budget deliberating and never actually
    # spoke — a real failure, not "nothing was worth keeping" (that case has
    # no thinking either, it just has nothing). Detected live rather than
    # predicted by model name, so it catches whichever model actually chokes
    # on whichever prompt, at whatever budget is really in play, instead of
    # a static guess. One retry only, thinking forced off — no loop, and if
    # the retry ALSO comes back empty that's just distillation's ordinary
    # "nothing new" case handled below.
    if not (result.content or "").strip() and (result.thinking or "").strip():
        result = _run("off")
    usage = result.usage if isinstance(result.usage, dict) else {}
    # Append, don't rewrite. The model returned only NEW entries; splice
    # them onto the existing memory body (logs section stripped first so
    # apply_memory_log_index re-attaches a fresh one at the end). An
    # empty result means the model judged nothing new worth recording —
    # leave the existing memory as-is. Whole-file rewriting is now
    # consolidation's job, not distillation's: distillation can only add,
    # so it can never drop an existing entry.
    new_entries = (result.content or "").strip()
    existing_body = strip_memory_logs_section(existing_memory)
    if new_entries:
        combined = (existing_body + "\n\n" + new_entries).strip()
    else:
        combined = existing_body
    return {
        # Under the 2026-06-01 staging architecture, the caller writes
        # `new_entries` to the per-scope staging file instead of saving
        # `memory` to memory.md. `memory` is still returned for
        # backward compatibility (and so manual flows could in
        # principle still use it), but the normal _on_distill_done
        # path now ignores it. See
        # docs/design/memory-architecture-and-ritual-framing.md.
        "memory": apply_memory_log_index(combined, kin_name),
        "new_entries": new_entries,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "model": model,
    }















# --- Main window -------------------------------------------------- #



# ── foreground / single-instance helpers (were below the class) ──────


# --- Entry point -------------------------------------------------- #

def _force_foreground(hwnd):
    """Reliably bring a window to the foreground on Windows, working
    around the foreground-lock protection that makes a bare
    SetForegroundWindow silently fail (the window flashes in the taskbar
    instead of focusing).

    A naive SetForegroundWindow only succeeds when the calling process
    already holds foreground rights (recent user input). When it doesn't,
    Windows refuses the switch. The AttachThreadInput dance works around
    it: temporarily attach our thread's input queue to the CURRENT
    foreground thread's, so Windows treats our SetForegroundWindow as
    coming from the app that already owns the foreground, then detach. A
    minimize->restore jolt is the fallback for a still-stubborn window.

    Best-effort and fully guarded — it never raises into the caller. It
    does NOT rescue a fully-wedged shell (that needs an Explorer restart
    or reboot — no app-side call can); it fixes the common 'window didn't
    come to the front' misses, which are exactly what a screen-reader user
    can't recover from on their own. No-op off Windows.

    argtypes are declared so a 64-bit HWND isn't truncated — ctypes
    defaults untyped integer arguments to 32-bit c_int, which silently
    corrupts large handles on Win64."""
    if sys.platform != "win32" or not hwnd:
        return False
    SW_RESTORE = 9
    SW_MINIMIZE = 6
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
        user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.IsIconic.argtypes = [ctypes.c_void_p]
        user32.AttachThreadInput.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int]
        kernel32.GetCurrentThreadId.restype = ctypes.c_ulong
    except Exception:
        return False

    hwnd_int = int(hwnd)
    hwnd_p = ctypes.c_void_p(hwnd_int)
    try:
        if user32.IsIconic(hwnd_p):
            user32.ShowWindow(hwnd_p, SW_RESTORE)
    except Exception:
        pass

    fg_tid = cur_tid = 0
    attached = False
    try:
        fg = user32.GetForegroundWindow()
        if fg:
            fg_tid = user32.GetWindowThreadProcessId(ctypes.c_void_p(fg), None)
        cur_tid = kernel32.GetCurrentThreadId()
        if fg_tid and cur_tid and fg_tid != cur_tid:
            attached = bool(user32.AttachThreadInput(cur_tid, fg_tid, 1))
        user32.BringWindowToTop(hwnd_p)
        user32.SetForegroundWindow(hwnd_p)
    except Exception:
        pass
    finally:
        if attached:
            try:
                user32.AttachThreadInput(cur_tid, fg_tid, 0)
            except Exception:
                pass

    # Fallback: if it still isn't the foreground window, a minimize->
    # restore cycle often jolts a stubborn window to the front. Only
    # fires when the primary path missed, so there's no flicker in the
    # common (successful) case.
    try:
        fg2 = user32.GetForegroundWindow()
        if not fg2 or int(fg2) != hwnd_int:
            user32.ShowWindow(hwnd_p, SW_MINIMIZE)
            user32.ShowWindow(hwnd_p, SW_RESTORE)
    except Exception:
        pass
    return True


def _ensure_foreground_lock_disabled():
    """Set HKCU\\Control Panel\\Desktop\\ForegroundLockTimeout to 0 so
    Windows always lets a window take the foreground. The non-zero Windows
    default lets the foreground-lock leave a window flashing in the taskbar
    instead of focusing — a state a screen-reader user can't recover from
    (no focus = NVDA reads nothing = "it didn't open"). Hearthkin is built
    for exactly the people who can't troubleshoot that, so it manages this
    by default (opt out in Preferences).

    Only writes when the value is currently non-zero, so there's no
    registry churn on every launch. Takes effect on the next sign-in.
    Windows-only, fully guarded — never raises. Returns True if it changed
    the value."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop", 0,
            winreg.KEY_READ | winreg.KEY_SET_VALUE,
        )
        try:
            try:
                cur, _ = winreg.QueryValueEx(key, "ForegroundLockTimeout")
            except FileNotFoundError:
                cur = None
            if cur != 0:
                winreg.SetValueEx(
                    key, "ForegroundLockTimeout", 0, winreg.REG_DWORD, 0,
                )
                return True
            return False
        finally:
            winreg.CloseKey(key)
    except Exception:
        return False


def _bring_existing_hearthkin_to_front():
    """If another Hearthkin process owns a top-level window with a
    title starting with 'Hearthkin', bring it to the foreground.
    Used when a duplicate-launch is rejected by SingleInstanceChecker
    so the user gets visible feedback (the existing instance pops
    up) instead of silent failure.

    Windows-only; returns False on other platforms or if no matching
    window is found."""
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32
    except Exception:
        return False

    # SW_RESTORE = 9 — un-minimize and show.
    SW_RESTORE = 9
    matches = []

    def enum_callback(hwnd, _lparam):
        # Skip invisible windows so we don't accidentally raise the
        # tray icon's hidden owner window or similar.
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value or ""
            if title.startswith(APP_NAME):
                matches.append(hwnd)
        return True

    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        ctypes.c_void_p,  # HWND
        ctypes.c_void_p,  # LPARAM
    )
    try:
        user32.EnumWindows(EnumWindowsProc(enum_callback), 0)
    except Exception:
        return False

    if not matches:
        return False
    hwnd = matches[0]
    # Cross-process foreground steal (a second launch surfacing the
    # already-running instance) is the case Windows denies most readily —
    # use the robust helper, not a bare SetForegroundWindow.
    try:
        _force_foreground(hwnd)
    except Exception:
        return False
    return True


