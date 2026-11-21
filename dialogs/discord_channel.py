# SPDX-License-Identifier: CC0-1.0

"""dialogs.discord_channel - one Discord server channel, like a Telegram group.

Added 20 Nov 2026 so Discord channels work the way Telegram groups do: each
channel the kin is in has its own window, with a name the kin sees, when the
kin answers there, a mute list, and the two switches for what the channel
shares with the person's private chats. Before, Discord had one line of
channel IDs, one answering rule for every channel, one people list for every
place, and no way to mute anyone.
"""

import wx


class _DiscordChannelDialog(wx.Dialog):
    """Add or edit one channel. get_values() returns (channel_id, name,
    policy, exclude, private_sees, sees_private); channel_id is digits only,
    "" when what was typed wasn't an ID; exclude is a list of user ID
    strings.

    Tab order: ID, name, the two answering choices, the mute list and its
    buttons, the two switches, Save, Cancel."""

    def __init__(self, parent, channel_id="", name="", policy="mention_only",
                 exclude=None, private_sees=False, sees_private=False):
        super().__init__(parent, title="Discord channel",
                         style=wx.DEFAULT_DIALOG_STYLE)
        self._exclude = [str(x) for x in (exclude or []) if str(x).strip()]
        outer = wx.BoxSizer(wx.VERTICAL)

        id_lbl = wx.StaticText(self, label="&Channel ID:")
        self.id_field = wx.TextCtrl(self, value=str(channel_id or ""))
        outer.Add(id_lbl, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        outer.Add(self.id_field, flag=wx.EXPAND | wx.LEFT | wx.RIGHT,
                  border=8)

        name_lbl = wx.StaticText(
            self, label="&Display label (kin sees this as the channel's name):")
        self.name_field = wx.TextCtrl(self, value=str(name or ""))
        outer.Add(name_lbl, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        outer.Add(self.name_field, flag=wx.EXPAND | wx.LEFT | wx.RIGHT,
                  border=8)

        self.mention_rb = wx.RadioButton(
            self, label="&Mention-only: the kin answers when @-mentioned "
                        "or replied to with a mention",
            style=wx.RB_GROUP)
        self.always_rb = wx.RadioButton(
            self, label="Al&ways: the kin answers every message here "
                        "(best in a quiet channel)")
        (self.always_rb if policy == "always" else self.mention_rb).SetValue(
            True)
        outer.Add(self.mention_rb, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        outer.Add(self.always_rb, flag=wx.LEFT | wx.RIGHT, border=8)

        muted_lbl = wx.StaticText(self, label="Muted in this channel:")
        self.muted_list = wx.ListBox(self, style=wx.LB_SINGLE)
        self.muted_list.SetName(
            "Muted in this channel (the kin ignores these people here)")
        self.muted_list.SetMinSize((-1, 70))
        outer.Add(muted_lbl, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        outer.Add(self.muted_list, flag=wx.EXPAND | wx.LEFT | wx.RIGHT,
                  border=8)
        mute_row = wx.BoxSizer(wx.HORIZONTAL)
        add_btn = wx.Button(self, label="Add by &ID…")
        add_btn.Bind(wx.EVT_BUTTON, self._on_mute)
        unmute_btn = wx.Button(self, label="&Unmute")
        unmute_btn.Bind(wx.EVT_BUTTON, self._on_unmute)
        mute_row.Add(add_btn, flag=wx.RIGHT, border=6)
        mute_row.Add(unmute_btn)
        outer.Add(mute_row, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)

        self.private_sees_check = wx.CheckBox(
            self,
            label=("Your private chats with the kin inc&lude this channel: "
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
            value=("Everyone in this channel can talk to the kin except the "
                   "people muted here. Your people list is for direct "
                   "messages only. With either switch on, what's said here is "
                   "kept with your desktop chat, so clearing the desktop chat "
                   "clears it too. To find an ID, turn on Developer Mode in "
                   "Discord (Settings, Advanced), then right-click the "
                   "channel or person and choose Copy ID."),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL
            | wx.TE_WORDWRAP)
        hint.SetName("How this channel works")
        hint.SetMinSize((460, 100))
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
        self._refresh_muted()
        (self.name_field if channel_id else self.id_field).SetFocus()

    def _refresh_muted(self):
        self.muted_list.Set(list(self._exclude))

    def _on_mute(self, _event):
        dlg = wx.TextEntryDialog(
            self, "Discord user ID to mute in this channel:", "Mute someone")
        if dlg.ShowModal() == wx.ID_OK:
            uid = "".join(c for c in dlg.GetValue() if c.isdigit())
            if uid and uid not in self._exclude:
                self._exclude.append(uid)
                self._refresh_muted()
        dlg.Destroy()

    def _on_unmute(self, _event):
        idx = self.muted_list.GetSelection()
        if 0 <= idx < len(self._exclude):
            self._exclude.pop(idx)
            self._refresh_muted()

    def get_values(self):
        cid = "".join(c for c in self.id_field.GetValue() if c.isdigit())
        policy = "always" if self.always_rb.GetValue() else "mention_only"
        return (cid, self.name_field.GetValue().strip(), policy,
                list(self._exclude),
                bool(self.private_sees_check.GetValue()),
                bool(self.sees_private_check.GetValue()))
