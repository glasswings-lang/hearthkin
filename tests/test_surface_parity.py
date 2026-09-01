# SPDX-License-Identifier: CC0-1.0
"""Guard test: the four surfaces a kin speaks through cannot drift apart
silently any more.

The map itself lives in `tests/_surface_matrix.py` — read that first; it says
what every capability is and, for every surface, whether that surface has it,
lacks it, or is a place where the question does not arise. This file only
enforces it.

WHAT THIS CATCHES, which nothing did before:

  * a capability declared Present that is no longer wired -> somebody removed
    it, or the matrix was flattering the code to begin with;
  * a capability declared Absent that IS now wired -> somebody built it and
    the map went stale, which is how the next person ends up rebuilding it;
  * a capability or a surface with a cell missing -> adding either one forces
    an answer for every combination, rather than leaving the new column
    quietly undefined.

That last one is the point. Every gap this audit found existed because a
feature was added to the surface that provoked it and nothing anywhere asked
about the others. A test that fails on an UNANSWERED question is the only kind
that prevents that.

WHY A SOURCE PROBE IS ENOUGH HERE. It answers "is this wired at all", not "is
it correct" — the other hundred-odd files in this folder are what check
correctness. Nothing this audit turned up was subtle: a webcam with no gate, a
reply that arrived as silence, a list box that could not express the one value
the config documented. They were unobserved, not hard. A coarse instrument
that covers every combination is exactly what was missing, and the detector
checks itself against a positive control below before any of its answers are
believed.

Run: python tests/test_surface_parity.py
     python tests/test_surface_parity.py --report    (print the map, check
                                                      nothing)
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="parity-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from _surface_matrix import (                                  # noqa: E402
    CAPABILITIES, SURFACES, SURFACE_ORDER, SURFACE_NAMES,
    Present, Absent, NotHere, is_wired, markers_for, _strip,
)

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# ── 0. the detector has to work before its answers mean anything ───────
#
# This whole file is an argument from absence: "the marker is not there, so
# the feature is not there." Absence is only evidence once the instrument is
# known to fire on a positive control.

print("--- the detector, before believing anything it says ---")

_probe = '''
"""A module docstring naming inject_into_messages, which is not code."""
# A comment mentioning clean_kin_reply, which is also not code.
def f():
    """A docstring mentioning should_stop."""
    x = load_app_prompt(
        "tool_use_hint", name)          # a string on a continuation line
    return x
'''
_stripped = _strip(_probe)
check("a real call is found", "load_app_prompt" in _stripped)
check("a string argument on a continuation line survives -- the bug that made "
      "this detector report a wired feature as missing",
      "tool_use_hint" in _stripped)
check("a module docstring is not mistaken for code",
      "inject_into_messages" not in _stripped)
check("a comment is not mistaken for code", "clean_kin_reply" not in _stripped)
check("a function docstring is not mistaken for code",
      "should_stop" not in _stripped)


# ── 1. every cell is answered ──────────────────────────────────────────

print("--- every capability is answered for every surface ---")

_keys = [c.key for c in CAPABILITIES]
check("no capability is declared twice", len(_keys) == len(set(_keys)))

_missing = []
for cap in CAPABILITIES:
    for surface in SURFACE_ORDER:
        if surface not in cap.surfaces:
            _missing.append(f"{cap.key} x {surface}")
check("no cell is left undefined -- a new surface or capability must be "
      "answered for, not defaulted",
      not _missing)
if _missing:
    for m in _missing:
        print("     unanswered: " + m)

_extra = []
for cap in CAPABILITIES:
    for surface in cap.surfaces:
        if surface not in SURFACES:
            _extra.append(f"{cap.key} names unknown surface {surface!r}")
check("no cell names a surface that doesn't exist", not _extra)

check("every capability says what it is, in words",
      all((c.what or "").strip() for c in CAPABILITIES))
check("...and what it costs when it's missing",
      all((c.why or "").strip() for c in CAPABILITIES))

# A reason is what someone reads when deciding whether to close a gap, so an
# empty or token one makes the entry worthless.
_thin = [f"{c.key} x {s}" for c in CAPABILITIES for s, cell in c.surfaces.items()
         if isinstance(cell, (Absent, NotHere)) and len(cell.reason.strip()) < 40]
check("every Absent and NotHere carries a real reason, not a shrug", not _thin)
if _thin:
    for t in _thin:
        print("     thin reason: " + t)


# ── 2. the ratchet, both ways ──────────────────────────────────────────

print("--- declared Present must actually be wired ---")

_broken = []
for cap in CAPABILITIES:
    for surface in SURFACE_ORDER:
        cell = cap.surfaces.get(surface)
        if isinstance(cell, Present) and not is_wired(cap, surface, cell):
            _broken.append(
                f"{cap.key} on {SURFACE_NAMES[surface]}: declared present, but "
                f"none of {markers_for(cap, cell)} is in the code")
check("nothing declared present has quietly stopped being wired", not _broken)
for b in _broken:
    print("     " + b)

print("--- declared missing must actually be missing ---")

_stale = []
for cap in CAPABILITIES:
    for surface in SURFACE_ORDER:
        cell = cap.surfaces.get(surface)
        if isinstance(cell, NotHere) and not getattr(cell, "probe", True):
            continue          # no marker could mean anything here; see NotHere
        if isinstance(cell, (Absent, NotHere)) and is_wired(cap, surface, cell):
            _stale.append(
                f"{cap.key} on {SURFACE_NAMES[surface]}: the matrix says it "
                f"isn't there, but {markers_for(cap, cell)} IS in the code — "
                f"if you just built it, move the cell to Present")
check("nothing declared missing has quietly been built without the map "
      "being updated", not _stale)
for s in _stale:
    print("     " + s)


# ── 3. the map, printed ────────────────────────────────────────────────
#
# Printed on every run, pass or fail. A backlog nobody sees is a backlog
# nobody closes, and the whole failure being fixed here is that the shape of
# the gaps was never in front of anyone.

def print_map():
    width = max(len(c.key) for c in CAPABILITIES) + 2
    head = "capability".ljust(width) + "".join(
        s[:8].ljust(10) for s in SURFACE_ORDER)
    print()
    print(head)
    print("-" * len(head))
    for cap in CAPABILITIES:
        row = cap.key.ljust(width)
        for surface in SURFACE_ORDER:
            cell = cap.surfaces.get(surface)
            mark = {"present": "yes", "absent": "GAP",
                    "not_here": "n/a"}.get(getattr(cell, "state", ""), "?")
            row += mark.ljust(10)
        print(row)


gaps = [(c, s) for c in CAPABILITIES for s in SURFACE_ORDER
        if isinstance(c.surfaces.get(s), Absent)]

if "--report" in sys.argv:
    print_map()

print()
print(f"{len(gaps)} open gap(s) across "
      f"{len(CAPABILITIES)} capabilities x {len(SURFACE_ORDER)} surfaces:")
by_surface = {}
for cap, surface in gaps:
    by_surface.setdefault(surface, []).append(cap.key)
for surface in SURFACE_ORDER:
    named = by_surface.get(surface) or []
    if named:
        print(f"  {SURFACE_NAMES[surface]}: " + ", ".join(sorted(named)))

print()
if _fails:
    print(f"test_surface_parity: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_surface_parity: all checks passed")
