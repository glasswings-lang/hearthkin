# SPDX-License-Identifier: CC0-1.0

"""dialogs.restore_history — bring a kin's OWN archived conversation back.

The counterpart to ImportHistoryDialog, and deliberately a separate door.

Import is for history from somewhere else: it brackets what it writes in
"[hearthkin: imported ...]" markers and stamps every row
`source: import:<label>`, so the kin can tell carried-in history from turns
it took here. Restore is for the kin's own turns coming home — from an
archived kin folder, a rescued backup, a conversation cleaned up outside
the app. Nothing is announced and nothing is relabelled, because there is
nothing foreign arriving.

Running an archive through the import door would tell a kin its own past
was seed history it "may not remember writing", and would overwrite the
`source` recording where each turn actually came from. Each dialog refuses
the other's file shape and says which door to use.

See importers/hearthkin_jsonl.py and _canonical.restore_history.
"""

import os

import wx

from importers import hearthkin_jsonl
from importers._canonical import restore_rows
from kin_persistence import list_agents, load_agent_conversation


class RestoreHistoryDialog(wx.Dialog):
    """File → Restore a kin's history… — pick an archived conversation.jsonl,
    pick the kin it belongs to, choose how it combines, restore."""

    def __init__(self, parent):
        super().__init__(parent, title="Restore a kin's history", size=(620, 500))
        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)
        body = wx.BoxSizer(wx.VERTICAL)

        self._rows = None            # parsed rows from the source file(s)
        # Authoritative when non-empty; the text field then shows a count
        # rather than a path, because twenty paths don't fit in a box and
        # nobody wants them read out one after another.
        self._paths = []
        self._skipped = []           # [(path, why)] for files that wouldn't read
        self._existing_agents = list_agents()

        # ─── Source file ─────────────────────────────────────────── #
        body.Add(wx.StaticText(panel, label="&Archived conversation file(s):"),
                 flag=wx.BOTTOM, border=2)
        file_row = wx.BoxSizer(wx.HORIZONTAL)
        self.file_field = wx.TextCtrl(panel)
        self.file_field.Bind(wx.EVT_TEXT, self._on_file_typed)
        browse_btn = wx.Button(panel, label="&Browse…")
        browse_btn.Bind(wx.EVT_BUTTON, self._on_browse)
        file_row.Add(self.file_field, proportion=1, flag=wx.RIGHT, border=6)
        file_row.Add(browse_btn)
        body.Add(file_row, flag=wx.EXPAND | wx.BOTTOM, border=10)

        # ─── Which kin ───────────────────────────────────────────── #
        body.Add(wx.StaticText(panel, label="&Whose history is this?"),
                 flag=wx.BOTTOM, border=2)
        self.kin_choice = wx.Choice(
            panel, choices=self._existing_agents or ["(no kin yet)"])
        if self._existing_agents:
            self.kin_choice.SetSelection(0)
        self.kin_choice.Bind(wx.EVT_CHOICE, self._on_input_changed)
        body.Add(self.kin_choice, flag=wx.EXPAND | wx.BOTTOM, border=10)

        # ─── How it combines ─────────────────────────────────────── #
        # A StaticText would label nothing here — the next control is a
        # radio button, which uses its own label as its accessible name.
        # Read-only TextCtrl so the question itself lands in tab order.
        # Multiline is what actually puts it in the tab order. Single-line
        # read-only TextCtrls are not keyboard-focusable on wxMSW — wx refuses
        # them focus because there is nothing to scroll — so this question was
        # unreachable by Tab, which is the one thing the comment above says it
        # exists to fix.
        mode_header = wx.TextCtrl(
            panel, value="How should it combine with what's already there?",
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
            | wx.TE_NO_VSCROLL)
        mode_header.SetMinSize((-1, 44))
        body.Add(mode_header, flag=wx.EXPAND | wx.BOTTOM, border=2)
        self.mode_merge_radio = wx.RadioButton(
            panel, label="Weave it in by &time (recommended)", style=wx.RB_GROUP)
        self.mode_append_radio = wx.RadioButton(panel, label="Add it to the &end")
        self.mode_replace_radio = wx.RadioButton(
            panel, label="&Replace what's there (a backup is kept)")
        self.mode_merge_radio.SetValue(True)
        for rb in (self.mode_merge_radio, self.mode_append_radio,
                   self.mode_replace_radio):
            rb.Bind(wx.EVT_RADIOBUTTON, self._on_input_changed)
            body.Add(rb, flag=wx.BOTTOM, border=4)
        body.Add((0, 6))

        # ─── What will happen ────────────────────────────────────── #
        body.Add(wx.StaticText(panel, label="What will &happen:"),
                 flag=wx.BOTTOM, border=2)
        self.summary = wx.TextCtrl(
            panel,
            value="Pick an archived conversation file to begin.",
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
            | wx.TE_NO_VSCROLL,
            size=(-1, 110))
        body.Add(self.summary, flag=wx.EXPAND | wx.BOTTOM, border=10)

        # ─── Buttons ─────────────────────────────────────────────── #
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.restore_btn = wx.Button(panel, label="&Restore")
        self.restore_btn.Bind(wx.EVT_BUTTON, self._on_restore)
        self.restore_btn.Disable()
        cancel_btn = wx.Button(panel, wx.ID_CANCEL, label="Ca&ncel")
        btn_row.Add(self.restore_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(cancel_btn)
        body.Add(btn_row, flag=wx.ALIGN_RIGHT)

        panel.SetSizer(body)
        outer.Add(panel, proportion=1, flag=wx.EXPAND | wx.ALL, border=12)
        self.SetSizer(outer)
        self.file_field.SetFocus()

        # Populated on a successful restore; the caller reads it.
        self.result = None

    # ─── Helpers ─────────────────────────────────────────────────── #

    def _selected_kin(self):
        if not self._existing_agents:
            return None
        sel = self.kin_choice.GetSelection()
        if sel < 0:
            return None
        return self._existing_agents[sel]

    def _selected_mode(self):
        if self.mode_replace_radio.GetValue():
            return "replace"
        if self.mode_append_radio.GetValue():
            return "append"
        return "merge"

    def _say(self, text, can_restore=False):
        self.summary.SetValue(text)
        self.restore_btn.Enable(bool(can_restore))

    # ─── Event handlers ──────────────────────────────────────────── #

    def _on_browse(self, event):
        with wx.FileDialog(
            self, "Pick archived conversation files",
            wildcard=("Saved conversations (*.jsonl;*.json)|*.jsonl;*.json"
                      "|All files (*.*)|*.*"),
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            paths = dlg.GetPaths()
        self._paths = list(paths)
        if len(paths) == 1:
            self.file_field.SetValue(paths[0])
        else:
            # SetValue fires EVT_TEXT, which re-runs the dry run — that's
            # wanted, and _refresh reads _paths (set above) in preference
            # to the field, so the summary text here is never parsed.
            self.file_field.SetValue(f"{len(paths)} files selected")

    def _current_paths(self):
        """Files to restore. A multi-selection wins; otherwise whatever
        is typed in the field, so pasting a path still works."""
        if self._paths:
            return list(self._paths)
        one = self.file_field.GetValue().strip()
        return [one] if one else []

    def _on_file_typed(self, event):
        """Typing a path by hand replaces any multi-selection — otherwise
        the field would say one thing and the restore would do another."""
        if self._paths and self.file_field.GetValue().strip() not in (
                f"{len(self._paths)} files selected",):
            self._paths = []
        self._refresh()

    def _on_input_changed(self, event):
        """Re-read the file and re-run the dry run. Cheap enough to do
        synchronously — restoring reads one local .jsonl, with none of the
        tar-extraction or format-sniffing the import path has to do."""
        self._refresh()

    def _refresh(self):
        paths = self._current_paths()
        self._rows = None
        self._skipped = []
        if not paths:
            self._say("Pick an archived conversation file to begin.")
            return

        batches = []
        read_ok = 0
        for path in paths:
            if not os.path.isfile(path):
                self._skipped.append((path, "not found"))
                continue
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except OSError as e:
                self._skipped.append((path, str(e)))
                continue
            # A foreign export goes through File -> Import history instead —
            # it needs the markers and the source labelling that restore
            # deliberately doesn't apply. Say which door, don't just refuse.
            if not hearthkin_jsonl.detect(text):
                self._skipped.append((path, "not a kin's own conversation"))
                continue
            try:
                these = hearthkin_jsonl.parse(path)
            except Exception as e:  # noqa: BLE001
                self._skipped.append((path, str(e)))
                continue
            if not these:
                self._skipped.append((path, "no turns in it"))
                continue
            read_ok += 1
            stamps = [r.get("ts") for r in these if r.get("ts")]
            batches.append((min(stamps) if stamps else "", these))

        # Everything rejected, and all of it looked foreign: that's the
        # other door's work. Keep the full explanation for this case — it's
        # the one where someone needs telling WHERE to go, not just that
        # this didn't work.
        if not batches and self._skipped and all(
                why == "not a kin's own conversation"
                for _p, why in self._skipped):
            self._say(
                "That doesn't look like a kin's own conversation file.\n\n"
                "If it's history from somewhere else — Telegram, Skype, "
                "Kindroid, a text log — use File → Import history instead. "
                "That path labels it as carried-in history, which this one "
                "deliberately doesn't do.")
            return
        if not batches:
            why = self._skipped[0][1] if self._skipped else "nothing readable"
            self._say(f"Couldn't read that: {why}")
            return

        # Oldest archive first — by each file's own earliest timestamp, not
        # by filename, since "conversation (3).jsonl" says nothing about
        # when it happened. Decides the result only for "add it to the
        # end"; merge re-sorts everything by time anyway.
        batches.sort(key=lambda b: b[0])
        rows = []
        for _first, these in batches:
            rows.extend(these)
        self._rows = rows

        kin = self._selected_kin()
        if not kin:
            self._say(f"Found {len(rows)} turns. Pick which kin they "
                      f"belong to.")
            return

        mode = self._selected_mode()
        try:
            existing = load_agent_conversation(kin)
            merged, stats = restore_rows(existing, rows, mode=mode)
        except Exception as e:  # noqa: BLE001
            self._say(f"Couldn't work out what would happen: {e}")
            return

        stamps = sorted(r["ts"] for r in rows if r.get("ts"))
        span = (f" ({stamps[0][:10]} to {stamps[-1][:10]})"
                if stamps else "")
        from_files = f" from {read_ok} files" if read_ok > 1 else ""
        lines = [
            f"{stats['restored']} turns would come back to "
            f"{kin}{from_files}{span}."
        ]
        if stats["skipped_duplicates"]:
            lines.append(
                f"{stats['skipped_duplicates']} are already there and would "
                f"be left alone.")
        if stats["dropped"]:
            lines.append(f"{stats['dropped']} couldn't be read and would be skipped.")
        # Never a silent skip. A file quietly dropped from a twenty-file
        # restore is how an archive arrives incomplete with no record of
        # what is missing.
        if self._skipped:
            shown = "; ".join(f"{os.path.basename(p)} ({why})"
                              for p, why in self._skipped[:4])
            more = "; …" if len(self._skipped) > 4 else ""
            lines.append(
                f"{len(self._skipped)} file"
                f"{'s' if len(self._skipped) != 1 else ''} would be skipped: "
                f"{shown}{more}")
        if mode == "replace":
            lines.append(f"{kin}'s current {len(existing)} turns would be "
                         f"replaced. A backup is kept.")
        else:
            lines.append(f"{kin} would end up with {len(merged)} turns in all.")

        # Caution when this looks like something already restored.
        #
        # A turn with no timestamp can never be matched as a duplicate (see
        # _restore_key — guessing there deletes real data), so re-restoring
        # something that overlaps what's already here silently doubles every
        # unstamped turn while reporting a healthy-looking number "coming
        # back". That reads as a win. It isn't. Say so plainly instead of
        # leaving it to be worked out from the arithmetic.
        unstamped = sum(1 for r in rows if not r.get("ts"))
        overlapping = stats["skipped_duplicates"] > 0
        in_backups = any(
            "backups" in os.path.normpath(p).lower().split(os.sep)
            for p in paths)

        if in_backups:
            lines.append(
                "\nCAREFUL: a backups folder is involved. Backups are "
                "undo copies of a conversation that's already here — "
                "restoring one usually adds turns a second time rather than "
                "bringing anything back.")
        elif overlapping and unstamped:
            lines.append(
                f"\nCareful: some of this is already here, and {unstamped} "
                f"turns have no timestamp, so there's no way to tell whether "
                f"those are already here too. If this overlaps what "
                f"{kin} has, restoring would add them a second time.")

        self._say("\n".join(lines), can_restore=stats["restored"] > 0)

    def _on_restore(self, event):
        kin = self._selected_kin()
        if not self._rows and not self._current_paths():
            return
        if not kin:
            wx.MessageBox("Pick which kin this history belongs to.",
                          "No kin picked", wx.OK | wx.ICON_INFORMATION, self)
            return

        mode = self._selected_mode()
        if mode == "replace":
            confirm = wx.MessageBox(
                f"This replaces everything {kin} currently remembers of this "
                f"conversation with what's in the file.\n\n"
                f"A backup is written first, so it can be undone. Go ahead?",
                "Replace this kin's conversation?",
                wx.YES_NO | wx.ICON_QUESTION, self)
            if confirm != wx.YES:
                return

        self.result = {
            "paths": self._current_paths(),
            # Kept so any caller still reading a single path keeps working.
            "path": (self._current_paths() or [""])[0],
            "kin": kin,
            "mode": mode,
        }
        self.EndModal(wx.ID_OK)
