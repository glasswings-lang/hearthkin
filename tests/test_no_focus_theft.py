# SPDX-License-Identifier: CC0-1.0
"""Guard test: a default suite run cannot take the foreground.

Creating a top-level wx window TAKES THE FOREGROUND on Windows even when the
window is never shown — measured: right after building a dialog,
GetForegroundWindow() returns that dialog's own handle while both wx.IsShown()
and Win32 IsWindowVisible() report it hidden. Hearthkin sharpens that on purpose
by disabling Windows' foreground lock at startup, so an approval dialog can
reliably come to the front. A screen reader follows focus, not visibility. So a
widget-building test drags NVDA into an invisible window with nothing to read
and no obvious way out, mid-whatever-you-were-doing.

The rule already existed, in prose and in one test's own opt-in gate. Two later
tests were then added without it and shipped stealing focus on every run. A rule
each new file has to remember is a rule that gets forgotten, so the gate now
lives in the RUNNER: it reads each test's source and skips the ones that pull in
wx unless HEARTHKIN_GUI_TESTS is set. A new widget-building test is excluded the
moment it exists, whether or not its author ever heard of this.

Pinned here:
  * the detector actually fires (checked against the real files on disk, not
    only synthetic samples — a detector proven only against its own fixtures
    can be broken and still look green);
  * it doesn't fire on ordinary tests;
  * the runner honours it, and says out loud what it skipped;
  * and every widget-building file ALSO gates itself, so running one directly
    is a deliberate act too.

Run: python tests/test_no_focus_theft.py
"""

import os
import sys
import glob
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_all  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# --- the detector, on synthetic samples ---------------------------------
# Written as ordinary assignments rather than a quoted block, so this file's own
# source never carries a wx import at the start of a line — otherwise the very
# rule being tested would skip the test that tests it.
_TOP = "import wx\n"
_INDENTED = "try:\n    import wx\n"
_FROM = "from wx import Frame\n"
_MENTION = '"""A docstring that says import wx without doing it."""\n'
_STRING = 'msg = "import wx to build the dialog"\n'

check("plain top-level wx import is caught", run_all.builds_widgets(_TOP))
check("an indented wx import inside a try is caught",
      run_all.builds_widgets(_INDENTED))
check("`from wx import ...` is caught", run_all.builds_widgets(_FROM))
check("a docstring merely MENTIONING the import is not caught",
      not run_all.builds_widgets(_MENTION))
check("...nor is a string literal containing it",
      not run_all.builds_widgets(_STRING))
check("an ordinary test is not caught",
      not run_all.builds_widgets("import json\nfrom pathlib import Path\n"))


# --- and on the real files ----------------------------------------------
# The instrument gets checked against a known positive before its zeroes are
# believed: if this list ever comes back empty, that is the detector having
# broken, not the tree having become safe.

widget_files = []
plain_files = []
for path in sorted(glob.glob(str(HERE / "test_*.py"))):
    source = Path(path).read_text(encoding="utf-8", errors="replace")
    (widget_files if run_all.builds_widgets(source) else plain_files).append(
        Path(path).name)

check("the detector finds the widget-building tests that really exist",
      len(widget_files) >= 1)
print(f"       ({len(widget_files)} widget-building, {len(plain_files)} plain: "
      + ", ".join(widget_files) + ")")
check("this test itself is not classified as widget-building",
      Path(__file__).name not in widget_files)

# Every widget-building file also gates itself. The runner is the guarantee for
# `run_all.py`; this is the guarantee for someone running one file by hand.
for name in widget_files:
    source = (HERE / name).read_text(encoding="utf-8", errors="replace")
    check(f"{name} gates itself on HEARTHKIN_GUI_TESTS",
          "HEARTHKIN_GUI_TESTS" in source)


# --- the runner honours it ----------------------------------------------
# Source-level, because invoking run_all.py from a test that run_all.py runs
# would recurse.

runner = (HERE / "run_all.py").read_text(encoding="utf-8")
check("the runner classifies each file before running it",
      "builds_widgets(source)" in runner)
check("...and routes those through the isolated-desktop wrapper",
      "gui_runner" in runner and "_gui_runner.py" in runner)
check("...falling back to skipping when isolation isn't possible",
      "skipped_gui.append(name)" in runner)
# A skip nobody is told about is a test you believe you have.
check("a skip is announced, not silent", "Skipped " in runner)
check("...and so is an isolated run", "isolated_gui" in runner)
check("...and the green line counts what actually ran",
      "len(files) - len(skipped_gui)" in runner)
# The runner prints on the person's desktop, so it must never move ITSELF.
check("the runner probes isolation in a throwaway process, not in itself",
      "def desktop_isolation_available" in runner
      and "subprocess.run" in runner.split("def desktop_isolation_available")[1][:800])

wrapper = (HERE / "_gui_runner.py").read_text(encoding="utf-8")
check("the wrapper isolates before running anything",
      "enter_isolated_desktop()" in wrapper)
# The whole safety property. "Couldn't make it safe" must never become "so we
# did it anyway" — that failure lands on a person mid-task.
check("...and REFUSES the test outright when it can't",
      "REFUSED" in wrapper and "return 3" in wrapper)


# --- end to end: a real dialog test, and the foreground never moves ------
#
# The only honest way to ask. Note this parent process never isolates itself:
# GetForegroundWindow() is answered by the CALLING THREAD's desktop, so a
# process that moved could not report on the desktop the person is using — it
# would return the isolated desktop's foreground (None) and read as a change
# that never happened. Measure from where the person is.

if sys.platform == "win32" and run_all.desktop_isolation_available():
    import ctypes
    import subprocess
    _u = ctypes.WinDLL("user32", use_last_error=True)
    _u.GetForegroundWindow.restype = ctypes.c_void_p

    import tempfile
    import time
    from ctypes import wintypes

    _u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p,
                                            ctypes.POINTER(wintypes.DWORD)]

    def _foreground_pid():
        """Which process owns the window the person is looking at."""
        hwnd = _u.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        _u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    # Ask the PRECISE question: does a window belonging to the test process
    # ever hold the foreground?
    #
    # The obvious version — foreground before vs after — is not that question.
    # It fails whenever the person switches window while the suite runs, which
    # on this machine is most of the time, and it reports their own typing as
    # a focus theft. A test that cries wolf about ordinary use is worse than no
    # test: it gets muted, and then it isn't watching anything.
    _target = HERE / "test_stable_tab_order.py"
    _proc = subprocess.Popen(
        [sys.executable, str(HERE / "_gui_runner.py"), str(_target)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=dict(os.environ,
                 HEARTHKIN_HOME=tempfile.mkdtemp(prefix="focus-e2e-")))
    _stolen = []
    while _proc.poll() is None:
        if _foreground_pid() == _proc.pid:
            _stolen.append(_proc.pid)
            break
        time.sleep(0.05)
    _out = _proc.stdout.read() or ""
    _proc.wait()

    check("a real widget test RUNS on the isolated desktop (not skipped)",
          "SKIP" not in _out and _proc.returncode == 0)
    check("...it really built widgets, rather than passing vacuously",
          "tab order" in _out)
    check("...and no window it made ever took the foreground", not _stolen)
else:
    check("desktop isolation unavailable here — widget tests stay skipped",
          not run_all.gui_tests_enabled())


print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_no_focus_theft: all checks passed")
