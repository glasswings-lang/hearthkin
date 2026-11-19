# SPDX-License-Identifier: CC0-1.0
"""A kin that can open files gets a pointer, not pasted notes (18 Nov 2026).

Agreed with the person this app is for: pasted notes arrived on almost every
turn, often ten times the length of a short message, and never once helped a
kin remember. A kin with read_file now gets one line naming where in its own
logs the subject lives, and opens what it wants itself. A kin without
read_file keeps the pasted notes; it has no other way in.

What this file pins:
  * with read_file: a hint naming memory/<log> and the line that matched,
    and none of the note's other text
  * without read_file: the old pasted note, intact (positive control)
  * a log the kin has open (its read_file call still in the messages) is
    not hinted again; once that call is gone, the hint may come back
  * a read of other lines of the same log does not count as holding the note
  * a weak match (two shared words) gives no hint; a kin's own
    recall_min_overlap setting still wins
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Its own folder inside the suite's shared sandbox (run_all.py), so the test
# kin's notes can't meet another test's.
os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="recallhint-", dir=os.environ.get("HEARTHKIN_HOME"))

import pathlib  # noqa: E402

import kin_persistence as K  # noqa: E402
from memory_recall import inject_into_messages  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "hintkin"
kdir = pathlib.Path(os.environ["HEARTHKIN_HOME"]) / "kin" / KIN
(kdir / "memory").mkdir(parents=True)
(kdir / "memory" / "garden.md").write_text(
    "# Garden\n"
    "\n"
    "Key notes:\n"
    "The tomatoes by the greenhouse door split after the August storm.\n"
    "SECRETBODY the compost turned to soup in September.\n",
    encoding="utf-8")
(kdir / "memory" / "music.md").write_text(
    "# Music\n\nThe cello practice moved to Thursday evenings.\n",
    encoding="utf-8")
# A few ordinary logs, so word scoring has a real corpus to weigh against
# (with two files every word looks common and nothing scores at all).
for _name, _text in (
        ("cats", "Mosi sleeps on the radiator shelf most afternoons."),
        ("bus", "The number nine bus stops running at eleven on Sundays."),
        ("books", "Halfway through the lighthouse novel; the ending drags."),
        ("kitchen", "The kettle whistle broke, so it just boils quietly now.")):
    (kdir / "memory" / f"{_name}.md").write_text(
        f"# {_name.title()}\n\n{_text}\n", encoding="utf-8")

ASK = "What happened to the tomatoes by the greenhouse door after the storm?"
TOOLS = ["read_file", "memory_search"]


def send(history, tools, cfg=None):
    out, used = inject_into_messages(history, KIN, num_ctx=32000,
                                     cfg=cfg or {}, tool_names=tools)
    block = out[-2]["content"] if used else ""
    return block, used


def user(text):
    return {"role": "user", "content": text}


def read_call(path, start=0, count=0):
    return {"role": "assistant", "content": "", "tool_calls": [{
        "function": {"name": "read_file",
                     "arguments": {"path": path, "start_line": start,
                                   "line_count": count}}}]}


# --- hint for a kin that can open files
block, used = send([user(ASK)], TOOLS)
check("a kin with read_file gets a hint", bool(used))
check("the hint names the log", "memory/garden.md" in block)
check("the hint points at the line that matched (4), not the heading",
      "from line 4" in block)
check("the hint carries none of the note's other text",
      "SECRETBODY" not in block and "Key notes" not in block)
check("the hint is one line", "\n" not in block)
check("the hint says how to open it", "read_file" in block)

# --- positive control: a kin without read_file keeps pasted notes
block_t, used_t = send([user(ASK)], [])
check("a kin without read_file still gets the note text",
      "tomatoes by the greenhouse door split" in block_t)

# --- the kin has the log open: no hint
hist = [read_call("memory/garden.md"),
        {"role": "tool", "content": "(the file)"}, user(ASK)]
block_h, used_h = send(hist, TOOLS)
check("a log the kin has open is not hinted again", not used_h)

# absolute path to the same log counts too
hist_abs = [read_call(str(kdir / "memory" / "garden.md")),
            {"role": "tool", "content": "(the file)"}, user(ASK)]
_, used_abs = send(hist_abs, TOOLS)
check("an absolute path to the open log counts as open", not used_abs)

# a read of other lines of the same log does not hold the note
hist_part = [read_call("memory/garden.md", start=1, count=2),
             {"role": "tool", "content": "(two lines)"}, user(ASK)]
_, used_part = send(hist_part, TOOLS)
check("reading other lines of the log does not hide the hint", bool(used_part))

# once the reading is gone from the messages, the hint may come back
hist_gone = [{"role": "system",
              "content": "[hearthkin: earlier tool call — read_file(...) → ...]"},
             user(ASK)]
_, used_gone = send(hist_gone, TOOLS)
check("once the reading has dropped out, the hint returns", bool(used_gone))

# --- weak match: two shared words only
WEAK = "The greenhouse door is stuck."
_, used_weak = send([user(WEAK)], TOOLS)
check("a weak two-word match gives no hint", not used_weak)
_, used_weak_cfg = send([user(WEAK)], TOOLS, cfg={"recall_min_overlap": 2})
check("a kin's own overlap setting still wins", bool(used_weak_cfg))
_, used_weak_text = send([user(WEAK)], [])
check("pasted notes for toolless kins keep the old two-word gate",
      bool(used_weak_text))

# --- a wake-up's standing header is not matched on
(kdir / "memory" / "wakeups.md").write_text(
    "# Wake-ups\n\nThe scheduled wake-up fired; nobody was currently typing, "
    "so the configured prompt ran on its own.\n", encoding="utf-8")
WAKE = ("[hearthkin: scheduled wake-up — fired at 08:00 on Wednesday, "
        "November 18, 2026 (local time). Nobody is currently typing to you; "
        "the text below is the scheduled prompt configured for this wake-up.]"
        "\n\nWrite today's journal entry.")
_, used_wake = send([user(WAKE)], TOOLS)
check("a wake-up's own header pulls no notes", not used_wake)
_, used_wake_t = send([user(WAKE)], [])
check("...for toolless kins either", not used_wake_t)
WAKE_JOB = WAKE.replace("Write today's journal entry.",
                        "Check how the tomatoes by the greenhouse door are.")
_, used_job = send([user(WAKE_JOB)], TOOLS)
check("the wake-up's own job text still matches", bool(used_job))

print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("all passed")
