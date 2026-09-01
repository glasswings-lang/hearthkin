# SPDX-License-Identifier: CC0-1.0
"""Which upstream served an OpenRouter call gets recorded, and reads back.

OpenRouter routes one model name to several providers, and they do not serve
it identically — different chat templates, different sampler defaults, some
prepend framing of their own. So a kin's register can shift with the provider
rather than the model, and telling those apart was impossible: the field was
in every response and thrown away.

The load-bearing test here is the LAST one. usage.log is parsed by a strictly
ordered regex, so inserting a field mid-line without teaching the parser would
drop every OpenRouter row out of the cost history — silently, looking exactly
like a quiet month.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="usageprov-"))

import kin_persistence as K  # noqa: E402
import llm_backend as LB  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# 1. The provider is lifted out of a blocking response, alongside the cost.
u = LB._usage_with_cost({
    "provider": "DeepInfra",
    "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.002},
})
check("1 provider captured from a blocking response", u.get("provider") == "DeepInfra")
check("1 cost still captured too", u.get("cost") == 0.002)

# 2. Local Ollama responses carry no provider — the field stays absent rather
#    than becoming a placeholder that would read as a real answer.
u_local = LB._usage_with_cost({"usage": {"prompt_tokens": 100}})
check("2 no provider on a local call", "provider" not in u_local)

# 3. Streaming: OpenRouter names the provider on the FIRST frame and sends
#    authoritative token counts on the LAST. Taking the last frame wholesale
#    loses the name every time — this is the case that actually bites.
first = {"provider": "Together", "usage": {"prompt_tokens": 0, "cost": 0.0}}
last = {"usage": {"prompt_tokens": 900, "completion_tokens": 40, "cost": 0.004}}
merged = LB._merge_streaming_usage(LB._merge_streaming_usage({}, first), last)
check("3 provider survives the streaming merge", merged.get("provider") == "Together")
check("3 ...and the authoritative token count wins",
      merged.get("prompt_tokens") == 900)
check("3 ...and the cost is right", merged.get("cost") == 0.004)

# 4. It reaches the log line.
K.append_usage_log(kin="Tester", model="openrouter/google/gemma-4-31b-it",
                   prompt_tokens=1200, completion_tokens=80, est_cost=0.0012,
                   surface="telegram-dm", real_cost=0.0011, provider="DeepInfra")
K.append_usage_log(kin="Tester", model="gemma4:31b", prompt_tokens=1200,
                   completion_tokens=80, est_cost=None, surface="desktop",
                   prefill_secs=12.0, gen_secs=6.0)
lines = K.USAGE_LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
check("4 the provider is written to the log", "provider=DeepInfra" in lines[0])
check("4 a local call writes no provider field", "provider=" not in lines[1])

# 5. THE ONE THAT MATTERS: the parser still reads both shapes. A new field in
#    the middle of a strictly ordered line silently drops rows otherwise.
rows = K.parse_usage_log()
check("5 both rows still parse (no silent loss)", len(rows) == 2)
if len(rows) == 2:
    check("5 the provider reads back", rows[0].get("provider") == "DeepInfra")
    check("5 a local row has no provider", rows[1].get("provider") is None)
    check("5 costs still parse alongside it",
          rows[0].get("real_cost") == 0.0011 and rows[0].get("in") == 1200)
    check("5 local timings still parse", rows[1].get("surface") == "desktop")

# 6. Positive control: the old line shape, with no provider, must still parse —
#    every line written before today looks like this.
K.USAGE_LOG_PATH.write_text(
    "2026-08-01T10:00:00 kin=Old model=openrouter/x in=10 out=2 cached=0 "
    "est_cost=$0.0001 real_cost=$0.0001 surface=desktop\n", encoding="utf-8")
old = K.parse_usage_log()
check("6 pre-existing log lines still parse", len(old) == 1)
check("6 ...with provider reported as unknown, not invented",
      old and old[0].get("provider") is None)

print()
if _fails:
    print(f"{len(_fails)} FAILED: " + "; ".join(_fails))
    sys.exit(1)
print("all usage-provider checks passed")
