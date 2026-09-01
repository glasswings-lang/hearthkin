# SPDX-License-Identifier: CC0-1.0

"""dialogs.search - extracted from the former monolithic dialogs.py."""

import threading

import wx

from kin_persistence import (
    list_agents, load_soul, load_memory, load_agent_conversation,
    list_rooms, load_room_conversation,
)


class _SearchScopeDialog(wx.Dialog):
    """The "where to search" checkboxes for SearchDialog, collapsed into
    a sub-dialog so the main dialog's path (query field → Search →
    results) stays short for NVDA Tab users. The caller owns the scope
    STATE dict; this dialog builds checkboxes from it and returns the
    updated dict via get_state() when ShowModal returns wx.ID_OK.
    """

    _SCOPES = [
        ("souls", "So&uls"),
        ("memories", "&Memories"),
        ("convos", "Single-kin con&versations"),
        ("rooms", "&Room conversations"),
    ]

    def __init__(self, parent, state):
        super().__init__(parent, title="Search scope",
                         style=wx.DEFAULT_DIALOG_STYLE)
        outer = wx.BoxSizer(wx.VERTICAL)

        # Read-only TextCtrl: checkboxes name themselves from their own
        # labels, so as a StaticText this is announced to nobody -- and a
        # user tabbing straight into four checked boxes has nothing telling
        # them what the boxes select or that unchecking narrows a search.
        intro = wx.TextCtrl(
            self,
            value=("Choose which files the search looks through. All four are "
                   "on by default — turn some off to narrow a noisy search."),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL)
        intro.SetMinSize((360, 56))
        intro.SetName("About search scope")
        outer.Add(intro, flag=wx.EXPAND | wx.ALL, border=10)

        self._boxes = {}
        for key, label in self._SCOPES:
            cb = wx.CheckBox(self, label=label)
            cb.SetValue(bool(state.get(key, True)))
            self._boxes[key] = cb
            outer.Add(cb, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(self, wx.ID_OK, label="&OK")
        cancel_btn = wx.Button(self, wx.ID_CANCEL, label="Ca&ncel")
        ok_btn.SetDefault()
        btn_row.AddStretchSpacer()
        btn_row.Add(ok_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(cancel_btn)
        outer.Add(btn_row, flag=wx.EXPAND | wx.ALL, border=10)

        self.SetSizer(outer)
        self.Fit()
        self.Centre()

    def get_state(self):
        """Return the scope state dict reflecting the current checkboxes."""
        return {key: bool(cb.GetValue()) for key, cb in self._boxes.items()}


class SearchDialog(wx.Dialog):
    """Plain-text search across all kin: souls, memories, conversations, and rooms.

    Tab order: query field -> Search button -> Search filters button ->
    results list -> Open / Close. Within the results list, arrow keys
    move through entries. Pressing Enter in the query field (or clicking
    Search) runs the search and jumps focus to the results list. The
    four search scopes live behind the Search filters button.
    """

    SOURCE_LABELS = {
        "soul": "soul",
        "memory": "memory",
        "convo": "conversation",
        "room": "room",
    }

    def __init__(self, parent, on_open_target):
        super().__init__(parent, title="Search across kin", size=(720, 560))
        self.on_open_target = on_open_target  # callback(target_kind, target_name)
        self._results = []  # list of dicts: {kin, source, file, snippet, full}
        # One search at a time (M-O3) — the worker reads every kin's
        # full history off-thread; the Search button is disabled while
        # one runs.
        self._search_inflight = False
        # Search-scope state. All four on by default; the _SearchScopeDialog
        # (opened via the Search filters button) edits this dict.
        self._scope_state = {
            "souls": True, "memories": True, "convos": True, "rooms": True,
        }

        panel = wx.Panel(self)

        query_lbl = wx.StaticText(panel, label="Search for:")
        self.query_field = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)
        self.query_field.Bind(wx.EVT_TEXT_ENTER, self._on_search_clicked)

        self.search_btn = wx.Button(panel, label="&Search")
        self.search_btn.Bind(wx.EVT_BUTTON, self._on_search_clicked)
        self.search_btn.SetDefault()

        query_row = wx.BoxSizer(wx.HORIZONTAL)
        query_row.Add(query_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        query_row.Add(self.query_field, proportion=1, flag=wx.RIGHT, border=6)
        query_row.Add(self.search_btn)

        # Search scope (souls / memories / conversations / rooms) lives
        # behind this button rather than as four inline checkboxes, so
        # the default path — query field, Search, results — is a short
        # Tab walk. The button label shows when the scope is narrowed.
        self.scope_btn = wx.Button(panel, label="Search &filters…")
        self.scope_btn.Bind(wx.EVT_BUTTON, self._on_open_scope)
        scope_row = wx.BoxSizer(wx.HORIZONTAL)
        scope_row.Add(self.scope_btn, flag=wx.ALIGN_CENTER_VERTICAL)

        results_lbl = wx.StaticText(panel, label="Results (Tab into the list, arrow keys to browse):")
        self.results_list = wx.ListBox(panel, style=wx.LB_SINGLE)
        self.results_list.Bind(wx.EVT_LISTBOX, self._on_result_selected)
        self.results_list.SetMinSize((-1, 180))

        snippet_lbl = wx.StaticText(panel, label="Match context:")
        self.snippet_field = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP)
        self.snippet_field.SetMinSize((-1, 130))

        open_btn = wx.Button(panel, label="&Open this in Hearthkin")
        open_btn.Bind(wx.EVT_BUTTON, self._on_open_clicked)
        close_btn = wx.Button(panel, wx.ID_CANCEL, label="C&lose")
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.AddStretchSpacer()
        btn_row.Add(open_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(close_btn)

        ps = wx.BoxSizer(wx.VERTICAL)
        ps.Add(query_row, flag=wx.EXPAND | wx.BOTTOM, border=8)
        ps.Add(scope_row, flag=wx.BOTTOM, border=8)
        ps.Add(results_lbl, flag=wx.BOTTOM, border=4)
        ps.Add(self.results_list, proportion=2, flag=wx.EXPAND | wx.BOTTOM, border=8)
        ps.Add(snippet_lbl, flag=wx.BOTTOM, border=4)
        ps.Add(self.snippet_field, proportion=1, flag=wx.EXPAND | wx.BOTTOM, border=8)
        ps.Add(btn_row, flag=wx.EXPAND)
        panel.SetSizer(ps)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, proportion=1, flag=wx.EXPAND | wx.ALL, border=12)
        self.SetSizer(outer)

        self.query_field.SetFocus()

    def _on_search_clicked(self, event):
        query = self.query_field.GetValue().strip()
        if not query or self._search_inflight:
            return
        # _run_search reads + formats every kin's full conversation
        # history — multi-MB for long-running kin — so it runs on a
        # daemon thread (M-O3) instead of freezing the UI in the
        # click handler. Results land back via wx.CallAfter.
        self._search_inflight = True
        self.search_btn.Disable()
        self.snippet_field.SetValue("Searching…")
        scope = dict(self._scope_state)

        def worker():
            try:
                results = self._run_search(
                    query,
                    search_souls=scope["souls"],
                    search_memories=scope["memories"],
                    search_convos=scope["convos"],
                    search_rooms=scope["rooms"],
                )
                err = None
            except Exception as e:
                results, err = [], str(e)
            wx.CallAfter(self._on_search_done, query, results, err)

        threading.Thread(target=worker, daemon=True).start()

    def _on_search_done(self, query, results, err):
        # Liveness guard — the dialog may have been closed mid-search.
        if not self:
            return
        self._search_inflight = False
        self.search_btn.Enable()
        if err is not None:
            self.snippet_field.SetValue(f"Search failed: {err}")
            return
        self._results = results
        labels = []
        for r in self._results:
            line = r.get("first_line", "").strip().replace("\n", " ")
            if len(line) > 80:
                line = line[:77] + "..."
            labels.append(f"{r['kin']} · {self.SOURCE_LABELS.get(r['source'], r['source'])} · {line}")
        self.results_list.Set(labels)
        if labels:
            self.results_list.SetSelection(0)
            self._render_snippet(0)
            # Jump focus to the results after a search so the user
            # arrows straight into hits instead of tabbing past the
            # Search button and the filters button to reach them.
            self.results_list.SetFocus()
        else:
            self.snippet_field.SetValue(f"No matches for {query!r}.")

    def _on_open_scope(self, _event):
        """Open the search-scope sub-dialog; apply the result on OK."""
        dlg = _SearchScopeDialog(self, self._scope_state)
        try:
            if dlg.ShowModal() == wx.ID_OK:
                new_state = dlg.get_state()
                if not any(new_state.values()):
                    # All scopes off would make every search a no-op;
                    # keep the prior state and say why.
                    wx.MessageBox(
                        "At least one search scope must stay selected — "
                        "otherwise the search has nothing to look through.",
                        "Search scope", wx.OK | wx.ICON_WARNING,
                    )
                else:
                    self._scope_state = new_state
                    self._update_scope_button_label()
        finally:
            dlg.Destroy()

    def _update_scope_button_label(self):
        """Repaint the filters button so its label shows when the scope
        is narrowed. NVDA reads the button label on focus, so this is
        the accessible 'is the search narrowed?' signal."""
        on = sum(1 for v in self._scope_state.values() if v)
        if on == 4:
            self.scope_btn.SetLabel("Search &filters…")
        else:
            self.scope_btn.SetLabel(f"Search &filters… ({on} of 4 scopes)")

    def _on_result_selected(self, event):
        idx = self.results_list.GetSelection()
        if idx >= 0:
            self._render_snippet(idx)

    def _render_snippet(self, idx):
        if idx < 0 or idx >= len(self._results):
            self.snippet_field.SetValue("")
            return
        r = self._results[idx]
        self.snippet_field.SetValue(r.get("snippet", ""))

    def _on_open_clicked(self, event):
        idx = self.results_list.GetSelection()
        if idx < 0 or idx >= len(self._results):
            return
        r = self._results[idx]
        kind = "room" if r["source"] == "room" else "kin"
        self.on_open_target(kind, r["kin"])

    @staticmethod
    def _make_snippet(text, query, context_chars=200):
        """Find query in text (case-insensitive) and return the surrounding context."""
        idx = text.lower().find(query.lower())
        if idx < 0:
            return text[:context_chars * 2]
        start = max(0, idx - context_chars)
        end = min(len(text), idx + len(query) + context_chars)
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(text) else ""
        return prefix + text[start:end] + suffix

    @staticmethod
    def _first_match_line(text, query):
        for line in text.splitlines():
            if query.lower() in line.lower():
                return line
        return text.splitlines()[0] if text.splitlines() else ""

    def _run_search(self, query, search_souls, search_memories, search_convos, search_rooms):
        out = []
        q_lower = query.lower()

        def consider(kin, source, full_text):
            if q_lower in (full_text or "").lower():
                out.append({
                    "kin": kin,
                    "source": source,
                    "first_line": self._first_match_line(full_text, query),
                    "snippet": self._make_snippet(full_text, query),
                })

        for name in list_agents():
            if search_souls:
                consider(name, "soul", load_soul(name))
            if search_memories:
                consider(name, "memory", load_memory(name))
            if search_convos:
                convo = load_agent_conversation(name)
                if convo:
                    text = self._format_convo(convo, name)
                    consider(name, "convo", text)

        if search_rooms:
            for room_name in list_rooms():
                convo = load_room_conversation(room_name)
                if convo:
                    text = self._format_convo(convo, room_name)
                    consider(room_name, "room", text)

        return out

    @staticmethod
    def _format_convo(convo, kin_or_room):
        lines = []
        for m in convo:
            role = m.get("role", "")
            speaker = m.get("speaker") or ("You" if role == "user" else (kin_or_room or "Model"))
            content = m.get("content", "")
            ts = m.get("ts", "")
            tstr = f" ({ts})" if ts else ""
            lines.append(f"{speaker}{tstr}: {content}")
        return "\n\n".join(lines)


