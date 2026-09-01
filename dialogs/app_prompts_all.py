# SPDX-License-Identifier: CC0-1.0

"""dialogs.app_prompts_all — browse and edit every editable harness prompt.

The install-wide counterpart to EditKinDialog's per-kin Prompts tab. That tab
edits one kin's override; this edits the shared file every kin falls back to
(~/.hearthkin/prompts/<slug>.md), which is the layer most operators actually
want to change.

Before this existed there was no install-wide editor at all: `Tools → Edit base
prompt…` covered only base_prompt.md, and `Tools → Prompt updates…` only listed
prompts whose shipped default had changed. Anything else meant hand-editing
files. That left the detector word-lists (gesture_messages, reach_messages) with
no UI whatsoever, despite existing specifically to be edited by the operator —
against the standing rule that configuration a normal user touches must be
UI-reachable.

Accessibility notes (see CLAUDE.md):
  - wx.ListBox with the prompt titles, so native first-letter navigation works
    against what's actually displayed.
  - The description and the current text live in read-only TextCtrls, not
    StaticText, so they're reachable in tab order rather than by object nav.
  - Buttons carry &mnemonics; the visible label IS the accessible name.
"""

import wx

from kin_persistence import (
    APP_PROMPT_REGISTRY,
    load_app_prompt,
    save_app_prompt,
)


class AllAppPromptsDialog(wx.Dialog):
    """List every registered prompt; edit the install-wide copy of one."""

    def __init__(self, parent):
        super().__init__(parent, title="Edit prompts",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         size=(780, 640))
        self._slugs = sorted(
            APP_PROMPT_REGISTRY,
            key=lambda s: (APP_PROMPT_REGISTRY[s].get("title") or s).lower())

        outer = wx.BoxSizer(wx.VERTICAL)

        intro = wx.TextCtrl(
            self, value=(
                "These are the words Hearthkin puts around your kin — the "
                "notes it slips into a conversation, the nudges, the framing "
                "for a scheduled wake-up. Editing one here changes it for "
                "every kin. A kin can still have its own version: that's set "
                "in that kin's Settings, under Prompts, and it wins over "
                "anything here.\n\n"
                "Your edits are never overwritten by an update. Any "
                "{curly_brace} slots are filled in when the message is sent, "
                "so keep them where you want those values to appear."
            ),
            style=(wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP),
        )
        intro.SetName("About these prompts")
        intro.SetMinSize((-1, 96))
        outer.Add(intro, 0, wx.EXPAND | wx.ALL, 8)

        outer.Add(wx.StaticText(self, label="&Prompts:"), 0, wx.LEFT, 8)
        self.list = wx.ListBox(
            self, choices=[APP_PROMPT_REGISTRY[s].get("title") or s
                           for s in self._slugs])
        self.list.SetName("Prompts")
        outer.Add(self.list, 1, wx.EXPAND | wx.ALL, 8)

        outer.Add(wx.StaticText(self, label="&What it's for:"), 0, wx.LEFT, 8)
        self.desc = wx.TextCtrl(
            self, style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP)
        self.desc.SetName("What this prompt is for")
        self.desc.SetMinSize((-1, 90))
        outer.Add(self.desc, 0, wx.EXPAND | wx.ALL, 8)

        outer.Add(wx.StaticText(self, label="C&urrent wording:"), 0, wx.LEFT, 8)
        self.preview = wx.TextCtrl(
            self, style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP)
        self.preview.SetName("Current wording")
        self.preview.SetMinSize((-1, 140))
        outer.Add(self.preview, 1, wx.EXPAND | wx.ALL, 8)

        row = wx.BoxSizer(wx.HORIZONTAL)
        self.edit_btn = wx.Button(self, label="&Edit this prompt…")
        self.restore_btn = wx.Button(self, label="&Restore built-in wording")
        close_btn = wx.Button(self, wx.ID_CANCEL, "&Close")
        row.Add(self.edit_btn, 0, wx.RIGHT, 6)
        row.Add(self.restore_btn, 0, wx.RIGHT, 6)
        row.AddStretchSpacer(1)
        row.Add(close_btn, 0)
        outer.Add(row, 0, wx.EXPAND | wx.ALL, 8)

        self.SetSizer(outer)

        self.list.Bind(wx.EVT_LISTBOX, self._on_select)
        self.list.Bind(wx.EVT_LISTBOX_DCLICK, self._on_edit)
        self.edit_btn.Bind(wx.EVT_BUTTON, self._on_edit)
        self.restore_btn.Bind(wx.EVT_BUTTON, self._on_restore)

        if self._slugs:
            self.list.SetSelection(0)
        self._refresh()
        self.list.SetFocus()

    # ---- helpers ----

    def _selected_slug(self):
        i = self.list.GetSelection()
        if i == wx.NOT_FOUND or i >= len(self._slugs):
            return None
        return self._slugs[i]

    def _refresh(self):
        slug = self._selected_slug()
        if not slug:
            self.desc.SetValue("")
            self.preview.SetValue("")
            return
        entry = APP_PROMPT_REGISTRY.get(slug) or {}
        ph = entry.get("placeholders") or []
        desc = entry.get("desc", "")
        if ph:
            desc += "\n\nFilled in when sent: " + ", ".join(ph)
        current = load_app_prompt(slug)
        if current.strip() != (entry.get("default") or "").strip():
            desc += "\n\n(You have edited this one.)"
        self.desc.SetValue(desc)
        self.preview.SetValue(current)

    def _on_select(self, _evt):
        self._refresh()

    def _on_edit(self, _evt):
        slug = self._selected_slug()
        if not slug:
            return
        entry = APP_PROMPT_REGISTRY.get(slug) or {}
        from dialogs.app_prompt import AppPromptEditDialog
        dlg = AppPromptEditDialog(
            self, "every kin", entry.get("title", slug),
            load_app_prompt(slug), entry.get("default", ""),
            help_text=(
                "This wording applies to every kin. Restore default puts back "
                "Hearthkin's built-in text (it does not save until you press "
                "OK). Any {curly_brace} slots are filled in at send time; keep "
                "them where you want those values to appear.\n\n"
                "A single kin can override this in its own Settings → Prompts."
            ),
        )
        try:
            if dlg.ShowModal() == wx.ID_OK:
                save_app_prompt(slug, dlg.get_prompt())
                self._refresh()
        finally:
            dlg.Destroy()

    def _on_restore(self, _evt):
        slug = self._selected_slug()
        if not slug:
            return
        entry = APP_PROMPT_REGISTRY.get(slug) or {}
        title = entry.get("title", slug)
        if wx.MessageBox(
                f"Put back Hearthkin's built-in wording for \"{title}\"?\n\n"
                "Your current version is backed up first, so this can be "
                "undone from the backups folder.",
                "Restore built-in wording",
                wx.YES_NO | wx.ICON_QUESTION, self) != wx.YES:
            return
        save_app_prompt(slug, entry.get("default", ""))
        self._refresh()
