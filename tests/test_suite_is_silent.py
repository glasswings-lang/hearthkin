# SPDX-License-Identifier: CC0-1.0
"""Guard test: running the tests must not speak, and must not make a sound.

Tests drive real handlers, and real handlers talk. `test_distill_walk_pacing`
calls the actual "Cancel distilling" handler seven times to check what it does
with a paced walk — and that handler ends with
`nvda_speak("Distilling cancelled. ...")`. So a suite run announced
"Distilling cancelled. Progress kept." four times in a row into a live screen
reader, on top of whatever the person was actually reading. Reported as being
"spammed out", and it took a wrong guess (that the app was doing it) before the
tests turned out to be the source.

Nothing was wrong with the test or with the handler. They were simply never
meant to meet a real speech channel, and no amount of care in either one would
have noticed: a test cannot be expected to know that some handler five calls
down finishes at the person's ears.

So it is enforced at the two choke points every announcement and every cue goes
through — `audio.nvda_speak` and `audio._play_async` — not by asking each test
to patch what it might reach. Same shape as the widget/foreground gate living
in the runner rather than in each test file.

Pinned here:
  * the silence check is ON while a test is running, by name and by env var;
  * the real speech path is genuinely not reached — measured against a spy in
    place of the NVDA library, with a POSITIVE CONTROL first, so that a zero
    means "nothing spoke" rather than "the spy was never wired up";
  * the same for sound;
  * and the runner sets it for every child.

Run: python tests/test_suite_is_silent.py
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import audio  # noqa: E402

# --- the check itself ---------------------------------------------------

check("this process counts as a test run, so it is silent",
      audio._suppressed())

_saved = os.environ.pop("HEARTHKIN_SILENT", None)
try:
    check("...recognised by the script name alone, with no env var set",
          audio._suppressed())
    # And having noticed, it must tell its children — several tests shell out
    # to a fresh interpreter, and `python -c ...` has no test-looking name.
    check("...and it exports the flag so subprocesses inherit the silence",
          os.environ.get("HEARTHKIN_SILENT") == "1")
finally:
    if _saved is not None:
        os.environ["HEARTHKIN_SILENT"] = _saved

# A second profile is a legitimate way to run the REAL app. Silencing on that
# would mute someone's actual screen reader output.
check("HEARTHKIN_HOME alone does not silence the app",
      "HEARTHKIN_HOME" not in (audio._suppressed.__doc__ or ""))


# --- the speech path, measured against a spy ----------------------------
# The instrument is checked against a known positive BEFORE its zero is
# believed. A spy that was never wired up reports silence too.

class _SpyDLL:
    def __init__(self):
        self.said = []

    def nvdaController_speakText(self, text):
        self.said.append(text)


_spy = _SpyDLL()
_real_dll, audio._nvda_dll = audio._nvda_dll, _spy
_real_suppressed = audio._suppressed
try:
    audio._suppressed = lambda: False
    audio.nvda_speak("positive control")
    check("the spy DOES catch speech when nothing is suppressing it",
          _spy.said == ["positive control"])

    audio._suppressed = _real_suppressed
    audio.nvda_speak("this must not be said")
    check("...and catches nothing once the real check is back",
          _spy.said == ["positive control"])
finally:
    audio._suppressed = _real_suppressed
    audio._nvda_dll = _real_dll


# --- the sound path -----------------------------------------------------

class _SpyWinsound:
    SND_MEMORY = 4
    SND_FILENAME = 0x20000

    def __init__(self):
        self.played = 0

    def PlaySound(self, *a, **k):
        self.played += 1

    def Beep(self, *a, **k):
        self.played += 1


_snd = _SpyWinsound()
_real_ws, audio.winsound = getattr(audio, "winsound", None), _snd
try:
    audio._play_async("chime", lambda: b"", 1.0)
    check("no cue is played during a test run", _snd.played == 0)
finally:
    audio.winsound = _real_ws


# --- end to end: the test that actually did it --------------------------
# Runs the offending file in a child whose NVDA library is a spy, and asks how
# many times it was reached. The positive control runs in the same child, so a
# zero here cannot be a wiring failure.

_child = r'''
import os, sys, runpy, json
sys.path.insert(0, r"{root}")
import audio

class Spy:
    def __init__(self): self.said = []
    def nvdaController_speakText(self, t): self.said.append(t)

spy = Spy()
audio._nvda_dll = spy
real = audio._suppressed
audio._suppressed = lambda: False
audio.nvda_speak("control")
audio._suppressed = real
control_ok = spy.said == ["control"]

sys.argv = [r"{target}"]
try:
    runpy.run_path(r"{target}", run_name="__main__")
except SystemExit:
    pass
sys.stderr.write("RESULT " + json.dumps(
    {{"control_ok": control_ok, "spoken": spy.said[1:]}}) + "\n")
'''.format(root=str(ROOT), target=str(HERE / "test_distill_walk_pacing.py"))

_run = subprocess.run(
    [sys.executable, "-c", _child], capture_output=True, text=True,
    env=dict(os.environ,
             HEARTHKIN_HOME=tempfile.mkdtemp(prefix="silent-e2e-"),
             HEARTHKIN_SILENT="1"))

_line = [l for l in (_run.stderr or "").split("\n") if l.startswith("RESULT ")]
if not _line:
    check("the end-to-end silence probe ran at all", False)
    print("      child stderr:", (_run.stderr or "")[-400:])
else:
    import json
    _data = json.loads(_line[0][len("RESULT "):])
    check("the child's spy works (positive control spoke)",
          _data.get("control_ok") is True)
    check("...and test_distill_walk_pacing said NOTHING through it",
          _data.get("spoken") == [])
    if _data.get("spoken"):
        print("      it said:", _data["spoken"][:5])


# --- the runner sets it too ---------------------------------------------

runner = (HERE / "run_all.py").read_text(encoding="utf-8")
check("run_all.py silences every child it starts",
      'HEARTHKIN_SILENT="1"' in runner)


# --- the one deliberate exception ---------------------------------------
# A suite verdict is a line of terminal output that scrolls past, which is no
# use to someone reading by screen reader: the suite was run and nothing came
# back at all. So run_all says the result out loud, once, at the end. That is
# the opposite of the problem this file exists to prevent — the problem is
# incidental chatter from handlers, dozens of times, mid-run.
#
# The exception has to stay exactly that narrow, so: it is used at the END of
# the runner, and by NO test.

check("run_all announces the verdict out loud at the end",
      "_announce(" in runner and "speak_result" in runner)
check("...for a failure as well as a pass — silence must not read as green",
      runner.count("_announce(") >= 2)
check("...and it can't take the run down with it",
      "except Exception:" in runner.split("def _announce")[1])

_leaks = []
for _p in sorted(HERE.glob("test_*.py")):
    _src = _p.read_text(encoding="utf-8", errors="replace")
    # This file names it to forbid it; that mention is inside a string.
    if "speak_result(" in _src and _p.name != Path(__file__).name:
        _leaks.append(_p.name)
check("no TEST file speaks through the result channel", not _leaks)
if _leaks:
    print("      leaked into:", ", ".join(_leaks))

# And the exception really is an exception: it must work while suppressed,
# otherwise the verdict would be swallowed by the very rule it sits beside.
_spy2 = _SpyDLL()
_real2, audio._nvda_dll = audio._nvda_dll, _spy2
try:
    check("this process is still suppressed for ordinary speech",
          audio._suppressed())
    audio.nvda_speak("must not be heard")
    audio.speak_result("verdict")
    check("...yet the verdict gets through, and only the verdict",
          _spy2.said == ["verdict"])
finally:
    audio._nvda_dll = _real2


print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_suite_is_silent: all checks passed")
