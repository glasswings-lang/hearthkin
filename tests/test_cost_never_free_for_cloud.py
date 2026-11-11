# SPDX-License-Identifier: CC0-1.0
"""A paid model is never described as costing nothing.

`_estimate_call_cost` returned 0.0 for every model that wasn't on OpenRouter,
which included Ollama's cloud models, and the catch-up dialogs turned an
unpriceable model's None into 0.0 as well. Every one of those was then shown
as "local model -- no per-call charge" or "(local -- no charge)": on models
that spend the person's credit on every call.

What this file pins:
  * a local Ollama model costs 0.0 (positive control: local really is free)
  * an Ollama cloud model costs None -- unknown, never zero
  * an OpenRouter model the catalogue can't price costs None
  * every place that words a distillation cost handles None before it can
    reach the "no charge" line
"""

import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="costfree-"))

from llm_backend import _estimate_call_cost  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


check("a local Ollama model costs nothing (control)",
      _estimate_call_cost("llama3.1:8b", 1000, 100) == 0.0)
check("an Ollama cloud model is unpriced, not free",
      _estimate_call_cost("glm-5.2:cloud", 1000, 100) is None)
check("the older -cloud tag is unpriced too",
      _estimate_call_cost("gpt-oss:120b-cloud", 1000, 100) is None)
check("an OpenRouter model with no known price is unpriced",
      _estimate_call_cost("openrouter/no-such/model-xyz", 1000, 100,
                          allow_fetch=False) is None)

# The wording sites: each on-screen "no charge" text must come after a None
# branch in the same block. Matched on the displayed strings, not on the word,
# so comments explaining the rule don't count.
SHOWN = ('"(local — no charge)"', '(local model — no per-call charge).')
for rel in ("frame/memory_mixin.py", "dialogs/edit_kin.py"):
    src = (ROOT / rel).read_text("utf-8")
    hits = [i for s in SHOWN for i in
            [j for j in range(len(src)) if src.startswith(s, j)]]
    check(f"{rel}: the displayed 'no charge' text was found", bool(hits))
    check(f"{rel}: every one comes after an 'is None' branch",
          all("is None" in src[max(0, i - 900):i] for i in hits))

print()
print("FAILED: %d" % len(_fails) if _fails else "ALL PASS")
sys.exit(1 if _fails else 0)
