# SPDX-License-Identifier: CC0-1.0
"""Exact repeats within one batch of tool calls run once.

MiMo v2.6 has a known bug (fixed by Xiaomi in a later checkpoint a provider
may not serve) where one reply holds the same call with the same arguments
dozens of times. On 13 Nov 2026 a kin asked for one web page and its reply
held over two hundred identical fetch and search calls; the per-reply ceiling
stopped it at fifteen, all of them the same two calls.

What is pinned, through the real run_tool_loop with a stand-in model:

  * In one batch, a call repeated with byte-identical arguments runs once.
    Every repeat still gets its own result (providers require one per call),
    saying which call it repeats.
  * Different arguments are different calls: reading lines 1-50 and then
    51-100 of one file both run. That was the operator's worry, and it's
    the first thing checked.
  * The same call on a LATER turn runs again: the world may have changed.
  * Park moves and webcam captures are never treated as repeats.
  * Repeats don't use up the per-reply call ceiling, and aren't shown as
    steps.
  * The check can fail: with the repeat check switched off, the flood runs.

Run: python tests/test_tool_repeat_in_batch.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="repeat-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import llm_backend  # noqa: E402


class _Res:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.thinking = ""
        self.tool_calls = tool_calls or []
        self.usage = {}
        self.stopped = False


def _call(i, name, **args):
    return {"id": f"c{i}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def run(batches, *, max_tool_calls=None):
    """Run the real tool loop with a model that answers each turn with the
    next batch of calls, then a plain reply. Returns (ran, shown, result)."""
    ran, shown = [], []
    turns = list(batches) + [None]

    def fake_chat(model, history, **kw):
        nxt = turns.pop(0)
        return _Res("done") if nxt is None else _Res("", nxt)

    def tool(name):
        def runner(args):
            ran.append((name, json.dumps(args, sort_keys=True)))
            return f"{name} result {len(ran)}"
        return runner

    names = {"fetch_url", "memory_search", "read_file", "tff", "use_webcam", "note"}
    orig = llm_backend.chat
    llm_backend.chat = fake_chat
    try:
        res = llm_backend.run_tool_loop(
            "fake-model", [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": n}} for n in names],
            tool_executor={n: tool(n) for n in names},
            on_tool_call=lambda name, args, result, err: shown.append(name),
            max_tool_calls=max_tool_calls)
    finally:
        llm_backend.chat = orig
    return ran, shown, res


print("--- different lines are different calls ---")

ran, _, res = run([[_call(0, "read_file", path="a.md", start_line=1, line_count=50),
                    _call(1, "read_file", path="a.md", start_line=51, line_count=50)]])
check("reading lines 1-50 and 51-100 of one file both run", len(ran) == 2)

print("--- the flood ---")

flood = []
for i in range(200):
    if i % 2 == 0:
        flood.append(_call(i, "fetch_url", url="https://example.org/a.html"))
    else:
        flood.append(_call(i, "memory_search", query="the piece"))
ran, shown, res = run([flood], max_tool_calls=15)
check("two hundred copies of two calls run as two calls", len(ran) == 2)
results = [m for m in res.messages_added if m.get("role") == "tool"]
check("every call still gets its own result",
      len(results) == 200
      and [m["tool_call_id"] for m in results] == [f"c{i}" for i in range(200)])
check("a repeat says which call it repeats",
      "same fetch_url call" in results[2]["content"]
      and "call 1 in this batch" in results[2]["content"])
check("repeats don't use up the call ceiling (the reply wasn't cut off)",
      not getattr(res, "hit_tool_limit", False) and res.content == "done")
check("repeats aren't shown as steps", shown == ["fetch_url", "memory_search"])

print("--- a later turn runs again ---")

ran, _, _ = run([[_call(0, "read_file", path="a.md")],
                 [_call(1, "read_file", path="a.md")]])
check("the same call on the next turn runs again", len(ran) == 2)

print("--- some repeats mean something ---")

ran, _, _ = run([[_call(0, "tff", command="go north"),
                  _call(1, "tff", command="go north"),
                  _call(2, "use_webcam"), _call(3, "use_webcam")]])
check("two identical park moves in one batch both run",
      [n for n, _ in ran].count("tff") == 2)
check("two webcam captures in one batch both run",
      [n for n, _ in ran].count("use_webcam") == 2)

print("--- argument order doesn't hide a repeat ---")

a = {"id": "x0", "type": "function", "function": {
    "name": "note", "arguments": '{"file": "j.md", "text": "hi"}'}}
b = {"id": "x1", "type": "function", "function": {
    "name": "note", "arguments": '{"text": "hi", "file": "j.md"}'}}
ran, _, _ = run([[a, b]])
check("the same arguments in a different order count as a repeat", len(ran) == 1)

print("--- the check can fail ---")

_orig = llm_backend._REPEATABLE_IN_BATCH
llm_backend._REPEATABLE_IN_BATCH = _orig | {"fetch_url", "memory_search"}
try:
    ran, _, res = run([flood], max_tool_calls=15)
finally:
    llm_backend._REPEATABLE_IN_BATCH = _orig
check("with the repeat check off, the flood runs until the ceiling",
      len(ran) == 15 and getattr(res, "hit_tool_limit", False))

print()
if _fails:
    print(f"test_tool_repeat_in_batch: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_tool_repeat_in_batch: all checks passed")
