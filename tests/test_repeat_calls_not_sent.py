# SPDX-License-Identifier: CC0-1.0
"""Copies of one call in one batch are sent to the model once.

test_tool_repeat_in_batch.py pins that a repeat only RUNS once. This file
pins what is SENT afterwards: on 14 Nov 2026 a kin's batches held 60 to 380
copies of one call each, and every one of them, with its "[not run: same
call]" note, went out again as input on every later turn. That cost money
the operator couldn't see, and showed the model that a batch of hundreds was
normal.

What is pinned, through the real chat() with a stand-in provider:

  * A batch of copies goes out as one call and one result.
  * Different arguments are different calls, and both go out.
  * Copies that really ran and came back with different results (older
    floods, from before the batch guard) stay: they are real history.
  * Park moves and webcam captures always go out, repeats and all.
  * A local model's blank ids are paired by position.
  * The stored history is not changed.
  * The check can fail: with the filter switched off, the copies go out.

Run: python tests/test_repeat_calls_not_sent.py
"""

import copy
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="repeat-sent-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import llm_backend  # noqa: E402


def _call(cid, name, **args):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def _flood(copies, *, blank_ids=False, ran_differently=False):
    calls, results = [], []
    for k in range(copies):
        cid = "" if blank_ids else f"c{k}"
        calls.append(_call(cid, "exec", command="Get-Content reply.txt"))
        if k == 0:
            text = "the reply"
        elif ran_differently:
            text = f"the reply, changed {k}"
        else:
            text = llm_backend._NOT_RUN_REPEAT.format(name="exec", n=1)
        results.append({"role": "tool", "tool_call_id": cid, "content": text})
    return ([{"role": "user", "content": "check the reply"},
             {"role": "assistant", "content": "", "tool_calls": calls}]
            + results
            + [{"role": "assistant", "content": "It says the reply."},
               {"role": "user", "content": "thanks"}])


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
        except Exception as e:  # the stand-in's reply shape may not suit chat
            if not _sent:
                raise
            _ = e
    finally:
        llm_backend._chat_openrouter_blocking = o1
        llm_backend._chat_ollama_blocking = o2
    return _sent[-1]


def tool_parts(msgs):
    calls = sum(len(m.get("tool_calls") or []) for m in msgs
                if m.get("role") == "assistant")
    results = sum(1 for m in msgs if m.get("role") == "tool")
    return calls, results


print("--- a batch of copies goes out once ---")

hist = _flood(200)
before = copy.deepcopy(hist)
sent = sent_for(hist)
check("two hundred copies go out as one call", tool_parts(sent) == (1, 1))
check("the one result that goes out is the real one",
      [m["content"] for m in sent if m.get("role") == "tool"] == ["the reply"])
check("the rest of the conversation goes out unchanged",
      [m.get("content") for m in sent if m.get("role") in ("user", "assistant")
       ][-2:] == ["It says the reply.", "thanks"])
check("the stored history isn't changed", hist == before)

print("--- different arguments are different calls ---")

calls = [_call("a", "read_file", path="x.md", start_line=1),
         _call("b", "read_file", path="x.md", start_line=51)]
hist = [{"role": "user", "content": "read"},
        {"role": "assistant", "content": "", "tool_calls": calls},
        {"role": "tool", "tool_call_id": "a", "content": "lines 1-50"},
        {"role": "tool", "tool_call_id": "b", "content": "lines 51-100"},
        {"role": "user", "content": "ok"}]
check("reading two parts of one file both go out",
      tool_parts(sent_for(hist)) == (2, 2))

print("--- copies that really ran differently stay ---")

check("an older flood whose copies came back different still goes out whole",
      tool_parts(sent_for(_flood(5, ran_differently=True))) == (5, 5))

print("--- some repeats mean something ---")

calls = [_call("p", "tff", command="go north"), _call("q", "tff", command="go north")]
hist = [{"role": "user", "content": "walk"},
        {"role": "assistant", "content": "", "tool_calls": calls},
        {"role": "tool", "tool_call_id": "p", "content": "you walk north"},
        {"role": "tool", "tool_call_id": "q", "content": "you walk north"},
        {"role": "user", "content": "ok"}]
check("two identical park moves both go out", tool_parts(sent_for(hist)) == (2, 2))

print("--- a local model's blank ids ---")

sent = sent_for(_flood(30, blank_ids=True), model="qwen3:8b")
check("blank-id copies are paired by position and go out once",
      tool_parts(sent) == (1, 1)
      and [m["content"] for m in sent if m.get("role") == "tool"] == ["the reply"])

print("--- the check can fail ---")

_orig = llm_backend._drop_repeat_tool_calls
llm_backend._drop_repeat_tool_calls = lambda m: m
try:
    sent = sent_for(_flood(200))
finally:
    llm_backend._drop_repeat_tool_calls = _orig
check("with the filter off, all two hundred copies go out",
      tool_parts(sent) == (200, 200))

print()
if _fails:
    print(f"test_repeat_calls_not_sent: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_repeat_calls_not_sent: all checks passed")
