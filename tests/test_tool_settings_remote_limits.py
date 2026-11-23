# SPDX-License-Identifier: CC0-1.0
"""The Tools settings window's two remote-safety settings.

Builds the real dialog (isolated desktop only) and checks that "Most tool
calls in one reply for other people" and "Warn me aloud after this many
calls in a remote reply" are there, reachable by Tab, start at the kin's
values, save what's typed, carry shortcut letters nothing else uses, and
that the old rounds setting now says rounds, not calls.
"""

import os
import re
import sys
import tempfile

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="toolset-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


if os.environ.get("HEARTHKIN_GUI_TESTS", "").strip() not in ("1", "true", "yes"):
    print("SKIP -- builds real widgets, which take the foreground on the "
          "live desktop, and a screen reader follows focus. Run it safely on "
          "an isolated desktop with:")
    print("    python tests/_gui_runner.py " + __file__)
    sys.exit(0)

try:
    import wx
except Exception as e:                                    # pragma: no cover
    print(f"SKIP wxPython unavailable ({e})")
    sys.exit(0)

app = wx.App()
from dialogs.tool_settings import ToolSettingsDialog  # noqa: E402


def main():
    saved = {}
    dlg = ToolSettingsDialog(
        None, {"remote_max_tool_calls": 12, "remote_tool_call_notice": 7},
        lambda k, v: saved.__setitem__(k, v), lambda k, v: None, kin_name="Wren")
    cap = getattr(dlg, "remote_cap_field", None)
    notice = getattr(dlg, "remote_notice_field", None)
    check("the per-reply limit for other people is there", cap is not None)
    check("the spoken-warning setting is there", notice is not None)
    check("...starting at the kin's values",
          cap is not None and str(cap.GetValue()) == "12"
          and notice is not None and str(notice.GetValue()) == "7")

    panel = dlg.GetChildren()[0]
    stops = [c for c in panel.GetChildren() if c.CanAcceptFocusFromKeyboard()]
    check("both are tab stops", cap in stops and notice in stops)

    labels = [c.GetLabel() for c in panel.GetChildren() if isinstance(c, wx.StaticText)]
    check("the rounds setting now says rounds",
          any("Max tool rounds per reply" in l for l in labels)
          and not any("Max tool calls per reply" in l for l in labels))
    letters = [m.group(1).lower() for l in labels + [c.GetLabel() for c in stops]
               for m in [re.search(r"&(\w)", l or "")] if m]
    check("shortcut letters are all different", len(letters) == len(set(letters)))

    staging = getattr(dlg, "staging_read_field", None)
    check("the staging read size is there (it used to be file-only)",
          staging is not None and staging in stops
          and str(staging.GetValue()) == "6000")
    for field, key, val in ((cap, "remote_max_tool_calls", "20"),
                            (notice, "remote_tool_call_notice", "0"),
                            (staging, "staging_read_max_chars", "4000")):
        if field is None:
            continue
        field.SetValue(val)
        field._commit(None)
        check(f"{key} saves what was typed", str(saved.get(key)) == val)
    dlg.Destroy()

    # Tools left out of wake-ups (15 Nov 2026): the kin's enabled tools,
    # ticked from cron_tools_off, saving the ticked ones.
    from hearthkin_paths import kin_dir
    from kin_persistence import save_kin_tools
    kin_dir("Wren").mkdir(parents=True, exist_ok=True)
    save_kin_tools("Wren", ["exec", "read_file", "edit_file"])
    saved.clear()
    dlg = ToolSettingsDialog(
        None, {"cron_tools_off": ["exec"]},
        lambda k, v: saved.__setitem__(k, v), lambda k, v: None, kin_name="Wren")
    wake = getattr(dlg, "wake_tools_off", None)
    check("the wake-up tools list is there", wake is not None)
    if wake is not None:
        names = [wake.GetString(i) for i in range(wake.GetCount())]
        check("...listing the kin's switched-on tools, and reach_out and "
              "message_kin, which every wake-up is given (23 Nov 2026)",
              names == ["edit_file", "exec", "message_kin", "reach_out", "read_file"])
        check("...with exec ticked, as saved", [n for i, n in enumerate(names) if wake.IsChecked(i)] == ["exec"])
        panel = dlg.GetChildren()[0]
        check("...and it's a tab stop",
              wake in [c for c in panel.GetChildren() if c.CanAcceptFocusFromKeyboard()])
        wake.Check(names.index("read_file"), True)
        dlg._on_wake_tool_tick(None)
        check("ticking another saves both", sorted(saved.get("cron_tools_off") or []) == ["exec", "read_file"])
        wake.Check(names.index("exec"), False)
        wake.Check(names.index("read_file"), False)
        dlg._on_wake_tool_tick(None)
        check("unticking all saves an empty list", saved.get("cron_tools_off") == [])
        labels = [c.GetLabel() for c in panel.GetChildren() if isinstance(c, wx.StaticText)]
        stops = [c for c in panel.GetChildren() if c.CanAcceptFocusFromKeyboard()]
        letters = [m.group(1).lower() for l in labels + [c.GetLabel() for c in stops]
                   for m in [re.search(r"&(\w)", l or "")] if m]
        check("shortcut letters are still all different", len(letters) == len(set(letters)))
    dlg.Destroy()

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
