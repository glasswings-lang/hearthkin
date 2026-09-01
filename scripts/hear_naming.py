#!/usr/bin/env python
"""hear_naming — tab through the naming patterns and hear what NVDA does.

    python scripts/hear_naming.py

`scripts/audit_ui.py` asks Windows what name it offers for a control, which is
what NVDA reads. That covers the name. It cannot answer the part that only an
ear can settle: what NVDA does with a control that has no name but does have
text — and, for a multi-line read-only field, whether focusing it speaks the
whole thing or only the line the caret is on.

So this builds one specimen per pattern and lets you Tab through them. Each
specimen is announced by a read-only field immediately before it, which is safe
precisely because of the thing being demonstrated: a read-only TextCtrl cannot
name the control that follows it, so the announcer cannot contaminate the case.

What was already measured on this machine, so you know what to expect:

    StaticText -> anything          the StaticText is the name
    read-only TextCtrl -> anything  NO NAME (it cannot label)
    RadioButton -> anything         NO NAME (it cannot label)
    Button -> anything              NO NAME
    SetName("X") on anything        NO NAME -- SetName is not the MSAA name
    a label separated from its
      field by any other control    NO NAME

    An unnamed read-only field still exposes its text as the accessible VALUE,
    so it is heard. An unnamed EMPTY editable field exposes nothing at all.

The open question this harness is for: cases 2 and 3.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("HEARTHKIN_HOME",
                      os.path.join(os.environ.get("TEMP", "."), "hk-hear-naming"))

import wx  # noqa: E402

_RO = wx.TE_READONLY
_ROM = wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL


class HearNamingDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Hear what NVDA says", size=(700, 640))
        panel = wx.Panel(self)
        box = wx.BoxSizer(wx.VERTICAL)

        def announce(text):
            """Say which case is next. Read-only, so it names nothing after it."""
            t = wx.TextCtrl(panel, value=text, style=_RO)
            box.Add(t, flag=wx.EXPAND | wx.TOP | wx.BOTTOM, border=6)

        def add(w, border=2):
            box.Add(w, flag=wx.EXPAND | wx.BOTTOM, border=border)
            return w

        announce("CASE 1. A field with a StaticText right before it. "
                 "This is the one that works — you should hear a name.")
        add(wx.StaticText(panel, label="&Kin name:"))
        add(wx.TextCtrl(panel), border=8)

        announce("CASE 2. A read-only field holding one line of text and no "
                 "name. Does NVDA read the words, or just say 'edit'?")
        add(wx.TextCtrl(panel, value="Target kin (where history lands):",
                        style=_RO), border=8)

        announce("CASE 3. A read-only field holding FIVE lines and no name. "
                 "The question that matters: do you get all five on focus, or "
                 "only the first? If only the first, every long help text in "
                 "this app is mostly unread until you arrow down it.")
        add(wx.TextCtrl(panel, value=(
            "Line one: whole conversations keeps every exchange next to its reply.\n"
            "Line two: weaving reads as one chronology across everyone.\n"
            "Line three: with a single file selected this makes no difference.\n"
            "Line four: you should be hearing this line too.\n"
            "Line five: and this one, if focus reads the whole field."),
            style=_ROM), border=8).SetMinSize((-1, 90))

        announce("CASE 4. An EDITABLE, EMPTY field with no name — only a radio "
                 "button before it. This is the live bug in Import history. "
                 "Expect a bare 'edit' with nothing to say what it is for.")
        add(wx.RadioButton(panel, label="Create a &new kin:", style=wx.RB_GROUP))
        add(wx.TextCtrl(panel), border=8)

        announce("CASE 5. The same thing with a StaticText inserted between the "
                 "radio and the field. This is the proposed fix — it should now "
                 "have a name.")
        add(wx.RadioButton(panel, label="Create a new kin (&fixed):"))
        add(wx.StaticText(panel, label="Name for the ne&w kin:"))
        add(wx.TextCtrl(panel), border=8)

        announce("CASE 6. A combo box with only a radio button before it, "
                 "named with SetName — which was measured to do nothing. "
                 "Expect the selected kin's name and no idea what it is for.")
        add(wx.RadioButton(panel, label="&Existing kin:"))
        broken = add(wx.Choice(panel, choices=["Opal", "Marlow", "Wren"]), border=8)
        broken.SetSelection(0)
        broken.SetName("Existing kin to import into")

        close = wx.Button(panel, wx.ID_CANCEL, label="&Close")
        box.Add(close, flag=wx.ALIGN_RIGHT | wx.TOP, border=10)

        panel.SetSizer(box)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, proportion=1, flag=wx.EXPAND | wx.ALL, border=12)
        self.SetSizer(outer)


def main():
    app = wx.App()
    dlg = HearNamingDialog(None)
    dlg.ShowModal()
    dlg.Destroy()
    app.Destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
