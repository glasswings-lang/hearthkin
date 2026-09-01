"""Run every test_*.py in this folder and report one aggregate result.

Each test file is a standalone plain-Python script (no pytest) that prints
PASS/FAIL lines and exits non-zero if anything failed. This runner just
executes each one and tallies the exit codes, so there's a single command
to prove the whole suite is green.

Run:  python tests/run_all.py
"""

import os
import re
import sys
import glob
import shutil
import tempfile
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


# --- widget-building tests are opt-in, and the RUNNER decides -------------
#
# Creating a top-level wx window TAKES THE FOREGROUND on Windows even though
# the window is never shown: right after building a dialog, GetForegroundWindow()
# returns that dialog's own handle while both wx.IsShown() and Win32
# IsWindowVisible() report it hidden. Hearthkin sharpens this deliberately — it
# disables Windows' foreground lock at startup so an approval dialog can come to
# the front — so on the machine this project is FOR, any widget-building test
# grabs focus. A screen reader follows focus, not visibility, so a suite run
# drags NVDA into an invisible window with nothing to read and no clear way out.
#
# That rule was written down, and then two later tests were added without the
# opt-in gate and shipped stealing focus on every run. So the gate no longer
# depends on each test file remembering: the runner reads the source, and a file
# that pulls in wx is handled by the runner, not by its own good intentions.
#
# Handled how, though. An opt-in flag was the first answer and it was a bad one:
# the person this project is for runs a screen reader at all times and therefore
# can never set it, so the flag locked the only person who needs these tests out
# of them. Coverage that exists only for people who don't need it isn't coverage.
#
# So they now RUN, on a Windows desktop with no input attached (see
# _isolated_desktop.py). Windows there are real and fully testable but have no
# path to anyone's foreground. Skipping is the fallback for when that isn't
# possible — not the plan.
#
# Anchored to the start of a line so a test that merely mentions the import in a
# docstring or a string literal isn't misread as one that builds windows.
_IMPORTS_WX_RE = re.compile(r"^[ \t]*(?:import|from)[ \t]+wx\b", re.M)


def builds_widgets(source):
    """True if this test file pulls in wxPython, and so may take the foreground.

    Deliberately a source scan rather than a per-file opt-in flag: the property
    we need is "nothing in a default run can steal focus", and a check that each
    test has to remember to apply is exactly the check that gets forgotten."""
    return bool(_IMPORTS_WX_RE.search(source))


def gui_tests_enabled():
    """The old opt-in. Now only a fallback for machines where the desktop can't
    be isolated — it is not something the person this project is for can ever
    set, since their screen reader is always running."""
    return (os.environ.get("HEARTHKIN_GUI_TESTS", "").strip().lower()
            in ("1", "true", "yes"))


def desktop_isolation_available():
    """Can widget tests be run where they cannot reach anyone's foreground?

    Asked in a THROWAWAY PROCESS on purpose. `enter_isolated_desktop()` moves
    the calling thread, and this runner's own thread must stay where the person
    is — it prints there. Probing in-process would answer the question by
    doing the thing to the wrong process."""
    probe = os.path.join(HERE, "_isolated_desktop.py")
    if not os.path.exists(probe):
        return False
    try:
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, r'%s');"
             "import _isolated_desktop as d;"
             "print('YES' if d.enter_isolated_desktop() else 'NO')" % HERE],
            capture_output=True, text=True, timeout=60)
    except Exception:
        return False
    return "YES" in (out.stdout or "")


def main():
    files = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
    if not files:
        print("No test_*.py files found.", flush=True)
        return 0
    # Every child runs against a throwaway ~/.hearthkin. Running the suite must
    # not touch the runtime state of the person running it: before this, the
    # token-calibration test's deliberately-corrupt JSON was logged into the
    # REAL logs/save_failures.log on every run, salting one of the always-on
    # diagnostic logs with synthetic failures until a genuine save problem was
    # indistinguishable from suite noise. See kin_persistence.HEARTHKIN_HOME.
    #
    # The sandbox is ALWAYS one this runner just created, and an inherited
    # HEARTHKIN_HOME is refused rather than obeyed.
    #
    # Accepting a target directory sounds like a courtesy and is a data-loss
    # risk with no upside. The only reason to name a directory is that it
    # already holds something — a profile with particular kin on disk — and a
    # directory that already holds something is precisely the one a test run
    # must never write into. There is no safe version of "write my tests into
    # your existing state", so the option doesn't exist. Refusing out loud
    # rather than silently substituting, because a setting that appears to be
    # obeyed but isn't is the worse failure of the two.
    #
    # If you need to see what a run produced, pass --keep: the sandbox path is
    # printed and left on disk. That covers the real need without ever
    # pointing the suite at data someone cares about.
    # A KNOWN parent, a FRESH child. `tests/.state/` is predictable — findable
    # without hunting through a deep temp path, which matters when --keep leaves
    # one behind to inspect — and gitignored, so runs never dirty the tree. But
    # the suite never writes into the parent itself: mkdtemp makes a new
    # subdirectory per run, atomically, failing rather than reusing. A single
    # fixed directory reused every run would be a pre-existing directory being
    # written into, which is the thing this whole arrangement exists to avoid.
    keep = "--keep" in sys.argv
    inherited = (os.environ.get("HEARTHKIN_HOME") or "").strip()
    if inherited:
        print(f"Ignoring HEARTHKIN_HOME={inherited}", flush=True)
        print("The suite only ever writes to a directory it just created — "
              "running tests against an existing state directory risks the "
              "data in it. Use --keep to inspect the sandbox instead.",
              flush=True)
    parent = os.path.join(HERE, ".state")
    os.makedirs(parent, exist_ok=True)
    sandbox = tempfile.mkdtemp(prefix="run-", dir=parent)
    # HEARTHKIN_SILENT: tests drive real handlers, and real handlers speak and
    # chime. The cancel-distilling handler ends in nvda_speak, so a suite run
    # announced "Distilling cancelled. Progress kept." four times over whatever
    # the person was reading. Silence is set for every child here, and enforced
    # again inside audio.py — a test cannot be expected to know which handler
    # five calls down ends at the speech channel.
    env = dict(os.environ, HEARTHKIN_HOME=sandbox, HEARTHKIN_SILENT="1")
    if keep:
        print(f"Sandbox kept at {sandbox}", flush=True)
    failed = []
    skipped_gui = []
    isolated_gui = []
    # Probed once, in a throwaway process, so this runner never moves its own
    # thread off the desktop the person is using.
    can_isolate = desktop_isolation_available()
    allow_gui = gui_tests_enabled()
    gui_runner = os.path.join(HERE, "_gui_runner.py")
    try:
        for path in files:
            name = os.path.basename(path)
            cmd = [sys.executable, path]
            try:
                source = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                source = ""
            if builds_widgets(source):
                if can_isolate:
                    # Run it, on a desktop that has no input attached.
                    cmd = [sys.executable, gui_runner, path]
                    isolated_gui.append(name)
                elif not allow_gui:
                    skipped_gui.append(name)
                    continue
            print(f"\n===== {name} =====", flush=True)
            result = subprocess.run(cmd, env=env)
            if result.returncode != 0:
                failed.append(name)
    finally:
        # Safe to remove unconditionally: `sandbox` is always a mkdtemp this
        # function made moments ago, never a path anyone handed us.
        if not keep:
            shutil.rmtree(sandbox, ignore_errors=True)

    print("\n" + ("=" * 50), flush=True)
    if isolated_gui:
        print(f"Ran {len(isolated_gui)} wx test file(s) on an isolated desktop, "
              f"where their windows cannot reach your foreground: "
              + ", ".join(isolated_gui), flush=True)
    if skipped_gui:
        # Announced, never silent: a test that quietly doesn't run is a test you
        # believe you have. Named individually so it's obvious which coverage is
        # missing and why.
        print(f"Skipped {len(skipped_gui)} test file(s) that build wx windows: "
              + ", ".join(skipped_gui), flush=True)
        print("The desktop could not be isolated on this machine, and a wx "
              "window on the live desktop takes the foreground even unshown — "
              "which drags a screen reader into a window it can't read.",
              flush=True)
    if failed:
        print(f"SUITE FAILED - {len(failed)} file(s): " + ", ".join(failed), flush=True)
        _announce(f"Suite failed. {len(failed)} file"
                  f"{'s' if len(failed) != 1 else ''}: "
                  + ", ".join(_spoken_name(f) for f in failed) + ".")
        return 1
    ran = len(files) - len(skipped_gui)
    print(f"SUITE GREEN - {ran} test file(s) passed", flush=True)
    _announce(f"Suite green. {ran} test files passed."
              + (f" {len(skipped_gui)} skipped." if skipped_gui else ""))
    return 0


def _spoken_name(filename):
    """A test filename as something worth listening to: no .py, no test_
    prefix, underscores as spaces. 'test_park_sharing.py' reads back as
    'park sharing' rather than being spelled at you."""
    stem = filename[:-3] if filename.endswith(".py") else filename
    if stem.startswith("test_"):
        stem = stem[len("test_"):]
    return stem.replace("_", " ")


def _announce(text):
    """Say the verdict out loud, once, at the very end.

    The result of a suite run is a line of terminal output that scrolls past —
    no use to someone reading by screen reader, who ran the suite and got
    nothing back at all. This is the one deliberate announcement a test run
    makes; everything else stays silent (see audio._suppressed).

    Best-effort in every direction: no NVDA, no audio module, any error at all
    — the exit code and the printed line are still the real answer, and a
    runner that crashed while trying to be helpful would be worse than one
    that said nothing."""
    try:
        sys.path.insert(0, os.path.dirname(HERE))
        from audio import speak_result
        speak_result(text)
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
