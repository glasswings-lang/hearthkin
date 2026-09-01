# SPDX-License-Identifier: CC0-1.0

"""dialogs.agent_name - extracted from the former monolithic dialogs.py."""

import wx

from kin_persistence import AGENTS_DIR, agent_dir


class AgentNameDialog(wx.Dialog):
    def __init__(self, parent, title="New kin", initial_name="", prompt="Kin name:",
                 show_skip_option=False):
        # Taller when the skip option is shown so the extra fields fit cleanly.
        size = (440, 280) if show_skip_option else (380, 170)
        super().__init__(parent, title=title, size=size)
        self._show_skip_option = show_skip_option
        panel = wx.Panel(self)

        lbl = wx.StaticText(panel, label=prompt)
        self.name_field = wx.TextCtrl(panel, value=initial_name)
        self.name_field.Bind(wx.EVT_KEY_DOWN, self._on_key)

        ps = wx.BoxSizer(wx.VERTICAL)
        ps.Add(lbl, flag=wx.BOTTOM, border=4)
        ps.Add(self.name_field, flag=wx.EXPAND | wx.BOTTOM, border=10)

        if show_skip_option:
            self.skip_checkbox = wx.CheckBox(
                panel,
                label="&Skip identity setup — talk first, let it emerge",
            )
            self.skip_checkbox.SetValue(False)
            ps.Add(self.skip_checkbox, flag=wx.BOTTOM, border=10)

            path_lbl = wx.StaticText(panel, label="Will be saved to:")
            self.path_display = wx.TextCtrl(
                panel,
                value=self._compute_path_display(initial_name),
                style=wx.TE_READONLY,
            )
            ps.Add(path_lbl, flag=wx.BOTTOM, border=2)
            ps.Add(self.path_display, flag=wx.EXPAND | wx.BOTTOM, border=10)
            self.name_field.Bind(wx.EVT_TEXT, self._on_name_changed)
        else:
            self.skip_checkbox = None
            self.path_display = None

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(panel, wx.ID_OK, label="&OK")
        cancel_btn = wx.Button(panel, wx.ID_CANCEL, label="Ca&ncel")
        ok_btn.SetDefault()
        btn_row.Add(ok_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(cancel_btn)
        ps.Add(btn_row)
        panel.SetSizer(ps)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, proportion=1, flag=wx.EXPAND | wx.ALL, border=12)
        self.SetSizer(outer)
        self.name_field.SetFocus()
        if initial_name:
            self.name_field.SelectAll()

    @staticmethod
    def _compute_path_display(name):
        # Show where the kin's files will live. Empty name → just the agents
        # directory so the user can see the parent regardless.
        candidate = name.strip()
        if not candidate:
            return str(AGENTS_DIR)
        return str(agent_dir(candidate))

    def _on_name_changed(self, event):
        if self.path_display is not None:
            self.path_display.SetValue(self._compute_path_display(self.name_field.GetValue()))
        event.Skip()

    def _on_key(self, event):
        if event.GetKeyCode() == wx.WXK_RETURN:
            self.EndModal(wx.ID_OK)
        else:
            event.Skip()

    def get_name(self):
        return self.name_field.GetValue().strip()

    def get_skip_identity_setup(self):
        return bool(self.skip_checkbox and self.skip_checkbox.GetValue())


