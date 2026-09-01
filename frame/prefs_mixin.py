"""PrefsMixin — extracted from hearthkin.pyw (2026-07 modularisation).

One cohesive slice of the Hearthkin frame's behavior. Mixed into the
Hearthkin(wx.Frame) class in hearthkin.pyw; every method here runs with
the frame as `self`. Moved verbatim — no logic changed.
"""
from frame_shared import (
    CONFIG_FILE, _IntField, atomic_write_json, json, llm_backend, nvda_speak, nvda_status,
    resolve_kin_ollama_host, sys, threading, urllib, windows_startup, wx,
)


class PrefsMixin:

    def _build_prefs_tab(self, parent):
        """App-wide preferences. Per-kin settings (model, params, soul, memory,
        Telegram) live in the Kin tab — they belong to a specific kin, not the app."""
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Read-only TextCtrl, not StaticText: the next control is a
        # checkbox, which uses its own visible label as its name and
        # ignores any preceding StaticText — so this pointer to where the
        # per-kin settings actually live was never announced at all.
        intro = wx.TextCtrl(
            parent,
            value=("These are app-wide settings. For per-kin settings — model, "
                   "temperature, soul, memory, Telegram — see the Kin tab."),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
        )
        intro.SetName("What these preferences cover")
        intro.SetMinSize((-1, 40))

        prefs_label = wx.StaticText(parent, label="App preferences:")

        self.log_check = wx.CheckBox(parent, label="&Log conversations to file")
        self.log_check.SetValue(self.config.get("logging_enabled", False))
        self.log_check.Bind(wx.EVT_CHECKBOX, self._on_log_toggle)

        nvda_label = wx.StaticText(parent, label="NVDA reads replies:")
        self.nvda_choice = wx.Choice(parent, choices=["Off", "Short ('Reply ready')", "Full reply", "Streaming (sentence by sentence)"])
        nvda_idx = {"off": 0, "short": 1, "full": 2, "stream": 3}.get(self.config.get("nvda_mode", "off"), 0)
        self.nvda_choice.SetSelection(nvda_idx)
        self.nvda_choice.Bind(wx.EVT_CHOICE, self._on_nvda_mode)

        # NVDA controller DLL load status. The DLL ships with Hearthkin
        # (vendor/nvda — LGPL 2.1) and is loaded at import time; this
        # surface lets users see whether speech will work and, if not,
        # which paths we tried. The Test button speaks a fixed phrase
        # so users can verify NVDA actually receives the call. Tab-
        # reachable read-only TextCtrl (not StaticText) so screen-reader
        # users hit it in the tab cycle.
        nvda_loaded, nvda_msg, nvda_path = nvda_status()
        nvda_status_label = wx.StaticText(parent, label="NVDA controller DLL:")
        self.nvda_status_field = wx.TextCtrl(
            parent,
            value=nvda_msg,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
        )
        self.nvda_status_field.SetMinSize((-1, 48))
        self.nvda_status_field.SetName("NVDA controller DLL load status")
        self.nvda_test_btn = wx.Button(parent, label="Test &NVDA speech")
        self.nvda_test_btn.Bind(wx.EVT_BUTTON, self._on_test_nvda_speech)
        if not nvda_loaded:
            self.nvda_test_btn.Disable()
            self.nvda_test_btn.SetLabel("Test NVDA speech (DLL not loaded)")

        # Per-cue switches and volumes live behind a button rather than in
        # line: four cues x (checkbox + volume + test) is a wall of controls
        # for something most people set once. House pattern - everyday control
        # on the tab, the detail in a focused dialog.
        self.sound_cues_btn = wx.Button(parent, label="Sound c&ues…")
        self.sound_cues_btn.Bind(wx.EVT_BUTTON, self._on_sound_cues)

        # Dictation — speaking into the message box instead of typing it.
        # App-level rather than per-kin: it is about your voice and your
        # microphone, neither of which changes with who you are talking
        # to. Behind a button for the same reason as the sound cues —
        # a backend, a model, a device and a server address is a wall of
        # controls for something set once.
        self.dictation_btn = wx.Button(parent, label="&Dictation…")
        self.dictation_btn.Bind(wx.EVT_BUTTON, self._on_dictation_settings)

        self.chime_check = wx.CheckBox(parent, label="Play &chime on reply")
        self.chime_check.SetValue(self.config.get("reply_chime", False))
        self.chime_check.Bind(wx.EVT_CHECKBOX, self._on_chime_toggle)

        # Chime volume — Beep has no volume control on Windows and is often
        # inaudibly quiet, so we generate a wav with adjustable amplitude.
        # Tolerant cast — a corrupt chime_volume value shouldn't kill
        # the whole Preferences build (audit L-B34).
        try:
            chime_vol_init = int(round(float(self.config.get("chime_volume", 0.8) or 0.0) * 100))
        except (TypeError, ValueError):
            chime_vol_init = 80
        chime_vol_init = max(0, min(100, chime_vol_init))
        self.chime_vol_lbl = wx.StaticText(parent, label="Chime &volume:")
        self.chime_vol_slider = wx.Slider(parent, value=chime_vol_init, minValue=0, maxValue=100)
        self.chime_vol_display = wx.StaticText(parent, label=f"{chime_vol_init}%")
        self.chime_vol_slider.Bind(wx.EVT_SLIDER, self._on_chime_volume)
        chime_vol_row = wx.BoxSizer(wx.HORIZONTAL)
        chime_vol_row.Add(self.chime_vol_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        chime_vol_row.Add(self.chime_vol_slider, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL)
        chime_vol_row.Add(self.chime_vol_display, flag=wx.ALIGN_CENTER_VERTICAL | wx.LEFT, border=6)

        self.chime_explainer = wx.TextCtrl(
            parent,
            value=(
                "How loud the reply chimes sound. Windows beeps are often very "
                "quiet by default — turn this up if you can't hear them. Set to "
                "0 to silence the chime even when the box above is checked. "
                "Moving the slider plays a test tone at the new volume so you "
                "can hear what you'll get."
            ),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
        )
        self.chime_explainer.SetMinSize((-1, 80))

        # Approval alert — a distinct sound when a kin is waiting on you to
        # approve a gated tool. Separate from the reply chime and on by
        # default: it's a safety signal, not decoration. Created here (after
        # the chime widgets, before Enter) so tab order matches (tab order ==
        # widget-creation order on wxMSW).
        self.approval_alert_check = wx.CheckBox(
            parent, label="Play a sound when a kin is &waiting for approval")
        self.approval_alert_check.SetValue(self.config.get("approval_alert", True))
        self.approval_alert_check.Bind(wx.EVT_CHECKBOX, self._on_approval_alert_toggle)

        self.approval_alert_explainer = wx.TextCtrl(
            parent,
            value=(
                "A two-tone cue (different from the reply chime) when a kin on "
                "Telegram or Discord asks to run a command or use the webcam and "
                "is blocked waiting for you to say yes or no. On by default, even "
                "if reply chimes are off — spoken alerts can be cut off by your "
                "own typing, and without a sound a request can sit unseen until "
                "it times out. Loudness follows the chime volume above. Toggling "
                "this plays the alert so you know what to listen for."
            ),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
        )
        self.approval_alert_explainer.SetMinSize((-1, 80))

        # Problem alert — the other thing that happens while nobody is
        # looking. Same shape and same reasoning as the approval alert
        # above; created directly after it so tab order groups the two
        # alert switches together (tab order == creation order on wxMSW).
        self.problem_alert_check = wx.CheckBox(
            parent,
            label="Play a sound when &background work stops unexpectedly")
        self.problem_alert_check.SetValue(self.config.get("problem_alert", True))
        self.problem_alert_check.Bind(wx.EVT_CHECKBOX,
                                      self._on_problem_alert_toggle)

        self.problem_alert_explainer = wx.TextCtrl(
            parent,
            value=(
                "A falling two-tone cue when work you left running stops on "
                "its own: a kin's memory distillation failed, or a "
                "'redistill from start' hit an error part-way through. "
                "Falling, where the approval alert rises, so you can tell "
                "which one you heard without learning a code. On by default, "
                "even if reply chimes are off — this work runs unattended by "
                "design, so a failure nobody is told about is one nobody "
                "finds until they go looking hours later. Loudness follows "
                "the chime volume above. Toggling this plays the alert so "
                "you know what to listen for."
            ),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
        )
        self.problem_alert_explainer.SetMinSize((-1, 90))

        # Custom sounds — an operator can drop their own WAVs in the folder to
        # replace any built-in tone (the defaults are loud). Button opens it.
        self.open_sounds_btn = wx.Button(
            parent, label="Open my &sounds folder…")
        self.open_sounds_btn.Bind(wx.EVT_BUTTON, self._on_open_sounds_folder)
        self.sounds_explainer = wx.TextCtrl(
            parent,
            value=(
                "Drop your own WAV files here to replace the built-in sounds — "
                "name them send.wav, first.wav, done.wav (reply stages) or "
                "approval.wav (the waiting-for-approval alert). A file always "
                "wins over the built-in tone; delete it to go back. Plain 16-bit "
                "WAVs follow the volume slider; other formats play at their own "
                "level. This is how you'd make and ship a quieter set."
            ),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
        )
        self.sounds_explainer.SetMinSize((-1, 80))

        self.enter_check = wx.CheckBox(parent, label="Plain &Enter sends (else: Ctrl+Enter)")
        self.enter_check.SetValue(self.config.get("enter_sends", False))
        self.enter_check.Bind(wx.EVT_CHECKBOX, self._on_enter_toggle)

        self.warn_model_swap_check = wx.CheckBox(
            parent,
            label=(
                "&Warn when changing a kin's model (voice continuity confirm)"
            ),
        )
        self.warn_model_swap_check.SetValue(self.config.get("warn_on_model_swap", True))
        self.warn_model_swap_check.Bind(wx.EVT_CHECKBOX, self._on_warn_model_swap_toggle)

        self.telegram_cmd_menu_check = wx.CheckBox(
            parent,
            label=(
                "Register Telegram slash-command &menu "
                "(uncheck if typing a command sends /help — Unigram/screen readers)"
            ),
        )
        self.telegram_cmd_menu_check.SetValue(
            bool(self.config.get("telegram_command_menu", True))
        )
        self.telegram_cmd_menu_check.Bind(
            wx.EVT_CHECKBOX, self._on_telegram_cmd_menu_toggle,
        )

        # System-tray + autostart toggles. Both are Windows-only
        # behaviors; the start-with-Windows toggle disables itself
        # gracefully on non-Windows. Close-to-tray works on any
        # platform but is most useful where there's actually a tray.
        self.tray_close_check = wx.CheckBox(
            parent,
            label=(
                "Minimize to system &tray on close (Alt+F4)"
            ),
        )
        self.tray_close_check.SetValue(
            bool(self.config.get("close_to_tray", True))
        )
        self.tray_close_check.Bind(
            wx.EVT_CHECKBOX, self._on_close_to_tray_toggle,
        )

        self.auto_update_check = wx.CheckBox(
            parent,
            label="Check for &updates on startup",
        )
        self.auto_update_check.SetValue(
            bool(self.config.get("auto_check_updates_on_startup", False))
        )
        self.auto_update_check.Bind(
            wx.EVT_CHECKBOX, self._on_auto_update_toggle,
        )

        self.start_with_windows_check = wx.CheckBox(
            parent,
            label="Start &Hearthkin when Windows starts",
        )
        # Source-of-truth is the registry — re-read it here so a
        # registry edit made elsewhere (e.g. with msconfig) shows up.
        registry_enabled = False
        try:
            registry_enabled = windows_startup.is_enabled()
        except Exception:
            pass
        self.start_with_windows_check.SetValue(registry_enabled)
        if not windows_startup.is_supported():
            self.start_with_windows_check.Disable()
            self.start_with_windows_check.SetLabel(
                self.start_with_windows_check.GetLabel()
                + "  (Windows-only)"
            )
        self.start_with_windows_check.Bind(
            wx.EVT_CHECKBOX, self._on_start_with_windows_toggle,
        )

        self.foreground_lock_check = wx.CheckBox(
            parent,
            label="Keep Hearthkin's window reliably &focusable (recommended)",
        )
        self.foreground_lock_check.SetValue(
            bool(self.config.get("manage_foreground_lock", True))
        )
        if sys.platform != "win32":
            self.foreground_lock_check.Disable()
            self.foreground_lock_check.SetLabel(
                self.foreground_lock_check.GetLabel() + "  (Windows-only)"
            )
        self.foreground_lock_check.Bind(
            wx.EVT_CHECKBOX, self._on_foreground_lock_toggle,
        )

        # Operator's display name — inlined as "[name] " on user turns
        # in room mode so the kin in a room can tell who the human is.
        # Plain TextCtrl + buddy StaticText (buddy label is the
        # accessible name on wxMSW — see the dialogs convention).
        # Empty by default; empty value = no attribution prefix added.
        user_name_row = wx.BoxSizer(wx.HORIZONTAL)
        user_name_lbl = wx.StaticText(
            parent, label="&Your name (shown to kin in rooms):",
        )
        # wx.TE_PROCESS_ENTER is REQUIRED for the EVT_TEXT_ENTER bind
        # below — without it, wxWidgets asserts and the entire
        # Preferences dialog fails to build. The default TextCtrl
        # sends Enter to default-button processing, not to the text
        # control's own event handlers; the style flag opts in.
        self.user_name_field = wx.TextCtrl(
            parent,
            value=str(self.config.get("user_name", "") or ""),
            style=wx.TE_PROCESS_ENTER,
        )
        self.user_name_field.Bind(
            wx.EVT_KILL_FOCUS, self._on_user_name_changed,
        )
        # Also save on Enter — for users who type-then-Tab-or-Enter,
        # the kill-focus binding already handles Tab; Enter without
        # Tab needs an explicit save so the field commits without
        # the user having to leave it.
        self.user_name_field.Bind(
            wx.EVT_TEXT_ENTER, self._on_user_name_changed,
        )
        user_name_row.Add(user_name_lbl,
                          flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        user_name_row.Add(self.user_name_field, proportion=1,
                          flag=wx.ALIGN_CENTER_VERTICAL)
        # Read-only TextCtrl, not StaticText: nothing here adopts it as a
        # buddy label, so what the kin will actually call you — and that
        # blank means no name tag — reached sighted users only.
        user_name_explainer = wx.TextCtrl(
            parent,
            value=(
                "Inlined onto every message you send in a room so the "
                "kin can tell who the human is, same way they see each "
                "other tagged with [KinName]. This is what the kin will "
                "call you. Leave blank to send room messages without a "
                "name tag. Only used in rooms (1-on-1 chat doesn't need "
                "it; the kin already knows who they're talking to)."
            ),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
        )
        user_name_explainer.SetName("About your name in rooms")
        user_name_explainer.SetMinSize((-1, 84))

        # Chat history render window. Caps how many recent messages
        # paint into chat_display on kin-load — bigger histories paint
        # in chunks via the "Load older messages" button above the
        # conversation. The full conversation stays in memory either
        # way; this is a paint cap, not a retention cap.
        history_row = wx.BoxSizer(wx.HORIZONTAL)
        history_lbl = wx.StaticText(
            parent,
            label="Recent c&hat messages shown on kin load (0 = all):",
        )
        self.history_window_field = _IntField(
            parent,
            value=int(self.config.get("chat_history_window", 200) or 0),
            min_val=0, max_val=100000,
            size=(100, -1),
            # "(0 = all)" is in the name because the field's own name wins
            # over the StaticText beside it — without it, the one value
            # with special meaning reaches sighted users only.
            name="Recent chat messages shown on kin load (0 = all)",
            on_commit=self._on_chat_history_window_changed,
        )
        history_row.Add(history_lbl,
                        flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        history_row.Add(self.history_window_field,
                        flag=wx.ALIGN_CENTER_VERTICAL)
        # Read-only TextCtrl, not StaticText: the next control has its own
        # SetName, so this never got announced — including what a safe
        # value is and what the "Load older messages" button steps by.
        history_explainer = wx.TextCtrl(
            parent,
            value=(
                "Big chat histories paint in chunks. 200 is a safe "
                "default; 0 paints everything at once like before "
                "(slower for kin with thousands of turns). "
                "Each click of \"Load older messages\" reveals "
                "this many more older messages."
            ),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
        )
        history_explainer.SetName("About the chat history window")
        history_explainer.SetMinSize((-1, 68))

        # --- Connections (API keys) section ---
        # Read/edit API keys without editing JSON files by hand. Each entry
        # writes to ~/.ai_programs/<provider>_key.json on save; env vars
        # like OPENROUTER_API_KEY or BRAVE_API_KEY still override the file
        # if set. The current value is shown masked so over-the-shoulder
        # and screen-reading-out-loud don't leak the whole key — Edit pops
        # a focused dialog that shows the full key for verification.
        #
        # Section header is a focusable read-only TextCtrl rather than
        # a wx.StaticText. StaticText is not in the Tab cycle on
        # wxMSW, so a screen-reader user tabbing through Preferences
        # would land on the OpenRouter Edit button with no context
        # for what section they were in. The TextCtrl wraps the
        # heading text in something NVDA reads on focus, so the
        # cluster has a clear "Connections" anchor.
        conn_sep = wx.StaticLine(parent, style=wx.LI_HORIZONTAL)
        conn_header = wx.TextCtrl(
            parent,
            value="Connections — API keys:",
            style=wx.TE_READONLY | wx.TE_NO_VSCROLL,
        )
        conn_header.SetName("Connections section heading")
        # Read-only TextCtrl, not StaticText, for the same reason as the
        # heading above it: every control below sets its own name, so this
        # is never adopted as a buddy label. Where the keys are written,
        # that env vars silently win over them, and that Test spends a
        # real call are all things you want before touching this section —
        # and as StaticText none of it was announced.
        conn_intro = wx.TextCtrl(
            parent,
            value=(
                "Keys for paid model providers and search services. Saved "
                "to ~/.ai_programs/<provider>_key.json. Environment vars "
                "(OPENROUTER_API_KEY, BRAVE_API_KEY, etc.) override these "
                "values when set. Test buttons make a small live call to "
                "verify the key works."
            ),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
        )
        conn_intro.SetName("About the Connections section")
        conn_intro.SetMinSize((-1, 68))

        # Container to track each provider's display textctrl so we can
        # refresh it after Edit/Test. Keyed on the provider name.
        self._provider_key_displays = {}

        or_row = self._build_provider_key_row(
            parent, "openrouter", "OpenRouter API key:", "sk-or-…",
            with_balance_button=True,
        )
        brave_row = self._build_provider_key_row(
            parent, "brave", "Brave Search API key:", "BSA…"
        )
        elevenlabs_row = self._build_provider_key_row(
            parent, "elevenlabs", "ElevenLabs API key:", "sk_…"
        )

        # (The former global "Ollama host" row lived here. Ollama machine
        # selection is now per-kin — chosen in the model browser, stored
        # as the kin's ollama_host_name. See model_browser's Machine
        # picker and migrate_global_ollama_host.)
        semantic_row = self._build_semantic_memory_row(parent)

        sizer.Add(intro, flag=wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, border=8)
        sizer.AddSpacer(8)
        sizer.Add(prefs_label, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(self.log_check, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(nvda_label, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(self.nvda_choice, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(nvda_status_label, flag=wx.LEFT | wx.RIGHT, border=6)
        sizer.Add(self.nvda_status_field,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(self.nvda_test_btn, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(self.chime_check, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(self.sound_cues_btn, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(self.dictation_btn, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(chime_vol_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(self.chime_explainer, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(self.approval_alert_check, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(self.approval_alert_explainer, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(self.problem_alert_check, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(self.problem_alert_explainer, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(self.open_sounds_btn, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(self.sounds_explainer, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(self.enter_check, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(self.warn_model_swap_check, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(self.telegram_cmd_menu_check, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(self.tray_close_check,
                  flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(self.start_with_windows_check,
                  flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(self.foreground_lock_check,
                  flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(self.auto_update_check,
                  flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(user_name_row,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=6)
        sizer.Add(user_name_explainer,
                  flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(history_row, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=6)
        sizer.Add(history_explainer, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)

        sizer.Add(conn_sep, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, border=6)
        sizer.Add(conn_header, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(conn_intro, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(or_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(brave_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(elevenlabs_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(semantic_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)

        parent.SetSizer(sizer)

    # --- Provider-key (API keys in Preferences → Connections) helpers --- #

    def _build_provider_key_row(self, parent, provider_name, label_text, placeholder, with_balance_button=False):
        """Build one Connections row inside its own StaticBox so the
        cluster announces as a group to NVDA. Each box contains:
        masked-key display + Edit + Test. The provider name lives on
        the box label so a screen-reader user hears "OpenRouter API
        key, group" when focus enters, then the controls inside,
        rather than tabbing through 6 unrelated buttons in a row.

        Returns the box's outer sizer so the caller can drop it into
        the parent panel's vertical layout."""
        # Button labels include the provider name so NVDA reads "Edit
        # OpenRouter API key, button" on tab-focus. The earlier shape
        # used generic "&Edit…" / "&Test" labels with a SetName override —
        # but on wxMSW the button's accessible name comes from the visible
        # label, not from SetName, so a screen-reader user heard "Edit,
        # button" with no context. The label IS the accessibility name
        # for a button; treat it that way.
        clean_label = label_text.rstrip(":").strip()
        box = wx.StaticBox(parent, label=clean_label)
        outer = wx.StaticBoxSizer(box, wx.VERTICAL)
        row = wx.BoxSizer(wx.HORIZONTAL)
        # Inner widgets get parented to the StaticBox, not the panel,
        # so NVDA correctly nests them under the box label and Tab
        # navigation announces the group.
        display = wx.TextCtrl(box, style=wx.TE_READONLY)
        display.SetName(label_text.rstrip(":"))
        display.SetValue(self._mask_provider_key(
            llm_backend.resolve_provider_key(provider_name)
        ))
        self._provider_key_displays[provider_name] = (display, placeholder)
        edit_btn = wx.Button(box, label=f"Edit {clean_label}…")
        edit_btn.Bind(
            wx.EVT_BUTTON,
            lambda e, pn=provider_name: self._on_edit_provider_key(pn),
        )
        test_btn = wx.Button(box, label=f"Test {clean_label}")
        test_btn.Bind(
            wx.EVT_BUTTON,
            lambda e, pn=provider_name: self._on_test_provider_key(pn),
        )
        row.Add(display, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        row.Add(edit_btn, flag=wx.RIGHT, border=6)
        row.Add(test_btn)
        # Optional "Show balance" button — OpenRouter has a per-account
        # credits/usage endpoint that's actually useful (current remaining
        # balance, total spent). Their web dashboard buries the balance
        # behind chart-heavy UI that screen-readers can't navigate, so
        # surfacing it here as a one-line plain-text answer (status field
        # + NVDA speak) makes the "how much have I burned" question
        # accessible. Brave / ElevenLabs don't expose a comparable
        # account-balance API so this button is per-provider opt-in.
        if with_balance_button:
            # 6px gap before the balance button. Use AddSpacer(int) —
            # row.Add(wx.Size(6, 0)) raises TypeError on current
            # wxPython (the Sizer.Add overload set doesn't accept
            # bare wx.Size; it wants a (w, h) tuple or a real spacer).
            # That bug silently broke the entire Preferences dialog
            # via the unhandled exception in open_preferences_dialog.
            row.AddSpacer(6)
            bal_btn = wx.Button(box, label=f"Show {clean_label} balance")
            bal_btn.Bind(
                wx.EVT_BUTTON,
                lambda e, pn=provider_name: self._on_show_provider_balance(pn),
            )
            row.Add(bal_btn, flag=wx.LEFT, border=6)
        outer.Add(row, flag=wx.EXPAND | wx.ALL, border=4)
        return outer

    def _build_semantic_memory_row(self, parent):
        """Semantic memory search controls in their own StaticBox. A
        checkbox to enable embedding reranking of memory_search, the
        embed-model name, and a Download button that pulls the model so
        the user never needs a terminal. App-level — applies to every
        kin's memory search. Embeddings run on this machine's Ollama
        (localhost); the per-kin machine setting is for chat models."""
        box = wx.StaticBox(parent, label="Semantic memory search")
        outer = wx.StaticBoxSizer(box, wx.VERTICAL)

        self.semantic_check = wx.CheckBox(
            box, label="&Use semantic memory search (Ollama embeddings)")
        self.semantic_check.SetValue(bool(self.config.get("semantic_memory")))
        self.semantic_check.Bind(wx.EVT_CHECKBOX, self._on_semantic_toggle)

        row = wx.BoxSizer(wx.HORIZONTAL)
        model_lbl = wx.StaticText(box, label="&Embedding model:")
        self.embed_model_field = wx.TextCtrl(
            box,
            value=str(self.config.get("embed_model", "") or "nomic-embed-text"),
            style=wx.TE_PROCESS_ENTER,
        )
        self.embed_model_field.Bind(
            wx.EVT_KILL_FOCUS, self._on_embed_model_changed)
        self.embed_model_field.Bind(
            wx.EVT_TEXT_ENTER, self._on_embed_model_changed)
        download_btn = wx.Button(box, label="Download embedding model")
        download_btn.Bind(wx.EVT_BUTTON, self._on_download_embed_model)

        row.Add(model_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        row.Add(self.embed_model_field, proportion=1,
                flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        row.Add(download_btn, flag=wx.ALIGN_CENTER_VERTICAL)

        # Which machine runs embeddings (app-level — memory search is
        # app-wide). Parallels the per-kin chat machine; "This machine" +
        # any saved remotes. Stored as embed_host.
        host_row = wx.BoxSizer(wx.HORIZONTAL)
        embed_host_lbl = wx.StaticText(box, label="Embedding mac&hine:")
        self.embed_host_choice = wx.Choice(box, choices=[])
        self.embed_host_choice.Bind(wx.EVT_CHOICE, self._on_embed_host_changed)
        self._embed_host_values = []
        host_row.Add(embed_host_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        host_row.Add(self.embed_host_choice, proportion=1,
                     flag=wx.ALIGN_CENTER_VERTICAL)
        self._rebuild_embed_host_choice()

        # Embedding-call timeout (SECONDS). Bounds every embed so a slow /
        # busy / unreachable embed host degrades to keyword search instead
        # of hanging — an un-timeout'd embed once froze the whole app on
        # send. 0 = use the built-in fallback.
        timeout_row = wx.BoxSizer(wx.HORIZONTAL)
        embed_timeout_lbl = wx.StaticText(
            box, label="Embedding &timeout (seconds, 0 = default):")
        self.embed_timeout_field = _IntField(
            box,
            value=int(self.config.get("embed_timeout_secs", 12) or 0),
            min_val=0, max_val=600,
            size=(80, -1),
            # "(0 = default)" is in the name because the field's own name
            # wins over the StaticText beside it — without it, the one
            # value with special meaning reaches sighted users only.
            name="Embedding call timeout in seconds (0 = default)",
            on_commit=self._on_embed_timeout_changed,
        )
        timeout_row.Add(embed_timeout_lbl,
                        flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        timeout_row.Add(self.embed_timeout_field,
                        flag=wx.ALIGN_CENTER_VERTICAL)

        # How long Ollama keeps the embedding model resident after a call, so
        # per-turn semantic recall doesn't cold-reload it every time. The embed
        # model is tiny (~0.4 GB) and co-resides with the chat model fine, so
        # pinning it (-1) costs almost nothing and removes a cold-load from
        # every recall turn. 0 = leave it to the Ollama server's own default.
        keepalive_row = wx.BoxSizer(wx.HORIZONTAL)
        embed_ka_lbl = wx.StaticText(
            box, label="Keep embedding model &loaded (minutes):")
        self.embed_keep_alive_field = _IntField(
            box,
            value=int(self.config.get("embed_keep_alive_min", 30) or 0),
            min_val=-1, max_val=1440,
            size=(80, -1),
            # Special values baked into the name — the field's name wins over
            # the StaticText beside it, so -1/0 reach non-sighted users too.
            name="Keep embedding model loaded, minutes "
                 "(-1 = always loaded, 0 = server default)",
            on_commit=self._on_embed_keep_alive_changed,
        )
        keepalive_row.Add(embed_ka_lbl,
                          flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        keepalive_row.Add(self.embed_keep_alive_field,
                          flag=wx.ALIGN_CENTER_VERTICAL)

        # Read-only TextCtrl, not StaticText: nothing after it adopts it as
        # a buddy label, so the whole explanation of what this checkbox
        # does — that it runs a model on a machine you pick, that it needs
        # a one-time download, and that it fails soft back to keyword
        # search — reached sighted users only.
        explainer = wx.TextCtrl(
            box,
            value=(
                "When on, a kin's memory search ranks results by MEANING, "
                "not just matching keywords — so 'the thing where the kin "
                "sounds wrong' can still find a note titled 'voice "
                "compatibility.' It runs a small embedding model on the "
                "embedding machine chosen below; click Download embedding "
                "model once to fetch it there (or run 'ollama pull "
                "nomic-embed-text' on that box). Off = the original keyword "
                "search, with no embedding calls. If the model or that "
                "machine isn't available, search quietly falls back to keywords."
            ),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
        )
        explainer.SetName("About semantic memory search")
        # Sized to the text: TE_NO_VSCROLL clips rather than scrolls, so a
        # min height short of the wrapped line count hides the tail from
        # sighted users. This is the longest explainer in Preferences.
        explainer.SetMinSize((-1, 140))

        outer.Add(self.semantic_check, flag=wx.ALL, border=6)
        outer.Add(row, flag=wx.EXPAND | wx.ALL, border=4)
        outer.Add(host_row, flag=wx.EXPAND | wx.ALL, border=4)
        outer.Add(timeout_row, flag=wx.EXPAND | wx.ALL, border=4)
        outer.Add(keepalive_row, flag=wx.EXPAND | wx.ALL, border=4)
        outer.Add(explainer,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        return outer

    def _on_semantic_toggle(self, _event):
        """Save the semantic-memory on/off flag to app config. Takes effect
        immediately — memory_search reads the flag per call."""
        self.config["semantic_memory"] = bool(self.semantic_check.GetValue())
        try:
            atomic_write_json(CONFIG_FILE, self.config)
        except Exception as e:
            self._set_status(f"Save failed: {e}")
            return
        state = "on" if self.config["semantic_memory"] else "off"
        self._set_status(f"Semantic memory search {state}.")

    def _on_embed_model_changed(self, event):
        """Save the embed-model name on Tab-away or Enter."""
        try:
            event.Skip()
        except Exception:
            pass
        new_model = (self.embed_model_field.GetValue() or "").strip()
        old_model = str(self.config.get("embed_model", "") or "")
        if new_model == old_model:
            return
        self.config["embed_model"] = new_model
        try:
            atomic_write_json(CONFIG_FILE, self.config)
        except Exception as e:
            self._set_status(f"Save failed: {e}")
            return
        self._set_status("Embedding model saved.")

    def _on_embed_timeout_changed(self, value):
        """Save the embedding-call timeout (seconds) to app config. 0 uses
        the built-in fallback (llm_backend._EMBED_TIMEOUT_SECS). Read per
        call by the recall / memory-search embed path, so it takes effect
        on the next send."""
        self.config["embed_timeout_secs"] = int(value)
        try:
            atomic_write_json(CONFIG_FILE, self.config)
        except Exception as e:
            self._set_status(f"Save failed: {e}")
            return
        secs = int(value)
        self._set_status(
            f"Embedding timeout set to {secs}s."
            if secs > 0 else "Embedding timeout set to the built-in default.")

    def _on_embed_keep_alive_changed(self, value):
        """Save how long (minutes) the embedding model stays resident to app
        config. -1 = pinned forever, 0 = the Ollama server default, N = N
        minutes. Read per embed call, so it takes effect on the next recall."""
        self.config["embed_keep_alive_min"] = int(value)
        try:
            atomic_write_json(CONFIG_FILE, self.config)
        except Exception as e:
            self._set_status(f"Save failed: {e}")
            return
        mins = int(value)
        if mins < 0:
            msg = "Embedding model will stay loaded (pinned)."
        elif mins == 0:
            msg = "Embedding model keep-alive left to the Ollama default."
        else:
            msg = f"Embedding model stays loaded for {mins} min after use."
        self._set_status(msg)

    def _on_download_embed_model(self, _event):
        """Pull the embed model onto the configured Ollama host in a
        background thread, reporting progress to the status field + NVDA so
        the user doesn't need a terminal on the box running Ollama."""
        model = (self.embed_model_field.GetValue() or "").strip() \
            or "nomic-embed-text"
        self._set_status(
            f"Downloading {model}… (first pull can take a few minutes)")
        nvda_speak(f"Downloading {model}. This can take a few minutes.")

        embed_host = resolve_kin_ollama_host(
            self.config.get("embed_host", "")) or None

        def worker():
            def progress(line):
                wx.CallAfter(self._set_status, f"{model}: {line}")
            ok, err = llm_backend.pull_ollama_model(
                model, progress_cb=progress, host=embed_host)
            if ok:
                msg = f"{model} downloaded — semantic memory search is ready."
            else:
                msg = f"Download failed: {err}"
            wx.CallAfter(self._set_status, msg)
            wx.CallAfter(nvda_speak, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _rebuild_embed_host_choice(self):
        """Populate the embedding-machine dropdown from the registry and
        select the saved embed_host. 'This machine' + each saved remote;
        an unsaved current value is shown so it isn't lost."""
        from kin_persistence import load_ollama_hosts, THIS_MACHINE_NAME
        cur = str(self.config.get("embed_host", "") or "")
        labels = [f"{THIS_MACHINE_NAME} (localhost)"]
        values = [THIS_MACHINE_NAME]
        for name, url in load_ollama_hosts():
            labels.append(f"{name}  ({url})")
            values.append(url)
        if cur and cur != THIS_MACHINE_NAME and cur not in values:
            labels.append(f"{cur}  (not saved)")
            values.append(cur)
        self._embed_host_values = values
        self.embed_host_choice.Set(labels)
        target = cur or THIS_MACHINE_NAME
        self.embed_host_choice.SetSelection(
            values.index(target) if target in values else 0)

    def _on_embed_host_changed(self, _event):
        idx = self.embed_host_choice.GetSelection()
        if idx < 0 or idx >= len(self._embed_host_values):
            return
        self.config["embed_host"] = self._embed_host_values[idx]
        try:
            atomic_write_json(CONFIG_FILE, self.config)
        except Exception as e:
            self._set_status(f"Save failed: {e}")
            return
        self._set_status("Embedding machine saved.")

    def _mask_provider_key(self, key):
        """Render a key for the read-only display. Shows enough of the
        prefix/suffix to let the user recognize it, but hides the middle
        so screen-readers and over-the-shoulder views don't read the
        whole secret aloud. Empty key returns the literal '(not set)'
        sentinel so 'is this configured?' is unambiguous."""
        if not key:
            return "(not set)"
        key = key.strip()
        if len(key) <= 10:
            return "•" * len(key)
        return f"{key[:6]}…{key[-4:]}  ({len(key)} chars)"

    def _refresh_provider_key_display(self, provider_name):
        """Re-read the key from disk and update the row's display textctrl.
        Called after Edit (key may have changed) and Test (in case the
        user fixed something out-of-band)."""
        entry = self._provider_key_displays.get(provider_name)
        if not entry:
            return
        display, _placeholder = entry
        try:
            key = llm_backend.resolve_provider_key(provider_name)
        except Exception:
            key = ""
        display.SetValue(self._mask_provider_key(key))

    def _on_edit_provider_key(self, provider_name):
        """Pop a focused MASKED entry dialog to accept a new key, write it
        to disk, and refresh the display. Empty value clears the key (env
        var still wins).

        The field is masked (wx.PasswordEntryDialog), not plaintext:
        the primary user is blind and NVDA-primary, and a plaintext
        TextEntryDialog pre-filled with the live key had NVDA read the
        whole billable secret aloud character-by-character and rendered it
        on screen for anyone nearby (2026-07 security audit G1). A masked
        field is announced as protected — NVDA does not speak its contents.
        The current key is pre-filled (so OK-without-editing keeps it), and
        the masked prefix6…suffix4 display row remains the verification
        surface."""
        current = llm_backend.resolve_provider_key(provider_name)
        _entry = self._provider_key_displays.get(provider_name)
        placeholder = _entry[1] if _entry else ""
        prompt = (
            f"Enter the API key for {provider_name} (the field is hidden "
            f"for your privacy).\n\n"
            f"Example shape: {placeholder}\n\n"
            f"Leave blank to clear the on-disk value (any env var "
            f"will still apply)."
        )
        dlg = wx.PasswordEntryDialog(
            self,
            prompt,
            caption=f"Edit {provider_name} API key",
            value=current,
            style=wx.OK | wx.CANCEL,
        )
        try:
            if dlg.ShowModal() == wx.ID_OK:
                new_key = dlg.GetValue().strip()
                try:
                    llm_backend.write_provider_key(provider_name, new_key)
                except Exception as e:
                    self._set_status(
                        f"Could not save {provider_name} key: {e}"
                    )
                    return
                self._refresh_provider_key_display(provider_name)
                status = "saved" if new_key else "cleared"
                self._set_status(f"{provider_name} API key {status}.")
        finally:
            dlg.Destroy()

    def _on_test_provider_key(self, provider_name):
        """Hit the provider's cheapest authenticated endpoint with the
        configured key. Reports success or the error body via status bar.
        Runs the network call in a worker thread so the UI doesn't freeze
        on slow networks."""
        key = llm_backend.resolve_provider_key(provider_name)
        if not key:
            self._set_status(
                f"{provider_name}: no key configured. Click Edit to add one."
            )
            return
        self._set_status(f"{provider_name}: testing key…")

        def worker():
            ok, message = self._provider_key_test_call(provider_name, key)
            wx.CallAfter(self._on_provider_test_done, provider_name, ok, message)

        threading.Thread(target=worker, daemon=True).start()

    def _provider_key_test_call(self, provider_name, key):
        """Run the per-provider verification request. Returns (ok, message)
        where message is short enough to fit in the status bar."""
        # Note: urlopen raises HTTPError for any non-2xx status, so the
        # bodies below never see a failure status — the HTTPError except
        # at the bottom is the live non-200 path.
        try:
            if provider_name == "openrouter":
                req = urllib.request.Request(
                    "https://openrouter.ai/api/v1/auth/key",
                    headers={"Authorization": f"Bearer {key}"},
                )
                with urllib.request.urlopen(req, timeout=15):
                    return True, "OpenRouter key OK."
            elif provider_name == "brave":
                url = (
                    "https://api.search.brave.com/res/v1/web/search"
                    "?q=test&count=1"
                )
                req = urllib.request.Request(
                    url,
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": key,
                    },
                )
                with urllib.request.urlopen(req, timeout=15):
                    return True, "Brave Search key OK."
            elif provider_name == "elevenlabs":
                # ElevenLabs uses xi-api-key, not Bearer. /v1/user is
                # the cheapest auth-check endpoint and surfaces useful
                # subscription info we can show in the status line.
                req = urllib.request.Request(
                    "https://api.elevenlabs.io/v1/user",
                    headers={"xi-api-key": key},
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    try:
                        data = json.loads(
                            resp.read().decode("utf-8", "replace")
                        )
                        sub = data.get("subscription") or {}
                        tier = sub.get("tier") or "?"
                        used = sub.get("character_count")
                        limit = sub.get("character_limit")
                        if used is not None and limit:
                            return True, (
                                f"ElevenLabs key OK — tier {tier}, "
                                f"{used:,}/{limit:,} chars used"
                            )
                        return True, f"ElevenLabs key OK — tier {tier}"
                    except Exception:
                        return True, "ElevenLabs key OK."
            else:
                return False, f"No test implemented for {provider_name!r}."
        except urllib.error.HTTPError as e:
            return False, f"{provider_name}: HTTP {e.code} {e.reason}"
        except urllib.error.URLError as e:
            return False, f"{provider_name}: network error: {e.reason}"
        except Exception as e:
            return False, f"{provider_name}: {type(e).__name__}: {e}"

    def _on_show_provider_balance(self, provider_name):
        """Fetch the provider's account balance / usage info and surface
        it as plain text in the status field + spoken via NVDA. The web
        dashboards (OpenRouter especially) bury this info behind
        chart-heavy UI that doesn't read well in a screen-reader; this
        button puts it one keypress away in the already-accessible
        Connections panel.

        Runs the network call in a worker thread so the UI doesn't
        block. Only OpenRouter is implemented — Brave and ElevenLabs
        don't expose a comparable account-balance endpoint."""
        key = llm_backend.resolve_provider_key(provider_name)
        if not key:
            self._set_status(
                f"{provider_name}: no key configured. Click Edit to add one."
            )
            return
        self._set_status(f"{provider_name}: fetching balance…")

        def worker():
            ok, message = self._provider_balance_call(provider_name, key)
            wx.CallAfter(self._on_provider_balance_done, provider_name, ok, message)

        threading.Thread(target=worker, daemon=True).start()

    def _provider_balance_call(self, provider_name, key):
        """Provider-specific balance/usage fetch. Returns (ok, message)
        where `message` is a short single-line string fit for the
        status field. New providers go here as elif branches."""
        try:
            if provider_name == "openrouter":
                # /api/v1/credits returns {data: {total_credits, total_usage}}
                # in dollars. Remaining balance = total_credits - total_usage.
                # This is the field OpenRouter's web dashboard surfaces
                # only inside a chart; we just want the number.
                req = urllib.request.Request(
                    "https://openrouter.ai/api/v1/credits",
                    headers={"Authorization": f"Bearer {key}"},
                )
                # Non-200 raises HTTPError — handled by the except below.
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = resp.read().decode("utf-8", "replace")
                data = json.loads(raw).get("data") or {}
                total_credits = float(data.get("total_credits") or 0.0)
                total_usage = float(data.get("total_usage") or 0.0)
                remaining = total_credits - total_usage
                # Format: balance first (the thing the user asked for),
                # then enough context to make the number readable
                # ("X.XX remaining of Y.YY total = Z.ZZ spent").
                return True, (
                    f"OpenRouter balance: ${remaining:.2f} remaining "
                    f"(${total_usage:.2f} spent of ${total_credits:.2f} "
                    f"in credits)."
                )
            else:
                return False, f"No balance check implemented for {provider_name!r}."
        except urllib.error.HTTPError as e:
            return False, f"{provider_name}: HTTP {e.code} {e.reason}"
        except urllib.error.URLError as e:
            return False, f"{provider_name}: network error: {e.reason}"
        except Exception as e:
            return False, f"{provider_name}: {type(e).__name__}: {e}"

    def _on_provider_balance_done(self, provider_name, ok, message):
        """Surface the balance result on the Activity field AND speak
        it via NVDA, same shape as _on_provider_test_done. User-invoked
        action → MUST speak regardless of nvda_mode (otherwise pressing
        the button gives no feedback to a screen-reader user)."""
        self._set_status(message, speak=True)

    def _on_provider_test_done(self, provider_name, ok, message):
        """Surface the test result on the Activity field AND speak it
        via NVDA. Test results are user-invoked actions (the user
        clicked Test and is waiting for an answer) — they MUST speak
        regardless of the nvda_mode preference, which gates flood-
        prone things like phase announcements and reply readout.
        Otherwise a screen-reader user clicks Test and gets no
        feedback at all unless they happen to tab to the Activity
        field afterwards. Refreshes the display in case the user
        fixed an unrelated issue between Edit and Test."""
        self._set_status(message, speak=True)
        self._refresh_provider_key_display(provider_name)

    def _send_hint_text(self):
        return ("Send: Enter  |  Newline: Shift+Enter"
                if self.config.get("enter_sends")
                else "Send: Ctrl+Enter  |  Newline: Enter")
