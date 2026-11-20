# SPDX-License-Identifier: CC0-1.0
"""A compaction note gets room to be whole, and the person sets how much
(20 Nov 2026).

Telegram and Discord asked for the note under the CHAT reply cap
(telegram_token_cap, 1024 on the kin it was found on). That kin thinks before
writing, and its last two notes both stopped at exactly 1,024 tokens. The
desktop set no cap at all. Now the note's allowance is the kin's own
"compact_note_tokens" setting (default 6000), shown in Memory settings, in
place of any chat cap, on all three surfaces.
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="compactroom-", dir=os.environ.get("HEARTHKIN_HOME"))

import kin_persistence as K  # noqa: E402
from chat_helpers import compact_options, COMPACT_NOTE_MIN_TOKENS  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


check("new kin start with room for 6,000",
      K.DEFAULT_AGENT_CONFIG.get("compact_note_tokens") == 6000)
o = compact_options({"num_predict": 1024, "num_ctx": 65536},
                    {"compact_note_tokens": 6000})
check("the chat cap no longer limits the note", o["num_predict"] == 6000)
check("other options are kept", o["num_ctx"] == 65536)
check("the person's setting is used, higher",
      compact_options({}, {"compact_note_tokens": 12000})["num_predict"]
      == 12000)
check("...or lower",
      compact_options({"num_predict": 8000},
                      {"compact_note_tokens": 3000})["num_predict"] == 3000)
check("a missing or broken setting falls back to the default",
      compact_options({}, {})["num_predict"] == COMPACT_NOTE_MIN_TOKENS
      and compact_options({}, {"compact_note_tokens": "x"})["num_predict"]
      == COMPACT_NOTE_MIN_TOKENS)
src = {f: open(os.path.join(HERE, f), encoding="utf-8").read()
       for f in ("telegram_bot.py", "discord_bot.py",
                 "frame/file_menu_mixin.py")}
for f, s in src.items():
    check(f"{f} asks for the note through compact_options",
          "compact_options(" in s)
ek = open(os.path.join(HERE, "dialogs", "edit_kin.py"), encoding="utf-8").read()
check("the setting is in the kin's Memory settings",
      '"compact_note_tokens"' in ek and "compact_note_row" in ek)

print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("all passed")
