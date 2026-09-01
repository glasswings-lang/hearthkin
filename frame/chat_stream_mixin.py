"""ChatStreamMixin — extracted from hearthkin.pyw (2026-07 modularisation).

One cohesive slice of the Hearthkin frame's behavior. Mixed into the
Hearthkin(wx.Frame) class in hearthkin.pyw; every method here runs with
the frame as `self`. Moved verbatim — no logic changed.
"""
from frame_shared import (
    Path, _is_cron_user_text, agent_dir, append_agent_conversation_turn,
    append_failure_log, atomic_write_text, clean_kin_reply, cron_helpers,
    extract_inline_thinking, json,
    load_agent_config,
    load_app_prompt, load_agent_conversation, logging, now_iso, nvda_speak, re,
    resolve_kin_ollama_host, save_agent_config, save_agent_conversation,
    save_agent_conversation_preserving_externals, save_room_conversation,
    strip_self_timestamp, threading, time, wx,
)


class ChatStreamMixin:

    def _stat_conversation_mtime(self, kin_name):
        """Return the conversation.jsonl file's mtime for `kin_name`,
        or None if the file doesn't exist yet. Cheap one-syscall
        check used by the background poller to detect external
        writes (Telegram bot in shared mode, cron subprocess) since
        the desktop last loaded the file."""
        try:
            path = agent_dir(kin_name) / "conversation.jsonl"
            return path.stat().st_mtime
        except (OSError, FileNotFoundError):
            return None

    def _on_conversation_poll_tick(self, event):
        """Background mtime check. Fires every 5 seconds. If the
        active kin's conversation.jsonl was written by something
        other than this desktop process since we last loaded it,
        reload the conversation and re-paint. Skipped during
        streaming (would blow away the in-progress turn) and during
        room mode (rooms have their own conversation file)."""
        if not self.current_agent or self.current_room is not None:
            return
        if getattr(self, "_streaming", False):
            return
        if getattr(self, "_closing", False):
            return
        current_mtime = self._stat_conversation_mtime(self.current_agent)
        if current_mtime is None:
            return
        seen = getattr(self, "_conversation_mtime_seen", None)
        if seen is not None and current_mtime <= seen:
            return  # no external change
        # External change detected — reload from disk. Capture sizes
        # before/after so the user gets a visible/audible confirmation
        # that the live sync fired (per memory note: status-only fields
        # aren't tab-reachable in practice; speech is the source of
        # truth for screen-reader users).
        prev_len = len(getattr(self, "conversation", []) or [])
        try:
            self._reload_active_kin_conversation_from_disk()
        except Exception as e:
            try:
                append_failure_log(
                    "save_failures.log", self.current_agent,
                    "conversation poll reload", e,
                )
            except Exception:
                pass
            return
        new_len = len(getattr(self, "conversation", []) or [])
        delta = new_len - prev_len
        if delta > 0:
            msg = (
                f"Synced {delta} new message{'s' if delta != 1 else ''}"
                f"{self._describe_sync_source(delta)}."
            )
            try:
                self._set_status(msg)
            except Exception:
                pass
            try:
                if (self.config.get("nvda_mode") or "off") != "off":
                    nvda_speak(msg)
            except Exception:
                pass

    # source-prefix -> how to say it out loud. Every row carries where it came
    # from; this turns that into the tail of a sentence.
    _SYNC_SOURCE_WORDS = {
        "telegram": " from Telegram",
        "discord": " from Discord",
        "import": " from an import",
        "reach_out": " — a kin reached out",
        "cron": " from a scheduled wake-up",
        "room": " from a room",
    }

    def _describe_sync_source(self, delta):
        """Where did the turns that just appeared actually come from?

        This used to be the hard-coded words "from Telegram", because when the
        poller was written the Telegram bot was the only thing that could write
        to a kin's conversation behind the desktop's back. That stopped being
        true: imports, cron wake-ups, a kin's own reach_out, Discord and rooms
        all do it now. So importing 204 turns of Skype history into a kin with
        Telegram switched off, no bot token and no allow_from list announced
        "Synced 191 new messages from Telegram" — confidently, and about a
        surface that kin has never been on.

        Every row already records its own `source`. Read it instead of
        guessing. Mixed batches say nothing rather than picking a winner: a
        count with no provenance is honest, and a wrong provenance is not.

        Returns a leading-space fragment, or "" when it can't tell — the
        sentence reads correctly either way.
        """
        try:
            rows = (getattr(self, "conversation", []) or [])[-delta:]
        except Exception:
            return ""
        kinds = set()
        for r in rows:
            if not isinstance(r, dict):
                continue
            src = str(r.get("source") or "").strip()
            if src:
                kinds.add(src.split(":")[0].lower())
        if len(kinds) != 1:
            return ""
        return self._SYNC_SOURCE_WORDS.get(kinds.pop(), "")

    def _reload_active_kin_conversation_from_disk(self):
        """Reload `conversation.jsonl` for the active kin into
        `self.conversation` and re-render the chat display. Used by
        (a) the mtime poll when an external write is detected, and
        (b) the migration handler when share-with-desktop just got
        toggled on and the kin's conversation needs to reflect the
        newly migrated lines.

        Defensive about state: skipped if no kin loaded or if the
        kin is currently streaming (would lose the in-progress turn).
        Caller is expected to pre-check those conditions for any
        external trigger that has more context."""
        if not self.current_agent:
            return
        if getattr(self, "_streaming", False):
            return
        # External write means the kin's actual current state has
        # diverged from any open snapshot — clearing current_convo_file
        # stops the next auto-persist from overwriting the snapshot
        # file with the reloaded conversation (audit H15).
        self.current_convo_file = None
        fresh = load_agent_conversation(self.current_agent)
        prior_window = getattr(self, "_render_window", 0) or 0
        self.conversation = fresh
        self._persisted_msg_count = len(fresh)
        self._conversation_mtime_seen = self._stat_conversation_mtime(
            self.current_agent
        )
        # Preserve user's existing view extent across a poll-triggered
        # reload. Picking max(prior, cfg) means: if the user clicked
        # "Load older" to expand to 400, a Telegram sync doesn't drag
        # them back down to 200. Cap at total so the window never
        # claims to show more than exists.
        window_cfg = int(self.config.get("chat_history_window", 200) or 0)
        total = len(self.conversation)
        if window_cfg <= 0:
            self._render_window = total
        else:
            self._render_window = min(max(prior_window, window_cfg), total)
        self._render_conversation()
        try:
            self._refresh_load_older_button()
        except Exception:
            pass
        try:
            self._update_token_display()
        except Exception:
            pass

    def _cron_mirror_footer(self, kin_name, tool_history):
        """Tool-receipt footer for a cron-injected reply mirrored to Telegram
        from the active-kin path. Parity with the isolated cron path's footer
        (hearthkin_cron._cron_tool_receipt_footer), so a scheduled tend shows
        its receipt on Telegram even when Hearthkin is running with this kin
        active. The desktop GUI already shows the tool calls; this is the
        unfakeable signal for the operator watching only from Telegram.

          - tools fired                 -> "_used: read_staging, edit_file_"
          - tools available, none fired -> "_(no tools called)_"  (gesture)
          - no tools available          -> ""

        Gated on telegram.show_tool_summary. Built from the actual tool-loop
        round-trips, not the model's prose."""
        try:
            cfg = load_agent_config(kin_name) or {}
            tg = cfg.get("telegram") or {}
            if not tg.get("show_tool_summary", True):
                return ""
            from chat_helpers import scan_intermediate_tool_content
            _, names = scan_intermediate_tool_content(tool_history or [])
            if names:
                return "\n\n_used: " + ", ".join(names) + "_"
            from kin_persistence import load_kin_tools
            if load_kin_tools(kin_name):
                return "\n\n_(no tools called)_"
        except Exception:
            pass
        return ""

    def _maybe_mirror_to_telegram(self, kin_name, user_text, reply_text):
        """If any Telegram user has 'Mirror desktop messages to my
        Telegram chat' enabled for this kin, push the just-completed
        round-trip (user message + kin reply) to their Telegram chat
        with a "💻 (desktop)" prefix. Fires from the desktop's reply-
        completion handlers (_on_stream_done, _on_tool_loop_done,
        mini-chat finish).

        Fire-and-forget on a worker thread so a slow Telegram API
        round-trip doesn't block the UI. Failures are silent — the
        mirror is a nice-to-have, not load-bearing for either
        surface's correctness."""
        if not kin_name:
            return
        try:
            cfg = load_agent_config(kin_name)
        except Exception:
            return
        tg = (cfg or {}).get("telegram") or {}
        bot_token = (tg.get("bot_token") or "").strip()
        if not bot_token:
            return
        mirror_map = tg.get("user_mirror_to_telegram") or {}
        if not isinstance(mirror_map, dict):
            return
        targets = [str(uid) for uid, on in mirror_map.items() if on]
        if not targets:
            return
        threading.Thread(
            target=self._mirror_to_telegram_worker,
            args=(bot_token, kin_name, targets, user_text, reply_text),
            daemon=True,
        ).start()

    def _mirror_to_telegram_worker(self, bot_token, kin_name, user_ids,
                                   user_text, reply_text):
        """Worker-thread side of _maybe_mirror_to_telegram. Two
        sendMessage calls per target: one for the user's message
        (so the Telegram thread shows what was asked) and one for
        the kin's reply. For private DMs chat_id == user_id, so we
        don't need to remember a chat_id separately.

        Failures get appended to telegram_failures.log — previously
        they were silently swallowed, which made "the mirror isn't
        working" undebuggable. The log is the canonical source for
        why a mirror push didn't land.
        """
        try:
            for uid in user_ids:
                if user_text:
                    self._mirror_send_chunked(
                        bot_token, uid, kin_name,
                        f"💻 (desktop) you said:\n\n{user_text}",
                    )
                if reply_text:
                    self._mirror_send_chunked(
                        bot_token, uid, kin_name,
                        f"💻 (desktop) {kin_name} replied:\n\n{reply_text}",
                    )
        except Exception as e:
            try:
                append_failure_log(
                    "telegram_failures.log",
                    kin_name,
                    "mirror worker top-level",
                    e,
                )
            except Exception:
                pass

    def _mirror_send_chunked(self, bot_token, chat_id, kin_name, text):
        """Telegram's per-message limit is 4096 chars; split if longer.
        Failures land in telegram_failures.log with the chat_id and
        offset so a user can see why a desktop→Telegram mirror didn't
        arrive."""
        from telegram_bot import telegram_api_call
        LIMIT = 4000
        if not text:
            return
        for i in range(0, len(text), LIMIT):
            try:
                telegram_api_call(
                    bot_token, "sendMessage",
                    {"chat_id": chat_id, "text": text[i:i + LIMIT]},
                    timeout=15,
                )
            except Exception as e:
                try:
                    append_failure_log(
                        "telegram_failures.log",
                        kin_name,
                        f"mirror chat_id={chat_id} offset={i}",
                        e,
                    )
                except Exception:
                    pass

    def _notify_cron_failure(self, kin_name, user_text, reason):
        """Push a one-liner "your scheduled wake-up failed" message to
        every Telegram user with mirror-to-Telegram enabled for this
        kin. Used by the active-kin watchdog / error paths so a cron
        firing while the operator is away from the keyboard still
        produces a visible signal somewhere they'll see it (their
        phone). Without this the entire active-kin cron failure mode
        is silent — chat-display gets an error marker, but the chat
        display is on the desktop the operator isn't at.

        Tries to extract the scheduled HH:MM from the framed prompt
        so the failure message points at the specific wake-up
        ('Cron at 12:00 failed: …' rather than just 'Cron failed').
        Same recipients + bot_token resolution as
        _maybe_mirror_to_telegram so the surface stays consistent."""
        if not kin_name:
            return
        try:
            cfg = load_agent_config(kin_name)
        except Exception:
            return
        tg = (cfg or {}).get("telegram") or {}
        bot_token = (tg.get("bot_token") or "").strip()
        if not bot_token:
            return
        mirror_map = tg.get("user_mirror_to_telegram") or {}
        if not isinstance(mirror_map, dict):
            return
        targets = [str(uid) for uid, on in mirror_map.items() if on]
        if not targets:
            return
        # Pull the scheduled time out of the framed prompt for the
        # message text. cron_helpers.frame_wake_up_prompt produces
        # something like "[hearthkin: scheduled wake-up — fired at
        # 12:00 on Thursday, ...]" so a simple regex against the
        # prefix gets us the HH:MM without parsing the whole frame.
        m = re.search(r"fired at (\d{1,2}:\d{2})", user_text or "")
        time_label = m.group(1) if m else "(unknown time)"
        msg = (
            f"⚠️ Hearthkin: {kin_name}'s scheduled wake-up at "
            f"{time_label} didn't complete.\n\n"
            f"Reason: {reason}\n\n"
            f"The wake-up prompt was saved; you can re-run it from "
            f"Hearthkin's chat when you get back."
        )

        def _worker():
            for uid in targets:
                try:
                    self._mirror_send_chunked(bot_token, uid, kin_name, msg)
                except Exception:
                    pass

        threading.Thread(target=_worker, daemon=True).start()

    def _persist_current_conversation(self):
        """Save self.conversation. Always writes the kin's auto-persist file
        (`~/.hearthkin/kin/<name>/conversation.jsonl`); when the user has
        opened or saved a named snapshot (`current_convo_file` set), also
        writes that snapshot so it stays in sync. Without the second write,
        snapshots freeze at the moment they were saved and later messages
        appear lost to the user — they're in the auto-persist file but
        not in the named file the user has been watching.

        Append-only fast path: new messages since the last save get
        appended one line each (constant cost per turn). When the
        in-memory conversation got shorter than what's on disk — clear-
        chat, regen-last-turn, etc. — falls back to a full rewrite via
        save_agent_conversation. `_persisted_msg_count` tracks how many
        of the in-memory messages are already on disk.

        Side effect: keeps `_render_window` in sync with what's
        actually painted in chat_display. Live-appended turns are
        already visible via AppendText, so the window must grow to
        include them — otherwise the "(N older)" button label lies,
        claiming messages are hidden when they're sitting right there
        on screen. Clamps down too (regen / clear shrinks total)
        so the window never exceeds the actual history. Then
        refreshes the Load Older button label."""
        if not (self.current_agent and self.current_room is None):
            return
        total = len(self.conversation)
        persisted = getattr(self, "_persisted_msg_count", 0)
        # Grow the render window by ONLY the newly appended messages —
        # live appends are already painted so the window must cover
        # them, but older hidden history stays hidden. Setting the
        # window to `total` here (the old shape) permanently defeated
        # chat-history windowing after the first send (audit M-F6).
        # min(total, ...) also clamps down when total shrank
        # (clear-chat / regen).
        appended = max(0, total - persisted)
        self._render_window = min(total, max(0, self._render_window) + appended)
        try:
            self._refresh_load_older_button()
        except Exception:
            pass
        # Snapshot the file's mtime BEFORE we write. If it moved since
        # the poller last synced (_conversation_mtime_seen), an external
        # append (Telegram bot in shared mode, cron worker) landed that
        # self.conversation doesn't have yet — in that case we must NOT
        # advance _conversation_mtime_seen to the post-write value, or
        # the external lines stay invisible until the *next* external
        # write bumps mtime again (audit M-F3). Leaving the stale value
        # makes the next poll tick see mtime > seen and reload.
        pre_write_mtime = self._stat_conversation_mtime(self.current_agent)
        seen_before = getattr(self, "_conversation_mtime_seen", None)
        external_pending = (
            pre_write_mtime is not None
            and seen_before is not None
            and pre_write_mtime > seen_before
        )
        self._last_save_failed = False
        try:
            if persisted > len(self.conversation):
                # Conversation shrank (clear-chat / regen). Full
                # rewrite, but go through the preserving variant so
                # any messages appended to disk by a cron worker or
                # Telegram bot since our last save survive — without
                # this, a regen happening while a cron is mid-call
                # would silently nuke the cron's writes when the
                # cron lands its append after our rewrite. The
                # helper takes the per-kin lock, reloads disk,
                # splices externals onto the end of our in-memory
                # list, then rewrites. Update memory + count to
                # match what actually hit disk.
                merged = save_agent_conversation_preserving_externals(
                    self.current_agent, self.conversation, persisted,
                )
                if len(merged) != len(self.conversation):
                    # Externals got spliced in. Update memory so the
                    # next persist call sees the right count and the
                    # chat display can re-render with them visible.
                    self.conversation = merged
                    try:
                        self._render_conversation()
                    except Exception:
                        pass
                # The preserving variant reloaded disk under the lock
                # and spliced externals into memory — nothing pending
                # remains for the poller to pick up.
                external_pending = False
                # The desktop conversation just shrank (clear-chat / regen).
                # Pull the desktop distill bookmark back to the new length if
                # it now points past the end, so an ordinary regen lands at
                # "caught up" (no wasteful full re-distill) and a clear (new
                # length 0) drops it to 0 so the fresh conversation distills
                # from the start. Without this, the read-time heal
                # (live_distill_bookmark) would treat a regen's 1-2-turn
                # overshoot as a restart and re-distill the whole thing. Only
                # the "desktop" scope maps to this file; other scopes have
                # their own histories and bookmarks.
                try:
                    _new_len = len(self.conversation)
                    _dcfg = load_agent_config(self.current_agent) or {}
                    _doff = _dcfg.get("distill_offsets") or {}
                    if int(_doff.get("desktop", 0) or 0) > _new_len:
                        _doff["desktop"] = _new_len
                        _dcfg["distill_offsets"] = _doff
                        save_agent_config(self.current_agent, _dcfg)
                except Exception as _e:
                    append_failure_log(
                        "save_failures.log", self.current_agent,
                        "distill bookmark clamp on shrink", _e)
            else:
                for i in range(persisted, len(self.conversation)):
                    append_agent_conversation_turn(
                        self.current_agent, self.conversation[i]
                    )
            self._persisted_msg_count = len(self.conversation)
            # Capture the post-write mtime so the background poller
            # treats this as "we already know about it" and doesn't
            # try to reload the file we just wrote — UNLESS an external
            # append landed before our write (see external_pending
            # above), in which case the stale seen-value stays so the
            # poller reloads those lines on its next tick.
            if not external_pending:
                self._conversation_mtime_seen = self._stat_conversation_mtime(
                    self.current_agent
                )
        except Exception as e:
            append_failure_log("save_failures.log", self.current_agent, "auto-persist", e)
            self._set_status(f"Auto-save failed (see logs/save_failures.log): {e}")
            self._last_save_failed = True
        if self.current_convo_file:
            try:
                data = {
                    "agent": self.current_agent,
                    "model": self._current_chat_model_clean(),
                    "saved_at": now_iso(),
                    "messages": self.conversation,
                }
                atomic_write_text(
                    self.current_convo_file,
                    json.dumps(data, indent=2, ensure_ascii=False),
                )
            except Exception as e:
                append_failure_log(
                    "save_failures.log",
                    self.current_agent,
                    f"snapshot:{Path(self.current_convo_file).name}",
                    e,
                )
                self._set_status(
                    f"Snapshot save failed (see logs/save_failures.log): {e}"
                )
                self._last_save_failed = True

    def _on_stream_done(self, gen):
        if self._closing:
            return
        if gen != self._stream_id:
            return
        self._cancel_cold_start_timer()
        self._cancel_still_waiting_timer()
        self._cancel_stream_watchdog_timer()
        raw_reply = self._stream_buf
        user_text = self._stream_user_text
        thinking = self._think_buf
        paint_cursor = self._paint_cursor
        self._stream_buf = ""
        self._stream_user_text = ""
        self._think_buf = ""
        self._paint_cursor = 0
        self._streaming = False

        cfg = self.agent_cfg if self.current_agent else {}
        show_thinking = cfg.get("show_thinking", False)
        feed_thinking = cfg.get("feed_thinking", False)

        # Pull any inline <thinking>...</thinking> markup out of content
        # and merge it into the structured thinking field. Normalizes the
        # shape on disk so show_thinking / feed_thinking / recent_thinking
        # all work the same regardless of whether the model used the
        # reasoning channel or leaked into content. See
        # chat_helpers.extract_inline_thinking for the design rationale.
        raw_reply, thinking = extract_inline_thinking(raw_reply, thinking)

        # Log thinking regardless of display settings
        if thinking:
            self._log(f"THINKING: {thinking}")

        if not raw_reply.strip():
            # Try to salvage from the tool-loop's intermediate content
            # before falling back to "[no reply produced]". Same shape
            # as the Telegram handlers (commit f82287c): Haiku-4.5 +
            # side-action tools like `note` often produce substantive
            # content alongside a tool_call then ~2 EOS tokens after
            # the tool result. The intermediate IS the kin's reply.
            from chat_helpers import (
                scan_intermediate_tool_content,
                strip_tool_summary_footer,
            )
            intermediate, tool_names = scan_intermediate_tool_content(
                self._pending_tool_history)
            salvaged_content = ""
            if intermediate:
                candidate, _drop = extract_inline_thinking(intermediate, "")
                candidate = strip_self_timestamp(candidate)
                candidate = strip_tool_summary_footer(candidate)
                candidate = candidate.strip()
                if candidate:
                    salvaged_content = candidate
            model_name = self._current_chat_model_clean()
            if salvaged_content:
                # Surface the salvaged content as if it were the reply
                # the model meant to send. Display in chat normally
                # and persist as the assistant turn. A system note
                # gets spliced in below so the kin's next read knows
                # what happened.
                self.chat_display.AppendText(salvaged_content + "\n")
                self._maybe_speak_sentence(salvaged_content)
                reply_to_save = salvaged_content
                self._salvaged_intermediate = True
                self._salvaged_tool_names = tool_names
                self._log_empty_reply(
                    self.current_agent or "?",
                    f"{model_name} [salvaged]", raw_reply)
            else:
                empty_marker = "[no reply produced]"
                self.chat_display.AppendText(empty_marker + "\n")
                reply_to_save = empty_marker
                self._salvaged_intermediate = False
                self._salvaged_tool_names = []
                self._log_empty_reply(
                    self.current_agent or "?", model_name, raw_reply)
                # Surface + SPEAK it — a silent kin otherwise just looks like
                # nothing happened to a screen-reader user.
                _silent_msg = f"{self.current_agent or 'The kin'} gave no reply."
                self._set_status(_silent_msg, speak=True)
        else:
            # Normal path — non-empty final content. No salvage state
            # to track. Reset the flags so a future empty turn's
            # bookkeeping doesn't see stale state from this one.
            self._salvaged_intermediate = False
            self._salvaged_tool_names = []
            # Flush whatever wasn't painted in-stream (typically the last partial
            # sentence without trailing punctuation), then close the block. Must
            # happen BEFORE the reasoning section — otherwise live-painted
            # sentences land above the reasoning and the unpainted tail lands
            # below it, splitting the reply in two with reasoning sandwiched
            # between the halves.
            remainder = raw_reply[paint_cursor:]
            if remainder:
                self.chat_display.AppendText(remainder)
                # Speak the unpainted tail too — it didn't end on a
                # sentence boundary, but it's still part of the
                # reply the user wants to hear.
                self._maybe_speak_sentence(remainder)
            self.chat_display.AppendText("\n")
            # Strip any leading "[YYYY-MM-DD HH:MM]" prefix the kin
            # echoed from the timestamp-grounding context the user/
            # assistant turns are prefixed with. The block header above
            # already shows the timestamp; the echoed prefix is visible
            # duplication. Strip ONLY the leading run — mid-message
            # references to specific timestamps (e.g. "you said at
            # [2026-05-18 07:09]") are legitimate and pass through. The
            # in-stream paint above may have briefly shown the prefix
            # before this strip, but the stored content and any future
            # reload will be clean, breaking the kin's "my prior
            # replies all open with timestamps" feedback loop.
            # Full cleanup, not just the timestamp. This path used to strip
            # the timestamp alone, which was defensible while desktop
            # context held no speaker names for a model to imitate. It does
            # now: imported multi-party history puts "[Name] " in front of
            # user turns (see _history_entry_for_model), which is exactly
            # the material that teaches a small model to write other
            # people's turns. clean_kin_reply owns the order and reports
            # impersonation rather than fixing it silently.
            reply_to_save, _imp = clean_kin_reply(
                raw_reply, self.current_agent or "",
                known_speakers=self._other_speakers_in_history())
            if _imp:
                logging.getLogger("hearthkin").warning(
                    "IMPERSONATION (desktop): %s opened its reply as somebody "
                    "else. The tag was stripped, but the body is likely still "
                    "in that voice. Check whether an import filed another "
                    "person's turns under this kin — "
                    "scripts/audit_speaker_slots.py finds those.",
                    self.current_agent)

        # Reasoning block goes after the complete reply (gray italic). Earlier
        # versions placed it before, which broke under sentence-by-sentence
        # streaming — see the splitting comment above.
        if thinking and show_thinking:
            display_text = f"\n{'─' * 40}\n💭 Reasoning:\n{thinking}\n{'─' * 40}\n"
            start_pos = self.chat_display.GetLastPosition()
            self.chat_display.AppendText(display_text)
            end_pos = self.chat_display.GetLastPosition()
            self.chat_display.SetStyle(start_pos, end_pos, self._reasoning_style)

        ts = now_iso()
        # User turn is already in self.conversation + persisted —
        # _send_message did that at send time so a hung stream can't
        # lose the message. Just guard against the (impossible?)
        # case where _user_turn_persisted is False, in which case
        # we fall back to the old behavior.
        if not getattr(self, "_user_turn_persisted", False):
            self.conversation.append({"role": "user", "content": user_text, "ts": ts})
        self._user_turn_persisted = False
        # Splice in any tool-call round-trip turns from a tool-loop reply
        # (empty for the pure-streaming path). They sit between the user
        # message and the final assistant reply so the model sees its own
        # past tool calls on subsequent turns. Stamp each with the same ts
        # as the surrounding pair for reload-order stability.
        # Capture the round-trips before clearing — the cron mirror footer
        # (below) needs to know which tools actually fired this turn.
        _mirror_tool_history = list(self._pending_tool_history)
        for tool_msg in self._pending_tool_history:
            tool_msg.setdefault("ts", ts)
            self.conversation.append(tool_msg)
        self._pending_tool_history = []
        # Persist reasoning ALWAYS (when the model produced any), so the
        # kin's record is complete and tools like `recent_thinking` can
        # surface it on request. Whether it's sent back to the model on
        # subsequent turns is decoupled — that's `feed_thinking`'s job,
        # applied at request-build time in _history_entry_for_model.
        # Truncate at persist time per the per-kin cap to keep file
        # size bounded; the cap controls record verbosity AND subsequent-
        # turn cost in one knob.
        assistant_msg = {"role": "assistant", "content": reply_to_save, "ts": ts}
        if thinking:
            cap = int(cfg.get("think_max_chars", 1200) or 0)
            if cap > 0 and len(thinking) > cap:
                thinking = thinking[:cap] + "\n... [reasoning truncated]"
            assistant_msg["thinking"] = thinking
        self.conversation.append(assistant_msg)
        # When the assistant turn was salvaged from a tool-loop's
        # intermediate content (model returned empty after a tool
        # result), append a system note explaining what happened so
        # the kin's next read sees the gap honestly. Mirror the
        # Telegram salvage-path system note shape.
        if getattr(self, "_salvaged_intermediate", False):
            tool_names = getattr(self, "_salvaged_tool_names", []) or []
            self.conversation.append({
                "role": "system",
                "content": load_app_prompt(
                    "salvage_note", self.current_agent).replace(
                        "{tools}", ", ".join(tool_names) or "(none)"),
                "ts": ts,
            })
            self._salvaged_intermediate = False
            self._salvaged_tool_names = []
        # Authoring bridge: if the kin authored a file in its natural text
        # register (a ```write:<path>``` fence or a *writes X* emote + fence)
        # instead of emitting a write_file call it froze on, perform the write
        # now and append a confirming system note. Appends to self.conversation
        # before persist so the note is saved with the turn. See
        # authoring_bridge.py.
        self._maybe_run_authoring_bridge(reply_to_save, ts, _mirror_tool_history)
        # Reading nudge: if the kin narrated reading CONTENT (not presence)
        # without actually loading it, and nothing was auto-attached this turn,
        # tell it plainly that narrating a read loads nothing. See reading_bridge.
        self._maybe_nudge_read_gesture(reply_to_save, ts, _mirror_tool_history)
        # Park bridge: a `> command` line in the reply runs for real, same as
        # on Telegram and on a cron wake-up. Before this the desktop was the
        # one surface that read the line and did nothing with it.
        self._maybe_route_park_command(reply_to_save, ts, user_text)
        self._persist_current_conversation()
        # A cron wake-up that took the live-injection path still owes the
        # daily journal an entry — the kin's reply IS the entry, and on the
        # isolated path hearthkin_cron writes it. Stash set by
        # _on_cron_timer_tick just before it handed the turn over. POPPED, so
        # a turn can never be journalled twice and a stale stash can never
        # attach itself to a later turn; guarded, because a journal that
        # can't be written must never disturb a reply that already landed.
        _cron_j = getattr(self, "_pending_cron_journal", None)
        self._pending_cron_journal = None
        if _cron_j and _is_cron_user_text(user_text):
            try:
                if _cron_j.get("kin") == self.current_agent:
                    cron_helpers.append_journal(
                        _cron_j["kin"],
                        _cron_j.get("time_label") or "(no time)",
                        _cron_j.get("prompt") or "",
                        reply_to_save,
                    )
            except Exception as e:
                append_failure_log(
                    "cron_errors.log", self.current_agent,
                    "journal_live_path", e)
        self._log(f"MODEL: {reply_to_save}")

        # Mirror the round-trip to any Telegram users who've opted
        # in to "mirror desktop messages to my Telegram chat" for
        # this kin. No-op when no users are opted in or when there's
        # no bot token. Fire-and-forget on a worker thread.
        try:
            mirror_reply = reply_to_save
            # On a cron-injected wake-up, append the tool-receipt footer so the
            # operator watching from Telegram can tell a real tend from a
            # gesture — the same signal the isolated cron path posts.
            if _is_cron_user_text(user_text):
                mirror_reply = reply_to_save + self._cron_mirror_footer(
                    self.current_agent, _mirror_tool_history)
            self._maybe_mirror_to_telegram(
                self.current_agent, user_text, mirror_reply,
            )
        except Exception:
            pass

        # Memory: tally desktop-scope messages for this kin
        if self.current_agent:
            key = (self.current_agent, "desktop")
            self._messages_since_distill[key] = (
                self._messages_since_distill.get(key, 0) + 1
            )
            dlg = self._dialog_for(self.current_agent)
            if dlg is not None:
                try:
                    dlg._refresh_chat_counters_display()
                except Exception:
                    pass

        self.send_btn.Enable()
        self.stop_btn.Disable()
        # Don't overwrite an auto-save failure message with the generic
        # "Ready." — that message points to the failure log and the user
        # needs to see it. On the normal success path, "Ready (auto-saved)"
        # gives ongoing reassurance that saves are happening.
        if not getattr(self, "_last_save_failed", False):
            self._set_status("Ready (auto-saved).")

        mode = self.config.get("nvda_mode", "off")
        if mode == "short":
            nvda_speak("Reply ready.")
        elif mode == "full":
            # "Full reply" reads the whole reply aloud once, at completion.
            nvda_speak(reply_to_save)
        # "Streaming" mode ("stream") instead reads the reply live via
        # _maybe_speak_sentence (sentence-by-sentence as it arrives for a
        # tool-less kin; the whole reply in one call for a tool kin, until
        # the tool-loop streaming keystone lands) — so it deliberately does
        # NOT read reply_to_save here (that path would double-speak). See
        # _maybe_speak_sentence route 2.
        # Audible "reply complete" cue — high tone, slightly longer
        self._chime("done")
        self._update_token_display()

        # After the UI has updated, check whether to fire auto-distillation
        if self.current_agent:
            wx.CallAfter(self._maybe_auto_distill, self.current_agent)

    def _show_cold_start_hint(self, gen):
        """Update the Activity field to "(still loading…)" when 8
        seconds elapse without a first reply chunk, and speak "Still
        loading" once via NVDA. Replaces the old behavior of painting
        a "[Still waiting for the first reply token…]" block into the
        chat transcript — that was log clutter for screen-reader users
        re-reading conversation history, and a focused Activity field
        + spoken phase carries the same information without polluting
        the chat record.

        Guarded by gen so a stale timer from a previous turn that's
        been stopped (clear-chat, mode switch) doesn't fire.

        Provider-agnostic — cold-start happens everywhere:
          - Ollama: model unloaded from VRAM after OLLAMA_KEEP_ALIVE
            (5 min default); reload of a big-param model + KV cache
            for big num_ctx can be 30-60s before the first token.
          - OpenRouter (Anthropic / OpenAI / etc.): cold TLS
            handshake, provider routing, prompt-cache write on first
            turn (Anthropic cache TTL is 5 min — same shape as
            Ollama's keep-alive, coincidentally), or transient
            provider-side load.
          - Both: SSE connection establishes but no tokens emitted
            for a while (CLAUDE.md flags this as a real failure mode
            for the OpenRouter path).
        """
        if gen != self._stream_id:
            return
        # Activity-line update — sighted users see "(still loading…)"
        # without having to look anywhere else. Uses the same
        # _set_status hook everything else does (with auto-revert
        # after 4s, then re-fires through the revert path until the
        # first chunk lands — but the chunk handlers cancel this
        # timer, so the transient message naturally falls off when
        # the model starts producing output).
        self._set_status(
            "(still loading… cold-start can take 30-60s for idle "
            "models or first-call provider routing)"
        )
        # Speak once via NVDA — the spoken-phase guard treats this as
        # its own phase name so it won't fire twice if the timer somehow
        # ran more than once, and a real first chunk that follows will
        # still announce "Thinking" or "Typing" because the phase name
        # differs.
        self._speak_status_phase("Still loading")

    def _cancel_cold_start_timer(self):
        """Cancel the pending cold-start hint timer if it hasn't fired
        yet. Safe to call multiple times / when no timer exists."""
        t = getattr(self, "_cold_start_timer", None)
        if t is not None:
            try:
                if t.IsRunning():
                    t.Stop()
            except Exception:
                pass
            self._cold_start_timer = None

    def _start_still_waiting_timer(self, my_gen, elapsed_ms=0):
        """Schedule the next 'still waiting' status update. First call
        in a turn passes elapsed_ms=0 → fires 60s later. Subsequent
        self-reschedules pass the current elapsed value → fires 30s
        later, advancing the counter.

        Each tick announces elapsed time in the status field if no
        chunks have arrived yet — gives the operator a visible signal
        that the model is still being given time (large prefills on
        local Ollama legitimately take many minutes). Cancelled
        whenever any chunk arrives or the stream completes / errors
        / hits the watchdog."""
        # Stamp the turn's start on the first call of the turn so the
        # Activity field's in-flight line can show elapsed seconds (see
        # _compose_in_flight_status). monotonic so a clock change mid-turn
        # can't make elapsed go negative.
        if elapsed_ms == 0:
            self._stream_started_at = time.monotonic()
        delay_ms = 60000 if elapsed_ms == 0 else 30000
        self._still_waiting_timer = wx.CallLater(
            delay_ms,
            self._on_still_waiting_tick,
            my_gen,
            elapsed_ms + delay_ms,
        )

    def _on_still_waiting_tick(self, my_gen, elapsed_ms):
        """Periodic 'still waiting' status update. No-op if the stream
        has produced chunks (output is flowing → operator can see
        what's happening already) or if a newer turn has started."""
        self._still_waiting_timer = None
        if my_gen != self._stream_id:
            return
        if getattr(self, "_stream_chunks_seen", 0) > 0:
            return
        # Use the same composer as the 4s in-flight refresh so the wording
        # stays consistent no matter which timer last touched the field.
        self._set_status(self._compose_in_flight_status())
        # Reschedule for the next tick.
        self._start_still_waiting_timer(my_gen, elapsed_ms)

    def _cancel_still_waiting_timer(self):
        """Cancel the pending still-waiting status timer. Called from
        every stream-completion path AND from the chunk handlers (the
        first arriving chunk means we don't need to nag anymore)."""
        t = getattr(self, "_still_waiting_timer", None)
        if t is not None:
            try:
                if t.IsRunning():
                    t.Stop()
            except Exception:
                pass
            self._still_waiting_timer = None

    def _on_stream_heartbeat(self, my_gen):
        """SSE keepalive from OpenRouter — the connection is alive, no
        new data this beat. Bumps the watchdog's chunk-count so a
        stalled-but-connected provider doesn't trip the no-chunks
        timer, but doesn't paint anything (no content) and doesn't
        announce a phase change (no behavior to surface)."""
        if self._closing:
            return
        if my_gen != self._stream_id:
            return
        self._stream_chunks_seen = getattr(self, "_stream_chunks_seen", 0) + 1

    def _compute_watchdog_timeout_ms(self, model, agent_cfg):
        """Return the watchdog timeout in milliseconds for a stream
        starting now. Picks the value from three sources, in priority:

          1. Per-kin override (`watchdog_timeout_minutes` in agent_cfg).
             A positive integer wins. 0 / missing means "auto."
          2. OpenRouter heuristic: 5 minutes flat. Network / provider
             hangs are short-deadline; if there's no output in 5 min
             the stream is dead, not slow.
          3. Ollama heuristic (local OR remote — the bottleneck is the
             same: CPU prefill on a large prompt): base 5 min plus
             1 min per 8k of num_ctx above 8k, capped at 30 min.

        Why scale by num_ctx for Ollama: prefill (reading the prompt
        into the model's attention state) happens BEFORE the first
        output token, and its cost scales with prompt length. At 90%
        CPU on a 9-10 GB model, a 65k prefill genuinely takes ~10 min,
        regardless of whether anything is "wrong." The original fixed
        5-min watchdog was right for OpenRouter network hangs and
        wrong for slow local prefill; this helper fixes that without
        making the operator tune anything per-kin in the common case.
        """
        BASE_MIN = 5
        CAP_MIN = 30
        # Per-kin override always wins if set.
        override = 0
        if agent_cfg:
            try:
                override = int(agent_cfg.get("watchdog_timeout_minutes", 0) or 0)
            except (TypeError, ValueError):
                override = 0
        if override > 0:
            return max(BASE_MIN, override) * 60 * 1000
        # OpenRouter: fixed base.
        if isinstance(model, str) and model.startswith("openrouter/"):
            return BASE_MIN * 60 * 1000
        # Ollama: scale by num_ctx.
        num_ctx = 8192
        if agent_cfg:
            try:
                num_ctx = int(agent_cfg.get("num_ctx", 8192))
            except (TypeError, ValueError):
                pass
        extra_8k_blocks = max(0, (num_ctx - 8192) // 8192)
        minutes = min(CAP_MIN, BASE_MIN + extra_8k_blocks)
        return minutes * 60 * 1000

    def _cancel_stream_watchdog_timer(self):
        """Cancel the pending streaming watchdog. Called from every
        stream-completion path (done, error, stop) so a normal
        completion doesn't trigger the hang-recovery later."""
        t = getattr(self, "_stream_watchdog_timer", None)
        if t is not None:
            try:
                if t.IsRunning():
                    t.Stop()
            except Exception:
                pass
            self._stream_watchdog_timer = None

    def _on_stream_watchdog_fire(self, gen_at_start):
        """Streaming watchdog. Fires 5 minutes after _send_message
        if no chunks (content OR thinking) ever arrived for that
        stream generation. The symptoms it catches: model hang,
        network drop with no error propagation, OS sleep mid-stream,
        anything that leaves _streaming wedged True with the UI
        showing 'Sending…' and the Stop button as the only option.

        Stale-fire guard: if the user has already started a new
        stream by the time we fire, gen_at_start won't match
        self._stream_id and we no-op.

        Recovery: paint a [no response — possible hang] marker,
        clear the streaming flag, re-enable Send. Logs the event to
        ~/.hearthkin/logs/streaming_hangs.log so the underlying
        cause is visible later."""
        if gen_at_start != self._stream_id:
            return  # newer turn already in flight
        # If chunks DID arrive, it's not a hang — let the normal
        # completion path handle it.
        if getattr(self, "_stream_chunks_seen", 0) > 0:
            self._stream_watchdog_timer = None
            return
        # Not a hang if WE are the reason nothing is coming back. Ollama
        # answers one request at a time, so a distillation, a scheduled
        # wake-up or a heartbeat on the same daemon leaves this turn queued
        # and silent — a distillation bite routinely runs thirteen minutes,
        # and the shortest watchdog window here is five. Observed: a turn
        # sent at 04:47:51 was declared hung at 04:52:51 to the second, while
        # the model was working steadily on something else the app had asked
        # for. Painting "[no response — possible hang]" over a healthy queued
        # turn is worse than waiting: it costs the reply, and it teaches the
        # person that sending at the wrong moment loses their message, which
        # is the kind of doubt that stops someone writing at all.
        #
        # So hold off and re-arm for another full window, as many times as
        # needed. The stop button stays live throughout, the Activity line
        # says what is being waited on, and the moment the background work
        # clears, the ordinary timeout applies again — a genuine hang is
        # still caught, just not one we caused.
        holding = ""
        try:
            holding = self._own_background_on_the_model()
        except Exception:
            holding = ""
        if holding:
            try:
                self._log(f"watchdog held off: {holding} has the model; "
                          f"this turn is queued, not hung")
            except Exception:
                pass
            # Re-arm for the SAME window this turn was given, rather than
            # recomputing it: the room path sets this timer too, with a
            # different kin's config than self.agent_cfg, and a re-arm that
            # quietly changed the budget by which path it came from would be
            # its own small mystery.
            minutes = max(1, int(getattr(self, "_stream_watchdog_minutes", 5) or 5))
            self._stream_watchdog_timer = wx.CallLater(
                minutes * 60000, self._on_stream_watchdog_fire, gen_at_start,
            )
            return
        # Real hang. Force-clear and surface.
        self._stream_watchdog_timer = None
        self._cancel_cold_start_timer()
        self._cancel_still_waiting_timer()
        self._stream_id += 1   # invalidate any late chunks from this turn
        self._streaming = False
        # Snapshot user_text BEFORE we clear it so we can detect a
        # cron-injected wake-up and notify Telegram opt-in users —
        # otherwise the operator has zero signal that the scheduled
        # wake-up silently failed (cron's whole point is firing while
        # nobody's at the keyboard).
        hung_user_text = self._stream_user_text
        self._stream_buf = ""
        self._stream_user_text = ""
        self._think_buf = ""
        self._paint_cursor = 0
        # Rooms also need a recovery here, otherwise the auto-round
        # loop would still think a kin is mid-stream and refuse to
        # advance. _room_active is the room-path equivalent of
        # _streaming; clearing it lets the user press Continue or send
        # again. _room_auto_mode off because cascading auto-rounds on
        # a hung connection is the worst possible follow-up.
        self._room_active = False
        self._room_auto_mode = False
        if self.current_room is not None:
            # Make Continue actually work as the recovery path:
            # _on_continue requires _room_paused, which normally only
            # _finish_round sets — and the hung round never reached
            # _finish_round. Without these two lines the room was
            # unresumable until the user typed a new message
            # (audit M-F7).
            self._room_paused = True
            try:
                self.continue_btn.Enable(True)
            except Exception:
                pass
        # Compute the model + provider category up-front so both the
        # user-facing chat message and the log entry can use them.
        elapsed_min = getattr(self, "_stream_watchdog_minutes", 5)
        model_clean = (
            getattr(self, "_current_room_model", "") or
            self._current_chat_model_clean() or "?"
        )
        is_openrouter = isinstance(model_clean, str) and model_clean.startswith("openrouter/")
        # Provider-aware message text — names the actual cause space
        # rather than the generic "check ollama / network." The OpenRouter
        # case really is "provider hung or network died." The Ollama case
        # is usually "model is still prefilling a huge prompt on CPU,
        # which the watchdog cut off."
        if is_openrouter:
            chat_msg = (
                f"\n[no response from {model_clean} after {elapsed_min} "
                f"minutes. The provider or network may have hung. Your "
                f"message is preserved; try sending again. If this keeps "
                f"happening on this model, the provider may be down — try "
                f"a different model. See logs/streaming_hangs.log.]\n"
            )
            status_msg = (
                f"Stream timed out after {elapsed_min} min (provider hang "
                f"or network drop). Your message is saved; try again."
            )
        else:
            chat_msg = (
                f"\n[no response from Ollama after {elapsed_min} minutes. "
                f"If you're running a large model mostly on CPU at a high "
                f"num_ctx, prefill (reading the prompt) can take longer "
                f"than the watchdog allows. Your message is preserved; "
                f"try sending again. To wait longer per attempt, raise "
                f"Watchdog timeout in Settings → Model && generation. "
                f"See logs/streaming_hangs.log.]\n"
            )
            status_msg = (
                f"Stream timed out after {elapsed_min} min (likely slow "
                f"prefill on a large context). Your message is saved; "
                f"try again or raise the per-kin Watchdog timeout."
            )
        try:
            self.chat_display.AppendText(chat_msg)
        except Exception:
            pass
        try:
            self.send_btn.Enable()
            self.stop_btn.Disable()
            self._set_status(status_msg, speak=True)
        except Exception:
            pass
        try:
            from kin_persistence import LOGS_DIR
            log_path = LOGS_DIR / "streaming_hangs.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            # Identify the speaker correctly in both surfaces — in room
            # mode current_agent is None but _current_room_speaker has
            # the active kin name. Without this fix, room hangs logged
            # "kin=None" which was useless for diagnosis.
            speaker = self.current_agent or getattr(self, "_current_room_speaker", None) or "?"
            surface = "room" if self.current_room else "1on1"
            room_name = self.current_room or "-"
            with log_path.open("a", encoding="utf-8") as f:
                f.write(
                    f"{now_iso()} surface={surface} room={room_name} "
                    f"kin={speaker} model={model_clean} "
                    f"reason=no_chunks_in_{elapsed_min}min\n"
                )
        except Exception:
            pass
        try:
            nvda_speak(f"Stream timed out after {elapsed_min} minutes. Send button is enabled.")
        except Exception:
            pass
        # If the hung turn was a cron wake-up, mirror a failure
        # one-liner to Telegram opt-in users — the active-kin cron
        # path otherwise has zero Telegram signal on hang (mirror
        # only fires from _on_stream_done, which never ran). This
        # is the bug that bit a kin's cron turn on 2026-05-28.
        if self.current_agent and _is_cron_user_text(hung_user_text):
            try:
                self._notify_cron_failure(
                    self.current_agent, hung_user_text,
                    "stream hung for 5 minutes — no chunks arrived",
                )
            except Exception:
                pass

    def _on_stream_error(self, gen, msg):
        if gen != self._stream_id:
            return
        self._cancel_cold_start_timer()
        self._cancel_still_waiting_timer()
        self._cancel_stream_watchdog_timer()
        partial = self._stream_buf
        unpainted = partial[self._paint_cursor:]
        # Snapshot before clearing — see watchdog-fire comment for why.
        failed_user_text = self._stream_user_text
        self._stream_buf = ""
        self._stream_user_text = ""
        self._think_buf = ""
        self._paint_cursor = 0
        self._streaming = False
        # User turn was persisted at send time — already in
        # self.conversation + on disk. Just clear the flag so the
        # next turn starts clean.
        self._user_turn_persisted = False
        if unpainted:
            self.chat_display.AppendText(unpainted)
        self.chat_display.AppendText(f"\n[error: {msg}]\n")
        self.send_btn.Enable()
        self.stop_btn.Disable()
        from chat_helpers import humanize_error
        try:
            _host = resolve_kin_ollama_host(
                self.agent_cfg.get("ollama_host_name", "")) if self.agent_cfg else None
        except Exception:
            _host = None
        human = humanize_error(msg, kin=self.current_agent, host=_host or None)
        self._set_status(human, speak=True)
        # Cron failure path — same justification as the watchdog fire.
        if self.current_agent and _is_cron_user_text(failed_user_text):
            try:
                self._notify_cron_failure(
                    self.current_agent, failed_user_text, msg,
                )
            except Exception:
                pass

    def _announce_stopped(self):
        """Audio confirmation that a reply was cut off mid-generation. Escape
        (or the Stop button) only ever wrote VISUAL feedback — '[stopped]' in
        the transcript, 'Stopped.' in the status line — so an NVDA user pressing
        Escape mid-reply got total silence and no way to know the stop even
        registered (they'd sit waiting for an answer that was never coming).
        Speak it. Gated on nvda_mode so a sighted user, who has the visual
        markers, isn't spoken at."""
        try:
            if self.config.get("nvda_mode", "off") != "off":
                nvda_speak("Stopped")
        except Exception:
            pass

    def _on_stop(self, event):
        # Cancel any pending cold-start hint timer so it doesn't paint
        # after the user explicitly stopped the turn.
        self._cancel_cold_start_timer()
        self._cancel_still_waiting_timer()
        self._cancel_stream_watchdog_timer()
        # Cut off any voice playback in flight. The engine drops queued
        # sentences and stops the audio stream immediately. No-op if
        # the kin doesn't have voice on or nothing's playing.
        try:
            if getattr(self, "_voice_engine", None) is not None:
                self._voice_engine.stop_speaking()
        except Exception:
            pass
        # Room mode has its own stop semantics — cancel current kin's stream, end round
        if self.current_room is not None:
            if not self._streaming and not self._room_active:
                return
            self._stream_id += 1
            self._streaming = False
            partial = self._stream_buf
            unpainted = partial[self._paint_cursor:]
            self._stream_buf = ""
            self._think_buf = ""
            self._paint_cursor = 0
            if partial:
                if unpainted:
                    self.chat_display.AppendText(unpainted)
                self.chat_display.AppendText("\n[stopped]\n")
                ts = now_iso()
                # Apply the FULL anti-impersonation cleanup before saving
                # the partial reply. The previous code only stripped the
                # leading timestamp, which meant a stopped-mid-stream reply
                # that had leaked "[OtherKin]:" or "[OwnKin]:" prefixes
                # got persisted with the impersonation intact — feeding
                # the pattern back into the next turn's context and making
                # the next reply MORE likely to impersonate. The four
                # passes mirror _on_room_kin_done.
                # Extract inline <thinking>...</thinking> markup BEFORE
                # the impersonation strip — keeps stored content clean
                # and prevents the markup pattern from being saved into
                # the partial (which would prime the next turn). Stop-
                # path doesn't have access to the structured thinking
                # buffer (the stream may have ended mid-reasoning), so
                # this only operates on whatever's in `partial`.
                cleaned_partial, _extracted = extract_inline_thinking(partial, "")
                cleaned_partial, _imp = clean_kin_reply(
                    cleaned_partial, self._current_room_speaker)
                if _imp:
                    logging.getLogger("hearthkin").warning(
                        "IMPERSONATION (room, stopped mid-stream): %s opened its "
                        "reply as another kin. Tag stripped, but the body is "
                        "likely still in the other kin's voice. Since the room "
                        "history builder moved foreign kin to the user slot, "
                        "this should be unreachable — if you are reading this, "
                        "the attractor is back. See "
                        "docs/design/multi-kin-rooms-shared-history.md",
                        self._current_room_speaker)
                self.room_conversation.append({
                    "role": "assistant",
                    "content": cleaned_partial,
                    "ts": ts,
                    "speaker": self._current_room_speaker,
                    "model": self._current_room_model,
                })
                try:
                    save_room_conversation(self.current_room, self.room_conversation)
                except Exception as e:
                    append_failure_log(
                        "save_failures.log",
                        self.current_room or "?",
                        "save_room_conversation (stop partial)",
                        e,
                    )
            else:
                self.chat_display.AppendText("\n[stopped]\n")
            # Force-close the current round (don't run remaining members)
            self._room_active = False
            self._room_round_index = len(self._room_active_order)
            self._room_auto_mode = False
            if self._auto_timer is not None:
                self._auto_timer.Stop()
            self.auto_check.SetValue(False)
            self._finish_round()
            self._announce_stopped()
            return

        # A park turn keeps running after the reply that started it has landed,
        # so by here `_streaming` is already False and the early return below
        # would make Stop do nothing at all — which is the "nothing stops it
        # but quitting" shape this app keeps closing. Bumping the generation IS
        # the stop signal the park loop reads (see _start_park_turn._stale), and
        # it also cuts short the model call in flight via should_stop.
        if not self._streaming and (getattr(self, "_park_workers", None) or set()):
            self._stream_id += 1
            self.chat_display.AppendText("\n[stopped]\n")
            self.send_btn.Enable()
            self.stop_btn.Disable()
            self._set_status("Stopped.")
            self._announce_stopped()
            return

        if not self._streaming:
            return
        self._stream_id += 1
        self._streaming = False

        partial = self._stream_buf
        user_text = self._stream_user_text
        unpainted = partial[self._paint_cursor:]
        self._stream_buf = ""
        self._stream_user_text = ""
        self._think_buf = ""
        self._paint_cursor = 0

        if partial:
            if unpainted:
                self.chat_display.AppendText(unpainted)
            self.chat_display.AppendText("\n[stopped]\n")
            ts = now_iso()
            # User turn is already persisted (saved at send time so
            # a hang doesn't eat the message) — only need to add the
            # partial assistant reply.
            if not getattr(self, "_user_turn_persisted", False):
                self.conversation.append({"role": "user", "content": user_text, "ts": ts})
            self._user_turn_persisted = False
            # Extract inline <thinking>...</thinking> markup before
            # persisting the partial — same treatment the room stop
            # path got (audit M-F10). Persisting raw markup primes the
            # next turn's format-attractor. Extracted reasoning is
            # kept on the turn's thinking field so the record stays
            # complete.
            cleaned_partial, extracted_think = extract_inline_thinking(partial, "")
            cleaned_partial = strip_self_timestamp(cleaned_partial)
            stopped_msg = {"role": "assistant", "content": cleaned_partial, "ts": ts}
            if extracted_think:
                stopped_msg["thinking"] = extracted_think
            self.conversation.append(stopped_msg)
            self._persist_current_conversation()
            self._log(f"MODEL (partial, stopped): {partial}")
        else:
            self.chat_display.AppendText("\n[stopped]\n")
            # No partial reply — user message already saved at send
            # time, so it's preserved across the stop. Clear the flag
            # so the next turn doesn't think there's a stale persisted
            # user turn waiting.
            self._user_turn_persisted = False

        self.send_btn.Enable()
        self.stop_btn.Disable()
        self._set_status("Stopped.")
        self._announce_stopped()
        self._update_token_display()

    def _on_regen(self, event):
        if self._streaming:
            return
        # A salvaged-empty-reply turn ends with a trailing role=system
        # note (_on_stream_done appends it after the assistant turn).
        # Pop it first so the turn beneath can regenerate — otherwise
        # a salvaged turn was permanently un-regenerable (audit L-B22).
        if (self.conversation
                and self.conversation[-1].get("role") == "system"
                and str(self.conversation[-1].get("content") or "").startswith(
                    "[hearthkin: your post-tool reply")):
            self.conversation.pop()
        if not self.conversation or self.conversation[-1].get("role") != "assistant":
            self._set_status("Nothing to regenerate (last message wasn't a reply).")
            return
        # Pop the final assistant reply plus any preceding tool round-trip
        # turns (assistant-with-tool_calls / tool-result) back to but not
        # including the user message that started the turn. Without this,
        # the intermediates would survive as orphans referencing a tool
        # call from a turn we just regenerated away — the model on the
        # next call would see broken-shape history.
        self.conversation.pop()
        while (self.conversation
               and self.conversation[-1].get("role") in ("tool", "assistant")):
            self.conversation.pop()
        if not self.conversation or self.conversation[-1].get("role") != "user":
            self._set_status("Nothing to regenerate.")
            return
        popped = self.conversation.pop()
        last_user = popped.get("content", "")
        # Carry the original turn's image attachments through to the
        # regenerated request. Without this, regen of an image turn
        # would send text-only and the kin would lose the image
        # context. The files are still on disk in attachments/, so
        # we pass refs (not the staged-attachment file-picker flow).
        regen_atts = popped.get("attachments") if isinstance(popped.get("attachments"), list) else None
        self._render_conversation()
        self._persist_current_conversation()
        self._send_message(last_user, regen_attachment_refs=regen_atts)

    def _on_edit_message(self, event):
        """Chat -> Edit a message... (Ctrl+E). Open a dialog listing the
        past turns in the currently-active conversation (1-on-1 or room),
        let the user rewrite one in place, persist, re-render.

        Scope: user + assistant turns only. Tool-result and system-note
        turns are skipped so an edit can't shear off a tool round-trip
        or mangle framework bookkeeping. Emptying the text field is
        rejected — deleting a message is a different action (add
        later if needed); this one only edits.

        Rationale: local models occasionally produce a garbage turn
        (hallucinated pseudo-tracebacks, format-attractor spam) that
        then poisons every subsequent turn's context. Before this
        existed the only remedy was Clear chat (nukes the whole
        history) or hand-editing conversation.jsonl (needs Hearthkin
        closed to avoid autosave clobber)."""
        if self._streaming:
            self._set_status("Wait for the current reply to finish first.")
            return
        if self.current_room is not None:
            source = self.room_conversation
            surface = "room"
        elif self.current_agent:
            source = self.conversation
            surface = "1on1"
        else:
            self._set_status("Open a kin or a room first.")
            return
        if not source:
            self._set_status("Nothing to edit yet.")
            return
        # Editable set: user + assistant turns with a content string.
        # Skip tool-result messages (editing those breaks the round-trip)
        # and system notes (framework bookkeeping — e.g. rolling-window
        # markers, salvage traces — not things a user should be rewriting).
        editable = [
            (idx, msg) for idx, msg in enumerate(source)
            if msg.get("role") in ("user", "assistant")
        ]
        if not editable:
            self._set_status("No editable messages in this conversation.")
            return

        from dialogs import EditMessageDialog
        dlg = EditMessageDialog(self, editable)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            idx = dlg.get_selected_index()
            new_text = dlg.get_new_text()
        finally:
            dlg.Destroy()

        if idx is None or idx < 0 or idx >= len(source):
            return
        # Reject an empty save — deleting a message isn't what this
        # dialog does. Without this an accidental Ctrl+A / Delete / OK
        # would silently blank the turn.
        if not new_text.strip():
            self._set_status(
                "Message can't be empty. Cancel out and use Clear chat "
                "if you want to remove history.")
            return
        # Preserve the original message's fields (ts, speaker, model,
        # tool_calls, attachments...) — only content changes. An
        # assistant-with-tool_calls turn keeps its tool_calls; the
        # following role=tool results still reference the same call ids.
        source[idx]["content"] = new_text

        if surface == "room":
            try:
                save_room_conversation(self.current_room, self.room_conversation)
            except Exception as e:
                append_failure_log(
                    "save_failures.log",
                    self.current_room or "?",
                    "save_room_conversation (edit message)",
                    e,
                )
                self._set_status("Save failed — see save_failures.log.")
                return
        else:
            # Full rewrite, not append-only: the edit changed a message
            # in place so length is unchanged and the fast-path append
            # branch of _persist_current_conversation would leave the
            # edit invisible on disk. Going through save_agent_conversation
            # directly, then reconciling the persist bookkeeping so the
            # next auto-save on a new turn behaves normally.
            try:
                save_agent_conversation(self.current_agent, self.conversation)
                self._persisted_msg_count = len(self.conversation)
                self._conversation_mtime_seen = self._stat_conversation_mtime(
                    self.current_agent)
            except Exception as e:
                append_failure_log(
                    "save_failures.log",
                    self.current_agent or "?",
                    "save_agent_conversation (edit message)",
                    e,
                )
                self._set_status("Save failed — see save_failures.log.")
                return

        try:
            self._render_conversation()
        except Exception:
            pass
        self._set_status("Message updated.")

    def _on_clear(self, event):
        if self._streaming or self._room_active:
            self._set_status("Stop the current reply first.")
            return
        if self.current_room is not None:
            if not self.room_conversation:
                return
            dlg = wx.MessageDialog(
                self,
                f"Wipe the conversation history in room '{self.current_room}'? This cannot be undone.",
                "Confirm",
                wx.YES_NO | wx.CANCEL | wx.NO_DEFAULT | wx.ICON_WARNING,
            )
            r = dlg.ShowModal()
            dlg.Destroy()
            if r != wx.ID_YES:
                return
            self.room_conversation = []
            self._room_round_count = 0
            self._room_auto_count = 0
            try:
                save_room_conversation(self.current_room, self.room_conversation)
            except Exception as e:
                append_failure_log(
                    "save_failures.log",
                    self.current_room or "?",
                    "save_room_conversation (clear)",
                    e,
                )
            self.chat_display.Clear()
            self._update_round_label()
            self._set_status(f"Room '{self.current_room}' history cleared.")
            return
        if self.conversation:
            dlg = wx.MessageDialog(self, "Clear the current conversation?",
                                   "Confirm", wx.YES_NO | wx.CANCEL | wx.NO_DEFAULT)
            r = dlg.ShowModal()
            dlg.Destroy()
            if r != wx.ID_YES:
                return
        self.conversation.clear()
        self.chat_display.Clear()
        self.current_convo_file = None
        self._persist_current_conversation()
        self._update_token_display()
        self._set_status("Conversation cleared.")
