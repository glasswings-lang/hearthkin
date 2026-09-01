# SPDX-License-Identifier: CC0-1.0
"""Run one widget-building test on an isolated desktop.

    python tests/_gui_runner.py tests/test_stable_tab_order.py

The isolation has to happen before the test's first line — `SetThreadDesktop`
fails once a thread owns windows or hooks, and wx must not be imported yet. So
it happens HERE, in a fresh process, and the test file is then run as if it were
`__main__`. The test needs no cooperation and no knowledge of any of this.

That is the point. The previous design asked every widget-building test to opt
itself in, and two were added that didn't — a rule each new file has to remember
is a rule that gets forgotten. Here the runner decides, before the test exists.

If isolation is unavailable this **refuses to run the test** rather than running
it unprotected. "We couldn't make it safe" must never quietly become "so we did
it anyway": the failure it would cause lands on a person mid-task, not on
whoever reads the exit code.
"""

import os
import runpy
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _isolated_desktop import enter_isolated_desktop, isolation_detail


def main(argv):
    if len(argv) < 2:
        print("usage: _gui_runner.py <test file> [args...]")
        return 2
    target = argv[1]

    if not enter_isolated_desktop():
        print(f"REFUSED to run {os.path.basename(target)} — could not isolate "
              f"the desktop, and a wx window on the live desktop steals focus "
              f"from a screen reader.")
        print(f"  reason: {isolation_detail()}")
        return 3

    # The desktop is real and has no input attached, so widgets here cannot
    # reach anyone's foreground. Tell the test that building them is fine —
    # its own gate exists for someone running it directly, where it isn't.
    os.environ["HEARTHKIN_GUI_TESTS"] = "1"
    os.environ["HEARTHKIN_GUI_ISOLATED"] = "1"

    sys.argv = argv[1:]
    try:
        runpy.run_path(target, run_name="__main__")
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
