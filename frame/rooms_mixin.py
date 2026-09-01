"""RoomsMixin — extracted from hearthkin.pyw (2026-07 modularisation).

One cohesive slice of the Hearthkin frame's behavior. Mixed into the
Hearthkin(wx.Frame) class in hearthkin.pyw; every method here runs with
the frame as `self`. Moved verbatim — no logic changed.
"""
from frame_shared import (
    load_app_prompt,
    APP_NAME, CONFIG_FILE, DEFAULT_ROOM_CONFIG, ROOMS_DIR, RoomEditDialog,
    _extract_chunk_content, _extract_chunk_thinking, _num_ctx_of, append_failure_log,
    atomic_write_json, build_system_prompt, clean_kin_reply, create_room, datetime,
    delete_room, extract_inline_thinking, format_ts_prefix, kin_tools, list_agents,
    list_rooms, llm_backend, load_agent_config, load_kin_tools, load_memory,
    load_memory_for_prompt,
    load_room_config, load_room_conversation, load_soul, logging, now_iso, ollama,
    resolve_kin_ollama_host, save_room_config, save_room_conversation,
    strip_model_annotation, threading, time, wx,
)


class RoomsMixin:

    # --- Rooms --- #

    def _refresh_rooms_state(self):
        """Enable/disable the Edit Room button based on whether any rooms exist."""
        if hasattr(self, "edit_room_btn"):
            self.edit_room_btn.Enable(bool(list_rooms()))

    def _load_room(self, name):
        # If we're leaving a single-kin chat, fire the kin's on-close
        # distillation trigger so a kin → room transition doesn't
        # silently skip distillation the way kin → kin doesn't
        # (audit H14).
        if self.current_agent and self.current_room is None:
            self._maybe_distill_on_close(self.current_agent)
        self._exit_room_mode()
        self._loading_agent = True
        try:
            self.current_room = name
            self.room_cfg = load_room_config(name)
            self.room_conversation = load_room_conversation(name)

            # Filter members down to ones that still exist on disk
            existing = set(list_agents())
            valid = [m for m in self.room_cfg.get("members", []) if m in existing]
            missing = [m for m in self.room_cfg.get("members", []) if m not in existing]

            self._room_round_count = 0
            self._room_round_index = 0
            self._room_round_order = list(valid)
            self._room_auto_count = 0
            self._room_auto_mode = False
            self._room_paused = True
            self._room_active = False
            self._room_last_user_input = time.monotonic()

            self.auto_check.Show()
            self.auto_check.SetValue(False)
            self.round_label.Show()
            self._rounds_label.Show()
            # Kick the Load-older button so it drops the stale kin-mode label
            # ("Load older messages (606 older)") on entry — the refresh
            # function already knows to hide it when current_room is set, it
            # just wasn't being called from this path.
            self._refresh_load_older_button()
            self.continue_btn.Show()
            self.continue_btn.Enable(False)  # nothing to continue until a round happens
            self.regen_btn.Disable()  # regen is a single-kin concept

            # Visually clear and re-render existing transcript
            self.chat_display.Clear()
            self._render_room_conversation()

            members_str = ", ".join(valid) if valid else "(no valid members)"
            warn = f" — missing: {', '.join(missing)}" if missing else ""
            self._set_status(f"Loaded room: {name} [{members_str}]{warn}")
            self._update_round_label()

            mismatched, delay_reason = self._mixed_models_warning()
            if mismatched:
                self._append_block_plain(
                    f"Note: members use different models ({mismatched}). "
                    f"{delay_reason}"
                )

            # Mode radio + selectors: switch to room mode
            if hasattr(self, "mode_kin_radio"):
                self._mode_set_kin(False)
                self._apply_mode_visibility()
            if hasattr(self, "room_choice"):
                self._refresh_room_list(select=name)
            # Room mode is text-only for v1 — hide the Talk button.
            self._refresh_talk_button_visibility()
            # Image attachments aren't supported in rooms yet (the
            # @-mention routing design is a follow-up). Hide the
            # attach UI entirely so it doesn't sit in tab order.
            self._refresh_attach_button_state()

            # Force a layout pass so the new buttons show
            self.continue_btn.GetParent().Layout()

            self.config["last_target_kind"] = "room"
            self.config["last_room"] = name
            atomic_write_json(CONFIG_FILE, self.config)
            self.SetTitle(f"{APP_NAME} — Room: {name}")
            # Refresh the inline token label and Usage tab now that state
            # has flipped to room mode. _load_agent already does this for
            # kin loads; without it here, the Usage tab keeps showing the
            # previously active kin until the next keystroke.
            self._update_token_display()
        finally:
            self._loading_agent = False

    def _exit_room_mode(self):
        """Persist conversation, cancel any in-flight stream, hide room UI."""
        if self.current_room is None:
            return
        # Cancel any in-flight stream (mark generation stale)
        self._stream_id += 1
        self._streaming = False
        self._room_active = False
        self._room_auto_mode = False
        if self._auto_timer is not None:
            try:
                self._auto_timer.Stop()
            except Exception:
                pass
        try:
            save_room_conversation(self.current_room, self.room_conversation)
        except Exception as e:
            append_failure_log(
                "save_failures.log",
                self.current_room or "?",
                "save_room_conversation (exit room)",
                e,
            )
        self.current_room = None
        self.room_cfg = {}
        self.room_conversation = []
        self._room_round_order = []
        self.auto_check.Hide()
        self.round_label.Hide()
        self._rounds_label.Hide()
        self.continue_btn.Hide()
        self.round_label.SetValue("")
        # And on exit — refresh so the button reflects whatever kin the frame
        # is returning to, rather than staying hidden from the room state.
        self._refresh_load_older_button()
        self.regen_btn.Enable(True)
        self.send_btn.Enable(True)
        self.stop_btn.Disable()
        try:
            self.continue_btn.GetParent().Layout()
        except Exception:
            pass

    def _mixed_models_warning(self):
        """Return (models_csv, delay_reason) when the room's members use
        different models, else ("", "").

        The reason string explains *why* turns may have delays — local
        Ollama models swap weights in/out of GPU memory (a few seconds
        per model change), OpenRouter models hit a remote API (network
        latency + provider load), and a mix of the two combines both.
        Previously this said "Each turn will pause briefly while ollama
        swaps" regardless of provider — but if the room has OpenRouter
        kin in it, Ollama isn't doing anything for those turns; the
        pause is network round-trips. Spelling it out lets the user
        understand what they're actually waiting for."""
        models = set()
        for m in self._room_round_order:
            cfg = load_agent_config(m)
            models.add(strip_model_annotation(cfg.get("model", "")))
        if len(models) <= 1:
            return "", ""
        has_or = any(m.startswith("openrouter/") for m in models)
        has_ollama = any(not m.startswith("openrouter/") and m for m in models)
        if has_or and has_ollama:
            reason = (
                "Each turn pauses briefly: Ollama swaps weights for local "
                "kin (a few seconds per model change), and OpenRouter kin "
                "go over the network (varies by provider load)."
            )
        elif has_or:
            reason = (
                "Each turn makes a network call to OpenRouter — delays "
                "depend on provider load and routing."
            )
        else:
            reason = (
                "Each turn pauses briefly while Ollama swaps the next "
                "kin's model weights into GPU memory."
            )
        return ", ".join(sorted(models)), reason

    def _render_room_conversation(self):
        """Repaint chat_display from self.room_conversation.

        Performance: builds the whole transcript in Python and commits
        with a single SetValue, mirroring _render_conversation. The
        previous shape called _append_block once per message — the
        O(N²) Win32 EDIT pattern plus one NVDA UIA TextChanged event
        per message that the kin path was explicitly rewritten to
        remove (audit M-F13). Output format matches _append_block
        exactly: "\\n[header]\\ncontent\\n" per message."""
        parts = []
        for msg in self.room_conversation:
            speaker = msg.get("speaker") or ("You" if msg.get("role") == "user" else "Model")
            ts = msg.get("ts", "")
            ts_obj = None
            if ts:
                try:
                    ts_obj = datetime.datetime.fromisoformat(ts)
                except ValueError:
                    ts_obj = None
            header = self._block_header(speaker, ts_obj)
            parts.append(f"\n[{header}]\n{msg.get('content', '') or ''}\n")
        self.chat_display.SetValue("".join(parts))

    def _update_round_label(self):
        if self.current_room is None:
            self.round_label.SetValue("")
            return
        cap = self.room_cfg.get("max_auto_rounds", 10)
        auto = self._room_auto_count
        total = self._room_round_count
        if self._room_auto_mode:
            self.round_label.SetValue(f"Round {total} (auto: {auto}/{cap})")
        else:
            self.round_label.SetValue(f"Round {total}")

    def _append_block_plain(self, text):
        self.chat_display.AppendText(f"\n{text}\n")

    def _normalize_member_names(self, raw_names, kin):
        """Match user-entered names against kin list, case-insensitively.
        Returns (canonical_in_order, unrecognized)."""
        kin_by_lower = {k.lower(): k for k in kin}
        canonical = []
        unrecognized = []
        for raw in raw_names:
            hit = kin_by_lower.get(raw.strip().lower())
            if hit is not None:
                if hit not in canonical:
                    canonical.append(hit)
            else:
                if raw.strip():
                    unrecognized.append(raw)
        return canonical, unrecognized

    def _on_new_room(self, event):
        kin = list_agents()
        if not kin:
            wx.MessageBox("Create at least one kin first.", "No kin", wx.OK | wx.ICON_INFORMATION)
            return
        dlg = RoomEditDialog(self, title="New room", available_kin=kin)
        if dlg.ShowModal() == wx.ID_OK:
            res = dlg.get_result()
            name = res["name"]
            normalized, unknown = self._normalize_member_names(res["members"], kin)
            if not name:
                wx.MessageBox("Room name cannot be empty.", "Error", wx.OK | wx.ICON_WARNING)
            elif (ROOMS_DIR / name).exists():
                wx.MessageBox(f"A room named '{name}' already exists.", "Error", wx.OK | wx.ICON_WARNING)
            elif not normalized:
                wx.MessageBox(
                    f"None of those names matched a kin.\n\n"
                    f"You typed: {', '.join(res['members']) or '(nothing)'}\n"
                    f"Available: {', '.join(kin)}\n\n"
                    f"Tip: hit the 'Insert all' button next to the available list, then trim to taste.",
                    "Members not recognized",
                    wx.OK | wx.ICON_WARNING,
                )
            else:
                if unknown:
                    wx.MessageBox(
                        f"These weren't recognized and will be ignored: {', '.join(unknown)}",
                        "Heads up",
                        wx.OK | wx.ICON_INFORMATION,
                    )
                cfg = dict(DEFAULT_ROOM_CONFIG)
                cfg["members"] = normalized
                cfg["context_note"] = res["context_note"]
                cfg["max_auto_rounds"] = res["max_auto_rounds"]
                cfg["auto_inactivity_min"] = res["auto_inactivity_min"]
                cfg["per_turn_token_cap"] = res["per_turn_token_cap"]
                cfg["distill_to_memory"] = res["distill_to_memory"]
                if create_room(name, cfg["members"]):
                    save_room_config(name, cfg)
                    self._refresh_room_list(select=name)
                    self._refresh_rooms_state()
                    self._load_room(name)
        dlg.Destroy()

    def _pick_room(self, action_label):
        """Return a room name to act on. Uses current_room if set; otherwise prompts."""
        if self.current_room:
            return self.current_room
        rooms = list_rooms()
        if not rooms:
            wx.MessageBox("No rooms exist yet. Use Room → New room to create one.",
                          "No rooms", wx.OK | wx.ICON_INFORMATION)
            return None
        choice = wx.SingleChoiceDialog(self, f"Pick a room to {action_label}:",
                                       f"{action_label.title()} room", rooms)
        result = None
        if choice.ShowModal() == wx.ID_OK:
            result = choice.GetStringSelection()
        choice.Destroy()
        return result

    def _on_edit_room(self, event):
        target = self._pick_room("edit")
        if not target:
            return
        cfg = load_room_config(target)
        kin = list_agents()
        dlg = RoomEditDialog(
            self,
            # Parallel to the kin dialog's "{kin_name}'s settings" — NVDA
            # reads the title on open, and it should say the same kind of
            # thing the kin's does. New-room creation passes its own title.
            title=f"{target}'s settings",
            initial_name=target,
            initial_cfg=cfg,
            available_kin=kin,
            name_locked=True,
        )
        if dlg.ShowModal() == wx.ID_OK:
            res = dlg.get_result()
            normalized, unknown = self._normalize_member_names(res["members"], kin)
            if not normalized:
                wx.MessageBox(
                    f"A room needs at least one valid member.\n\n"
                    f"You typed: {', '.join(res['members']) or '(nothing)'}\n"
                    f"Available: {', '.join(kin)}",
                    "No valid members",
                    wx.OK | wx.ICON_WARNING,
                )
            else:
                new_cfg = dict(cfg)
                new_cfg["members"] = normalized
                new_cfg["context_note"] = res["context_note"]
                new_cfg["max_auto_rounds"] = res["max_auto_rounds"]
                new_cfg["auto_inactivity_min"] = res["auto_inactivity_min"]
                new_cfg["per_turn_token_cap"] = res["per_turn_token_cap"]
                new_cfg["distill_to_memory"] = res["distill_to_memory"]
                save_room_config(target, new_cfg)
                if unknown:
                    wx.MessageBox(
                        f"Ignored unrecognized names: {', '.join(unknown)}",
                        "Heads up",
                        wx.OK | wx.ICON_INFORMATION,
                    )
                if self.current_room == target:
                    self._load_room(target)  # rebuilds with new config
                else:
                    self._set_status(f"Edited room: {target}")
        dlg.Destroy()

    def _on_delete_room(self, event):
        target = self._pick_room("delete")
        if not target:
            return
        dlg = wx.MessageDialog(
            self,
            f"Delete room '{target}'? Its conversation history will be removed. The kin themselves are not affected.",
            "Delete room",
            wx.YES_NO | wx.CANCEL | wx.NO_DEFAULT | wx.ICON_WARNING,
        )
        if dlg.ShowModal() == wx.ID_YES:
            if self.current_room == target:
                self._exit_room_mode()
            delete_room(target)
            self._refresh_room_list()
            self._refresh_rooms_state()
            self._set_status(f"Deleted room: {target}")
            if self.current_room is None:
                agents = list_agents()
                if agents:
                    self._load_agent(agents[0])
        dlg.Destroy()

    def _on_continue(self, event):
        if not self.current_room or self._room_active or not self._room_paused:
            return
        if not self._room_round_order:
            self._set_status("Room has no valid members.")
            return
        self._room_paused = False
        self._room_round_index = 0
        rotation = self._room_round_count % max(1, len(self._room_round_order))
        members = self._room_round_order
        # 2-kin rotation lockout. With N=2 members and rotation-by-1
        # each round, the last speaker of round N is always the first
        # speaker of round N+1 — a mathematical property of rotating
        # a 2-element list. Result: every round boundary produces a
        # consecutive same-speaker pair, which is BOTH visually weird
        # AND maximizes impersonation gravity when the two kin share
        # a model (as a kin and a clone of it do). Forcing rotation=0 for
        # the 2-kin case means the order is always [A, B] each round,
        # giving an alternating A B A B A B sequence across boundaries
        # at the cost of A always going first. For 2 kin specifically,
        # consistent-first-speaker is a smaller cost than every-other-
        # turn being the same speaker. 3+ kin rooms work fine with
        # rotation, so we only special-case the 2-kin shape.
        if len(members) == 2:
            rotation = 0
        self._room_active_order = members[rotation:] + members[:rotation]
        self.continue_btn.Enable(False)
        self._run_next_kin_in_round()

    def _on_auto_toggle(self, event):
        if self.current_room is None:
            return
        self._room_auto_mode = self.auto_check.GetValue()
        if self._room_auto_mode:
            self._room_auto_count = 0
            self._room_last_user_input = time.monotonic()
            # Start an inactivity timer (fires every 30s, checks idle threshold)
            if self._auto_timer is None:
                self._auto_timer = wx.Timer(self)
                self.Bind(wx.EVT_TIMER, self._on_auto_tick, self._auto_timer)
            self._auto_timer.Start(30000)
            # If we're already paused, kick off the next round
            if self._room_paused and not self._room_active:
                self._on_continue(None)
        else:
            if self._auto_timer is not None:
                self._auto_timer.Stop()
        self._update_round_label()

    def _on_auto_tick(self, event):
        if self.current_room is None or not self._room_auto_mode:
            return
        idle_min = self.room_cfg.get("auto_inactivity_min", 15)
        elapsed_min = (time.monotonic() - self._room_last_user_input) / 60.0
        if elapsed_min >= idle_min:
            self._room_auto_mode = False
            self.auto_check.SetValue(False)
            self._auto_timer.Stop()
            self._set_status(
                f"Auto mode paused — {idle_min} min of inactivity. Hit Continue to resume."
            )
            self._update_round_label()

    def _send_to_room(self, user_text):
        # Append user message to room transcript
        ts = now_iso()
        self.room_conversation.append({"role": "user", "content": user_text, "ts": ts, "speaker": "You"})
        try:
            save_room_conversation(self.current_room, self.room_conversation)
        except Exception as e:
            # Had no error handling at all. If the save failed the user
            # would see their message in the UI but never reach disk —
            # and there'd be nothing in the failure log to explain it.
            append_failure_log(
                "save_failures.log",
                self.current_room or "?",
                "save_room_conversation (user input)",
                e,
            )
        self._append_block("You", user_text, datetime.datetime.now())

        # User typing breaks the auto-round chain (counter resets)
        self._room_auto_count = 0
        self._room_last_user_input = time.monotonic()

        if not self._room_round_order:
            self._set_status("Room has no valid members.")
            return

        # Start a new round, rotated by total round count
        self._room_paused = False
        self._room_round_index = 0
        rotation = self._room_round_count % max(1, len(self._room_round_order))
        members = self._room_round_order
        # 2-kin rotation lockout — same fix _on_continue carries (see
        # the long comment there): rotating a 2-element list guarantees
        # a consecutive same-speaker pair at every round boundary, max
        # impersonation gravity for same-model pairs (audit L-B18).
        if len(members) == 2:
            rotation = 0
        self._room_active_order = members[rotation:] + members[:rotation]
        self._run_next_kin_in_round()

    def _run_next_kin_in_round(self):
        if self._room_round_index >= len(self._room_active_order):
            self._finish_round()
            return
        kin_name = self._room_active_order[self._room_round_index]
        self._stream_one_kin_in_room(kin_name)

    def _stream_one_kin_in_room(self, kin_name):
        if ollama is None:
            self._set_status("Error: ollama library not installed.")
            self._finish_round()
            return

        cfg = load_agent_config(kin_name)
        model = strip_model_annotation(cfg.get("model", "qwen2.5:7b-instruct"))
        if not model or model.startswith("("):
            self._append_block_plain(f"[skipped {kin_name}: no valid model]")
            self._room_round_index += 1
            wx.CallAfter(self._run_next_kin_in_round)
            return

        soul = load_soul(kin_name)
        # for_prompt: a room turn doesn't go through the desktop send path
        # either, so a kin met only in rooms had the same silent gap.
        memory = load_memory_for_prompt(kin_name)
        ctx_note = self.room_cfg.get("context_note", "").strip()
        other_names = [m for m in self._room_active_order if m != kin_name]
        # The history builder now delivers BOTH the human and the other kin
        # in the `user` channel, each tagged "[Name] " (see the long note
        # there). So this block must not (a) claim they arrive any other way,
        # or (b) demonstrate the "[Name]: " shape — the old text did both,
        # and spelling the attractor out in the system prompt is just handing
        # the model the pattern we removed from the history. It must also say
        # which bracket name is the human, since "the user" is no longer a
        # channel the human uniquely occupies.
        room_human = (self.config.get("user_name", "") or "").strip()
        room_block = (
            f"You are in a room with {', '.join(other_names) or 'no one else'}. "
            "Every turn but your own arrives tagged with the speaker's name in "
            "brackets at the start of the line; your own words are never "
            "tagged. Go by the bracket to tell who said what, and speak only "
            "in your own voice, never theirs.\n"
            "\n"
            "Format rules — these are critical:\n"
            f"- Do NOT prefix your reply with your own name. The chat system labels you as [{kin_name}] automatically.\n"
            "- Do NOT write replies for other people in the room. Only your own next turn.\n"
            "- Do NOT write a multi-character scene. One reply, in your voice, addressed to whoever you're addressing.\n"
            "- If a prior turn from someone else looks cut off (mid-sentence, unclosed quote, trailing comma, etc.), that's just their generation hitting its length cap — do NOT finish their thought, close their quote, or continue in their voice. Start your own reply from your own perspective.\n"
            + (
                f"- [{room_human}] is the human in the room. "
                f"{', '.join(other_names)} are kin like you. Treat the human as such.\n"
                if room_human and other_names
                else "- The user is in the room. They are the human; treat them as such.\n"
            )
        )
        if ctx_note:
            room_block += "\n\n" + ctx_note

        # Resolve this kin's enabled tools first so the base prompt's
        # tool/memory scaffolding is fenced to what's on (same list picks
        # the streaming-vs-tool-loop worker below).
        enabled_tool_names = load_kin_tools(kin_name)
        sys_prompt = build_system_prompt(
            soul, memory, room_block=room_block, enabled_tools=enabled_tool_names,
            kin_name=kin_name
        )

        # Operator's display name — inlined as "[name] " on every
        # user turn so kin in the room can tell who the human is
        # (same way they see each other tagged with [KinName]:).
        # Empty user_name skips the prefix entirely. Read once per
        # room turn-build, not snapshotted into stored history, so
        # a rename in Preferences takes effect retroactively for
        # the whole room transcript (rare action, and "I want kin
        # to see my new name everywhere" is the more useful
        # behavior than "freeze the old name in each turn").
        room_user_name = (self.config.get("user_name", "") or "").strip()
        room_user_prefix = f"[{room_user_name}] " if room_user_name else ""

        history = []
        for m in self.room_conversation:
            role = m.get("role")
            content = m.get("content", "")
            speaker = m.get("speaker", "")
            m_thinking = m.get("thinking")
            # Apply ts prefix only to USER turns and OTHER kin's turns —
            # never to the active kin's own prior replies. Reading their
            # own past output prefixed with the format they're not
            # supposed to emit creates a self-reinforcing pattern loop
            # that destabilizes generation.
            ts_prefix = format_ts_prefix(m.get("ts"))
            if role == "user":
                history.append({
                    "role": "user",
                    "content": ts_prefix + room_user_prefix + content,
                })
            else:
                # OTHER kin go in the USER slot — the same way telegram_bot
                # puts every other group member (human or not) in `user`
                # with a "[Name] " prefix. This is the design doc's Option C
                # (multi-kin-rooms-shared-history.md); the old code used
                # Option A (foreign kin as `assistant: [Name]: content`),
                # which the doc itself flagged as a "format-pattern
                # attractor."
                #
                # It was. One kin opened a reply with
                # "[2026-01-02 09:15] [Opal]: ..." and wrote Opal's entire
                # turn in Opal's voice. Not identity bleed — autocomplete.
                # The assistant slot was full of "[Name]: text" exemplars,
                # so the likeliest continuation of the document was one
                # more of them. The leading-timestamp shielded the tag from
                # strip_leading_speaker_tag (whose regex needs ':' right
                # after ']'), and strip_self_timestamp — which runs LAST —
                # then removed the shield, persisting a naked "[Opal]:".
                #
                # Keeping the assistant slot exclusively the active kin's
                # own bare words removes the exemplars entirely, so there
                # is no pattern left to complete. Telegram groups have
                # never leaked for exactly this reason. Note the format is
                # "[Name] " (space), not "[Name]: " (colon) — matching
                # telegram_bot's sender_prefix and dropping the script-
                # transcript shape that invites continuation.
                if speaker and speaker != kin_name:
                    history.append({"role": "user", "content": ts_prefix + f"[{speaker}] {content}"})
                elif speaker == kin_name:
                    # Pass the kin its own prior thinking (native field) so it
                    # sees its own reasoning, not other kins'. No ts prefix
                    # on own turns — see above.
                    own_msg = {"role": "assistant", "content": content}
                    if m_thinking:
                        own_msg["thinking"] = m_thinking
                    history.append(own_msg)

        messages = [{"role": "system", "content": sys_prompt}] + history

        options = {
            "temperature": cfg.get("temperature", 0.8),
            "top_p": cfg.get("top_p", 0.9),
            "top_k": cfg.get("top_k", 40),
            "min_p": cfg.get("min_p", 0.0),
            "repeat_penalty": cfg.get("repeat_penalty", 1.1),
            "presence_penalty": cfg.get("presence_penalty", 0.0),
            "frequency_penalty": cfg.get("frequency_penalty", 0.0),
            "num_ctx": _num_ctx_of(cfg),
            "num_predict": self.room_cfg.get("per_turn_token_cap", 800),
            # Halt the moment the model tries to open another speaker's
            # bracket block — this is what stops SpeakerNine from writing a whole
            # fake transcript of Jade/Finn/Luna.
            "stop": ["\n["],
        }

        self._stream_id += 1
        my_gen = self._stream_id
        self._streaming = True
        self._room_active = True
        self._stream_buf = ""
        self._think_buf = ""
        # Wire up the same streaming watchdog the 1-on-1 path uses, so
        # a hung room-kin worker (network drop, OS sleep mid-stream,
        # provider hang past its timeout) doesn't leave _room_active
        # wedged True forever — which would block the Continue button
        # AND prevent further room operations. _on_stream_watchdog_fire
        # was extended to clear _room_active too. Cancelled when the
        # kin's turn completes via _on_room_kin_done / _error / _finish.
        self._stream_chunks_seen = 0
        self._cancel_stream_watchdog_timer()
        # Same scaling as the 1-on-1 path; the speaking kin's own cfg
        # determines the timeout (each kin in a room can have its own
        # num_ctx, model, and watchdog_timeout_minutes override).
        watchdog_ms = self._compute_watchdog_timeout_ms(model, cfg)
        self._stream_watchdog_minutes = watchdog_ms // 60000
        self._stream_watchdog_timer = wx.CallLater(
            watchdog_ms, self._on_stream_watchdog_fire, my_gen,
        )
        self._start_still_waiting_timer(my_gen)
        # Room replies are post-processed (strip_self_tag /
        # strip_trailing_other_speakers) before display, so painting
        # progressively would show a prefix that's about to be stripped.
        # Room stays atomic-paint; cursor stays at 0.
        self._paint_cursor = 0
        self._current_room_speaker = kin_name
        self._current_room_model = model

        self.send_btn.Disable()
        self.stop_btn.Enable()
        self._set_status(f"{kin_name} sending ({model})…")
        # Per-turn phase guard reset — each kin in the room gets to
        # announce its own first-phase transition.
        self._reset_spoken_phase()
        self._append_block_header(kin_name, datetime.datetime.now())

        from kin_persistence import think_effort_of
        think_effort = think_effort_of(cfg)
        think_effort = self._guard_think_capability_effort(model, think_effort, kin_name)
        think = (think_effort != "off")
        cache = bool(cfg.get("cache", True))
        cache_ttl = str(cfg.get("cache_ttl", "auto"))
        openrouter_provider = llm_backend.build_openrouter_provider_routing(
            cfg.get("openrouter_provider_order"),
            bool(cfg.get("openrouter_allow_fallbacks", True)),
        )
        max_ctx_tokens = max(512, _num_ctx_of(cfg) - 2000)

        # Audible "send received, generating" cue — low tone
        self._chime("send")

        # Per-kin tools allowlist decides which worker runs (resolved
        # above for prompt fencing). Empty → the streaming room path
        # (unchanged). Non-empty → non-streaming tool-loop path mirroring
        # the 1-on-1 tool flow: tool calls painted inline via
        # _on_tool_call_display, final reply still goes through the room
        # post-processing (strip_self_tag / strip_trailing_other_speakers)
        # via _on_room_kin_done. exec approval still fires the wx dialog —
        # rooms are the desktop UI.

        room_show_thinking = bool(cfg.get("show_thinking", False))
        def worker():
            try:
                if enabled_tool_names:
                    self._run_room_tool_loop_inline(
                        my_gen, kin_name, model, messages, options,
                        think, cache, max_ctx_tokens, enabled_tool_names,
                        think_effort=think_effort,
                        show_thinking=room_show_thinking,
                        cache_ttl=cache_ttl,
                        openrouter_provider=openrouter_provider,
                    )
                else:
                    self._run_room_streaming_inline(
                        my_gen, kin_name, model, messages, options, think, cache, max_ctx_tokens,
                        think_effort=think_effort,
                        show_thinking=room_show_thinking,
                        cache_ttl=cache_ttl,
                        openrouter_provider=openrouter_provider,
                    )
            except llm_backend.OpenRouterRateLimitError as e:
                retry = f" Try again in {e.retry_after}s." if e.retry_after else ""
                wx.CallAfter(self._on_room_kin_error, my_gen,
                             f"Rate limited on {model}.{retry}")
            except Exception as e:
                wx.CallAfter(self._on_room_kin_error, my_gen, str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _room_bracketed_names(self):
        """Every name this room puts in brackets on a user turn: the person's
        display name from Preferences, plus each kin who has spoken. Recall is
        told these so it can ignore its own bookkeeping without guessing from
        the bracket shape -- the same names-not-patterns rule the
        impersonation strippers follow. Never raises; an empty list just means
        no bracket is stripped, which costs a missed match rather than taking
        a word out of somebody's mouth."""
        try:
            names = set()
            u = (self.config.get("user_name", "") or "").strip()
            if u:
                names.add(u)
            for m in (self.room_conversation or []):
                sp = (m.get("speaker") or "").strip() if isinstance(m, dict) else ""
                if sp:
                    names.add(sp)
            return sorted(names)
        except Exception:
            return []

    def _run_room_streaming_inline(self, my_gen, kin_name, model, messages, options, think, cache, max_ctx_tokens, *, think_effort=None, show_thinking=True, cache_ttl="auto", openrouter_provider=None):
        """Streaming room path — used when the active turn's kin has no
        tools enabled. Identical to the prior in-line code; extracted so
        the worker() body stays small and the two paths read parallel.

        `show_thinking` is the active kin's setting (passed in by the
        dispatcher which has already loaded that kin's cfg)."""
        # Per-turn memory recall for this room kin — its own relevant depth,
        # inlined on the latest user turn (no tool call). The block is a
        # clearly-bracketed [hearthkin: …] note, not a [Name]: speaker turn, so
        # it stays clear of the room impersonation safeguards. Fail-soft.
        try:
            from memory_recall import inject_into_messages
            messages, _ = inject_into_messages(
                messages, kin_name,
                num_ctx=int((options or {}).get("num_ctx", 8192) or 8192),
                cfg=load_agent_config(kin_name) or {},
                # Names this room brackets onto a turn. Ours, so recall may
                # ignore them; a bracket the PERSON typed is theirs and stays
                # -- a plural system announcing who is fronting writes exactly
                # that shape, and it is part of what they said.
                speaker_names=self._room_bracketed_names())
        except Exception:
            pass
        room_host = resolve_kin_ollama_host(
            (load_agent_config(kin_name) or {}).get("ollama_host_name", ""))
        stream = llm_backend.chat(
            model,
            messages,
            options=options,
            think=think,
            think_effort=think_effort,
            show_thinking=show_thinking,
            stream=True,
            cache=cache, cache_ttl=cache_ttl,
            openrouter_provider=openrouter_provider,
            max_context_tokens=max_ctx_tokens,
            kin_name=kin_name,
            surface="room",
            ollama_host=room_host,
        )
        first_chunk = True
        for chunk in stream:
            if my_gen != self._stream_id:
                break
            if getattr(chunk, "done", False):
                break
            # SSE heartbeat — bumps watchdog liveness, no content.
            if getattr(chunk, "heartbeat", False):
                wx.CallAfter(self._on_stream_heartbeat, my_gen)
                continue
            content = _extract_chunk_content(chunk)
            thinking = _extract_chunk_thinking(chunk)
            if content or thinking:
                if first_chunk:
                    self._chime("first")
                    first_chunk = False
            if content:
                wx.CallAfter(self._on_room_chunk, my_gen, content)
            if thinking:
                wx.CallAfter(self._on_room_thinking_chunk, my_gen, thinking)
        wx.CallAfter(self._on_room_kin_done, my_gen)

    def _run_room_tool_loop_inline(self, my_gen, kin_name, model, messages, options,
                                   think, cache, max_ctx_tokens, enabled_tool_names,
                                   *, think_effort=None, show_thinking=True, cache_ttl="auto", openrouter_provider=None):
        """Tool-loop room path — used when the active turn's kin has
        tools enabled. Non-streaming: run_tool_loop blocks until all
        tool round-trips resolve and a final reply is produced. Tool
        calls paint inline via _on_tool_call_display (the same helper
        the 1-on-1 path uses). Final content goes into _stream_buf and
        routes through _on_room_kin_done so the existing strip_self_tag
        / strip_trailing_other_speakers cleanup runs identically."""
        # Look up the kin's model so capability-gated tools (use_webcam)
        # only appear in the schema list when the model can use them.
        room_kin_cfg = load_agent_config(kin_name) or {}
        room_kin_model = strip_model_annotation(room_kin_cfg.get("model", "") or "")
        try:
            room_tool_result_cap = int(room_kin_cfg.get("tool_result_cap", 8000))
        except (TypeError, ValueError):
            room_tool_result_cap = 8000
        schemas, executor = kin_tools.load_tools(
            enabled_tool_names,
            context={"agent_name": kin_name},
            model=room_kin_model,
        )
        executor = self._wrap_exec_executor(executor, kin_name)
        # Rooms get the tool-use hint but NOT the authoring-bridge hint — the
        # bridge runs on the 1-on-1 completion path (_on_stream_done), not on
        # _on_room_kin_done, so advertising the fence convention in a room
        # would promise a save that never happens. (A follow-up can wire the
        # bridge into the room path if room authoring becomes a real use.)
        messages = self._inject_tool_use_hint(
            list(messages), [s["function"]["name"] for s in schemas],
            with_authoring_hint=False,
        )
        # Per-turn memory recall for this room kin (its own depth, inlined on
        # the latest user turn; no tool call). Fail-soft.
        try:
            from memory_recall import inject_into_messages
            messages, _ = inject_into_messages(
                messages, kin_name,
                num_ctx=int((options or {}).get("num_ctx", 8192) or 8192),
                cfg=room_kin_cfg,
                # Names this room brackets onto a turn. Ours, so recall may
                # ignore them; a bracket the PERSON typed is theirs and stays
                # -- a plural system announcing who is fronting writes exactly
                # that shape, and it is part of what they said.
                speaker_names=self._room_bracketed_names())
        except Exception:
            pass
        self._chime("first")

        # Non-streaming tool-loop: announce "Typing" up front (same
        # reason as the 1-on-1 tool-loop — no streaming chunks to
        # carry the phase change).
        def _announce_typing():
            speaker = self._current_room_speaker or kin_name
            self._set_status(f"{speaker} typing…")
            self._speak_status_phase("Typing")
        wx.CallAfter(_announce_typing)

        def on_tool_call(name, args, result, is_error):
            if my_gen != self._stream_id:
                return
            wx.CallAfter(self._on_tool_call_display, my_gen, name, args, result)

        result = llm_backend.run_tool_loop(
            model, messages,
            tools=schemas, tool_executor=executor,
            options=options, cache=cache, cache_ttl=cache_ttl,
            openrouter_provider=openrouter_provider,
            on_tool_call=on_tool_call,
            think=think, think_effort=think_effort,
            show_thinking=show_thinking,
            max_context_tokens=max_ctx_tokens,
            tool_result_cap=room_tool_result_cap,
            # kin_name lets attachment-bearing tool results (use_webcam
            # specifically) resolve relative paths and inject synthetic
            # user turns the speaking kin can actually see. Without
            # this, a room kin with use_webcam enabled would capture
            # successfully but the model would only see the tool-result
            # JSON, never the image itself.
            kin_name=kin_name,
            surface="room-tool",
            max_iterations=int(room_kin_cfg.get("max_tool_iterations", 8) or 8),
            ollama_host=resolve_kin_ollama_host(
                room_kin_cfg.get("ollama_host_name", "")),
        )
        if my_gen != self._stream_id:
            return
        self._maybe_log_tool_name_as_text(
            kin_name, model, result,
            [s["function"]["name"] for s in schemas],
        )
        # Feed the final reply into the existing room-completion path
        # so post-processing + persistence run identically. The buffer
        # writes are marshalled through wx.CallAfter — this is a worker
        # thread, and writing _stream_buf / _think_buf / _paint_cursor
        # directly from here was the one break in the CallAfter
        # discipline (audit L-B21).
        final_content = result.content or ""
        final_thinking = result.thinking or ""
        # Stash the tool-loop's intermediate turns so _on_room_kin_done
        # can salvage the kin's pre-tool content as the reply when the
        # final post-tool content is empty (the Haiku-4.5 + `note`
        # pattern, same shape as the Telegram and desktop salvages).
        # Even though full tool round-trip persistence in room history
        # is a v1 limitation (see below), the salvage scan only needs
        # the per-turn added_turns — which we have right here.
        added_turns = list(getattr(result, "messages_added", []) or [])

        # Known limitation: intermediate tool round-trip turns are NOT
        # spliced into room conversation persistence in v1. The room
        # conversation carries per-speaker tagged turns; reconstructing
        # tool round-trips per speaker would mean either tagging tool
        # results with speaker (extra fields) or restructuring how
        # rooms render history. Follow-up. For now the kin sees their
        # own tool calls via the inline chat display only; they don't
        # show up in the next turn's context. (Salvage above operates
        # on the current turn only and works regardless.)
        def _hand_off():
            if my_gen != self._stream_id:
                return
            self._stream_buf = final_content
            self._think_buf = final_thinking
            self._paint_cursor = 0
            self._pending_room_tool_history = added_turns
            self._on_room_kin_done(my_gen)
        wx.CallAfter(_hand_off)

    def _on_room_thinking_chunk(self, gen, text):
        if self._closing:
            return
        if gen != self._stream_id:
            return
        # Tick proof-of-life so the watchdog doesn't kill an actively-
        # reasoning model 5 minutes in.
        self._stream_chunks_seen = getattr(self, "_stream_chunks_seen", 0) + 1
        if getattr(self, "_spoken_phase", None) != "Thinking":
            speaker = self._current_room_speaker or "Kin"
            self._set_status(f"{speaker} thinking…")
            self._speak_status_phase("Thinking")
        self._think_buf += text

    def _on_room_chunk(self, gen, text):
        if self._closing:
            return
        if gen != self._stream_id:
            return
        # Tick proof-of-life so the watchdog doesn't kill the stream
        # while content chunks are actively arriving.
        self._stream_chunks_seen = getattr(self, "_stream_chunks_seen", 0) + 1
        if getattr(self, "_spoken_phase", None) != "Typing":
            speaker = self._current_room_speaker or "Kin"
            self._set_status(f"{speaker} typing…")
            self._speak_status_phase("Typing")
        # Buffer only — room replies are post-processed (strip_self_tag /
        # strip_trailing_other_speakers) before display, so we can't safely
        # paint until the full reply is in hand. Kin (1:1) mode streams
        # sentence-by-sentence via _on_stream_chunk; room mode stays atomic.
        self._stream_buf += text

    def _on_room_kin_done(self, gen):
        if self._closing:
            return
        if gen != self._stream_id:
            return
        # Cancel the watchdog timer — this turn completed within the
        # 5-minute window, so the hang-recovery shouldn't fire.
        self._cancel_stream_watchdog_timer()
        reply = self._stream_buf
        thinking = self._think_buf
        self._stream_buf = ""
        self._think_buf = ""
        self._streaming = False

        # Per-kin thinking display / feed settings
        kin_cfg = load_agent_config(self._current_room_speaker) if self._current_room_speaker else {}
        show_thinking = kin_cfg.get("show_thinking", False)
        feed_thinking = kin_cfg.get("feed_thinking", False)

        # Pull inline <thinking>...</thinking> markup out of content and
        # merge into the structured thinking field BEFORE logging /
        # display / cleanup. Keeps room next-speaker context clean of
        # the markup pattern (prevents format-attractor priming for the
        # next kin in the loop) AND populates the structured field so
        # the per-kin show_thinking display below picks up the
        # extracted reasoning. See chat_helpers.extract_inline_thinking.
        reply, thinking = extract_inline_thinking(reply, thinking)

        if thinking:
            self._log(f"THINKING [{self._current_room_speaker}]: {thinking}")
        if thinking and show_thinking:
            self.chat_display.AppendText(f"[{self._current_room_speaker} reasoning]\n{thinking}\n")

        raw_reply = reply
        # clean_kin_reply owns the ORDER (timestamp unwrapped first — see its
        # docstring; the old order here let "[TS] [OtherKin]:" through and
        # that is exactly how the room impersonation leak persisted).
        reply, _imp = clean_kin_reply(reply, self._current_room_speaker)
        if _imp:
            logging.getLogger("hearthkin").warning(
                "IMPERSONATION (room): %s opened its reply as another kin. "
                "The tag was stripped, but a reply that opens this way is "
                "compromised end-to-end — the body is in the other kin's "
                "voice and stripping only hides that. Since the room history "
                "builder moved foreign kin to the user slot, this should be "
                "unreachable; if it fires, the attractor is back and this "
                "case wants a re-roll, not a scrub. Raw: %r",
                self._current_room_speaker, raw_reply[:200])

        # Visible marker if the model produced nothing — but first try to
        # salvage from intermediate tool-loop content (parallels the
        # Telegram + desktop salvages; same Haiku-4.5 + `note` pattern).
        if not reply.strip():
            from chat_helpers import (
                scan_intermediate_tool_content,
                strip_tool_summary_footer,
            )
            pending = getattr(self, "_pending_room_tool_history", []) or []
            intermediate, tool_names = scan_intermediate_tool_content(pending)
            salvaged_content = ""
            if intermediate:
                candidate, _drop = extract_inline_thinking(intermediate, "")
                candidate, _imp = clean_kin_reply(
                    candidate, self._current_room_speaker or "")
                if _imp:
                    logging.getLogger("hearthkin").warning(
                        "IMPERSONATION (room, salvaged tool content): %s "
                        "opened as another kin",
                        self._current_room_speaker)
                candidate = strip_tool_summary_footer(candidate)
                candidate = candidate.strip()
                if candidate:
                    salvaged_content = candidate
            if salvaged_content:
                self.chat_display.AppendText(salvaged_content + "\n")
                reply_to_save = salvaged_content
                self._room_salvaged_intermediate = True
                self._room_salvaged_tool_names = tool_names
                self._log_empty_reply(
                    self._current_room_speaker,
                    f"{self._current_room_model} [salvaged]",
                    raw_reply,
                )
            else:
                empty_marker = "[no reply produced]"
                self.chat_display.AppendText(empty_marker + "\n")
                reply_to_save = empty_marker
                self._room_salvaged_intermediate = False
                self._room_salvaged_tool_names = []
                self._log_empty_reply(
                    self._current_room_speaker,
                    self._current_room_model,
                    raw_reply,
                )
        else:
            self.chat_display.AppendText(reply + "\n")
            reply_to_save = reply
            self._room_salvaged_intermediate = False
            self._room_salvaged_tool_names = []

        ts = now_iso()
        room_msg = {
            "role": "assistant",
            "content": reply_to_save,
            "ts": ts,
            "speaker": self._current_room_speaker,
            "model": self._current_room_model,
        }
        # Feed reasoning back via native `thinking` field (same shape Ollama
        # returns) when feed_thinking is on. Truncate per the per-kin cap so
        # it doesn't pile up across turns and slow every reply.
        if thinking and feed_thinking:
            cap = int(kin_cfg.get("think_max_chars", 1200) or 0)
            if cap > 0 and len(thinking) > cap:
                thinking = thinking[:cap] + "\n... [reasoning truncated]"
            room_msg["thinking"] = thinking
        self.room_conversation.append(room_msg)
        # Salvage system note — if the room kin's reply was salvaged
        # from intermediate tool-loop content, append a system note so
        # next read of room history shows the kin what happened.
        if getattr(self, "_room_salvaged_intermediate", False):
            tool_names = getattr(self, "_room_salvaged_tool_names", []) or []
            self.room_conversation.append({
                "role": "system",
                "content": load_app_prompt(
                    "salvage_note_room", self._current_room_speaker)
                .replace("{speaker}", str(self._current_room_speaker))
                .replace("{tools}", ", ".join(tool_names) or "(none)"),
                "ts": ts,
                "speaker": self._current_room_speaker,
            })
            self._room_salvaged_intermediate = False
            self._room_salvaged_tool_names = []
        # Clear stashed pending tool history so it doesn't carry into
        # the next kin's turn.
        self._pending_room_tool_history = []
        try:
            save_room_conversation(self.current_room, self.room_conversation)
        except Exception as e:
            # Was silently swallowed — exact parallel to the Telegram
            # bug we just fixed. A kin's room turn could be appended to
            # in-memory state and rendered to the UI, but never reach
            # disk, with no log entry to find out why. Logging it now.
            append_failure_log(
                "save_failures.log",
                self.current_room or "?",
                f"save_room_conversation (kin={self._current_room_speaker})",
                e,
            )

        # Audible "reply complete" cue — high tone, slightly longer
        self._chime("done")

        # Memory: tally this kin's room-scope messages, the way
        # _on_stream_done does for "desktop". This is the wire that was
        # missing until 2026-07-16 — every other part (staging, the
        # per-(kin, scope) counters, the nightly tending cron that
        # already reads every staging file it finds) was built and
        # running; the room just wasn't attached to any of it, so
        # nothing that happened in a room ever reached a kin's memory.
        # See docs/design/room-memory.md.
        #
        # Gated on the room's opt-in flag, and gated per SPEAKER: each
        # member counts its own turns toward its own cadence, and
        # distills its own slice. Runs after the save above so the
        # worker's disk read sees this turn.
        speaker = self._current_room_speaker
        if speaker and self.room_cfg.get("distill_to_memory", False):
            room_scope = self._distill_scope_for_room(self.current_room)
            key = (speaker, room_scope)
            self._messages_since_distill[key] = (
                self._messages_since_distill.get(key, 0) + 1
            )
            dlg = self._dialog_for(speaker)
            if dlg is not None:
                try:
                    dlg._refresh_chat_counters_display()
                except Exception:
                    pass
            wx.CallAfter(
                self._maybe_auto_distill, speaker, scope_key=room_scope)

        self._room_round_index += 1
        # Brief pause between turns so the UI has room to breathe
        wx.CallLater(150, self._run_next_kin_in_round)

    def _on_room_kin_error(self, gen, msg):
        if gen != self._stream_id:
            return
        # Cancel the watchdog timer — error was surfaced cleanly, no
        # hang-recovery needed.
        self._cancel_stream_watchdog_timer()
        partial = self._stream_buf
        self._stream_buf = ""
        self._think_buf = ""
        self._streaming = False
        if partial:
            self.chat_display.AppendText(partial)
        self.chat_display.AppendText(f"\n[error from {self._current_room_speaker}: {msg}]\n")
        self._room_round_index += 1
        wx.CallLater(150, self._run_next_kin_in_round)

    def _finish_round(self):
        self._room_active = False
        self._streaming = False
        self._room_round_count += 1
        self._room_paused = True

        self.send_btn.Enable()
        self.stop_btn.Disable()
        self.continue_btn.Enable(True)
        self._update_round_label()

        cap = self.room_cfg.get("max_auto_rounds", 10)
        if self._room_auto_mode:
            self._room_auto_count += 1
            if self._room_auto_count >= cap:
                self._room_auto_mode = False
                self.auto_check.SetValue(False)
                if self._auto_timer is not None:
                    self._auto_timer.Stop()
                self._set_status(f"Auto mode hit cap of {cap} rounds. Hit Continue to keep going.")
                self._update_round_label()
                return
            idle_min = self.room_cfg.get("auto_inactivity_min", 15)
            elapsed_min = (time.monotonic() - self._room_last_user_input) / 60.0
            if elapsed_min >= idle_min:
                self._room_auto_mode = False
                self.auto_check.SetValue(False)
                self._set_status(f"Auto mode paused — {idle_min} min of inactivity.")
                self._update_round_label()
                return
            # Schedule next round
            self._set_status("Auto-continuing in 1.5s — type or hit Stop to interrupt.")
            wx.CallLater(1500, self._auto_continue_if_still_on)
        else:
            self._set_status(f"Round {self._room_round_count} complete. Continue or type to send.")

    def _auto_continue_if_still_on(self):
        if (
            self.current_room is not None
            and self._room_auto_mode
            and self._room_paused
            and not self._room_active
        ):
            self._on_continue(None)
