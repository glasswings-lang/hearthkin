# SPDX-License-Identifier: CC0-1.0
"""Distillation retries once, thinking off, when a model burns its whole
budget deliberating and says nothing — regardless of which model it is.

This replaces an earlier fix that force-disabled thinking for any model
whose name contained "gemma", reasoning from a single live incident (a kin
capped at num_predict=400, Ollama done_reason "length", content ""). That
incident was real, but the fix generalised from one small-budget observation
to a permanent blacklist by brand name, and it was the wrong axis besides:
the same failure — content empty, thinking non-empty — is independently
documented in run_tool_loop's retry-once comment against qwen36-opus, a
different family entirely (see tests/test_tool_loop_bail.py). Guessing which
model families can reason and excluding them by name is exactly what
tests/test_ollama_think_off.py already warns against.

So distill_memory_blocking now detects the failure instead of predicting it:
empty content + non-empty thinking -> one retry with thinking forced off,
same shape as run_tool_loop's existing recovery. This file pins that no
model name is consulted anywhere in the decision, only the content of the
result.

Run: python tests/test_distill_thinking_retry.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_failures = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _failures.append(label)


import frame_shared as fs  # noqa: E402
import llm_backend  # noqa: E402

fs.list_agents = lambda: ["Tarn"]
if getattr(fs, "ollama", None) is None:
    fs.ollama = object()

CONVO = [{"role": "user", "content": "hello"},
         {"role": "assistant", "content": "hi"}]


class _Result:
    def __init__(self, content="", thinking=""):
        self.content = content
        self.thinking = thinking
        self.usage = {}
        self.tool_calls = []


def _scripted(*results):
    """Stand in for llm_backend.chat_collect: returns each _Result in
    order, one per call, and records the think_effort each call was made
    with so a test can assert on it without touching model internals."""
    calls = []

    def _fake(model, messages, *, on_content=None, think_effort=None, **kw):
        calls.append(think_effort)
        idx = min(len(calls) - 1, len(results) - 1)
        return results[idx]

    return _fake, calls


# ── a genuinely silent model (gemma-named, to prove the name is irrelevant) ─
fake, calls = _scripted(_Result(content="", thinking="thinking hard about it..."),
                        _Result(content="Talked about the garden.", thinking=""))
llm_backend.chat_collect = fake
result = fs.distill_memory_blocking(
    "Tarn", CONVO, "", "gemma4:latest", think_effort="medium")
check(len(calls) == 2, "empty-content-with-thinking triggers exactly one retry")
check(calls[0] == "medium", "first attempt uses the caller's think_effort")
check(calls[1] == "off", "the retry forces thinking off")
check("Talked about the garden." in (result.get("new_entries") or ""),
      "the retry's content is what actually gets kept")


# ── the SAME failure on a non-gemma model — the fix must not care ──────────
fake, calls = _scripted(_Result(content="", thinking="deliberating..."),
                        _Result(content="Noted the weather.", thinking=""))
llm_backend.chat_collect = fake
result = fs.distill_memory_blocking(
    "Tarn", CONVO, "", "qwen3.6:27b-nvfp4", think_effort="medium")
check(len(calls) == 2, "the same recovery fires on a qwen model, unprompted by name")
check("Noted the weather." in (result.get("new_entries") or ""),
      "recovery works identically regardless of model family")


# ── positive control: a model that answers on the first try never retries ──
fake, calls = _scripted(_Result(content="All good here.", thinking="a little thought"))
llm_backend.chat_collect = fake
result = fs.distill_memory_blocking(
    "Tarn", CONVO, "", "gemma4:latest", think_effort="medium")
check(len(calls) == 1, "a model that actually spoke is never retried")
check(calls[0] == "medium",
      "a healthy gemma run keeps the caller's think_effort — no blanket ban")


# ── both attempts come back empty: that's "nothing new", not a bug ─────────
fake, calls = _scripted(_Result(content="", thinking="still thinking"),
                        _Result(content="", thinking=""))
llm_backend.chat_collect = fake
result = fs.distill_memory_blocking(
    "Tarn", CONVO, "existing memory here", "gemma4:latest", think_effort="medium")
check(len(calls) == 2, "a genuinely empty distillation still retries exactly once")
check(result.get("new_entries") == "",
      "and then it's just distillation's ordinary nothing-new case, not a crash")
check("existing memory here" in (result.get("memory") or ""),
      "existing memory survives untouched when there's truly nothing to add")


# ── a plain empty reply with NO thinking is left alone — not this failure ──
fake, calls = _scripted(_Result(content="", thinking=""))
llm_backend.chat_collect = fake
result = fs.distill_memory_blocking(
    "Tarn", CONVO, "", "gemma4:latest", think_effort="off")
check(len(calls) == 1,
      "empty content with NO thinking is an ordinary quiet turn, not retried")


print()
if _failures:
    print("FAILED (%d): %s" % (len(_failures), "; ".join(_failures)))
    sys.exit(1)
print("all distill-thinking-retry checks passed")
