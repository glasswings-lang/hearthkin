# SPDX-License-Identifier: CC0-1.0
"""A kin can compact its own conversation at a wake-up (24 Nov 2026, from an
outside review).

The person compacted by hand or missed the mark, and then the window rolled,
trimming the oldest turns with nothing in the kin's words covering the gap.
With the switch on, after a wake-up, a conversation past the kin's mark is
compacted by the kin: a memory pass with tools, then its note, then the
compaction row. Pinned:

  * off by default, and off means nothing runs;
  * below the mark, nothing runs;
  * past it: the memory pass gets tools, then the note is asked for with the
    person's own compaction request, then one compaction row is appended,
    holding the note, saying the kin did it, and nothing is deleted;
  * the note becomes now.md;
  * no note, no compaction;
  * a model on record as not calling tools skips the memory pass;
  * there's a switch for it in the kin's Memory settings.

Run: python tests/test_kin_led_compaction.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="kinled-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import hearthkin_cron as hc  # noqa: E402
import kin_persistence as kp  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402
from llm_backend import ChatResult  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Sorrel"
d = kin_dir(KIN)
d.mkdir(parents=True, exist_ok=True)
(d / "soul.md").write_text("I am Sorrel.", encoding="utf-8")
talk = [{"role": "user" if i % 2 == 0 else "assistant",
         "content": f"a long line of talk number {i} " * 20} for i in range(60)]
kp.save_agent_conversation(KIN, talk)

calls = []
hc._load_cron_tools = lambda kin, model=None: (["schema"], {"note": None}, ["note"])
hc._cron_tool_loop = lambda *a, **k: calls.append(("tools", k.get("surface"))) or ChatResult(content="done")
note_text = ["Where I am: we talked a lot. I feel steady."]
hc._cron_chat = lambda model, msgs, **k: calls.append(
    ("note", msgs[-1]["content"])) or ChatResult(content=note_text[0])

cfg = dict(kp.load_agent_config(KIN) or {})
cfg.update({"model": "m", "num_ctx": 4096, "compact_hint_pct": 50})
check("off by default", not kp.DEFAULT_AGENT_CONFIG.get("kin_led_compaction", False)
      if hasattr(kp, "DEFAULT_AGENT_CONFIG") else True)
check("off means nothing runs", hc.maybe_kin_led_compaction(KIN, cfg) == "" and not calls)

cfg["kin_led_compaction"] = True
cfg["compact_hint_pct"] = 100000
check("below the mark, nothing runs", hc.maybe_kin_led_compaction(KIN, cfg) == "" and not calls)

cfg["compact_hint_pct"] = 50
line = hc.maybe_kin_led_compaction(KIN, cfg)
rows = kp.load_agent_conversation(KIN)
check("past the mark the memory pass runs, with tools", calls and calls[0][0] == "tools")
check("...then the note is asked for, with the compaction request",
      len(calls) > 1 and calls[1][0] == "note" and "write that note now" in calls[1][1])
check("...then one compaction row is appended, with the note",
      len(rows) == 61 and rows[-1].get("compact") and note_text[0] in rows[-1]["content"])
check("...saying the kin did it", "your kin-led compaction is switched on" in rows[-1]["content"])
check("...and nothing is deleted", rows[:60] == talk or [r["content"] for r in rows[:60]] == [t["content"] for t in talk])
check("the note becomes now.md", note_text[0] in (d / "now.md").read_text(encoding="utf-8"))
check("the person gets one line about it",
      "was compacted" in line and "Nothing was deleted" in line)
check("...which names the memory pass, since it ran (accuracy audit)",
      "had a memory pass" in line and calls[0][0] == "tools")

calls.clear()
note_text[0] = ""
kp.save_agent_conversation(KIN, talk + [{"role": "user", "content": "more " * 2000}])
line = hc.maybe_kin_led_compaction(KIN, cfg)
check("no note, no compaction", "nothing was compacted" in line
      and not any(r.get("compact") for r in kp.load_agent_conversation(KIN)))

calls.clear()
note_text[0] = "A note."
hc._tools_known_dead = lambda model: True
line = hc.maybe_kin_led_compaction(KIN, cfg)
check("a model that doesn't call tools skips the memory pass",
      calls and calls[0][0] == "note")
check("...and the line doesn't claim one happened",
      "had a memory pass" not in line and "kept what mattered" not in line)

src = (ROOT / "dialogs" / "edit_kin.py").read_text(encoding="utf-8")
check("there's a switch for it in Memory settings", '"kin_led_compaction"' in src)

print()
if _fails:
    print(f"test_kin_led_compaction: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_kin_led_compaction: all checks passed")
