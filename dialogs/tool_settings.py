"""Per-kin tool-behaviour settings, in their own dialog.

Split off the Tools tab to keep it focused on the two everyday things —
the trust level (whether exec calls get gated) and the per-tool enable
list. These are the lower-frequency numeric knobs: the Telegram approval
timeout, how much tool history is kept verbatim, the per-result character
cap, and the max tool calls per reply.

Button-opens-dialog (NVDA-discoverable), flat, like the recall / sampling
/ model-options dialogs. The Telegram approval timeout saves into the
telegram sub-dict (the bot reads it there), so this dialog takes both the
top-level `save_param` and the `save_telegram_param` callbacks; everything
else is top-level. Saves are byte-identical to the old inline fields.
"""

import wx

from ._shared import _IntField


class ToolSettingsDialog(wx.Dialog):
    def __init__(self, parent, cfg, save_param, save_telegram_param, kin_name=""):
        title = "Tool behaviour settings"
        if kin_name:
            title = f"{title} — {kin_name}"
        super().__init__(parent, title=title,
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.cfg = cfg
        self._save_param = save_param
        self._save_telegram_param = save_telegram_param

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        def row(label, make_field):
            # Create the StaticText label BEFORE the field so that, in
            # z-order (= widget creation order), each field's immediately
            # preceding widget is its OWN label. wxMSW/NVDA derives a plain
            # TextCtrl's accessible name from the nearest preceding
            # StaticText; if the field is created first (as this dialog used
            # to do), NVDA pairs it with the *previous* row's label and the
            # whole column reads shifted by one. SetName() on a TextCtrl does
            # NOT override this on wxMSW, so z-order is the real fix.
            r = wx.BoxSizer(wx.HORIZONTAL)
            lbl = wx.StaticText(panel, label=label)
            field = make_field()
            r.Add(lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
            r.Add(field)
            sizer.Add(r, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
            return field

        def help(text):
            t = wx.StaticText(panel, label=text)
            t.Wrap(520)
            t.SetForegroundColour(wx.Colour(110, 110, 110))
            sizer.Add(t, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=6)

        tg = cfg.get("telegram") or {}

        self.approval_timeout_field = row(
            "Telegram approval &timeout (seconds):",
            lambda: _IntField(
                panel, value=int(tg.get("approval_timeout_secs") or 600),
                min_val=30, max_val=86400, size=(100, -1),
                on_commit=lambda v: self._save_telegram_param("approval_timeout_secs", v)))
        help("Only on Telegram: when an exec call asks for approval through "
             "chat, how long the kin waits for an 'allow'/'deny' reply before "
             "auto-denying. Desktop approval dialogs wait for your click. "
             "30–86400; default 600 (10 min).")

        # Remote unattended exec toggle. OFF (default) makes a remote exec ask
        # for approval regardless of trust level; ON lets a Trusted/Full kin
        # run remote commands with no prompt (the denylist still always
        # applies). This is the accessible surface for the config key of the
        # same name — the alternative was hand-editing config.json.
        self.unattended_exec_check = wx.CheckBox(
            panel,
            label="Run remote (Telegram/Discord) &exec without asking")
        self.unattended_exec_check.SetValue(
            bool(cfg.get("remote_unattended_exec", False)))
        self.unattended_exec_check.Bind(
            wx.EVT_CHECKBOX,
            lambda e: self._save_param("remote_unattended_exec", e.IsChecked()))
        sizer.Add(self.unattended_exec_check,
                  flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        help("Affects REMOTE surfaces only (Telegram, Discord) — never changes "
             "how exec behaves in the desktop chat. OFF (default): a command "
             "that arrives over Telegram/Discord asks for your OK first — in "
             "the Telegram chat, or a desktop dialog for Discord — even when "
             "the kin's trust level is Trusted or Full. ON: a Trusted or Full "
             "kin runs remote commands with no prompt, exactly like it already "
             "does on the desktop. Either way the denylist (rm -rf /, drive "
             "wipes, force-push to main) is ALWAYS enforced. Turn this ON when "
             "the kin is really just you over Telegram and the approval prompts "
             "for harmless commands (listing a folder, reading a file) are in "
             "your way.")

        # Remote file confinement opt-out. OFF (default) restricts the file
        # tools to the kin's own folder on Telegram/Discord — relative paths
        # work as always, absolute paths are refused. ON gives the kin the
        # same reach it already has on the desktop. Separate control from
        # the exec toggle above: that one governs exec APPROVAL, this one
        # governs file PATHS, and neither implies the other. Operators
        # reasonably expect the exec toggle to cover this; it does not, so
        # both help texts say so explicitly.
        self.unconfined_files_check = wx.CheckBox(
            panel,
            label="Let remote (Telegram/Discord) &file tools reach outside "
                  "the kin folder")
        self.unconfined_files_check.SetValue(
            bool(cfg.get("remote_unconfined_files", False)))
        self.unconfined_files_check.Bind(
            wx.EVT_CHECKBOX,
            lambda e: self._save_param("remote_unconfined_files",
                                       e.IsChecked()))
        sizer.Add(self.unconfined_files_check,
                  flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        help("Affects REMOTE surfaces only (Telegram, Discord) — the desktop "
             "chat already reaches anywhere and is not changed by this. OFF "
             "(default): read_file, write_file and edit_file are restricted "
             "to the kin's own folder over Telegram/Discord; a relative path "
             "like 'memory.md' works, an absolute path like 'C:\\Users\\...' "
             "is refused. ON: the kin reads and writes anywhere on this "
             "machine over Telegram/Discord, exactly as it already can on the "
             "desktop. This is a separate switch from the exec one above — "
             "turning that one on does NOT lift this restriction. Worth "
             "knowing before you turn it on: a remote surface is reachable by "
             "anyone who gets the bot token or gets text into the kin's "
             "context, so if the kin also has fetch_url or web_search, a "
             "hostile web page it reads could try to talk it into fetching a "
             "file and posting the contents back. Turn this ON when the "
             "remote surface is really just you.")

        self.toolhist_field = row(
            "Tool &history kept:",
            lambda: _IntField(
                panel, value=cfg.get("tool_history_keep", 5),
                min_val=0, max_val=50, size=(100, -1),
                on_commit=lambda v: self._save_param("tool_history_keep", v)))
        help("Recent tool round-trips kept verbatim in context; older ones "
             "are summarized to a single line so context doesn't bloat. "
             "0 = always summarize. Default 5.")

        self.tool_result_cap_field = row(
            "Tool &result cap (chars):",
            lambda: _IntField(
                panel, value=cfg.get("tool_result_cap", 8000),
                min_val=0, max_val=65536, size=(100, -1),
                on_commit=lambda v: self._save_param("tool_result_cap", v)))
        help("Each tool result (e.g. read_file output) is truncated to this "
             "many characters before the kin sees it. Default 8000 (~2000 "
             "tokens). Raise for kin reading big files; 0 = no truncation "
             "here (num_ctx still applies downstream).")

        self.max_tool_iter_field = row(
            "&Max tool calls per reply:",
            lambda: _IntField(
                panel, value=cfg.get("max_tool_iterations", 8),
                min_val=1, max_val=2000, size=(100, -1),
                on_commit=lambda v: self._save_param("max_tool_iterations", v)))
        help("How many tool call → result cycles one reply may run before it "
             "stops. Default 8. On a LOCAL model each cycle is just time, not "
             "money — raise it freely for a game-playing kin or long agentic "
             "work (the dig-and-build grind in the tff park game, for instance, "
             "wants a high cap). On a PAID model (OpenRouter) each cycle is "
             "another billable call, so keep it modest there.")

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
        self.SetInitialSize((560, 560))
        self.Layout()

    def _on_close(self, _event):
        if self.IsModal():
            self.EndModal(wx.ID_CLOSE)
        else:
            self.Destroy()
