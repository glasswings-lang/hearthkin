# SPDX-License-Identifier: CC0-1.0

"""dialogs.discord_channel - one Discord server channel's sharing switches.

Added 20 Nov 2026, the Discord counterpart of the Telegram group window's
two switches. Discord channels had no window of their own: which channels a
kin answers in is a line of IDs on the Discord tab, and that stays as it is.
This window is only about what a channel shares with the person's private
chats, so it sits next to the people list on the same tab.
"""

import wx


class _DiscordChannelDialog(wx.Dialog):
    """Add or edit one channel: its ID, a name for it, and the two sharing
    switches. get_values() returns (channel_id, name, private_sees,
    sees_private); channel_id is digits only, "" when what was typed
    wasn't an ID.

    Tab order: ID, name, the two switches, Save, Cancel."""

    def __init__(self, parent, channel_id="", name="", private_sees=True,
                 sees_private=False):
        super().__init__(parent, title="Discord channel",
                         style=wx.DEFAULT_DIALOG_STYLE)
        outer = wx.BoxSizer(wx.VERTICAL)

        id_lbl = wx.StaticText(self, label="Channel &ID:")
        self.id_field = wx.TextCtrl(self, value=str(channel_id or ""))
        outer.Add(id_lbl, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        outer.Add(self.id_field, flag=wx.EXPAND | wx.LEFT | wx.RIGHT,
                  border=8)

        name_lbl = wx.StaticText(
            self, label="&Name (the kin sees it with what's said there):")
        self.name_field = wx.TextCtrl(self, value=str(name or ""))
        outer.Add(name_lbl, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        outer.Add(self.name_field, flag=wx.EXPAND | wx.LEFT | wx.RIGHT,
                  border=8)

        self.private_sees_check = wx.CheckBox(
            self,
            label=("Your private chats with the kin in&clude this channel: "
                   "the desktop, your shared direct messages and wake-ups "
                   "get what's said here, marked with this channel's name."))
        self.private_sees_check.SetValue(bool(private_sees))
        outer.Add(self.private_sees_check,
                  flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)

        self.sees_private_check = wx.CheckBox(
            self,
            label=("This channel &gets your private chats with the kin. "
                   "Everyone here is then in a conversation where the kin "
                   "has your private talk in front of it. Off unless you "
                   "choose it."))
        self.sees_private_check.SetValue(bool(sees_private))
        outer.Add(self.sees_private_check,
                  flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)

        hint = wx.TextCtrl(
            self,
            value=("With either switch on, what's said in this channel is "
                   "kept with your desktop chat, so clearing the desktop "
                   "chat clears it too. To find a channel's ID, turn on "
                   "Developer Mode in Discord (Settings, Advanced), then "
                   "right-click the channel and choose Copy ID."),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL
            | wx.TE_WORDWRAP)
        hint.SetName("What these switches do")
        hint.SetMinSize((420, 80))
        outer.Add(hint, flag=wx.EXPAND | wx.ALL, border=8)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.AddStretchSpacer(1)
        ok_btn = wx.Button(self, wx.ID_OK, label="&Save")
        cancel_btn = wx.Button(self, wx.ID_CANCEL, label="Ca&ncel")
        ok_btn.SetDefault()
        btn_row.Add(ok_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(cancel_btn)
        outer.Add(btn_row, flag=wx.EXPAND | wx.ALL, border=8)

        self.SetSizer(outer)
        self.Fit()
        (self.name_field if channel_id else self.id_field).SetFocus()

    def get_values(self):
        cid = "".join(c for c in self.id_field.GetValue() if c.isdigit())
        return (cid, self.name_field.GetValue().strip(),
                bool(self.private_sees_check.GetValue()),
                bool(self.sees_private_check.GetValue()))
