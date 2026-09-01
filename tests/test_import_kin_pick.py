# SPDX-License-Identifier: CC0-1.0
"""Guard test: choosing who the kin is, instead of being guessed at.

One field in the import dialog decides who said what for an entire
archive, and it used to be a bare text box pre-filled by reading the
FILENAME: anything ending `_User.txt` became the kin. Telegram names its
exports after either side of a chat, so both spellings are ordinary
filenames and the guess cannot tell which it is looking at.

Get it backwards and every word you ever said is filed as the kin's own.
That happened: 29,451 turns of one person's own messages sat in a kin's
mouth for months, with every other participant — including other kin —
demoted to "someone talking to it".

Nothing caught it, and the reason is worth keeping. There WAS a spoken
summary, and it said "80353 messages — 29451 from the kin, 50902 from
others". Which is exactly what you would expect to hear whether the
right person had been chosen or the wrong one. The counts were never the
missing piece. The NAME was.

So two things are pinned here: you pick from the speakers actually in
the file, with their turn counts beside them; and the summary names who
it is about to treat as the kin.

Run: python tests/test_import_kin_pick.py
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault(
    "HEARTHKIN_HOME", tempfile.mkdtemp(prefix="hearthkin-imppick-"))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# OPT-IN, and it must stay that way.
#
# Creating a top-level wx window TAKES THE FOREGROUND on Windows, even
# though the window is never shown and both wx.IsShown() and Win32
# IsWindowVisible() report it hidden. Measured: right after building a
# dialog, GetForegroundWindow() returns that dialog's own handle.
#
# Hearthkin makes this worse on purpose — it disables Windows' foreground
# lock at startup (`manage_foreground_lock`) so an approval dialog can
# reliably come to the front. So on the machine this project is FOR, any
# widget-building test grabs focus.
#
# A screen reader follows focus, not visibility. So a suite run drags
# NVDA into an invisible window that has nothing to read and nothing to
# escape from — which is worse than a window popping up, not better. It
# happened, mid-task, to the person who uses this app daily.
#
# Set HEARTHKIN_GUI_TESTS=1 to run it deliberately, when you aren't
# relying on the screen reader for something else.
if os.environ.get("HEARTHKIN_GUI_TESTS", "").strip() not in ("1", "true", "yes"):
    print("SKIP -- builds real widgets, which take the foreground on the "
          "live desktop, and a screen reader follows focus. Run it safely on "
          "an isolated desktop with:")
    print("    python tests/_gui_runner.py " + __file__)
    sys.exit(0)

try:
    import wx
    _app = wx.App()
except Exception as e:      # headless CI, no display
    print(f"SKIP — no usable wx display ({type(e).__name__})")
    sys.exit(0)

from dialogs.import_history import (  # noqa: E402
    ImportHistoryDialog, _FILENAME_KIN_GUESS)


# A three-party export named after the WRONG side of the chat — the
# exact shape that caused the damage.
_d = tempfile.mkdtemp(prefix="hearthkin-imppick-src-")
PATH = os.path.join(_d, "SpeakerThree_User.txt")
_lines = []
for i in range(30):
    _lines.append(f"[01-02-2024 10:{i:02d}:00] SpeakerThree: mine {i}")
for i in range(9):
    _lines.append(f"[01-02-2024 11:{i:02d}:00] SpeakerTwo: theirs {i}")
for i in range(4):
    _lines.append(f"[01-02-2024 12:{i:02d}:00] SpeakerOne: third {i}")
with open(PATH, "w", encoding="utf-8") as f:
    f.write("\n".join(_lines))

check("the filename guess still picks the wrong side, unchanged",
      _FILENAME_KIN_GUESS.match("SpeakerThree_User.txt").group(1) == "SpeakerThree")

_frame = wx.Frame(None)
dlg = ImportHistoryDialog(_frame)
dlg.file_field.ChangeValue(PATH)
dlg._paths = [PATH]
dlg.kin_name_in_source.ChangeValue("SpeakerThree")     # the guess, as it lands
dlg._start_parse_worker()
for _ in range(100):
    wx.Yield()
    time.sleep(0.05)
    if dlg._parsed:
        break

check("the file parsed", dlg._parsed is not None)

# --- every speaker in the file is offered, with counts ------------------

rows = [dlg.speaker_list.GetString(i)
        for i in range(dlg.speaker_list.GetCount())]
check("every speaker in the file is listed", len(rows) == 3)
check("...most-talkative first", rows[0].startswith("SpeakerThree"))
check("...with their turn counts, so a lopsided pick is visible",
      "30" in rows[0] and "9" in rows[1] and "4" in rows[2])
check("the list is backed by raw names, not display strings",
      dlg._speaker_rows == ["SpeakerThree", "SpeakerTwo", "SpeakerOne"])

# --- the summary NAMES who it is about to make the kin ------------------

wrong = dlg._pending_status or ""
check("the summary names the speaker it would file as the kin",
      "SpeakerThree" in wrong)
check("...and says plainly what that means",
      "own words" in wrong)
check("...and accounts for everyone else",
      "said to them" in wrong)

# --- picking corrects it, without typing --------------------------------

dlg.speaker_list.SetSelection(dlg._speaker_rows.index("SpeakerTwo"))
dlg._on_speaker_picked(None)
check("picking a speaker sets the kin name instantly",
      dlg.kin_name_in_source.GetValue() == "SpeakerTwo")
# The remap itself is debounced through the same 300ms timer typing
# uses (arrowing through a long speaker list fires one EVT_LISTBOX per
# keypress, same shape as one EVT_TEXT per keystroke — applying
# immediately meant a full re-parse per arrow press on a large archive,
# confirmed live on a 100+-speaker file). Fire it directly rather than
# waiting on a real timer through an event loop.
dlg._apply_kin_name()

roles = {}
for m in dlg._parsed[0]:
    roles.setdefault(m["speaker"], set()).add(m["role"])
check("the kin now holds the assistant slot alone",
      roles["SpeakerTwo"] == {"assistant"})
check("...the person who was wrongly the kin is now a user",
      roles["SpeakerThree"] == {"user"})
check("...and the third party stays a user throughout",
      roles["SpeakerOne"] == {"user"})

right = dlg._pending_status or ""
check("the summary follows the correction", "SpeakerTwo" in right)
check("...and the two readings are distinguishable by ear", wrong != right)

dlg.Destroy()
_frame.Destroy()

if _fails:
    print("\n%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
    sys.exit(1)
print("\nall import kin-pick checks passed")
