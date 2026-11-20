# SPDX-License-Identifier: CC0-1.0
"""Running out of tool rounds: the kin is told, so it answers.

On 20 Nov 2026 a kin reading deeply through files used every round of tool
use in one reply. The loop then asked it for a final answer with tools taken
away but never said why, and the kin answered with nothing; the person got
"[Tool loop exceeded 8 iterations without a final answer.]" while the kin was
plainly working. The tool-CALL limit already sent a note saying "answer now
with what you have"; the ROUNDS limit didn't.

Pinned here, through the real run_tool_loop with a stand-in model:

  * The final turn after the last round carries a note naming the limit
    and asking for an answer now.
  * The kin's answer to that turn is what comes back.
  * If it still answers with nothing, the fallback says plainly what
    happened, and the result is flagged as having hit a limit.
  * The check can fail: a reply that finishes inside the limit gets no note.

Run: python tests/test_rounds_limit_note.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="roundsnote-"))
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


def a_read(i):
    return [{"id": f"r{i}", "type": "function",
             "function": {"name": "read_file",
                          "arguments": json.dumps({"path": f"part{i}.txt"})}}]


def run(tool_rounds, final_text, max_iterations=3):
    """The model asks for one read per turn for `tool_rounds` turns, then
    gives `final_text`. Returns (result, the history each call saw)."""
    seen = []
    state = {"n": 0}

    def fake_chat(model, history, **k):
        seen.append(list(history))
        state["n"] += 1
        # The final turn is sent without tools: answer then, as a real
        # model told it's out of rounds would.
        if state["n"] <= tool_rounds and k.get("tools"):
            return _Res("", a_read(state["n"]))
        return _Res(final_text)

    orig = llm_backend.chat
    llm_backend.chat = fake_chat
    try:
        res = llm_backend.run_tool_loop(
            "test-model", [{"role": "user", "content": "read it all"}],
            tools=[{"type": "function", "function": {"name": "read_file"}}],
            tool_executor={"read_file": lambda a: "text of " + a["path"]},
            max_iterations=max_iterations)
    finally:
        llm_backend.chat = orig
    return res, seen


def note_in(history):
    return any(m.get("role") == "system"
               and "rounds of tool use" in str(m.get("content", ""))
               for m in history)


# Wants more rounds than allowed, then answers once told.
res, seen = run(tool_rounds=99, final_text="Here's what I found so far.")
check("the last call carries the out-of-rounds note", note_in(seen[-1]))
check("the note names the limit", any(
    "all 3 of its rounds" in str(m.get("content", "")) for m in seen[-1]))
check("no earlier call carried it", not any(note_in(h) for h in seen[:-1]))
check("the kin's answer comes back", res.content == "Here's what I found so far.")
check("flagged as having hit a limit", getattr(res, "hit_tool_limit", False))

# Still answers with nothing: a plain fallback, not the old wording.
res, seen = run(tool_rounds=99, final_text="")
check("fallback says what happened",
      "rounds of tool use" in res.content and "Stopped" in res.content)
check("old wording gone", "without a final answer" not in res.content)

# Control: finishing inside the limit gets no note.
res, seen = run(tool_rounds=1, final_text="All done.")
check("inside the limit: no note anywhere", not any(note_in(h) for h in seen))
check("inside the limit: answer comes back", res.content == "All done.")
check("inside the limit: not flagged", not getattr(res, "hit_tool_limit", False))

if _fails:
    print(f"\n{len(_fails)} FAILED")
    sys.exit(1)
print("\nall passed")
