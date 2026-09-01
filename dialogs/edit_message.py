# SPDX-License-Identifier: CC0-1.0

"""EditMessageDialog — pick a past turn from the current conversation
(1-on-1 or room) and rewrite its content in place. Reached from
Chat -> Edit a message... (Ctrl+E).

Built to unblock the recurring case where a local model produces a
garbage turn (hallucinated tracebacks, format-attractor spam) that then
poisons every future turn in the same context — the user needs to
surgically fix or trim the offending content without dropping the whole
history via Clear chat."""

import wx


def _preview(text, width=72):
    """Compact one-line preview of a message for the dropdown label.
    Collapses whitespace, truncates with an ellipsis."""
    s = " ".join(str(text or "").split())
    if len(s) > width:
        s = s[: width - 1] + "…"
    return s


def _timestamp_hhmm(ts):
    """'2026-07-22T11:35:51' -> '11:35', silently returns '' on any mismatch."""
    if not ts or not isinstance(ts, str):
        return ""
    # Cheap slice — the persisted format is ISO, avoid a datetime parse.
    if "T" in ts:
        after = ts.split("T", 1)[1]
        return after[:5] if len(after) >= 5 else ""
    return ""


def _speaker_label(msg):
    speaker = msg.get("speaker")
    if speaker:
        return str(speaker)
    role = msg.get("role", "")
    if role == "user":
        return "You"
    if role == "assistant":
        return "Assistant"
    return role or "?"


class EditMessageDialog(wx.Dialog):
    """editable is a list of (original_index, message_dict) tuples in the
    order the messages appear in the underlying conversation list. The
    dialog presents them newest-first so the most recent turn is the
    default selection — that's the one being edited nearly every time."""

    def __init__(self, parent, editable):
        super().__init__(
            parent,
            title="Edit a message",
            size=(720, 520),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        # Newest first — the top item is selected by default.
        self._entries = list(reversed(editable))

        panel = wx.Panel(self)

        pick_label = wx.StaticText(panel, label="Message to &edit:")
        self.pick = wx.Choice(panel, choices=self._build_choice_labels())
        if self._entries:
            self.pick.SetSelection(0)
        self.pick.Bind(wx.EVT_CHOICE, self._on_pick_change)

        text_label = wx.StaticText(panel, label="Message &text:")
        self.text = wx.TextCtrl(
            panel,
            style=wx.TE_MULTILINE | wx.TE_RICH2,
        )
        # Load the currently-selected message into the text field.
        self._load_selected()

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(panel, wx.ID_OK, label="&OK")
        cancel_btn = wx.Button(panel, wx.ID_CANCEL, label="Ca&ncel")
        ok_btn.SetDefault()
        btn_row.AddStretchSpacer(1)
        btn_row.Add(ok_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(cancel_btn)

        ps = wx.BoxSizer(wx.VERTICAL)
        ps.Add(pick_label, flag=wx.BOTTOM, border=4)
        ps.Add(self.pick, flag=wx.EXPAND | wx.BOTTOM, border=12)
        ps.Add(text_label, flag=wx.BOTTOM, border=4)
        ps.Add(self.text, proportion=1, flag=wx.EXPAND | wx.BOTTOM, border=12)
        ps.Add(btn_row, flag=wx.EXPAND)
        panel.SetSizer(ps)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, proportion=1, flag=wx.EXPAND | wx.ALL, border=12)
        self.SetSizer(outer)

        self.pick.SetFocus()

    def _build_choice_labels(self):
        labels = []
        for _idx, msg in self._entries:
            speaker = _speaker_label(msg)
            hhmm = _timestamp_hhmm(msg.get("ts"))
            preview = _preview(msg.get("content", ""))
            if hhmm:
                labels.append(f"{speaker} · {hhmm} · {preview}")
            else:
                labels.append(f"{speaker} · {preview}")
        return labels

    def _load_selected(self):
        sel = self.pick.GetSelection()
        if sel < 0 or sel >= len(self._entries):
            self.text.SetValue("")
            return
        _idx, msg = self._entries[sel]
        self.text.SetValue(str(msg.get("content", "") or ""))

    def _on_pick_change(self, event):
        self._load_selected()
        event.Skip()

    def get_selected_index(self):
        """Return the ORIGINAL index into the underlying conversation
        list (not the reversed dropdown index), or None if nothing
        selected."""
        sel = self.pick.GetSelection()
        if sel < 0 or sel >= len(self._entries):
            return None
        idx, _msg = self._entries[sel]
        return idx

    def get_new_text(self):
        return self.text.GetValue()
