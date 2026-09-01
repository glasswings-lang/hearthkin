# SPDX-License-Identifier: CC0-1.0
"""Standalone test for run_tool_loop's result-aware stuck-loop guard.

The guard used to bail whenever the model repeated a tool call with the same
arguments — assuming same args meant same result and no progress. But a tool
like tff (the park game) returns a DIFFERENT result for identical args (each "dig"
finds different materials), so it IS making progress and must not be cut off.
The guard now bails only when the call signature AND the results both repeat.

These cases stub llm_backend.chat so no network is touched: one where the tool
returns a varying result (must run to the iteration cap, never falsely bailed)
and one where it returns an identical result (must bail after the repeat).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm_backend  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


class FakeResult:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.thinking = ""
        self.usage = None
        self.messages_added = []


def run_scenario(result_for_call, max_iterations=5):
    """Drive run_tool_loop with a stubbed chat() that keeps asking to 'dig'
    while tools are offered, and a 'dig' executor whose output is decided by
    result_for_call(n). Returns the number of times the tool actually ran."""
    def fake_chat(model, messages, options=None, stream=False,
                  tools=None, cache=False, **kw):
        if tools:  # a loop turn: ask for another identical dig
            return FakeResult(tool_calls=[
                {"id": "1", "function": {"name": "dig", "arguments": "{}"}}])
        return FakeResult(content="[final]")  # the no-tools final call

    exec_calls = {"n": 0}

    def dig(args):
        exec_calls["n"] += 1
        return result_for_call(exec_calls["n"])

    orig = llm_backend.chat
    llm_backend.chat = fake_chat
    try:
        llm_backend.run_tool_loop(
            "fakemodel",
            [{"role": "user", "content": "play"}],
            tools=[{"type": "function", "function": {"name": "dig"}}],
            tool_executor={"dig": dig},
            max_iterations=max_iterations,
        )
    finally:
        llm_backend.chat = orig
    return exec_calls["n"]


def run_scripted(script):
    """Drive run_tool_loop with a chat() that returns the given FakeResults in
    order (the last one repeats if the loop calls again). No real tools.
    Returns (final_content, number_of_chat_calls)."""
    calls = {"n": 0}

    def fake_chat(model, messages, options=None, stream=False,
                  tools=None, cache=False, **kw):
        i = min(calls["n"], len(script) - 1)
        calls["n"] += 1
        return script[i]

    orig = llm_backend.chat
    llm_backend.chat = fake_chat
    try:
        res = llm_backend.run_tool_loop(
            "fakemodel", [{"role": "user", "content": "hi"}],
            tools=[], tool_executor={}, max_iterations=5)
    finally:
        llm_backend.chat = orig
    return (res.content or ""), calls["n"]


def think_result(content):
    """An empty- or full-content result that ALSO carries thinking — the
    'thinking model' shape qwen36-opus produces."""
    r = FakeResult(content=content, tool_calls=[])
    r.thinking = "As Vesper I should say something warm here..."
    return r


def main():
    # Varying result (tff's "dig"): IS progress -> never falsely
    # bailed, so it runs every iteration up to the cap (5 digs).
    n_varying = run_scenario(lambda i: f"found {i} sticks", max_iterations=5)
    check("varying result is not falsely bailed (runs to cap)", n_varying == 5)

    # Identical result: genuinely stuck -> bails right after the repeat, so the
    # tool runs exactly twice (the original call + the one repeat that confirms
    # no progress), not all 5 iterations.
    n_stuck = run_scenario(lambda i: "found nothing", max_iterations=5)
    check("identical result bails after the repeat (runs twice)", n_stuck == 2)

    # Thinking-model-went-silent: empty content + thinking -> re-sample once,
    # and the retry's spoken reply is what's returned (not the silence).
    content, n = run_scripted([think_result(""), think_result("here i am!")])
    check("empty-but-thinking retries and returns the spoken reply",
          content == "here i am!" and n == 2)

    # Persistent thought-but-silent -> retried exactly ONCE, then gives up
    # (no infinite loop): two chat calls, still empty.
    content, n = run_scripted([think_result("")])
    check("persistent empty+thinking retries once then stops",
          content == "" and n == 2)

    # Empty content with NO thinking (a non-thinking model genuinely saying
    # nothing) is left alone — returned immediately, not retried.
    content, n = run_scripted([FakeResult(content="", tool_calls=[])])
    check("empty with no thinking is not retried (returned immediately)",
          content == "" and n == 1)

    if _fails:
        print(f"\nFAILED {len(_fails)}: " + ", ".join(_fails))
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
