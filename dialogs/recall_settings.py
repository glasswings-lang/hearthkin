"""Per-turn memory-recall settings, in their own dialog.

Split out of the Memory tab's old inline "show advanced settings" block:
NVDA skimmed straight past the reveal checkbox, so the recall knobs were
effectively invisible to a screen-reader user. This follows the app's
standard, discoverable pattern — a button opens a dialog (same shape as
the model browser and the search-filter dialogs).

The dialog edits the same ``recall_*`` config keys and persists through
the parent EditKinDialog's ``_save_param`` callback, so every save is
byte-identical to the old inline path (load-modify-save per key on disk).
"""

import wx

from ._shared import _IntField


class RecallSettingsDialog(wx.Dialog):
    """Edit one kin's per-turn memory-recall knobs.

    `cfg` is the parent dialog's current kin config (read once, at open).
    `save_param(key, value)` is the parent's `_save_param` bound method —
    it reloads config from disk, applies the one key, and saves, so saves
    here stay coherent even though the parent may refresh its own cfg.
    """

    def __init__(self, parent, cfg, save_param, kin_name=""):
        title = "Memory recall settings"
        if kin_name:
            title = f"{title} — {kin_name}"
        super().__init__(parent, title=title,
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.cfg = cfg
        self._save_param = save_param
        self._recall_pct_values = []

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Read-only TextCtrl, not StaticText: a checkbox names itself from
        # its own label, so a StaticText here would label nothing and a
        # keyboard user would never hear what recall is or what "how
        # present" means -- this is the only place either is explained.
        blurb = wx.TextCtrl(
            panel,
            value=("Before each reply, Hearthkin can automatically surface the "
                   "most relevant slice of this kin's own depth logs — "
                   "inline, no tool call. 'How present' is the share "
                   "of the kin's context window reserved for that recalled "
                   "memory each turn — not a share of its total memory."),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL)
        blurb.SetMinSize((-1, 90))
        blurb.SetName("About per-turn memory recall")
        sizer.Add(blurb, flag=wx.EXPAND | wx.ALL, border=10)

        self.recall_enabled_check = wx.CheckBox(
            panel, label="Surface relevant memory automatically each turn")
        self.recall_enabled_check.SetValue(bool(self.cfg.get("recall_enabled", True)))
        self.recall_enabled_check.Bind(
            wx.EVT_CHECKBOX,
            lambda e: self._save_param(
                "recall_enabled", self.recall_enabled_check.GetValue()))
        sizer.Add(self.recall_enabled_check, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)

        pct_row = wx.BoxSizer(wx.HORIZONTAL)
        pct_lbl = wx.StaticText(panel, label="How &present:")
        self.recall_pct_choice = wx.Choice(panel, choices=[])
        self.recall_pct_choice.Bind(wx.EVT_CHOICE, self._on_recall_pct_choice)
        pct_row.Add(pct_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        pct_row.Add(self.recall_pct_choice, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(pct_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=10)

        items_row = wx.BoxSizer(wx.HORIZONTAL)
        items_lbl = wx.StaticText(panel, label="Max recall &items:")
        self.recall_items_field = _IntField(
            panel, value=self.cfg.get("recall_max_items", 6),
            min_val=1, max_val=50, size=(100, -1),
            name="Max recall items",
            on_commit=lambda v: self._save_param("recall_max_items", v))
        items_row.Add(items_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        items_row.Add(self.recall_items_field)
        sizer.Add(items_row, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)

        boost_lbl = wx.StaticText(
            panel, label="Always fa&vour (one word or phrase per line):")
        self.recall_boost_field = wx.TextCtrl(
            panel, style=wx.TE_MULTILINE | wx.TE_WORDWRAP)
        self.recall_boost_field.SetMinSize((-1, 64))
        # The label's "(one word or phrase per line)" is the input format;
        # the StaticText is never announced, so the SetName has to carry it.
        self.recall_boost_field.SetName("Always favour (one word or phrase per line)")
        self.recall_boost_field.SetValue(
            "\n".join(self.cfg.get("recall_boost", []) or []))
        self.recall_boost_field.Bind(wx.EVT_KILL_FOCUS, self._on_boost_kill_focus)
        sizer.Add(boost_lbl, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        sizer.Add(self.recall_boost_field, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=10)

        # Journals are the one part of a kin's memory that is almost never
        # what somebody meant, so they stay out of automatic surfacing unless
        # this is ticked. A checkbox names itself, so no buddy label needed.
        self.recall_journals_check = wx.CheckBox(
            panel, label="Also surface daily &journal entries automatically")
        self.recall_journals_check.SetValue(
            bool(self.cfg.get("recall_include_journals", False)))
        self.recall_journals_check.Bind(
            wx.EVT_CHECKBOX,
            lambda e: self._save_param(
                "recall_include_journals",
                self.recall_journals_check.GetValue()))
        sizer.Add(self.recall_journals_check,
                  flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)

        journals_help = wx.TextCtrl(
            panel,
            value=("Off by default. A dated journal entry is rarely what "
                   "someone means — one matched the word 'tend' in a sentence "
                   "about tending children and handed the kin a note about "
                   "its own memory ritual instead. Journals are also the "
                   "newest file a kin owns every day, so they outrank the "
                   "depth logs that were written to be remembered. Nothing is "
                   "hidden either way: the kin can open any journal itself, "
                   "and memory_search still finds them."),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL)
        journals_help.SetMinSize((-1, 92))
        journals_help.SetName("About journals and automatic recall")
        sizer.Add(journals_help, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=10)

        # How choosy recall is about surfacing anything at all. Every other
        # dial on this screen is a ceiling -- none of them can decline -- so
        # before this existed, recall surfaced its full quota on every turn
        # whether or not the message had anything to do with memory.
        choosy_row = wx.BoxSizer(wx.HORIZONTAL)
        choosy_lbl = wx.StaticText(panel, label="How &choosy:")
        self.recall_choosy_choice = wx.Choice(panel, choices=[])
        self.recall_choosy_choice.Bind(wx.EVT_CHOICE, self._on_choosy_choice)
        choosy_row.Add(choosy_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        choosy_row.Add(self.recall_choosy_choice, proportion=1,
                       flag=wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(choosy_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=10)

        # Read-only TextCtrl, not StaticText: the control above is a Choice,
        # which names itself from its buddy label, so a StaticText here would
        # never be announced -- and "choosy" alone doesn't say that the point
        # is for recall to be able to surface NOTHING.
        choosy_help = wx.TextCtrl(
            panel,
            value=("Most messages aren't about anything in memory, and for "
                   "those the right amount of recalled memory is none. Choosier "
                   "means a note has to share more of its words with what was "
                   "just said before it will be surfaced. If a kin keeps "
                   "answering its own notes instead of you, make it choosier; "
                   "if it never remembers anything, make it more generous."),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL)
        choosy_help.SetMinSize((-1, 92))
        choosy_help.SetName("About how choosy recall is")
        sizer.Add(choosy_help, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=10)

        fence_lbl = wx.StaticText(
            panel, label="&Never auto-surface (one path word or phrase per line):")
        self.recall_fence_field = wx.TextCtrl(
            panel, style=wx.TE_MULTILINE | wx.TE_WORDWRAP)
        self.recall_fence_field.SetMinSize((-1, 64))
        self.recall_fence_field.SetName(
            "Never auto-surface (one path word or phrase per line)")
        self.recall_fence_field.SetValue(
            "\n".join(self.cfg.get("recall_fence", []) or []))
        self.recall_fence_field.Bind(wx.EVT_KILL_FOCUS, self._on_fence_kill_focus)
        # Read-only TextCtrl: the next control is a button, which names
        # itself, so as a StaticText this would never be announced -- and
        # without it "never auto-surface" reads as "hidden from the kin".
        fence_help = wx.TextCtrl(
            panel,
            value=("Fenced logs are still reachable when the kin runs "
                   "memory_search deliberately — they're only kept out of the "
                   "automatic per-turn surfacing."),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL)
        fence_help.SetMinSize((-1, 56))
        fence_help.SetName("About fencing")
        sizer.Add(fence_lbl, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        sizer.Add(self.recall_fence_field, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=10)
        sizer.Add(fence_help, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=10)

        # Settings auto-save as you change them; Close also flushes the
        # two text fields in case focus never left them.
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        close_btn = wx.Button(panel, wx.ID_CLOSE, "&Close")
        close_btn.Bind(wx.EVT_BUTTON, self._on_close)
        btn_row.AddStretchSpacer()
        btn_row.Add(close_btn)
        sizer.Add(btn_row, flag=wx.EXPAND | wx.ALL, border=10)

        self.SetEscapeId(wx.ID_CLOSE)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self._rebuild_recall_pct_choice()
        self._rebuild_choosy_choice()

        panel.SetSizer(sizer)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, proportion=1, flag=wx.EXPAND)
        self.SetSizer(outer)
        self.SetInitialSize((560, 540))
        self.Layout()

    # Named presets over three raw numbers, deliberately. The numbers are
    # per-kin config and stay editable there for anyone who wants them; what
    # belongs on a screen is the judgement ("choosier"), not the mechanism.
    # Each preset sets all three together because they only mean anything
    # together -- a strict word-overlap with a generous size floor is not a
    # coherent position, it's two half-settings.
    _CHOOSY_PRESETS = [
        ("Generous — surface memory whenever it might relate",
         {"recall_min_overlap": 1, "recall_distinctive_frac": 0.50,
          "recall_min_block_chars": 800}),
        ("Balanced — surface memory that matches what was just said",
         {"recall_min_overlap": 2, "recall_distinctive_frac": 0.34,
          "recall_min_block_chars": 500}),
        ("Choosy — surface memory only on a clear match",
         {"recall_min_overlap": 3, "recall_distinctive_frac": 0.20,
          "recall_min_block_chars": 300}),
    ]

    def _choosy_current(self):
        return {
            "recall_min_overlap": int(self.cfg.get("recall_min_overlap", 2) or 2),
            "recall_distinctive_frac": float(
                self.cfg.get("recall_distinctive_frac", 0.34) or 0.34),
            "recall_min_block_chars": int(
                self.cfg.get("recall_min_block_chars", 500) or 500),
        }

    def _rebuild_choosy_choice(self):
        """Presets for the three relevance-gate keys, with a 'Custom' entry so
        a hand-set combination isn't silently overwritten by whichever preset
        happens to land on index 0."""
        cur = self._choosy_current()
        labels = [name for name, _ in self._CHOOSY_PRESETS]
        values = [vals for _, vals in self._CHOOSY_PRESETS]

        def _same(a, b):
            return (a["recall_min_overlap"] == b["recall_min_overlap"]
                    and a["recall_min_block_chars"] == b["recall_min_block_chars"]
                    and abs(a["recall_distinctive_frac"]
                            - b["recall_distinctive_frac"]) < 1e-6)

        if not any(_same(cur, v) for v in values):
            labels.insert(0, (
                f"Custom — {cur['recall_min_overlap']} shared words, "
                f"{round(cur['recall_distinctive_frac'] * 100)}% spread, "
                f"{cur['recall_min_block_chars']} characters"))
            values.insert(0, cur)
        self._choosy_values = values
        self.recall_choosy_choice.Set(labels)
        for i, v in enumerate(values):
            if _same(cur, v):
                self.recall_choosy_choice.SetSelection(i)
                break

    def _on_choosy_choice(self, _evt):
        i = self.recall_choosy_choice.GetSelection()
        if i < 0 or i >= len(getattr(self, "_choosy_values", [])):
            return
        for key, val in self._choosy_values[i].items():
            self.cfg[key] = val
            self._save_param(key, val)

    def _rebuild_recall_pct_choice(self):
        """Light/Medium/Rich presets for recall_budget_pct, labelled as a
        share of the *context window* (not of total memory — that read
        ambiguously) with the resulting per-turn token budget spelled out
        so the meaning is concrete. A hand-set non-preset value shows as a
        'Custom' entry so it isn't lost."""
        num_ctx = int(self.cfg.get("num_ctx", 0) or 0)

        def _label(name, pct):
            text = f"{name} — {round(pct * 100)}% of context"
            if num_ctx > 0:
                tok = int(num_ctx * pct)
                text += (f" (~{tok / 1000:.1f}k tokens)" if tok >= 1000
                         else f" (~{tok} tokens)")
            return text

        presets = [("Light", 0.10), ("Medium", 0.18), ("Rich", 0.28)]
        cur = float(self.cfg.get("recall_budget_pct", 0.18) or 0.18)
        labels = [_label(n, p) for n, p in presets]
        values = [p for _, p in presets]
        if not any(abs(cur - v) < 1e-6 for v in values):
            labels.insert(0, _label("Custom", cur))
            values.insert(0, cur)
        self._recall_pct_values = values
        self.recall_pct_choice.Set(labels)
        sel = next((i for i, v in enumerate(values) if abs(cur - v) < 1e-6), 0)
        self.recall_pct_choice.SetSelection(sel)

    def _on_recall_pct_choice(self, _event):
        idx = self.recall_pct_choice.GetSelection()
        if idx < 0 or idx >= len(self._recall_pct_values):
            return
        self._save_param("recall_budget_pct", round(self._recall_pct_values[idx], 2))

    def _commit_boost(self):
        lines = [ln.strip() for ln in self.recall_boost_field.GetValue().splitlines()
                 if ln.strip()]
        self._save_param("recall_boost", lines)

    def _commit_fence(self):
        lines = [ln.strip() for ln in self.recall_fence_field.GetValue().splitlines()
                 if ln.strip()]
        self._save_param("recall_fence", lines)

    def _on_boost_kill_focus(self, event):
        event.Skip()
        self._commit_boost()

    def _on_fence_kill_focus(self, event):
        event.Skip()
        self._commit_fence()

    def _on_close(self, _event):
        # Flush both text fields in case focus never left them (kill-focus
        # wouldn't have fired), then dismiss.
        self._commit_boost()
        self._commit_fence()
        if self.IsModal():
            self.EndModal(wx.ID_CLOSE)
        else:
            self.Destroy()
