# SPDX-License-Identifier: CC0-1.0

"""dialogs.distill_prompt - extracted from the former monolithic dialogs.py."""

import wx

from kin_persistence import DEFAULT_DISTILL_PROMPT


class DistillPromptDialog(wx.Dialog):
    """Edit the per-kin memory distillation system prompt.

    The text below is the system message sent to the summarizer model when
    memory is being distilled. {kin_name} is substituted with the actual kin's
    name at runtime. The user message that follows is mechanical (existing
    memory + conversation transcript) and isn't editable here.
    """

    def __init__(self, parent, kin_name, current_prompt):
        super().__init__(parent, title=f"Distillation prompt: {kin_name}", size=(640, 540))
        panel = wx.Panel(self)

        intro = wx.StaticText(
            panel,
            label=(
                "This is the system prompt sent to the summarizer model when "
                f"distilling memory for {kin_name}. The placeholder {{kin_name}} "
                "is substituted with the kin's actual name at runtime.\n\n"
                "After this system prompt, Hearthkin sends a user message containing "
                "the existing memory and the recent conversation; you don't edit that part."
            ),
        )
        intro.Wrap(600)

        self.prompt_field = wx.TextCtrl(panel, value=current_prompt,
                                        style=wx.TE_MULTILINE | wx.TE_WORDWRAP)
        self.prompt_field.SetMinSize((-1, 320))

        restore_btn = wx.Button(panel, label="&Restore default")
        restore_btn.Bind(
            wx.EVT_BUTTON,
            lambda e: self.prompt_field.SetValue(DEFAULT_DISTILL_PROMPT),
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


