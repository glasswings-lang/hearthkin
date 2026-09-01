"""KinMgmtMixin — extracted from hearthkin.pyw (2026-07 modularisation).

One cohesive slice of the Hearthkin frame's behavior. Mixed into the
Hearthkin(wx.Frame) class in hearthkin.pyw; every method here runs with
the frame as `self`. Moved verbatim — no logic changed.
"""
from frame_shared import (
    AGENTS_DIR, APP_NAME, AgentNameDialog, CONFIG_FILE, _num_ctx_of,
    _replace_name_in_kin_files, _scan_name_occurrences_in_kin, agent_dir,
    append_failure_log, append_model_history, atomic_write_json, clone_agent,
    create_agent, cron_helpers,
    datetime, delete_agent, json, list_agents, list_rooms, llm_backend, load_agent_config,
    load_agent_conversation, load_memory, load_soul, nvda_speak, rebuild_listbox,
    rename_kin_in_rooms, resolve_kin_ollama_host, save_agent_config,
    strip_model_annotation, threading, urllib, validate_kin_name, wx,
)


class KinMgmtMixin:

    # --- Agent management --- #

    def _refresh_kin_list(self, select=None):
        """Populate the header's kin combo with kin names. The combo is hidden
        in room mode, so no sentinel is needed.

        When `select` is given, that kin is selected (used after rename /
        create to land on the new name). When `select` is None, the
        previously-selected kin is preserved by name — important after
        delete or out-of-band refresh so NVDA's focus doesn't drop back
        to the first kin in the list every time.
        """
        if not hasattr(self, "agent_choice"):
            return
        kin = list_agents()
        prev_idx = self.agent_choice.GetSelection()
        if select is None and prev_idx >= 0:
            try:
                saved_key = self.agent_choice.GetString(prev_idx)
            except Exception:
                saved_key = None
        else:
            saved_key = select
        if not kin:
            self.agent_choice.Set(kin)
            self.agent_choice.SetSelection(wx.NOT_FOUND)
            return
        rebuild_listbox(
            self.agent_choice, kin,
            keys=kin, saved_key=saved_key, saved_index=prev_idx,
        )

    def _refresh_room_list(self, select=None):
        """Populate the header's room combo with room names. Selection-
        preserving — see _refresh_kin_list docstring."""
        if not hasattr(self, "room_choice"):
            return
        rooms = list_rooms()
        prev_idx = self.room_choice.GetSelection()
        if select is None and prev_idx >= 0:
            try:
                saved_key = self.room_choice.GetString(prev_idx)
            except Exception:
                saved_key = None
        else:
            saved_key = select
        if not rooms:
            self.room_choice.Set(rooms)
            self.room_choice.SetSelection(wx.NOT_FOUND)
            return
        rebuild_listbox(
            self.room_choice, rooms,
            keys=rooms, saved_key=saved_key, saved_index=prev_idx,
        )

    def _refresh_agent_list(self, select=None):
        self._refresh_kin_list(select=select)

    def _load_agent(self, name):
        """Switch active context to a single-kin chat with `name`. Loads the
        kin's config, refreshes the model picker in the header, renders the
        auto-persisted conversation, and switches the mode radio to Kin."""
        # Leaving a room: stop any in-flight stream and persist conversation
        self._exit_room_mode()
        # If we're leaving a previous kin and they have unsaved messages and
        # auto-distill-on-close is on, kick off distillation in the background.
        if self.current_agent and self.current_agent != name:
            self._maybe_distill_on_close(self.current_agent)
        # Drop any staged attachment from the previous kin — the
        # file-picker absolute path is OK to clear (file still on
        # disk, user can re-attach), but the webcam-rel-path slot
        # points at the PREVIOUS kin's attachments/ dir and would
        # try to reference a file the NEW kin doesn't own. Same
        # logic applies to file-picker staging too; carrying it
        # across an explicit kin switch is surprising UX.
        if self.current_agent and self.current_agent != name:
            self._pending_attachment = None
            self._pending_attachment_rel = None
            if hasattr(self, "attached_label") and self.attached_label is not None:
                self.attached_label.SetValue("")
                self.attached_label.Hide()
            if hasattr(self, "clear_attach_btn") and self.clear_attach_btn is not None:
                self.clear_attach_btn.Hide()
        # Invalidate any in-flight single-kin reply for the kin we're
        # LEAVING before we repoint current_agent / self.conversation.
        # A reply worker (streaming OR tool-loop) runs on a background
        # thread; its completion callbacks are gated only by
        # self._stream_id, NOT by which kin is active. Without bumping the
        # id here, a reply that started as the old kin — most damagingly a
        # cron tend that read the old kin's staging and edited the old
        # kin's files — runs its completion against the NEWLY loaded kin,
        # appending the old kin's transcript into the new kin's
        # conversation.jsonl (one kin's tend landing in another kin's
        # history when the desktop is switched mid-loop). Every
        # other teardown path (stop / regen / close / watchdog) already
        # bumps the id; kin-switch was the gap. The worker thread runs to
        # completion harmlessly — its tool side effects already went to the
        # correct (old) kin's own files; only the now-stale completion,
        # which would have contaminated the new kin, is dropped.
        if self.current_agent and self.current_agent != name:
            self._stream_id += 1
            if self._streaming:
                self._streaming = False
                self._cancel_stream_watchdog_timer()
                self._cancel_cold_start_timer()
                self._cancel_still_waiting_timer()
                self._stream_buf = ""
                self._stream_user_text = ""
                self._think_buf = ""
                self._paint_cursor = 0
        self._loading_agent = True
        try:
            self.current_agent = name
            self.agent_cfg = load_agent_config(name)

            # Point the shared Ollama-probe machinery (capability /api/show,
            # ctx length, model preload, model listing — everything that
            # resolves the host via _OLLAMA_HOST_OVERRIDE rather than an
            # explicit per-call host) at THIS kin's machine. The chat send
            # paths pass their host explicitly and don't depend on this;
            # this is purely so the synchronous capability probes on the
            # switch path hit the kin's reachable daemon instead of a dead
            # localhost (which otherwise stalls the switch).
            try:
                llm_backend.set_ollama_host(resolve_kin_ollama_host(
                    self.agent_cfg.get("ollama_host_name", "")))
            except Exception:
                pass

            # Snapshot the active model for swap-warning comparisons.
            # (The chat model dropdown moved to the Settings dialog —
            # nothing in the header to repopulate here anymore. The
            # active model comes from cfg, which is the source of truth.)
            self._active_model = strip_model_annotation(
                self.agent_cfg.get("model", "") or ""
            )

            # Auto-persisted conversation: load from disk, render. Defer to
            # _render_conversation so tool round-trip turns are handled in
            # one place rather than duplicating the walk here.
            self.conversation = load_agent_conversation(name)
            # Snapshot the conversation file's mtime right after load so
            # the background poller can spot external writes (Telegram
            # bot in shared mode, cron, etc.) and reload without
            # double-counting our own writes.
            self._conversation_mtime_seen = self._stat_conversation_mtime(name)
            # Cache soul + memory now so _update_token_display doesn't
            # hit disk on every input keystroke. Refreshed by
            # _invalidate_kin_text_cache when EditKinDialog saves
            # either file.
            self._soul_cache = load_soul(name) or ""
            self._memory_cache = load_memory(name) or ""
            # Everything we just loaded is already on disk — the append
            # path uses this to decide which messages to write next.
            self._persisted_msg_count = len(self.conversation)
            # Compute initial render window. 0 in config = render
            # everything (legacy). Otherwise cap at the configured
            # value, but never exceed actual history length.
            window_cfg = int(self.config.get("chat_history_window", 200) or 0)
            if window_cfg <= 0:
                self._render_window = len(self.conversation)
            else:
                self._render_window = min(window_cfg, len(self.conversation))
            self._render_conversation()
            self.current_convo_file = None
            self._update_token_display()

            # Mode radio + selectors reflect the new context
            if hasattr(self, "mode_kin_radio"):
                self._mode_set_kin(True)
                self._apply_mode_visibility()
            self._refresh_kin_list(select=name)
            # Talk button visibility tracks per-kin voice setting.
            self._refresh_talk_button_visibility()
            # Attach Image button enabled state tracks the kin's
            # current model's vision capability.
            self._refresh_attach_button_state()

            self.config["last_agent"] = name
            self.config["last_target_kind"] = "kin"
            atomic_write_json(CONFIG_FILE, self.config)
            self.SetTitle(f"{APP_NAME} — {name}")
            self._set_status(f"Loaded kin: {name}")
            # Optional Ollama preload — fires a background warm-up so
            # the model is already loaded by the time the user sends
            # their first message. Per-kin opt-in (preload_on_switch);
            # no-op for OpenRouter kin and for kin that didn't opt in.
            self._maybe_preload_ollama_model()
        finally:
            self._loading_agent = False

    def _maybe_preload_ollama_model(self):
        """Fire a background /api/chat with model-only on the active
        kin's Ollama model to start loading it into memory. By the
        time the user hits Send, the model's already warm.

        No-op when:
        - The kin's model is an OpenRouter-routed model (preload only
          matters for Ollama; OR models don't have a 'load' step).
        - The kin's `preload_on_switch` config is off (default).
        - We're mid-shutdown (self._closing).

        Fire-and-forget: we don't surface success or failure to the
        UI. If Ollama is down or the model name is wrong, the user
        will find out the same way they would have anyway — on their
        first send."""
        if self._closing:
            return
        cfg = self.agent_cfg or {}
        if not bool(cfg.get("preload_on_switch", False)):
            return
        model = strip_model_annotation(cfg.get("model", "") or "").strip()
        if not model:
            return
        if llm_backend._is_openrouter_model(model):
            return
        # The keep_alive AND num_ctx on the warm-up call must both match
        # what the chat path will use. Ollama keys a loaded instance by
        # (model, num_ctx) — a model loaded at one context size is a
        # DIFFERENT resident instance from the same model at another. If
        # we warm at Ollama's default context but the kin chats at, say,
        # 64k, the first real send finds no matching instance and pays a
        # full cold-load anyway — defeating the entire point of preload.
        # So we warm at the kin's actual num_ctx (empty messages => load
        # only, no prefill cost) and with the kin's keep_alive window.
        keep_alive = llm_backend._coerce_keep_alive(cfg.get("keep_alive", ""))
        num_ctx = _num_ctx_of(cfg)

        def worker():
            try:
                from llm_backend import _resolve_ollama_host
                host = _resolve_ollama_host()
                body = {
                    "model": model,
                    "messages": [],
                    "options": {"num_ctx": num_ctx},
                }
                if keep_alive is not None:
                    body["keep_alive"] = keep_alive
                req = urllib.request.Request(
                    host + "/api/chat",
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                # Generous timeout — a cold-load of a 26B model can
                # take 30-60s and we want the load to complete, not
                # time out partway through and leave Ollama mid-load
                # when the user's first real send arrives.
                with urllib.request.urlopen(req, timeout=120) as resp:
                    resp.read()
            except Exception:
                # Silent — preload is best-effort. The first real send
                # will produce a clear error if Ollama isn't reachable.
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _on_kin_selected_event(self, event):
        choice = self.agent_choice.GetValue()
        if not choice:
            return
        if choice == self.current_agent and self.current_room is None:
            return
        self._load_agent(choice)

    def _on_room_selected_event(self, event):
        if not hasattr(self, "room_choice"):
            return
        choice = self.room_choice.GetValue()
        if not choice:
            return
        if choice == self.current_room:
            return
        self._load_room(choice)

    def _name_collides_with_operator(self, name):
        """True when `name` is the operator's own name (Preferences →
        your name), compared case-insensitively.

        Not a validation failure — a kin may legitimately share the
        operator's name, and nothing on disk breaks. What breaks is
        attribution, in two specific places:

          * Rooms. Room history reaches each kin as
            `assistant: [Name]: content`, and the operator's own turns
            are prefixed `[<user_name>] `. Same string for both means
            the model receives two speakers under one label with no way
            to separate them — the identity-convergence risk named in
            docs/design/multi-kin-rooms-shared-history.md, and the
            reason the anti-impersonation chain exists.

          * Distillation. `_infer_operator_name` only derives the
            operator's name when there's exactly one unique non-kin
            speaker. Collide the two and it finds none, and per
            importers/_marker.py the summarizer then falls back to the
            marker prefix and starts writing "[hearthkin] did X" about
            the operator's own past.

        A one-to-one chat is unaffected: the `[<user_name>] ` prefix is
        built only on the room path, so plain chat turns carry no name
        label to collide.
        """
        mine = (self.config.get("user_name", "") or "").strip()
        return bool(mine) and name.strip().lower() == mine.lower()

    def _confirm_operator_name_reuse(self, name):
        """Warn about an operator/kin name collision. True to proceed."""
        mine = (self.config.get("user_name", "") or "").strip()
        answer = wx.MessageBox(
            f"'{name}' is your own name.\n\n"
            f"In a room, your turns and this kin's turns would both be "
            f"labelled '{mine}', and there'd be no way for them to tell "
            f"which of you said what. Memory would lose track the same "
            f"way.\n\n"
            f"A one-to-one chat is fine — this only bites in rooms and "
            f"in memory.\n\n"
            f"You can give them a different name here and still call them "
            f"'{mine}' in their soul file. Only the label has to differ.\n\n"
            f"Use '{name}' anyway?",
            "That's your name too",
            wx.YES_NO | wx.ICON_QUESTION | wx.NO_DEFAULT,
        )
        return answer == wx.YES

    def _on_new_agent(self, event):
        dlg = AgentNameDialog(self, title="New kin", show_skip_option=True)
        if dlg.ShowModal() == wx.ID_OK:
            name = dlg.get_name()
            skip_identity = dlg.get_skip_identity_setup()
            # Reject path-hostile / reserved names BEFORE touching disk
            # (audit SH4 — agent_dir(name) is a raw path join).
            name_problem = validate_kin_name(name) if name else ""
            if not name:
                wx.MessageBox("Kin name cannot be empty.", "Error", wx.OK | wx.ICON_WARNING)
            elif name_problem:
                wx.MessageBox(
                    f"Can't use that name: {name_problem}",
                    "Error", wx.OK | wx.ICON_WARNING,
                )
            elif (self._name_collides_with_operator(name)
                  and not self._confirm_operator_name_reuse(name)):
                pass  # operator backed out; leave the dialog's name alone
            elif not create_agent(name, blank_soul=skip_identity):
                wx.MessageBox(f"A kin named '{name}' already exists.", "Error", wx.OK | wx.ICON_WARNING)
            else:
                self._refresh_agent_list(select=name)
                self._load_agent(name)
                if skip_identity:
                    self._set_status(
                        f"Created kin: {name} (no identity defined). Talk to them; "
                        f"open their Settings later to crystallize a soul if you want."
                    )
                    # Skip the Settings dialog — let identity emerge through chat.
                else:
                    self._set_status(f"Created kin: {name}. Edit their soul to introduce them.")
                    # Open the Settings dialog so the user can immediately edit the soul
                    self._on_edit_kin(None)
        dlg.Destroy()

    def _on_clone_agent(self, event):
        if not self.current_agent:
            return
        dlg = AgentNameDialog(
            self,
            title=f"Clone '{self.current_agent}'",
            initial_name=f"{self.current_agent}_copy",
            prompt="Name for the cloned kin:",
        )
        if dlg.ShowModal() == wx.ID_OK:
            new_name = dlg.get_name()
            if not new_name:
                wx.MessageBox("Name cannot be empty.", "Error", wx.OK | wx.ICON_WARNING)
            elif (AGENTS_DIR / new_name).exists():
                wx.MessageBox(f"'{new_name}' already exists.", "Error", wx.OK | wx.ICON_WARNING)
            else:
                # clone_agent now returns a list of human-readable
                # reset summaries (empty list = clean clone with
                # nothing to reset; None = failure). Surface the list
                # both in the status bar AND in a confirmation dialog
                # so the user can't miss what got stripped — bot
                # tokens / exec allowlists / cron entries getting
                # silently cloned was the bug this release fixes.
                reset_items = clone_agent(self.current_agent, new_name)
                if reset_items is None:
                    wx.MessageBox(
                        f"Couldn't clone '{self.current_agent}' to "
                        f"'{new_name}' — see the logs folder for "
                        f"details.",
                        "Clone failed",
                        wx.OK | wx.ICON_ERROR,
                    )
                else:
                    src_name = self.current_agent
                    self._refresh_agent_list(select=new_name)
                    self._load_agent(new_name)
                    if reset_items:
                        reset_summary = "; ".join(reset_items)
                        self._set_status(
                            f"Cloned {src_name} → {new_name}. "
                            f"Reset: {reset_summary}."
                        )
                        wx.MessageBox(
                            f"Cloned '{src_name}' to '{new_name}'.\n\n"
                            f"For safety, the following per-deployment "
                            f"settings were NOT copied to the clone "
                            f"and have been reset to defaults:\n\n"
                            f"• " + "\n• ".join(reset_items) + "\n\n"
                            f"If the clone needs any of these, "
                            f"configure them deliberately in Settings.",
                            "Clone created",
                            wx.OK | wx.ICON_INFORMATION,
                        )
                    else:
                        self._set_status(f"Cloned to: {new_name}")
        dlg.Destroy()

    def _cleanup_per_kin_state(self, name):
        """Drop every per-kin internal state entry keyed by this kin
        (singular name keys or tuple keys starting with the name) so
        a deleted kin doesn't leave stale counters / queues behind
        that a future same-named kin would inherit (audit H4)."""
        for d in (self._distilling, self._distill_queue,
                  self._last_consolidation_at,
                  getattr(self, "_distill_threads", {}),
                  getattr(self, "_distill_dead_since", {}),
                  getattr(self, "_distill_progress", {})):
            d.pop(name, None)
        for attr in ("_messages_since_distill", "_walking_from_start"):
            d = getattr(self, attr, None)
            if isinstance(d, dict):
                for k in list(d.keys()):
                    if isinstance(k, tuple) and k and k[0] == name:
                        d.pop(k, None)

    def _migrate_per_kin_state(self, old_name, new_name):
        """Re-key per-kin internal state entries from old_name to
        new_name on rename so distillation counters and queues
        survive the rename instead of silently resetting (audit H5)."""
        for d in (self._distilling, self._distill_queue,
                  self._last_consolidation_at,
                  getattr(self, "_distill_threads", {}),
                  getattr(self, "_distill_dead_since", {}),
                  getattr(self, "_distill_progress", {})):
            if old_name in d:
                d[new_name] = d.pop(old_name)
        for attr in ("_messages_since_distill", "_walking_from_start"):
            d = getattr(self, attr, None)
            if isinstance(d, dict):
                for k in list(d.keys()):
                    if isinstance(k, tuple) and k and k[0] == old_name:
                        d[(new_name,) + k[1:]] = d.pop(k)

    def _on_rename_agent(self, event):
        if not self.current_agent:
            return
        dlg = AgentNameDialog(self, title="Rename kin", initial_name=self.current_agent)
        if dlg.ShowModal() == wx.ID_OK:
            new_name = dlg.get_name()
            # Same validation as _on_new_agent — a rename writes
            # through agent_dir(new_name) too (audit SH4).
            name_problem = validate_kin_name(new_name) if new_name else ""
            if not new_name:
                wx.MessageBox("Kin name cannot be empty.", "Error", wx.OK | wx.ICON_WARNING)
            elif new_name == self.current_agent:
                pass
            elif name_problem:
                wx.MessageBox(
                    f"Can't use that name: {name_problem}",
                    "Error", wx.OK | wx.ICON_WARNING,
                )
            elif (AGENTS_DIR / new_name).exists():
                wx.MessageBox(f"A kin named '{new_name}' already exists.", "Error", wx.OK | wx.ICON_WARNING)
            elif (self._name_collides_with_operator(new_name)
                  and not self._confirm_operator_name_reuse(new_name)):
                pass  # operator backed out of the rename
            else:
                old_name = self.current_agent
                old_bot = self.bots.pop(old_name, None)
                was_running = old_bot is not None and old_bot.is_running()
                if old_bot is not None:
                    old_bot.stop()
                try:
                    agent_dir(old_name).rename(agent_dir(new_name))
                except Exception as e:
                    # The rename didn't happen — the kin still lives
                    # under the old name. Restore the bot we just
                    # popped/stopped (fresh start if it was running),
                    # log, and surface. Without this guard a Windows
                    # PermissionError (AV scan, file still held by the
                    # bot thread) escaped the handler with the bot
                    # gone — silent under pythonw (audit M-F5).
                    append_failure_log(
                        "save_failures.log", old_name,
                        f"rename kin to '{new_name}'", e,
                    )
                    if old_bot is not None:
                        self.bots[old_name] = old_bot
                        if was_running:
                            try:
                                self._start_bot_for(old_name)
                            except Exception:
                                pass
                    msg = (f"Rename failed: {e} — the kin is unchanged. "
                           f"See logs/save_failures.log.")
                    self._set_status(msg)
                    try:
                        nvda_speak(msg)
                    except Exception:
                        pass
                    dlg.Destroy()
                    return
                # Re-key in-memory per-kin state (distill counters,
                # walk-from-start flags, etc.) so the renamed kin keeps
                # its tallies instead of silently resetting (audit H5).
                self._migrate_per_kin_state(old_name, new_name)
                # Rewrite room membership lists. Without this, every room
                # that had the kin under the old name silently routes turns
                # to a non-existent folder — the renamed kin never gets a
                # turn in the room. Unconditional (no user prompt): a stale
                # member name has no legitimate use case, unlike old-name
                # occurrences in soul.md which can be intentional.
                rooms_updated = rename_kin_in_rooms(old_name, new_name)
                # After dir rename, offer to update old-name occurrences in
                # soul.md and memory.md. Whole-word + case-sensitive scan.
                # User can decline if the old name is intentionally referenced
                # as a name origin / past identity / quoted phrase.
                occurrences = _scan_name_occurrences_in_kin(new_name, old_name)
                replaced = 0
                if occurrences:
                    total = sum(occurrences.values())
                    files_str = ", ".join(f"{f} ({n})" for f, n in occurrences.items())
                    answer = wx.MessageBox(
                        (
                            f"Found {total} occurrence{'s' if total != 1 else ''} "
                            f"of '{old_name}' in {files_str}.\n\n"
                            f"Replace with '{new_name}'?\n\n"
                            f"Whole-word, case-sensitive only. Choose No if any "
                            f"are intentional references to the old name."
                        ),
                        "Update name in soul/memory?",
                        wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION,
                    )
                    if answer == wx.YES:
                        replaced = _replace_name_in_kin_files(new_name, old_name, new_name)
                self._refresh_agent_list(select=new_name)
                self._load_agent(new_name)
                if was_running:
                    self._start_bot_for(new_name)
                # Build a status message that names each side effect so
                # the user can verify the rename touched what they expected.
                parts = [f"Renamed to: {new_name}"]
                if rooms_updated:
                    parts.append(
                        f"updated {len(rooms_updated)} room"
                        f"{'s' if len(rooms_updated) != 1 else ''}"
                    )
                if replaced > 0:
                    parts.append(
                        f"updated {replaced} occurrence"
                        f"{'s' if replaced != 1 else ''} in soul/memory"
                    )
                if len(parts) > 1:
                    self._set_status(f"{parts[0]} ({', '.join(parts[1:])})")
                else:
                    self._set_status(parts[0])
        dlg.Destroy()

    def _on_delete_agent(self, event):
        if not self.current_agent:
            return
        # Note: delete_agent() removes the whole agent_dir which already includes
        # conversation.json, memory.md, etc. — no extra cleanup needed there,
        # but Windows scheduled tasks live OUTSIDE the kin folder and need
        # explicit cleanup (see schtasks_sync_kin call below).
        dlg = wx.MessageDialog(
            self,
            f"Delete kin '{self.current_agent}'? This removes their soul, config, and history. It cannot be undone.",
            "Delete kin",
            wx.YES_NO | wx.CANCEL | wx.NO_DEFAULT | wx.ICON_WARNING,
        )
        if dlg.ShowModal() == wx.ID_YES:
            name = self.current_agent
            old_bot = self.bots.pop(name, None)
            if old_bot is not None:
                old_bot.stop()
            # Tear down Windows scheduled tasks before the kin folder
            # is removed. Without this, every daily cron fire after
            # deletion writes a "missing_kin: no agent dir" line to
            # cron_errors.log forever — a deleted kin can keep firing
            # daily for weeks afterwards. Passing an empty
            # cron_entries list makes schtasks_sync_kin delete all
            # 32 slot tasks and recreate none. No-op on non-Windows.
            try:
                if cron_helpers.schtasks_supported():
                    cron_helpers.schtasks_sync_kin(name, [])
            except Exception:
                pass
            delete_agent(name)
            # Drop in-memory per-kin state so a recreated same-named
            # kin doesn't inherit stale counters (audit H4).
            self._cleanup_per_kin_state(name)
            self.current_agent = None
            agents = list_agents()
            self._refresh_agent_list()
            if agents:
                self._load_agent(agents[0])
            else:
                self.chat_display.Clear()
                self.conversation.clear()
                self.SetTitle(APP_NAME)
                self._set_status(f"Deleted kin: {name}. No kin remain.")
        dlg.Destroy()

    def _on_save_soul(self, event):
        """Menu shortcut for Save Soul: opens the Settings dialog so the user
        can edit and save (and any pending edits get saved on close)."""
        if not self.current_agent:
            self._set_status("No kin loaded.")
            return
        self._on_edit_kin(event)

    # --- Param changes --- #
    #
    # The kin's chat model lives in self.agent_cfg["model"] (source of
    # truth) and is edited via the Settings dialog's model section. The
    # dialog calls back into self._change_kin_model() for the audit /
    # warning flow when the user commits a new model. The frame no
    # longer has its own model dropdown widget or related handlers
    # (_on_model_text_changed / _on_model_committed / _on_browse_openrouter)
    # — those moved into the dialog as of 2026-05-13.

    def _confirm_model_swap(self, current, new_clean, compat_notes=None):
        """Custom confirm dialog for model swaps with a 'don't show again'
        checkbox AND a compatibility-findings section. Returns
        (keep, suppress_future) — keep is whether the swap should go
        through, suppress_future is whether to disable the routine
        voice-change warning for the rest of the session and across
        future launches.

        `compat_notes` is the list returned by
        `compat.analyze_kin_for_target`. When non-empty, findings are
        rendered in a tab-reachable read-only multi-line TextCtrl above
        the suppress checkbox so NVDA users can navigate each finding.
        Blockers raise the wording urgency but don't disable Continue —
        the operator may know something the check doesn't."""
        compat_notes = compat_notes or []
        has_blocker = any(n.is_blocker() for n in compat_notes)
        dlg = wx.Dialog(self, title="Model change")

        intro_lines = [
            "Changing this kin's model can change their voice.",
            "",
            f"From: {current or '(none)'}",
            f"To:   {new_clean}",
            "",
            "It also changes who writes this kin's memory. Notes written",
            "before today keep the old model's habits.",
            "",
            "Recorded in this kin's model_history.md.",
        ]
        msg = wx.StaticText(dlg, label="\n".join(intro_lines))

        findings_field = None
        if compat_notes:
            sev_label = {
                "blocker": "BLOCKER",
                "warning": "WARNING",
                "info": "Info",
            }
            lines = []
            for n in compat_notes:
                lines.append(f"[{sev_label.get(n.severity, n.severity.upper())}] {n.title}")
                # Indent the detail body for readability.
                for d_line in (n.detail or "").splitlines() or [""]:
                    lines.append(f"    {d_line}")
                if n.action_hint:
                    lines.append(f"    {n.action_hint}")
                lines.append("")
            findings_text = "\n".join(lines).rstrip()
            # Tab-reachable focusable read-only TextCtrl so NVDA can
            # arrow through every finding line. Single-line StaticText
            # would not give NVDA navigation beyond a one-shot read.
            findings_label = wx.StaticText(dlg, label="&Compatibility findings:")
            findings_field = wx.TextCtrl(
                dlg, value=findings_text,
                style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP,
                size=(560, 200),
            )

        suppress_chk = wx.CheckBox(
            dlg,
            label=(
                "&Don't show the voice-change warning again "
                "(uncheck via Preferences if you want it back)"
            ),
        )
        # Phrase the action button to reflect urgency when blockers exist.
        yes_label = (
            "&Continue with swap anyway" if has_blocker
            else "&Continue with swap"
        )
        yes_btn = wx.Button(dlg, wx.ID_YES, label=yes_label)
        no_btn = wx.Button(dlg, wx.ID_NO, label="Ca&ncel")
        no_btn.SetDefault()

        yes_btn.Bind(wx.EVT_BUTTON, lambda evt: dlg.EndModal(wx.ID_YES))
        no_btn.Bind(wx.EVT_BUTTON, lambda evt: dlg.EndModal(wx.ID_NO))

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(msg, flag=wx.ALL, border=12)
        if findings_field is not None:
            sizer.Add(findings_label, flag=wx.LEFT | wx.RIGHT, border=12)
            sizer.Add(findings_field,
                      proportion=1,
                      flag=wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND,
                      border=12)
        sizer.Add(suppress_chk, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.AddStretchSpacer()
        btn_row.Add(no_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(yes_btn)
        sizer.Add(btn_row, flag=wx.EXPAND | wx.ALL, border=12)
        dlg.SetSizerAndFit(sizer)
        dlg.Centre()

        r = dlg.ShowModal()
        suppress = suppress_chk.GetValue()
        dlg.Destroy()
        return (r == wx.ID_YES), suppress

    def _change_kin_model(self, new_model):
        """Swap the current kin's model. Shows a warning so the user knows
        voice can change, writes a one-line audit entry to model_history.md,
        and updates config + UI on confirm. Reverts UI on cancel.

        The warning dialog is gated by the app-level `warn_on_model_swap`
        config flag — useful the first time, noise after. A "don't show
        again" checkbox on the dialog flips the flag off (per-app, not
        per-kin) when the user dismisses with Yes."""
        if not self.current_agent:
            return False
        current = self._active_model
        new_clean = strip_model_annotation(new_model)
        if new_clean == current:
            return False
        # Pre-flight compatibility check — surfaces any cross-provider
        # differences that the operator should know about (context cap,
        # tool support, image support, caching, tool-id format, etc.).
        # See compat.py for the catalog. Findings are always shown when
        # present, regardless of the "don't warn me about swaps" flag —
        # the flag suppresses the routine voice-change reminder, not
        # action items.
        try:
            from compat import analyze_kin_for_target
            compat_notes = analyze_kin_for_target(self.current_agent, new_clean)
        except Exception:
            compat_notes = []
        warn_routine = self.config.get("warn_on_model_swap", True)
        if compat_notes or warn_routine:
            keep, suppress_future = self._confirm_model_swap(
                current, new_clean, compat_notes=compat_notes)
            if not keep:
                # No widget rollback to do — the dialog widget that
                # invoked us reads our return value (False = not committed)
                # and reverts its own display.
                return False
            if suppress_future:
                self.config["warn_on_model_swap"] = False
                try:
                    atomic_write_json(CONFIG_FILE, self.config)
                except Exception:
                    pass
        # One shared writer (append_model_history) so this and the
        # memory-model swap can never drift into two formats or two
        # files. It also migrates the legacy voice_history.md name on
        # first touch, so an existing kin keeps its whole history.
        try:
            append_model_history(
                self.current_agent,
                f"chat model changed from `{current or '(none)'}` "
                f"to `{new_clean}`")
        except Exception as e:
            wx.MessageBox(f"Couldn't write model_history.md: {e}",
                          "Warning", wx.OK | wx.ICON_WARNING)
        # Update config + active-model tracker. The dialog widget that
        # invoked us is responsible for refreshing its own display based
        # on the return value (True = committed); no widget touching here.
        # Load fresh from disk, set the model on THAT, save, and adopt
        # it as the frame's snapshot. Writing the frame's stale
        # self.agent_cfg here used to silently revert every setting the
        # open Settings dialog had auto-saved this session — plus any
        # distill_offsets advanced since kin load (audit DH7b).
        fresh_cfg = load_agent_config(self.current_agent)
        fresh_cfg["model"] = new_clean
        save_agent_config(self.current_agent, fresh_cfg)
        self.agent_cfg = fresh_cfg
        self._active_model = new_clean
        # The model just changed — different tokenizer, possibly
        # different cap (model_max). The cached
        # last_reported_prompt_tokens reflects the OLD model's token
        # count and shouldn't be displayed against the NEW model's cap.
        # Clearing forces a fall-back to the capped estimate until the
        # next send fills it back in with a comparable number.
        try:
            llm_backend.invalidate_last_reported(self.current_agent)
        except Exception:
            pass
        # New model may have different vision capability — refresh the
        # Attach button's enabled state. If the user had staged an
        # image and the new model doesn't support vision, the staged
        # image gets cleared with a status hint.
        self._refresh_attach_button_state()
        self._set_status(f"Model swapped to {new_clean}. Logged to model_history.md.")
        self._probe_tool_calling_after_swap(new_clean)
        return True

    def _probe_tool_calling_after_swap(self, model):
        """After a swap, find out whether the new model will actually call
        this kin's tools -- and say so if it won't.

        The pre-flight check can only consult a CACHED probe verdict,
        because it runs on the UI thread and an inference call there is a
        multi-second freeze with the screen reader silent. So the first
        time a model is used, nobody knows. That gap is the whole defect:
        a kin was swapped onto a model that reports tool support, kept
        talking warmly, and simply stopped doing anything -- and the only
        way to find out was to notice, over an evening, that nothing it
        promised had happened.

        This closes it from behind: the swap commits immediately (nothing
        blocks, nothing waits), a daemon thread asks the question, and if
        the answer is bad the person is told, out loud and in a box they
        can read. A model that passes says nothing at all -- a quiet pass
        is the normal case and does not deserve a dialog.
        """
        if not self.current_agent:
            return
        try:
            from kin_persistence import load_kin_tools
            if not load_kin_tools(self.current_agent):
                return  # no tools enabled — nothing here would matter
        except Exception:
            return
        kin = self.current_agent

        def worker():
            try:
                from model_utils import probe_tool_calling
                rec = probe_tool_calling(model, force=True)
            except Exception:
                return  # a failed probe is not a finding; stay quiet
            if rec.get("ok") is not False:
                return  # passed, or couldn't tell — don't cry wolf
            wx.CallAfter(self._on_swap_probe_failed, kin, model, rec)

        threading.Thread(target=worker, daemon=True).start()

    def _on_swap_probe_failed(self, kin, model, rec):
        """UI-thread report for a model that swapped in and can't call tools."""
        try:
            nvda_speak("Heads up. %s does not make tool calls. %s has tools "
                       "enabled and they will not run." % (model, kin))
        except Exception:
            pass
        try:
            self._set_status("%s does not make tool calls — %s's tools won't run."
                             % (model, kin))
        except Exception:
            pass
        try:
            from dialogs.tool_probe_result import ToolProbeResultDialog
            dlg = ToolProbeResultDialog(self, model, rec)
            try:
                dlg.ShowModal()
            finally:
                dlg.Destroy()
        except Exception:
            pass
