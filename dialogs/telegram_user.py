# SPDX-License-Identifier: CC0-1.0

"""dialogs.telegram_user - extracted from the former monolithic dialogs.py."""

import wx


class _TelegramUserDialog(wx.Dialog):
    """Small modal for adding or editing one Telegram-user entry on a
    kin. Three fields: numeric user_id, display label, tool-access
    bucket. The bucket is a radio group rather than a dropdown — radios
    are individually focusable, NVDA reads each option's name and
    explainer as you arrow through.

    The display label is NOT cosmetic — when set, it's the inline
    attribution prefix the kin sees on this user's messages in both
    DMs and groups (e.g. "[Alex] hello" instead of the Telegram-
    derived "[Display Name (@username)] hello"). Blank = let
    Telegram's first/last/username show through.

    Returns (user_id_str, label_str, bucket_name) via get_values() when
    ShowModal returns wx.ID_OK. Caller writes back to cfg + persists.
    """

    def __init__(self, parent, user_id="", label="", bucket="none",
                 share_desktop=False, mirror_to_telegram=False,
                 webcam_permission="ask"):
        super().__init__(parent, title="Telegram user",
                         style=wx.DEFAULT_DIALOG_STYLE)
        self._bucket = bucket or "none"
        self._share_desktop_initial = bool(share_desktop)
        self._mirror_to_telegram_initial = bool(mirror_to_telegram)
        self._webcam_permission = (webcam_permission or "ask").lower()
        if self._webcam_permission not in ("ask", "auto", "deny"):
            self._webcam_permission = "ask"

        outer = wx.BoxSizer(wx.VERTICAL)

        id_lbl = wx.StaticText(self, label="&User ID (numeric Telegram ID):")
        self.id_field = wx.TextCtrl(self, value=user_id)
        outer.Add(id_lbl, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        outer.Add(self.id_field, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        # How to obtain the ID the field above wants. The next field takes its
        # name from its own StaticText below, so this one labels nothing and was
        # reaching sighted users only.
        id_hint = wx.TextCtrl(
            self,
            value="Tip: the user can type /whoami to the bot to find their ID.",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        id_hint.SetName("How to find a user's Telegram ID")
        id_hint.SetMinSize((-1, 24))
        outer.Add(id_hint, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        label_lbl = wx.StaticText(
            self, label="&Display label (kin sees this as the user's name):",
        )
        self.label_field = wx.TextCtrl(self, value=label)
        outer.Add(label_lbl, flag=wx.LEFT | wx.RIGHT, border=8)
        outer.Add(self.label_field, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=8)
        # Says the label is not cosmetic — it's what the kin reads as this
        # person's name. Nothing takes its name from it (the radios below use
        # their own labels), so it was unreachable from the keyboard.
        label_hint = wx.TextCtrl(
            self,
            value=(
                "Inlined onto every message this user sends to the kin — "
                "DMs and groups both. e.g. set this to \"Alex\" and the "
                "kin sees \"[Alex] hello\" instead of "
                "\"[Display Name (@username)] hello\". Leave blank to "
                "let Telegram's own name show through."
            ),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        label_hint.SetName("What the display label does")
        label_hint.SetMinSize((-1, 80))
        outer.Add(label_hint, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        # The radios below name themselves from their own labels and ignore a
        # preceding StaticText, so as a StaticText this question is spoken to
        # nobody — you arrow through the buckets never hearing what they answer.
        # A read-only TextCtrl is in the tab order.
        bucket_lbl = wx.TextCtrl(
            self,
            value="Tool access for this user:",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        bucket_lbl.SetName("Tool access for this user — heading for the options below")
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

        # Share-with-desktop checkbox. When on, this user's chat with
        # the kin uses the kin's main conversation.jsonl, the same
        # file the desktop reads + writes, so the conversation is
        # continuous across surfaces. Off by default — sharing
        # generally only makes sense for the operator's own Telegram
        # number, not for friends or other users whose chats should
        # stay separate from the operator's desktop view.
        self.share_desktop_check = wx.CheckBox(
            self,
            label=(
                "&Share conversation with Hearthkin desktop "
                "(usually only your own Telegram user)"
            ),
        )
        self.share_desktop_check.SetValue(self._share_desktop_initial)
        outer.Add(self.share_desktop_check,
                  flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        # What the checkbox above actually does. A StaticText between two
        # checkboxes labels neither of them, so this only ever reached sighted
        # users; read-only TextCtrl puts it in the tab order.
        share_hint = wx.TextCtrl(
            self,
            value=(
                "When on: messages from this Telegram user land in the "
                "kin's main conversation file, so the desktop chat and "
                "this Telegram thread are the same conversation. The "
                "kin sees both surfaces' contributions as one thread. "
                "Off (default): this user gets their own private "
                "conversation history with the kin."
            ),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        share_hint.SetName("Share conversation with desktop explainer")
        share_hint.SetMinSize((-1, 80))
        outer.Add(share_hint, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        # Mirror-to-Telegram opt-in. Independent of share — when on,
        # every desktop-side message + reply for this kin gets pushed
        # to the user's Telegram chat with a "💻 (desktop)" prefix.
        # Lets you carry on a desktop conversation and then pick it
        # up from your phone with the full visible history.
        self.mirror_to_telegram_check = wx.CheckBox(
            self,
            label=(
                "&Mirror desktop messages to my Telegram chat "
                "(carry desktop conversation to phone)"
            ),
        )
        self.mirror_to_telegram_check.SetValue(self._mirror_to_telegram_initial)
        outer.Add(self.mirror_to_telegram_check,
                  flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        # What the checkbox above does, and how it differs from sharing. The
        # webcam radios below use their own labels, so this named nothing and
        # only reached sighted users.
        mirror_hint = wx.TextCtrl(
            self,
            value=(
                "When on: every desktop send + kin reply for this kin "
                "gets pushed to this user's Telegram chat, prefixed "
                "'💻 (desktop)'. Useful with share-with-desktop above "
                "if you want to see the desktop conversation reflected "
                "in Telegram and not just have the kin remember it. "
                "Off (default): nothing pushed; desktop conversations "
                "stay on the desktop."
            ),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        mirror_hint.SetName("Mirror desktop messages to Telegram explainer")
        mirror_hint.SetMinSize((-1, 95))
        outer.Add(mirror_hint, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        # Webcam permission. Layered on top of the bucket gate above —
        # bucket says "user can EVER call use_webcam"; this radio says
        # "what happens when they actually try". Ask is the safety
        # default; Auto skips the operator-side prompt for trusted
        # users; Deny refuses outright.
        # Same as the bucket radios above: a StaticText here would name nothing,
        # leaving the three options with no audible question.
        webcam_lbl = wx.TextCtrl(
            self,
            value="Webcam permission for this user:",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        webcam_lbl.SetName("Webcam permission for this user — heading for the options below")
        webcam_lbl.SetMinSize((-1, 24))
        outer.Add(webcam_lbl, flag=wx.LEFT | wx.RIGHT, border=8)
        self._webcam_radios = {}
        _webcam_options = [
            ("ask",
             "&Ask each time — pop a dialog on the desktop to approve / deny each request"),
            ("auto",
             "A&uto-approve — capture without asking (for trusted users)"),
            ("deny",
             "Al&ways deny — refuse webcam requests from this user silently"),
        ]
        for i, (key, text) in enumerate(_webcam_options):
            style = wx.RB_GROUP if i == 0 else 0
            rb = wx.RadioButton(self, label=text, style=style)
            rb.SetValue(key == self._webcam_permission)
            self._webcam_radios[key] = rb
            outer.Add(rb, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        # States when this setting is live at all. The next control is the Save
        # button, which names itself from its label, so as a StaticText this
        # caveat was unreachable from the keyboard.
        webcam_hint = wx.TextCtrl(
            self,
            value=(
                "Only applies when use_webcam is in the kin's tools "
                "list AND this user's bucket above includes use_webcam "
                "(currently: Write or Full). Otherwise the user can't "
                "call it regardless of this setting."
            ),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        webcam_hint.SetName("When the webcam permission applies")
        webcam_hint.SetMinSize((-1, 60))
        outer.Add(webcam_hint, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

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
        if not user_id:
            self.id_field.SetFocus()
        else:
            self.label_field.SetFocus()

    def get_values(self):
        """Return (user_id, label, bucket, share_desktop,
        mirror_to_telegram, webcam_permission).
        user_id is normalized to digits only (plus optional leading
        '-' for forward-compat with group chat IDs); empty string
        means invalid input."""
        raw = self.id_field.GetValue().strip()
        for prefix in ("telegram:", "tg:"):
            if raw.lower().startswith(prefix):
                raw = raw[len(prefix):].strip()
        sign = ""
        if raw.startswith("-"):
            sign = "-"
            raw = raw[1:]
        uid = sign + "".join(c for c in raw if c.isdigit())
        label = self.label_field.GetValue().strip()
        bucket = "none"
        for name, rb in self._bucket_radios.items():
            if rb.GetValue():
                bucket = name
                break
        share_desktop = bool(self.share_desktop_check.GetValue())
        mirror_to_telegram = bool(self.mirror_to_telegram_check.GetValue())
        webcam_permission = "ask"
        for key, rb in self._webcam_radios.items():
            if rb.GetValue():
                webcam_permission = key
                break
        return (uid, label, bucket, share_desktop, mirror_to_telegram,
                webcam_permission)


