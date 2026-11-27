# SPDX-License-Identifier: CC0-1.0
"""Closing Hearthkin, driven in sequence the way a person uses it.

27 Nov 2026: with "Minimize to system tray on close" ticked, closing the
window asked "Hearthkin is still working, quit anyway?" instead of going to
the tray. Every piece had its own test (the tray, the question, Exit), and
each passed alone. The fault was only in the sequence: Exit sets a
"quitting" flag, "wait" kept Hearthkin open, and nothing cleared the flag,
so every later close took the quitting path. The person: "we need more
intelligent tests that don't just check features in isolation."

This file runs the real LifecycleMixin._on_close through orders of events,
with only the window and the dialog stood in for.

Run: python tests/test_close_sequences.py
"""

import os
import sys
import tempfile

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="closeseq-"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import wx  # noqa: E402

import frame.lifecycle_mixin as lm  # noqa: E402

fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        fails.append(label)


class Event:
    def __init__(self, can_veto=True):
        self._can = can_veto
        self.vetoed = False

    def CanVeto(self):
        return self._can

    def Veto(self):
        self.vetoed = True


class Dialog:
    """Stands in for ConfirmCloseDialog: records that it was shown and gives
    the answer the test set."""
    shown = 0
    answer = wx.ID_CANCEL

    def __init__(self, parent, busy):
        Dialog.shown += 1

    def ShowModal(self):
        return Dialog.answer

    def Destroy(self):
        pass


lm.ConfirmCloseDialog = Dialog


class TrayIcon:
    icon_visible = True


class Frame(lm.LifecycleMixin):
    """The real close logic; a window that only remembers what was done."""

    def __init__(self, close_to_tray=True, busy=("Sorrel is replying",)):
        self.config = {"close_to_tray": close_to_tray}
        self._quitting = False
        self._tray_icon = TrayIcon()
        self._busy = list(busy)
        self.shown = True
        self.iconized = False
        self.tore_down = False
        self.said = []

    def _work_in_flight(self):
        return list(self._busy)

    def IsShown(self):
        return self.shown

    def Hide(self):
        self.shown = False

    def Show(self, show=True):
        self.shown = show

    def Iconize(self, on=True):
        self.iconized = on

    def _announce_minimized_to_tray(self):
        self.said.append("tray")

    def _announce_minimized_to_taskbar(self):
        self.said.append("taskbar")

    def Close(self):
        ev = Event()
        self._on_close(ev)
        return ev

    def _teardown_marker(self):
        self.tore_down = True


# Stop a real quit at the point the teardown starts, so a test can see that
# the close went through without running the whole shutdown.
class _Quit(Exception):
    pass


_real_on_close = lm.LifecycleMixin._on_close


def _on_close(self, event):
    real = lm.LifecycleMixin._confirm_close_while_busy

    def confirm(s, e):
        ok = real(s, e)
        if ok:
            s.tore_down = True
            raise _Quit()
        return ok
    self._confirm_close_while_busy = lambda e: confirm(self, e)
    try:
        _real_on_close(self, event)
    except _Quit:
        pass


Frame._on_close = _on_close


def reset_dialog(answer):
    Dialog.shown = 0
    Dialog.answer = answer


# 1. The plain case: closing goes to the tray, nothing asked.
f = Frame()
reset_dialog(wx.ID_CANCEL)
ev = f.Close()
check("closing with the tray box ticked hides to the tray and asks nothing",
      not f.shown and Dialog.shown == 0 and ev.vetoed and f.said == ["tray"])

# 2. The sequence that broke: Exit while busy, say wait, then close again.
f = Frame()
reset_dialog(wx.ID_CANCEL)
f._on_exit(None)
check("Exit while a kin is working asks first", Dialog.shown == 1)
check("...and 'wait' keeps Hearthkin open", f.shown and not f.tore_down)
check("...and it is no longer quitting", f._quitting is False)
reset_dialog(wx.ID_CANCEL)
ev = f.Close()
check("closing after that goes to the tray again, without asking",
      not f.shown and Dialog.shown == 0 and ev.vetoed and not f.tore_down)

# 3. The same from the tray's own Exit.
f = Frame()
f.Hide()
reset_dialog(wx.ID_CANCEL)
f.exit_from_tray()
check("the tray's Exit while working asks, and 'wait' keeps it running",
      Dialog.shown == 1 and not f.tore_down and f._quitting is False)
f.Show()
reset_dialog(wx.ID_CANCEL)
f.Close()
check("...and the next close goes to the tray", not f.shown and Dialog.shown == 0)

# 4. Exit and quit anyway really quits.
f = Frame()
reset_dialog(wx.ID_OK)
f._on_exit(None)
check("Exit, then 'quit anyway', quits", f.tore_down)

# 5. Nothing working: Exit quits without asking; with the box off, closing quits.
f = Frame(busy=())
reset_dialog(wx.ID_CANCEL)
f._on_exit(None)
check("Exit with nothing working quits without asking",
      f.tore_down and Dialog.shown == 0)
f = Frame(close_to_tray=False, busy=())
f.Close()
check("with the tray box off and nothing working, closing quits", f.tore_down)

# 6. With the box off and work going, closing asks; 'wait' leaves it as it was.
f = Frame(close_to_tray=False)
reset_dialog(wx.ID_CANCEL)
f.Close()
check("with the tray box off, closing while working asks, and 'wait' keeps it",
      Dialog.shown == 1 and f.shown and not f.tore_down)

# 7. A hidden window asked to close from outside (installer, shutdown) quits.
f = Frame(busy=())
f.Hide()
f.Close()
check("a close request while hidden in the tray is a real exit (installer)",
      f.tore_down)

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("test_close_sequences: all checks passed")
