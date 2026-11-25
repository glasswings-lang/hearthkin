"""The room heads-up about kin sharing one Ollama (24 Nov 2026).

It used to fire whenever a room's kin used different models and said, as
fact, "Ollama swaps the next kin's model weights into GPU memory" -- even
when the kin were on an online provider or on different machines, where
nothing waits. The person wants the heads-up, only where it is true: two or
more kin with DIFFERENT models on the SAME Ollama machine. Plain Python.
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


import frame.rooms_mixin as rm  # noqa: E402

CONFIGS = {}
HOSTS = {"": "", "Mac mini": "http://192.168.1.50:11434",
         "This machine": "http://localhost:11434"}
rm.load_agent_config = lambda k: CONFIGS.get(k, {})
rm.resolve_kin_ollama_host = lambda n: HOSTS.get(n or "", "")
rm.llm_backend._resolve_ollama_host = lambda: "http://localhost:11434"


class Stub:
    _shared_ollama_note = rm.RoomsMixin._shared_ollama_note


def note(order, cfgs):
    CONFIGS.clear()
    CONFIGS.update(cfgs)
    s = Stub()
    s._room_round_order = order
    return s._shared_ollama_note()


hosted = "openrouter/anthropic/claude-x"

check(note(["A", "B"], {"A": {"model": "qwen3:8b"}, "B": {"model": "qwen3:8b"}}) == "",
      "same model on the same Ollama: no note")
check(note(["A", "B"], {"A": {"model": "qwen3:8b"}, "B": {"model": hosted}}) == "",
      "one local, one online: no note (nothing waits)")
check(note(["A", "B"], {"A": {"model": hosted}, "B": {"model": "openrouter/x/y"}}) == "",
      "both online: no note")
check(note(["A", "B"], {
    "A": {"model": "qwen3:8b"},
    "B": {"model": "gemma3:12b", "ollama_host_name": "Mac mini"}}) == "",
      "different models on different machines: no note")

n = note(["A", "B", "C"], {
    "A": {"model": "qwen3:8b"},
    "B": {"model": "gemma3:12b", "ollama_host_name": "This machine"},
    "C": {"model": hosted}})
check("Heads-up" in n and "A on qwen3:8b" in n and "B on gemma3:12b" in n,
      "different models on the same machine (unset = this computer): note names both")
check("C" not in n.split("):", 1)[1], "the online kin isn't named")
check("If that machine can't hold" in n, "says 'if', since Hearthkin can't know memory")
check("GPU" not in n, "claims nothing about a GPU")

n2 = note(["A", "B"], {
    "A": {"model": "qwen3:8b", "ollama_host_name": "Mac mini"},
    "B": {"model": "gemma3:12b", "ollama_host_name": "Mac mini"}})
check("(Mac mini)" in n2, "names the shared machine")

if _failures:
    print(f"{len(_failures)} FAILURE(S): " + ", ".join(_failures))
    sys.exit(1)
print("test_room_shared_ollama_note: all checks passed")
