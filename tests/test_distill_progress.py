# SPDX-License-Identifier: CC0-1.0
"""A distillation reports how far along it is, from inside the call.

Distilling and consolidating are model calls like any other, and they
routinely run twenty to forty minutes. From outside, "still reading a
10k-token bite", "writing steadily" and "wedged" look exactly alike, so
the only cue anyone had was a beep that meant "alive" and nothing more.

These calls now stream and count characters as the summary arrives, and
the frame turns that count into a rising tone (see
StatusVoiceMixin._tick_distilling_sound and tests/test_distilling_sound.py
for the tone; this file is about the count reaching it at all).

Two things are load-bearing here:

  * the count is CUMULATIVE characters, not deltas and not chunks —
    chunk size is a provider detail (Ollama sends a token at a time,
    OpenRouter sends whatever the upstream provider felt like), so a cue
    paced off chunks would run at a different speed per provider;
  * a progress callback that raises must not break the call. The cue
    exists to report on the work, and must never be able to cost a kin
    its memory.

Run: python tests/test_distill_progress.py
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


class _Result:
    def __init__(self, content):
        self.content = content
        self.usage = {}
        self.thinking = ""
        self.tool_calls = []


_seen = {}

DELTAS = ["Talked ", "about the ", "garden."]


def _fake_chat_collect(model, messages, *, on_content=None, **kw):
    """Stand-in for the real streaming collector: hands the caller's
    on_content the same deltas a provider would, then returns the
    assembled result."""
    _seen["ran"] = True
    _seen["stream_kw"] = kw
    for d in DELTAS:
        if on_content is not None:
            on_content(d)
    return _Result("".join(DELTAS))


llm_backend.chat_collect = _fake_chat_collect
if getattr(fs, "ollama", None) is None:
    fs.ollama = object()
fs.list_agents = lambda: ["Tarn"]

CONVO = [{"role": "user", "content": "hello"},
         {"role": "assistant", "content": "hi"}]


# ── the count reaches the caller, cumulatively ────────────────────────
reported = []
result = fs.distill_memory_blocking(
    "Tarn", CONVO, "", "any-model", on_progress=reported.append)

check(_seen.get("ran") is True,
      "positive control: the probe stood in for the real model call")
check(bool(reported), "a distillation reports progress at all")
check(reported == [7, 17, 24],
      "it reports CUMULATIVE characters written, not per-chunk deltas "
      f"(got {reported})")
check(reported == sorted(reported),
      "the count only ever goes up, so a cue built on it can't fall back")
check(result.get("new_entries") == "".join(DELTAS),
      "streaming for the cue didn't change what the caller gets back")


# ── nobody listening: still works, no callback demanded ───────────────
_seen.clear()
result = fs.distill_memory_blocking("Tarn", CONVO, "", "any-model")
check(_seen.get("ran") is True and result.get("new_entries"),
      "a caller that passes no on_progress is unaffected")


# ── a broken cue can't break the distillation ─────────────────────────
def _explode(_n):
    raise RuntimeError("the cue is broken")


_seen.clear()
try:
    result = fs.distill_memory_blocking(
        "Tarn", CONVO, "", "any-model", on_progress=_explode)
    survived = bool(result.get("new_entries"))
except Exception as e:
    survived = False
    print(f"    (raised {type(e).__name__}: {e})")
check(survived,
      "a progress callback that raises does NOT cost the kin its "
      "distillation")


# ── consolidation reports the same way ────────────────────────────────
reported = []
_seen.clear()
out = fs.consolidate_memory_blocking(
    "some existing memory", "any-model", kin_name="Tarn",
    on_progress=reported.append)
check(_seen.get("ran") is True, "positive control: consolidation ran")
check(reported == [7, 17, 24],
      "a consolidation reports progress the same way — it holds the same "
      f"slot and takes just as long (got {reported})")
check("".join(DELTAS) in (out or ""),
      "consolidation still returns its rewritten memory")


print()
if _failures:
    print(f"FAILED ({len(_failures)}):")
    for f in _failures:
        print("  - " + f)
    sys.exit(1)
print("ALL PASS")
