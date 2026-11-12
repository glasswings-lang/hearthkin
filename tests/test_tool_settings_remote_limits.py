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

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
