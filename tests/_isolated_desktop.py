# SPDX-License-Identifier: CC0-1.0
"""Run widget-building tests on a Windows desktop that has no input attached.

Creating a top-level wx window takes the FOREGROUND on Windows even when the
window is never shown, and this app disables the foreground lock at startup on
purpose so approval dialogs can reach the person using it. A screen reader
follows focus, so a widget-building test drags NVDA into an empty invisible
window mid-task.

The previous answer was an opt-in flag: don't run those tests unless you say so.
That is not an answer for the person this project is for, who has a screen reader
running at all times and therefore can never set it — it locked the only person
who needs those tests out of them. A gate is not a fix.

This is the fix. Windows has more than one *desktop* inside a window station
(`WinSta0` holds `Default`, `Winlogon`, the screensaver's). Exactly one of them
is the **input desktop** — the one connected to the keyboard, the mouse, and the
foreground window. A thread can be moved to a different desktop with
`SetThreadDesktop`, and every window it creates afterwards belongs there. Those
windows are real and fully functional: they lay out, they take programmatic
focus, they answer `IsShown()`. They simply are not on the desktop the person is
looking at, so nothing they do can reach the foreground or the screen reader.

This isn't a trick or a race — a window on a non-input desktop has no path to
your input queue at all. It's the same mechanism a service uses so it can't
interfere with a logged-in session.

Two constraints worth knowing:

  * `SetThreadDesktop` fails if the calling thread already has windows or hooks,
    so this must run before anything creates one — before wx is even imported.
    `_gui_runner.py` calls it as the very first thing in a fresh process, which
    is why the test files themselves need no cooperation.
  * The desktop must stay open for as long as its windows live. The handle is
    held in a module global for the life of the process rather than closed.

**Nothing here ever calls `SwitchDesktop`.** That would put the new desktop in
front of the person — the exact opposite of the point. There is no code path in
this file that changes what anyone is looking at.

Fails soft everywhere: on non-Windows, on a locked-down window station, on any
API error, `enter_isolated_desktop()` returns False and the caller falls back to
skipping the test. Never leave "we couldn't isolate" meaning "run it anyway".
"""

import os
import sys

_ISOLATED = False
_HANDLE = None          # kept alive deliberately; see the docstring
_DETAIL = "not attempted"


def _windows():
    return sys.platform == "win32"


def isolation_detail():
    """Why isolation succeeded or failed, for a human reading test output."""
    return _DETAIL


def _desktop_name(handle, user32):
    """The name Windows knows a desktop handle by. Used to prove the thread
    really moved, rather than trusting a BOOL return."""
    import ctypes
    from ctypes import wintypes
    UOI_NAME = 2
    buf = ctypes.create_unicode_buffer(256)
    needed = wintypes.DWORD()
    ok = user32.GetUserObjectInformationW(
        handle, UOI_NAME, buf, ctypes.sizeof(buf), ctypes.byref(needed))
    return buf.value if ok else "?"


def input_desktop_name():
    """The name of the desktop the person is actually looking at."""
    if not _windows():
        return ""
    import ctypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    h = user32.OpenInputDesktop(0, False, 0x0001)   # DESKTOP_READOBJECTS
    if not h:
        return ""
    try:
        return _desktop_name(h, user32)
    finally:
        user32.CloseDesktop(h)


def current_desktop_name():
    """The name of the desktop THIS THREAD's windows would be created on."""
    if not _windows():
        return ""
    import ctypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    h = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
    return _desktop_name(h, user32) if h else ""


def enter_isolated_desktop(name="hearthkin-tests"):
    """Move this thread onto a fresh desktop with no input attached.

    Returns True only when the move is CONFIRMED: the thread's desktop is now a
    different one from the input desktop. A True that merely trusted an API's
    return value would be the kind of reassurance that is worse than none —
    the caller uses this to decide whether it is safe to build windows.
    """
    global _ISOLATED, _HANDLE, _DETAIL
    if _ISOLATED:
        return True
    if not _windows():
        _DETAIL = f"not Windows ({sys.platform}) — no desktops to isolate onto"
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        user32.CreateDesktopW.restype = wintypes.HANDLE
        user32.CreateDesktopW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        user32.SetThreadDesktop.restype = wintypes.BOOL
        user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
        user32.GetThreadDesktop.restype = wintypes.HANDLE
        user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
        user32.OpenInputDesktop.restype = wintypes.HANDLE
        user32.OpenInputDesktop.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        user32.GetUserObjectInformationW.restype = wintypes.BOOL

        # Per-process name: two suite runs at once must not fight over one
        # desktop, and a stale handle from a crashed run must not be inherited.
        full = f"{name}-{os.getpid()}"
        # GENERIC_ALL: this process both creates the desktop and puts windows on
        # it. Nothing else is ever granted access to it.
        handle = user32.CreateDesktopW(full, None, None, 0, 0x10000000, None)
        if not handle:
            _DETAIL = (f"CreateDesktopW failed (error "
                       f"{ctypes.get_last_error()}) — the window station may "
                       f"not permit new desktops")
            return False

        if not user32.SetThreadDesktop(handle):
            _DETAIL = (f"SetThreadDesktop failed (error "
                       f"{ctypes.get_last_error()}) — this thread already owns "
                       f"windows or hooks; isolation must happen first")
            user32.CloseDesktop(handle)
            return False

        # Prove it, rather than believe it. Two independent facts: the thread's
        # desktop is the one just made, and that is NOT the input desktop.
        mine = current_desktop_name()
        theirs = input_desktop_name()
        if not mine or mine.lower() != full.lower():
            _DETAIL = f"thread desktop is {mine!r}, expected {full!r}"
            return False
        if theirs and mine.lower() == theirs.lower():
            _DETAIL = f"thread is still on the INPUT desktop ({theirs!r})"
            return False

        _HANDLE = handle
        _ISOLATED = True
        _DETAIL = (f"on desktop {mine!r}; the person is on {theirs or '?'!r} — "
                   f"windows made here cannot reach their foreground")
        return True
    except Exception as e:                                # pragma: no cover
        _DETAIL = f"{type(e).__name__}: {e}"
        return False


if __name__ == "__main__":
    # Safe probe: reports what isolation WOULD do. Creates no windows.
    print(f"platform          : {sys.platform}")
    print(f"input desktop     : {input_desktop_name()!r}")
    print(f"this thread on    : {current_desktop_name()!r}")
    ok = enter_isolated_desktop()
    print(f"isolation         : {'OK' if ok else 'UNAVAILABLE'}")
    print(f"detail            : {isolation_detail()}")
    print(f"this thread now on: {current_desktop_name()!r}")
    print(f"input desktop still: {input_desktop_name()!r}  (must be unchanged)")
