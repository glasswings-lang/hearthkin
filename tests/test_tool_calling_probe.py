"""A model that SAYS it can call tools must be caught when it doesn't.

Plain Python; run via tests/run_all.py.

Ollama's `capabilities` list reports whether a model's template can
EXPRESS a tool call. It cannot report whether the weights will ever emit
one. A roleplay finetune of a tool-trained base inherits the base's
template, declares `tools` quite truthfully, and then writes a
description of using the tool instead -- prose that reads exactly like
success and does nothing at all.

That combination is the worst shape a failure can take: the kin sounds
fine. It answers warmly, it says it is reading the file, and nothing
happens, for a whole evening, with no error anywhere. The pre-flight
check had no way to see it, because the one signal it consulted was the
flag that was already saying yes.

So the probe asks the model directly, and this pins the three things
that have to hold:

  1. A declared-but-doesn't model raises a BLOCKER on a tools-using kin.
     (On the old code this returned early and said nothing at all.)
  2. A model that passes the probe stays silent -- no crying wolf.
  3. The pre-flight NEVER runs the probe itself. It reads the cache and
     nothing more. An inference call on the UI thread is a multi-second
     freeze with the screen reader silent, which is the exact defect the
     cron test was rewritten to avoid (M-D2).
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_failures = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _failures.append(label)


import compat
import model_utils
from dialogs.tool_probe_result import format_probe_result

MODEL = "pretend/roleplay-24b"


def _notes_for(cfg_tools, probe_verdict):
    """Run the tool-support check with a stubbed kin and a stubbed probe."""
    notes = []
    profile = compat.ModelProfile(family="ollama", supports_tools=True)
    real_loader = compat.__dict__.get("_test_tools_loader")
    model_utils._tool_probe_cache.clear()
    if probe_verdict is not None:
        model_utils._tool_probe_cache[MODEL] = {
            "ok": probe_verdict, "called": [], "said": "I'll read that now.",
            "error": "",
        }
    import kin_persistence
    orig = kin_persistence.load_kin_tools
    kin_persistence.load_kin_tools = lambda name: list(cfg_tools)
    try:
        compat._check_tool_support({}, [], profile, notes,
                                   kin_name="TestKin", target_model=MODEL)
    finally:
        kin_persistence.load_kin_tools = orig
    return notes


try:
    # ── 1. declared, but doesn't: blocker ────────────────────────────
    notes = _notes_for(["read_file", "note"], probe_verdict=False)
    check(len(notes) == 1, "declared-but-doesn't raises exactly one note")
    if notes:
        n = notes[0]
        check(n.severity == compat.SEVERITY_BLOCKER,
              "that note is a BLOCKER, not a soft warning")
        check("doesn't" in n.title.lower() or "does not" in n.title.lower(),
              "the title says the model doesn't do it")
        # The distinction that matters: this is NOT the old
        # "doesn't support tools" message. It has to say the model
        # claims support, or the reader goes looking for a flag that
        # already agrees with them.
        blob = (n.title + " " + n.detail).lower()
        check("says it can" in blob or "reports tool support" in blob,
              "it names the trap: says it can, and doesn't")

    # ── 2. passes the probe: silence ─────────────────────────────────
    notes = _notes_for(["read_file", "note"], probe_verdict=True)
    check(notes == [], "a model that PASSES the probe raises nothing")

    # ── 3. never probed: silence (no verdict, no claim) ──────────────
    notes = _notes_for(["read_file", "note"], probe_verdict=None)
    check(notes == [], "an un-probed model raises nothing")

    # ── 4. no tools enabled: silence even on a failing model ─────────
    notes = _notes_for([], probe_verdict=False)
    check(notes == [], "a kin with no tools enabled is left alone")

    # ── 5. the pre-flight must NEVER run an inference itself ─────────
    # Paired with a positive: prove the check still RAN (it produced the
    # blocker) while the probe stayed untouched. An "it wasn't called"
    # assertion on its own is equally true when nothing happened at all.
    called = {"n": 0}
    real_probe = model_utils.probe_tool_calling

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("pre-flight must not probe on the UI thread")

    model_utils.probe_tool_calling = _boom
    try:
        notes = _notes_for(["read_file"], probe_verdict=False)
    finally:
        model_utils.probe_tool_calling = real_probe
    check(called["n"] == 0, "pre-flight never calls the probe (no UI freeze)")
    check(len(notes) == 1, "...and the check still ran, from the cache alone")

    # ── 6. the result text carries the evidence ──────────────────────
    txt = format_probe_result(MODEL, {
        "ok": False, "called": [], "said": "Reading file: notes.md", "error": ""})
    check("did NOT" in txt, "a failure result says so plainly")
    check("Reading file: notes.md" in txt,
          "...and quotes what the model wrote instead")
    txt_ok = format_probe_result(MODEL, {
        "ok": True, "called": ["hearthkin_probe_echo"], "said": "", "error": ""})
    check("made the tool call" in txt_ok, "a pass result says so plainly")
    check("did NOT" not in txt_ok, "...and doesn't also say it failed")
    txt_unk = format_probe_result(MODEL, {
        "ok": None, "called": [], "said": "", "error": "connection refused"})
    check("could not tell" in txt_unk.lower(), "an unknown result admits it")
    check("connection refused" in txt_unk, "...and shows why")

finally:
    model_utils._tool_probe_cache.clear()

print()
if _failures:
    print("FAILED (%d):" % len(_failures))
    for f in _failures:
        print("  - " + f)
    sys.exit(1)
print("All tool-calling-probe checks passed.")
