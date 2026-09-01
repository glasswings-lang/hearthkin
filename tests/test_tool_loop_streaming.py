"""Standalone test for the tool-loop streaming keystone (llm_backend).

Verifies the two new helpers that let run_tool_loop stream its talking turn
while still resolving tool calls:

  1. _accumulate_stream_tool_calls assembles OpenRouter-style DELTAS (indexed,
     string-fragment arguments) into whole tool_calls, and appends Ollama-style
     WHOLE tool_calls unchanged.
  2. _chat_collect_streaming forwards content deltas to on_content live AND
     returns a blocking-shaped ChatResult (content + tool_calls + usage), so
     it's a drop-in for chat(stream=False) inside the loop.

Run: python tests/test_tool_loop_streaming.py   (or via tests/run_all.py)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm_backend  # noqa: E402
from llm_backend import (  # noqa: E402
    Chunk, _accumulate_stream_tool_calls, _chat_collect_streaming,
)

_failures = []


def check(cond, label):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _failures.append(label)


# 1. OpenRouter-style deltas: name + id on the first fragment, arguments split.
acc = []
_accumulate_stream_tool_calls(acc, [{"index": 0, "id": "call_a", "type": "function",
                                     "function": {"name": "read_file", "arguments": '{"pa'}}])
_accumulate_stream_tool_calls(acc, [{"index": 0, "function": {"arguments": 'th": "x"}'}}])
check(len(acc) == 1, "OR deltas assemble into one tool_call")
check(acc[0]["function"]["name"] == "read_file", "OR delta name preserved")
check(acc[0]["function"]["arguments"] == '{"path": "x"}', "OR delta arguments concatenated")
check(acc[0]["id"] == "call_a", "OR delta id preserved")

# 2. Two OR tool calls arriving out of index order.
acc = []
_accumulate_stream_tool_calls(acc, [{"index": 1, "function": {"name": "b", "arguments": "{}"}}])
_accumulate_stream_tool_calls(acc, [{"index": 0, "function": {"name": "a", "arguments": "{}"}}])
check(len(acc) == 2 and acc[0]["function"]["name"] == "a" and acc[1]["function"]["name"] == "b",
      "OR two tool calls placed by index regardless of arrival order")

# 3. Ollama-style whole tool_call (no index, dict arguments, one chunk).
acc = []
_accumulate_stream_tool_calls(acc, [{"function": {"name": "note", "arguments": {"text": "hi"}}}])
check(len(acc) == 1 and acc[0]["function"]["name"] == "note", "Ollama whole tool_call appended")
check(acc[0]["function"]["arguments"] == {"text": "hi"}, "Ollama dict arguments preserved")

_orig_chat = llm_backend.chat

# 4. Talking turn: content forwarded live + result assembled + usage captured.
def fake_chat_talking(model, messages, **kwargs):
    yield Chunk(content="Hello, ")
    yield Chunk(content="SpeakerFifteen.")
    yield Chunk(done=True, usage={"prompt_tokens": 5, "completion_tokens": 3})

try:
    llm_backend.chat = fake_chat_talking
    seen = []
    res = _chat_collect_streaming("m", [], on_content=seen.append)
    check(seen == ["Hello, ", "SpeakerFifteen."], "content deltas forwarded live to on_content")
    check(res.content == "Hello, SpeakerFifteen.", "assembled content matches concatenation")
    check(res.tool_calls == [], "no tool_calls on a talking turn")
    check(res.usage.get("prompt_tokens") == 5, "usage captured from the done chunk")

    # 5. Tool-calling turn streamed (Ollama whole tool_call on a chunk).
    def fake_chat_tool(model, messages, **kwargs):
        yield Chunk(tool_calls=[{"function": {"name": "read_file",
                                              "arguments": {"path": "x"}}}])
        yield Chunk(done=True, usage={"prompt_tokens": 9})
    llm_backend.chat = fake_chat_tool
    res2 = _chat_collect_streaming("m", [], on_content=lambda t: None)
    check(len(res2.tool_calls) == 1
          and res2.tool_calls[0]["function"]["name"] == "read_file",
          "streamed tool_call assembled into the returned ChatResult")

    # 6. on_content raising must NOT break collection.
    def fake_chat_boom(model, messages, **kwargs):
        yield Chunk(content="x")
        yield Chunk(done=True, usage={})
    llm_backend.chat = fake_chat_boom

    def boom(_):
        raise RuntimeError("render died")
    res3 = _chat_collect_streaming("m", [], on_content=boom)
    check(res3.content == "x", "on_content exception swallowed; content still assembled")
finally:
    llm_backend.chat = _orig_chat

if _failures:
    print(f"\n{len(_failures)} FAILURE(S)")
    sys.exit(1)
print("\nAll tool-loop streaming checks passed.")
