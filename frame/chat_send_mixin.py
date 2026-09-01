"""ChatSendMixin — extracted from hearthkin.pyw (2026-07 modularisation).

One cohesive slice of the Hearthkin frame's behavior. Mixed into the
Hearthkin(wx.Frame) class in hearthkin.pyw; every method here runs with
the frame as `self`. Moved verbatim — no logic changed.
"""
from frame_shared import (
    clean_kin_reply,
    load_app_prompt,
    _is_cron_user_text,
    _extract_chunk_content, _extract_chunk_thinking, _last_sentence_end, _num_ctx_of,
    build_system_prompt, datetime, kin_tools, llm_backend, load_agent_config,
    load_kin_tools, now_iso, ollama, os, resolve_kin_ollama_host, threading, wx,
)


class ChatSendMixin:

    def _on_send(self, event):
        if self._streaming or self._room_active:
            return
        user_text = self.input_box.GetValue().strip()
        # Allow sending an image with no text (just the image). Most
        # vision models handle a no-caption image fine — they'll
        # describe / react to it. Refuse only when BOTH text and
        # attachment are empty (neither file-picker-staged nor
        # webcam-staged).
        has_attachment = (self._pending_attachment is not None
                          or self._pending_attachment_rel is not None)
        if not user_text and not has_attachment:
            return
        if self.current_room is not None:
            self.input_box.Clear()
            self._send_to_room(user_text)
        else:
            # Clear the input only once _send_message accepted the
            # send. Clearing first meant every validation early-return
            # (invalid model, vision mismatch, attachment save failure)
            # silently destroyed the typed message — unrecoverable for
            # an operator who composed a long one (audit M-F2).
            if self._send_message(user_text):
                self.input_box.Clear()

    def _send_message(self, user_text, regen_attachment_refs=None):
        """Returns True when the send was accepted (worker fired),
        False on any validation early-return — _on_send only clears
        the input box on True so a refused send doesn't destroy the
        typed message (audit M-F2)."""
        if ollama is None:
            self._set_status("Error: ollama library not installed (pip install ollama).")
            return False
        if not self.current_agent:
            self._set_status("No kin loaded. Create or select one first.")
            return False

        model = self._current_chat_model_clean()
        if not model or model.startswith("("):
            self._set_status("Please select a valid model.")
            return False

        # Resolve outgoing image attachments. Three sources:
        #   - `regen_attachment_refs` (list of pre-saved relative
        #     paths) when re-running a previous user turn via the
        #     Regenerate button. The files are already on disk.
        #   - `self._pending_attachment_rel` (single relative path,
        #     already on disk) when the webcam-capture path staged
        #     it. Same shape as the regen source — no save step.
        #   - `self._pending_attachment` (single absolute path) when
        #     the file-picker staged it. Save it to the kin's
        #     attachments/ dir now and reference the resulting
        #     relative path. Save failures refuse the send rather
        #     than silently drop the image.
        attachment_refs = []
        if regen_attachment_refs:
            attachment_refs = [r for r in regen_attachment_refs if isinstance(r, str) and r]
            if attachment_refs and not llm_backend.model_supports_images(model):
                self._set_status(
                    "Can't regenerate with images on this model — "
                    "switch to a vision-capable model first."
                )
                return False
        elif self._pending_attachment_rel is not None:
            if not llm_backend.model_supports_images(model):
                self._set_status(
                    "This model doesn't support images. Clear the image "
                    "or change the model in Settings."
                )
                return False
            attachment_refs.append(self._pending_attachment_rel)
        elif self._pending_attachment is not None:
            if not llm_backend.model_supports_images(model):
                self._set_status(
                    "This model doesn't support images. Clear the image "
                    "or change the model in Settings."
                )
                return False
            try:
                from kin_persistence import save_attachment
                rel = save_attachment(self.current_agent, self._pending_attachment)
                attachment_refs.append(rel)
            except ValueError as e:
                wx.MessageBox(
                    f"Couldn't attach that image: {e}",
                    "Attach image", wx.OK | wx.ICON_WARNING,
                )
                return False
            except Exception as e:
                self._set_status(f"Couldn't save attachment: {e}")
                return False

        # Use the per-kin text cache instead of hitting disk on every
        # send. EditKinDialog invalidates the cache on Save Soul / Save
        # Memory; load_agent populates it on kin switch (audit H21).
        # A kin writing its own memory.md through a file tool does
        # neither, so the cache is re-validated against the files'
        # mtime first — otherwise the kin cannot see anything it wrote
        # to its own memory for the rest of the session, and reports
        # having no memory at all. See _refresh_kin_text_cache_if_stale.
        self._refresh_kin_text_cache_if_stale()
        soul = (self._soul_cache or "").strip()
        memory = self._memory_cache or ""
        # Resolve the kin's enabled tools first so the base prompt's
        # tool/memory scaffolding is fenced to what's actually on (a
        # tool-less kin gets none of it). Same list drives the
        # streaming-vs-tool-loop worker choice below.
        enabled_tool_names = load_kin_tools(self.current_agent)
        sys_content = build_system_prompt(soul, memory, enabled_tools=enabled_tool_names,
                                          kin_name=self.current_agent)
        # Text-in/text-out park kin (`park` = chat|keeper): tell it the
        # `> command` convention exists, or it never learns it here.
        #
        # This surface LISTENS for the line (_route_park_command, below) but
        # was the only one that never TAUGHT it: park_chat_hint was loaded in
        # exactly one place, telegram_bot. So a chat-mode kin in the main
        # window saw its `tff` tool schema and nothing else, and reached for
        # the tool every time — which is precisely what the `>` convention
        # exists to replace. Same class of gap as the routing one this file's
        # _route_park_command was added to close, one layer up: we taught the
        # harness to listen on every surface and to teach on one.
        #
        # Best-effort and additive: a park problem must never cost a send.
        try:
            import park_keeper
            if park_keeper.kin_park_mode(self.current_agent) in ("chat", "keeper"):
                _park_hint = load_app_prompt("park_chat_hint", self.current_agent)
                if _park_hint and _park_hint.strip():
                    sys_content = (sys_content or "") + "\n\n" + _park_hint
        except Exception:
            pass
        # Compact older tool round-trips into single role=system markers
        # before translating each surviving message. The kin's per-config
        # `tool_history_keep` decides how many recent pairs stay verbatim.
        # Default 5; the conversation.json on disk is unaffected — this
        # only changes what's sent in this request's messages list.
        keep_window = int(self.agent_cfg.get("tool_history_keep", 5) or 0)
        compacted = self._compact_tool_history(self.conversation, keep_window)
        history = []
        for _m in compacted:
            _entry = self._history_entry_for_model(_m)
            if _entry is not None:
                history.append(_entry)
        messages = []
        if sys_content:
            messages.append({"role": "system", "content": sys_content})
        messages.extend(history)
        new_user_msg = {"role": "user", "content": user_text}
        if attachment_refs:
            new_user_msg["attachments"] = attachment_refs
        messages.append(new_user_msg)

        # Per-turn memory retrieval runs on the WORKER thread (see worker()
        # below), NOT here. It can make an Ollama embedding call for semantic
        # recall, and a slow / unreachable embed host on the UI thread froze
        # the whole app on send — an un-timeout'd embed was a permanent,
        # unkillable hang. Initialize the "what surfaced" display field here
        # so the readout is safe even if recall is skipped or hasn't run yet.
        # See docs/design/per-turn-memory-retrieval.md.
        self._last_recall_used = []

        # Reading bridge — "the sharing is the loading". If the operator named
        # a real file in this message, place its TEXT in front of the kin this
        # turn, as a system block just before the user turn, so it can engage
        # the content instead of gesturing at reading it. TEXT, not an image
        # attach — the existing attach path is image-only and useless to a
        # kin on a non-vision model, which is why attaching a file to one
        # does nothing. Ephemeral to this send, like the
        # recall injection above. self._shared_files_this_turn lets the
        # read-gesture nudge stay quiet when content was actually placed here.
        self._shared_files_this_turn = []
        try:
            import reading_bridge
            _shared = reading_bridge.extract_shared_paths(user_text)
            if _shared:
                _block = reading_bridge.build_shared_context_block(
                    reading_bridge.read_shared_files(_shared))
                if _block and messages:
                    messages.insert(len(messages) - 1,
                                    {"role": "system", "content": _block})
                    self._shared_files_this_turn = _shared
        except Exception:
            pass

        options = {
            "temperature": self.agent_cfg.get("temperature", 0.8),
            "top_p": self.agent_cfg.get("top_p", 0.9),
            "top_k": self.agent_cfg.get("top_k", 40),
            "min_p": self.agent_cfg.get("min_p", 0.0),
            "repeat_penalty": self.agent_cfg.get("repeat_penalty", 1.1),
            "presence_penalty": self.agent_cfg.get("presence_penalty", 0.0),
            "frequency_penalty": self.agent_cfg.get("frequency_penalty", 0.0),
            "num_ctx": _num_ctx_of(self.agent_cfg),
            # Without this the desktop path sent no num_predict at all
            # and the reply generated until the context window filled,
            # truncating mid-sentence with no stop token. Rooms and
            # Telegram have always passed their own caps.
            "num_predict": self.agent_cfg.get("num_predict", 2000),
        }

        self._stream_id += 1
        my_gen = self._stream_id
        self._streaming = True
        self._stream_buf = ""
        self._stream_user_text = user_text
        self._think_buf = ""
        self._paint_cursor = 0
        # Watchdog: if no chunks (content OR thinking) arrive within
        # the scaled timeout window, force-clear the streaming state
        # and surface the hang. _stream_chunks_seen increments in both
        # _on_stream_chunk and _on_stream_thinking_chunk; the watchdog
        # checks it on a delayed wx.CallLater. Cancelled when the
        # stream completes normally (or errors).
        #
        # Timeout scales by provider + num_ctx — see
        # _compute_watchdog_timeout_ms. The fixed 5-min default used
        # to be right for OpenRouter network hangs but cut off local
        # Ollama prefill on large contexts before the model could
        # produce its first token. The helper handles that.
        self._stream_chunks_seen = 0
        watchdog_ms = self._compute_watchdog_timeout_ms(model, self.agent_cfg)
        self._stream_watchdog_minutes = watchdog_ms // 60000
        self._stream_watchdog_timer = wx.CallLater(
            watchdog_ms, self._on_stream_watchdog_fire, my_gen,
        )
        # Periodic "still waiting" status updates so the operator can
        # see the model is still being given time (no chunks yet but
        # we haven't given up). First update at 60s, then every 30s.
        self._start_still_waiting_timer(my_gen)

        self.send_btn.Disable()
        self.stop_btn.Enable()
        self._set_status("Sending…")
        # Reset the per-turn phase guard so the first content/thinking
        # chunk announces. Without this, a previous turn's "Typing"
        # would suppress the new turn's first speech.
        self._reset_spoken_phase()

        now = datetime.datetime.now()
        self._log_session_header(model, sys_content, options)
        self._log(f"USER: {user_text}")
        # Show the attachment marker in chat right after the user
        # text so the user sees what got sent. _append_block paints
        # the text block; we append a small [image: filename] suffix
        # via _append_attachment_markers when refs are present.
        self._append_block("You", user_text, now)
        if attachment_refs:
            self._append_attachment_markers(attachment_refs)
        self._append_block_header(self.current_agent or "Model", now)

        # Save the user message immediately, BEFORE the worker fires.
        # If the stream hangs (network blip, model wedged, OS sleep,
        # etc.) and never reaches _on_stream_done, the message would
        # otherwise be lost — only painted to chat_display, never
        # persisted. Stash _user_turn_persisted so _on_stream_done /
        # _on_stop know not to double-append.
        user_ts = now.isoformat(timespec="seconds")
        persisted_user_msg = {
            "role": "user", "content": user_text, "ts": user_ts,
        }
        if attachment_refs:
            persisted_user_msg["attachments"] = attachment_refs
        self.conversation.append(persisted_user_msg)
        self._user_turn_persisted = True
        try:
            self._persist_current_conversation()
        except Exception:
            pass
        # Clear the pending-attachment state now that it's persisted
        # — UI back to "ready for next turn" with no image staged.
        # Both file-picker AND webcam slots clear, regardless of
        # which one provided the refs.
        if attachment_refs:
            self._pending_attachment = None
            self._pending_attachment_rel = None
            self.attached_label.SetValue("")
            self.attached_label.Hide()
            self.clear_attach_btn.Hide()
            if self.attach_btn.GetParent():
                self.attach_btn.GetParent().Layout()

        from kin_persistence import think_effort_of
        think_effort = think_effort_of(self.agent_cfg)
        think_effort = self._guard_think_capability_effort(
            model, think_effort, self.current_agent or "kin",
        )
        # Legacy `think` boolean still passed downstream to anything
        # that hasn't moved to think_effort yet (Ollama path is
        # boolean-only natively).
        think = (think_effort != "off")
        cache = bool(self.agent_cfg.get("cache", True))
        cache_ttl = str(self.agent_cfg.get("cache_ttl", "auto"))
        openrouter_provider = llm_backend.build_openrouter_provider_routing(
            self.agent_cfg.get("openrouter_provider_order"),
            bool(self.agent_cfg.get("openrouter_allow_fallbacks", True)),
        )
        # Safety net so a runaway history never blows the model's context window.
        # num_ctx is what the model can hold; leave ~2K headroom for the reply.
        # Tolerant cast so a corrupted num_ctx config value doesn't raise
        # ValueError mid-send and eat the user's typed message before it
        # gets persisted (audit H16).
        max_ctx_tokens = max(512, _num_ctx_of(self.agent_cfg) - 2000)

        # Audible "send received, generating" cue — low tone
        self._chime("send")

        # Cold-start hint: if no reply chunk arrives within 8 seconds,
        # paint a "[Loading model into memory…]" block in the chat
        # display so the user knows what they're waiting for. Ollama
        # unloads idle models after 5 min of inactivity (OLLAMA_KEEP_ALIVE
        # default); reloading a 26B-param model into VRAM takes 20-60s
        # before inference starts. Big num_ctx adds more. The hint is
        # informational; the actual reply lands below it when it arrives.
        # Cancelled by _on_stream_chunk / _on_tool_loop_done / errors.
        self._cold_start_timer = wx.CallLater(
            8000, self._show_cold_start_hint, my_gen,
        )

        # Per-kin tools allowlist decides which worker runs (resolved
        # above for prompt fencing). Empty list → streaming as before.
        # Non-empty → non-streaming tool-loop path (run_tool_loop is sync
        # because the model's tool-call decisions need to be resolved
        # before the next response chunk arrives).

        def worker():
            nonlocal messages
            try:
                # Per-turn memory retrieval — moved OFF the UI thread. It can
                # make a network embedding call for semantic recall; embed_texts
                # now has a timeout so a slow embed host degrades to BM25 instead
                # of hanging. Fail-soft: leaves `messages` unchanged on any
                # problem. Both branches below send this same `messages`.
                try:
                    from memory_recall import inject_into_messages
                    messages, self._last_recall_used = inject_into_messages(
                        messages, self.current_agent,
                        num_ctx=_num_ctx_of(self.agent_cfg), cfg=self.agent_cfg)
                except Exception:
                    pass
                # A kin with no staging-read and no write tool can't reach its
                # own pending notes at all, so hand them over inline. No-op for
                # every kin that has the tools. See toolless_memory.py.
                try:
                    import toolless_memory
                    messages, self._toolless_scopes = toolless_memory.inject(
                        messages, self.current_agent, enabled_tool_names,
                        model=model)
                except Exception:
                    self._toolless_scopes = []
                if enabled_tool_names:
                    self._run_tool_loop_inline(
                        my_gen, model, messages, options, think, cache,
                        max_ctx_tokens, enabled_tool_names,
                        think_effort=think_effort,
                        cache_ttl=cache_ttl,
                        openrouter_provider=openrouter_provider,
                    )
                else:
                    self._run_streaming_inline(
                        my_gen, model, messages, options, think, cache,
                        max_ctx_tokens,
                        think_effort=think_effort,
                        cache_ttl=cache_ttl,
                        openrouter_provider=openrouter_provider,
                    )
            except llm_backend.OpenRouterRateLimitError as e:
                retry = f" Try again in {e.retry_after}s." if e.retry_after else ""
                wx.CallAfter(self._on_stream_error, my_gen,
                             f"Rate limited on {model}.{retry}")
            except Exception as e:
                wx.CallAfter(self._on_stream_error, my_gen, str(e))

        # What a park turn needs to ask this kin for its NEXT move. Stashed
        # here rather than rebuilt in the park worker for the same reason
        # Telegram builds its `ask` at the send site: this is where the model,
        # the options and this turn's messages already are, and reassembling
        # them down there would be a second copy of the send path, free to
        # drift from this one. Keyed by generation so a park loop from an
        # abandoned turn can tell it is stale.
        self._park_continuation = {
            "gen": my_gen,
            "model": model,
            "messages": list(messages),
            "options": options,
            "cache": cache,
            "cache_ttl": cache_ttl,
            "openrouter_provider": openrouter_provider,
            "max_ctx_tokens": max_ctx_tokens,
        }
        threading.Thread(target=worker, daemon=True).start()
        return True

    def _run_streaming_inline(self, my_gen, model, messages, options, think, cache, max_ctx_tokens, *, think_effort=None, cache_ttl="auto", openrouter_provider=None):
        """Streaming chat path. Used when the kin has no tools enabled —
        preserves the sentence-by-sentence paint experience. Runs on the
        worker thread; UI updates via wx.CallAfter.

        `think_effort` (kwarg) carries the four-state tier when the
        caller has it; `think` boolean is kept for older code paths
        that haven't migrated yet (llm_backend.chat resolves them).

        `show_thinking` is read from the active kin's cfg and passed
        through to llm_backend so reasoning gets excluded from the
        OpenRouter response when the user has turned the display off
        — required for models like MiMo that emit reasoning as
        content instead of into a separate field."""
        show_thinking = bool((self.agent_cfg or {}).get("show_thinking", False))
        # No-tools hint: when the kin has no tools enabled, surface that
        # explicitly so the model doesn't roleplay actions it can't take.
        # _inject_tool_use_hint handles both branches — empty tool_names
        # produces the "you have no tools, ask the operator" variant.
        messages = self._inject_tool_use_hint(list(messages), [])
        stream = llm_backend.chat(
            model, messages,
            options=options, think=think, think_effort=think_effort,
            show_thinking=show_thinking,
            stream=True, cache=cache, cache_ttl=cache_ttl,
            openrouter_provider=openrouter_provider,
            max_context_tokens=max_ctx_tokens,
            kin_name=self.current_agent,
            surface="desktop",
            ollama_host=resolve_kin_ollama_host(
                (self.agent_cfg or {}).get("ollama_host_name", "")),
        )
        # Same honesty as the tool path: say we are WAITING, not sending.
        # "Sending…" was set on the UI thread before this worker started and
        # was accurate for that instant; by here the request is out and we are
        # doing nothing but waiting, which on a cold local model is minutes.
        # Leaving "Sending…" up for four minutes describes the one part that
        # already finished. Both paths now use the same words for the same
        # state, so the Activity field reads consistently whether or not the
        # kin has tools. "Typing" still belongs solely to _on_stream_chunk.
        def _announce_waiting():
            self._set_status("Waiting for the model…")
            self._speak_status_phase("Waiting")
        wx.CallAfter(_announce_waiting)
        first_chunk = True
        for chunk in stream:
            if my_gen != self._stream_id:
                break
            if getattr(chunk, "done", False):
                break
            # SSE heartbeat (`:` comment line from OpenRouter) — bumps
            # the watchdog liveness counter but produces no content
            # and doesn't transition any UI phase.
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
                wx.CallAfter(self._on_stream_chunk, my_gen, content)
            if thinking:
                wx.CallAfter(self._on_stream_thinking_chunk, my_gen, thinking)
        wx.CallAfter(self._on_stream_done, my_gen)

    def _run_tool_loop_inline(self, my_gen, model, messages, options, think, cache, max_ctx_tokens, enabled_tool_names, *, think_effort=None, cache_ttl="auto", openrouter_provider=None):
        """Tool-calling chat path. Non-streaming: the model gets schemas,
        each tool_call gets executed and painted to chat_display via the
        on_tool_call callback, and the final ChatResult is handed to
        `_on_tool_loop_done` which routes through `_on_stream_done` for
        the rest of the standard post-reply handling (save, NVDA, chime,
        distillation tally).

        Intermediate tool-call and tool-result messages ARE persisted:
        _on_tool_loop_done stashes them in _pending_tool_history, and
        _on_stream_done splices them into self.conversation between the
        user message and the final assistant reply (the Bug-A fix,
        2026-05-11). The kin sees its own past tool calls on subsequent
        turns; older round-trips get compacted to one-line summaries by
        _compact_tool_history per the per-kin tool_history_keep."""
        # A scheduled-tend wake-up routes through here too (cron inject to
        # the active kin), not only through hearthkin_cron. Detect it so the
        # staging tools stay present for tending even if staging momentarily
        # reads empty. The wake-up frame's bracket prefix is stable even when
        # an operator edits the body of the wake_up_frame prompt.
        cron_turn = any(
            isinstance(m, dict) and m.get("role") == "user"
            and isinstance(m.get("content"), str)
            and "[hearthkin: scheduled wake-up" in m["content"]
            for m in messages[-3:]
        )
        schemas, executor = kin_tools.load_tools(
            enabled_tool_names,
            context={"agent_name": self.current_agent},
            model=self._current_chat_model_clean(),
            cron_turn=cron_turn,
        )
        # Wrap the exec executor with harness-side approval if it's in
        # the enabled set. Other executors pass through unchanged. The
        # wrapping happens here (not in tools/__init__.py) because the
        # approval flow depends on Hearthkin's wxPython frame + worker-
        # thread Event plumbing — tools/ stays UI-free.
        executor = self._wrap_exec_executor(executor, self.current_agent)
        # Auto-inject a tool-use nudge into the system prompt. Many local
        # models (Gemma especially) default to "I'm a language model, I
        # have no filesystem access" boilerplate even when proper tool
        # schemas are provided alongside the request. Without this hint
        # the model's training pattern-matches the question to a refusal
        # and never tries the tool call. Verified empirically: gemma4:26b
        # refuses cold but emits a clean tool_call when nudged.
        messages = self._inject_tool_use_hint(
            list(messages), [s["function"]["name"] for s in schemas],
        )

        # First-chunk chime up front — tool-loop is non-streaming, so the
        # next audible cue won't fire until the whole exchange is done.
        self._chime("first")

        # Announce up front that the turn is UNDERWAY — tool-calling turns
        # produce no content to stream, so during tool execution the chat
        # would otherwise go silent until the final talking turn begins.
        #
        # It must not say "Typing", which it did until 2026-08-07. "Typing"
        # means words are arriving, and on this path nothing had arrived yet —
        # the announcement fired the instant the request was dispatched. So a
        # request that never even started at the far end looked identical to a
        # reply coming in: forty minutes of "Typing…", with the work chime
        # still sounding, for a model that was queued behind another one and
        # never loaded. Being unable to tell a slow reply from a stuck one is
        # what made a whole day of slowness impossible to diagnose from the
        # chair. "Typing" is now only ever said by _on_stream_chunk, when a
        # content chunk has genuinely landed.
        #
        # Marshal through wx.CallAfter because we're on the worker thread.
        def _announce_sent():
            self._set_status("Waiting for the model…")
            self._speak_status_phase("Waiting")
        wx.CallAfter(_announce_sent)

        def on_tool_call(name, args, result, is_error):
            if my_gen != self._stream_id:
                return
            wx.CallAfter(self._on_tool_call_display, my_gen, name, args, result)

        # Stream the kin's talking turn live — reuse the exact sentence-paint
        # + NVDA-speech machinery the tool-less path already uses, via the
        # run_tool_loop on_content keystone. on_turn clears the paint buffer at
        # each turn boundary so the streamed reply ends up holding ONLY the
        # final turn's content (tool-turn preamble is already saved in the
        # round-trip; see _reset_tool_stream_buf).
        def on_content(text):
            if my_gen != self._stream_id:
                return
            wx.CallAfter(self._on_stream_chunk, my_gen, text)

        def on_turn():
            if my_gen != self._stream_id:
                return
            wx.CallAfter(self._reset_tool_stream_buf, my_gen)

        # Signals _on_tool_loop_done that content was streamed live into
        # _stream_buf / _paint_cursor, so it must NOT overwrite them.
        self._tool_stream_active = True

        show_thinking = bool((self.agent_cfg or {}).get("show_thinking", False))
        try:
            tool_result_cap = int((self.agent_cfg or {}).get("tool_result_cap", 8000))
        except (TypeError, ValueError):
            tool_result_cap = 8000
        result = llm_backend.run_tool_loop(
            model, messages,
            tools=schemas, tool_executor=executor,
            options=options, cache=cache, cache_ttl=cache_ttl,
            openrouter_provider=openrouter_provider,
            on_tool_call=on_tool_call,
            on_content=on_content, on_turn=on_turn,
            think=think, think_effort=think_effort,
            show_thinking=show_thinking,
            max_context_tokens=max_ctx_tokens,
            tool_result_cap=tool_result_cap,
            kin_name=self.current_agent,
            surface="desktop-tool",
            max_iterations=int((self.agent_cfg or {}).get("max_tool_iterations", 8) or 8),
            ollama_host=resolve_kin_ollama_host(
                (self.agent_cfg or {}).get("ollama_host_name", "")),
        )
        if my_gen != self._stream_id:
            return
        self._maybe_log_tool_name_as_text(
            self.current_agent, model, result,
            [s["function"]["name"] for s in schemas],
        )
        wx.CallAfter(self._on_tool_loop_done, my_gen, result)

    def _on_tool_loop_done(self, gen, result):
        """Hand the final ChatResult to the standard post-reply path by
        stuffing it into the streaming buffers. _on_stream_done then
        handles display, save, NVDA, chime, distillation tally identically
        to the streaming path. Also stashes the intermediate tool-call +
        tool-result turns from the loop so _on_stream_done can splice them
        into self.conversation between the user message and the final
        assistant reply — giving the kin a persistent memory of their own
        tool calls."""
        if self._closing:
            return
        # Tool-loop is non-streaming — the whole result lands here in
        # one shot. Cancel the cold-start hint regardless of whether
        # it fired during the wait.
        self._cancel_cold_start_timer()
        self._cancel_still_waiting_timer()
        if gen != self._stream_id:
            return
        if getattr(self, "_tool_stream_active", False):
            # Content was streamed live via on_content → _on_stream_chunk,
            # which already built _stream_buf (== result.content, thanks to the
            # per-turn on_turn reset) and advanced _paint_cursor. Leave them so
            # _on_stream_done paints only the unpainted tail — no double-paint.
            self._tool_stream_active = False
        else:
            # Non-streaming tool loop: the whole result lands here at once.
            self._stream_buf = result.content or ""
            self._paint_cursor = 0  # nothing painted live yet
        self._think_buf = result.thinking or ""
        self._pending_tool_history = list(getattr(result, "messages_added", []) or [])
        self._on_stream_done(gen)

    def _reset_tool_stream_buf(self, gen):
        """Clear the live-paint buffer at the start of each tool-loop turn
        (called via the run_tool_loop on_turn hook). Keeps _stream_buf holding
        only the FINAL talking turn's content — tool-turn preamble is already
        persisted in the round-trip (messages_added), so it must not also land
        in the saved reply. chat_display keeps whatever was already painted;
        only the buffer + cursor reset."""
        if gen != self._stream_id:
            return
        self._stream_buf = ""
        self._paint_cursor = 0

    def _inject_tool_use_hint(self, messages, tool_names, *, with_authoring_hint=True):
        """Append a tool-use nudge to the system message. The kin's soul
        is left intact; the hint just gets concatenated underneath it so
        the model sees both the persona and either the "you actually
        have these tools, use them" instruction (when tool_names is
        non-empty) or a "you currently have no tools — ask if you'd
        want one" instruction (when tool_names is empty).

        The no-tools branch closes the gap that produces the
        roleplayed-tending pattern: a kin without tools has no idea it
        has no tools, so it will happily roleplay reading staging notes
        that it cannot actually read. This hint
        makes the absence visible per-turn and gives the kin a script
        for what to do instead.

        Hint is ephemeral: it lives only in the request's system
        message, never gets persisted to conversation.jsonl."""
        # Hint text lives in ~/.hearthkin/prompts/ (editable); load_app_prompt
        # seeds + serves it. Shared verbatim with the Telegram surface.
        from kin_persistence import load_app_prompt
        if tool_names:
            hint = load_app_prompt("tool_use_hint", self.current_agent).replace(
                "{tools}", ", ".join(tool_names))
            # Kin that can write files get the authoring-bridge fallback too:
            # a low-load way to save a file (fenced ```write:<path>``` block, or
            # a *writes X* emote + fence) for when the big write_file argument
            # snags. See authoring_bridge.py + _maybe_run_authoring_bridge.
            if with_authoring_hint and {"write_file", "edit_file"} & set(tool_names):
                hint += load_app_prompt("authoring_bridge_hint", self.current_agent)
        else:
            hint = load_app_prompt("tool_use_hint_no_tools", self.current_agent)
        new_messages = []
        injected = False
        for m in messages:
            if not injected and m.get("role") == "system":
                copy = dict(m)
                copy["content"] = (m.get("content") or "") + hint
                new_messages.append(copy)
                injected = True
            else:
                new_messages.append(m)
        if not injected:
            new_messages = [{"role": "system", "content": hint.lstrip()}] + new_messages
        return new_messages

    def _turn_had_write_tool_call(self, turn_tool_history):
        """True if this turn's tool round-trips include a write-class tool
        call (write_file / edit_file). Lets the authoring-bridge nudge stay
        quiet when the kin actually did write via the structured tool."""
        for m in (turn_tool_history or []):
            if not isinstance(m, dict):
                continue
            for tc in (m.get("tool_calls") or []):
                name = (tc.get("function") or {}).get("name") or ""
                if name in ("write_file", "edit_file"):
                    return True
        return False

    def _maybe_run_authoring_bridge(self, reply_text, ts, turn_tool_history=None):
        """Authoring bridge (desktop): let a kin author a file in its natural
        text register — a ```write:<path>``` fence, or a *writes X* emote
        followed by a plain fenced block — and perform the structured write
        it would otherwise freeze on (see authoring_bridge.py). Gated on
        write_file / edit_file being enabled, so a chat-only kin is never
        touched.

        Commits any writes found, appends a confirming system note (persisted
        with the turn so the kin's next read knows what landed) and paints it
        to chat. When the reply gestured at a write but committed no content
        AND no write tool actually fired this turn, injects a one-line
        teach-nudge toward the fence convention instead of leaving the kin
        stuck. Best-effort: any failure is logged, never raised — the reply
        is already saved by the time this runs."""
        try:
            kin = self.current_agent
            if not kin:
                return
            enabled = set(load_kin_tools(kin) or [])
            if not ({"write_file", "edit_file"} & enabled):
                # A kin with no tools has no other way to write its memory at
                # all, so the bridge is its whole write path — but confined to
                # memory.md / memory/, and it archives the staging scopes it
                # was shown this turn. Every other tool-less kin (one with
                # read_staging, say) still returns here untouched.
                self._maybe_commit_toolless_memory(reply_text, ts, enabled)
                return
            import authoring_bridge
            writes = authoring_bridge.extract_authoring_writes(reply_text)
            if writes:
                results = authoring_bridge.commit_authoring_writes(kin, writes)
                oks = [(p, d) for (p, ok, d) in results if ok]
                errs = [(p, d) for (p, ok, d) in results if not ok]
                bits = []
                if oks:
                    saved = ", ".join(
                        f"{os.path.basename(str(p))} ({n} bytes)" for p, n in oks)
                    bits.append("saved from your reply: " + saved)
                for p, e in errs:
                    bits.append(f"could NOT save {p!r} — {e}")
                note = load_app_prompt("authoring_bridge_result", kin).replace(
                    "{results}", "; ".join(bits))
                self.chat_display.AppendText("\n" + note + "\n")
                self.conversation.append(
                    {"role": "system", "content": note, "ts": ts})
                self._set_status(
                    f"Authoring bridge saved {len(oks)} file(s)"
                    + (f", {len(errs)} failed" if errs else ""))
                return
            # Nothing committed. If the reply clearly meant to write a file and
            # no write tool fired either, nudge toward the fence convention.
            if self._turn_had_write_tool_call(turn_tool_history):
                return
            if authoring_bridge.looks_like_write_gesture(reply_text):
                self.conversation.append({
                    "role": "system",
                    "content": load_app_prompt(
                        "authoring_write_nudge", kin),
                    "ts": ts,
                })
        except Exception as e:
            try:
                self._log(f"authoring bridge error: {e}")
            except Exception:
                pass

    def _maybe_commit_toolless_memory(self, reply_text, ts, enabled):
        """Write side of the no-tools memory loop (desktop). Commits any
        fenced blocks the kin authored — confined to its own memory — archives
        the staging scopes it was shown this turn, and persists a receipt with
        the turn so its next read knows what actually landed.

        Called only from the tool-less branch of _maybe_run_authoring_bridge.
        Best-effort: never raises; the reply is already saved by the time this
        runs."""
        try:
            import toolless_memory
            results, archived = toolless_memory.commit(
                self.current_agent, reply_text, enabled,
                shown_scopes=getattr(self, "_toolless_scopes", []) or [],
                model=(self.agent_cfg.get("model") or "").strip())
            note = toolless_memory.receipt(self.current_agent, results, archived)
            if not note:
                # Nothing landed. If the kin MEANT to keep something, say so in
                # its history — a silent miss leaves it believing it saved. Not
                # painted to chat: the tooled nudge isn't either, and the
                # streaming loop stays calm by design.
                nudge = toolless_memory.missed_write_nudge(
                    self.current_agent, reply_text, results)
                if nudge:
                    self.conversation.append(
                        {"role": "system", "content": nudge, "ts": ts})
                return
            self._toolless_scopes = []
            self.chat_display.AppendText("\n" + note + "\n")
            self.conversation.append(
                {"role": "system", "content": note, "ts": ts})
            oks = sum(1 for (_p, ok, _d) in results if ok)
            self._set_status(f"Saved {oks} memory file(s) from the reply"
                             + (f", archived {len(archived)} scope(s)"
                                if archived else ""))
        except Exception as e:
            try:
                self._log(f"toolless memory commit error: {e}")
            except Exception:
                pass

    def _maybe_nudge_read_gesture(self, reply_text, ts, turn_tool_history=None):
        """Reading-side prompting: when a kin narrates reading CONTENT (a
        content-reach like *reads through it*, NOT a presence reach like *looks
        at you*) but no read tool fired and nothing was auto-attached this turn,
        append a plain system note — narrating a read loads nothing. Gated on
        read_file being enabled. Suppressed when a file was shared this turn (the
        content IS in front of it) or when read_file/memory_search/read_staging
        actually fired. See reading_bridge.py. Best-effort; never raises."""
        try:
            kin = self.current_agent
            if not kin or "read_file" not in set(load_kin_tools(kin) or []):
                return
            if getattr(self, "_shared_files_this_turn", None):
                return  # content was placed in front of it — no nudge
            for m in (turn_tool_history or []):
                if not isinstance(m, dict):
                    continue
                for tc in (m.get("tool_calls") or []):
                    name = (tc.get("function") or {}).get("name") or ""
                    if name in ("read_file", "memory_search", "read_staging"):
                        return  # it actually read something
            import reading_bridge
            reach = reading_bridge.looks_like_read_gesture(reply_text)
            if reach:
                self.conversation.append({
                    "role": "system",
                    "content": load_app_prompt(
                        "read_gesture_nudge", kin).replace(
                            "{reach}", str(reach[:60])),
                    "ts": ts,
                })
        except Exception as e:
            try:
                self._log(f"read-gesture nudge error: {e}")
            except Exception:
                pass

    def _maybe_route_park_command(self, reply_text, ts, user_text=""):
        """Run the `> command` a kin put in its DESKTOP reply.

        The `> ` park convention existed on exactly two surfaces: Telegram
        (telegram_bot._route_park_command) and the cron keeper. Not here. So a
        kin talking to the operator in the main window wrote `> look at the
        village`, nothing read the line, nothing ran it, and nothing said so --
        it was indistinguishable from the park ignoring it. Meanwhile
        park_chat_hint, which TELLS the kin to use this convention, is injected
        for any kin whose park mode is chat or keeper, on every surface. We
        taught a mechanism that only worked in two places out of three.

        Observed: a keeper asked for its village twice in desktop chat, in
        correct syntax, and got silence -- while the same kin's `> care for
        everyone` on a scheduled wake-up minutes earlier had worked fine.

        Shares park_keeper.route_reply and the park_result_single prompt with
        the other two surfaces, so what a kin is told about its own move can't
        drift by surface again. Gated on the kin's `park` setting, best-effort
        throughout: a park error must never damage a reply that already landed.

        It closed the gap where this surface didn't read the `>` line at all,
        and stopped there — it ran ONE move and the turn ended. That is the
        other half of the same hole: a kin that cannot look *and* act spends
        its only move looking, and looking is what you do when you cannot act
        on what you see. The whole turn now runs, through the loop shared with
        Telegram (``park_keeper.play_turn``), on a worker thread — see
        ``_start_park_turn`` for why it cannot be done here.
        """
        try:
            kin = self.current_agent
            if not kin:
                return
            # A cron wake-up painted into this window already had its park
            # hook run by the cron path (hearthkin_cron / _run_isolated), and
            # that path must stay the single owner of a keeper's scheduled
            # turn so it behaves identically whether the app was open. Running
            # here too would execute the kin's move TWICE -- fed twice, bred
            # twice -- which is worse than never running it at all.
            if _is_cron_user_text(user_text):
                return
            mode = str((load_agent_config(kin) or {}).get("park", "off") or "off")
            if mode.strip().lower() not in ("chat", "keeper"):
                return
            import park_keeper
            from tools import get_game
            host = get_game("tff")
            if host is None:
                return
            # Nothing to run is not an event. Checked before the reachability
            # probe so an ordinary reply with no `>` line stays completely
            # silent — and so an unreachable park is only ever announced to
            # someone who actually tried to move in it.
            # A quote refused by the guard must be SAID, not swallowed. This
            # gate used the singular extractor, which returns "" for a refusal
            # exactly as it does for "no command here" — so a kin whose move
            # was declined got silence, and so did the person. Silence is the
            # one answer that looks identical to the park having agreed.
            _cmds, _quote_why = park_keeper.extract_command_run(reply_text)
            if _quote_why:
                self._append_block(
                    "park",
                    f"Nothing was run from that: {_quote_why}. The words are "
                    f"safe and nothing in the park changed. A move needs to be "
                    f"on its own line.")
                return
            if not _cmds:
                return
            # An unreachable park is not a turn — see GameHost.reachable. Say
            # so in the window rather than running the move and painting a raw
            # connection error as though it were something the kin did.
            ok, why = host.reachable(kin)
            if not ok:
                host.log_unreachable(kin, why, "desktop")
                self._append_block(
                    "park",
                    "The park isn't reachable right now, so that move "
                    "didn't run and nothing was changed.")
                return
            self._start_park_turn(kin, host, reply_text, ts)
        except Exception as e:
            try:
                self._log(f"park routing error: {e}")
            except Exception:
                pass

    def _start_park_turn(self, kin, host, reply_text, ts):
        """Play this kin's whole park turn on a worker thread.

        **Why a thread.** This is reached from ``_on_stream_done``, on the UI
        thread. A turn of park is one or more model calls, and a blocking model
        call here freezes the window — the exact shape of the un-timeout'd
        embed that made this app permanently unkillable on send. So the loop
        runs on a worker and everything it wants to show goes back through
        ``wx.CallAfter``; the ``_on_refresh_models`` pattern.

        **Why the generation guard.** ``gen`` is this turn's ``_stream_id``.
        Sending again bumps it, and so does the Stop button — one mechanism
        already means both "the person moved on" and "the person said stop",
        so the loop needs no second cancel channel. Every callback re-checks
        it, or a loop from an abandoned turn could paint into a later one.

        **What happens with no continuation context.** ``ask`` is passed only
        when ``_park_continuation`` belongs to THIS generation. Without it the
        turn is exactly one move — the old behaviour — rather than a guess at
        how to rebuild this kin's prompt. Degrading to the previous behaviour
        is the right failure here; inventing a second prompt builder is not.

        The continuation asks for park moves only, offering no tools even to a
        kin that has them: what is being asked for is the next move in a game,
        not a work session. A kin that wants a tool stops writing `>` lines
        and says so.
        """
        import park_keeper
        ctx = dict(getattr(self, "_park_continuation", None) or {})
        gen = self._stream_id
        can_ask = bool(ctx.get("model")) and ctx.get("gen") == gen
        msgs = list(ctx.get("messages") or [])
        if can_ask:
            msgs.append({"role": "assistant", "content": reply_text})
        show_thinking = bool((self.agent_cfg or {}).get("show_thinking", False))
        ollama_host = resolve_kin_ollama_host(
            (self.agent_cfg or {}).get("ollama_host_name", ""))

        def _stale():
            """Stop signal AND generation guard. Doubles as `should_stop` for
            the model call, so pressing Stop cuts the current call short as
            well as ending the loop."""
            try:
                return bool(self._closing) or gen != self._stream_id
            except Exception:
                return False

        def _run_move(text):
            if _stale():
                return False
            cmd, res = park_keeper.route_reply(
                text, lambda c, s="": host.run(kin, c, say=s))
            if not res:
                return False
            # The person sees the plain result; the kin's copy carries the
            # trimmings (what other tenants did, and one thing worth doing on
            # a look) exactly as on Telegram — never bare `run()`.
            try:
                kin_res = host.decorate(kin, cmd, res)
            except Exception:
                kin_res = res
            note = (load_app_prompt("park_result_single", kin)
                    .replace("{command}", str(cmd))
                    .replace("{result}", str(kin_res)))
            # Into the worker's own message list directly, rather than reading
            # it back off self.conversation the way Telegram does. That append
            # happens on the UI thread via CallAfter, so reading it back here
            # would be a race — the next ask could go out without the result
            # of the move it is supposed to be reacting to.
            msgs.append({"role": "system", "content": note})
            wx.CallAfter(self._on_park_move_done, gen, cmd, res, note, ts)
            return True

        def _ask():
            if _stale():
                return ""
            r = llm_backend.chat_collect(
                ctx["model"], msgs,
                options=ctx.get("options"),
                should_stop=_stale,
                show_thinking=show_thinking,
                cache=ctx.get("cache"),
                cache_ttl=ctx.get("cache_ttl", "auto"),
                openrouter_provider=ctx.get("openrouter_provider"),
                max_context_tokens=ctx.get("max_ctx_tokens"),
                kin_name=kin,
                surface="desktop-park",
                ollama_host=ollama_host,
            )
            if getattr(r, "stopped", False):
                return ""
            nxt = (getattr(r, "content", "") or "").strip()
            if not nxt:
                return ""
            # Same cleanup the main desktop reply gets. This text is persisted
            # into the kin's own history, and desktop context carries "[Name]"
            # prefixes on imported multi-party turns — the material that
            # teaches a small model to write other people's turns.
            nxt, _imp = clean_kin_reply(
                nxt, kin, known_speakers=self._other_speakers_in_history())
            if not nxt.strip():
                return ""
            msgs.append({"role": "assistant", "content": nxt})
            wx.CallAfter(self._on_park_reply, gen, nxt, ts)
            return nxt

        def worker():
            result = None
            try:
                result = park_keeper.play_turn(
                    kin, reply_text,
                    run_move=_run_move,
                    ask=_ask if can_ask else None,
                    awaiting=lambda: host.awaiting_answer(kin),
                    cancelled=_stale,
                )
            except Exception as e:
                try:
                    self._log(f"park turn error: {e}")
                except Exception:
                    pass
            wx.CallAfter(self._on_park_turn_done, gen, kin, result)

        try:
            self._park_workers.add(kin)
        except Exception:
            pass
        # Posted rather than set directly: _on_stream_done is still running and
        # will re-enable Send / disable Stop after this returns, which would
        # undo it. A nested CallAfter lands after that.
        wx.CallAfter(self._park_turn_ui_begin, gen, kin)
        threading.Thread(target=worker, daemon=True).start()

    def _park_turn_ui_begin(self, gen, kin):
        """Say that the turn is still going, and leave Stop reachable.

        Send stays ENABLED on purpose. Sending again bumps `_stream_id`, which
        the loop reads as stale and stops on — so a new message is already a
        clean way out. Disabling it would mean a park worker that somehow never
        finished left the window unusable, and a stuck app is a worse bug than
        a turn that overlaps a message."""
        if self._closing or gen != self._stream_id:
            return
        try:
            self.stop_btn.Enable()
        except Exception:
            pass
        try:
            self._set_status(f"{kin} is taking its park turn…")
        except Exception:
            pass

    def _on_park_move_done(self, gen, cmd, res, note, ts):
        """One move landed: paint it and give the kin its ground truth."""
        if self._closing or gen != self._stream_id:
            return
        # A BARE header. _append_block wraps whatever it is given in brackets
        # -- "You" paints as [You] -- so handing it "[park] ..." painted
        # "[[park] care for the night sky]".
        self._append_block(f"park: {cmd}", res)
        self.conversation.append({"role": "system", "content": note, "ts": ts})
        self._persist_current_conversation()

    def _on_park_reply(self, gen, text, ts):
        """The kin's words between moves — painted and saved like any reply, so
        a park turn reads as one continuous turn rather than a row of results
        with the kin's voice missing from between them."""
        if self._closing or gen != self._stream_id:
            return
        self._append_block(self.current_agent or "Model", text)
        self.conversation.append({"role": "assistant", "content": text,
                                  "ts": ts})
        self._persist_current_conversation()

    def _on_park_turn_done(self, gen, kin, result):
        """Turn over. Always de-registers, even when the turn was abandoned —
        a park worker left on that list would make confirm-on-close warn about
        work that finished hours ago."""
        try:
            self._park_workers.discard(kin)
        except Exception:
            pass
        if self._closing or gen != self._stream_id:
            return
        try:
            self.stop_btn.Disable()
        except Exception:
            pass
        # Only a spent ALLOWANCE is worth saying. A kin that stopped writing
        # `>` lines has finished, and narrating that would put a line under
        # every ordinary ending. It goes to the person AND into the kin's
        # history for the same reason it goes into the chat on Telegram: the
        # kin reads its own history and would otherwise start over next turn
        # instead of carrying on.
        if result is not None and result.spent_allowance:
            try:
                spent = load_app_prompt("park_moves_spent", kin).replace(
                    "{moves}", str(result.moves))
                self._append_block("park", spent)
                self.conversation.append({"role": "system", "content": spent,
                                          "ts": now_iso()})
                self._persist_current_conversation()
            except Exception:
                pass
        try:
            self._set_status("Ready.")
        except Exception:
            pass

    def _on_tool_call_display(self, gen, name, args, result):
        """Append a tool-call + result block to chat_display. Runs on the
        UI thread via wx.CallAfter from the tool-loop worker. Args are
        formatted concisely (truncated past 200 chars); result preview
        capped at 500 chars so a giant tool output doesn't dominate the
        chat view — the full result still goes to the model regardless."""
        if self._closing:
            return
        if gen != self._stream_id:
            return
        try:
            args_str = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())
        except Exception:
            args_str = "?"
        if len(args_str) > 200:
            args_str = args_str[:200] + "..."
        result_str = result if isinstance(result, str) else str(result)
        if not result_str:
            result_str = "[empty result]"
        if len(result_str) > 500:
            result_str = result_str[:500] + "\n... [truncated for display; full result sent to model]"
        block = f"\n[tool: {name}({args_str})]\n{result_str}\n"
        start_pos = self.chat_display.GetLastPosition()
        self.chat_display.AppendText(block)
        end_pos = self.chat_display.GetLastPosition()
        # Reuse the reasoning style (gray italic) so tool blocks read as
        # "framework activity" visually distinct from the kin's voice.
        if hasattr(self, "_reasoning_style") and self._reasoning_style is not None:
            try:
                self.chat_display.SetStyle(start_pos, end_pos, self._reasoning_style)
            except Exception:
                pass

    def _on_stream_thinking_chunk(self, gen, text):
        # Bail if shutdown started before this queued callback fires —
        # otherwise we paint into widgets being destroyed (audit H8).
        if self._closing:
            return
        if gen != self._stream_id:
            return
        # First reasoning chunk landed — cancel the cold-start hint and
        # transition into the "Thinking" phase (speech + status text).
        self._cancel_cold_start_timer()
        self._cancel_still_waiting_timer()
        # Thinking chunks are proof of life from the model. Tick the
        # watchdog counter so a reasoning model that thinks for a long
        # time before producing any content doesn't get killed by the
        # hang-recovery watchdog. (Bug fix: thinking chunks used to be
        # invisible to the watchdog, so reasoning models were getting
        # cut off mid-think.)
        self._stream_chunks_seen = getattr(self, "_stream_chunks_seen", 0) + 1
        if getattr(self, "_spoken_phase", None) != "Thinking":
            self._set_status("Thinking…")
            self._speak_status_phase("Thinking")
        self._think_buf += text

    def _on_stream_chunk(self, gen, text):
        # Bail on shutdown to avoid painting into destroyed widgets
        # (audit H8).
        if self._closing:
            return
        if gen != self._stream_id:
            return
        # First chunk arrived — cancel the cold-start "Loading model"
        # hint timer (no-op if it already fired or was already cancelled).
        self._cancel_cold_start_timer()
        self._cancel_still_waiting_timer()
        # Tell the watchdog the stream is alive so it doesn't pull
        # the rug out 60s in. The watchdog only fires if THIS
        # counter stayed at 0 for the full window.
        self._stream_chunks_seen = getattr(self, "_stream_chunks_seen", 0) + 1
        # First content chunk transitions us into the "Typing" phase.
        # If we were already in "Thinking" (reasoning model just finished
        # its think block), _speak_status_phase still announces the
        # transition because the new phase name differs. Status text
        # updates in lockstep so a sighted user sees the same change.
        if getattr(self, "_spoken_phase", None) != "Typing":
            self._set_status("Typing…")
            self._speak_status_phase("Typing")
        # Accumulate then paint at sentence boundaries. Per-token paint floods
        # NVDA with text-changed events; per-reply paint means the whole answer
        # arrives in one chunk. Sentence-by-sentence: one coherent text-changed
        # event per sentence — readable as it streams, no flood.
        self._stream_buf += text
        end = _last_sentence_end(self._stream_buf, self._paint_cursor)
        if end is not None and end > self._paint_cursor:
            sentence = self._stream_buf[self._paint_cursor:end]
            self.chat_display.AppendText(sentence)
            self._paint_cursor = end
            # Fire the freshly-completed sentence at TTS in parallel.
            # The voice engine queues + plays asynchronously; this
            # call returns immediately. No-op if voice is disabled
            # for the active kin or the kin has no voice_id picked.
            self._maybe_speak_sentence(sentence)
