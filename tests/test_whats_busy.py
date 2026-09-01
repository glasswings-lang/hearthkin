# SPDX-License-Identifier: CC0-1.0
"""Guard test: you can ask the app what it's doing, without quitting it.

Reported after a kin took nine minutes to answer: *"Not sure if that's because
there's a distillation going on or what, and so far the only way to check from
within hearthkin is to quit it and force it to surface that way."*

That was accurate. `_work_in_flight()` has always known — it phrases the answer
in whole sentences ("<kin> is saving notes to its memory") and the heartbeat
writes those same sentences to a log every minute. But the only thing that ever
ASKED it was the quit prompt. So the way to find out whether a reply was stuck
behind a 16-minute memory write was to start closing the app and read the
warning, then cancel. For a question worth asking often, that is a bad deal, and
the alternative — watching a status bar that says nothing — is worse.

Chat → "What's it busy with?" (Ctrl+B) asks it directly, and SPEAKS the answer.
Spoken because the moment you want to ask is exactly the moment nothing appears
to be happening, and a status bar is somewhere you have to go and look.

Pinned here: every shape of answer reads as a sentence, the menu entry exists
and is wired, and nothing about it can raise — the honest answer to a broken
probe is "I can't tell", never a traceback on a keystroke.

Run: python tests/test_whats_busy.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="busy-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import frame.lifecycle_mixin as LM  # noqa: E402

_said = []
LM.nvda_speak = lambda text: _said.append(text)


class _Frame:
    """Just enough frame to run the handler, like the walk-pacing tests do —
    the REAL method, unbound, so this exercises what ships."""
    _on_whats_busy = LM.LifecycleMixin._on_whats_busy

    def __init__(self, busy):
        self._busy = busy
        self.status = []

    def _work_in_flight(self):
        if isinstance(self._busy, Exception):
            raise self._busy
        return self._busy

    def SetStatusText(self, text):
        self.status.append(text)


def ask(busy):
    _said.clear()
    f = _Frame(busy)
    f._on_whats_busy(None)
    return f, (_said[-1] if _said else None)


# --- idle is a real answer, and must be given ---------------------------
# Silence would be indistinguishable from the key not working.
f, said = ask([])
check("idle says so out loud", said == "Nothing is running. It's idle.")
check("...and puts the same words in the status bar", f.status == [said])

# --- one thing ----------------------------------------------------------
f, said = ask(["Alder is saving notes to its memory"])
check("one job is read back as a sentence",
      said == "Alder is saving notes to its memory.")

# --- several ------------------------------------------------------------
# The real case behind the report: a distillation and a reply at once, which
# is exactly what makes a kin look like it has stopped responding.
f, said = ask(["Alder is saving notes to its memory",
               "Bramble is part-way through a reply on Telegram"])
check("two jobs are joined with 'and', not as a bare list",
      said == ("Alder is saving notes to its memory, and Bramble is "
               "part-way through a reply on Telegram."))

f, said = ask(["A is distilling", "B is replying", "C is tending its park"])
check("three jobs read as speech, not as a comma soup",
      said == "A is distilling, B is replying, and C is tending its park.")

# --- nothing here may raise ---------------------------------------------
_raised = None
try:
    f, said = ask(RuntimeError("probe exploded"))
except Exception as e:                                    # pragma: no cover
    _raised = e
check("a broken probe does not raise on a keystroke", _raised is None)
check("...and says it can't tell, rather than claiming idle",
      said == "I can't tell what's running just now.")


class _NoStatusBar(_Frame):
    def SetStatusText(self, text):
        raise RuntimeError("no status bar yet")


_said.clear()
_raised = None
try:
    _NoStatusBar(["X is busy"])._on_whats_busy(None)
except Exception as e:                                    # pragma: no cover
    _raised = e
check("a failing status bar doesn't swallow the spoken answer",
      _raised is None and _said == ["X is busy."])


# --- the menu entry exists and is wired ---------------------------------
# Source-level: building the menu needs a real frame, and a wx window on the
# live desktop takes the foreground (see CLAUDE.md).
_menus = (ROOT / "frame" / "menus_mixin.py").read_text(encoding="utf-8")
check("the Chat menu offers it", "What's it &busy with?" in _menus)
check("...on Ctrl+B", "Ctrl+B" in _menus)
check("...and it's bound to the handler",
      "self._on_whats_busy, self.mnu_whats_busy" in _menus)
check("the handler it names actually exists",
      hasattr(LM.LifecycleMixin, "_on_whats_busy"))


print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_whats_busy: all checks passed")
