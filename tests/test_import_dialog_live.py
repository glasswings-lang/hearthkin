# SPDX-License-Identifier: CC0-1.0
"""Guard test: the import dialog's controls actually do what they say.

Two bugs shipped in the multi-select import while every existing test passed,
because none of them ever drove the dialog. `test_import_many` proves the
parsing engine combines files correctly; `test_stable_tab_order` proves the
controls stay put in the tab order. Both were green while the dialog in front
of a person was unusable:

  * Picking several files left the Import button disabled forever. The
    multi-file summary that goes in the source field ("(43 files selected)")
    is not a path, so the existence check in the file-changed handler rejected
    it and nothing ever parsed. You could select an archive and then not
    import it.

  * Choosing "weave everything together by date" did nothing at all. It was
    bound to a handler that returned at its first line, so the picker read as
    set while the import stayed in whole-conversation order — and the status
    line below it went on saying "conversations kept whole", the screen
    contradicting the control you had just used.

Neither is visible from the engine or from the tab order. Both are obvious
within two seconds of building the dialog and using it. So this test does that:
real files on disk, real event loop, the debounce timers and parse threads
actually running, and it asks what the person asks — is the button clickable,
and did the thing I picked change the answer.

Slower than a source scan by a couple of seconds. That is the price of asking
the only question that was ever going to catch this.
"""

import os
import sys
import tempfile
import pathlib

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="importlive-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label, flush=True)
    if not cond:
        _fails.append(label)


# OPT-IN, like every widget-building test here. This one goes furthest — it
# builds the dialog AND runs a real event loop — and a top-level wx window takes
# the FOREGROUND on Windows even unshown, with a screen reader following focus
# rather than visibility. `tests/run_all.py` also refuses to start this file
# without the flag; this gate is what makes running it directly deliberate too.
if os.environ.get("HEARTHKIN_GUI_TESTS", "").strip() not in ("1", "true", "yes"):
    print("SKIP -- builds real widgets, which take the foreground on the "
          "live desktop, and a screen reader follows focus. Run it safely on "
          "an isolated desktop with:")
    print("    python tests/_gui_runner.py " + __file__)
    sys.exit(0)

import wx  # noqa: E402
import dialogs.import_history as ih  # noqa: E402
from dialogs.import_history import ImportHistoryDialog  # noqa: E402


# Two Telegram-shaped exports whose turns interleave in time, so the two
# combine orders produce visibly different sequences: kept whole gives
# Jan, Mar, Feb, Apr (each thread intact); woven gives Jan, Feb, Mar, Apr.
_SRC = pathlib.Path(tempfile.mkdtemp(prefix="importlive-src-"))
(_SRC / "SpeakerFive_User.txt").write_text(
    "[01-01-2024 10:00:00] SpeakerFive: morning\n"
    "[01-03-2024 10:00:00] User: hello back\n",
    encoding="utf-8",
)
(_SRC / "Tarn_User.txt").write_text(
    "[01-02-2024 10:00:00] Tarn: a February thought\n"
    "[01-04-2024 10:00:00] User: reply in April\n",
    encoding="utf-8",
)
_PATHS = [str(_SRC / "SpeakerFive_User.txt"), str(_SRC / "Tarn_User.txt")]

# The dialog debounces file changes ~300ms and parses on a daemon thread, so
# each step waits before looking. Generous enough not to flake on a loaded
# machine, and the whole run is still a few seconds.
_SETTLE_MS = 1200

app = wx.App()
dlg = ImportHistoryDialog(None)


def dates():
    """Timestamps of the parsed messages, in the order they'd be imported."""
    if not dlg._parsed:
        return []
    return [m.get("ts", "?")[:10] for m in dlg._parsed[0]]


def fire_combine():
    """Send the same EVT_CHOICE wx sends when someone picks in the combo."""
    evt = wx.CommandEvent(wx.EVT_CHOICE.typeId, dlg.combine_choice.GetId())
    evt.SetEventObject(dlg.combine_choice)
    dlg.combine_choice.GetEventHandler().ProcessEvent(evt)


def step_select():
    dlg.kin_name_in_source.ChangeValue("SpeakerFive")
    dlg._set_sources(_PATHS)
    wx.CallLater(_SETTLE_MS, step_check_batch)


def step_check_batch():
    # The whole point of multi-select: having picked several files, you can
    # actually start the import.
    check("picking several files leaves the Import button clickable",
          dlg.import_btn.IsEnabled())
    check("...and every message from both files is there", len(dates()) == 4)
    check("...with conversations kept whole by default (Jan, Mar, Feb, Apr)",
          dates() == ["2024-01-01", "2024-01-03", "2024-01-02", "2024-01-04"])
    # The picker must not claim one conversation while the screen says two
    # files -- the UI arguing with itself in front of the person using it.
    shown = dlg.conv_choice.GetString(dlg.conv_choice.GetSelection())
    check("...and the conversation picker does not claim a single conversation",
          "one conversation" not in shown.lower())

    dlg.combine_choice.SetSelection(1)   # Weave everything together by date
    fire_combine()
    wx.CallLater(_SETTLE_MS, step_check_weave)


def step_check_weave():
    check("choosing 'weave by date' actually reorders the import",
          dates() == ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"])
    # The status field is PACED -- repainting it resets its caret and sends
    # NVDA back to the first line, so writes are spaced at least 4s apart and
    # the newest text sits pending until then. Flush to read what it will say.
    dlg._flush_display()
    check("...and the status line agrees with the control that was just used",
          "woven by date" in dlg.status_field.GetValue())
    check("...and the Import button is NOT paced -- it enables immediately",
          dlg.import_btn.IsEnabled())

    dlg.combine_choice.SetSelection(0)   # back to keeping conversations whole
    fire_combine()
    wx.CallLater(_SETTLE_MS, step_check_back)


def step_check_back():
    check("switching back to 'keep whole' reorders again -- the control is "
          "live in both directions",
          dates() == ["2024-01-01", "2024-01-03", "2024-01-02", "2024-01-04"])

    # A single file after a batch: _paths must clear, or the dialog keeps
    # reporting a batch that is no longer selected.
    dlg._set_sources([_PATHS[0]])
    wx.CallLater(_SETTLE_MS, step_check_single)


def step_check_single():
    check("dropping back to one file still parses and enables Import",
          dlg.import_btn.IsEnabled() and len(dates()) == 2)
    dlg._flush_display()
    check("...and the status line stops talking about a batch",
          "files," not in dlg.status_field.GetValue())
    step_check_pacing()


def step_check_pacing():
    """The feedback fields must be reachable, and must not repaint under you.

    Both halves matter and they pull against each other. A single-line
    read-only TextCtrl is not keyboard-focusable on wxMSW, so making these
    readable at all means making them multiline -- and a multiline field that
    repaints resets its caret to the top, which throws a screen reader back to
    the first line mid-sentence. A parse writes here three times in about a
    second, so without pacing the result is unreadable exactly when it lands.
    """
    check("the status field is reachable by Tab at all",
          dlg.status_field.CanAcceptFocusFromKeyboard())
    check("the detected-format field is reachable by Tab at all",
          dlg.format_display.CanAcceptFocusFromKeyboard())

    dlg._flush_display()
    settled = dlg.status_field.GetValue()
    dlg._show(status="TRANSIENT ONE")
    dlg._show(status="TRANSIENT TWO")
    check("a burst of writes does not repaint the field under you",
          dlg.status_field.GetValue() == settled)
    check("...and the newest one is what is waiting, not the first",
          dlg._pending_status == "TRANSIENT TWO")
    dlg._flush_display()
    check("...and it lands once the window passes",
          dlg.status_field.GetValue() == "TRANSIENT TWO")
    step_check_speech()


def step_check_speech():
    """The result has to reach you without you going looking for it.

    Making the field Tab-reachable means you CAN read it. It does not mean you
    ever find out there is something to read — WCAG's Status Messages
    criterion is specifically about status being available without taking
    focus, and speaking is how that is done here. The main window has had this
    pipe since it was written; this dialog never used it, so for its whole life
    everything it had to say sat silently in a field nobody could even Tab to.

    Speech is deliberately NOT paced. The four-second rule exists because
    repainting the field moves its caret and drags a screen reader back to the
    top; saying something aloud moves nothing, so the result is heard when it
    is known rather than up to four seconds later.
    """
    spoken = []
    ih.nvda_speak = lambda text: spoken.append(text)

    dlg._last_spoken = None
    dlg._show(status="Parsing…")
    check("routine progress is not announced — 'Parsing…' is not news",
          spoken == [])

    dlg._show(status="4 messages — 1 from the kin. From 2 files, woven by date.",
              speak=True)
    check("the settled result IS announced", len(spoken) == 1)
    check("...immediately, not held back by the repaint pacing",
          spoken and "woven by date" in spoken[0])

    dlg._show(status="4 messages — 1 from the kin. From 2 files, woven by date.",
              speak=True)
    check("...and saying the same thing twice does not repeat it",
          len(spoken) == 1)

    dlg._show(status="Parse failed: nothing readable", speak=True)
    check("failures are announced too", len(spoken) == 2)

    # A screen reader that isn't running must never break the import.
    def boom(_text):
        raise RuntimeError("no NVDA here")
    ih.nvda_speak = boom
    try:
        dlg._show(status="something else entirely", speak=True)
        survived = True
    except Exception:
        survived = False
    check("a failing screen reader cannot break the dialog", survived)

    finish()


def finish():
    dlg.Destroy()
    wx.CallLater(50, app.ExitMainLoop)


def bail():
    """Never hang the suite: if a step stopped chaining, fail loudly."""
    check("the dialog finished every step without stalling", False)
    finish()


wx.CallLater(200, step_select)
_watchdog = wx.CallLater(30000, bail)
app.MainLoop()
_watchdog.Stop()
app.Destroy()

if _fails:
    print(f"\ntest_import_dialog_live: {len(_fails)} FAILED", flush=True)
    for f in _fails:
        print("  - " + f, flush=True)
    sys.exit(1)
print("\ntest_import_dialog_live: all checks passed", flush=True)
