# SPDX-License-Identifier: CC0-1.0
"""Guard test: running the tests must not touch the runner's own kin state.

Before `HEARTHKIN_HOME` existed, every `python tests/run_all.py` appended a
synthetic failure into the REAL `~/.hearthkin/logs/save_failures.log` — the
token-calibration test feeds a deliberately-corrupt JSON file to prove the
loader ignores it, and the loader dutifully logged that to one of the always-on
diagnostic logs. Those logs are what the project reads first when something has
gone wrong, so salting them with suite noise costs the ability to recognise a
genuine save problem. A test must never mutate the runtime state of whoever
runs it.

Pinned here:
  * `HEARTHKIN_HOME` actually relocates the tree kin_persistence owns;
  * the legacy `~/.ollama_chat` migration is SKIPPED under an override — it
    renames the real legacy tree into CONFIG_DIR, which under a test sandbox
    would move a person's old install into a temp dir that is then deleted;
  * `run_all.py` still sets the sandbox, so the isolation can't quietly be
    dropped;
  * and the end-to-end property: running the offending test leaves the real
    log untouched.
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


def _probe(expr, home=None):
    """Import kin_persistence in a fresh process and print `expr`."""
    env = dict(os.environ)
    if home is None:
        env.pop("HEARTHKIN_HOME", None)
    else:
        env["HEARTHKIN_HOME"] = home
    out = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, r'{ROOT}');"
         f"import kin_persistence as k; print({expr})"],
        capture_output=True, text=True, env=env, cwd=str(ROOT))
    return out.stdout.strip(), out.returncode


# --- the override relocates the tree ------------------------------------

sandbox = tempfile.mkdtemp(prefix="hearthkin-isolation-")

got, rc = _probe("k.CONFIG_DIR", home=sandbox)
check("HEARTHKIN_HOME relocates CONFIG_DIR", rc == 0 and got == sandbox)

got, rc = _probe("k.LOGS_DIR", home=sandbox)
check("...so the always-on logs land in the sandbox, not the real home",
      rc == 0 and got.startswith(sandbox))

got, rc = _probe("k.AGENTS_DIR", home=sandbox)
check("...and so do the kin folders", rc == 0 and got.startswith(sandbox))

got, rc = _probe("k.CONFIG_DIR")
check("without it, the real ~/.hearthkin is used exactly as before",
      rc == 0 and got == str(Path.home() / ".hearthkin"))


# --- the legacy migration must not fire under an override ---------------
#
# _migrate_legacy RENAMES LEGACY_DIR (which always points into the person's
# REAL home) onto CONFIG_DIR when CONFIG_DIR doesn't exist yet. Under a test
# sandbox that means moving a real ~/.ollama_chat into a temp directory the
# suite deletes on the way out. This is the check that stops that.

import kin_persistence as k  # noqa: E402

fake_home = Path(tempfile.mkdtemp(prefix="hearthkin-legacy-"))
fake_legacy = fake_home / ".ollama_chat"
fake_legacy.mkdir()
(fake_legacy / "canary.txt").write_text("irreplaceable", encoding="utf-8")

_prev = (k._HOME_OVERRIDE, k.CONFIG_DIR, k.LEGACY_DIR)
try:
    k._HOME_OVERRIDE = str(fake_home / "sandbox")
    k.CONFIG_DIR = fake_home / "sandbox"      # deliberately does not exist
    k.LEGACY_DIR = fake_legacy
    k._migrate_legacy()
    check("an override leaves the real legacy tree where it is",
          fake_legacy.exists() and (fake_legacy / "canary.txt").exists())
    check("...and does not create the sandbox by moving it there",
          not (fake_home / "sandbox").exists())

    # Same setup WITHOUT the override still migrates — the guard must not have
    # broken first-run migration for real users.
    k._HOME_OVERRIDE = ""
    k._migrate_legacy()
    check("without an override, first-run migration still happens",
          (fake_home / "sandbox" / "canary.txt").exists())
finally:
    k._HOME_OVERRIDE, k.CONFIG_DIR, k.LEGACY_DIR = _prev


# --- the runner still sandboxes -----------------------------------------

runner = (HERE / "run_all.py").read_text(encoding="utf-8")
check("run_all.py still sets HEARTHKIN_HOME for its children",
      "HEARTHKIN_HOME" in runner)
check("...pointed at a temp dir, not a fixed path",
      "mkdtemp" in runner)
check("...and cleans it up afterwards", "rmtree" in runner)
# Source-level, because invoking run_all.py from inside a test it runs would
# recurse.
#
# The runner must NEVER accept a target directory. The only reason to name one
# is that it already holds something, and a directory that already holds
# something is exactly what a test run must not write into — so there is no
# safe version of the option and it must not exist. It is refused out loud
# rather than silently ignored, because a setting that looks obeyed and isn't
# is the worse of the two failures.
check("the sandbox is always freshly created, never a path handed in",
      "sandbox = tempfile.mkdtemp(" in runner
      and "HEARTHKIN_HOME=sandbox" in runner)
# A known parent makes --keep findable without hunting a deep temp path. The
# parent is only ever a container: mkdtemp(dir=parent) creates a new child per
# run, so no run writes into a directory that already held anything. A single
# fixed reused directory would defeat the point.
check("the sandbox parent is predictable, and gitignored",
      'os.path.join(HERE, ".state")' in runner
      and "/tests/.state/" in (ROOT / ".gitignore").read_text(encoding="utf-8"))
check("...but each run gets its OWN fresh child of it, never the parent",
      "mkdtemp(prefix=\"run-\", dir=parent)" in runner)
check("an inherited HEARTHKIN_HOME is refused, and said so",
      'os.environ.get("HEARTHKIN_HOME")' in runner
      and "Ignoring HEARTHKIN_HOME" in runner)
check("nothing in the runner can delete a directory it did not create",
      "rmtree(sandbox" in runner and runner.count("rmtree") == 1)


# --- end to end: the real log is untouched ------------------------------

real_log = Path.home() / ".hearthkin" / "logs" / "save_failures.log"
before = (real_log.stat().st_mtime_ns, real_log.stat().st_size) if real_log.exists() else None

env = dict(os.environ, HEARTHKIN_HOME=tempfile.mkdtemp(prefix="hearthkin-e2e-"))
run = subprocess.run([sys.executable, str(HERE / "test_token_calibration.py")],
                     capture_output=True, text=True, env=env, cwd=str(ROOT))
check("the calibration test still passes under the sandbox", run.returncode == 0)

after = (real_log.stat().st_mtime_ns, real_log.stat().st_size) if real_log.exists() else None
check("...and wrote nothing to the real save_failures.log", before == after)


# --- the override reaches the TOOLS layer, not just kin_persistence -----
#
# It didn't, for a long time, and this is what that cost. ~25 sites across
# tools/, park_*, memory_recall and cron_helpers each computed
# `Path.home() / ".hearthkin"` for themselves — they can't import
# kin_persistence, which imports tools._io — so the override was half a profile
# switch. `GameHost.save_path()` CREATES the kin folder it returns, and
# test_park_sharing.py asks it where kin named Solo, Blank and Broken would keep
# a park. Three folders that were never anyone's kin appeared in a real kin
# list, in among the real ones, on every suite run.
#
# The decision now lives in `hearthkin_paths`, which depends on nothing in the
# project, so both sides can sit on the same answer.

import hearthkin_paths  # noqa: E402

probe_home = tempfile.mkdtemp(prefix="hearthkin-tools-")
probe = subprocess.run(
    [sys.executable, "-c",
     f"import sys; sys.path.insert(0, r'{ROOT}');"
     "from tools import get_game;"
     "print(get_game('tff').save_path('ZZ-Probe-Kin'))"],
    capture_output=True, text=True, cwd=str(ROOT),
    env=dict(os.environ, HEARTHKIN_HOME=probe_home))
check("the tools layer honours HEARTHKIN_HOME too",
      probe.returncode == 0 and probe.stdout.strip().startswith(probe_home))
check("...so asking where a kin's park is creates nothing in the real home",
      not (Path.home() / ".hearthkin" / "kin" / "ZZ-Probe-Kin").exists())

# End to end, against the test that actually did it. Compare the whole listing
# rather than a count: a run that added one folder and removed another would
# tie.
real_kin = Path.home() / ".hearthkin" / "kin"
before_kin = sorted(p.name for p in real_kin.iterdir()) if real_kin.exists() else []
run = subprocess.run([sys.executable, str(HERE / "test_park_sharing.py")],
                     capture_output=True, text=True, cwd=str(ROOT),
                     env=dict(os.environ,
                              HEARTHKIN_HOME=tempfile.mkdtemp(prefix="hearthkin-park-")))
check("the park-sharing test still passes under the sandbox", run.returncode == 0)
after_kin = sorted(p.name for p in real_kin.iterdir()) if real_kin.exists() else []
check("...and left the real kin list exactly as it found it",
      before_kin == after_kin)


# ── A test file run ON ITS OWN also gets a sandbox ────────────────────
#
# `run_all.py` hands every child a fresh HEARTHKIN_HOME, and does it in the
# runner rather than trusting each file to remember. But a test file also gets
# run standalone -- that is how you run one while you are working on it -- and
# nothing covered that path. Those runs wrote into the REAL kin folder, which
# is where a row of kin nobody created came from: folders named after fixtures,
# sitting in among the real ones.
#
# Cosmetic only for as long as no fixture name collides with a real kin. Some
# of those names are perfectly ordinary things to call one, and the day that
# happens a standalone test run writes into somebody's actual kin.
#
# Two properties, and the second matters more than the first.


def _probe(argv0_name, parent_dirname, env_home=None):
    """Ask hearthkin_paths where state lives, from a process whose script name
    is `parent_dirname/argv0_name`. Returns the path it answered."""
    box = Path(tempfile.mkdtemp(prefix="hearthkin-probe-"))
    dirn = box / parent_dirname
    dirn.mkdir(parents=True, exist_ok=True)
    script = dirn / argv0_name
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "import hearthkin_paths\n"
        "print(hearthkin_paths.config_dir())\n",
        encoding="utf-8")
    env = dict(os.environ)
    env.pop("HEARTHKIN_HOME", None)
    if env_home:
        env["HEARTHKIN_HOME"] = env_home
    run = subprocess.run([sys.executable, str(script)],
                         capture_output=True, text=True, cwd=str(ROOT), env=env)
    return run.stdout.strip(), run.returncode


real_home = Path.home() / ".hearthkin"

# A standalone test file, no HEARTHKIN_HOME set: must NOT land on the real tree.
out, rc = _probe("test_probe.py", "tests")
check("a standalone test file answers at all", rc == 0)
check("...and is NOT pointed at the real kin tree",
      bool(out) and Path(out) != real_home)
check("...it got a throwaway directory instead",
      "hearthkin-standalone-" in out)

# THE ONE THAT MATTERS: the real app must never be sandboxed by this. A
# heuristic that quietly redirected someone's actual install would hide every
# kin they have, and look exactly like data loss.
out_app, rc_app = _probe("hearthkin.pyw", "app")
check("the real app still answers", rc_app == 0)
check("...and still gets the REAL tree, not a sandbox",
      Path(out_app) == real_home)
check("...with no sandbox anywhere in the answer",
      "hearthkin-standalone-" not in out_app)

# An explicit override still wins over the heuristic, from either kind of
# process -- the sandbox is a fallback, never an override of the override.
forced = tempfile.mkdtemp(prefix="hearthkin-forced-")
out_f, _ = _probe("test_probe.py", "tests", env_home=forced)
check("an explicit HEARTHKIN_HOME still wins for a test file",
      Path(out_f) == Path(forced))


print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_state_isolation: all checks passed")
