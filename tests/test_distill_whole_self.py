# SPDX-License-Identifier: CC0-1.0
"""Distillation and memory consolidation run as the WHOLE kin (23 Nov 2026).

The person's principle: "if you're going to be distilling something, then you
should be given what you're given when the thing took place." Distillation got
only the soul, and consolidation not even that, so a kin whose identity file
had grown past its soul was summarised by an older, thinner self, and the
base prompt reached neither. Both now carry what chat carries, built by the
same function. Pinned:

  * distillation carries the base prompt, the soul and the identity file;
  * so does consolidation;
  * what they carry IS the chat prompt (same builder), so anything added to
    chat later reaches them;
  * the run-size reserve counts the whole of it;
  * the check can fail: the soul alone would not carry the identity file.

Run: python tests/test_distill_whole_self.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="distillself-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import frame_shared as fs  # noqa: E402
import kin_persistence as kp  # noqa: E402
import llm_backend  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Sorrel"
home = kin_dir(KIN)
home.mkdir(parents=True, exist_ok=True)
(home / "soul.md").write_text("SOUL-MARK I am Sorrel.", encoding="utf-8")
(home / "identity.md").write_text("IDENTITY-MARK amber, the light through glass.",
                                  encoding="utf-8")
(home / "base_prompt.md").write_text("BASE-MARK the house rules.", encoding="utf-8")
kp.save_kin_tools(KIN, ["read_file"])
if getattr(fs, "ollama", None) is None:
    fs.ollama = object()
fs.list_agents = lambda: [KIN]


class _Result:
    def __init__(self, content):
        self.content = content
        self.thinking = ""
        self.usage = {}
        self.tool_calls = []


seen = []


def fake_collect(model, messages, **kw):
    seen.append(messages)
    return _Result("- a note")


real = llm_backend.chat_collect
llm_backend.chat_collect = fake_collect
try:
    fs.distill_memory_blocking(KIN, [{"role": "user", "content": "hi"}], "", "m")
    d_sys = seen[-1][0]["content"]
    fs.consolidate_memory_blocking("- long memory", "m", kin_name=KIN)
    c_sys = seen[-1][0]["content"]
finally:
    llm_backend.chat_collect = real

for name, sys_text in (("distillation", d_sys), ("consolidation", c_sys)):
    check(f"{name} carries the base prompt", "BASE-MARK" in sys_text)
    check(f"{name} carries the soul", "SOUL-MARK" in sys_text)
    check(f"{name} carries the identity file", "IDENTITY-MARK" in sys_text)

chat = kp.build_system_prompt(kp.load_soul(KIN), "", enabled_tools=["read_file"],
                              kin_name=KIN)
check("what distillation carries is the chat prompt itself", d_sys.startswith(chat.strip()))
check("...and so is consolidation's", c_sys.startswith(chat.strip()))

check("the run-size reserve measures the whole kin, not the soul",
      "kin_self_prompt(agent_name)" in (ROOT / "frame" / "memory_mixin.py").read_text(encoding="utf-8"))

check("the check can fail: the soul alone doesn't carry the identity file",
      "IDENTITY-MARK" not in kp.load_soul(KIN))

print()
if _fails:
    print(f"test_distill_whole_self: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_distill_whole_self: all checks passed")
