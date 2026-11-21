# SPDX-License-Identifier: CC0-1.0
"""A reply that opens as one of the other speakers is caught everywhere
(20 Nov 2026 audit).

clean_kin_reply catches a reply opening "[Name] ..." only when it's told
which names the kin was shown. The desktop, Telegram groups and (since
tonight) Telegram DMs told it; rooms, Discord and wake-ups never did, and
rooms label every other kin exactly that way. chat_helpers.speakers_shown
collects the names, and every surface passes them.

Run: python tests/test_speakers_shown_everywhere.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chat_helpers import clean_kin_reply, speakers_shown  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


rows = [{"role": "user", "content": "[2026-11-20 10:00] [Wren, your person] hi"},
        {"role": "user", "content": "[Finch] hey"},
        {"role": "assistant", "content": "[not a speaker] my own words"},
        {"role": "user", "content": "plain, no name"},
        {"role": "assistant", "content": "x", "speaker": "Ash"}]
names = speakers_shown(rows, also=("Robin",))
check("names come from brackets, speaker fields and extras",
      {"Wren, your person", "Finch", "Ash", "Robin"} <= names)
check("...not from the kin's own replies", "not a speaker" not in names)
check("a reply opening as one of them is caught",
      clean_kin_reply("[Finch] I think so.", "Kimi", known_speakers=names)
      == ("I think so.", True))
check("the check can fail: without the names it isn't",
      clean_kin_reply("[Finch] I think so.", "Kimi")[1] is False)

for path, needle in (("frame/rooms_mixin.py", "known_speakers=_shown"),
                     ("discord_bot.py", "known_speakers=_shown"),
                     ("hearthkin_cron.py", "known_speakers=_shown")):
    src = (ROOT / path).read_text(encoding="utf-8")
    check(f"{path} passes the names it showed", needle in src)
dsrc = (ROOT / "discord_bot.py").read_text(encoding="utf-8")
check("Discord passes them on the live text, the final reply and salvage",
      dsrc.count("known_speakers=_shown") >= 3)

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_speakers_shown_everywhere: all checks passed")
