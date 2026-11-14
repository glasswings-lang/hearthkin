# SPDX-License-Identifier: CC0-1.0
"""Most tool calls at once: a cap on how many calls run from ONE round.

On 13 Nov 2026 a kin given an empty REAPER project put 91 different guesses
into a single batch ("fx --help", "fx 0 -2 --help", dozens of getters)
without reading one answer, against a guide that said to do one thing and
read it back. Neither existing limit could catch it: it was one round out of
a hundred, and 91 calls was under its call ceiling. The repeat guard couldn't
either, since no two calls were the same.

Pinned here, through the real run_tool_loop with a stand-in model:

  * Past the cap, the rest of the batch comes back "not run: read these
    answers first", each with its own result, and the reply carries on to
    the model's next round: paced, not ended.
  * The next round can ask for more, and it runs.
  * The cap is the kin's own setting (max_calls_per_round), read by the
    loop itself so every surface gets it; 0 means no limit.
  * A caller can still pass its own number.
  * The check can fail: with no cap, all 91 run.

Run: python tests/test_round_cap.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="roundcap-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import llm_backend  # noqa: E402
from kin_persistence import DEFAULT_AGENT_CONFIG, save_agent_config  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402


class _Res:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.thinking = ""
        self.tool_calls = tool_calls or []
        self.usage = {}
        self.stopped = False


def guesses(n, start=0):
    return [{"id": f"g{start + i}", "type": "function",
             "function": {"name": "exec",
                          "arguments": json.dumps({"command": f"probe {start + i}"})}}
            for i in range(n)]


def run(batches, **kw):
    ran, seen_by_model = [], []
    turns = list(batches) + [None]

    def fake_chat(model, history, **k):
        seen_by_model.append([m for m in history if m.get("role") == "tool"])
        nxt = turns.pop(0)
        return _Res("done") if nxt is None else _Res("", nxt)

    def ex(args):
        ran.append(args["command"])
        return "answer to " + args["command"]

    orig = llm_backend.chat
    llm_backend.chat = fake_chat
    try:
        res = llm_backend.run_tool_loop(
            "fake-model", [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "exec"}}],
            tool_executor={"exec": ex}, **kw)
    finally:
        llm_backend.chat = orig
    return ran, res, seen_by_model


print("--- the REAPER burst ---")

ran, res, seen = run([guesses(91)], max_calls_per_round=8, max_tool_calls=100)
check("of 91 guesses in one round, 8 run", len(ran) == 8 and ran[0] == "probe 0")
results = [m for m in res.messages_added if m.get("role") == "tool"]
check("every guess still gets its own result", len(results) == 91)
check("the rest say to read the answers first",
      all("read the answers" in m["content"].lower() for m in results[8:])
      and "(8)" in results[8]["content"])
check("the reply isn't ended: the model gets its next round and answers",
      res.content == "done" and not getattr(res, "hit_tool_limit", False))
check("...and in that round it sees the 8 answers",
      len(seen) == 2 and sum(m["content"].startswith("answer to") for m in seen[1]) == 8)

print("--- the next round can ask for more ---")

ran, res, _ = run([guesses(10), guesses(5, start=10)], max_calls_per_round=8)
check("8 run from the first round and 5 from the second",
      len(ran) == 13 and ran[-1] == "probe 14")

print("--- it's the kin's own setting ---")

kin_dir("Bracken").mkdir(parents=True, exist_ok=True)
cfg = dict(DEFAULT_AGENT_CONFIG)
check("the default is 8", cfg.get("max_calls_per_round") == 8)
cfg["max_calls_per_round"] = 3
save_agent_config("Bracken", cfg)
ran, _, _ = run([guesses(20)], kin_name="Bracken")
check("a kin set to 3 runs 3 at once", len(ran) == 3)
cfg["max_calls_per_round"] = 0
save_agent_config("Bracken", cfg)
ran, _, _ = run([guesses(20)], kin_name="Bracken")
check("a kin set to 0 has no limit", len(ran) == 20)
ran, _, _ = run([guesses(20)], kin_name="Bracken", max_calls_per_round=5)
check("a caller's own number wins over the setting", len(ran) == 5)
ran, _, _ = run([guesses(20)])
check("with no kin at all, the default of 8 applies", len(ran) == 8)

print("--- the check can fail ---")

ran, _, _ = run([guesses(91)], max_calls_per_round=0)
check("with no cap, all 91 guesses run", len(ran) == 91)

print()
if _fails:
    print(f"test_round_cap: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_round_cap: all checks passed")
