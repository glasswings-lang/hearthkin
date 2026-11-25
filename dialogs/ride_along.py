# SPDX-License-Identifier: CC0-1.0
"""dialogs.ride_along — which files ride along with every message (24 Nov 2026).

The person's design, agreed 23 Nov 2026: every text file in the kin's folder
as one flat list of paths (NVDA-friendly, not a tree), each with its size in
words; the pieces that already ride (practice.md, soul, identity, voice
anchor, memory index) shown ticked; unticking the soul or the memory index
only after a warning; a running total, so the cost of every message is in
view before anything is added. The kin asks; the person ticks.

Saves two lists in the kin's config: `ride_along` (extra files, paths
relative to the kin's folder) and `ride_along_off` (built-in pieces switched
off). kin_persistence.build_system_prompt reads both, so every surface and
distillation get the same.

Keyboard: a box to narrow the list by typing (a big kin has hundreds of
files), the list itself (Space ticks), the total as a read-only box you can
Tab to, and Save / Cancel.
"""

import wx

from kin_persistence import (RIDE_ALONG_BUILTINS, agent_dir,
                             load_agent_config, ride_along_candidates,
                             _read_text_tolerant)

WARN_OFF = {"soul.md", "memory.md"}


def _words(path):
    try:
        return len(_read_text_tolerant(path).split()) if path.is_file() else 0
    except Exception:
        return 0


def build_rows(kin):
    """[(key, label, words, ticked, is_builtin)] for the list: built-ins
    first, then every other text file. Pure, so it can be tested."""
    cfg = load_agent_config(kin) or {}
    off = set(cfg.get("ride_along_off") or [])
    on = set(str(p) for p in (cfg.get("ride_along") or []))
    d = agent_dir(kin)
    rows = []
    builtin_files = set()
    for key, fname in RIDE_ALONG_BUILTINS:
        builtin_files.add(fname.lower())
        w = _words(d / fname)
        rows.append((key, f"{key} (rides by default), {w:,} words", w,
                     key not in off, True))
    for rel, w in ride_along_candidates(kin):
        if rel.lower() in builtin_files:
            continue
        rows.append((rel, f"{rel}, {w:,} words", w, rel in on, False))
    return rows


def total_line(rows, ticked_keys):
    words = sum(r[2] for r in rows if r[0] in ticked_keys)
    return (f"Riding along with every message now: about {words:,} words "
            f"(roughly {int(words * 1.35):,} tokens), on top of the base "
            f"prompt and the conversation. Every word here is sent with every "
            f"message, and changing a ticked file makes the next message a "
            f"little slower to start.")


class RideAlongDialog(wx.Dialog):
    def __init__(self, parent, kin, save_param):
        super().__init__(parent, title=f"What rides along with {kin}'s messages",
                         size=(720, 560))
        self.kin = kin
        self._save_param = save_param
        self.rows = build_rows(kin)
        self.ticked = {r[0] for r in self.rows if r[3]}
        self._shown = []

        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)
        intro = wx.TextCtrl(
            panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
            | wx.TE_NO_VSCROLL,
            value=(f"Ticked files are sent to {kin} with every message, and "
                   f"to its memory notes. The first five ride by default. "
                   f"When {kin} asks for a file to be kept in front of it, "
                   f"tick it here."))
        intro.SetMinSize((-1, 50))
        intro.SetName("What this list does")
        s.Add(intro, flag=wx.EXPAND | wx.ALL, border=8)

        s.Add(wx.StaticText(panel, label="&Show only files containing:"),
              flag=wx.LEFT | wx.RIGHT, border=8)
        self.filter = wx.TextCtrl(panel)
        self.filter.Bind(wx.EVT_TEXT, lambda e: self._fill())
        s.Add(self.filter, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        s.Add(wx.StaticText(panel, label="&Files (Space ticks or unticks):"),
              flag=wx.LEFT | wx.RIGHT, border=8)
        # TickList, never wx.CheckListBox: NVDA hears whether each file is
        # ticked (tests/test_no_checklistbox.py).
        from dialogs._shared import TickList
        self.lst = TickList(panel, "Files")
        self.lst.Bind(wx.EVT_CHECKLISTBOX, self._on_tick)
        s.Add(self.lst, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
              border=8)

        self.total = wx.TextCtrl(
            panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
            | wx.TE_NO_VSCROLL)
        self.total.SetMinSize((-1, 50))
        self.total.SetName("Total riding along")
        s.Add(self.total, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        ok = wx.Button(panel, wx.ID_OK, label="&Save")
        ok.Bind(wx.EVT_BUTTON, self._on_save)
        btns.Add(ok, flag=wx.RIGHT, border=6)
        btns.Add(wx.Button(panel, wx.ID_CANCEL, label="&Cancel"))
        s.Add(btns, flag=wx.ALIGN_RIGHT | wx.ALL, border=8)
        panel.SetSizer(s)
        self._fill()
        self.lst.SetFocus()

    def _fill(self):
        want = self.filter.GetValue().strip().lower()
        self._shown = [r for r in self.rows if not want or want in r[0].lower()]
        self.lst.Set([r[1] for r in self._shown])
        for i, r in enumerate(self._shown):
            self.lst.Check(i, r[0] in self.ticked)
        self.total.SetValue(total_line(self.rows, self.ticked))

    def _on_tick(self, event):
        i = event.GetInt()
        if i < 0 or i >= len(self._shown):
            return
        key = self._shown[i][0]
        if self.lst.IsChecked(i):
            self.ticked.add(key)
        else:
            if key in WARN_OFF and self._shown[i][4]:
                what = ("its soul, who it is" if key == "soul.md"
                        else "its memory index, what it remembers")
                if wx.MessageBox(
                        f"Stop sending {self.kin} {what}, with every message? "
                        f"It would not know this until you tick it again.",
                        "Untick this?", wx.YES_NO | wx.NO_DEFAULT
                        | wx.ICON_WARNING, self) != wx.YES:
                    self.lst.Check(i, True)
                    return
            self.ticked.discard(key)
        self.total.SetValue(total_line(self.rows, self.ticked))

    def _on_save(self, event):
        builtin_keys = [r[0] for r in self.rows if r[4]]
        self._save_param("ride_along_off",
                         [k for k in builtin_keys if k not in self.ticked])
        self._save_param("ride_along",
                         [r[0] for r in self.rows
                          if not r[4] and r[0] in self.ticked])
        self.EndModal(wx.ID_OK)
