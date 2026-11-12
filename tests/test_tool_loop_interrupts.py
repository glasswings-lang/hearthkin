# SPDX-License-Identifier: CC0-1.0
"""Standalone tests: a stop, or a message from the person, lands BETWEEN the
tool calls of one model turn -- not only between model turns (no network).

A kin's model once put about sixty `exec` calls in a single reply. /stop was
only checked between model replies, so it couldn't land until every one of
them had run, and the only way out was closing the app. These tests drive
run_tool_loop with a stand-in model and check:

  - stop before call 3 of 6: calls 1-2 run, 3-6 don't, every call still has
    a result in history, and the turn ends marked stopped;
  - a message before call 3 of 6: the same holding, then the message is in
    the conversation and the model's next turn sees it and answers it;
  - with neither, all six run (the control);
  - a broken interject callback never breaks the turn.
"""

import os
import sys
import tempfile

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="hk-interrupt-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm_backend  # noqa: E402
from llm_backend import ChatResult  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def batch(n):
    return [{"id": f"c{i}", "type": "function",
             "function": {"name": "peek", "arguments": f'{{"n": {i}}}'}}
            for i in range(1, n + 1)]


class FakeModel:
    """First turn: six calls at once. Later turns: a plain reply that says
    whether the last thing it saw was the person's message."""
    def __init__(self):
        self.turns = 0
        self.seen = []

    def __call__(self, model, history, **kw):
        self.turns += 1
        self.seen.append(list(history))
        if self.turns == 1:
            return ChatResult(content="Reading everything.", thinking="",
                              tool_calls=batch(6), usage={})
        last_user = [m for m in history if m.get("role") == "user"][-1]
        return ChatResult(content=f"Answering: {last_user['content']}",
                          thinking="", tool_calls=[], usage={})


def run(should_stop=None, interject=None):
    fake = FakeModel()
    ran = []

    def peek(args):
        ran.append(args.get("n"))
        return f"line {args.get('n')}"
    real = llm_backend.chat
    llm_backend.chat = fake
    try:
        res = llm_backend.run_tool_loop(
            "stub", [{"role": "user", "content": "tend, please"}],
            tools=[{"type": "function", "function": {"name": "peek"}}],
            tool_executor={"peek": peek},
            should_stop=should_stop, interject=interject, max_iterations=5)
    finally:
        llm_backend.chat = real
    return res, ran, fake


def tool_results(res):
    return [m for m in res.messages_added if m.get("role") == "tool"]


def main():
    # Control: nothing interrupts, all six run.
    res, ran, fake = run()
    check("control: all six calls run", ran == [1, 2, 3, 4, 5, 6])
    check("control: the turn ends with a reply", (res.content or "").startswith("Answering"))

    # Stop lands between calls: the stop is asked for once two have run.
    progress = []

    def should_stop():
        return len(progress) >= 2
    real = llm_backend.chat
    fake = FakeModel()
    llm_backend.chat = fake
    try:
        res = llm_backend.run_tool_loop(
            "stub", [{"role": "user", "content": "tend, please"}],
            tools=[{"type": "function", "function": {"name": "peek"}}],
            tool_executor={"peek": lambda a: progress.append(a.get("n")) or "ok"},
            should_stop=should_stop, max_iterations=5)
    finally:
        llm_backend.chat = real
    check("stop: only the first two calls ran", progress == [1, 2])
    check("stop: the turn is marked stopped", getattr(res, "stopped", False) is True)
    tr = tool_results(res)
    check("stop: every one of the six calls still has a result", len(tr) == 6)
    check("stop: the held ones say they did not run",
          all("not run" in t["content"] for t in tr[2:]) and
          not any("not run" in t["content"] for t in tr[:2]))
    check("stop: no further model call was made", fake.turns == 1)

    # A message lands between calls, and gets answered.
    progress = []
    sent = {"done": False}

    def interject():
        if len(progress) >= 2 and not sent["done"]:
            sent["done"] = True
            return [{"role": "user", "content": "wait, stop reading the journals",
                     "ts": "2026-11-12T03:16:00"}]
        return []
    fake = FakeModel()
    llm_backend.chat = fake
    try:
        res = llm_backend.run_tool_loop(
            "stub", [{"role": "user", "content": "tend, please"}],
            tools=[{"type": "function", "function": {"name": "peek"}}],
            tool_executor={"peek": lambda a: progress.append(a.get("n")) or "ok"},
            interject=interject, max_iterations=5)
    finally:
        llm_backend.chat = real
    check("message: only the first two calls ran", progress == [1, 2])
    tr = tool_results(res)
    check("message: every call still has a result", len(tr) == 6)
    check("message: the held ones point at the new message",
          all("sent a message" in t["content"] for t in tr[2:]))
    users = [m for m in res.messages_added if m.get("role") == "user"]
    check("message: it is in the turn's saved steps, as given",
          len(users) == 1 and users[0]["content"] == "wait, stop reading the journals"
          and users[0].get("ts") == "2026-11-12T03:16:00")
    check("message: the model's next turn saw it last",
          fake.turns == 2 and fake.seen[1][-1]["content"] == "wait, stop reading the journals")
    check("message: and answered it", res.content == "Answering: wait, stop reading the journals")
    check("message: the turn is not marked stopped", not getattr(res, "stopped", False))

    # A message that arrives before the loop's first model call is seen too.
    early = {"done": False}

    def early_interject():
        if not early["done"]:
            early["done"] = True
            return [{"role": "user", "content": "also, the left hand"}]
        return []
    fake = FakeModel()
    llm_backend.chat = fake
    try:
        res = llm_backend.run_tool_loop(
            "stub", [{"role": "user", "content": "tend, please"}],
            tools=[], tool_executor={}, interject=early_interject, max_iterations=3)
    finally:
        llm_backend.chat = real
    check("an early message is in front of the model's first turn",
          fake.seen[0][-1]["content"] == "also, the left hand")

    # A broken callback never breaks the turn.
    def boom():
        raise RuntimeError("queue gone")
    progress = []
    fake = FakeModel()
    llm_backend.chat = fake
    try:
        res = llm_backend.run_tool_loop(
            "stub", [{"role": "user", "content": "tend, please"}],
            tools=[{"type": "function", "function": {"name": "peek"}}],
            tool_executor={"peek": lambda a: progress.append(a.get("n")) or "ok"},
            interject=boom, max_iterations=5)
    finally:
        llm_backend.chat = real
    check("a failing interject callback is ignored", progress == [1, 2, 3, 4, 5, 6])

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        sys.exit(1)
    print("ALL PASS")



if __name__ == "__main__":
    main()
