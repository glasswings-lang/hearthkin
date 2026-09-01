"""Ask before quitting while a kin is still working.

Closing Hearthkin tears down bots, cancels timers and abandons in-flight
model calls. That is right when nothing is happening and quietly costly when
something is: a kin halfway through a reply to someone on Telegram, a memory
distillation that has been chewing for two minutes, a cron wake-up nobody is
watching, a kin blocked waiting for a tool approval.

The problem this solves is not "quitting is dangerous" — it's that there was
no way to KNOW. The only available signals were remembering to check, or
hearing the machine's fans spin up. Neither reaches you for a kin running on
another machine, and neither reaches you at all if you aren't in the room.

So this lists what is actually in flight, by name, and lets the answer be
"wait". Nothing is torn down until the answer is "close anyway" — the check
happens before the teardown in `_on_close`, not alongside it.

Accessibility notes, since this dialog only ever appears at a moment when
something is at stake:
  * the list is a read-only multiline TextCtrl, not StaticText, so it can be
    reached by Tab and re-read as many times as needed rather than announced
    once and gone;
  * its buddy label is created BEFORE it, so wxMSW/NVDA derives the field's
    accessible name from the nearest preceding StaticText;
  * "Wait" is created first, is the default, and is what Escape does, so
    every reflexive dismissal is the safe one;
  * and the summary is spoken on open, because a modal that appears while
    your attention is elsewhere is a modal you can miss.
"""

import wx

from audio import nvda_speak


class ConfirmCloseDialog(wx.Dialog):
    """Returns wx.ID_OK for "close anyway", wx.ID_CANCEL for "wait"."""

    def __init__(self, parent, busy_lines):
        super().__init__(parent, title="Something's still working",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.busy_lines = list(busy_lines or [])

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        n = len(self.busy_lines)
        # ONE StaticText, carrying both the count and the field's accessible
        # name, created immediately before the field. Two of them — a heading
        # plus a label — and the second wins the buddy slot, leaving the first
        # announced to nobody. Mnemonic on "finishing": D and A belong to the
        # buttons below.
        #
        # "still finishing", not "still working": a blind reader of the
        # announced text alone heard "3 things are still working" as possibly
        # meaning "still functioning correctly".
        heading = ("Hearthkin is still &finishing one thing:" if n == 1
                   else f"Hearthkin is still &finishing {n} things:")
        sizer.Add(wx.StaticText(panel, label=heading),
                  flag=wx.LEFT | wx.RIGHT | wx.TOP, border=12)

        # The consequence goes INSIDE the field rather than in a StaticText
        # after it. Explanatory text between a field and a button is read by
        # nobody: a button uses its own label as its name, so the text labels
        # nothing and a tabbing user never reaches it. As the field's last
        # line it's part of what gets read.
        body = "\n".join(f"• {line}" for line in self.busy_lines)
        body += ("\n\nQuitting now abandons the work above. "
                 "Staying open leaves Hearthkin exactly as it is.")
        self.list_field = wx.TextCtrl(
            panel, value=body,
            style=wx.TE_MULTILINE | wx.TE_READONLY,
            size=(460, 150))
        sizer.Add(self.list_field,
                  proportion=1, flag=wx.EXPAND | wx.ALL, border=12)

        # "Don't quit", not "Wait": a blind reader heard "Wait" as "hold the
        # quit until the work finishes, THEN close" — a deferred quit, the
        # opposite of what it does. Someone picking it on that reading would
        # come back later to a still-open app. And "Quit anyway", not "Close
        # anyway", because inside a dialog "Close" is the ordinary word for
        # "dismiss this box"; "Quit" is what the person actually pressed.
        #
        # Safe option created first: it gets focus, it's the default, and it's
        # what Escape does. A reflexive Enter or Escape must never be the
        # destructive answer.
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.wait_btn = wx.Button(panel, wx.ID_CANCEL, "&Don't quit")
        self.close_btn = wx.Button(panel, wx.ID_OK, "Quit &anyway")
        btn_row.AddStretchSpacer()
        btn_row.Add(self.wait_btn, flag=wx.RIGHT, border=8)
        btn_row.Add(self.close_btn)
        sizer.Add(btn_row, flag=wx.EXPAND | wx.ALL, border=12)

        self.SetEscapeId(wx.ID_CANCEL)
        self.wait_btn.SetDefault()
        self.wait_btn.SetFocus()

        panel.SetSizer(sizer)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, proportion=1, flag=wx.EXPAND)
        self.SetSizer(outer)
        self.SetInitialSize((520, 340))
        self.Layout()
        self.CentreOnParent()

        try:
            nvda_speak(self._announcement())
        except Exception:
            pass

    def _announcement(self):
        """What gets spoken on open. The CONSEQUENCE leads; the inventory
        follows.

        The first draft spoke only the list, and a reader given nothing but
        the announced text couldn't tell what was being decided — no spoken
        word was "quit" or "close". Someone who hit the shortcut by accident
        would hear a set of kin names and a button, with no idea a quit was
        underway. The stakes were reachable only by tabbing into the field,
        which nobody has a reason to do when focus is on a button and the
        situation already sounds fully described.

        Speech is interruptible, so length in the tail is cheap — but the
        first sentence has to carry the decision. Long lists are truncated
        because one enormous utterance can't be re-heard except by tabbing;
        the field always holds all of it.
        """
        n = len(self.busy_lines)
        subject = "one thing" if n == 1 else f"{n} things"
        lead = (f"Hearthkin is still finishing {subject}. Quitting now "
                f"abandons that; staying open changes nothing.")
        shown = self.busy_lines[:3]
        tail = ". ".join(shown)
        remaining = n - len(shown)
        if remaining > 0:
            tail += f". And {remaining} more, listed in this window"
        return f"{lead} {tail}."
