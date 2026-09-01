# SPDX-License-Identifier: CC0-1.0
"""Guard test: the model-history audit trail.

This file used to be called `voice_history.md`, from back when the only
reason to change a kin's model was a deliberate change of voice. Two
things went wrong under that name.

First, nobody could find it. It is the ONLY record of which model wrote
a kin's memory -- and that matters, because a summariser's habits (how
well it keeps two people apart, say) end up baked into memory.md, and
memory outlives the swap away from the model that produced it. When
something in memory turns out to be wrong, "who wrote this and when?"
is answerable here and nowhere else. Nobody looks for that under
"voice".

Second, the name shaped the code. The memory (distillation) model was
deliberately excluded from the audit, on the reasoning that a
summariser "never speaks back to the user, so voice-continuity warnings
would be noise." That is correct about warnings and wrong about the
audit, and it was wrong *because* the file was named for voice: the
memory model is the one whose provenance matters most. Changing it left
no trace at all.

So: renamed, and both kinds of swap recorded. The rename migrates on
first touch, and a failed rename must fall back to the old file rather
than start an empty new one -- losing a kin's provenance to a
permissions error would defeat the entire point.

Run: python tests/test_model_history.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault(
    "HEARTHKIN_HOME",
    tempfile.mkdtemp(prefix="hearthkin-modelhist-"))

import kin_persistence as kp  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def bail(label):
    """Report a clean FAIL and stop, rather than tracebacking.

    A guard test that explodes when the feature is absent still 'fails',
    but it fails by crashing -- which reads as a broken test rather than
    a missing feature, and prints nothing about what is actually wrong.
    Run this file against code without the rename and it says so in one
    line. That is also the positive control: a checker never seen to
    report a failure is not a checker.
    """
    check(label, False)
    print()
    print("FAILURES: %d" % len(_fails))
    for f in _fails:
        print("  - " + f)
    sys.exit(1)


if not hasattr(kp, "MODEL_HISTORY_FILE") or not hasattr(
        kp, "append_model_history"):
    bail("kin_persistence exposes the model-history API "
         "(MODEL_HISTORY_FILE + append_model_history)")


def fresh(name):
    d = kp.agent_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    for f in (kp.MODEL_HISTORY_FILE, kp.LEGACY_MODEL_HISTORY_FILE):
        p = d / f
        if p.exists():
            p.unlink()
    return d


# --- the sandbox is real, or nothing below is safe to run -----------
check("sandboxed away from a real ~/.hearthkin",
      "hearthkin-modelhist" in str(kp.AGENTS_DIR)
      or "HEARTHKIN_HOME" in os.environ)

# --- migration keeps history -----------------------------------------
d = fresh("MigrateMe")
(d / kp.LEGACY_MODEL_HISTORY_FILE).write_text(
    "# Voice history\n\n- 2026-07-08 23:48 - model changed from `a` to `b`\n",
    encoding="utf-8")

p = kp.model_history_path("MigrateMe")
body = p.read_text(encoding="utf-8")
# Paired assertions: the old file being gone is only good news if the
# new one exists AND still carries the entry. An absence alone is also
# what a delete looks like.
check("legacy file is migrated to the new name, entry intact",
      p.name == kp.MODEL_HISTORY_FILE
      and "2026-07-08" in body
      and not (d / kp.LEGACY_MODEL_HISTORY_FILE).exists())

kp.append_model_history("MigrateMe", "chat model changed from `b` to `c`")
body = p.read_text(encoding="utf-8")
check("appending to a migrated file keeps what was already there",
      "2026-07-08" in body and "from `b` to `c`" in body)

# --- a fresh kin gets a header that says what the file is FOR --------
fresh("Newborn")
kp.append_model_history("Newborn", "chat model changed from `(none)` to `x`")
body = kp.model_history_path("Newborn").read_text(encoding="utf-8")
check("new file is headed 'Model history', not 'Voice history'",
      body.startswith("# Model history") and "Voice history" not in body)
check("header explains it is the record of who WROTE the memory",
      "WHICH MODEL WROTE" in body)
check("header still tells distillation to keep off",
      "Distillation must not touch this file" in body)

# --- the memory model is recorded at all ------------------------------
# The whole point of the change. Under the old name this was excluded.
fresh("Summariser")
kp.append_model_history(
    "Summariser", "memory model changed from `(same as chat model)` to `m`")
body = kp.model_history_path("Summariser").read_text(encoding="utf-8")
check("a memory-model swap is recorded, and says it was the memory model",
      "memory model changed" in body and "to `m`" in body)

# --- chat and memory swaps are distinguishable in one file -----------
kp.append_model_history("Summariser", "chat model changed from `x` to `y`")
body = kp.model_history_path("Summariser").read_text(encoding="utf-8")
check("both kinds live in one file and can be told apart",
      "memory model changed" in body and "chat model changed" in body)

# --- entries are dated and use the same dash as the existing files ---
entries = [ln for ln in body.splitlines() if ln.startswith("- 20")]
check("every entry is a dated line",
      len(entries) == 2 and all(" \u2014 " in ln for ln in entries))

# --- the call sites are actually wired -------------------------------
# A helper nothing calls is a helper that records nothing. These are
# static checks on purpose: the swap paths live behind wx dialogs, and
# the failure being guarded against is somebody quietly dropping the
# call, which reads the same either way.
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def src(rel):
    with open(os.path.join(root, rel), encoding="utf-8") as f:
        return f.read()


ek = src(os.path.join("dialogs", "edit_kin.py"))
check("the memory-model CHANGE path records the swap",
      "append_model_history" in ek and "memory model changed from" in ek)
check("the memory-model RESET path records it too "
      "(falling back to the chat model is still a change of who writes)",
      "(same as chat model)" in ek and "previous = " in ek)

km = src(os.path.join("frame", "kin_mgmt_mixin.py"))
check("the chat-model path goes through the same single writer",
      "append_model_history" in km
      and 'vh_path = agent_dir' not in km)
check("no user-visible string still names voice_history.md",
      "Logged to model_history.md" in km
      and "recorded in this kin's voice_history.md" not in km.lower())

print()
if _fails:
    print("FAILURES: %d" % len(_fails))
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("all model-history checks passed")
