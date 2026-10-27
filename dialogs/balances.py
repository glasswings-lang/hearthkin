# SPDX-License-Identifier: CC0-1.0

"""Tools → Balances: how much money is left on every service, in one place.

The checking lives in `balances.py`; this is only the window. One read-only
box, one plain line per service, so it can be read at any pace and copied to
someone who can help.

The answer arrives a second or two after the window opens, on a worker
thread. Focus starts on "Check again" and moves to the box when the lines
land: moving focus is what makes a screen reader read them, where quietly
changing the text of a box you're already in says nothing at all.
"""

import threading

import wx

import balances


class BalancesDialog(wx.Dialog):
    def __init__(self, parent, check=balances.check_all):
        super().__init__(parent, title="Balances",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._check = check
        self._busy = False

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        # Label immediately before the field: z-order is where wxMSW/NVDA
        # gets an unlabelled TextCtrl's accessible name from.
        sizer.Add(wx.StaticText(panel, label="&Balances:"),
                  flag=wx.LEFT | wx.RIGHT | wx.TOP, border=12)
        self.report_field = wx.TextCtrl(
            panel, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(560, 220))
        sizer.Add(self.report_field, proportion=1,
                  flag=wx.EXPAND | wx.ALL, border=12)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.again_btn = wx.Button(panel, wx.ID_ANY, "Check &again")
        self.copy_btn = wx.Button(panel, wx.ID_ANY, "Cop&y")
        self.close_btn = wx.Button(panel, wx.ID_CLOSE, "&Close")
        btns.Add(self.again_btn, flag=wx.RIGHT, border=8)
        btns.Add(self.copy_btn, flag=wx.RIGHT, border=8)
        btns.AddStretchSpacer()
        btns.Add(self.close_btn)
        sizer.Add(btns, flag=wx.EXPAND | wx.ALL, border=12)

        self.again_btn.Bind(wx.EVT_BUTTON, lambda _e: self.refresh())
        self.copy_btn.Bind(wx.EVT_BUTTON, self._on_copy)
        self.close_btn.Bind(wx.EVT_BUTTON, lambda _e: self.EndModal(wx.ID_CLOSE))
        self.SetEscapeId(wx.ID_CLOSE)

        panel.SetSizer(sizer)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, proportion=1, flag=wx.EXPAND)
        self.SetSizerAndFit(outer)
        # Focus is placed when the window actually appears, never at build
        # time: a window that isn't on screen must not take focus from the
        # one that is.
        self.Bind(wx.EVT_SHOW, self._on_show)
        self.refresh()

    def _on_show(self, event):
        event.Skip()
        if event.IsShown() and self._busy:
            wx.CallAfter(self.again_btn.SetFocus)

    def refresh(self):
        if self._busy:
            return
        self._busy = True
        self.again_btn.Disable()
        self.report_field.SetValue("Checking every service. This takes a moment.")
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        try:
            lines = self._check()
        except Exception as e:  # check_all never raises; a stand-in might
            lines = [f"The check failed: {e}"]
        wx.CallAfter(self._show, lines)

    def _show(self, lines):
        if not self:  # closed before the answer came back
            return
        self._busy = False
        self.again_btn.Enable()
        # Set once, never line by line: every change to a text box is an
        # event a screen reader is told about.
        self.report_field.SetValue("\n".join(lines) or "No services to check.")
        self.report_field.SetInsertionPoint(0)
        if self.IsShown():
            self.report_field.SetFocus()

    def _on_copy(self, _e):
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(self.report_field.GetValue()))
            finally:
                wx.TheClipboard.Close()
