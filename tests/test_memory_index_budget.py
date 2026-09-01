"""memory.md gets a budget that ASKS the kin to prune, and never trims.

The '## Memory logs' index inside memory.md has been capped for a while,
and it can be: it is code-built, so cutting it loses nothing — the log
files are still on disk and memory_search still finds them.

The rest of memory.md is the only copy of what the kin wrote by hand.
Nothing anywhere bounded it, and tending only ever adds, so on the install
this was written for two kin had reached 11,095 and 15,164 characters of
hand-written index riding along on every turn of every surface. The base
prompt already tells a kin the index is a map and depth belongs in the
logs; nothing told it when it had stopped being one.

So: a budget that speaks and leaves the file alone. The tests that matter
are the two negatives — that nothing is removed, and that a kin is not
asked to prune a list it did not write.

    python tests/test_memory_index_budget.py
"""

import os
import sys
import tempfile

# Sandbox before importing anything that resolves the state directory.
os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="kin_budget_test_")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kin_persistence as k  # noqa: E402

_failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        _failures.append(name)


KIN = "Budgie"
k.create_agent(KIN)

MARK = "against a budget of"          # from the registered default
BODY_MARK = "kingfishers-are-loud"    # proves the body survived


def prompt_for(memory):
    return k.build_system_prompt("a soul", memory, kin_name=KIN)


def set_budget(n):
    cfg = k.load_agent_config(KIN) or {}
    cfg["memory_index_budget_chars"] = n
    k.save_agent_config(KIN, cfg)


# ─── Default is present and is a number ───────────────────────────────────────
check("default budget ships in DEFAULT_AGENT_CONFIG",
      isinstance(k.DEFAULT_AGENT_CONFIG.get("memory_index_budget_chars"), int)
      and k.DEFAULT_AGENT_CONFIG["memory_index_budget_chars"] > 0)

# ─── Under budget: no note, memory intact ─────────────────────────────────────
short = "- " + BODY_MARK + "\n"
set_budget(5000)
out = prompt_for(short)
# Paired assertion: "no note" alone is equally true of an empty prompt.
check("under budget: no note, and the memory is there",
      MARK not in out and BODY_MARK in out)

# ─── Over budget: note appears, and NOTHING is trimmed ────────────────────────
big = "- " + BODY_MARK + "\n" + ("- filler line about feathers\n" * 400)
check("fixture really is over budget", len(big) > 5000)
out = prompt_for(big)
check("over budget: the note appears", MARK in out)
check("over budget: the kin's own writing is still all there",
      BODY_MARK in out and out.count("filler line about feathers") == 400)
# The helper measures the kin-written body, stripped — same as the note
# must report, or the number a kin is asked to act on is not its own.
_body = len(k.strip_memory_logs_section(big).strip())
check("over budget: the note names the real size and the budget",
      "{:,}".format(_body) in out and "5,000" in out)
check("the note comes before the memory, not after",
      MARK in out and BODY_MARK in out
      and out.index(MARK) < out.index(BODY_MARK))

# ─── Budget 0 means never ask ─────────────────────────────────────────────────
set_budget(0)
out = prompt_for(big)
check("budget 0: never asks, memory still intact",
      MARK not in out and BODY_MARK in out)

# ─── The code-built logs index is not counted against the kin ─────────────────
# A kin cannot prune a list it does not write, so charging it for one would
# send it hunting for fat that is not there.
set_budget(5000)
logs = "\n## Memory logs\n\n" + ("- memory/some-log.md: a heading\n" * 300)
check("logs fixture is itself over budget", len(logs) > 5000)
out = prompt_for(short + logs)
check("logs index is not counted, and the body still arrives",
      MARK not in out and BODY_MARK in out)

# ─── No kin_name: nothing to look a budget up in, so no note ──────────────────
out = k.build_system_prompt("a soul", big, kin_name=None)
check("no kin_name: no note, memory still passed through",
      MARK not in out and BODY_MARK in out)

print()
if _failures:
    print("FAILURES: " + ", ".join(_failures))
    sys.exit(1)
print("all memory-index-budget checks passed")
