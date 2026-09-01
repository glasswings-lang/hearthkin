# SPDX-License-Identifier: CC0-1.0
"""Guard test: an approval request comes and gets you.

An exec approval is the single most time-critical thing Hearthkin asks for. It
blocks a kin mid-turn — a worker thread is literally parked on the answer — and
it expires on a timer. Every other notification can afford to wait politely.
This one cannot.

It was waiting politely. The dialog opened *inside the app*, which might be
behind a browser or hidden in the system tray, and the supporting signals were
a Windows toast (lost among Telegram's, Signal's and everything else's) and
NVDA speech (which never gets a gap to land in when character echo is on, and
character echo is on because it has to be). The result: a kin sat blocked for
HOURS waiting for an approval nobody knew had been asked for, and then timed
out.

So the dialog now restores the window and takes the foreground. That is
deliberately rude, and it is correct here: a window arriving in front is a
*focus event*, which a screen reader announces immediately rather than
queueing behind whatever else is talking. It is also the only signal that
survives the person being in a different application entirely.

This is source-level. Actually exercising it needs a real modal on a real
desktop with a real window manager — but the properties that make it work are
checkable, and each of them is one deletion away from silently regressing to
the behaviour that cost those hours.
"""

import os
import sys
import pathlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


ROOT = pathlib.Path(__file__).resolve().parent.parent
src = (ROOT / "frame" / "cron_exec_mixin.py").read_text(encoding="utf-8")

# The approval body: from the dialog factory call back to where it's shown.
start = src.index("def _request_approval_dialog")
end = src.index("def _request_webcam_approval")
body = src[start:end]

check("the approval path restores the window before asking",
      "self.bring_to_front()" in body)
check("...and foregrounds the DIALOG, not just the frame "
      "(restoring the app is no use if the question is behind it)",
      "_force_foreground(dlg.GetHandle())" in body)
check("...before it blocks on the answer",
      body.index("bring_to_front") < body.index("dlg.ShowModal()")
      and body.index("_force_foreground") < body.index("dlg.ShowModal()"))
check("an audible alert still fires alongside it",
      "_play_approval_alert()" in body)

# Both attention-grabbing calls must be individually guarded. If either can
# raise, the dialog never shows, and the kin waits for an answer nobody was
# ever asked for -- strictly worse than the bug being fixed.
for call in ("self.bring_to_front()", "_force_foreground(dlg.GetHandle())"):
    i = body.index(call)
    before = body[max(0, i - 120):i]
    after = body[i:i + 160]
    check(f"{call} is wrapped so a failure can't stop the dialog appearing",
          "try:" in before and "except Exception:" in after)

check("the worker is still released no matter what happens",
      "finally:" in body and "decision_event.set()" in body)

# bring_to_front must genuinely un-hide, since Hearthkin can be sitting in the
# tray with its window hidden -- Raise() alone does nothing to a hidden window.
life = (ROOT / "frame" / "lifecycle_mixin.py").read_text(encoding="utf-8")
btf = life[life.index("def bring_to_front"):]
btf = btf[:btf.index("\n    def ", 10)]
check("bring_to_front un-hides a window that's minimised to tray",
      "Show(" in btf or "Iconize(" in btf)
check("...and forces the foreground rather than merely asking",
      "_force_foreground" in btf)

print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_approval_visibility: all checks passed")
