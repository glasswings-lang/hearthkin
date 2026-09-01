# SPDX-License-Identifier: CC0-1.0

"""dialogs.base_prompt — view + edit the shared base prompt that prepends
every kin's system prompt on every send.

The base prompt lives at `~/.hearthkin/base_prompt.md` and is loaded by
`kin_persistence.load_base_prompt()` (which seeds the file from
`DEFAULT_BASE_PROMPT` if missing). Editing it here affects every kin —
the file gets re-read on every send via `build_system_prompt`, so
saves take effect immediately.

Accessible per project convention: multi-line TextCtrl for the editor,
focusable read-only TextCtrl for the live char/token counter, &Letter
mnemonics on every button, Esc closes via Cancel."""

import wx

from kin_persistence import (
    BASE_PROMPT_FILE, DEFAULT_BASE_PROMPT,
    load_base_prompt, atomic_write_text,
)


class BasePromptDialog(wx.Dialog):
    """Tools → Edit base prompt… — view + edit the shared system prompt
    that loads ahead of every kin's soul.md on every send."""

    def __init__(self, parent):
        super().__init__(
            parent, title="Edit base prompt",
            size=(720, 620),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)
        body = wx.BoxSizer(wx.VERTICAL)

        # Section header that lives in a focusable read-only TextCtrl so
        # NVDA hits it on Tab — explains what this prompt does, where
        # it lives, and the cost shape.
        header_text = (
            "This prompt prepends every kin's soul.md on every send.\n"
            "Lives at: " + str(BASE_PROMPT_FILE) + "\n"
            "Cost shape: every character here is sent on every turn for "
            "every kin (cached on cache-supporting providers when "
            "prompt caching is on, but cache misses still re-bill it). "
            "Worth a periodic look.\n"
            "Other harness prompts (the tool-use nudge, the roleplay "
            "corrector, the cron wake-up framing, the rolling-window "
            "marker) are editable too — plain-text files in "
            "~/.hearthkin/prompts/. Edit them in any text editor; each is "
            "backed up to prompts/backups/ before an overwrite."
        )
        self.header_field = wx.TextCtrl(
            panel,
            value=header_text,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP,
            size=(-1, 90),
        )
        # First control in the dialog — nothing precedes it to name it.
        self.header_field.SetName("What the base prompt is, and what it costs")
        body.Add(self.header_field, flag=wx.EXPAND | wx.BOTTOM, border=10)

        # The editor itself.
        body.Add(
            wx.StaticText(panel, label="Base &prompt content:"),
            flag=wx.BOTTOM, border=2,
        )
        self.editor = wx.TextCtrl(
            panel,
            style=wx.TE_MULTILINE | wx.TE_DONTWRAP,
        )
        # Bind size growth via proportion so the editor takes the rest
        # of the dialog.
        body.Add(self.editor, proportion=1,
                 flag=wx.EXPAND | wx.BOTTOM, border=10)
        self.editor.Bind(wx.EVT_TEXT, self._on_text_changed)

        # Live char + token count, focusable so NVDA can read it.
        body.Add(
            wx.StaticText(panel, label="&Size:"),
            flag=wx.BOTTOM, border=2,
        )
        self.size_field = wx.TextCtrl(
            panel,
            value="(loading…)",
            style=wx.TE_READONLY,
        )
        body.Add(self.size_field, flag=wx.EXPAND | wx.BOTTOM, border=10)

        # Status / action feedback.
        self.status_field = wx.TextCtrl(
            panel,
            value="",
            style=wx.TE_READONLY,
        )
        # The size field above is a TextCtrl, not a label, so it can't name
        # this one — unnamed edit field without it.
        self.status_field.SetName("Status")
        body.Add(self.status_field, flag=wx.EXPAND | wx.BOTTOM, border=10)

        # Buttons.
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.save_btn = wx.Button(panel, label="&Save")
        self.save_btn.Bind(wx.EVT_BUTTON, self._on_save)
        self.save_btn.Disable()  # enabled when the text changes
        self.reset_btn = wx.Button(panel, label="&Reset to default…")
        self.reset_btn.Bind(wx.EVT_BUTTON, self._on_reset)
        cancel_btn = wx.Button(panel, wx.ID_CANCEL, label="&Close")
        btn_row.Add(self.save_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(self.reset_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(cancel_btn)
        body.Add(btn_row, flag=wx.ALIGN_RIGHT)

        panel.SetSizer(body)
        outer.Add(panel, proportion=1, flag=wx.EXPAND | wx.ALL, border=12)
        self.SetSizer(outer)
        # Esc closes via wx.ID_CANCEL by default.
        self.SetEscapeId(wx.ID_CANCEL)

        self._original_text = ""
        self._load_current()
        self.editor.SetFocus()

    # ─── Event handlers ──────────────────────────────────────────── #

    def _load_current(self):
        try:
            text = load_base_prompt()
        except Exception as e:
            text = ""
            self.status_field.SetValue(f"Couldn't load: {e}")
        self._original_text = text
        self.editor.SetValue(text)
        self._refresh_size_display()
        self.save_btn.Disable()
        # Non-destructive drift notice: if a newer built-in default has
        # shipped for any editable prompt the operator already seeded, say
        # so here (this is the prompt-editing surface). Their files are
        # never touched — this is informational only.
        try:
            from kin_persistence import (
                app_prompts_needing_update,
                legacy_prompt_overrides_needing_review,
            )
            stale = (app_prompts_needing_update()
                     + legacy_prompt_overrides_needing_review())
            if stale:
                names = ", ".join(title for (_s, _h, _sh, title) in stale)
                self.status_field.SetValue(
                    "Heads-up: a newer built-in default shipped for: "
                    + names + ". Your edited files in ~/.hearthkin/prompts/ "
                    "are kept as-is; delete a prompt's .md to re-seed its "
                    "new default (back it up first if you want your wording)."
                )
        except Exception:
            pass

    def _on_text_changed(self, event):
        self._refresh_size_display()
        # Enable Save only when the buffer differs from what's on disk
        # (avoids tempting the user to save unchanged content).
        current = self.editor.GetValue()
        self.save_btn.Enable(current != self._original_text)

    def _refresh_size_display(self):
        text = self.editor.GetValue()
        chars = len(text)
        # Same ~4-chars-per-token estimate Hearthkin uses elsewhere in
        # context bookkeeping (see _est_tokens in llm_backend). Real
        # token count varies by provider/tokenizer; this is honest as
        # a rough cost dial, not as a precise figure.
        tokens_est = chars // 4
        diff = "" if not self._original_text else (
            f" (saved: {len(self._original_text):,} chars / "
            f"~{len(self._original_text)//4:,} tokens)"
        )
        self.size_field.SetValue(
            f"{chars:,} chars / ~{tokens_est:,} tokens estimated{diff}"
        )

    def _on_save(self, event):
        text = self.editor.GetValue()
        try:
            atomic_write_text(BASE_PROMPT_FILE, text)
        except Exception as e:
            wx.MessageBox(
                f"Couldn't save base prompt: {e}",
                "Save failed", wx.OK | wx.ICON_ERROR, self,
            )
            return
        self._original_text = text
        self.save_btn.Disable()
        self.status_field.SetValue(
            "Saved. Next send for every kin will use the new prompt."
        )
        try:
            from audio import nvda_speak
            nvda_speak("Base prompt saved.")
        except Exception:
            pass
        self._refresh_size_display()

    def _on_reset(self, event):
        confirm = wx.MessageBox(
            "Replace the current base prompt with the Hearthkin "
            "default? Your edits in this dialog will be lost. The file "
            "on disk is only overwritten if you then hit Save.",
            "Reset to default?",
            wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION, self,
        )
        if confirm != wx.YES:
            return
        self.editor.SetValue(DEFAULT_BASE_PROMPT)
        self.status_field.SetValue(
            "Editor reset to the Hearthkin default. Hit Save to write "
            "it to disk, or Close to leave your existing file alone."
        )
        # _on_text_changed fired by SetValue handles size + save-state.
