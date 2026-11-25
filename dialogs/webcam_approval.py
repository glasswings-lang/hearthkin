# SPDX-License-Identifier: CC0-1.0

"""dialogs.webcam_approval - extracted from the former monolithic dialogs.py."""

import wx


class WebcamApprovalDialog(wx.Dialog):
    """Approval prompt for a `use_webcam` tool call coming from a
    Telegram user (when that user's webcam_permission is "ask").

    Two outcomes:
      - Allow: capture this one time. The kin gets the photo on the
        next inference iteration; the user's webcam_permission setting
        doesn't change. The operator can flip it to "auto" in Kin
        settings, the Telegram or Discord tab, Edit person, if they want to
        stop being asked.
      - Deny: refuse, tool returns a denial string the model can read.

    Default button is Deny — webcam access is privacy-sensitive,
    safer outcome on a stray Enter keystroke. Tab order: explainer
    header → caller info → buttons.
    """

    DECISION_ALLOW = "allow"
    DECISION_DENY = "deny"

    def __init__(self, parent, kin_name, requester_label, requester_id,
                 surface="Telegram"):
        # `surface`: "Telegram" or "Discord", whichever the request came
        # from; this window said Telegram for both (accuracy audit, 24 Nov
        # 2026).
        super().__init__(
            parent,
            title=f"Webcam request from {kin_name}",
            size=(540, 320),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.decision = self.DECISION_DENY  # safety default on dismiss

        header = wx.TextCtrl(
            self,
            value=(
                f"{kin_name} is about to capture a photo from this "
                f"machine's webcam and send it to a {surface} user. "
                f"Allow takes the photo this one time. Deny refuses, and "
                f"{kin_name} is told you declined. To stop being asked "
                f"for this person, use Kin settings, {surface} tab, Edit "
                f"person, and choose Auto-approve."
            ),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        header.SetName("Webcam approval explainer")
        header.SetMinSize((-1, 130))

        who_label = wx.StaticText(self, label="Re&quester:")
        label_part = f" ({requester_label})" if requester_label else ""
        # Multiline so it is reachable by Tab at all. Single-line read-only
        # TextCtrls are not keyboard-focusable on wxMSW, and WHO is asking for
        # the webcam is the one fact this dialog exists to put in front of you
        # before you answer.
        who_field = wx.TextCtrl(
            self,
            value=f"{surface} user {requester_id}{label_part}",
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
            | wx.TE_NO_VSCROLL,
        )
        who_field.SetMinSize((-1, 40))

        allow_btn = wx.Button(self, label="A&llow capture")
        deny_btn = wx.Button(self, label="&Deny")
        deny_btn.SetDefault()

        allow_btn.Bind(
            wx.EVT_BUTTON, lambda _e: self._finish(self.DECISION_ALLOW)
        )
        deny_btn.Bind(
            wx.EVT_BUTTON, lambda _e: self._finish(self.DECISION_DENY)
        )

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(header, flag=wx.EXPAND | wx.ALL, border=8)
        sizer.Add(who_label, flag=wx.LEFT | wx.RIGHT, border=8)
        sizer.Add(who_field,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.AddStretchSpacer(1)
        btn_row.Add(allow_btn, flag=wx.LEFT, border=4)
        btn_row.Add(deny_btn, flag=wx.LEFT, border=4)
        sizer.Add(btn_row, flag=wx.EXPAND | wx.ALL, border=8)
        self.SetSizer(sizer)

    def _finish(self, decision):
        self.decision = decision
        self.EndModal(wx.ID_OK)


