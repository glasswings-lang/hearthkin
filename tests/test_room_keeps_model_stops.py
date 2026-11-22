# SPDX-License-Identifier: CC0-1.0
"""A room keeps a model's own stop words (22 Nov 2026).

When a request sends its own `stop` list, Ollama uses it INSTEAD of the
model's. Rooms send one ("\\n[" so a kin can't write other kins' lines), so a
voice model trained as a two-person script lost its own stop at "\\nThem:" and
wrote its partner's reply too. Rooms now add the model's own stops to theirs.

Run: python tests/test_room_keeps_model_stops.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import llm_backend as lb  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# The exact shape Ollama's /api/show returned for the real model.
SHOW = {"parameters": 'num_ctx                        8192\n'
                      'repeat_penalty                 1.08\n'
                      'stop                           "</s>"\n'
                      'stop                           "\\nThem:"\n'
                      'temperature                    0.85'}
calls = []


def fake_show(name, timeout=5, host=None):
    calls.append((name, host))
    return SHOW if name == "voice" else None


lb._ollama_show_raw = fake_show
lb._OWN_STOPS_CACHE.clear()

got = lb.model_own_stops("voice", host="http://h:11434")
check("a model's own stop words are read, newline and all",
      got == ["</s>", "\nThem:"])
lb.model_own_stops("voice", host="http://h:11434")
check("...and asked for once, not every turn",
      sum(1 for c in calls if c[0] == "voice") == 1)
check("a model with no stop words gives none", lb.model_own_stops("plain") == [])
before = len(calls)
lb.model_own_stops("plain")
check("a failed lookup isn't retried on the very next turn", len(calls) == before)
calls.clear()
check("a model on OpenRouter isn't looked up in Ollama at all",
      lb.model_own_stops("openrouter/anthropic/claude-sonnet-4") == []
      and not calls)

src = (ROOT / "frame" / "rooms_mixin.py").read_text(encoding="utf-8")
at = src.find('"stop": ["\\n["]')
check("rooms still stop a kin at another speaker's bracket", at != -1)
check("rooms add the model's own stop words to their own",
      "model_own_stops(" in src[at:at + 1200])

if _fails:
    print(f"{len(_fails)} FAILED: " + "; ".join(_fails))
    sys.exit(1)
print("test_room_keeps_model_stops: all checks passed")
