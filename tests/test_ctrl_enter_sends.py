# SPDX-License-Identifier: CC0-1.0
"""Guard test: Ctrl+Enter in the message box sends, on a fresh install.

Ctrl+Enter is the default send key ("Plain Enter sends" is off by default).
It is ALSO the accelerator on Chat -> Continue room round, and on wxMSW a menu
accelerator is translated before the focused control ever sees the key. So
the send branch in _on_input_key never ran: Ctrl+Enter in a one-on-one chat
fired "Continue room round", which does nothing outside a room, and a new
user could not send a message from the keyboard at all. Anyone who had turned
on plain-Enter sending would never have met it.

This builds a real frame on an isolated desktop with the real key handler
(_on_input_key) and the real menu handler (_on_ctrl_enter_menu), then delivers
real keystrokes: PostMessage to this process's own window, with the thread's
key state set through SetKeyboardState. Never SendInput, which would reach the
person's actual foreground.

A positive control first wires the menu item the OLD way and confirms that
Ctrl+Enter with text typed does not send — otherwise a pass would only prove
the keystrokes never arrived.

Run it safely with:  python tests/_gui_runner.py tests/test_ctrl_enter_sends.py
"""

import os
import sys
import tempfile

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="ctrlenter-", dir=(os.environ.get("HEARTHKIN_HOME") or None))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# Same opt-in gate as the other widget tests.
if os.environ.get("HEARTHKIN_GUI_TESTS", "").strip() not in ("1", "true", "yes"):
    print("SKIP -- builds real widgets, which take the foreground on the "
          "live desktop, and a screen reader follows focus. Run it safely on "
          "an isolated desktop with:")
    print("    python tests/_gui_runner.py " + __file__)
    sys.exit(0)

# Posting keystrokes is only done on a confirmed isolated desktop, even though
# they go to our own window: a live desktop is the person's, not the test's.
if os.environ.get("HEARTHKIN_GUI_ISOLATED") != "1" or sys.platform != "win32":
    print("SKIP -- delivers keystrokes, so only on an isolated Windows desktop:")
    print("    python tests/_gui_runner.py " + __file__)
    sys.exit(0)

try:
    import wx
except Exception as e:                                    # pragma: no cover
    print(f"SKIP wxPython unavailable ({e})")
    sys.exit(0)

import ctypes  # noqa: E402

app = wx.App()
app.SetExitOnFrameDelete(False)

from frame.input_attach_mixin import InputAttachMixin  # noqa: E402

_u = ctypes.windll.user32
WM_KEYDOWN, WM_KEYUP = 0x100, 0x101
VK_RETURN, VK_CONTROL, VK_LCONTROL = 0x0D, 0x11, 0xA2


def _set_ctrl(down):
    st = (ctypes.c_ubyte * 256)()
    _u.GetKeyboardState(st)
    st[VK_CONTROL] = st[VK_LCONTROL] = 0x80 if down else 0
    _u.SetKeyboardState(st)


class _Frame(InputAttachMixin, wx.Frame):
    """Just enough of the main window: the Chat menu item with its real
    accelerator, the message box with its real key handler, and a second box
    to put focus somewhere else."""

    def __init__(self, old_wiring=False):
        wx.Frame.__init__(self, None, title="ctrl-enter")
        self.config = {"enter_sends": False}      # the shipped default
        self._streaming = False
        self._pending_attachment = None
        self._pending_attachment_rel = None
        self.calls = []
        mb = wx.MenuBar()
        menu = wx.Menu()
        self.mnu_continue = menu.Append(
            wx.ID_ANY, "Continue room round\tCtrl+Enter")
        mb.Append(menu, "&Chat")
        self.SetMenuBar(mb)
        handler = self._on_continue if old_wiring else self._on_ctrl_enter_menu
        self.Bind(wx.EVT_MENU, handler, self.mnu_continue)
        panel = wx.Panel(self)
        self.other_box = wx.TextCtrl(panel, pos=(0, 0), size=(200, 60),
                                     style=wx.TE_MULTILINE)
        self.input_box = wx.TextCtrl(panel, pos=(0, 80), size=(200, 60),
                                     style=wx.TE_MULTILINE)
        self.input_box.Bind(wx.EVT_KEY_DOWN, self._on_input_key)

    def _on_send(self, _event):
        self.calls.append("send")

    def _on_continue(self, _event):
        self.calls.append("continue")

    def _on_stop(self, _event):
        self.calls.append("stop")


def _press(frame, target, ctrl, then):
    """Deliver Enter (with or without Ctrl) to `target`, then call
    then(calls) once the event loop has handled it."""
    target.SetFocus()

    def go():
        _set_ctrl(ctrl)
        lp = 1 | (0x1C << 16)
        _u.PostMessageW(target.GetHandle(), WM_KEYDOWN, VK_RETURN, lp)
        _u.PostMessageW(target.GetHandle(), WM_KEYUP, VK_RETURN,
                        lp | (1 << 30) | (1 << 31))

        def done():
            _set_ctrl(False)
            got = list(frame.calls)
            frame.calls.clear()
            try:
                then(got)
            except Exception as e:
                check("a check ran without raising (%s: %s)"
                      % (type(e).__name__, e), False)
                wx.CallAfter(run_next)
        wx.CallLater(400, done)
    wx.CallLater(200, go)


steps = []


def step(fn):
    steps.append(fn)
    return fn


def run_next():
    if not steps:
        wx.CallAfter(app.ExitMainLoop)
        return
    fn = steps.pop(0)
    try:
        fn()
    except Exception as e:
        # A step that raises must FAIL and move on, never hang. An exception
        # inside an event-loop callback does not end MainLoop, and run_all
        # has no per-test timeout, so a hang here would stall the whole suite.
        check("%s ran without raising (%s: %s)"
              % (fn.__name__, type(e).__name__, e), False)
        wx.CallAfter(run_next)


state = {}


@step
def control_old_wiring():
    f = _Frame(old_wiring=True)
    f.Show()
    f.input_box.SetValue("hello")

    def after(got):
        check("positive control: wired the old way, Ctrl+Enter with text typed "
              "does NOT send (it runs Continue room round)",
              "send" not in got and "continue" in got)
        f.Destroy()
        run_next()
    _press(f, f.input_box, True, after)


@step
def sends_when_typing():
    f = _Frame()
    f.Show()
    state["f"] = f
    f.input_box.SetValue("hello")

    def after(got):
        check("Ctrl+Enter in the message box with text typed sends", got == ["send"])
        run_next()
    _press(f, f.input_box, True, after)


@step
def empty_box_continues():
    f = state["f"]
    f.input_box.SetValue("")

    def after(got):
        check("Ctrl+Enter in an empty message box is still Continue room round",
              got == ["continue"])
        run_next()
    _press(f, f.input_box, True, after)


@step
def focus_elsewhere_continues():
    f = state["f"]
    f.input_box.SetValue("typed but not focused")

    def after(got):
        check("Ctrl+Enter with focus outside the message box does not send",
              got == ["continue"])
        run_next()
    _press(f, f.other_box, True, after)


@step
def attachment_alone_sends():
    f = state["f"]
    f.input_box.SetValue("")
    f._pending_attachment = "photo.png"

    def after(got):
        check("Ctrl+Enter with only an image staged sends, same as the button",
              got == ["send"])
        f._pending_attachment = None
        run_next()
    _press(f, f.input_box, True, after)


@step
def plain_enter_is_a_newline():
    f = state["f"]
    f.input_box.SetValue("hello")

    def after(got):
        check("plain Enter does not send while 'Plain Enter sends' is off",
              got == [])
        f.Destroy()
        run_next()
    _press(f, f.input_box, False, after)


keepalive = wx.Frame(None)
wx.CallLater(150, run_next)


def _give_up():
    # Last line of defence against a hang: whatever went wrong, end the loop
    # with a failure rather than leave the suite waiting forever.
    check("finished within 60 seconds", False)
    app.ExitMainLoop()


_deadline = wx.CallLater(60000, _give_up)
app.MainLoop()
if _deadline.IsRunning():
    _deadline.Stop()
keepalive.Destroy()

if _fails:
    print("\nFAILED %d: %s" % (len(_fails), ", ".join(_fails)))
    sys.exit(1)
print("\ntest_ctrl_enter_sends: all checks passed")
