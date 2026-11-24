# SPDX-License-Identifier: CC0-1.0
"""Hearthkin learns from real wake-ups which models call tools
(24 Nov 2026, from an outside review; "probably the highest-value follow-up",
in the maintainer's own notes of 22 Aug 2026).

Tool calling was measured as all-or-nothing per model, and a model that
can't call tools writes a fake call, invents its result and reasons from it.
The verdict only ever came from the Test button or a model swap, so such a
model kept being re-prompted, harder, every night. Pinned:

  * a tend with work, tools offered, and no tool call teaches "doesn't call
    tools" for a model with no verdict;
  * any real tool call teaches "calls tools", and overrides an old "doesn't";
  * an empty or stopped reply teaches nothing;
  * "doesn't" is never written over "does" by a quiet night;
  * with "doesn't" on record, the harder re-prompt is skipped;
  * the Kin settings line says each of the three states in plain words.

Run: python tests/test_tool_verdict_from_tends.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="verdict-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import hearthkin_cron as hc  # noqa: E402
import model_utils as mu  # noqa: E402
from llm_backend import ChatResult  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


mu.clear_tool_probe_cache()
quiet = ChatResult(content="I tended my notes and filed the color log.")
called = ChatResult(content="done", messages_added=[
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "1", "type": "function",
         "function": {"name": "read_staging", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "1", "content": "notes"}])

hc._learn_tool_verdict("m-quiet", quiet, True, ["read_staging"])
check("a tend with work and no tool call teaches 'doesn't call tools'",
      mu.probed_tool_calling("m-quiet") is False)
check("...and then the harder re-prompt is skipped", hc._tools_known_dead("m-quiet"))

hc._learn_tool_verdict("m-quiet", called, True, ["read_staging"])
check("a real tool call overrides it", mu.probed_tool_calling("m-quiet") is True)
hc._learn_tool_verdict("m-quiet", quiet, True, ["read_staging"])
check("a quiet night never writes 'doesn't' over 'does'",
      mu.probed_tool_calling("m-quiet") is True)

hc._learn_tool_verdict("m-empty", ChatResult(content=""), True, ["read_staging"])
check("an empty reply teaches nothing", mu.probed_tool_calling("m-empty") is None)
hc._learn_tool_verdict("m-stop", ChatResult(content="x", stopped=True), True, ["read_staging"])
check("a stopped reply teaches nothing", mu.probed_tool_calling("m-stop") is None)
hc._learn_tool_verdict("m-nowork", quiet, False, ["read_staging"])
check("a tend with no work teaches nothing", mu.probed_tool_calling("m-nowork") is None)

src = (ROOT / "hearthkin_cron.py").read_text(encoding="utf-8")
check("both tend paths skip the re-prompt for a known non-caller",
      src.count("and not _tools_known_dead(model)") == 2)
check("both tend paths learn from the outcome",
      src.count("    _learn_tool_verdict(model, result, staging_had_work, schemas)") == 2)

from dialogs.edit_kin import tool_verdict_line  # noqa: E402
check("Kin settings says 'calls tools'", "calls tools: Hearthkin has seen" in tool_verdict_line("m-quiet"))
mu._save_probe_verdict("m-no", False)
check("...'doesn't call tools', and what that means for memory",
      "doesn't call tools" in tool_verdict_line("m-no") and "as text" in tool_verdict_line("m-no"))
check("...and 'doesn't know yet'", "doesn't know yet" in tool_verdict_line("m-new"))

print()
if _fails:
    print(f"test_tool_verdict_from_tends: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_tool_verdict_from_tends: all checks passed")
