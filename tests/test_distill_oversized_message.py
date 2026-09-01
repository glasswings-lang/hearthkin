# SPDX-License-Identifier: CC0-1.0
"""Guard test: one enormous message must not stop a kin's memory forever.

`_distill_bite` deliberately sends at least one message even when it goes
over budget, because otherwise a message too big to fit would cap the bite
at zero, the bookmark would never move, and a walk would spin without
advancing. The comment said "let the provider truncate or fail instead",
and that was right while "over budget" meant a little over.

It is fatal when a single message is several times the whole context
window. Observed live: a kin's history held one pasted user turn of
440,659 characters -- about 110,000 tokens against a num_ctx of 32,768.
The bite handed it over whole. Local Ollama neither truncated nor failed
cleanly: it chewed for about forty minutes and timed out, the bookmark
stayed where it was, the same chunk was queued again, and it failed three
times overnight with nothing able to get past it. That is the same
deadlock the guard exists to prevent, only slower, and holding the model
the entire time.

Truncating beats skipping. Distillation is summarising, so a summary of
the first part of a huge paste is worth having; skipping would advance the
bookmark past content that then never reaches memory at all, with nothing
to show it happened.

What this file pins:

  - an ordinary bite is returned untouched, byte for byte;
  - a message larger than the whole budget is cut down;
  - the cut copy is a COPY -- the stored conversation is never edited;
  - the cut is marked, with the true size and where the whole thing is,
    so the summariser can say so instead of inventing an ending;
  - a message at the edge of the budget is left alone;
  - non-string and malformed content is passed through, not dropped;
  - and any fault returns the bite unchanged, because a sizing helper
    must never be why a distillation fails to run.

Run: python tests/test_distill_oversized_message.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="oversize-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


from frame.memory_mixin import MemoryMixin  # noqa: E402


class _Frame(MemoryMixin):
    def __init__(self):
        self.logged = []

    def _log(self, msg):
        self.logged.append(msg)


f = _Frame()
BUDGET = 20000          # tokens
MODEL = "some-model"

small = {"role": "user", "content": "a short thing"}
mid = {"role": "assistant", "content": "x" * 4000}

# The real shape: one pasted turn far larger than the whole window.
HUGE = "filler paragraph, repeated. " * 15714   # ~440,000 chars
huge = {"role": "user", "content": HUGE, "ts": "2026-07-25T15:10:39"}


# --- the ordinary case is not touched ------------------------------------

plain = [small, mid, dict(small)]
out = f._fit_oversized_messages(plain, BUDGET, MODEL, 1.1)
check("an ordinary bite comes back unchanged",
      [m["content"] for m in out] == [m["content"] for m in plain])
check("...and nothing is announced about it", not f.logged)


# --- the message that caused this ----------------------------------------

before_len = len(HUGE)
out = f._fit_oversized_messages([small, huge, mid], BUDGET, MODEL, 1.1)
check("the bite still has every message -- nothing is dropped", len(out) == 3)
cut = out[1]["content"]
check("the oversized message is cut down", len(cut) < before_len)
check("...to something that could actually fit the window",
      len(cut) < BUDGET * 5)
check("...but not to nothing", len(cut) > 1000)
check("the messages either side are untouched",
      out[0]["content"] == small["content"] and out[2]["content"] == mid["content"])


# --- it is a copy, and the record is not edited --------------------------

check("the stored message is NOT modified -- memory work must never "
      "rewrite a kin's own history",
      huge["content"] == HUGE and len(huge["content"]) == before_len)
check("...and the returned message is a different object", out[1] is not huge)
check("its other fields survive the copy", out[1].get("ts") == huge["ts"])


# --- the cut says what it is ---------------------------------------------

check("the cut is marked rather than silent", "[hearthkin:" in cut)
check("...naming the true size", f"{before_len:,}" in cut)
check("...and where the whole thing still lives",
      "conversation.jsonl" in cut)
check("...and telling the summariser not to invent an ending",
      "do not infer how it ended" in cut)
check("it says so in the activity log too, since a walk is unattended",
      any("oversized message" in m for m in f.logged))


# --- edges ----------------------------------------------------------------

f.logged.clear()
edge = {"role": "user", "content": "y" * (BUDGET * 2)}   # ~half the budget
out = f._fit_oversized_messages([edge], BUDGET, MODEL, 1.0)
check("a message comfortably inside the budget is left alone",
      out[0]["content"] == edge["content"])

odd = [{"role": "tool", "content": None},
       {"role": "user"},
       {"role": "user", "content": ""},
       "not a dict"]
out = f._fit_oversized_messages(odd, BUDGET, MODEL, 1.0)
check("malformed entries are passed through, not dropped", len(out) == 4)


# --- fail-soft ------------------------------------------------------------

check("a nonsense budget does not raise",
      f._fit_oversized_messages([huge], "not a number", MODEL, 1.0) is not None)
check("None in, something sensible out",
      f._fit_oversized_messages(None, BUDGET, MODEL, 1.0) in ([], None))


# --- and the guard it sits behind is still there --------------------------
#
# The "always send at least one message" rule is what makes this necessary.
# If somebody removes it, a huge message caps the bite at zero and the walk
# stops advancing instead -- a different deadlock, not a fix.

import re  # noqa: E402
src = (ROOT / "frame" / "memory_mixin.py").read_text(encoding="utf-8",
                                                     errors="replace")
body = re.sub(r"(?m)^\s*#.*$", "", re.sub(r'""".*?"""', "", src, flags=re.S))
check("the at-least-one-message rule is still in place",
      "cap_idx = 1" in body)
check("...and the trim runs on the bite that rule produces",
      "_fit_oversized_messages" in body)


print()
if _fails:
    print(f"{len(_fails)} FAILED: " + ", ".join(_fails))
    sys.exit(1)
print("all checks passed")
