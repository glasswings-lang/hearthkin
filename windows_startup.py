# SPDX-License-Identifier: CC0-1.0

"""Windows-only helpers for adding/removing Hearthkin from the user's
auto-start list.

Uses HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.
Per-user (no admin needed); the entry naturally disappears with the
user account. Path is rewritten on every enable() so a reinstall to a
different location heals the entry the next time the toggle is
flipped.

All functions are safe no-ops on non-Windows platforms — Hearthkin is
Windows-first but the chat path runs on Linux/macOS, so this module
shouldn't crash there. is_supported() lets the UI grey the toggle out
where the feature isn't real."""

import sys
from pathlib import Path


_RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "Hearthkin"


def _winreg():
    """Return the winreg module on Windows, None elsewhere."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        return winreg
    except ImportError:
        return None


def is_supported():
    """True only on Windows (where HKCU\\...\\Run is meaningful)."""
    return _winreg() is not None


def launch_command():
    """Build the command-line that should fire to start Hearthkin on
    login. Two cases:

      1. Frozen install (typical end-user, PyInstaller-built .exe) →
         use sys.executable directly. The Inno Setup installer puts
         Hearthkin.exe at a stable path, so this string stays valid
         until the next install.
      2. Source install (developer running `python hearthkin.pyw`) →
         use pythonw.exe (no console flash) plus the absolute script
         path. Walks from this module's directory to find the entry
         script; falls back to the cwd resolution if that doesn't
         exist (which would be very unusual)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")
    runner = str(pyw) if pyw.exists() else str(py)
    script = Path(__file__).parent / "hearthkin.pyw"
    if not script.exists():
        script = Path("hearthkin.pyw").resolve()
    return f'"{runner}" "{script}"'


def is_enabled():
    """True if Hearthkin is currently registered to launch at login."""
    winreg = _winreg()
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH) as key:
            try:
                val, _ = winreg.QueryValueEx(key, _VALUE_NAME)
                return bool(val)
            except FileNotFoundError:
                return False
    except OSError:
        return False


def enable():
    """Add (or refresh) the Run entry. Raises RuntimeError on
    non-Windows or if the registry write fails."""
    winreg = _winreg()
    if winreg is None:
        raise RuntimeError(
            "Start-with-Windows is a Windows-only feature."
        )
    cmd = launch_command()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH,
            0, winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, cmd)
    except OSError as e:
        raise RuntimeError(f"Couldn't write the registry value: {e}") from e


def disable():
    """Remove the Run entry. Silent no-op if not present."""
    winreg = _winreg()
    if winreg is None:
        return
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH,
            0, winreg.KEY_SET_VALUE,
        ) as key:
            try:
                winreg.DeleteValue(key, _VALUE_NAME)
            except FileNotFoundError:
                pass
    except OSError:
        pass


def current_command():
    """Return the registered launch command string, or '' if not set.
    Useful for the prefs UI to surface what's actually registered."""
    winreg = _winreg()
    if winreg is None:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH) as key:
            try:
                val, _ = winreg.QueryValueEx(key, _VALUE_NAME)
                return str(val or "")
            except FileNotFoundError:
                return ""
    except OSError:
        return ""
