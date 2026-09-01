# SPDX-License-Identifier: CC0-1.0

"""Cron subprocess entry point. Invoked by Windows Task Scheduler
(or a manual CLI run) to fire a scheduled wake-up for a single kin.

Lives outside `hearthkin.pyw` so it can start fast — no wxPython
import, no GUI, no main-frame construction. The whole life of this
process is: parse args, check the lock, either drop a request file
(if Hearthkin is up) or run the LLM call ourselves (if it's not),
write journal + Telegram on isolated runs, exit.

Usage:
    python hearthkin_cron.py --kin <kin> --entry-index 0
    python hearthkin_cron.py --kin <kin> --entry-index 0 --run-now

`--entry-index` selects which entry in the kin's `cron_entries` config
list to fire. Task Scheduler entries are created with a fixed index
per-row when the user configures cron in Settings.

`--run-now` bypasses the `cron_entries[index].enabled` check so you
can test even when the toggle is off. Used by the Settings dialog's "Test
wake-up now" button and by manual debugging."""

import argparse
import datetime
import json
import sys
import traceback
from pathlib import Path


def _add_repo_to_path():
    # When Task Scheduler invokes us, cwd is the system32 default. Make
    # sure the repo dir (where this file lives) is on sys.path so
    # relative imports of kin_persistence/llm_backend/cron_helpers work.
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))


_add_repo_to_path()

import llm_backend
import cron_helpers
import turn_steering
from hearthkin_paths import logs_dir
import tools as kin_tools
from chat_helpers import extract_inline_thinking
from kin_persistence import (
    CONFIG_FILE,
    LOGS_DIR,
    agent_dir,
    append_agent_conversation_turn,
    build_system_prompt,
    load_agent_config,
    load_agent_conversation,
    load_json,
    load_kin_tools,
    load_memory,
    load_memory_for_prompt,
    load_soul,
    resolve_kin_ollama_host,
)


# Tools we refuse to expose to the cron subprocess. The cron path runs
# unattended, possibly while the operator is asleep — tools that need
# UI approval, that hold open processes, or that capture the user's
# webcam can't safely fire from here.
#
# - exec: needs the harness-side approval dialog (wxPython); a cron
#   subprocess can't show one. Even with the kin's exec_allowlist.json
#   matched, running arbitrary commands unattended overnight is the
#   wrong default. If a kin needs scheduled exec, the operator can run
#   the same command manually or build it into a separate scheduled
#   task.
# - kill_process / list_processes: the process tracking set is held on
#   the running frame; a cron subprocess has no access to it.
# - use_webcam: captures from the user's webcam. Not appropriate
#   unattended.
#
# Everything else (read_file, write_file, edit_file, note, read_staging,
# archive_staging, memory_search, context_status, fetch_url, web_search,
# recent_thinking) is safe-for-cron and is exactly the set the kin's
# nightly tending prompt expects to use.
_CRON_TOOL_DENYLIST = frozenset({
    "exec", "kill_process", "list_processes", "use_webcam",
})


def _load_cron_tools(kin):
    """Load the kin's enabled tools, filter out the unsafe-for-cron set,
    and build (schemas, executor) ready for run_tool_loop. Returns
    (schemas, executor, enabled_names_after_filter) so the caller can
    decide whether to run the tool loop or fall through to plain chat()
    when no safe tools remain.

    Returns ([], {}, []) when the kin has no tools enabled or every
    enabled tool is on the cron denylist."""
    try:
        enabled = load_kin_tools(kin) or []
    except Exception:
        enabled = []
    safe_names = [n for n in enabled if n not in _CRON_TOOL_DENYLIST]
    if not safe_names:
        return [], {}, []
    cfg = load_agent_config(kin) or {}
    chat_model = (cfg.get("model") or "").strip()
    try:
        schemas, executor = kin_tools.load_tools(
            safe_names,
            context={"agent_name": kin},
            model=chat_model,
            cron_turn=True,  # a scheduled tend always gets its staging tools
        )
    except Exception as e:
        cron_helpers.log_cron_error(
            kin, "load_cron_tools",
            f"failed to build cron tool environment: {e}",
        )
        return [], {}, []
    return schemas, executor, safe_names


def _log_empty_cron_reply(kin, surface, model, raw_content):
    """Always-on diagnostic for empty cron replies. Parallel to
    Hearthkin._log_empty_reply on the desktop and TelegramBot's on the
    Telegram surfaces. Without this, a cron wake-up that returns empty
    content gets persisted as "[no reply produced]" with no log entry —
    the operator has no way to tell whether the model returned nothing
    or something else broke. Especially important for the nightly
    tending cron, where a silent failure means a day's staging notes
    go untended."""
    try:
        path = LOGS_DIR / "empty_replies.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with open(path, "a", encoding="utf-8") as f:
            f.write(
                f"{ts} [{kin}] surface={surface} model={model} "
                f"raw={raw_content!r}\n"
            )
    except Exception:
        pass


def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="hearthkin_cron",
        description="Fire a scheduled wake-up for a single kin.",
    )
    p.add_argument("--kin", required=True, help="Kin name (folder under ~/.hearthkin/kin/).")
    p.add_argument(
        "--entry-index",
        type=int,
        default=0,
        help="Which entry in the kin's cron_entries list to fire. Defaults to 0.",
    )
    p.add_argument(
        "--run-now",
        action="store_true",
        help="Bypass the cron_entries[index].enabled check. Used by the Test Now button.",
    )
    p.add_argument(
        "--time-label",
        default=None,
        help="The scheduled HH:MM this fire is for (journal label). A multi-time "
             "entry has no single `time` field, so the scheduler passes the "
             "specific fire-time here. Optional; falls back to the entry's time "
             "or the wall clock.",
    )
    return p.parse_args(argv)


def _resolve_entry(cfg, index):
    """Return the (entry_dict, time_label, prompt) tuple for the given
    index in cfg["cron_entries"], or (None, None, None) if out of range
    or malformed."""
    entries = cfg.get("cron_entries") or []
    if not isinstance(entries, list):
        return None, None, None
    if index < 0 or index >= len(entries):
        return None, None, None
    entry = entries[index]
    if not isinstance(entry, dict):
        return None, None, None
    time_label = str(entry.get("time", "") or "").strip()
    prompt = str(entry.get("prompt", "") or "").strip()
    return entry, time_label, prompt


def _est_tokens(text):
    """Rough token count, shared with the rest of the app. Falls back to a
    character heuristic rather than raising -- a sizing helper must never be
    the reason a wake-up fails."""
    try:
        from chat_helpers import estimate_tokens
        return estimate_tokens(text or "")
    except Exception:
        return max(1, len(text or "") // 4)


def _build_messages(kin, user_turn_text, enabled_tools=None, history_tokens=0):
    """Build the message list for the cron call: soul + memory as the
    system prompt, the kin's conversation history as the middle,
    `user_turn_text` as a final user turn. Filters tool-role turns out
    of history (cron doesn't run tools; passing them through can
    confuse providers that expect tool messages to be paired with
    active tool contexts).

    `enabled_tools` is the cron-safe tool set for this wake-up (from
    _load_cron_tools); it gates the base prompt's tool/memory
    scaffolding so a tool-less wake-up doesn't carry tending
    instructions it can't act on. Pass None to keep the whole base
    prompt (legacy / unknown).

    `user_turn_text` is the already-framed wake-up prompt. The caller
    runs the framing once (via cron_helpers.frame_wake_up_prompt) and
    passes the same string both here and to the persistence layer, so
    the model context, conversation.jsonl, and any kin re-reading her
    own history later all see consistent text.

    `history_tokens` caps the SIZE of the recent tail that comes along; 0
    means all of it, which is what a scheduled wake-up wants. A HEARTBEAT
    does not.
    A heartbeat asks one question -- do you feel like saying something? --
    and the honest answer is usually no, yet it was carrying the kin's
    entire conversation to ask it: measured at 22,000 tokens, roughly 280
    seconds of prefill, for a call that most often produces nothing. At 28%
    of all model calls on this install that was the single largest consumer
    of a machine somebody was waiting on.

    A heartbeat needs to know who it is and what has just been happening,
    not everything that ever happened. Capping the tail also makes the
    prompt small and stable, which is exactly the shape that stays warm in
    a context slot between heartbeats -- a huge prompt that changes every
    time can never be reused.

    Budgeted by tokens rather than turn count deliberately. Turn count is a
    proxy that fails exactly where it matters: a kin being fed long passages
    has turns of 1,200+ tokens, so a twelve-turn cap was still 20,000 of
    them."""
    soul = (load_soul(kin) or "").strip()
    # for_prompt: this subprocess is where a kin's nightly tending writes its
    # depth logs, and nothing here ever rebuilt the index that points at them.
    memory = load_memory_for_prompt(kin) or ""
    sys_content = build_system_prompt(soul, memory, enabled_tools=enabled_tools,
                                      kin_name=kin)
    # Name the tools this wake-up may actually call. Every conversational
    # surface does this and cron never did, which had it exactly backwards:
    # a scheduled tend is the LEAST supervised turn this app takes, so it got
    # the least steering, and a kin that narrates using a tool instead of
    # calling one is only found out in the morning when the note it described
    # writing turns out not to exist.
    # ...but not at a model that has been probed and does not call tools.
    # Measured 2026-08-22: three models, nine conditions, and this text moved
    # nothing in any of them -- the ones that call tools call them without it,
    # and the one that doesn't isn't rescued by it. Repeating an instruction a
    # model cannot follow is not steering, it is noise in the most expensive
    # position in the prompt, and such a model is routed to the text path
    # instead (see toolless_memory.use_text_memory_path).
    #
    # The authoring-bridge hint below deliberately STAYS: that one teaches the
    # fenced-block write, which is the road this model is now being sent down.
    _on_text_path = False
    if enabled_tools:
        try:
            import toolless_memory
            from kin_persistence import load_agent_config
            _on_text_path = toolless_memory.use_text_memory_path(
                enabled_tools, (load_agent_config(kin).get("model") or "").strip())
        except Exception:
            _on_text_path = False
    if enabled_tools and not _on_text_path:
        try:
            from kin_persistence import load_app_prompt
            sys_content = (sys_content or "") + load_app_prompt(
                "tool_use_hint", kin).replace(
                    "{tools}", ", ".join(enabled_tools))
        except Exception:
            pass
    # Kin that can write files get the authoring-bridge fallback hint — tending
    # is where write-gesturing bites (the kin is asked to write memory files),
    # and the low-load fenced-write path is honored by _maybe_authoring_bridge_cron
    # after the reply. See authoring_bridge.py.
    if enabled_tools and ({"write_file", "edit_file"} & set(enabled_tools)):
        try:
            from kin_persistence import load_app_prompt
            sys_content = (sys_content or "") + load_app_prompt(
                "authoring_bridge_hint", kin)
        except Exception:
            pass
    convo = load_agent_conversation(kin) or []
    if history_tokens and history_tokens > 0:
        # Budget by SIZE, not turn count. Counting turns sounds equivalent and
        # is not: a kin being fed long passages has turns of 1,200+ tokens, so
        # "the last twelve" was still 20,000 of them -- capping the number
        # while leaving the cost unbounded. Walk backwards until the budget is
        # spent, so the ceiling holds whoever the kin is and whatever they have
        # been talking about.
        readable = [m for m in convo
                    if isinstance(m, dict)
                    and m.get("role") in ("user", "assistant")
                    and isinstance(m.get("content"), str)]
        kept, spent = [], 0
        for m in reversed(readable):
            cost = _est_tokens(m["content"])
            # Always keep at least one turn, even if it alone blows the
            # budget: a heartbeat with no idea what just happened is worse
            # than a slightly expensive one.
            if kept and spent + cost > history_tokens:
                break
            kept.append(m)
            spent += cost
        convo = list(reversed(kept))

    messages = []
    if sys_content:
        messages.append({"role": "system", "content": sys_content})
    for m in convo:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        if not isinstance(content, str):
            continue
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_turn_text})
    return messages


def _maybe_post_telegram(kin, cfg, text):
    """Post `text` to every Telegram user who has 'Mirror desktop
    messages to my Telegram chat' enabled for this kin. Cron wake-ups
    are structurally desktop activity (kin acting on its own), so the
    mirror-to-telegram opt-in is the right gate for who receives them
    — same rule the active-kin chat path uses via
    Hearthkin._maybe_mirror_to_telegram, so all three cron delivery
    paths (subprocess-only, isolated-worker, active-kin) reach the
    same recipients regardless of which path fires.

    Prior shape posted to "first numeric entry in allow_from", which
    diverged from the active-kin path's mirror-to-telegram check. If
    you had cron + share-desktop on but mirror-to-telegram off, the
    cron reply landed on Telegram via paths 1/3 but vanished into
    desktop chat via path 2 — inconsistent and confusing. Now: same
    recipient rule everywhere.

    No-ops cleanly when Telegram is off, the bot token is missing, or
    no user has mirror-to-telegram enabled. Failures get logged to
    telegram_failures.log so the user can tell why a cron post didn't
    land. The reply is still journaled locally regardless — Telegram
    is best-effort delivery for cron."""
    tg = cfg.get("telegram") or {}
    if not isinstance(tg, dict):
        return
    if not tg.get("enabled"):
        return
    # Config key is "bot_token" everywhere else (telegram_bot.py,
    # dialogs.py, hearthkin.pyw _maybe_mirror_to_telegram). This
    # function read "token" since it was added — a one-char typo
    # that meant `tg.get("token")` always returned "" and the early
    # return at the next line silently no-op'd every cron post.
    # That's why path-1 and path-3 cron deliveries to Telegram
    # never landed; path-2 worked because Hearthkin's mirror helper
    # used the correct key. Fixed now.
    token = (tg.get("bot_token") or "").strip()
    if not token:
        return
    mirror_map = tg.get("user_mirror_to_telegram") or {}
    if not isinstance(mirror_map, dict):
        return
    targets = [str(uid) for uid, on in mirror_map.items() if on]
    if not targets:
        return
    try:
        from telegram_bot import telegram_api_call
    except Exception:
        return
    for target in targets:
        try:
            # Chunk if needed — Telegram's per-message text cap is
            # 4096 chars. Round down to 4000 to leave headroom for
            # any protocol overhead the SDK might add.
            for chunk_start in range(0, len(text), 4000):
                chunk = text[chunk_start: chunk_start + 4000]
                telegram_api_call(
                    token,
                    "sendMessage",
                    {"chat_id": target, "text": chunk},
                    timeout=20,
                )
        except Exception as e:
            # Was silently swallowed in the older shape. Log so a
            # missing cron post is debuggable. The reply is still in
            # the journal and conversation.jsonl regardless.
            try:
                from kin_persistence import append_failure_log
                append_failure_log(
                    "telegram_failures.log",
                    kin,
                    f"cron wake-up Telegram post chat_id={target}",
                    e,
                )
            except Exception:
                pass


def _tg_send_chunked(token, chat_id, text, kin, context):
    """Send `text` (chunked to Telegram's ~4096-char cap, 4000 for headroom)
    to a single Telegram chat_id — a DM user id OR a group chat id (negative;
    Telegram's sendMessage takes either on the same call). Failures log to
    telegram_failures.log so a missing post is debuggable; the reply is
    journaled locally regardless."""
    try:
        from telegram_bot import telegram_api_call
    except Exception:
        return
    try:
        for chunk_start in range(0, len(text), 4000):
            chunk = text[chunk_start: chunk_start + 4000]
            telegram_api_call(
                token, "sendMessage",
                {"chat_id": str(chat_id), "text": chunk}, timeout=20)
    except Exception as e:
        try:
            from kin_persistence import append_failure_log
            append_failure_log("telegram_failures.log", kin, context, e)
        except Exception:
            pass


def _deliver_to_destinations(kin, cfg, text, destinations):
    """Send a cron reply to the destinations the operator set on the cron
    entry — the 'where does this go' addressing. Each item is a dict
    ``{"surface": "telegram_dm" | "telegram_group" | "desktop", "id": <chat_id>}``.
    `telegram_dm` and `telegram_group` both resolve to a Telegram sendMessage
    on the id (group ids are negative). `desktop` (or an unknown surface)
    sends nothing outward: the reply is already recorded in the kin's
    conversation.jsonl + journal, which IS the desktop record. No-ops cleanly
    when Telegram is off or the bot token is missing. This is the addressed
    counterpart to _maybe_post_telegram's legacy mirror-to-DM behavior."""
    tg = cfg.get("telegram") or {}
    if not isinstance(tg, dict) or not tg.get("enabled"):
        return
    token = (tg.get("bot_token") or "").strip()
    if not token:
        return
    for d in (destinations or []):
        if not isinstance(d, dict):
            continue
        surface = d.get("surface")
        if surface in ("telegram_dm", "telegram_group"):
            chat_id = str(d.get("id", "")).strip()
            if chat_id:
                _tg_send_chunked(
                    token, chat_id, text, kin,
                    f"cron destination {surface} chat_id={chat_id}")
        # "desktop" / unknown -> no outward send (conversation.jsonl covers it)


def _append_to_conversation(kin, prompt, reply, intermediate_turns=None):
    """Append the wake-up user turn + (any tool round-trips) + final
    assistant reply to the kin's persisted conversation. Only call
    this when Hearthkin is NOT running (lock absent) — otherwise the
    GUI's in-memory copy would overwrite us on its next save. The
    request-file path handles the running-Hearthkin case.

    `intermediate_turns` is `result.messages_added` from a tool-loop
    run (assistant-with-tool_calls turns + role=tool result turns,
    in order). Passed through verbatim between the user prompt and
    the final reply so the kin's next read sees the full round-trip
    record — same shape the desktop path uses via
    `_pending_tool_history`. Empty/None for plain (no-tools) cron
    runs.

    Uses append_agent_conversation_turn so the cost is constant
    regardless of total conversation length (was O(N) under the old
    load + modify + save shape — meaningful for kin with hundreds of
    turns)."""
    now_iso = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        # raise_on_failure: the append helper logs-and-swallows by
        # default, which made this whole except block dead code (audit
        # DH6) — a disk-full / permission failure was already logged
        # once by the helper but never reached the cron-specific
        # logging below, so the canonical cron log had no trace of the
        # wake-up vanishing from chat history.
        append_agent_conversation_turn(
            kin, {"role": "user", "content": prompt, "ts": now_iso},
            raise_on_failure=True,
        )
        for turn in (intermediate_turns or []):
            # _clean_chat_message inside append normalizes the shape;
            # malformed turns are dropped, valid ones are persisted.
            append_agent_conversation_turn(kin, turn, raise_on_failure=True)
        append_agent_conversation_turn(
            kin, {"role": "assistant", "content": reply, "ts": now_iso},
            raise_on_failure=True,
        )
    except Exception as e:
        # If conversation.jsonl can't be written, the wake-up is
        # effectively lost from the kin's perspective on next launch.
        # Used to fall through silently; now logs so the user can
        # find out why their cron isn't visible in chat history.
        # (Stakes are lower than the Telegram migrations' equivalent —
        # the journal write still carries the reply.)
        try:
            from kin_persistence import append_failure_log
            append_failure_log(
                "save_failures.log",
                kin,
                f"cron append_conversation prompt_len={len(prompt or '')}",
                e,
            )
        except Exception:
            pass
        try:
            cron_helpers.log_cron_error(
                kin, "conversation_append_failed",
                f"wake-up turns not persisted to conversation.jsonl: {e}",
            )
        except Exception:
            pass


def _cron_tool_receipt_footer(cfg, result, tools_were_available):
    """Operator-facing receipt appended to a cron wake-up's Telegram post.

    Cron posts otherwise carry only the kin's reply text — nothing that
    says whether tools actually fired. So a real tending run and a model
    that merely *narrated* tending ("*reads staging notes*") look
    identical on Telegram. One kin narrates tool actions under the
    roleplay register without issuing structured calls; another tends for
    real but the proof never reaches the operator. The bare cron post hides
    both. This
    footer is the unfakeable receipt, built from the tool-loop's actual
    round-trips (result.messages_added), not the model's prose:

      - tools fired                 -> "_used: read_staging, edit_file_"
      - tools available, none fired -> "_(no tools called)_"   <- gesture
      - no tools available          -> ""  (nothing to report)

    Gated on cfg.telegram.show_tool_summary (default True), mirroring the
    live Telegram footer (TelegramBot._build_tool_summary_footer). Plain
    text, no emoji — reads cleanly under NVDA.
    """
    tg = (cfg or {}).get("telegram") or {}
    if not tg.get("show_tool_summary", True):
        return ""
    names = []
    try:
        from chat_helpers import scan_intermediate_tool_content
        _, names = scan_intermediate_tool_content(
            getattr(result, "messages_added", None) or [])
    except Exception:
        names = []
    if names:
        return "\n\n_used: " + ", ".join(names) + "_"
    if tools_were_available:
        return "\n\n_(no tools called)_"
    return ""


def _post_cron_reply_to_telegram(kin, cfg, reply, result, schemas, destinations=None):
    """Post a cron reply to Telegram. Strips any model-emitted `_used: ..._`
    line from the reply first — defense-in-depth so the receipt the operator
    sees was built by the harness from the actual tool-loop, not faked by a kin
    that picked up the pattern (the exact `**Tool call:** ...` markdown
    hallucination Mistral-era kin produce under the roleplay register).

    Routing:
      - `destinations is None` (legacy cron entries with no addressing): the
        reply goes to the mirror-enabled DM users (historic behavior), WITH the
        unfakeable tool-receipt footer (see _cron_tool_receipt_footer) — that
        footer is for the *operator*.
      - `destinations` is a list (the operator addressed this cron somewhere
        specific): the reply goes to exactly those places WITHOUT the receipt
        footer — a group's members want the message, not a '_used: ..._' trace.
    """
    clean = reply
    try:
        from chat_helpers import strip_tool_summary_footer
        clean = strip_tool_summary_footer(reply)
    except Exception:
        clean = reply
    if destinations is None:
        _maybe_post_telegram(
            kin, cfg, clean + _cron_tool_receipt_footer(cfg, result, bool(schemas)))
    else:
        _deliver_to_destinations(kin, cfg, clean, destinations)


def _read_staging_fired(result):
    """Did read_staging actually fire in this tool-loop result? Ground truth
    from the loop's round-trips — no gesture-pattern matching. Returns True on
    any uncertainty so we never falsely nag a kin that did tend."""
    try:
        from chat_helpers import scan_intermediate_tool_content
        _, names = scan_intermediate_tool_content(
            getattr(result, "messages_added", None) or [])
        return "read_staging" in names
    except Exception:
        return True


def _inject_staging_status(kin, messages, safe_tools=None, model=None):
    """Insert an ephemeral system note (not persisted) telling the kin the
    current staging state, right before the final user turn. Lets the kin
    answer 'nothing to tend' on an empty staging without fishing, and know
    there's real work when there is.

    For a kin with NO tools this is not enough and is arguably cruel: it is
    woken at 3am, told two scopes are pending, given a prompt that says to
    call `read_staging`, and has no way to do any of it. Such a kin gets the
    notes THEMSELVES inlined instead, plus the `toolless_tend_note`
    correction saying which part of the wake-up doesn't apply tonight. See
    toolless_memory.py.

    Returns ``(had_work, shown_scopes)`` — `had_work` captured BEFORE the loop
    (a successful tend archives the files), `shown_scopes` non-empty only on
    the tool-less path, for the archive-after-write step.
    """
    from kin_persistence import (
        list_staging_files, load_app_prompt, staging_status_line,
    )
    had_work = bool(list_staging_files(kin))
    try:
        import toolless_memory
        if toolless_memory.use_text_memory_path(safe_tools, model):
            # tending=True: a scheduled wake-up IS the tending moment. On the
            # conversational surfaces the notes only appear when the person
            # asks, so ordinary chat never carries them.
            new_messages, shown = toolless_memory.inject(
                messages, kin, safe_tools, tending=True, model=model)
            if shown:
                messages[:] = new_messages
                note = load_app_prompt("toolless_tend_note", kin)
                messages.insert(max(0, len(messages) - 1),
                                {"role": "system", "content": note})
                return had_work, shown
            # Nothing pending: fall through to the ordinary empty-staging line,
            # which exists to stop a kin miming a tend it has no work for.
    except Exception:
        pass
    status = staging_status_line(kin)
    if status and messages:
        # Before the last message (the framed user turn).
        messages.insert(max(0, len(messages) - 1),
                        {"role": "system", "content": status})
    return had_work, []


def _toolless_memory_cron(kin, reply, safe_tools, shown_scopes,
                          model=None):
    """Write side of the no-tools memory loop on a scheduled wake-up.

    Same ``(kin_note, operator_confirm)`` contract as the authoring bridge
    below: the kin-facing note carries full paths and is appended to
    conversation.jsonl so the kin's next read is accurate; the operator-facing
    line is basenames only and joins the journal entry. Never raises — a
    memory-plumbing failure must not sink an unattended run."""
    try:
        if not reply:
            return None, None
        import os
        import toolless_memory
        results, archived = toolless_memory.commit(
            kin, reply, safe_tools, shown_scopes=shown_scopes or [],
            model=model)
        note = toolless_memory.receipt(kin, results, archived)
        if not note:
            # Nothing landed. Unattended is exactly where a silent miss does
            # the most damage — nobody is there to notice the kin thanking
            # itself for a save that didn't happen. The kin-facing note goes
            # into its history; the operator sees it in the journal too,
            # because a tend that kept nothing is worth knowing about.
            nudge = toolless_memory.missed_write_nudge(kin, reply, results)
            if not nudge:
                return None, None
            return nudge, "[no-tools memory] nothing was saved this wake-up"
        oks = [(p, d) for (p, ok, d) in results if ok]
        errs = [p for (p, ok, _d) in results if not ok]
        conf_bits = []
        if oks:
            conf_bits.append("saved " + ", ".join(
                f"{os.path.basename(str(p))} ({n} bytes)" for p, n in oks))
        if errs:
            conf_bits.append(f"{len(errs)} file(s) couldn't be saved (see logs)")
        if archived:
            conf_bits.append(f"tended {len(archived)} staging scope(s)")
        return note, ("[no-tools memory] " + "; ".join(conf_bits)
                      if conf_bits else None)
    except Exception:
        return None, None


def _maybe_roleplay_corrective_cron(kin, reply, safe_tools, added_turns, model):
    """Notice a kin ACTING OUT a tool call on a scheduled wake-up.

    The judgement lives in turn_steering, shared with the other surfaces —
    whether a kin gestured has no business varying by where it was talking,
    and three copies of that rule is how they drift apart. What stays here is
    only the surface label for the log."""
    import turn_steering
    return turn_steering.roleplay_corrective_note(
        kin, reply, safe_tools, added_turns,
        surface="cron-subprocess", model=model)


def _maybe_read_nudge_cron(kin, reply, safe_tools, added_turns):
    """Notice a kin narrating a read it never made. Shared judgement; see
    _maybe_roleplay_corrective_cron above."""
    import turn_steering
    return turn_steering.read_gesture_note(
        kin, reply, safe_tools, added_turns)


def _maybe_authoring_bridge_cron(kin, reply, safe_tools, shown_scopes=(),
                                 model=None):
    """Authoring bridge on the isolated cron path — tending is the surface
    most prone to write-gesturing (the kin is asked to write memory files
    and, under load, narrates the write instead of issuing it). If the kin
    authored file content in text — a ```write:<path>``` fence, or a *writes
    X* emote followed by a plain fenced block — perform the write. Gated on
    write tools being cron-safe-enabled. See authoring_bridge.py.

    Returns ``(kin_note, operator_confirm)``: kin_note (full paths) is
    appended to conversation.jsonl so the kin's next read knows what landed;
    operator_confirm (basename-only) is appended to the journal entry the
    operator reviews. Either may be None. Never raises — a bridge failure
    must not sink the cron run. No gesture-nudge here: cron's outcome-based
    tend_retry already handles 'narrated tending instead of doing it'."""
    try:
        if not reply:
            return None, None
        if not ({"write_file", "edit_file"} & set(safe_tools or [])):
            # A kin with no tools tends over the text channel or not at all —
            # and an unattended wake-up is the one it can't ask anyone about.
            return _toolless_memory_cron(kin, reply, safe_tools,
                                         shown_scopes, model)
        import os
        import authoring_bridge
        writes = authoring_bridge.extract_authoring_writes(reply)
        if not writes:
            return None, None
        results = authoring_bridge.commit_authoring_writes(kin, writes)
        oks = [(p, d) for (p, ok, d) in results if ok]
        errs = [(p, d) for (p, ok, d) in results if not ok]
        note_bits, conf_bits = [], []
        if oks:
            note_bits.append("saved from your reply: " + ", ".join(
                f"{p} ({n} bytes)" for p, n in oks))
            conf_bits.append("saved " + ", ".join(
                f"{os.path.basename(str(p))} ({n} bytes)" for p, n in oks))
        for p, e in errs:
            note_bits.append(f"could NOT save {p!r} — {e}")
        if errs:
            conf_bits.append(f"{len(errs)} file(s) couldn't be saved (see logs)")
        from kin_persistence import load_app_prompt
        note = (load_app_prompt("authoring_bridge_result", kin)
                .replace("{results}", "; ".join(note_bits))
                if note_bits else None)
        conf = "[authoring bridge] " + "; ".join(conf_bits) if conf_bits else None
        return note, conf
    except Exception:
        return None, None


def _run_isolated(kin, cfg, time_label, prompt, tend_retry=0, destinations=None):
    """Announce this run to the desktop app, then do it.

    A wake-up here happens in a SEPARATE PROCESS from Hearthkin, sharing no
    state with it. So the confirm-on-close dialog could not see it: you could
    quit mid-wake-up, be told nothing was in flight, and abandon the turn —
    which is exactly what happened on 2026-07-27. A marker file is the only
    channel two processes have here.

    The marker is released in a `finally`, and readers sweep markers whose pid
    is gone, so neither a crash nor a kill can leave something that claims to
    be working forever.
    """
    marker = None
    try:
        marker = cron_helpers.mark_cron_running(kin, time_label)
    except Exception:
        pass
    try:
        return _run_isolated_inner(
            kin, cfg, time_label, prompt,
            tend_retry=tend_retry, destinations=destinations)
    finally:
        try:
            cron_helpers.clear_cron_running(marker)
        except Exception:
            pass


def _run_isolated_inner(kin, cfg, time_label, prompt, tend_retry=0,
                        destinations=None):
    """Lock-absent path: run the LLM call directly from this subprocess,
    persist the result everywhere (conversation + journal + Telegram).

    `destinations` (from the cron entry): where the reply is sent outward.
    None = legacy mirror-to-DM behavior; a list = the operator-addressed
    surfaces (specific DMs / groups). Threaded into _post_cron_reply_to_telegram.

    `tend_retry` (from the cron entry): when staging had pending work but the
    kin's reply called no tools, re-prompt with the editable tend_missed_call
    corrective up to this many times. Outcome-based — judged by whether
    read_staging actually fired, not by matching gesture prose.

    The wake-up prompt is framed (via cron_helpers.frame_wake_up_prompt)
    before going into both the model context and conversation.jsonl —
    same string in both places, so the kin's own history scan later
    surfaces the time/date anchor and the "this was scheduled, not from
    a user" signal. The journal entry keeps the raw prompt since
    `append_journal` already adds its own date header and "Cron
    wake-up" section title; the inline framing would be redundant
    there."""
    model = (cfg.get("model") or "").strip()
    if not model:
        raise RuntimeError("no model configured for kin")
    fired_at = datetime.datetime.now()
    framed_prompt = cron_helpers.frame_wake_up_prompt(
        prompt, time_label, fired_at=fired_at, kin_name=kin
    )
    # Resolve the cron-safe tool set before building messages so the base
    # prompt's tool/memory scaffolding is fenced to what this wake-up can
    # actually run (see _load_cron_tools / build_system_prompt).
    schemas, executor, safe_tools = _load_cron_tools(kin)
    messages = _build_messages(kin, framed_prompt, enabled_tools=safe_tools)
    # Park keeper: for a keeper kin a wake-up IS a park turn. Show the park as it stands
    # plus the one salient move, and ask for a `> command` we run after the
    # reply. Injected as an ephemeral turn (not baked into framed_prompt), so
    # the recorded prompt stays clean. Gated per-kin; best-effort — a game hiccup
    # must never break the wake-up.
    try:
        import park_keeper as _PK
        if _PK.kin_park_mode(kin) == "keeper":
            from tools import get_game
            _host = get_game("tff")
            # Don't wake a kin into a park that isn't there. If the park can't
            # be reached, this wake-up is simply not a park turn: no keeper
            # framing, no park state, no request for a move — so the kin is
            # never asked to tend somewhere it cannot get to, and never has to
            # sit alone with a shut door five times a day. The operator gets
            # the always-on log entry instead, which is whose problem it is.
            if _host is not None:
                _ok, _why = _host.reachable(kin)
                if not _ok:
                    _host.log_unreachable(kin, _why, "cron wake-up")
                    _host = None
            if _host is not None:
                _look = _host.run(kin, "look")
                # Ask the HOST, not the save file. On a served park there is no
                # local save, and save_path() quietly returns the kin's own
                # private one -- which produced a confident hint about a park
                # the kin wasn't playing. GameHost.hint knows which kind of
                # park this is and asks the right thing.
                _hint = _host.hint(kin)
                # What the operator (or another player) did in this park since
                # the kin's last turn — so a keeper on cron can see you tending
                # alongside it, not just a park that mysteriously changed.
                _others = _host.unseen_moves(kin)
                messages.append({
                    "role": "user",
                    "content": _PK.MECHANISM + "\n"
                    + _PK.build_turn_message(_look, _hint, others=_others),
                })
    except Exception:
        pass
    # Tell the kin what's in staging up front (ephemeral note); capture whether
    # there was real work, for the outcome-based retry below.
    staging_had_work, toolless_scopes = _inject_staging_status(
        kin, messages, safe_tools, model)
    # Per-turn memory recall — surface the kin's own relevant depth on this
    # wake-up (no tool call), the same mechanism the conversational surfaces
    # use. Fail-soft: a recall problem must never break a cron run. The lazy
    # import keeps the cron subprocess's cold start fast.
    try:
        from memory_recall import inject_into_messages
        messages, _ = inject_into_messages(
            messages, kin,
            num_ctx=int(cfg.get("num_ctx", 8192) or 8192), cfg=cfg)
    except Exception:
        pass
    options = {
        "temperature": cfg.get("temperature", 0.8),
        "top_p": cfg.get("top_p", 0.9),
        "top_k": cfg.get("top_k", 40),
        "min_p": cfg.get("min_p", 0.0),
        "repeat_penalty": cfg.get("repeat_penalty", 1.1),
        "presence_penalty": cfg.get("presence_penalty", 0.0),
        "frequency_penalty": cfg.get("frequency_penalty", 0.0),
        "num_ctx": cfg.get("num_ctx", 8192),
    }
    cache = bool(cfg.get("cache", True))
    cache_ttl = str(cfg.get("cache_ttl", "auto"))
    # Non-streaming for cron — we want the whole reply atomic so journal
    # write + Telegram post happen once.
    show_thinking = bool(cfg.get("show_thinking", False))
    # Input-context truncation: like every other conversational surface
    # (desktop / room / Telegram DM / Telegram group / tool-loop), cron
    # must pass max_context_tokens or llm_backend.chat() won't truncate
    # oversized history. Without this, a long-running kin's cron wake-up
    # ships the entire conversation.jsonl regardless of num_ctx — which
    # produced a 280k-token send against Anthropic's 200k hard
    # limit, failing the wake-up entirely. See
    # CLAUDE.md "Input-context truncation is per-surface" — cron was
    # the last surface that hadn't been wired up.
    max_ctx = max(2048, int(cfg.get("num_ctx", 8192) or 8192) - 2000)
    # Per-kin Ollama machine: route this kin's model to its chosen host.
    # "" falls back to the app-default host the subprocess already set
    # via set_ollama_host (config.json -> ollama_host). Ignored for
    # OpenRouter-routed kin.
    _kin_host = resolve_kin_ollama_host(cfg.get("ollama_host_name", ""))
    _max_iter = int(cfg.get("max_tool_iterations", 8) or 8)

    # If the kin has safe-for-cron tools enabled, route through
    # run_tool_loop so the model can actually invoke them. Without this,
    # a tending prompt that says "call read_staging, then write to
    # memory/<topic>.md" has no way to execute — the model would
    # roleplay-narrate the actions and produce a text reply describing
    # what it would have tended, instead of actually tending: the
    # prompt expected tools, the cron path passed no tools=, and
    # tending was architecturally impossible.
    # (schemas/executor/safe_tools resolved above, before _build_messages.)
    if schemas:
        result = llm_backend.run_tool_loop(
            model, messages,
            tools=schemas, tool_executor=executor,
            options=options, cache=cache, cache_ttl=cache_ttl,
            show_thinking=show_thinking,
            max_context_tokens=max_ctx,
            tool_result_cap=int(cfg.get("tool_result_cap", 8000) or 8000),
            kin_name=kin, surface="cron-subprocess-tools",
            max_iterations=_max_iter,
            ollama_host=_kin_host,
        )
    else:
        result = llm_backend.chat(
            model, messages,
            options=options, stream=False,
            cache=cache, cache_ttl=cache_ttl,
            show_thinking=show_thinking,
            max_context_tokens=max_ctx,
            kin_name=kin, surface="cron-subprocess",
            ollama_host=_kin_host,
        )
    # Outcome-based tend recovery: staging had real work but read_staging never
    # fired = the kin narrated tending instead of doing it. Re-prompt with the
    # editable tend_missed_call corrective up to `tend_retry` times. No
    # gesture-pattern matching — judged purely by whether the tool fired.
    if (schemas and tend_retry > 0 and staging_had_work
            and not _read_staging_fired(result)):
        from kin_persistence import load_app_prompt
        attempt = 0
        while attempt < tend_retry and not _read_staging_fired(result):
            attempt += 1
            retry_messages = (
                list(messages)
                + (getattr(result, "messages_added", None) or [])
                + [{"role": "system",
                    "content": load_app_prompt("tend_missed_call", kin)}]
            )
            result = llm_backend.run_tool_loop(
                model, retry_messages,
                tools=schemas, tool_executor=executor,
                options=options, cache=cache, cache_ttl=cache_ttl,
                show_thinking=show_thinking,
                max_context_tokens=max_ctx,
                tool_result_cap=int(cfg.get("tool_result_cap", 8000) or 8000),
                kin_name=kin, surface="cron-tend-retry",
                max_iterations=_max_iter,
                ollama_host=_kin_host,
            )
    reply = (result.content or "").strip()
    # Pull any inline <thinking>...</thinking> markup out of content
    # before persistence / journal / Telegram post. The other four
    # send surfaces (1-on-1, room, Telegram DM, Telegram group) do
    # this in their done-handlers; cron's isolated path skips them
    # so needs its own call. See chat_helpers.extract_inline_thinking
    # for why some models (Haiku 4.5 inline, MiMo, R1 distills) leak
    # reasoning into content rather than via the structured channel.
    reply, _thinking = extract_inline_thinking(reply, "")
    # Anti-impersonation cleanup. Every other surface has run this for a long
    # time and cron never did, on the surface where it matters most: a
    # scheduled reply goes to the journal, into the conversation the kin reads
    # back as its own words, and — when configured — straight out to a
    # Telegram group. So the one turn nobody is awake to read was the one that
    # could post another kin's name into a room full of people, and then teach
    # itself from the record that it had said it.
    #
    # `impersonated` means the model opened by speaking AS someone else, which
    # is not cosmetic: the whole reply is in that voice and stripping the label
    # only hides it. Unattended, there is nobody to re-roll it, so the honest
    # move is to keep the cleaned text and record the fault where the operator
    # will find it.
    try:
        from chat_helpers import clean_kin_reply
        reply, _impersonated = clean_kin_reply(reply, kin)
        reply = (reply or "").strip()
        if _impersonated:
            cron_helpers.log_cron_error(
                kin, "impersonation",
                "the scheduled reply opened in another speaker's voice; the "
                "tag was stripped but the turn may still be in that voice")
    except Exception as e:
        cron_helpers.log_cron_error(kin, "reply_cleanup", str(e))
    # Park keeper: run the `> command` the kin chose, and -- for a keeper's
    # own turn -- keep going until it stops (see _run_cron_park_turn).
    # Best-effort; never breaks the run.
    try:
        reply, _turn = _run_cron_park_turn(
            kin, reply, messages, model, options, cache, cache_ttl,
            show_thinking, max_ctx, _kin_host)
        # Nobody watches a cron run land in real time, so a turn that
        # actually looped needs a trace an operator could find later --
        # reuse the always-on cron log rather than a new file.
        if _turn is not None and _turn.taken > 1:
            cron_helpers.log_cron_error(
                kin, "park_turn",
                f"{_turn.taken} move(s) this wake-up (asked {_turn.asked}x)"
                + (", stopped at the move ceiling"
                   if _turn.spent_allowance else ""))
    except Exception:
        pass
    if not reply:
        _log_empty_cron_reply(
            kin, "cron-subprocess", model,
            getattr(result, "content", None) or "")
        reply = "[no reply produced]"
    # Authoring bridge: if the kin authored file content in text (a fenced
    # write / *writes X* emote) instead of a write_file call it froze on,
    # perform the write. Gated on write tools being cron-safe-enabled.
    bridge_note, bridge_confirm = _maybe_authoring_bridge_cron(
        kin, reply, safe_tools, toolless_scopes, model)
    # Steering notes for a turn nobody was awake to read. Both are appended to
    # the kin's own history AFTER the reply they describe, so the correction
    # arrives in the same place the kin will next look — not to the operator,
    # who cannot do anything about a model gesturing at 4am anyway. The
    # authoring bridge above runs FIRST on purpose: if it managed to commit
    # the write the kin only described, there is nothing left to correct.
    _added = getattr(result, "messages_added", None) or []
    steer_notes = []
    if not bridge_note:
        note = _maybe_roleplay_corrective_cron(
            kin, reply, safe_tools, _added, model)
        if note:
            steer_notes.append(note)
    note = _maybe_read_nudge_cron(kin, reply, safe_tools, _added)
    if note:
        steer_notes.append(note)
    # Persist tool round-trips (if any) so the kin's next read of
    # conversation.jsonl sees what they actually did. Without this the
    # kin would only see the final text reply on the next session and
    # lose the record of which tools fired with what arguments and
    # what came back.
    _append_to_conversation(
        kin, framed_prompt, reply,
        intermediate_turns=getattr(result, "messages_added", None) or [],
    )
    # Tell the app these turns happened. This branch only runs with Hearthkin
    # CLOSED, so there is no in-memory counter to tick and the "every N
    # messages" distillation trigger could never see a night's tending. The
    # percentage trigger reads the conversation off disk and always could —
    # which is why this went unnoticed: whether a kin kept remembering
    # depended on which of the two settings its person had chosen.
    try:
        cron_helpers.note_unattended_turns(kin, "desktop", 2)
    except Exception:
        pass
    # Kin-facing bridge note (full paths) appended AFTER the assistant reply
    # it describes, so the kin's next read knows what actually landed.
    if bridge_note:
        try:
            append_agent_conversation_turn(kin, {
                "role": "system", "content": bridge_note,
                "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            })
        except Exception:
            pass
    for _note in steer_notes:
        try:
            append_agent_conversation_turn(kin, {
                "role": "system", "content": _note,
                "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            })
        except Exception:
            pass
    # Operator-facing confirmation (basenames) rides into the journal entry.
    journal_reply = reply + (("\n\n" + bridge_confirm) if bridge_confirm else "")
    cron_helpers.append_journal(kin, time_label or "(no time)", prompt, journal_reply)
    _post_cron_reply_to_telegram(kin, cfg, reply, result, schemas, destinations=destinations)
    return reply


def _run_cron_park_turn(kin, reply, messages, model, options, cache,
                        cache_ttl, show_thinking, max_ctx, kin_host):
    """A keeper kin's whole park turn for this wake-up: the `> command`
    already in `reply`, then -- as long as it keeps writing one -- the next
    move, through the shared ``park_keeper.play_turn`` loop the desktop and
    Telegram DM already use. So a multi-step walkthrough (making a new
    species is twelve questions) finishes in one wake-up instead of crawling
    one step per scheduled fire.

    Cron is unattended -- nobody is there to press Cancel mid-run -- so this
    leans entirely on play_turn's own bounds (the kin's ``park_moves_max``,
    the hard stop, and the kin's own "no `>` line" stop) rather than adding
    an interrupt channel nothing here could ever use.

    Returns ``(reply, turn)``. ``turn`` is the ``park_keeper.TurnResult``, or
    ``None`` when this wasn't a keeper turn at all (mode off, no park, or the
    park unreachable) -- distinct from a turn that ran and took one move, so
    a caller can tell "nothing to log" from "one ordinary move ran".

    Split out from ``_run_isolated_inner`` so the loop itself can be tested
    without the rest of a wake-up's machinery (the tool loop, staging,
    memory recall, ...). Never raises -- a game hiccup must never break the
    wake-up; the caller still wraps this best-effort, since a network error
    from the model call itself can come from further down."""
    import park_keeper as _PK
    if not (reply and _PK.kin_park_mode(kin) == "keeper"):
        return reply, None
    from tools import get_game
    host = get_game("tff")
    if host is None:
        return reply, None
    # Same gate on the way out. A kin can still write a `>` line on a
    # wake-up that wasn't a park turn (it has its own reasons to reach for
    # its park), and running it against a park that's down would hand back
    # the raw connection error as the kin's own ground truth -- which is
    # exactly the text that teaches a kin it is locked out.
    ok, why = host.reachable(kin)
    if not ok:
        host.log_unreachable(kin, why, "cron `>` line")
        return reply, None
    # The same messages this wake-up's own reply was produced from (the
    # keeper framing/park-state turn was appended to `messages` earlier in
    # _run_isolated_inner), plus the reply itself -- mirrors how the desktop
    # and Telegram build their continuation context.
    turn_msgs = list(messages) + [{"role": "assistant", "content": reply}]
    moves = []      # (command, result) for everything that ran

    def _run_move(text):
        cmd, res = _PK.route_reply(
            text, lambda c, s="": host.run(kin, c, say=s))
        if not res:
            return False
        moves.append((cmd, res))
        turn_msgs.append({
            "role": "system", "content": _PK.feedback_note(cmd, res)})
        return True

    def _ask():
        # No tools offered here, on purpose -- same as the desktop and
        # Telegram continuations: this is asking for the next move in a
        # game, not a work session.
        r = llm_backend.chat(
            model, turn_msgs,
            options=options, stream=False,
            cache=cache, cache_ttl=cache_ttl,
            show_thinking=show_thinking,
            max_context_tokens=max_ctx,
            kin_name=kin, surface="cron-park-turn",
            ollama_host=kin_host,
        )
        nxt = (getattr(r, "content", "") or "").strip()
        if not nxt:
            return ""
        nxt, _ = extract_inline_thinking(nxt, "")
        try:
            from chat_helpers import clean_kin_reply as _ckr
            nxt, _ = _ckr(nxt, kin)
        except Exception:
            pass
        nxt = (nxt or "").strip()
        if not nxt:
            return ""
        turn_msgs.append({"role": "assistant", "content": nxt})
        return nxt

    turn = _PK.play_turn(kin, reply, run_move=_run_move, ask=_ask,
                         awaiting=lambda: host.awaiting_answer(kin))
    if moves:
        reply = reply + "\n\n🌳 " + "\n\n".join(
            res if len(moves) == 1 else "> %s\n%s" % (cmd, res)
            for cmd, res in moves)
    return reply, turn


# Heartbeat tools: reach_out (the point) + a couple of read-only tools so the
# kin can reflect before deciding whether it has anything to say. Small and
# fixed — a heartbeat is a quiet moment, not a work session.
_HEARTBEAT_TOOLS = ["reach_out", "memory_search", "recent_thinking"]


def _log_heartbeat(kin, outcome):
    """Always-on one-line diagnostic (logs/heartbeat.log): fired-vs-silent, so
    the operator can see the cadence and confirm silence really is silent —
    without any user-facing clutter."""
    try:
        logdir = logs_dir()
        logdir.mkdir(parents=True, exist_ok=True)
        with open(logdir / "heartbeat.log", "a", encoding="utf-8") as f:
            f.write(
                f"{datetime.datetime.now().isoformat(timespec='seconds')} "
                f"kin={kin} {outcome}\n")
    except Exception:
        pass


def run_heartbeat(kin, cfg, should_stop=None):
    """Fire one quiet heartbeat: give the kin a moment and the chance to reach
    out (via the reach_out tool). If it calls reach_out, that tool delivers +
    records the message. If it doesn't, NOTHING is recorded or sent — the
    heartbeat prompt and the kin's (non-reaching) reply are dropped, so silence
    leaves no trace: no journal, no conversation turn, no 'HEARTBEAT_OK'.
    Returns True if the kin reached out.

    Runs an LLM call every time regardless of outcome — that's the cost of
    giving the kin a genuine chance to speak. The caller (the app's heartbeat
    timer) gates opt-in + frequency + active hours.

    `should_stop`, when given, is forwarded to run_tool_loop exactly like
    every other should_stop caller in this codebase — polled between tool
    iterations, not mid-generation of a single call. The desktop frame sets
    this so a heartbeat that's already running backs off once something
    that actually matters (a distillation, at minimum) needs the model,
    rather than contending with it for the rest of its run."""
    model = (cfg.get("model") or "").strip()
    if not model:
        _log_heartbeat(kin, "skipped=no-model")
        return False
    try:
        from kin_persistence import load_app_prompt, DEFAULT_HEARTBEAT_FRAME
        framed = load_app_prompt("heartbeat_frame", kin) or DEFAULT_HEARTBEAT_FRAME
    except Exception:
        try:
            from kin_persistence import DEFAULT_HEARTBEAT_FRAME
            framed = DEFAULT_HEARTBEAT_FRAME
        except Exception:
            framed = "[This is a heartbeat. You may reach out with reach_out if you have something, or stay quiet.]"
    names = [n for n in _HEARTBEAT_TOOLS if n in kin_tools.list_available()]
    try:
        schemas, executor = kin_tools.load_tools(
            names, context={"agent_name": kin}, model=model,
            proactive_wake=True)
    except Exception as e:
        _log_heartbeat(kin, f"skipped=tool-load-failed:{e}")
        return False
    if not schemas:
        _log_heartbeat(kin, "skipped=no-tools")
        return False
    # Carry only the recent tail. See _build_messages: a heartbeat was
    # loading the kin's whole conversation -- 22,000 tokens, ~280 seconds of
    # prefill -- to ask one question whose usual answer is "no thanks". It was
    # 28% of every model call on this install, all of it competing with the
    # person actually waiting for a reply.
    try:
        hb_budget = int((cfg.get("heartbeat") or {}).get("history_tokens", 2500) or 2500)
    except (TypeError, ValueError):
        hb_budget = 2500
    messages = _build_messages(kin, framed, enabled_tools=names,
                               history_tokens=max(0, hb_budget))
    try:
        from memory_recall import inject_into_messages
        messages, _ = inject_into_messages(
            messages, kin,
            num_ctx=int(cfg.get("num_ctx", 8192) or 8192), cfg=cfg)
    except Exception:
        pass
    options = {
        "temperature": cfg.get("temperature", 0.8),
        "top_p": cfg.get("top_p", 0.9),
        "top_k": cfg.get("top_k", 40),
        "min_p": cfg.get("min_p", 0.0),
        "repeat_penalty": cfg.get("repeat_penalty", 1.1),
        "presence_penalty": cfg.get("presence_penalty", 0.0),
        "frequency_penalty": cfg.get("frequency_penalty", 0.0),
        "num_ctx": cfg.get("num_ctx", 8192),
    }
    max_ctx = max(2048, int(cfg.get("num_ctx", 8192) or 8192) - 2000)
    _kin_host = resolve_kin_ollama_host(cfg.get("ollama_host_name", ""))
    try:
        result = llm_backend.run_tool_loop(
            model, messages,
            tools=schemas, tool_executor=executor,
            options=options,
            cache=bool(cfg.get("cache", True)),
            cache_ttl=str(cfg.get("cache_ttl", "auto")),
            show_thinking=bool(cfg.get("show_thinking", False)),
            max_context_tokens=max_ctx,
            tool_result_cap=int(cfg.get("tool_result_cap", 8000) or 8000),
            kin_name=kin, surface="heartbeat",
            max_iterations=int(cfg.get("max_tool_iterations", 8) or 8),
            ollama_host=_kin_host,
            should_stop=should_stop,
        )
    except Exception as e:
        _log_heartbeat(kin, f"error={type(e).__name__}:{e}")
        return False
    # Did the kin reach out? Ground truth from the tool-loop's round-trips.
    fired = False
    try:
        from chat_helpers import scan_intermediate_tool_content
        _, names_fired = scan_intermediate_tool_content(
            getattr(result, "messages_added", None) or [])
        fired = "reach_out" in names_fired
    except Exception:
        fired = False
    # The kin wrote something and never sent it.
    #
    # This is the one surface where a missed tool call DELETES the kin's own
    # words. Everywhere else a reply has a reader and an unmade tool call only
    # means some work did not happen; here the reply IS the work, nobody reads
    # it, and reach_out is the entire delivery mechanism. Silence was assumed
    # to mean nothing was produced. It never checked, and the numbers said
    # otherwise the whole time: heartbeats logged "silent" generated MORE text
    # than the ones that reached out, because a kin answering "would you like
    # to say something?" in prose is answering it, just not through the tool.
    #
    # So it is asked once, plainly, and a second refusal is honoured. One
    # extra model call, only on the path that currently loses everything.
    asked = False
    if not fired and not getattr(result, "stopped", False):
        try:
            note = turn_steering.unsent_reach_note(
                kin, getattr(result, "content", "") or "", names,
                getattr(result, "messages_added", None) or [])
        except Exception:
            note = ""
        if note and not (should_stop and should_stop()):
            try:
                follow = list(messages)
                follow.append({"role": "assistant",
                               "content": getattr(result, "content", "") or ""})
                follow.append({"role": "user", "content": note})
                second = llm_backend.run_tool_loop(
                    model, follow,
                    tools=schemas, tool_executor=executor,
                    options=options,
                    cache=bool(cfg.get("cache", True)),
                    cache_ttl=str(cfg.get("cache_ttl", "auto")),
                    show_thinking=bool(cfg.get("show_thinking", False)),
                    max_context_tokens=max_ctx,
                    tool_result_cap=int(cfg.get("tool_result_cap", 8000) or 8000),
                    kin_name=kin, surface="heartbeat-nudge",
                    max_iterations=int(cfg.get("max_tool_iterations", 8) or 8),
                    ollama_host=_kin_host,
                    should_stop=should_stop,
                )
                from chat_helpers import scan_intermediate_tool_content
                _, after = scan_intermediate_tool_content(
                    getattr(second, "messages_added", None) or [])
                fired = "reach_out" in after
                # Only NOW has the kin actually been asked. Setting this
                # before the call looks equivalent and is not: a nudge that
                # raises would file the kin's words as a decision it never
                # made, and the text would be dropped on exactly the reasoning
                # this whole change exists to remove. Caught live -- a broken
                # second round recorded 1,029 characters as "declined".
                asked = True
            except Exception as e:
                _log_heartbeat(kin, f"nudge-failed={type(e).__name__}:{e}")

    # Silence is free and leaves NO trace in the kin's history: we deliberately
    # do NOT persist the heartbeat prompt or the kin's reply to
    # conversation.jsonl. Only reach_out (if it fired) recorded anything — the
    # message the kin chose to send. What IS recorded now is a message that
    # never got the chance: see log_unsent_reach for why the text is kept in
    # that case and not in the other.
    try:
        turn_steering.log_unsent_reach(
            kin, model, getattr(result, "content", "") or "",
            asked=asked, delivered=fired)
    except Exception:
        pass
    if fired:
        _log_heartbeat(kin, "reached-out-after-nudge" if asked else "reached-out")
    else:
        _log_heartbeat(kin, "silent-declined" if asked else "silent")
    return fired


def main(argv=None):
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    kin = args.kin

    # Point the Ollama client at the configured host BEFORE any LLM call.
    # The GUI does this on startup (hearthkin.pyw: set_ollama_host from the
    # app config); the standalone cron process never did, so when Hearthkin
    # was closed the cron fell through to localhost:11434 — the wrong Ollama
    # (404 model-not-found) instead of the configured remote host. Read the
    # same app-level config.json the GUI reads so there's one source of
    # truth. Empty string = historical "use OLLAMA_HOST env / localhost".
    try:
        llm_backend.set_ollama_host(load_json(CONFIG_FILE, {}).get("ollama_host", ""))
    except Exception:
        pass

    if not agent_dir(kin).exists():
        cron_helpers.log_cron_error(kin, "missing_kin", f"no agent dir for {kin!r}")
        return 1

    try:
        cfg = load_agent_config(kin)
    except Exception as e:
        cron_helpers.log_cron_error(kin, "config_load", str(e))
        return 1

    entry, time_label, prompt = _resolve_entry(cfg, args.entry_index)
    # A multi-time entry stores no single `time`; the scheduler passes the
    # specific fire-time via --time-label so the journal header is accurate.
    if getattr(args, "time_label", None):
        _tl = str(args.time_label).strip()
        if _tl:
            time_label = _tl
    # Per-entry outcome-based tend retry: 0 = off (default). When > 0, a wake-up
    # that was handed pending staging but called no tools gets re-prompted.
    tend_retry = 0
    destinations = None
    if isinstance(entry, dict):
        try:
            tend_retry = max(0, int(entry.get("tend_retry", 0) or 0))
        except (TypeError, ValueError):
            tend_retry = 0
        # Where the operator addressed this cron's output (None = legacy
        # mirror-to-DM). A list of {"surface", "id"} dicts; see
        # _deliver_to_destinations. Empty list is treated as "None" (no
        # explicit addressing) so an accidentally-emptied list falls back
        # to the historic behavior rather than delivering nowhere.
        _dests = entry.get("destinations")
        destinations = _dests if isinstance(_dests, list) and _dests else None
    if entry is None:
        cron_helpers.log_cron_error(
            kin, "entry_index_out_of_range",
            f"index {args.entry_index} not in cron_entries (len={len(cfg.get('cron_entries') or [])})",
        )
        return 1
    if not prompt:
        cron_helpers.log_cron_error(kin, "empty_prompt", "cron_entries entry has no prompt")
        return 1
    if not args.run_now and not entry.get("enabled", False):
        # Toggle is off; don't fire. Not an error — Task Scheduler may
        # still be wired up while the user has just temporarily disabled.
        return 0

    # Decide between request-file mode (Hearthkin up, inject) and
    # isolated mode (Hearthkin closed, or inject_when_running=false).
    running = cron_helpers.lock_indicates_running()
    inject = bool(cfg.get("cron_inject_when_running", True))

    if running and inject:
        try:
            # Pass the resolved time_label (the --time-label this task was
            # scheduled with, already folded in above): the consumer can't
            # re-derive it for a multi-time entry, which has no single
            # "time" key.
            cron_helpers.write_request_file(
                kin, prompt, args.entry_index, time_label=time_label)
        except Exception as e:
            cron_helpers.log_cron_error(kin, "request_file_write", str(e))
            return 1
        return 0

    if running and not inject:
        # Hearthkin is up but the user opted out of live injection. Don't
        # touch conversation.json (race), just journal + Telegram.
        # We still need to run an LLM call to GET the reply — there's no
        # way to journal a wake-up without producing the reply first.
        # That call uses the kin's persisted conversation as context but
        # does NOT append its own turn back to conversation.json.
        try:
            model = (cfg.get("model") or "").strip()
            if not model:
                raise RuntimeError("no model configured for kin")
            framed_prompt = cron_helpers.frame_wake_up_prompt(
                prompt, time_label, kin_name=kin
            )
            # Resolve cron-safe tools up front so the base prompt is fenced
            # to this wake-up's actual tool set (mirrors _run_isolated).
            schemas, executor, safe_tools = _load_cron_tools(kin)
            messages = _build_messages(kin, framed_prompt, enabled_tools=safe_tools)
            staging_had_work, toolless_scopes = _inject_staging_status(
                kin, messages, safe_tools, model)
            # Per-turn memory recall (same as the cron-subprocess path above).
            # Fail-soft.
            try:
                from memory_recall import inject_into_messages
                messages, _ = inject_into_messages(
                    messages, kin,
                    num_ctx=int(cfg.get("num_ctx", 8192) or 8192), cfg=cfg)
            except Exception:
                pass
            options = {
                "temperature": cfg.get("temperature", 0.8),
                "top_p": cfg.get("top_p", 0.9),
                "top_k": cfg.get("top_k", 40),
                "min_p": cfg.get("min_p", 0.0),
                "repeat_penalty": cfg.get("repeat_penalty", 1.1),
                "presence_penalty": cfg.get("presence_penalty", 0.0),
                "frequency_penalty": cfg.get("frequency_penalty", 0.0),
                "num_ctx": cfg.get("num_ctx", 8192),
            }
            # See comment on the cron-subprocess path above: cron must
            # pass max_context_tokens or num_ctx is ignored and an
            # oversized history sails straight through to the provider.
            max_ctx = max(2048,
                          int(cfg.get("num_ctx", 8192) or 8192) - 2000)
            _kin_host = resolve_kin_ollama_host(
                cfg.get("ollama_host_name", ""))
            _max_iter = int(cfg.get("max_tool_iterations", 8) or 8)
            # Tool-loop dispatch — same shape as cron-subprocess. NOTE:
            # this branch is "Hearthkin running but inject_when_running
            # OFF" — we still don't touch conversation.json (per the
            # comment above), so any tool round-trips fire but aren't
            # persisted. The kin gets the result for THIS turn but no
            # later session sees the tool calls. Acceptable trade-off
            # for this opt-out branch.
            # (schemas/executor/safe_tools resolved above, before _build_messages.)
            # cache_ttl rides along like _run_isolated's sibling path —
            # without it the v0.5.0 1h-TTL opt-in silently downgraded
            # to the provider's 5m default on this branch (audit M-P8).
            cache_ttl = str(cfg.get("cache_ttl", "auto"))
            if schemas:
                result = llm_backend.run_tool_loop(
                    model, messages,
                    tools=schemas, tool_executor=executor,
                    options=options,
                    cache=bool(cfg.get("cache", True)),
                    cache_ttl=cache_ttl,
                    show_thinking=bool(cfg.get("show_thinking", False)),
                    max_context_tokens=max_ctx,
                    tool_result_cap=int(
                        cfg.get("tool_result_cap", 8000) or 8000),
                    kin_name=kin, surface="cron-isolated-tools",
                    max_iterations=_max_iter,
                    ollama_host=_kin_host,
                )
            else:
                result = llm_backend.chat(
                    model, messages,
                    options=options, stream=False,
                    cache=bool(cfg.get("cache", True)),
                    cache_ttl=cache_ttl,
                    show_thinking=bool(cfg.get("show_thinking", False)),
                    max_context_tokens=max_ctx,
                    kin_name=kin, surface="cron-isolated",
                    ollama_host=_kin_host,
                )
            # Outcome-based tend retry (mirrors _run_isolated).
            if (schemas and tend_retry > 0 and staging_had_work
                    and not _read_staging_fired(result)):
                from kin_persistence import load_app_prompt
                attempt = 0
                while attempt < tend_retry and not _read_staging_fired(result):
                    attempt += 1
                    retry_messages = (
                        list(messages)
                        + (getattr(result, "messages_added", None) or [])
                        + [{"role": "system",
                            "content": load_app_prompt("tend_missed_call", kin)}]
                    )
                    result = llm_backend.run_tool_loop(
                        model, retry_messages,
                        tools=schemas, tool_executor=executor,
                        options=options,
                        cache=bool(cfg.get("cache", True)),
                        cache_ttl=cache_ttl,
                        show_thinking=bool(cfg.get("show_thinking", False)),
                        max_context_tokens=max_ctx,
                        tool_result_cap=int(
                            cfg.get("tool_result_cap", 8000) or 8000),
                        kin_name=kin, surface="cron-tend-retry",
                        max_iterations=_max_iter,
                        ollama_host=_kin_host,
                    )
            raw_reply = (result.content or "").strip()
            if not raw_reply:
                _log_empty_cron_reply(
                    kin, "cron-isolated", model,
                    getattr(result, "content", None) or "")
                reply = "[no reply produced]"
            else:
                reply = raw_reply
            # This branch deliberately doesn't touch conversation.jsonl (see
            # above), so there is nowhere to persist a receipt — but the WRITES
            # still have to happen, or a tool-less kin tends into nothing on
            # every wake-up that lands here. The operator-facing line joins the
            # journal, which is the record this branch does keep.
            _tl_note, _tl_confirm = _toolless_memory_cron(
                kin, reply, safe_tools, toolless_scopes, model)
            if _tl_confirm:
                reply = reply + "\n\n" + _tl_confirm
            cron_helpers.append_journal(kin, time_label or "(no time)", prompt, reply)
            _post_cron_reply_to_telegram(
                kin, cfg, reply, result, schemas, destinations=destinations)
        except Exception as e:
            tb = traceback.format_exc()
            cron_helpers.log_cron_error(kin, type(e).__name__, f"{e}\n{tb}")
            cron_helpers.append_journal_error_marker(
                kin, time_label or "(no time)", prompt,
                f"{type(e).__name__}: {e}",
            )
            try:
                _maybe_post_telegram(
                    kin, cfg,
                    f"⚠️ Hearthkin: {kin}'s scheduled wake-up at "
                    f"{time_label or '(no time)'} didn't complete.\n\n"
                    f"Reason: {type(e).__name__}: {e}\n\n"
                    f"Logged to ~/.hearthkin/logs/cron_errors.log.",
                )
            except Exception:
                pass
            return 1
        return 0

    # Isolated mode: Hearthkin is closed (or never running). Full path —
    # we own the conversation file.
    try:
        _run_isolated(kin, cfg, time_label, prompt, tend_retry=tend_retry,
                      destinations=destinations)
    except Exception as e:
        tb = traceback.format_exc()
        cron_helpers.log_cron_error(kin, type(e).__name__, f"{e}\n{tb}")
        cron_helpers.append_journal_error_marker(
            kin, time_label or "(no time)", prompt,
            f"{type(e).__name__}: {e}",
        )
        # Telegram heads-up so the operator (away from the desktop,
        # which is the whole point of cron) sees the failure on
        # their phone instead of only discovering it later in the
        # journal or chat history. Same recipient gate as the
        # success path (_maybe_post_telegram below) — reuses the
        # function so the failure surface stays consistent.
        try:
            _maybe_post_telegram(
                kin, cfg,
                f"⚠️ Hearthkin: {kin}'s scheduled wake-up at "
                f"{time_label or '(no time)'} didn't complete.\n\n"
                f"Reason: {type(e).__name__}: {e}\n\n"
                f"Logged to ~/.hearthkin/logs/cron_errors.log.",
            )
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
