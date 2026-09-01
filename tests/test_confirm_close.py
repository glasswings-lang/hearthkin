# SPDX-License-Identifier: CC0-1.0
"""Guard test: quitting while a kin is still working asks first.

Closing Hearthkin abandons in-flight model calls, stops the bots and cancels
the timers. That's correct when nothing is happening, and quietly costly when
something is — and there was no way to KNOW which. The only signals available
were remembering to check or hearing the machine's fans, neither of which
reaches you for a kin running on another machine, or at all if you're not in
the room.

`_work_in_flight` is the answer to "what would quitting cost right now". It's
tested here without wxPython — the frame method is bound to a stand-in holding
just the state it reads, so this runs anywhere.

The invariants that matter most are the two failure directions:
  * silent when nothing is happening (a prompt on every close is a prompt
    people learn to dismiss unread, and then it protects nothing); and
  * NEVER able to prevent quitting. Every probe is guarded, and a fault
    anywhere fails open. A blocked quit is a worse bug than a missing prompt —
    this app has a history of "Ctrl+Q does nothing" hangs.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import frame.lifecycle_mixin as _lm  # noqa: E402
from frame.lifecycle_mixin import LifecycleMixin  # noqa: E402


class _FakeCronHelpers:
    """Stands in for the cross-process marker reader."""

    def __init__(self, running=()):
        self._running = list(running)

    def cron_running_kin(self):
        return list(self._running)


_real_ch = _lm.cron_helpers
_lm.cron_helpers = _FakeCronHelpers()

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


class FakeBot:
    def __init__(self, label=None):
        self._label = label

    def active_turn_label(self):
        return self._label


class ExplodingBot:
    def active_turn_label(self):
        raise RuntimeError("bot is in a bad way")


class Frame:
    """Only the attributes _work_in_flight reads."""

    _work_in_flight = LifecycleMixin._work_in_flight

    def __init__(self, **kw):
        self._streaming = False
        self.current_agent = "Opal"
        self._room_active = False
        self.current_room = None
        self._distilling = {}
        self._cron_workers = set()
        self._heartbeat_workers = set()
        self.bots = {}
        self._pending_approvals = []
        for k, v in kw.items():
            setattr(self, k, v)


# --- silence when nothing is happening ----------------------------------

check("an idle Hearthkin reports nothing in flight, so no prompt appears",
      Frame()._work_in_flight() == [])


# --- each kind of work is named -----------------------------------------

got = Frame(_streaming=True, current_agent="Bracken")._work_in_flight()
check("a desktop reply mid-stream is named, with the kin",
      len(got) == 1 and "Bracken" in got[0])
# "here" was unresolvable read aloud — inside a dialog it can even sound like
# "in this dialog". Say the place.
check("...and says WHERE, not \"here\"",
      "main window" in got[0] and " here" not in got[0])

got = Frame(_room_active=True, current_room="hearth")._work_in_flight()
check("a room mid-round is named, with the room",
      len(got) == 1 and "hearth" in got[0])

got = Frame(_distilling={"Vesper": 123.0})._work_in_flight()
check("a distillation is named — long, silent, and costly to abandon",
      len(got) == 1 and "Vesper" in got[0] and "memory" in got[0])
check("...without the word \"distillation\", which NVDA garbles in parentheses",
      "(" not in got[0])

got = Frame(_cron_workers={("Opal", "07:30")})._work_in_flight()
check("a cron wake-up is named, with the time it fired",
      len(got) == 1 and "Opal" in got[0] and "07:30" in got[0] and "(" not in got[0])

# Both of these were MISSED in the first version, and both were caught the only
# way they could be — by quitting during one and watching it close in silence.
# A heartbeat runs on its own thread and registered nothing; a cron wake-up can
# run in a separate PROCESS, which shares no state with the frame at all.
got = Frame(_heartbeat_workers={"Vesper"})._work_in_flight()
check("a kin mid-heartbeat is named (was invisible, closed silently)",
      len(got) == 1 and "Vesper" in got[0] and "reach out" in got[0])

got = Frame(_heartbeat_workers={"Vesper", "Tarn"})._work_in_flight()
check("...all of them, in a stable order",
      len(got) == 2 and got[0].startswith("Tarn") and got[1].startswith("Vesper"))

got = Frame(bots={"Opal": FakeBot("Opal is part-way through a reply to SpeakerSeven "
                                  "on Telegram")})._work_in_flight()
check("a kin mid-reply on Telegram is named — the case with no local signal",
      len(got) == 1 and "Telegram" in got[0])

got = Frame(_pending_approvals=[object(), object()])._work_in_flight()
check("approvals waiting on you are counted and pluralised",
      len(got) == 1 and "2 tool approvals" in got[0])

got = Frame(_pending_approvals=[object()])._work_in_flight()
check("...and one is singular", "1 tool approval " in got[0])


# --- everything at once, and quiet bots ---------------------------------

got = Frame(
    _streaming=True, current_agent="Bracken",
    _distilling={"Vesper": 1.0, "Opal": 2.0},
    _cron_workers={("Tarn", "06:00")},
    bots={"a": FakeBot("Opal mid-reply on Telegram"), "b": FakeBot(None)},
    _pending_approvals=[object()],
)._work_in_flight()
check("every kind of work is listed together", len(got) == 6)
check("...and a bot with nothing in flight adds no line",
      not any(line is None for line in got))
check("...with distillations in a stable order (no jitter between opens)",
      got.index("Opal is saving notes to its memory")
      < got.index("Vesper is saving notes to its memory"))


# A cron wake-up in the standalone subprocess reports itself by marker file.
_lm.cron_helpers = _FakeCronHelpers([("Bracken", "12:00")])
got = Frame()._work_in_flight()
check("an out-of-process cron wake-up is seen through its marker file",
      len(got) == 1 and "Bracken" in got[0] and "12:00" in got[0])
_lm.cron_helpers = _FakeCronHelpers()


# --- fails open: nothing here can block a quit --------------------------

got = Frame(bots={"bad": ExplodingBot()})._work_in_flight()
check("a bot that raises is skipped, not fatal", got == [])

got = Frame(bots={"bad": ExplodingBot(),
                  "good": FakeBot("Vesper mid-reply on Telegram")})._work_in_flight()
check("...and does not stop the other bots being reported", len(got) == 1)


class Hostile:
    """Every attribute access explodes — the worst case for a method that
    must never prevent the app from closing."""

    _work_in_flight = LifecycleMixin._work_in_flight

    def __getattr__(self, name):
        raise RuntimeError(f"no {name}")


try:
    got = Hostile()._work_in_flight()
    survived = True
except Exception:
    got, survived = None, False
check("even a totally broken frame returns a list rather than raising",
      survived and got == [])


# --- what gets SPOKEN on open -------------------------------------------
#
# A context-free reader given only the announced text couldn't tell what was
# being decided: the first draft spoke the inventory and nothing else, so no
# spoken word was "quit" or "close". Someone who hit the shortcut by accident
# would hear a list of kin names and a focused button, with no idea a quit was
# underway — and the stakes sat behind a Tab press nobody has a reason to make
# while focus is on a button and the situation sounds fully described.
#
# _announcement is pure string work, so it's testable without wx. The class
# isn't imported (that would need wxPython); the method is bound to a stand-in.
from dialogs.confirm_close import ConfirmCloseDialog  # noqa: E402


class Spoken:
    _announcement = ConfirmCloseDialog._announcement

    def __init__(self, lines):
        self.busy_lines = lines


said = Spoken(["Bracken is part-way through a reply in the main window",
               "Vesper is saving notes to its memory"])._announcement()
check("the announcement says what is being decided, in words",
      "Quitting" in said)
check("...and what the safe answer costs, which is nothing",
      "staying open changes nothing" in said.lower())
check("...before naming anything — the decision leads, inventory follows",
      said.index("Quitting") < said.index("Bracken"))
check("...and still names the work", "Bracken" in said and "Vesper" in said)

one = Spoken(["Opal is saving notes to its memory"])._announcement()
check("one item reads as singular", "one thing" in one and "1 things" not in one)

many = Spoken([f"kin{i} is doing something" for i in range(7)])._announcement()
check("a long list is truncated rather than becoming one huge utterance",
      "kin0" in many and "kin6" not in many)
check("...and says how many were left out, and where to find them",
      "4 more" in many and "listed in this window" in many)
check("...while the total is still stated up front", "7 things" in many)


# --- the close path fails open too --------------------------------------
#
# Source-level: exercising _confirm_close_while_busy needs a real wx modal.
# These pin the properties that keep a bug here from becoming an app that
# won't quit.
src = (Path := __import__("pathlib").Path)(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
) / "frame" / "lifecycle_mixin.py"
text = src.read_text(encoding="utf-8")

check("an unvetoable close (system shutdown, installer) is never blocked",
      "if not event.CanVeto():\n                return True" in text)
check("no work in flight returns immediately, before building any dialog",
      "if not busy:\n                return True" in text)
check("any exception at all still lets the app close",
      "except Exception as e:" in text
       and text.split("confirm_close_while_busy")[2].split("return")[1].strip().startswith("True"))
check("the dialog is destroyed even if ShowModal raises",
      "finally:\n                dlg.Destroy()" in text)
check("the check runs BEFORE _closing is set, so 'wait' is truthful",
      text.index("_confirm_close_while_busy(event)")
      < text.index("self._closing = True"))
check("only an explicit 'close anyway' proceeds; anything else vetoes",
      "if answer == wx.ID_OK:" in text and "event.Veto()" in text)


print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_confirm_close: all checks passed")
