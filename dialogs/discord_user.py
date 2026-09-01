# SPDX-License-Identifier: CC0-1.0

"""dialogs.discord_user — add or edit one Discord-user entry on a kin.

The Discord tab used to carry a single multi-line box of numeric IDs, which
made the surface's per-user tool access (`discord.user_tools`, which the bot
has always read) reachable only by hand-editing JSON. In practice that meant
every Discord user sat on the default bucket, `none`, and no kin ever had a
tool on this surface — while the tab's own setup notes said "the kin uses its
normal tools here". This dialog is that missing screen.

Two fields, deliberately: the Discord user ID, and the tool-access bucket.
There is no display-label field because Discord already gives every message a
display name and the bot inlines that; a second name to keep in sync would
only be a way for the two to disagree.
"""

import wx


class _DiscordUserDialog(wx.Dialog):
    """Modal for one Discord-user entry. Returns (user_id_str, bucket_name)
    from get_values() when ShowModal returns wx.ID_OK; the caller writes back
    to cfg and persists.

    The bucket is a radio group rather than a dropdown — radios are
    individually focusable, so NVDA reads each option's name and its
    explainer as you arrow through, instead of announcing one collapsed
    value you then have to open to inspect.
    """

    def __init__(self, parent, user_id="", bucket="none"):
        super().__init__(parent, title="Discord user",
                         style=wx.DEFAULT_DIALOG_STYLE)
        self._bucket = bucket or "none"

        outer = wx.BoxSizer(wx.VERTICAL)

        id_lbl = wx.StaticText(
            self, label="&User ID (numeric Discord ID, or * for anyone):")
        self.id_field = wx.TextCtrl(self, value=user_id)
        outer.Add(id_lbl, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        outer.Add(self.id_field,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        # How to get the ID the field above wants, and what "*" means. A
        # read-only TextCtrl rather than StaticText: the next control is a
        # TextCtrl heading with its own name, so a StaticText here would
        # label nothing and never be spoken.
        id_hint = wx.TextCtrl(
            self,
            value=(
                "To find someone's ID: turn on Developer Mode in Discord's "
                "settings (Advanced), then right-click them and choose Copy "
                "User ID. Entering * instead lets ANYONE in the servers this "
                "bot has joined talk to the kin — fine on your own private "
                "server, and worth thinking twice about anywhere else, "
                "because the tool access you pick below applies to every one "
                "of them."
            ),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL
            | wx.TE_WORDWRAP,
        )
        id_hint.SetName("How to find a Discord user ID, and what the star means")
        id_hint.SetMinSize((-1, 96))
        outer.Add(id_hint, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        # The radios name themselves from their own labels and ignore a
        # preceding StaticText, so this question has to be in the tab order
        # itself — otherwise you arrow through four options without ever
        # hearing what they answer.
        bucket_lbl = wx.TextCtrl(
            self,
            value="Tool access for this user:",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL
            | wx.TE_WORDWRAP,
        )
        bucket_lbl.SetName(
            "Tool access for this user — heading for the options below")
        bucket_lbl.SetMinSize((-1, 24))
        outer.Add(bucket_lbl, flag=wx.LEFT | wx.RIGHT, border=8)

        from tools._buckets import BUCKET_ORDER, BUCKET_EXPLAINER
        self._bucket_radios = {}
        for i, name in enumerate(BUCKET_ORDER):
            style = wx.RB_GROUP if i == 0 else 0
            label_text = f"&{name.capitalize()} — {BUCKET_EXPLAINER.get(name, '')}"
            rb = wx.RadioButton(self, label=label_text, style=style)
            rb.SetValue(name == self._bucket)
            self._bucket_radios[name] = rb
            outer.Add(rb, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        # The bucket explainers are written for Telegram, which is where they
        # first appeared. Two things genuinely differ here, and both are
        # things a person would otherwise only discover by being surprised.
        surface_hint = wx.TextCtrl(
            self,
            value=(
                "Two differences from Telegram. A shell command (exec) asks "
                "for approval on THIS computer, in a dialog — never in the "
                "Discord server, so nobody there can approve their own "
                "request. And a webcam capture always asks you here too; "
                "Discord has no per-person always-allow setting, so it asks "
                "every time. The kin still only gets whatever tools you have "
                "switched on for it in the Tools tab — this narrows that "
                "list, it can't widen it."
            ),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL
            | wx.TE_WORDWRAP,
        )
        surface_hint.SetName("How tool approval works on Discord")
        surface_hint.SetMinSize((-1, 112))
        outer.Add(surface_hint, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(self, wx.ID_OK, label="&Save")
        cancel_btn = wx.Button(self, wx.ID_CANCEL, label="Ca&ncel")
        btn_row.Add(ok_btn, flag=wx.RIGHT, border=8)
        btn_row.Add(cancel_btn)
        outer.Add(btn_row, flag=wx.ALL | wx.ALIGN_RIGHT, border=8)

        self.SetSizerAndFit(outer)
        self.id_field.SetFocus()

    def get_values(self):
        """(user_id, bucket). The ID is returned as typed apart from
        surrounding whitespace — the caller validates, because it is the one
        that can say which list the value was rejected from."""
        bucket = "none"
        for name, rb in self._bucket_radios.items():
            if rb.GetValue():
                bucket = name
                break
        return self.id_field.GetValue().strip(), bucket
