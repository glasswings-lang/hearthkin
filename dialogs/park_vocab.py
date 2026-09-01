# SPDX-License-Identifier: CC0-1.0

"""dialogs.park_vocab — ParkVocabDialog: edit the park's hand-editable word
lists (actions, per-species nicknames, the 'everyone' words) from inside
Hearthkin, so an operator never has to find or open the files by hand.

Each entry is one plain-text file under ~/.hearthkin/park_words/. The dialog is
a simple master/detail: a ListBox of the word-lists on the left, the selected
list's contents in an edit box on the right.

Accessibility notes (the reason this uses a ListBox, not a ComboBox):
- A ListBox shows every option at once and is arrowed to browse; there's no
  dropdown to fight, and NVDA reads each item as you land on it.
- Picking a list loads it QUIETLY — no speech, no focus jump. Browsing the
  picker must never dump you into something else (the bug the park-play combo
  had). The edit box updates in place; you Tab to it when you're ready.
- Editing a list and then picking another auto-saves the first, so nothing is
  lost when you move between lists.
"""

import os

import wx

from audio import nvda_speak


class ParkVocabDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Edit park words",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         size=(720, 640))
        self._entries = []       # [(label, path)]
        self._loaded_idx = None  # index whose file is currently in the box
        self._dirty = False
        self._loading = False    # suppress the dirty flag during a load

        try:
            from tools import get_game
            host = get_game("tff")
            self._entries = list(host.vocab_files()) if host else []
        except Exception:
            self._entries = []

        outer = wx.BoxSizer(wx.VERTICAL)

        header = wx.TextCtrl(
            self,
            value=(
                "These are your park's word lists — the words a kin can use "
                "and what they mean. Pick a list on the left, edit it on the "
                "right (one word per line; lines starting with # are just "
                "notes), then press Save. Changes take effect on the kin's "
                "next turn. You can't break the game from here — the core "
                "actions always work no matter what you do."
            ),
            style=(wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL
                   | wx.TE_WORDWRAP),
        )
        header.SetName("Park words explainer")
        header.SetMinSize((-1, 92))
        outer.Add(header, 0, wx.EXPAND | wx.ALL, 8)

        body = wx.BoxSizer(wx.HORIZONTAL)

        left = wx.BoxSizer(wx.VERTICAL)
        left.Add(wx.StaticText(self, label="Word &lists:"), 0, wx.BOTTOM, 4)
        self.listbox = wx.ListBox(self, choices=[lbl for lbl, _ in self._entries])
        self.listbox.SetName("Word lists")
        self.listbox.Bind(wx.EVT_LISTBOX, self._on_pick)
        left.Add(self.listbox, 1, wx.EXPAND)
        body.Add(left, 1, wx.EXPAND | wx.RIGHT, 8)

        right = wx.BoxSizer(wx.VERTICAL)
        right.Add(wx.StaticText(self, label="&Words in this list (one per line):"),
                  0, wx.BOTTOM, 4)
        self.editor = wx.TextCtrl(self, style=wx.TE_MULTILINE)
        # "(one per line)" is the input format; without it in the NAME (the
        # StaticText above is never announced) there's no way to hear that
        # this field is line-separated rather than comma-separated.
        self.editor.SetName("Words in this list (one per line)")
        self.editor.Bind(wx.EVT_TEXT, self._on_edit)
        right.Add(self.editor, 1, wx.EXPAND)
        body.Add(right, 2, wx.EXPAND)

        outer.Add(body, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.save_btn = wx.Button(self, label="&Save")
        self.save_btn.Bind(wx.EVT_BUTTON, self._on_save)
        btn_row.Add(self.save_btn, 0, wx.RIGHT, 6)
        btn_row.AddStretchSpacer()
        close_btn = wx.Button(self, wx.ID_CLOSE, "&Close")
        close_btn.Bind(wx.EVT_BUTTON, self._on_close)
        btn_row.Add(close_btn, 0)
        outer.Add(btn_row, 0, wx.EXPAND | wx.ALL, 8)

        self.SetSizer(outer)
        self.SetEscapeId(wx.ID_CLOSE)
        self.Bind(wx.EVT_CLOSE, self._on_close)

        if self._entries:
            self.listbox.SetSelection(0)
            self._load(0)
            self.listbox.SetFocus()
        else:
            self.editor.SetValue(
                "The Time for Family game isn't set up, so there are no word "
                "lists to edit yet.")
            self.editor.Enable(False)
            self.save_btn.Enable(False)

    # ----- load / save ------------------------------------------------- #

    def _load(self, idx):
        if idx is None or idx < 0 or idx >= len(self._entries):
            return
        path = self._entries[idx][1]
        text = ""
        try:
            if os.path.exists(path):
                with open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
        except OSError:
            text = ""
        self._loading = True
        self.editor.SetValue(text)
        self._loading = False
        self._loaded_idx = idx
        self._dirty = False

    def _save_current(self, force=False):
        """Write the box back to the currently-loaded file. Returns True on
        success (or nothing-to-do). `force` writes even when not dirty (the Save
        button); otherwise only a dirty list is written."""
        if self._loaded_idx is None:
            return True
        if not force and not self._dirty:
            return True
        path = self._entries[self._loaded_idx][1]
        text = self.editor.GetValue()
        # Never create an empty file for a species that never had one.
        if not text.strip() and not os.path.exists(path):
            self._dirty = False
            return True
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            from kin_persistence import atomic_write_text
            atomic_write_text(path, text)
            self._dirty = False
            return True
        except Exception as e:  # noqa: BLE001 — surface, don't crash the UI
            wx.MessageBox(f"Couldn't save that list: {e}", "Save failed",
                          wx.OK | wx.ICON_ERROR, self)
            return False

    # ----- events ------------------------------------------------------ #

    def _on_edit(self, event):
        if not self._loading:
            self._dirty = True

    def _on_pick(self, event):
        idx = self.listbox.GetSelection()
        if idx == wx.NOT_FOUND or idx == self._loaded_idx:
            return
        # Save what you were editing before loading the new list. Loading is
        # quiet (no speech, no focus move), so browsing never dumps you away.
        if not self._save_current():
            if self._loaded_idx is not None:
                self.listbox.SetSelection(self._loaded_idx)
            return
        self._load(idx)

    def _on_save(self, event):
        if self._save_current(force=True):
            nvda_speak("Saved")

    def _on_close(self, event):
        self._save_current()
        if self.IsModal():
            self.EndModal(wx.ID_CLOSE)
        else:
            self.Destroy()
