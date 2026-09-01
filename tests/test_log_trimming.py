# SPDX-License-Identifier: CC0-1.0
"""Every always-on log must be trimmed. The list can't be a thing to remember.

The always-on logs bypass the session-logging toggle on purpose -- they are the
answer to "what actually happened", and the project's own guidance is to read
them before theorising. They are also, by that same design, written forever.

`kin_persistence.ALWAYS_ON_LOGS` is trimmed once per process launch. It held
eight names. The code wrote twenty-one. The thirteen missing ones grew with no
bound at all: 5.5 MB between them when this was measured, led by
prompt_fingerprint.log at 4.1 MB -- already twice the cap every listed log
gets, and it is the file you are told to open when replies go cold.

Nothing broke. That is rather the point: an unbounded diagnostic log fails by
becoming slowly less usable, which nobody notices on any particular day.

It failed the ordinary way. Each new log was added where it gets written, and
listing it for trimming was a separate step somebody had to remember -- the
same shape as the tool-bucket step that `test_tool_buckets.py` exists to catch.
So this derives the set from the source rather than restating it: add a log
anywhere, and this test finds it.

Run: python tests/test_log_trimming.py
"""

import os
import re
import sys
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))
import tempfile  # noqa: E402
os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="logtrim-"))

import kin_persistence as k  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# A log that genuinely should NOT be trimmed goes here, with the reason. Empty
# on purpose: every always-on log written today wants trimming, and an entry
# added without a reason is the thing this file exists to prevent.
EXEMPT = {
    # Not a log this app writes -- a deliberately-absent path an audit script
    # uses to prove its own missing-file handling. Trimming it would be
    # trimming a fixture.
    "nonexistent.log": "fixture name in scripts/audit_ui.py, never written",
}

# Any log filename mentioned anywhere in the app's own source. Deliberately
# broad rather than matching the two or three ways a log currently gets opened:
# audio.py builds its path with os.path.join and was invisible to a narrower
# scan, so an earlier version of this test reported full coverage while missing
# a log. Catching a name out of a docstring is the harmless direction -- a log
# worth writing about is a log worth bounding.
_PATTERNS = (
    re.compile(r'["\']([a-z0-9_]+\.log)["\']'),
    re.compile(r'logs/([a-z0-9_]+\.log)'),
)

written = {}
for path in sorted(ROOT.rglob("*.py")):
    parts = set(path.parts)
    if ".git" in parts or "tests" in parts or "__pycache__" in parts:
        continue
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    for pat in _PATTERNS:
        for m in pat.finditer(text):
            written.setdefault(m.group(1), set()).add(
                path.relative_to(ROOT).as_posix())

# CONTROL: the scan has to actually find things. A regex that matched nothing
# would make every check below pass while proving nothing at all.
check("CONTROL the scan finds log writes at all", len(written) >= 10)
check("CONTROL ...including ones known to exist",
      {"usage.log", "empty_replies.log"} <= set(written))

listed = set(k.ALWAYS_ON_LOGS)
check("CONTROL the trim list is non-empty", len(listed) >= 10)

missing = sorted(n for n in written if n not in listed and n not in EXEMPT)
for name in missing:
    where = ", ".join(sorted(written[name]))
    check(f"{name} is trimmed (written by {where})", False)
if not missing:
    print(f"PASS  all {len(written)} logs written by the app are trimmed")

# The reverse: a name in the list that nothing writes is dead weight, and
# usually means a log was renamed and the list kept the old spelling. Not
# fatal -- trim_log_file skips a file that isn't there -- but worth saying.
stale = sorted(n for n in listed if n not in written)
check(f"no stale names in the trim list ({stale or 'none'})", not stale)

# Trimming must actually bound the file, not just be listed.
big = Path(os.environ["HEARTHKIN_HOME"]) / "huge.log"
big.parent.mkdir(parents=True, exist_ok=True)
big.write_text("x" * 40 + "\n" * 1, encoding="utf-8")
with open(big, "a", encoding="utf-8") as f:
    for i in range(80_000):
        f.write(f"line {i} " + "y" * 40 + "\n")
before = big.stat().st_size
did = k.trim_log_file(big)
after = big.stat().st_size
check("CONTROL the fixture really was over the cap", before > 2_000_000)
check("an oversized log is actually trimmed", did and after < before)
_kept = big.read_text(encoding="utf-8", errors="replace").splitlines()
check("...and it says at the top that it was trimmed",
      bool(_kept) and _kept[0].startswith("# trimmed to last"))
check("...and no partial line survives the cut",
      len(_kept) > 1 and _kept[1].startswith("line "))

small = Path(os.environ["HEARTHKIN_HOME"]) / "small.log"
small.write_text("one line\n", encoding="utf-8")
check("a small log is left alone", not k.trim_log_file(small))

print()
if _fails:
    print("FAILED (%d): %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("all log-trimming checks passed")
