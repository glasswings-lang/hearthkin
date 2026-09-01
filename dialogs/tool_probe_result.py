# SPDX-License-Identifier: CC0-1.0

"""dialogs.tool_probe_result — the answer to "will this model use its tools?"

Ollama's `capabilities` list says whether a model's template can EXPRESS a
tool call. It cannot say whether the model will ever make one. A roleplay
finetune of a tool-trained base inherits the base's template, reports
`tools` quite truthfully, and then writes a description of using the tool
instead -- prose that reads exactly like success and does nothing.

`model_utils.probe_tool_calling` asks the real question. This dialog is
where the answer lands, because a verdict nobody is told is no better than
no verdict. The result goes in a read-only multiline box rather than a
message box so a screen reader can arrow through it line by line: when a
model FAILS, the useful part is the wording it produced instead, and that
can run to a paragraph.
"""

import wx


def format_probe_result(model_name, rec):
    """Turn a `probe_tool_calling` record into the text of the box.

    Plain sentences, no jargon: this is read aloud. The failing case
    quotes the model's own words back, because that is the evidence, and
    because seeing it makes the failure obvious in a way a verdict
    doesn't -- it looks like it worked.
    """
    ok = rec.get("ok")
    lines = ["Model: %s" % model_name, ""]
    if ok is True:
        called = ", ".join(rec.get("called") or []) or "(unnamed)"
        lines += [
            "Result: it made the tool call.",
            "",
            "Asked to use a tool, it used one. Tools called: %s." % called,
            "This model can do the tool work a kin needs -- reading files,",
            "searching memory, tending staging.",
        ]
    elif ok is False:
        lines += [
            "Result: it did NOT make the tool call.",
            "",
            "Asked outright to use a tool, it answered in words and called",
            "nothing. This is not a formatting problem that a retry will fix.",
            "It writes a description of using the tool, which reads as though",
            "it worked -- so a kin on this model looks fine and quietly stops",
            "doing anything.",
        ]
        said = (rec.get("said") or "").strip()
        if said:
            lines += ["", "What it wrote instead:", "", said]
        else:
            lines += ["", "It returned no words at all."]
    else:
        lines += [
            "Result: could not tell.",
            "",
            "The test did not complete, so this says nothing about the model",
            "either way. Usually that means Ollama was unreachable or the",
            "model is still loading. Worth trying again.",
        ]
        err = (rec.get("error") or "").strip()
        if err:
            lines += ["", "Details:", err]
    return "\n".join(lines)


class ToolProbeResultDialog(wx.Dialog):
    """Show one probe result. Read-only; nothing here changes settings."""

    def __init__(self, parent, model_name, rec):
        ok = rec.get("ok")
        verdict = {True: "passed", False: "FAILED"}.get(ok, "no result")
        super().__init__(parent, title="Tool-calling test — %s" % verdict,
                         size=(620, 460))
        panel = wx.Panel(self)

        label = wx.StaticText(panel, label="&Test result:")

        self.result_field = wx.TextCtrl(
            panel, value=format_probe_result(model_name, rec),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP,
        )
        self.result_field.SetMinSize((-1, 320))
        # Named for the screen reader: "Test result" is what gets spoken
        # when focus arrives, and focus arrives here on purpose so the
        # text can be read without hunting for it.
        self.result_field.SetName("Test result")

        close_btn = wx.Button(panel, wx.ID_OK, label="&Close")
        close_btn.SetDefault()

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.AddStretchSpacer()
        btn_row.Add(close_btn)

        ps = wx.BoxSizer(wx.VERTICAL)
        ps.Add(label, flag=wx.BOTTOM, border=6)
        ps.Add(self.result_field, proportion=1, flag=wx.EXPAND | wx.BOTTOM, border=10)
        ps.Add(btn_row, flag=wx.EXPAND)
        panel.SetSizer(ps)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, proportion=1, flag=wx.EXPAND | wx.ALL, border=12)
        self.SetSizer(outer)

        self.result_field.SetInsertionPoint(0)
        self.result_field.SetFocus()
