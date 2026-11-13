# SPDX-License-Identifier: CC0-1.0

"""dialogs.compact — the note a kin wrote before its conversation is
compacted (Chat -> Compact this conversation, 12 Nov 2026).

Shows the note so the person can read it before anything changes, edit it,
have it written again, or cancel. Nothing is deleted by compacting: the note
goes into the conversation as a marker, and from then on the kin is sent the
note and the last few messages rather than everything before them.

ShowModal returns wx.ID_OK (compact with the note as it now reads),
wx.ID_RETRY (ask the kin to write it again) or wx.ID_CANCEL."""

import wx


class CompactDialog(wx.Dialog):
    def __init__(self, parent, kin, note, keep=6):
        super().__init__(
            parent, title=f"Compact this conversation with {kin}",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        # Read-only explainer first, as a text field so a screen reader
        # reaches it in tab order rather than only on a review sweep.
        info = wx.TextCtrl(
            self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP,
            value=(
                f"Below is the note {kin} wrote for itself. If you compact, "
                f"{kin} will be sent this note and the last {keep} messages "
                "from now on, instead of everything before them.\n"
                "Nothing is deleted. The whole conversation stays here and "
                f"in {kin}'s folder, and memory notes are still made from "
                "all of it.\n"
                "You can change the note before compacting, or have it "
                "written again."))
        info.SetName("What compacting does")
        info.SetMinSize((-1, 96))
        # The label is created immediately before the field it names: that
        # is where Windows takes a field's spoken name from.
        note_label = wx.StaticText(self, label=f"&The note {kin} wrote:")
        self.note_field = wx.TextCtrl(
            self, value=note or "",
            style=wx.TE_MULTILINE | wx.TE_WORDWRAP)
        self.note_field.SetName(f"The note {kin} wrote")
        self.note_field.SetMinSize((520, 240))

        ok_btn = wx.Button(self, wx.ID_OK, label="&Compact with this note")
        again_btn = wx.Button(self, wx.ID_RETRY, label="Write it &again")
        cancel_btn = wx.Button(self, wx.ID_CANCEL, label="Cancel")
        ok_btn.SetDefault()
        again_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_RETRY))

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(info, flag=wx.EXPAND | wx.ALL, border=8)
        sizer.Add(note_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        sizer.Add(self.note_field, proportion=1,
                  flag=wx.EXPAND | wx.ALL, border=8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.AddStretchSpacer(1)
        row.Add(ok_btn, flag=wx.RIGHT, border=4)
        row.Add(again_btn, flag=wx.RIGHT, border=4)
        row.Add(cancel_btn)
        sizer.Add(row, flag=wx.EXPAND | wx.ALL, border=8)
        self.SetSizerAndFit(sizer)
        # Start on the note: it's what the person came here to read.
        self.note_field.SetFocus()
        self.note_field.SetInsertionPoint(0)

    def get_note(self):
        return self.note_field.GetValue().strip()
