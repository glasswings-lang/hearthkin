# SPDX-License-Identifier: CC0-1.0

"""dialogs.app_prompt — generic per-kin editor for an app-level prompt.

Used by the EditKinDialog "Prompts" tab. Unlike DistillPromptDialog (which is
hard-wired to the distillation prompt), this one is generic over any prompt:
the caller passes the title, the kin's current effective text, and the built-in
default to restore to. Saving the result writes a per-kin override; the cascade
(kin override -> install-wide shared -> in-code default) is resolved by the
loader, so this dialog only deals in "what should this one kin see."
"""

import wx


class AppPromptEditDialog(wx.Dialog):
    """Edit one prompt's text for one kin. Returns the edited text via
    get_prompt(); the caller decides where to persist it."""

    def __init__(self, parent, kin_name, title, current_text, default_text,
                 help_text=""):
        super().__init__(parent, title=f"{title}: {kin_name}", size=(680, 580))
        self._default_text = default_text or ""
        panel = wx.Panel(self)

        blurb = help_text or (
            "This is the text Hearthkin uses for this prompt when talking as "
            f"{kin_name}. Editing it here saves a copy that applies to "
            f"{kin_name} only — other kin are unaffected. Restore default puts "
            "back Hearthkin's built-in wording (it does not save until you "
            "press OK). Any {curly_brace} slots are filled in at send time; "
            "keep them where you want those values to appear."
        )
        intro = wx.StaticText(panel, label=blurb)
        intro.Wrap(640)

        self.prompt_field = wx.TextCtrl(
            panel, value=current_text or "",
            style=wx.TE_MULTILINE | wx.TE_WORDWRAP,
        )
        self.prompt_field.SetMinSize((-1, 360))
        self.prompt_field.SetName(f"{title} text")

        restore_btn = wx.Button(panel, label="&Restore default")
        restore_btn.Bind(
            wx.EVT_BUTTON,
            lambda _e: self.prompt_field.SetValue(self._default_text),
        )

        ok_btn = wx.Button(panel, wx.ID_OK, label="&OK")
        cancel_btn = wx.Button(panel, wx.ID_CANCEL, label="Ca&ncel")
        ok_btn.SetDefault()

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.Add(restore_btn, flag=wx.RIGHT, border=12)
        btn_row.AddStretchSpacer()
        btn_row.Add(ok_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(cancel_btn)

        ps = wx.BoxSizer(wx.VERTICAL)
        ps.Add(intro, flag=wx.BOTTOM, border=10)
        ps.Add(self.prompt_field, proportion=1, flag=wx.EXPAND | wx.BOTTOM, border=10)
        ps.Add(btn_row, flag=wx.EXPAND)
        panel.SetSizer(ps)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, proportion=1, flag=wx.EXPAND | wx.ALL, border=12)
        self.SetSizer(outer)

    def get_prompt(self):
        return self.prompt_field.GetValue()
