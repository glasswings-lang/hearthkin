"""InputAttachMixin — extracted from hearthkin.pyw (2026-07 modularisation).

One cohesive slice of the Hearthkin frame's behavior. Mixed into the
Hearthkin(wx.Frame) class in hearthkin.pyw; every method here runs with
the frame as `self`. Moved verbatim — no logic changed.
"""
from frame_shared import (
    DEFAULT_NUM_CTX, _num_ctx_of, estimate_tokens, llm_backend, nvda_speak, os, threading,
    wx,
)


class InputAttachMixin:

    # NOTE: _on_refresh_models, _on_refresh_models_done,
    # _on_refresh_models_failed, and _refresh_model_list used to live
    # here. They were tied to the chat-model dropdown in the header,
    # which moved into the Settings dialog (2026-05-13). The dialog
    # owns its own model widget and its own refresh button now.

    def _on_input_changed(self, event):
        self._update_token_display()

    def _update_token_display(self):
        # `or ""` so content=None on tool-call-only assistant turns doesn't
        # break the join. Tool-result turns still contribute their text.
        if self.current_room is not None:
            convo = "\n".join((m.get("content") or "") for m in self.room_conversation)
            input_text = self.input_box.GetValue()
            total = estimate_tokens(convo) + estimate_tokens(input_text)
            self.token_label.SetValue(f"≈ {total} room tokens (per-kin ctx varies)")
            self._refresh_usage_display()
            return
        # AUTHORITATIVE NUMBER FIRST: the provider's reported prompt-tokens
        # from the most recent blocking call. That's what actually went out
        # on the wire, post-truncation, post-tool-schemas — the only number
        # that reflects reality. Same fix shape as commit 291a6d1 applied
        # to context_status: the full archive divided by num_ctx is NOT
        # what fills the window. Truncation handles the archive. Only the
        # send-side number matters for "% of cap."
        ctx = _num_ctx_of(self.agent_cfg) if self.current_agent else DEFAULT_NUM_CTX
        real_in = None
        if self.current_agent:
            try:
                real_in = llm_backend.last_reported_prompt_tokens(
                    self.current_agent)
            except Exception:
                real_in = None
        if real_in:
            pct = (real_in / ctx * 100) if ctx > 0 else 0
            self.token_label.SetValue(
                f"≈ {real_in:,} sent last turn / {ctx:,} ctx "
                f"({pct:.0f}%)")
            self._refresh_usage_display()
            return
        # No real number yet this session — fall back to an estimate of
        # what would be sent NEXT. This is bounded by num_ctx (truncation
        # caps the send), so cap the estimated total at ctx so we don't
        # show 300%-cap warnings for kin whose archive is large but whose
        # actual sends are normal.
        soul = self._soul_cache if self.current_agent else ""
        memory = self._memory_cache if self.current_agent else ""
        model = self._current_chat_model_clean() if self.current_agent else ""
        convo_tokens = self._conversation_token_estimate(model)
        input_text = self.input_box.GetValue()
        raw_total = (estimate_tokens(soul) + estimate_tokens(memory)
                     + convo_tokens + estimate_tokens(input_text))
        # Send size is capped by num_ctx — the input-truncation in chat()
        # ensures soul + memory + truncated_convo + reply_headroom fits
        # within num_ctx. So the displayed "would-be-sent" estimate caps
        # at num_ctx, never above.
        capped_total = min(raw_total, ctx)
        pct = (capped_total / ctx * 100) if ctx > 0 else 0
        self.token_label.SetValue(
            f"≈ {capped_total:,} estimated next send / {ctx:,} ctx "
            f"({pct:.0f}%)")
        self._refresh_usage_display()

    # --- Chat / streaming --- #

    def _on_input_key(self, event):
        code = event.GetKeyCode()
        if code == wx.WXK_RETURN:
            if event.ControlDown():
                self._on_send(None)
                return
            if self.config.get("enter_sends") and not event.ShiftDown():
                self._on_send(None)
                return
        if code == wx.WXK_ESCAPE and self._streaming:
            self._on_stop(None)
            return
        event.Skip()

    def _refresh_attach_button_state(self):
        """Update the Attach Image + Take Photo buttons' enabled state
        to match the active kin's model. Called from _load_agent,
        _change_kin_model, and on the initial UI build. Rooms hide
        both buttons entirely (image attachments + webcam for rooms
        deferred — see the project memo on @-mention routing). When
        the model isn't vision-capable the buttons stay VISIBLE but
        disabled so users know the features exist; tooltips explain
        why they're dimmed."""
        if not hasattr(self, "attach_btn") or self.attach_btn is None:
            return
        in_room = self.current_room is not None
        if in_room:
            self.attach_btn.Hide()
            if hasattr(self, "take_photo_btn"):
                self.take_photo_btn.Hide()
            self.attached_label.Hide()
            self.clear_attach_btn.Hide()
            # Pending attachment makes no sense in room mode; drop it
            # so a later kin-switch doesn't carry it forward by accident.
            self._pending_attachment = None
            self._pending_attachment_rel = None
            return
        self.attach_btn.Show()
        if hasattr(self, "take_photo_btn"):
            self.take_photo_btn.Show()
        # Make the show/hide take effect now (the buttons just became
        # visible coming out of room mode); the enabled state + tooltips
        # get set once we know the model's vision capability.
        if self.attach_btn.GetParent():
            self.attach_btn.GetParent().Layout()
        model = self._current_chat_model_clean() if self.current_agent else ""
        if not model:
            self._apply_attach_capability(model, False)
            return
        # Resolve vision capability OFF the UI thread. model_supports_images
        # may hit /api/show over the network for a remote Ollama, which
        # BLOCKED the main thread for up to its timeout on the first switch
        # to any given model — the "lag switching between kin" freeze.
        # Cached lookups (every switch after the first this session) resolve
        # in ~1ms, so the tentative "checking" state below is invisible in
        # the common case; a cold remote probe just leaves the buttons
        # briefly disabled instead of freezing the whole app.
        self.attach_btn.Enable(False)
        if hasattr(self, "take_photo_btn"):
            self.take_photo_btn.Enable(False)
        self.attach_btn.SetToolTip("Checking whether this model accepts images…")
        # Monotonic token: only the most recent probe's result is applied,
        # so switching kin faster than a cold probe returns can't leave a
        # stale enabled state behind.
        self._attach_probe_token = getattr(self, "_attach_probe_token", 0) + 1
        my_token = self._attach_probe_token

        def _probe():
            try:
                supports = llm_backend.model_supports_images(model)
            except Exception:
                supports = False
            wx.CallAfter(
                self._on_attach_capability_probed, my_token, model, supports)

        threading.Thread(target=_probe, daemon=True).start()

    def _on_attach_capability_probed(self, token, model, supports):
        """Apply the background vision-capability probe result, unless it's
        stale — the kin/model changed, we entered a room, or the app is
        closing since the probe launched. Guarded by a monotonic token so
        only the most recent probe wins when kin are switched quickly."""
        if getattr(self, "_closing", False):
            return
        if token != getattr(self, "_attach_probe_token", None):
            return
        if self.current_room is not None:
            return
        current = self._current_chat_model_clean() if self.current_agent else ""
        if current != model:
            return
        self._apply_attach_capability(model, supports)

    def _apply_attach_capability(self, model, supports):
        """Set the attach / photo buttons' enabled state + tooltips for a
        resolved vision-capability result. Pure UI; main thread only."""
        if not hasattr(self, "attach_btn") or self.attach_btn is None:
            return
        self.attach_btn.Enable(bool(supports))
        if hasattr(self, "take_photo_btn"):
            self.take_photo_btn.Enable(bool(supports))
        if supports:
            self.attach_btn.SetToolTip(
                "Pick an image file to send with your next message."
            )
            if hasattr(self, "take_photo_btn"):
                self.take_photo_btn.SetToolTip(
                    "Capture a photo from your webcam (3-second "
                    "countdown), then send it with your next message."
                )
        else:
            tip = (
                "This kin's model doesn't support image input. "
                "Change to a vision-capable model in Settings → "
                "Model && generation to enable."
            )
            self.attach_btn.SetToolTip(tip)
            if hasattr(self, "take_photo_btn"):
                self.take_photo_btn.SetToolTip(tip)
            # If we had a staged attachment and the model just lost
            # vision capability, drop the stage and hide the label.
            if self._pending_attachment is not None or self._pending_attachment_rel is not None:
                self._pending_attachment = None
                self._pending_attachment_rel = None
                self.attached_label.Hide()
                self.clear_attach_btn.Hide()
        # Force the layout to pick up any show/hide changes. Cheap; safe.
        if self.attach_btn.GetParent():
            self.attach_btn.GetParent().Layout()

    def _on_attach_image(self, event):
        """File-picker handler for the Attach Image button. Stages the
        chosen file path in self._pending_attachment; the actual copy
        to the kin's attachments/ dir happens at send time (so canceling
        before send doesn't leave orphan files). Validates the size
        cap eagerly so the user finds out NOW rather than after typing
        a long message."""
        if not self.current_agent:
            self._set_status("Load a kin first.")
            return
        wildcard = (
            "Image files|*.jpg;*.jpeg;*.png;*.gif;*.webp"
            "|JPEG (*.jpg, *.jpeg)|*.jpg;*.jpeg"
            "|PNG (*.png)|*.png"
            "|GIF (*.gif)|*.gif"
            "|WebP (*.webp)|*.webp"
            "|All files (*.*)|*.*"
        )
        dlg = wx.FileDialog(
            self,
            message="Pick an image to send",
            wildcard=wildcard,
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        finally:
            dlg.Destroy()
        if not path:
            return
        # Up-front validation: size + extension. Better to refuse here
        # than to fail at send time after the user typed their message.
        from kin_persistence import ALLOWED_IMAGE_EXTS, MAX_ATTACHMENT_BYTES
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        if ext == "jpeg":
            ext = "jpg"
        if ext not in ALLOWED_IMAGE_EXTS:
            wx.MessageBox(
                f"That file's extension ({ext or 'none'}) isn't a supported "
                f"image format. Supported: JPG, PNG, GIF, WebP.",
                "Unsupported image", wx.OK | wx.ICON_WARNING,
            )
            return
        try:
            size = os.path.getsize(path)
        except OSError as e:
            wx.MessageBox(f"Couldn't read that file: {e}",
                          "Attach image", wx.OK | wx.ICON_WARNING)
            return
        if size > MAX_ATTACHMENT_BYTES:
            mb = size / (1024 * 1024)
            cap_mb = MAX_ATTACHMENT_BYTES // (1024 * 1024)
            wx.MessageBox(
                f"That image is {mb:.1f} MB — over the {cap_mb} MB cap. "
                f"Try resizing it or sending a smaller version.",
                "Image too large", wx.OK | wx.ICON_WARNING,
            )
            return
        self._pending_attachment = path
        name = os.path.basename(path)
        self.attached_label.SetValue(f"📎 {name}")
        self.attached_label.Show()
        self.clear_attach_btn.Show()
        self.attach_btn.GetParent().Layout()
        nvda_speak(f"Image attached: {name}")
        self._set_status(f"Image staged for next send: {name}")

    def _on_clear_attachment(self, event):
        if self._pending_attachment is None and self._pending_attachment_rel is None:
            return
        self._pending_attachment = None
        self._pending_attachment_rel = None
        self.attached_label.SetValue("")
        self.attached_label.Hide()
        self.clear_attach_btn.Hide()
        self.attach_btn.GetParent().Layout()
        nvda_speak("Image cleared")
        self._set_status("Image attachment cleared.")

    def _on_take_photo(self, event):
        """Webcam capture with a 3-2-1 countdown. Disables the button
        for the duration; status field + NVDA both narrate the
        countdown so blind users can pose. The actual capture runs
        on a worker thread so the wx event loop stays responsive
        (camera warmup + frame grab can take >100ms)."""
        if not self.current_agent:
            self._set_status("Load a kin first.")
            return
        model = self._current_chat_model_clean()
        if not llm_backend.model_supports_images(model):
            self._set_status(
                "This kin's model doesn't support images. Change "
                "to a vision-capable model first."
            )
            return
        self.take_photo_btn.Disable()
        self.attach_btn.Disable()
        self._countdown_remaining = 3
        nvda_speak("Taking photo in 3")
        self._set_status("Taking photo in 3...")
        wx.CallLater(1000, self._countdown_tick)

    def _countdown_tick(self):
        if self._countdown_remaining <= 1:
            nvda_speak("Capturing")
            self._set_status("Capturing photo from webcam...")
            threading.Thread(
                target=self._capture_photo_worker,
                args=(self.current_agent,),
                daemon=True,
            ).start()
            return
        self._countdown_remaining -= 1
        nvda_speak(str(self._countdown_remaining))
        self._set_status(f"Taking photo in {self._countdown_remaining}...")
        wx.CallLater(1000, self._countdown_tick)

    def _capture_photo_worker(self, kin_name):
        """Off-UI-thread webcam capture. Saves the JPEG bytes into
        the kin's attachments dir and marshals back to the main
        thread to stage the result. Errors come back the same way
        and surface in the status field."""
        try:
            from tools.use_webcam import capture_to_bytes
            data = capture_to_bytes()
            from kin_persistence import save_attachment_bytes
            rel = save_attachment_bytes(kin_name, data, mime_type="image/jpeg")
            wx.CallAfter(self._on_photo_captured_ok, kin_name, rel)
        except Exception as e:
            wx.CallAfter(self._on_photo_captured_err, str(e))

    def _on_photo_captured_ok(self, kin_name, rel_path):
        # If the user switched kin during the (very short) capture
        # window, drop the photo rather than stage it on the wrong
        # kin. Edge case but cheap to guard.
        if kin_name != self.current_agent:
            return
        # Drop any previously-staged image (file-picker or webcam)
        # so we end up with exactly one image staged.
        self._pending_attachment = None
        self._pending_attachment_rel = rel_path
        name = os.path.basename(rel_path) or rel_path
        self.attached_label.SetValue(f"📷 {name}")
        self.attached_label.Show()
        self.clear_attach_btn.Show()
        self.take_photo_btn.Enable()
        self.attach_btn.Enable()
        if self.attach_btn.GetParent():
            self.attach_btn.GetParent().Layout()
        nvda_speak("Photo captured")
        self._set_status(f"Photo staged for next send: {name}")

    def _on_photo_captured_err(self, error_text):
        self.take_photo_btn.Enable()
        self.attach_btn.Enable()
        nvda_speak("Photo capture failed")
        self._set_status(f"Couldn't capture photo: {error_text}")
        wx.MessageBox(
            f"Webcam capture failed:\n\n{error_text}\n\n"
            f"If opencv-python isn't installed, run:\n"
            f"  pip install opencv-python\n"
            f"(it's an optional dependency.)",
            "Take photo", wx.OK | wx.ICON_WARNING,
        )
