"""Per-kin Telegram message-behaviour settings, in their own dialog.

Split off the Telegram tab to keep it to the core: the bot token, the
allowed-user and group lists, and the run-bot toggle. These are the
quieter message-behaviour knobs — whether tool calls show in chat, the
tool-summary footer, the long-tool-loop progress ping, the per-surface
history cap, and the reply length cap.

Button-opens-dialog (NVDA-discoverable), flat. Most knobs save into the
telegram sub-dict via `save_telegram_param`; the reply length cap is a
top-level key (`telegram_token_cap`) and uses `save_param`. Both
callbacks come from the parent EditKinDialog, so saves are byte-identical
to the old inline controls.
"""

import wx

from ._shared import _IntField


class TelegramMessageSettingsDialog(wx.Dialog):
    def __init__(self, parent, cfg, save_param, save_telegram_param, kin_name=""):
        title = "Telegram message settings"
        if kin_name:
            title = f"{title} — {kin_name}"
        super().__init__(parent, title=title,
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.cfg = cfg
        self._save_param = save_param
        self._save_telegram_param = save_telegram_param
        tg = cfg.get("telegram") or {}

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        def help(text):
            t = wx.StaticText(panel, label=text)
            t.Wrap(540)
            t.SetForegroundColour(wx.Colour(110, 110, 110))
            sizer.Add(t, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=6)

        # First, because it's the only knob here about how the kin treats
        # YOU rather than how it reports on itself — and because the
        # default can't be right for everyone. Telegram never tells a bot
        # that someone is typing, so there is no way to wait until a
        # person has finished; the length of the pause has to be theirs to
        # choose. Someone composing slowly needs longer than the default,
        # and being answered halfway through a thought, every time, is a
        # far worse failure than a couple of seconds of quiet.
        wait_row = wx.BoxSizer(wx.HORIZONTAL)
        # Mnemonic on "message", not "Wait" — W is already taken by the
        # tool-summary footer checkbox below ("Sho&w").
        wait_row.Add(wx.StaticText(panel, label="Wait for the rest of my &message (seconds):"),
                     flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        self.message_wait_field = _IntField(
            panel, value=tg.get("message_wait_secs", 2),
            min_val=0, max_val=600, size=(100, -1),
            name="Wait for the rest of my message, in seconds (0 = answer immediately)",
            on_commit=lambda v: self._save_telegram_param("message_wait_secs", v))
        wait_row.Add(self.message_wait_field)
        sizer.Add(wait_row, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        help("After a message arrives, how long the kin keeps listening for "
             "more from you before it answers — so a thought you send across "
             "two messages gets one reply instead of two talking over each "
             "other. Raise this if you compose slowly and would rather the kin "
             "waited than answered a half-finished thought; the cost is that "
             "much quiet before every reply. Default 2 seconds. 0 means answer "
             "the moment you hit send — a message Telegram itself cut in half "
             "(anything over 4096 characters) is still put back together, since "
             "that pause isn't one you chose.")

        self.show_tool_calls_check = wx.CheckBox(
            panel, label="&Show tool calls in chat (🔧 name(args) and result preview)")
        self.show_tool_calls_check.SetValue(bool(tg.get("show_tool_calls", False)))
        self.show_tool_calls_check.Bind(
            wx.EVT_CHECKBOX, lambda e: self._save_telegram_param(
                "show_tool_calls", bool(self.show_tool_calls_check.GetValue())))
        sizer.Add(self.show_tool_calls_check, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)

        self.show_tool_summary_check = wx.CheckBox(
            panel, label="Sho&w tool-summary footer on replies (\"_used: note_\" italic line)")
        self.show_tool_summary_check.SetValue(bool(tg.get("show_tool_summary", True)))
        self.show_tool_summary_check.Bind(
            wx.EVT_CHECKBOX, lambda e: self._save_telegram_param(
                "show_tool_summary", bool(self.show_tool_summary_check.GetValue())))
        sizer.Add(self.show_tool_summary_check, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        help("Small italic footer listing which tools actually fired this turn "
             "— a ground-truth receipt the kin can't fake. Useful as a quieter "
             "signal when the full display above is off. Default on.")

        # The recall footer's sibling. Existed as a config key from the
        # per-turn-retrieval work but was never surfaced here, so it sat on
        # at its default with no way to turn it off short of hand-editing
        # config.json. Placed next to the tool-summary footer because the two
        # are easily confused: this one fires on turns where NO tool ran,
        # which can read as "a footer appears on every message", which in turn
        # makes a missing tool footer look like meaningful evidence.
        self.show_recall_check = wx.CheckBox(
            panel,
            label="Show memory-recall &footer on replies "
                  "(\"_recalled: notes.md_\" italic line)")
        self.show_recall_check.SetValue(bool(tg.get("show_recall_summary", True)))
        self.show_recall_check.Bind(
            wx.EVT_CHECKBOX, lambda e: self._save_telegram_param(
                "show_recall_summary", bool(self.show_recall_check.GetValue())))
        sizer.Add(self.show_recall_check, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        help("Names the memory logs per-turn recall surfaced for this reply — "
             "what depth the kin drew on without calling a tool. Genuinely "
             "useful when you're tuning recall, noisy the rest of the time, "
             "since it fires on most turns whether or not anything else "
             "happened. Default on. Turn it off if replies are getting buried; "
             "recall keeps working either way, you just stop being told.")

        prog_row = wx.BoxSizer(wx.HORIZONTAL)
        prog_row.Add(wx.StaticText(panel, label="Tool-loop &progress ping every (seconds, 0=off):"),
                     flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        self.progress_field = _IntField(
            panel, value=tg.get("tool_progress_interval_secs", 90),
            min_val=0, max_val=3600, size=(100, -1),
            # "0 = off" in the name — the StaticText holding it is never
            # announced, so the off switch was sighted-only.
            name="Tool-loop progress ping interval in seconds (0 = off)",
            on_commit=lambda v: self._save_telegram_param("tool_progress_interval_secs", v))
        prog_row.Add(self.progress_field)
        sizer.Add(prog_row, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        help("When full tool-call display is off and the kin enters a long "
             "tool loop, Telegram only shows the typing indicator. This posts "
             "a short \"still working — N tool calls so far\" message every N "
             "seconds so a long pass is visibly distinct from a hang. "
             "Default 90; 0 disables.")

        hist_row = wx.BoxSizer(wx.HORIZONTAL)
        hist_row.Add(wx.StaticText(panel, label="&History cap (messages):"),
                     flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        self.history_cap_field = _IntField(
            panel, value=tg.get("history_cap", 100),
            min_val=0, max_val=100000, size=(100, -1),
            name="Telegram history cap (messages)",
            on_commit=lambda v: self._save_telegram_param("history_cap", v))
        hist_row.Add(self.history_cap_field)
        sizer.Add(hist_row, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        help("How many messages each non-share Telegram user or group keeps "
             "before the oldest get trimmed. 0 = unlimited (constrained only "
             "by num_ctx). Default 100. Share-with-desktop users use the kin's "
             "main conversation file and aren't affected.")

        cap_row = wx.BoxSizer(wx.HORIZONTAL)
        cap_row.Add(wx.StaticText(panel, label="&Reply length cap (tokens):"),
                    flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        self.token_cap_field = _IntField(
            panel, value=cfg.get("telegram_token_cap", 900),
            min_val=50, max_val=32768, size=(100, -1),
            name="Reply length cap (tokens)",
            on_commit=lambda v: self._save_param("telegram_token_cap", v))
        cap_row.Add(self.token_cap_field)
        sizer.Add(cap_row, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        help("Soft cap on how long each Telegram reply can grow — the circuit "
             "breaker that stops a cascading model from generating to the "
             "context ceiling. Default 900 (matches desktop chat).")

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        close_btn = wx.Button(panel, wx.ID_CLOSE, "&Close")
        close_btn.Bind(wx.EVT_BUTTON, self._on_close)
        btn_row.AddStretchSpacer()
        btn_row.Add(close_btn)
        sizer.Add(btn_row, flag=wx.EXPAND | wx.ALL, border=10)

        self.SetEscapeId(wx.ID_CLOSE)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        panel.SetSizer(sizer)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, proportion=1, flag=wx.EXPAND)
        self.SetSizer(outer)
        self.SetInitialSize((560, 520))
        self.Layout()

    def _on_close(self, _event):
        if self.IsModal():
            self.EndModal(wx.ID_CLOSE)
        else:
            self.Destroy()
