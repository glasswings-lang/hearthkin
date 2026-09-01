# SPDX-License-Identifier: CC0-1.0

"""dialogs.ollama_machines — manage the named-Ollama-machine registry.

The registry is a hand-editable Markdown file (~/.hearthkin/ollama_hosts.md)
of `Name = URL` lines (kin_persistence.OLLAMA_HOSTS_FILE). A kin stores a
machine NAME in its config (`ollama_host_name`); dispatch resolves it to a
URL at call time, so changing a machine's address here updates every kin
that points at it. "This machine" (localhost) is always implicit and is
never listed.

This dialog is the accessible UI for that file so a non-coder doesn't have
to edit Markdown by hand. List of machines + Add / Edit / Remove, plus a
small entry sub-dialog with a Test button that probes the URL.
"""

import threading

import wx

from kin_persistence import load_ollama_hosts, save_ollama_hosts


class _MachineEntryDialog(wx.Dialog):
    """Add / edit one machine: a display name + an Ollama base URL, with
    a Test button that confirms the daemon answers at that URL.

    Returns (name, url) via get_values() when ShowModal() == wx.ID_OK.
    """

    def __init__(self, parent, name="", url=""):
        super().__init__(parent, title="Ollama machine",
                         style=wx.DEFAULT_DIALOG_STYLE)
        outer = wx.BoxSizer(wx.VERTICAL)

        name_lbl = wx.StaticText(self, label="&Name (your label for this machine):")
        self.name_field = wx.TextCtrl(self, value=name)
        outer.Add(name_lbl, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        outer.Add(self.name_field, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        # The next control takes its name from the Address label below, so as a
        # StaticText this hint reaches nobody. Read-only TextCtrl is tab-reachable.
        name_hint = wx.TextCtrl(
            self,
            value="e.g. \"Desk machine\" or \"Spare laptop\". This is just "
                  "what you'll see in the per-kin machine picker.",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        name_hint.SetName("Name hint")
        name_hint.SetMinSize((-1, 48))
        outer.Add(name_hint, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        url_lbl = wx.StaticText(self, label="&Address (Ollama URL):")
        self.url_field = wx.TextCtrl(
            self, value=url or "http://", style=wx.TE_PROCESS_ENTER)
        self.url_field.Bind(wx.EVT_TEXT_ENTER, self._on_test)
        outer.Add(url_lbl, flag=wx.LEFT | wx.RIGHT, border=8)
        outer.Add(self.url_field, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=8)
        # The next control is a button, which uses its own label as its name, so
        # as a StaticText this reaches nobody — and it is the only guidance on
        # what to type in the address field. Read-only TextCtrl is tab-reachable.
        url_hint = wx.TextCtrl(
            self,
            value="e.g. http://macmini.local:11434 or http://192.168.1.50:11434. "
                  "Ollama's default port is 11434. Use the machine's name on "
                  "your network (often ends in .local) or its IP address.",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        url_hint.SetName("Address hint")
        url_hint.SetMinSize((-1, 64))
        outer.Add(url_hint, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        test_row = wx.BoxSizer(wx.HORIZONTAL)
        self.test_btn = wx.Button(self, label="&Test connection")
        self.test_btn.Bind(wx.EVT_BUTTON, self._on_test)
        test_row.Add(self.test_btn, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=8)
        self.test_result = wx.TextCtrl(
            self, value="", style=wx.TE_READONLY)
        self.test_result.SetName("Test result")
        test_row.Add(self.test_result, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL)
        outer.Add(test_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

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
        (self.name_field if not name else self.url_field).SetFocus()

    def _on_test(self, _event):
        url = self.url_field.GetValue().strip().rstrip("/")
        if not url or url == "http:/" or url == "http://":
            self.test_result.SetValue("Enter an address first.")
            return
        self.test_result.SetValue("Testing…")
        self.test_btn.Disable()

        def worker():
            msg = self._probe(url)
            wx.CallAfter(self._on_test_done, msg)

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _probe(url):
        try:
            import ollama
        except Exception:
            return "ollama library not installed."
        try:
            result = ollama.Client(host=url).list()
            models = result.get("models", []) if isinstance(result, dict) else getattr(result, "models", [])
            n = len(models or [])
            return f"OK — reachable, {n} model{'s' if n != 1 else ''} available."
        except Exception as e:
            return f"Could not reach it: {e}"

    def _on_test_done(self, msg):
        if not self:
            return
        self.test_result.SetValue(msg)
        self.test_btn.Enable()

    def get_values(self):
        """Return (name, url). URL is trimmed; name is trimmed."""
        return (self.name_field.GetValue().strip(),
                self.url_field.GetValue().strip().rstrip("/"))


class OllamaMachinesDialog(wx.Dialog):
    """Manage the named-Ollama-machine registry. Loads the current list,
    lets the user Add / Edit / Remove, and writes it back on Save.

    Self-contained — reads and writes OLLAMA_HOSTS_FILE via
    kin_persistence. The caller just opens it and, on ID_OK, refreshes
    any machine pickers it owns.
    """

    def __init__(self, parent, seed_url=""):
        super().__init__(parent, title="Ollama machines",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        # entries: list of [name, url]
        self._entries = [list(pair) for pair in load_ollama_hosts()]
        self._seed_url = (seed_url or "").strip().rstrip("/")

        outer = wx.BoxSizer(wx.VERTICAL)

        # The list below has its own name, so as a StaticText this reaches
        # nobody — including the fact that "This machine" exists but is absent
        # from the list, which otherwise reads as a missing entry.
        # Read-only TextCtrl is tab-reachable. (Single & here: StaticText
        # escapes mnemonics, a TextCtrl value renders literally.)
        intro = wx.TextCtrl(
            self,
            value="Machines you can point individual kin at. \"This machine\" "
                  "(your local Ollama) is always available and isn't listed "
                  "here. Add a machine, then pick it in a kin's "
                  "Model & generation tab.",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        intro.SetName("About Ollama machines")
        intro.SetMinSize((-1, 64))
        outer.Add(intro, flag=wx.EXPAND | wx.ALL, border=8)

        from ._shared import rebuild_listbox  # noqa: F401  (used in _refresh)
        self._rebuild_listbox = rebuild_listbox
        # A StaticText immediately before the list is the only thing wxMSW
        # takes an accessible name from. SetName() was doing nothing here and
        # the list announced as a bare "list box"; the intro above it is a
        # read-only TextCtrl, which cannot name anything.
        machines_label = wx.StaticText(self, label="&Machines:")
        self.machines_list = wx.ListBox(self, style=wx.LB_SINGLE)
        self.machines_list.SetMinSize((420, 160))
        self.machines_list.Bind(wx.EVT_LISTBOX_DCLICK, self._on_edit)
        outer.Add(machines_label, flag=wx.LEFT | wx.RIGHT, border=8)
        outer.Add(self.machines_list,
                  proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=8)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        add_btn = wx.Button(self, label="&Add…")
        add_btn.Bind(wx.EVT_BUTTON, self._on_add)
        edit_btn = wx.Button(self, label="&Edit…")
        edit_btn.Bind(wx.EVT_BUTTON, self._on_edit)
        remove_btn = wx.Button(self, label="&Remove")
        remove_btn.Bind(wx.EVT_BUTTON, self._on_remove)
        btn_row.Add(add_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(edit_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(remove_btn)
        outer.Add(btn_row, flag=wx.ALL, border=8)

        close_row = wx.BoxSizer(wx.HORIZONTAL)
        close_row.AddStretchSpacer(1)
        save_btn = wx.Button(self, wx.ID_OK, label="&Save && close")
        cancel_btn = wx.Button(self, wx.ID_CANCEL, label="Ca&ncel")
        save_btn.SetDefault()
        close_row.Add(save_btn, flag=wx.RIGHT, border=6)
        close_row.Add(cancel_btn)
        outer.Add(close_row, flag=wx.EXPAND | wx.ALL, border=8)

        self.SetSizer(outer)
        self.Fit()
        self._refresh()

    def _refresh(self, saved_index=None):
        if saved_index is None:
            saved_index = self.machines_list.GetSelection()
        labels = [f"{name} = {url}" for name, url in self._entries]
        self._rebuild_listbox(self.machines_list, labels, saved_index=saved_index)

    def _on_add(self, _event):
        # Offer the app-level Ollama host as a starting URL so "add the
        # machine I already configured globally" is one keystroke.
        dlg = _MachineEntryDialog(self, name="", url=self._seed_url)
        try:
            if dlg.ShowModal() == wx.ID_OK:
                name, url = dlg.get_values()
                if name and url:
                    self._entries = [e for e in self._entries if e[0] != name]
                    self._entries.append([name, url])
                    self._refresh(saved_index=len(self._entries) - 1)
        finally:
            dlg.Destroy()

    def _on_edit(self, _event):
        idx = self.machines_list.GetSelection()
        if idx < 0 or idx >= len(self._entries):
            return
        old_name, old_url = self._entries[idx]
        dlg = _MachineEntryDialog(self, name=old_name, url=old_url)
        try:
            if dlg.ShowModal() == wx.ID_OK:
                name, url = dlg.get_values()
                if name and url:
                    self._entries[idx] = [name, url]
                    self._refresh(saved_index=idx)
        finally:
            dlg.Destroy()

    def _on_remove(self, _event):
        idx = self.machines_list.GetSelection()
        if idx < 0 or idx >= len(self._entries):
            return
        name = self._entries[idx][0]
        if wx.MessageBox(
                f"Remove \"{name}\" from the machine list? This only drops it "
                f"from the pickers — kin already pointed at it keep using its "
                f"address and keep working.",
                "Remove machine", wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION, self) != wx.YES:
            return
        del self._entries[idx]
        self._refresh(saved_index=idx)

    def get_entries(self):
        return [tuple(e) for e in self._entries]

    def commit(self):
        """Persist the current list. Returns True on success."""
        return save_ollama_hosts(self._entries)
