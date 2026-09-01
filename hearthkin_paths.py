# SPDX-License-Identifier: CC0-1.0
"""Where the runtime state lives — the one place that decides.

`HEARTHKIN_HOME` used to relocate only `kin_persistence.CONFIG_DIR`. Everything
in `tools/`, `park_*`, `memory_recall` and friends computed
`Path.home() / ".hearthkin"` for itself, because they cannot import
kin_persistence (it imports `tools._io`, so that direction is circular). So the
override was never a real profile switch: roughly thirty independent sites went
on writing into the person's actual home no matter what was set.

That is not only untidy. `tools/_game_host.save_path()` creates the kin folder
it returns, so a test that merely asked "where would kin X's park be?" made
`~/.hearthkin/kin/X/` on the machine running it — and `tests/test_park_sharing.py`
asks that about kin named Solo, Blank and Broken. Three folders that were never
anyone's kin appeared in a real kin list, in among the real ones.

This module is the shared base both sides can import: it depends on nothing in
the project, so `kin_persistence` and `tools/` can both sit on top of it without
a cycle. Every site that wants the state tree asks here.

The environment is read on each call rather than cached at import, so a process
that sets the variable before touching these paths gets the answer it asked for
regardless of import order — the ordering trap that made the old override need
a comment about which import had to come first.
"""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

__all__ = ["config_dir", "kin_dir", "logs_dir", "home_override"]

_DIRNAME = ".hearthkin"


def home_override():
    """The configured override, or "" when there isn't one."""
    return (os.environ.get("HEARTHKIN_HOME") or "").strip()


_AUTO_SANDBOX = None


def _running_a_test():
    """True when this process IS a test file under `tests/`.

    `tests/run_all.py` already hands every child a fresh sandbox, and does it
    in the runner rather than trusting each file to remember -- the same
    argument it makes about the wx gate. But a test file also runs standalone,
    which is how they get run one at a time while working on one, and nothing
    set the sandbox on that path. So Blank, Broken, Chatty, Solo, Reader,
    SpeakerTwo and friends kept reappearing in the real kin list: folders that
    were never anyone's kin, sitting in among the real ones.

    Cosmetic so far, only because none of those invented names collides with a
    real kin. 'Solo' and 'Reader' are perfectly plausible names for a real one,
    and the day someone uses one, a standalone test run writes into it.
    """
    try:
        argv0 = Path(sys.argv[0]).resolve()
    except (OSError, ValueError, IndexError):
        return False
    return (argv0.parent.name == "tests"
            and argv0.name.startswith(("test_", "run_all")))


def config_dir():
    """The root of the runtime state tree — `~/.hearthkin` by default.

    A test run that forgot to set `HEARTHKIN_HOME` gets a throwaway directory
    instead of the person's real one. Deciding it here means no test file has
    to remember, which is the only arrangement that has ever held.
    """
    override = home_override()
    if override:
        return Path(override).expanduser()
    global _AUTO_SANDBOX
    if _running_a_test():
        if _AUTO_SANDBOX is None:
            _AUTO_SANDBOX = tempfile.mkdtemp(prefix="hearthkin-standalone-")
            atexit.register(shutil.rmtree, _AUTO_SANDBOX, ignore_errors=True)
        return Path(_AUTO_SANDBOX)
    return Path.home() / _DIRNAME


def kin_dir(name):
    """The folder for one kin. Does NOT create it — callers that need it on
    disk say so themselves, so merely asking where a kin *would* live never
    conjures one."""
    return config_dir() / "kin" / (name or "default")


def logs_dir():
    """Where the always-on diagnostic logs go."""
    return config_dir() / "logs"
