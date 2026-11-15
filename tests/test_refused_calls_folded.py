# SPDX-License-Identifier: CC0-1.0
"""Calls in one batch that weren't run go out to the model as one line.

test_repeat_calls_not_sent.py pins that COPIES of one call go out once. This
pins the other half: different calls that were all refused. At one
kin's wake-up on 14 Nov 2026 a batch of 71 calls had 63 refused (the reply was
over its limit), and each refusal sat in the history as its own "[not run"
note, sent again on every later turn. The person it's for: "they should be
pushed down into 1 line."

What is pinned, through the real chat() with a stand-in provider:

  * A batch with many refused calls goes out with the calls that ran, plus
    ONE refused call whose note says how many more and which tools.
  * Every call that goes out still has its own result (providers require it).
  * One refusal alone is left as it is.
  * The stored history is not changed.
  * The check can fail: with the folding switched off, every refusal goes out.

Run: python tests/test_refused_calls_folded.py
"""

import copy
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="refused-folded-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import llm_backend  # noqa: E402

LIMIT = llm_backend._NOT_RUN_LIMIT


def _call(cid, name, **args):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def _batch(ran, refused):
    """A batch whose first `ran` calls ran and whose next ones were refused.
    `refused` is a list of tool names."""
    calls, results = [], []
    for k in range(ran):
        calls.append(_call(f"r{k}", "read_file", path=f"memory/{k}.md"))
        results.append({"role": "tool", "tool_call_id": f"r{k}", "content": f"file {k}"})
    for k, name in enumerate(refused):
        calls.append(_call(f"x{k}", name, path=f"other/{k}.md"))
        results.append({"role": "tool", "tool_call_id": f"x{k}", "content": LIMIT})
    return ([{"role": "user", "content": "tend"},
             {"role": "assistant", "content": "", "tool_calls": calls}]
            + results
            + [{"role": "assistant", "content": "Out of calls."},
               {"role": "user", "content": "ok"}])


_sent = []


def _capture_send(*a, **kw):
    msgs = kw.get("messages")
    if msgs is None:
        msgs = a[1]
    _sent.append(copy.deepcopy(msgs))
    return llm_backend.ChatResult(content="ok", thinking="", tool_calls=[],
                                  usage={}) if hasattr(llm_backend, "ChatResult") else None


def sent_for(history, model="openrouter/xiaomi/mimo-v2.6-pro"):
    _sent.clear()
    o1 = llm_backend._chat_openrouter_blocking
    o2 = llm_backend._chat_ollama_blocking
    llm_backend._chat_openrouter_blocking = _capture_send
    llm_backend._chat_ollama_blocking = _capture_send
    try:
        try:
            llm_backend.chat(model, history, stream=False)
        except Exception:
            if not _sent:
                raise
    finally:
        llm_backend._chat_openrouter_blocking = o1
        llm_backend._chat_ollama_blocking = o2
    return _sent[-1]


def tool_parts(msgs):
    calls = sum(len(m.get("tool_calls") or []) for m in msgs if m.get("role") == "assistant")
    results = sum(1 for m in msgs if m.get("role") == "tool")
    return calls, results


def paired(msgs):
    for i, m in enumerate(msgs):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            ids = sorted(t["id"] for t in m["tool_calls"])
            j, got = i + 1, []
            while j < len(msgs) and msgs[j].get("role") == "tool":
                got.append(msgs[j]["tool_call_id"])
                j += 1
            if sorted(got) != ids:
                return False
    return True


print("--- many refusals go out as one ---")

refused = ["exec"] * 39 + ["edit_file"] * 14 + ["note"] * 2
hist = _batch(8, refused)
before = copy.deepcopy(hist)
sent = sent_for(hist)
check("8 that ran and 55 refused go out as 9 calls", tool_parts(sent) == (9, 9))
notes = [m["content"] for m in sent if m.get("role") == "tool" and m["content"].startswith("[not run")]
check("one refusal note goes out", len(notes) == 1)
check("...saying how many and which tools",
      notes and "54 more calls" in notes[0] and "55 weren't run in all (exec 39, edit_file 14, note 2)" in notes[0])
check("the results of the calls that ran all go out",
      [m["content"] for m in sent if m.get("role") == "tool"][:8] == [f"file {k}" for k in range(8)])
check("every call that goes out has its result", paired(sent))
check("the stored history isn't changed", hist == before)

print("--- one refusal alone ---")

sent = sent_for(_batch(3, ["exec"]))
check("a single refusal goes out as it was",
      tool_parts(sent) == (4, 4)
      and [m["content"] for m in sent if m.get("role") == "tool"][-1] == LIMIT)

print("--- a batch where nothing ran ---")

sent = sent_for(_batch(0, ["read_file"] * 5))
check("five refused, none ran: one call and one note go out",
      tool_parts(sent) == (1, 1) and paired(sent))

print("--- the check can fail ---")

_orig = llm_backend._collapse_not_run_calls
llm_backend._collapse_not_run_calls = lambda m: m
try:
    sent = sent_for(_batch(8, refused))
finally:
    llm_backend._collapse_not_run_calls = _orig
check("with the folding off, all 55 refusals go out", tool_parts(sent) == (63, 63))

print()
if _fails:
    print(f"test_refused_calls_folded: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_refused_calls_folded: all checks passed")
