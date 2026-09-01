"""UsageMixin — extracted from hearthkin.pyw (2026-07 modularisation).

One cohesive slice of the Hearthkin frame's behavior. Mixed into the
Hearthkin(wx.Frame) class in hearthkin.pyw; every method here runs with
the frame as `self`. Moved verbatim — no logic changed.
"""
from frame_shared import (
    Path, _model_context_length, _num_ctx_of, estimate_message_tokens, estimate_tokens,
    json, llm_backend, strip_model_annotation, wx,
)


class UsageMixin:

    def _build_usage_tab(self, parent):
        """A read-only summary of the active kin/room's token usage, model,
        and context-window settings. Uses a multi-line TextCtrl rather than
        a StaticText so NVDA users can tab to it, arrow through the
        breakdown, and have the text actually spoken. Updates whenever the
        underlying state changes (kin/room load, input change, turn end),
        but does NOT auto-speak — the user reads it on demand, the same
        way a sighted user glances at it."""
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Read-only TextCtrl, not StaticText: usage_display below has its
        # own SetName, so it never adopts this as a buddy label and the
        # text — including the fact that it refreshes on its own — would
        # reach sighted users only.
        intro = wx.TextCtrl(
            parent,
            value="Usage estimates for the active conversation. Updates as you type and after each reply.",
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
        )
        intro.SetName("About this usage summary")
        intro.SetMinSize((-1, 40))
        sizer.Add(intro, flag=wx.ALL, border=8)

        self.usage_display = wx.TextCtrl(
            parent,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL | wx.TE_DONTWRAP,
        )
        self.usage_display.SetName("Usage summary")
        # Monospace so the column alignment in the breakdown stays readable.
        try:
            font = wx.Font(wx.FontInfo(10).Family(wx.FONTFAMILY_TELETYPE))
            self.usage_display.SetFont(font)
        except Exception:
            pass
        sizer.Add(self.usage_display, proportion=1,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        parent.SetSizer(sizer)
        self._refresh_usage_display()

    def _refresh_usage_display(self):
        """Rebuild the Usage tab contents from current state. Cheap — just
        re-estimates from already-in-memory strings — so safe to call on
        every input keystroke if needed. _update_token_display routes
        through here in addition to the inline label."""
        if not hasattr(self, "usage_display"):
            return  # tab not built yet (called early during __init__)
        try:
            text = self._build_usage_summary()
        except Exception as e:
            text = f"[usage display error: {e}]"
        try:
            self.usage_display.ChangeValue(text)
        except Exception:
            pass

    def _build_usage_summary(self):
        """Compose the multi-line usage breakdown shown on the Usage tab.
        Pure formatter — reads `self.conversation` / `self.room_conversation`,
        the active kin/room config, and the OpenRouter model cache for
        max-context lookup. No I/O beyond a single cached JSON read.

        Token counts are estimates (estimate_tokens uses ~4 chars/token);
        actual provider tokenizers differ but the ratio is close enough to
        steer "am I getting near my cap" decisions."""
        lines = []
        if self.current_room is not None:
            lines.append(f"Active: room \"{self.current_room}\"")
            members = self.room_cfg.get("members") or []
            if members:
                lines.append(f"Members: {', '.join(members)}")
            convo_text = "\n".join(
                (m.get("content") or "") for m in (self.room_conversation or [])
            )
            input_text = self.input_box.GetValue() if hasattr(self, "input_box") else ""
            convo_tokens = estimate_tokens(convo_text)
            input_tokens = estimate_tokens(input_text)
            lines.append("")
            lines.append(f"Conversation:   ~{convo_tokens:>7,} tokens")
            lines.append(f"Pending input:  ~{input_tokens:>7,} tokens")
            total = convo_tokens + input_tokens
            lines.append(f"{'─' * 32}")
            lines.append(f"Total:          ~{total:>7,} tokens")
            lines.append("")
            lines.append("Room context limits vary by kin; see each kin's config "
                         "for their num_ctx setting.")
            return "\n".join(lines)

        if not self.current_agent:
            return ("No kin or room loaded.\n\n"
                    "Pick a kin from the Chat tab to see token usage estimates.")

        cfg = self.agent_cfg or {}
        model = strip_model_annotation(str(cfg.get("model", "") or "")).strip()
        num_ctx = _num_ctx_of(cfg)

        # Use the in-memory soul/memory cache; this method is called
        # from _update_token_display on every input keystroke, so
        # re-reading from disk here would re-introduce the typing lag.
        # Same goes for the conversation estimate — memoized helper,
        # not an O(archive) walk per keystroke (audit M-F4).
        soul_text = self._soul_cache
        memory_text = self._memory_cache
        input_text = self.input_box.GetValue() if hasattr(self, "input_box") else ""

        soul_tokens = estimate_tokens(soul_text)
        memory_tokens = estimate_tokens(memory_text)
        convo_tokens = self._conversation_token_estimate(model)
        input_tokens = estimate_tokens(input_text)

        lines.append(f"Active: {self.current_agent}")
        lines.append(f"Model:  {model or '(unset)'}")
        max_ctx = self._lookup_model_max_context(model)
        if max_ctx:
            lines.append(f"Model max context: {max_ctx:,} tokens (provider-declared)")
        else:
            lines.append("Model max context: (unknown — cache miss or local model)")
        lines.append("")
        # The authoritative number: the provider's reported prompt-tokens
        # from the most recent send. That's what actually went out on the
        # wire, post-truncation, post-tool-schemas. None until the first
        # send of the session.
        try:
            real_in = llm_backend.last_reported_prompt_tokens(self.current_agent)
        except Exception:
            real_in = None

        lines.append(f"Your cap (num_ctx):  {num_ctx:,} tokens")
        effective_ceiling = max(512, num_ctx - 2000)
        lines.append(
            f"Effective cap:       {effective_ceiling:,} tokens "
            f"(num_ctx minus 2K reply headroom)"
        )
        lines.append("")
        if real_in:
            pct = real_in / effective_ceiling * 100 if effective_ceiling > 0 else 0
            lines.append(
                f"Most recent send:    {real_in:,} tokens "
                f"({pct:.0f}% of effective cap) — AUTHORITATIVE"
            )
            lines.append(
                "  (provider-reported actual prompt-tokens; this is the "
                "real number, not an estimate)")
            gauge = real_in
        else:
            lines.append(
                "Most recent send:    (none yet this session)")
            gauge = None
        lines.append("")
        lines.append("Fixed overhead (rides every turn regardless of chat length):")
        lines.append(f"  Soul:           ~{soul_tokens:>7,} tokens")
        lines.append(f"  Memory:         ~{memory_tokens:>7,} tokens")
        lines.append(f"  Pending input:  ~{input_tokens:>7,} tokens")
        lines.append("")
        lines.append(
            f"Conversation archive (full on-disk history): "
            f"~{convo_tokens:,} tokens")
        lines.append(
            "  This is the persisted record, NOT what's sent per turn. The "
            "system automatically truncates this down to fit num_ctx every "
            "send. Routinely 10x num_ctx on long-running kin and that's "
            "normal — you are not over your cap.")
        lines.append("")
        # Only nag about being near-cap when the authoritative figure
        # confirms it. The conversation-archive number is intentionally
        # NOT compared to cap here — that was the bug commit 291a6d1
        # fixed in context_status (and that this section had been
        # mirroring).
        if gauge is not None and effective_ceiling > 0:
            pct = gauge / effective_ceiling * 100
            if pct >= 100:
                lines.append(
                    "At or over the effective cap — sends trim the "
                    "oldest turns to fit, and the chat shows a small "
                    "system marker above the most recent user message "
                    "noting that older turns rolled out of the send. "
                    "This is the normal steady state for a long-running "
                    "conversation, not an error; the full archive stays "
                    "on disk and distillation has already been turning "
                    "older turns into staging notes. Tending — your "
                    "kin's review of those notes — is what brings "
                    "substance forward into curated memory.md and depth "
                    "logs so it rides every future send."
                )
            elif pct >= 85:
                lines.append(
                    "Close to the effective cap. Raise num_ctx in the "
                    "kin's Settings (if the model has room), or let "
                    "tending bring substance into the depth logs so it "
                    "carries forward without depending on chat-window "
                    "fit."
                )
            if max_ctx and num_ctx < max_ctx:
                headroom_pct = num_ctx / max_ctx * 100
                lines.append("")
                lines.append(
                    f"Note: cap is at {headroom_pct:.1f}% of the model's declared max "
                    f"({num_ctx:,} / {max_ctx:,}). Raise num_ctx in the kin's "
                    f"Settings if you want more room."
                )
        return "\n".join(lines)

    def _conversation_token_estimate(self, model):
        """Estimated token total for the full in-memory conversation,
        memoized on (kin, model, message count). This sits on the
        per-keystroke hot path (_update_token_display →
        _build_usage_summary → _compose_default_status), and re-walking
        a multi-MB archive per keystroke was audit M-F4. Message count
        is a sufficient invalidation key: the list only changes via
        append (send paths) or wholesale replacement (clear / regen /
        reload), all of which change its length."""
        key = (self.current_agent, model, len(self.conversation or []))
        cached = getattr(self, "_convo_est_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        est = sum(
            estimate_message_tokens(m, model)
            for m in (self.conversation or [])
        )
        self._convo_est_cache = (key, est)
        return est

    def _lookup_model_max_context(self, model):
        """Look up a model's declared context length. For OpenRouter models,
        reads from the on-disk cache written by model_browser refresh —
        memoized on the cache file's mtime, so the JSON is parsed once
        per file version instead of once per keystroke (audit M-F4).
        For Ollama (local) models, queries /api/show through model_utils
        (which caches per-session — see model_utils._context_length_cache).
        Returns an int (tokens) or None when unknown."""
        if not model:
            return None
        if not model.startswith("openrouter/"):
            # Ollama local model — query /api/show via model_utils.
            try:
                return _model_context_length(model)
            except Exception:
                return None
        cache_path = Path.home() / ".ai_programs" / "openrouter_models_cache.json"
        try:
            mtime = cache_path.stat().st_mtime
        except OSError:
            return None
        memo = getattr(self, "_or_ctx_memo", None)
        if memo is None or memo[0] != mtime:
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                return None
            # Cache file uses "models", OpenRouter live API uses "data".
            # Handle both shapes so this works against either; bare list
            # also accepted as a defensive fallback.
            if isinstance(data, dict):
                models = data.get("models") or data.get("data")
            else:
                models = data
            ctx_by_id = {}
            for m in models or []:
                if isinstance(m, dict) and isinstance(m.get("id"), str):
                    ctx = m.get("context_length")
                    if isinstance(ctx, int) and ctx > 0:
                        ctx_by_id[m["id"]] = ctx
            memo = (mtime, ctx_by_id)
            self._or_ctx_memo = memo
        return memo[1].get(model[len("openrouter/"):])
