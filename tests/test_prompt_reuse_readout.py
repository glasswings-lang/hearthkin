# SPDX-License-Identifier: CC0-1.0
"""Guard test: every prompt says how much of it the model could reuse.

The fingerprint log has always recorded enough to work this out — a per-message
role/size/hash for every request. Working it out meant diffing two lines by
hand, so in practice nobody did, and a conversation that re-read itself from
cold on every turn looked exactly like a slow model. Two separate fixes were
made in the wrong place before anyone diffed those lines.

So the arithmetic goes on the line: `reuse=94% first-change=msg 89 (user)`.

Why a percentage of CHARACTERS and not of messages: a local model reuses its
cached work only for an unbroken run from the very start of the prompt, so what
matters is how far in the first change lands, weighted by how much text sits
before it. "First change at message 89 of 92" sounds nearly free and usually is
— but the same position in a conversation whose bulk sits at the end would not
be, and a message count can't tell the two apart.

Run: python tests/test_prompt_reuse_readout.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="reuse-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import llm_backend as LB  # noqa: E402

reuse = LB._prefix_reuse


def fp(*sizes_and_tags):
    """Build a fingerprint list: (size, tag) per message."""
    return [f"{i}:user:{size}:{tag}"
            for i, (size, tag) in enumerate(sizes_and_tags)]


# --- the two cases that matter ------------------------------------------

# A pure append: everything the model already read is untouched.
before = fp((20000, "aaa"), (100, "bbb"))
after = fp((20000, "aaa"), (100, "bbb"), (50, "ccc"))
pct, first = reuse(before, after)
check("appending one message reuses nearly all of the prompt", pct >= 99)
check("...and reports the first change at the new message", first == 2)

# The failure this whole thread was about: the front moved.
shifted = fp((20000, "zzz"), (100, "bbb"), (50, "ccc"))
pct, first = reuse(after, shifted)
check("a change to the FIRST message reuses nothing", pct == 0)
check("...and points at message 0", first == 0)

# Weighted by size, not message count — the whole reason it's characters.
_bulky_tail = fp((10, "a"), (10, "b"), (30000, "c"))
_bulky_tail_changed = fp((10, "a"), (10, "b"), (30000, "CHANGED"))
pct, first = reuse(_bulky_tail, _bulky_tail_changed)
check("a late change to a huge message still reports low reuse", pct < 10)
print(f"       (2 of 3 messages identical, but only {pct}% of the text)")

_light_tail = fp((30000, "a"), (10, "b"), (10, "c"))
_light_tail_changed = fp((30000, "a"), (10, "b"), (10, "CHANGED"))
pct, _ = reuse(_light_tail, _light_tail_changed)
check("...while a late change to a tiny message reports high reuse", pct >= 99)


# --- edges --------------------------------------------------------------

check("no previous prompt reports unknown, not 100%",
      reuse(None, after) == (None, None))
check("an empty prompt reports unknown rather than dividing by zero",
      reuse(after, []) == (None, None))
check("identical prompts reuse everything",
      reuse(after, after)[0] == 100)
check("a shortened prompt still reports its shared head",
      reuse(after, before)[1] == 2)
# A malformed fingerprint must not raise — this is a diagnostic, and a
# diagnostic that throws on a send is worse than no diagnostic.
_raised = None
try:
    reuse(["garbage"], ["also:garbage"])
except Exception as e:                                    # pragma: no cover
    _raised = e
check("a malformed fingerprint does not raise", _raised is None)


# --- it actually reaches the log ----------------------------------------

_src = (ROOT / "llm_backend.py").read_text(encoding="utf-8")
check("the readout is written onto the log line",
      "{summary} |" in _src)
check("...comparing against this kin+surface's previous prompt",
      "_LAST_FINGERPRINT" in _src)
check("...and says so plainly on the very first prompt of a run",
      "first prompt seen this run" in _src)

# End to end: two calls, real logging path, second line carries the readout.
from kin_persistence import LOGS_DIR  # noqa: E402

_msgs1 = [{"role": "system", "content": "x" * 5000},
          {"role": "user", "content": "hello"}]
_msgs2 = _msgs1 + [{"role": "assistant", "content": "hi"},
                   {"role": "user", "content": "again"}]
LB._log_prompt_fingerprint("ProbeKin", "desktop", "m", _msgs1)
LB._log_prompt_fingerprint("ProbeKin", "desktop", "m", _msgs2)
_lines = [l for l in (LOGS_DIR / "prompt_fingerprint.log").read_text(
    encoding="utf-8", errors="replace").split("\n") if "ProbeKin" in l]
check("the first logged prompt says it has nothing to compare to",
      len(_lines) >= 2 and "first prompt seen this run" in _lines[0])
# 99%, not 100%: something DID change, so the readout must not round away
# the fact. 100% is reserved for a prompt that is byte-identical.
check("the second reports high-but-not-total reuse, because it appended",
      len(_lines) >= 2 and "reuse=99%" in _lines[1])
if len(_lines) >= 2:
    print("       " + _lines[1].split(" | ")[0])


# --- the system prompt itself is kept, in before/after pairs -------------
# The reuse figure says the system block changed; it can't say WHAT changed.
# Reconstructing it from its parts (base prompt, soul, memory, harness
# prompts) left thousands of characters unaccounted for on a real kin, so the
# guessing stops here: keep the last two versions that actually DIFFER, and the
# pair is always a before/after of a real change rather than two arbitrary
# turns. It is a concatenation of files already in the kin's own folder, so
# keeping a copy discloses nothing new.

def _sys_dir():
    return LOGS_DIR / "system_prompts"


def _pair(kin, surface):
    import re
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{kin}--{surface}")
    d = _sys_dir()
    cur, prev = d / f"{safe}.txt", d / f"{safe}.prev.txt"
    return (cur.read_text(encoding="utf-8") if cur.exists() else None,
            prev.read_text(encoding="utf-8") if prev.exists() else None)


def _send(kin, surface, sys_text, tail="hi"):
    LB._log_prompt_fingerprint(kin, surface, "m", [
        {"role": "system", "content": sys_text},
        {"role": "user", "content": tail}])


_send("PairKin", "telegram-dm", "BASE\nSOUL\n", "one")
cur, prev = _pair("PairKin", "telegram-dm")
check("the first system prompt is kept", cur == "BASE\nSOUL\n")
check("...with nothing to compare it against yet", prev is None)

_send("PairKin", "telegram-dm", "BASE\nSOUL\n", "two")
cur, prev = _pair("PairKin", "telegram-dm")
check("an UNCHANGED system prompt writes nothing new", prev is None)

_send("PairKin", "telegram-dm", "BASE\nSOUL\nGREW\n", "three")
cur, prev = _pair("PairKin", "telegram-dm")
check("a change rotates the old one aside", prev == "BASE\nSOUL\n")
check("...and keeps the new one", cur == "BASE\nSOUL\nGREW\n")
check("...so the pair is a real before/after, not two arbitrary turns",
      cur != prev and cur.startswith(prev))

# Surfaces are tracked separately — a kin's DM and tool prompts differ by
# design, and interleaving them would make every turn look like a change.
_send("PairKin", "telegram-dm-tool", "TOOLS\n", "x")
cur_dm, _ = _pair("PairKin", "telegram-dm")
cur_tool, _ = _pair("PairKin", "telegram-dm-tool")
check("each surface keeps its own pair",
      cur_dm == "BASE\nSOUL\nGREW\n" and cur_tool == "TOOLS\n")

# Names that would otherwise escape the folder or break a filesystem. The
# property that matters is containment — every file lands inside the folder —
# not whether any particular character survives sanitising.
_send("../evil kin", "a/b:c", "X\n", "y")
_escaped = [p for p in _sys_dir().iterdir()
            if p.resolve().parent != _sys_dir().resolve()]
check("no kin or surface name can write outside the folder", not _escaped)
check("...and none starts with a dot", not any(p.name.startswith(".")
                                               for p in _sys_dir().iterdir()))

# Not every call starts with a system message; that must be a quiet no-op.
_before = sorted(p.name for p in _sys_dir().iterdir())
LB._log_prompt_fingerprint("PairKin", "no-system", "m",
                           [{"role": "user", "content": "no system here"}])
check("a call with no system message writes nothing",
      sorted(p.name for p in _sys_dir().iterdir()) == _before)

# And it must never be able to cost a reply.
_raised = None
try:
    LB._keep_system_prompt_pair("PairKin", "telegram-dm", None)
    LB._keep_system_prompt_pair("PairKin", "telegram-dm", ["not a dict"])
except Exception as e:                                    # pragma: no cover
    _raised = e
check("malformed input does not raise", _raised is None)


print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_prompt_reuse_readout: all checks passed")
