# SPDX-License-Identifier: CC0-1.0
"""Guard test: a kin must never be handed less of its notes than it thinks,
and must never file away notes it has not read.

A staging file has no ceiling. The trip back to a kin does — tool results
are capped at 8,000 CHARACTERS. Those two facts sat next to each other
without meeting until a redistill-from-start put nineteen hours and 206
sections, about 1.5 MB, into one file. `read_staging` would have returned
the first 8,000 characters: half of one percent, the OLDEST half-percent,
cut mid-sentence, with nothing to say there was more.

The second half is worse and is the reason `archive_staging` changed too.
The tending prompt ends "call archive_staging when you've finished the
scope", and a kin that has read everything it was given has, from where it
sits, finished. So the ordinary correct instinct at the end of tending
would have filed away two hundred unread sections. That is not a fault to
train out of a kin; it is the right impulse over the wrong tool.

So: whole sections up to a budget, an explicit continuation, and archiving
that files away only what was actually read.

What this file pins:

  - a file is split on the section headings distillation already writes;
  - a batch stops on a seam, within budget, and never mid-section;
  - the reply says how many sections were NOT shown and how to ask;
  - a section larger than the whole budget is still returned, or a kin
    could never get past it;
  - reading advances a mark, and the mark never goes backwards;
  - archiving files away the read prefix and KEEPS the rest;
  - archiving with nothing read refuses, rather than hiding it;
  - reading everything then archiving still clears the scope entirely, i.e. the
    ordinary small-file case is unchanged;
  - the budget is in characters, and the setting says so.

Run: python tests/test_staging_batching.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="staging-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import kin_persistence as kp  # noqa: E402
from tools.read_staging import read_staging  # noqa: E402
from tools.archive_staging import archive_staging  # noqa: E402

KIN = "Bracken"
SCOPE = "desktop"

# 40 sections in the shape distillation actually appends.
body = "# Staging notes - desktop\n\n"
for i in range(40):
    body += (f"## 2026-08-18T{i // 60:02d}:{i % 60:02d}:00 - desktop - "
             f"source: walk-from-start-desktop\n\n"
             f"note number {i:02d}. " + ("filler " * 40) + "\n\n")
kp.staging_file_path(KIN, SCOPE).parent.mkdir(parents=True, exist_ok=True)
kp.staging_file_path(KIN, SCOPE).write_text(body, encoding="utf-8")

cfg = kp.load_agent_config(KIN)
cfg["staging_read_max_chars"] = 2000
kp.save_agent_config(KIN, cfg)


# --- the file knows its own seams ----------------------------------------

pre, secs = kp.split_staging_sections(body)
check("the file splits on the headings distillation writes", len(secs) == 40)
check("...with the header kept out of the sections", pre.startswith("# Staging"))
check("a file with no sections is returned whole, not lost",
      kp.split_staging_sections("just prose")[0] == "just prose")


# --- a batch stops on a seam ---------------------------------------------

first = read_staging(scope=SCOPE, agent_name=KIN)
check("a batch is smaller than the whole file", len(first) < len(body))
check("...and does not end mid-section",
      "[read_staging:" in first)
check("it says how many sections it showed, of how many",
      " of 40 " in first)
check("...and that some are still unread", "still unread" in first)
check("...and exactly how to ask for them", "start=" in first)
check("...and that archiving now will not bury them",
      "only what you have read" in first)
# The wording at this exact moment decides whether a backlog moves. An
# earlier version ended "call read_staging again WHEN YOU ARE READY",
# which arrives precisely as the kin chooses whether to continue and
# reads as permission to stop. On a 206-section backlog that is the
# difference between weeks and months.
check("...and it invites carrying on rather than stopping",
      "keep going" in first)
check("...while still making stopping safe, so it is an invitation and "
      "not a demand",
      "nothing is lost if you stop" in first)
check("the budget is respected", len(first) < 2000 + 900)


# --- the mark, and continuing ---------------------------------------------

mark = kp.staging_read_mark(KIN, SCOPE)
check("reading advanced the mark", 0 < mark < 40)

second = read_staging(scope=SCOPE, start=mark + 1, agent_name=KIN)
check("continuing from the mark returns later sections",
      f"note number {mark:02d}" in second)
check("...and does not repeat the first batch",
      "note number 00" not in second)
check("the mark moved forward", kp.staging_read_mark(KIN, SCOPE) > mark)

kp.set_staging_read_mark(KIN, SCOPE, 1)
check("the mark never goes backwards -- re-reading an early batch must "
      "not un-read a later one",
      kp.staging_read_mark(KIN, SCOPE) > 1)


# --- archiving files away only what was read ------------------------------

before = kp.staging_read_mark(KIN, SCOPE)
msg = archive_staging(scope=SCOPE, agent_name=KIN)
check("archiving reports what it filed away", "filed away" in msg)
check("...and that sections remain", "still pending" in msg)
check("...and says the kin need not stop there",
      "do not have to stop here" in msg)
check("...and that stopping partway costs nothing, since a kin that has "
      "had enough is allowed to have had enough",
      "nothing is lost if you stop partway" in msg)

left_text = kp.load_staging(KIN, SCOPE)
_, left_secs = kp.split_staging_sections(left_text)
check("the unread sections are still pending on disk",
      len(left_secs) == 40 - before)
check("...and the read ones are gone from the pending file",
      "note number 00" not in left_text)
check("the mark resets, since the remaining sections have shifted",
      kp.staging_read_mark(KIN, SCOPE) == 0)

arch = sorted((kp.staging_dir(KIN) / "archive").glob("*.md"))
check("what was filed away is on disk, not deleted", len(arch) == 1)
check("...and holds exactly the read sections",
      len(kp.split_staging_sections(
          arch[0].read_text(encoding="utf-8"))[1]) == before)


# --- archiving with nothing read is refused -------------------------------

msg = archive_staging(scope=SCOPE, agent_name=KIN)
check("archiving before reading refuses", "nothing has been read" in msg)
check("...and tells the kin what to do instead", "read_staging" in msg)
still = kp.load_staging(KIN, SCOPE)
check("...and nothing moved", len(kp.split_staging_sections(still)[1])
      == 40 - before)


# --- the ordinary small case is unchanged ---------------------------------

cfg["staging_read_max_chars"] = 500000
kp.save_agent_config(KIN, cfg)
whole = read_staging(scope=SCOPE, agent_name=KIN)
check("a file that fits is returned entire", "that is all of them" in whole)
check("...with no continuation instruction", "start=" not in whole)
msg = archive_staging(scope=SCOPE, agent_name=KIN)
check("...and archiving then clears the scope",
      "Nothing left pending" in msg)
check("...leaving no pending file", not kp.list_staging_files(KIN))


# --- an oversized single section still gets through -----------------------

big = "# Staging notes - desktop\n\n## 2026-08-18T00:00:00 - desktop\n\n"
big += "x" * 40000 + "\n"
kp.staging_file_path(KIN, "solo").write_text(big, encoding="utf-8")
cfg["staging_read_max_chars"] = 2000
kp.save_agent_config(KIN, cfg)
got = read_staging(scope="solo", agent_name=KIN)
check("a section bigger than the whole budget is still returned -- "
      "otherwise a kin could never get past it", len(got) > 20000)


# --- the setting names its unit -------------------------------------------

check("the budget setting exists",
      "staging_read_max_chars" in kp.DEFAULT_AGENT_CONFIG)
check("...and its name says what it counts",
      "chars" in "staging_read_max_chars")
check("...and it sits below the tool-result cap, so nothing downstream "
      "cuts a batch we already sized",
      kp.DEFAULT_AGENT_CONFIG["staging_read_max_chars"]
      < kp.DEFAULT_AGENT_CONFIG.get("tool_result_cap", 8000))


print()
if _fails:
    print(f"{len(_fails)} FAILED: " + ", ".join(_fails))
    sys.exit(1)
print("all checks passed")
