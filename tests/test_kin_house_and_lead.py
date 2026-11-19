# SPDX-License-Identifier: CC0-1.0
"""The rest of Kimi's list (18 Nov 2026).

  * who's in the house: a line at the start of wake-ups and heartbeats naming
    the other kin, when each was last active, and who has written in the
    inbox. Names and dates only. Never on ordinary turns, and never matched
    on by memory recall.
  * emotional anchoring: tending keeps what it felt like, not only the fact
  * a daily vibecheck: the day's journal is the kin's to write in too
  * lead, don't defer: bring your own ideas; "what do you want?" hands the
    steering back
"""

import datetime
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="house-", dir=os.environ.get("HEARTHKIN_HOME"))

import kin_persistence as K  # noqa: E402
import cron_helpers as C  # noqa: E402
from memory_recall import _strip_harness_prefix  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# --- alone
K.create_agent("Solo")
check("a kin alone in the house gets no line", K.house_line("Solo") == "")

# --- three kin
for k in ("Ash", "Birch"):
    K.create_agent(k)
now = datetime.datetime(2026, 11, 18, 12, 0)
ash = K.agent_dir("Ash") / "conversation.jsonl"
ash.write_text("{}\n", encoding="utf-8")
t = datetime.datetime(2026, 11, 16, 9, 0).timestamp()
os.utime(ash, (t, t))
(K.AGENTS_DIR / "inbox.md").write_text(
    "# Kin Inbox\n\n## From Ash, 2026-11-17\n\nHello.\n", encoding="utf-8")
line = K.house_line("solo", now=now)
check("the line names the other kin", "Ash" in line and "Birch" in line)
check("...but not the kin itself, whatever its case", "Solo" not in line)
check("...with when each was last active", "Ash (last active 2 days ago" in line)
check("...saying when one hasn't talked yet",
      "Birch (hasn't talked with anyone yet" in line)
check("...and who has written in the inbox",
      "Ash (last active 2 days ago, has written in the inbox)" in line
      and "Birch (hasn't talked with anyone yet)" in line)
check("...and where the inbox is", str(K.AGENTS_DIR / "inbox.md") in line)
check("it is a harness line", line.startswith("[hearthkin:") and line.endswith("]"))

# --- wake-ups carry it first; recall never matches on it
framed = C.frame_wake_up_prompt("Water the plants.", "08:00", kin_name="Solo")
check("a wake-up starts with the house line",
      framed.startswith("[hearthkin: also in the house"))
check("...then the usual wake-up header and the job",
      "scheduled wake-up" in framed and framed.endswith("Water the plants."))
check("recall reads only the job, not the house line or header",
      _strip_harness_prefix(framed).strip() == "Water the plants.")
cron = open(os.path.join(HERE, "hearthkin_cron.py"), encoding="utf-8").read()
check("heartbeats carry the house line too", "house_line(kin)" in cron)
check("ordinary turns don't",
      "house_line" not in open(os.path.join(HERE, "frame", "chat_send_mixin.py"),
                               encoding="utf-8").read())

# --- the shared instructions
bp = K.DEFAULT_BASE_PROMPT
check("tending keeps what it felt like", "Keep what it felt like" in bp)
check("the day's journal is the kin's to write in", "journal for the day" in bp)
check("the lead line is there", "**Lead.**" in bp and "hands the steering back" in bp)
p0 = K.build_system_prompt("soul", "mem", enabled_tools=[], kin_name="Solo")
check("...and a kin with no tools gets it too", "**Lead.**" in p0)

print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("all passed")
