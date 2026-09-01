# SPDX-License-Identifier: CC0-1.0

"""dialogs.prompt_updates — opt-in dialog for adopting improved prompt defaults.

When a Hearthkin release ships a better built-in default for one of the editable
prompts, the operator's own files are never changed automatically. This dialog
is where they decide, per prompt, what to do with the newer default:

  * Adopt   — replace their install-wide copy with the new default (their old
              copy is backed up first).
  * Stash   — write the new default into ~/.hearthkin/prompts/updates/ to read
              and compare later; nothing live changes.
  * View    — read the new default text here, alongside their current one.

Closing the dialog does nothing — keeping the current wording is always the
default action. Per-kin overrides are never touched by this dialog; it only
concerns the install-wide shared layer.
"""

import wx

from kin_persistence import (
    APP_PROMPT_REGISTRY,
    app_prompts_needing_update,
    legacy_prompt_overrides_needing_review,
    prompt_update_texts,
    load_app_prompt,
    adopt_prompt_update,
    stash_prompt_update,
)


class PromptUpdatesDialog(wx.Dialog):
    def __init__(self, parent, on_status=None):
        super().__init__(parent, title="Prompt updates", size=(720, 620))
        self._on_status = on_status or (lambda _m: None)
        panel = wx.Panel(self)

        header = wx.TextCtrl(
            panel,
            value=(
                "Newer built-in wording has shipped for the prompts below. "
                "Your own copies were left exactly as they are — nothing here "
                "changes anything until you choose to. For each one you can: "
                "Adopt it (replace your install-wide copy with the new default; "
                "your old copy is backed up first), Stash it (drop the new "
                "default into ~/.hearthkin/prompts/updates/ to read later, "
                "changing nothing live), or just read it in the preview below. "
                "This only affects the shared install-wide copies — any prompt "
                "you've customised for a specific kin is left alone."
            ),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        header.SetName("Prompt updates overview")
        header.SetMinSize((-1, 130))

        # Both labels below already existed -- but they were built inline
        # inside the body.Add() calls further down, which happens AFTER these
        # controls. wxMSW takes an accessible name from the immediately
        # preceding sibling in CREATION order, so a label created later sits
        # behind its control in z-order and names nothing. On screen they were
        # in the right place and both controls still announced as bare roles.
        # Creating them here fixes that without moving anything visually.
        listbox_label = wx.StaticText(panel, label="Prompts with a newer &default:")
        self.listbox = wx.ListBox(panel, choices=[], style=wx.LB_SINGLE)
        self.listbox.SetMinSize((-1, 140))
        self.listbox.Bind(wx.EVT_LISTBOX, lambda _e: self._refresh_preview())

        preview_label = wx.StaticText(
            panel, label="Pre&view (the new built-in default):")
        self.preview = wx.TextCtrl(
            panel, value="",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP,
        )
        self.preview.SetMinSize((-1, 200))

        adopt_btn = wx.Button(panel, label="&Adopt selected")
        stash_btn = wx.Button(panel, label="&Stash selected for later")
        adopt_all_btn = wx.Button(panel, label="Adopt a&ll")
        close_btn = wx.Button(panel, wx.ID_CLOSE, label="&Close")
        adopt_btn.Bind(wx.EVT_BUTTON, self._on_adopt)
        stash_btn.Bind(wx.EVT_BUTTON, self._on_stash)
        adopt_all_btn.Bind(wx.EVT_BUTTON, self._on_adopt_all)
        close_btn.Bind(wx.EVT_BUTTON, lambda _e: self.EndModal(wx.ID_CLOSE))

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.Add(adopt_btn, flag=wx.RIGHT, border=4)
        btn_row.Add(stash_btn, flag=wx.RIGHT, border=4)
        btn_row.Add(adopt_all_btn, flag=wx.RIGHT, border=4)
        btn_row.AddStretchSpacer()
        btn_row.Add(close_btn)

        body = wx.BoxSizer(wx.VERTICAL)
        body.Add(header, flag=wx.EXPAND | wx.BOTTOM, border=8)
        body.Add(listbox_label, flag=wx.BOTTOM, border=2)
        body.Add(self.listbox, flag=wx.EXPAND | wx.BOTTOM, border=8)
        body.Add(preview_label, flag=wx.BOTTOM, border=2)
        body.Add(self.preview, proportion=1, flag=wx.EXPAND | wx.BOTTOM, border=8)
        body.Add(btn_row, flag=wx.EXPAND)
        panel.SetSizer(body)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, proportion=1, flag=wx.EXPAND | wx.ALL, border=12)
        self.SetSizer(outer)
        self.SetEscapeId(wx.ID_CLOSE)

        self._stale = []
        self._reload()

    # ─── data ────────────────────────────────────────────────────────
    def _reload(self):
        try:
            self._stale = list(app_prompts_needing_update())
        except Exception:
            self._stale = []
        # The legacy prompts (base prompt, per-kin distillation prompt)
        # predate the registry. They were flagged at startup and then had no
        # row on this screen, which is the one place someone comes to resolve
        # exactly that message -- so the nudge named a dead end.
        try:
            self._stale += list(legacy_prompt_overrides_needing_review())
        except Exception:
            pass
        items = []
        for (_slug, seeded, shipped, title) in self._stale:
            items.append(f"{title} — you have v{seeded}, v{shipped} available")
        self.listbox.Set(items)
        if items:
            self.listbox.SetSelection(0)
        self._refresh_preview()

    def _selected_slug(self):
        idx = self.listbox.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self._stale):
            return None
        return self._stale[idx][0]

    def _refresh_preview(self):
        slug = self._selected_slug()
        if not slug:
            self.preview.SetValue("")
            return
        new_default, current = prompt_update_texts(slug)
        same = ((current or "").strip() == (new_default or "").strip())
        note = ("\n\n--- Your current install-wide version is identical to this "
                "default. ---" if same else
                "\n\n--- This differs from your current install-wide version. "
                "Adopt to switch to it. ---")
        self.preview.SetValue((new_default or "") + note)

    # ─── actions ─────────────────────────────────────────────────────
    def _on_adopt(self, _event):
        slug = self._selected_slug()
        if not slug:
            self._on_status("Select a prompt first.")
            return
        title = next((t for (s, _se, _sh, t) in self._stale if s == slug), slug)
        if adopt_prompt_update(slug):
            self._on_status(f"Adopted the new default for {title} (backed up your old copy).")
            self._reload()
        else:
            self._on_status(f"Couldn't adopt {title}.")

    def _on_stash(self, _event):
        slug = self._selected_slug()
        if not slug:
            self._on_status("Select a prompt first.")
            return
        title = next((t for (s, _se, _sh, t) in self._stale if s == slug), slug)
        path = stash_prompt_update(slug)
        if path:
            self._on_status(f"Stashed the new {title} default at {path} to review later.")
        else:
            self._on_status(f"Couldn't stash {title}.")

    def _on_adopt_all(self, _event):
        if not self._stale:
            return
        n = 0
        for (slug, _se, _sh, _t) in list(self._stale):
            if adopt_prompt_update(slug):
                n += 1
        self._on_status(f"Adopted {n} new prompt default(s); your old copies were backed up.")
        self._reload()
