"""More model options — the per-kin power knobs that don't belong on the
everyday Model && generation tab: reasoning detail, image history,
caching, OpenRouter provider routing, the streaming watchdog, and Ollama
keep-alive / preload.

Opened by a button (NVDA-discoverable, like the recall and sampling
dialogs). FLAT with bold section headers — deliberately no tabs: nesting
tabs inside a dialog opened from a tab adds navigation depth for a screen
reader, and at this control count headers group things just as well
without the "which tab did I leave it on" load. Groups that don't apply
to the kin's current model (provider routing on a local model, keep-alive
on an OpenRouter model, image history on a text-only model) are omitted
entirely rather than shown-but-inert.

Edits the same config keys through the parent EditKinDialog's
``_save_param``, so saves are byte-identical to the old inline controls.
"""

import wx
import wx.lib.scrolledpanel as scrolled

from ._shared import _IntField


class MoreModelOptionsDialog(wx.Dialog):
    """Advanced per-kin model knobs. `cfg` is read once at open;
    `save_param(key, value)` is the parent's bound method (load-modify-
    save per key on disk)."""

    def __init__(self, parent, cfg, save_param, kin_name=""):
        title = "More model options"
        if kin_name:
            title = f"{title} — {kin_name}"
        super().__init__(parent, title=title,
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.cfg = cfg
        self._save_param = save_param

        # Model-specific relevance: hide groups that can't apply.
        try:
            from model_utils import strip_model_annotation
            model = strip_model_annotation(str(cfg.get("model", "") or ""))
        except Exception:
            model = str(cfg.get("model", "") or "")
        is_openrouter = model.startswith("openrouter/")
        is_ollama = not is_openrouter
        try:
            from llm_backend import model_supports_images
            supports_images = bool(model_supports_images(model))
        except Exception:
            supports_images = True  # fail open — better a shown-but-inert knob than a hidden needed one

        panel = scrolled.ScrolledPanel(self)
        self._sizer = wx.BoxSizer(wx.VERTICAL)

        # ── Reasoning detail (always) ──────────────────────────────────
        self._header(panel, "Reasoning detail")
        self.show_thinking_check = wx.CheckBox(panel, label="S&how reasoning in chat")
        self.show_thinking_check.SetValue(bool(cfg.get("show_thinking", False)))
        self.show_thinking_check.Bind(
            wx.EVT_CHECKBOX, lambda e: self._save_param(
                "show_thinking", self.show_thinking_check.GetValue()))
        self._add(self.show_thinking_check)

        self.feed_thinking_check = wx.CheckBox(panel, label="F&eed reasoning into context")
        self.feed_thinking_check.SetValue(bool(cfg.get("feed_thinking", False)))
        self.feed_thinking_check.Bind(
            wx.EVT_CHECKBOX, lambda e: self._save_param(
                "feed_thinking", self.feed_thinking_check.GetValue()))
        self._add(self.feed_thinking_check)

        cap_row = wx.BoxSizer(wx.HORIZONTAL)
        cap_lbl = wx.StaticText(panel, label="&Reasoning cap (chars):")
        self.think_cap_field = _IntField(
            panel, value=cfg.get("think_max_chars", 1200),
            min_val=0, max_val=100000, size=(120, -1),
            name="Reasoning cap in characters",
            on_commit=lambda v: self._save_param("think_max_chars", v))
        cap_row.Add(cap_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        cap_row.Add(self.think_cap_field)
        self._add(cap_row)
        self._help(panel,
                   "When 'Feed reasoning into context' is on, each turn's "
                   "thinking is sent back so the kin sees its prior thoughts. "
                   "The cap keeps that from growing every turn and slowing "
                   "replies. Default 1200; 0 disables the cap (research use).")

        # ── Image input (vision models only) ───────────────────────────
        if supports_images:
            self._header(panel, "Image input")
            img_row = wx.BoxSizer(wx.HORIZONTAL)
            img_lbl = wx.StaticText(panel, label="Image history &kept:")
            self.imghist_field = _IntField(
                panel, value=cfg.get("image_history_keep", 2),
                min_val=0, max_val=50, size=(100, -1),
                name="Image history kept",
                on_commit=lambda v: self._save_param("image_history_keep", v))
            img_row.Add(img_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
            img_row.Add(self.imghist_field)
            self._add(img_row)
            self._help(panel,
                       "How many of the most recent image-bearing turns send "
                       "their image to the model. Older image turns send only "
                       "their caption text — images are expensive and bill "
                       "every turn they're in context. Default 2.")

        # ── Caching (always; inert on providers without caching) ───────
        self._header(panel, "Caching")
        self.cache_check = wx.CheckBox(panel, label="&Use prompt caching (when supported)")
        self.cache_check.SetValue(bool(cfg.get("cache", True)))
        self.cache_check.Bind(
            wx.EVT_CHECKBOX, lambda e: self._save_param(
                "cache", self.cache_check.GetValue()))
        self._add(self.cache_check)

        ttl_row = wx.BoxSizer(wx.HORIZONTAL)
        ttl_lbl = wx.StaticText(panel, label="Cache &TTL:")
        self.cache_ttl_choice = wx.Choice(panel, choices=[
            "Provider default",
            "5 minutes (explicit)",
            "1 hour (Anthropic + Google only)",
        ])
        self.cache_ttl_choice.SetSelection(
            {"auto": 0, "5m": 1, "1h": 2}.get(str(cfg.get("cache_ttl", "auto")), 0))
        self.cache_ttl_choice.Bind(wx.EVT_CHOICE, self._on_cache_ttl)
        ttl_row.Add(ttl_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        ttl_row.Add(self.cache_ttl_choice, proportion=1, flag=wx.EXPAND)
        self._add(ttl_row, expand=True)
        self._help(panel,
                   "Reuses this kin's identity (soul + memory) across turns "
                   "instead of re-sending it — 75-90% cheaper on the cached "
                   "portion on supported OpenRouter providers (Claude, OpenAI, "
                   "DeepSeek, Gemini, Qwen, Grok, Moonshot, Groq). First "
                   "message costs slightly more; every one after is cheaper. "
                   "No effect on Ollama. TTL: 'Provider default' sends none "
                   "(Anthropic then means 5 min); pick '1 hour' if this kin "
                   "sees more than one turn per hour. Default: provider default.")

        # ── Provider routing (OpenRouter only) ─────────────────────────
        if is_openrouter:
            self._header(panel, "OpenRouter provider routing")
            po_lbl = wx.StaticText(
                panel, label="&Provider order (comma-separated slugs):")
            self.provider_order_text = wx.TextCtrl(panel, value=", ".join(
                p for p in (cfg.get("openrouter_provider_order") or [])
                if isinstance(p, str) and p.strip()))
            self.provider_order_text.Bind(wx.EVT_KILL_FOCUS, self._on_provider_order)
            self._add(po_lbl)
            self._add(self.provider_order_text, expand=True)

            self.provider_fallbacks_check = wx.CheckBox(
                panel,
                label="&Allow fallbacks to other providers if pinned ones are unavailable")
            self.provider_fallbacks_check.SetValue(
                bool(cfg.get("openrouter_allow_fallbacks", True)))
            self.provider_fallbacks_check.Bind(
                wx.EVT_CHECKBOX, self._on_provider_fallbacks)
            self._add(self.provider_fallbacks_check)
            self._help(panel,
                       "Pins this kin's requests to specific inference "
                       "provider(s) — slugs from each model's 'Providers' tab "
                       "on openrouter.ai (e.g. 'DeepInfra, Together'). Matters "
                       "when providers enforce a model's content policy "
                       "differently, so default routing makes a conversation "
                       "refuse one minute and not the next. Empty = let "
                       "OpenRouter pick. Allow fallbacks (default on): fall "
                       "through to default routing if every pinned provider "
                       "is down.")

        # ── Ollama options (local models only) ─────────────────────────
        if is_ollama:
            self._header(panel, "Ollama options")
            ka_row = wx.BoxSizer(wx.HORIZONTAL)
            ka_lbl = wx.StaticText(panel, label="&Keep model loaded:")
            self.keep_alive_choice = wx.Choice(panel, choices=[
                "Ollama default (5 minutes)",
                "30 minutes",
                "1 hour",
                "Until I close Ollama (never unload)",
            ])
            self.keep_alive_choice.SetSelection(
                {"": 0, "30m": 1, "1h": 2, "-1": 3}.get(
                    str(cfg.get("keep_alive", "") or ""), 0))
            self.keep_alive_choice.Bind(wx.EVT_CHOICE, self._on_keep_alive)
            ka_row.Add(ka_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
            ka_row.Add(self.keep_alive_choice, proportion=1, flag=wx.EXPAND)
            self._add(ka_row, expand=True)

            self.preload_check = wx.CheckBox(
                panel, label="&Warm up this kin's model when I switch to it")
            self.preload_check.SetValue(bool(cfg.get("preload_on_switch", False)))
            self.preload_check.Bind(
                wx.EVT_CHECKBOX, lambda e: self._save_param(
                    "preload_on_switch", self.preload_check.GetValue()))
            self._add(self.preload_check)
            self._help(panel,
                       "Keep model loaded: Ollama unloads after 5 min idle, so "
                       "the next reply pays a 20-60s cold load. Raise it for a "
                       "kin you use regularly (costs RAM/VRAM held longer). "
                       "Warm up on switch: start loading the model in the "
                       "background when you switch to this kin, so it's ready "
                       "by your first message.")

        # ── Reliability (always) ───────────────────────────────────────
        self._header(panel, "Reliability")
        wd_row = wx.BoxSizer(wx.HORIZONTAL)
        wd_lbl = wx.StaticText(panel, label="Watchdog t&imeout (minutes, 0 = auto):")
        self.watchdog_field = _IntField(
            panel, value=cfg.get("watchdog_timeout_minutes", 0),
            min_val=0, max_val=120, size=(80, -1),
            # "0 = auto" belongs in the name: the StaticText carrying it is
            # never announced, and 0 is the DEFAULT here — so the one value
            # most kin are actually set to had its meaning reach sighted
            # users only.
            name="Watchdog timeout in minutes (0 = auto)",
            on_commit=lambda v: self._save_param("watchdog_timeout_minutes", v))
        wd_row.Add(wd_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        wd_row.Add(self.watchdog_field)
        self._add(wd_row)
        self._help(panel,
                   "If a reply makes no progress for this long, it's declared "
                   "hung and cut loose instead of leaving the kin stuck at "
                   "“typing” forever. Covers every path — streaming "
                   "chats, tool-using chats, and scheduled (cron) wake-ups. "
                   "0 (auto) picks it from the model: OpenRouter 5 min; Ollama "
                   "scales with context (8k=5, 32k=8, 131k=20, capped 30). "
                   "Override only if your hardware consistently needs longer.")

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        close_btn = wx.Button(panel, wx.ID_CLOSE, "&Close")
        close_btn.Bind(wx.EVT_BUTTON, self._on_close)
        btn_row.AddStretchSpacer()
        btn_row.Add(close_btn)
        self._sizer.Add(btn_row, flag=wx.EXPAND | wx.ALL, border=10)

        self.SetEscapeId(wx.ID_CLOSE)
        self.Bind(wx.EVT_CLOSE, self._on_close)

        panel.SetSizer(self._sizer)
        panel.SetupScrolling(scroll_x=False, scroll_y=True)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, proportion=1, flag=wx.EXPAND)
        self.SetSizer(outer)
        self.SetInitialSize((600, 640))
        self.Layout()

    # ── layout helpers ─────────────────────────────────────────────────

    def _header(self, panel, text):
        lbl = wx.StaticText(panel, label=text)
        f = lbl.GetFont(); f.SetWeight(wx.FONTWEIGHT_BOLD); lbl.SetFont(f)
        self._sizer.Add(lbl, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=12)
        self._sizer.Add(wx.StaticLine(panel, style=wx.LI_HORIZONTAL),
                        flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

    def _add(self, item, expand=False):
        flags = wx.LEFT | wx.RIGHT | wx.TOP
        if expand:
            flags |= wx.EXPAND
        self._sizer.Add(item, flag=flags, border=8)

    def _help(self, panel, text):
        lbl = wx.StaticText(panel, label=text)
        lbl.Wrap(560)
        lbl.SetForegroundColour(wx.Colour(110, 110, 110))
        self._sizer.Add(lbl, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=8)

    # ── handlers (replicate the old inline logic, save via save_param) ──

    def _on_cache_ttl(self, _event):
        idx = self.cache_ttl_choice.GetSelection()
        values = ["auto", "5m", "1h"]
        if 0 <= idx < len(values):
            self._save_param("cache_ttl", values[idx])

    def _on_keep_alive(self, _event):
        idx = self.keep_alive_choice.GetSelection()
        values = ["", "30m", "1h", "-1"]
        if 0 <= idx < len(values):
            self._save_param("keep_alive", values[idx])
            self._apply_keep_alive_live(values[idx])

    def _apply_keep_alive_live(self, value):
        """Push the new keep_alive to the kin's model on its Ollama host
        right now, if it's already loaded — so 'Keep model loaded' takes
        effect immediately instead of only on the next chat. Off the UI
        thread, best-effort; never raises into the dialog."""
        model = str(self.cfg.get("model", "") or "")
        if not model or model.startswith("openrouter/"):
            return
        host_name = self.cfg.get("ollama_host_name", "")
        import threading

        def _work():
            try:
                from kin_persistence import resolve_kin_ollama_host
                from llm_backend import set_ollama_keep_alive
                set_ollama_keep_alive(
                    model, value,
                    host=resolve_kin_ollama_host(host_name))
            except Exception:
                pass
        threading.Thread(target=_work, daemon=True).start()

    def _on_provider_order(self, event):
        event.Skip()
        raw = self.provider_order_text.GetValue() or ""
        cleaned = [t.strip() for t in raw.split(",") if t.strip()]
        self._save_param("openrouter_provider_order", cleaned)
        self.provider_order_text.ChangeValue(", ".join(cleaned))

    def _on_provider_fallbacks(self, _event):
        self._save_param("openrouter_allow_fallbacks",
                         bool(self.provider_fallbacks_check.GetValue()))

    def _on_close(self, _event):
        if self.IsModal():
            self.EndModal(wx.ID_CLOSE)
        else:
            self.Destroy()
