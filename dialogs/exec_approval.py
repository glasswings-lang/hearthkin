# SPDX-License-Identifier: CC0-1.0

"""dialogs.exec_approval - extracted from the former monolithic dialogs.py."""

import wx


class ExecApprovalDialog(wx.Dialog):
    """Approval prompt for an `exec` tool call. Shown when the kin's
    `tool_trust` level is "untrusted", or when the command matches a
    denylist pattern even for a "trusted" kin.

    Three outcomes via the buttons:
      - Allow once: run this one time, don't remember.
      - Allow + remember: run, AND add this exact command string to the
        kin's exec allowlist so the same command doesn't re-prompt.
      - Deny: refuse, return "[denied by user]" as the tool result.

    Default button is Deny — if the user mashes Enter without thinking,
    the safe outcome wins. Tab order: section header (read-only TextCtrl
    explaining what to look at) → Command field (read-only, copyable,
    no-wrap so PowerShell flags don't fold awkwardly) → Reason field
    (read-only, wrap on) → the three buttons in declared order.

    Invoked from a wxPython worker thread via the Hearthkin frame's
    `_request_exec_approval` helper, which marshals to the main thread
    with `wx.CallAfter` and blocks the worker on a `threading.Event`
    until the user picks. Don't instantiate this directly; use that
    helper so shutdown handling works."""

    DECISION_ALLOW = "allow"
    DECISION_REMEMBER = "remember"
    DECISION_DENY = "deny"

    def __init__(self, parent, kin_name, command, reason):
        super().__init__(
            parent,
            title=f"Tool call from {kin_name}",
            size=(640, 440),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.decision = self.DECISION_DENY  # default if dialog gets dismissed

        # Section header — read-only TextCtrl, tab-reachable, NVDA-friendly.
        # The header explains what the user is looking at BEFORE they
        # tab into the command field; without it the screen reader would
        # announce a multi-line command verbatim with no context.
        header = wx.TextCtrl(
            self,
            value=(
                f"{kin_name} wants to run a shell command. Review the "
                f"command below and decide. Allow once runs it this "
                f"time only. Allow plus remember adds this exact command "
                f"to {kin_name}'s allowlist so the same command does not "
                f"re-prompt. Deny refuses and the kin sees a denial "
                f"message as the result."
            ),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        header.SetName("Approval prompt explainer")
        header.SetMinSize((-1, 90))

        cmd_label = wx.StaticText(self, label="&Command:")
        cmd_field = wx.TextCtrl(
            self,
            value=command,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_DONTWRAP,
        )
        cmd_field.SetMinSize((-1, 80))

        reason_label = wx.StaticText(self, label="Re&ason:")
        reason_field = wx.TextCtrl(
            self,
            value=reason,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        reason_field.SetMinSize((-1, 60))

        allow_btn = wx.Button(self, label="A&llow once")
        remember_btn = wx.Button(self, label="Allow + &remember")
        deny_btn = wx.Button(self, label="&Deny")
        deny_btn.SetDefault()

        allow_btn.Bind(
            wx.EVT_BUTTON, lambda _e: self._finish(self.DECISION_ALLOW)
        )
        remember_btn.Bind(
            wx.EVT_BUTTON, lambda _e: self._finish(self.DECISION_REMEMBER)
        )
        deny_btn.Bind(
            wx.EVT_BUTTON, lambda _e: self._finish(self.DECISION_DENY)
        )

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(header, flag=wx.EXPAND | wx.ALL, border=8)
        sizer.Add(cmd_label, flag=wx.LEFT | wx.RIGHT, border=8)
        sizer.Add(
            cmd_field,
            proportion=1,
            flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
            border=8,
        )
        sizer.Add(reason_label, flag=wx.LEFT | wx.RIGHT, border=8)
        sizer.Add(
            reason_field,
            flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
            border=8,
        )
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.AddStretchSpacer(1)
        btn_row.Add(allow_btn, flag=wx.LEFT, border=4)
        btn_row.Add(remember_btn, flag=wx.LEFT, border=4)
        btn_row.Add(deny_btn, flag=wx.LEFT, border=4)
        sizer.Add(btn_row, flag=wx.EXPAND | wx.ALL, border=8)
        self.SetSizer(sizer)

    def _finish(self, decision):
        self.decision = decision
        self.EndModal(wx.ID_OK)


