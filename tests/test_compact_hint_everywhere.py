# SPDX-License-Identifier: CC0-1.0
"""The suggestion to compact reaches the person wherever they talk
(18 Nov 2026).

It existed only in the desktop window, once per app session. The person's kin
lives on Telegram, so it never reached them, and about 55,000 tokens went on
every call for days. Pinned here:
  * past the kin's mark, a sentence says how full it is and what each message
    costs, and how to compact on that surface
  * every time it crosses the mark (compacting re-arms it), and a reminder
    every few replies while it stays over: the person's rule, "told
    consistently"
  * places are separate (desktop, each Telegram DM, each Discord DM)
  * below the mark, or with the setting at 0, nothing
  * Telegram DMs and Discord DMs ask after each reply; Telegram groups and
    Discord server channels don't (/compact doesn't work there)
"""

import datetime
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="compacthint-", dir=os.environ.get("HEARTHKIN_HOME"))

from chat_helpers import compact_hint  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


CFG = {"num_ctx": 65536, "compact_hint_pct": 70}
D1 = datetime.date(2026, 11, 18)
D2 = datetime.date(2026, 11, 19)
HOW = "Send /compact to compact it."

import chat_helpers  # noqa: E402
N = chat_helpers.COMPACT_REMIND_EVERY


def ask(place, tokens, cfg=CFG):
    return compact_hint("Kin", cfg, place, HOW, real_tokens=tokens)


t = ask("telegram:1", 55000)
check("past the mark, it's said", "percent full" in t)
check("...with what each message costs", "55,000 tokens" in t)
check("...and how to compact here", t.endswith(HOW))
quiet = [ask("telegram:1", 56000) for _ in range(N - 1)]
check(f"not on every reply: quiet for the next {N - 1}",
      all(q == "" for q in quiet))
check(f"...then a reminder on reply {N} while it's still full",
      ask("telegram:1", 56000) != "")
check("after compacting it goes quiet", ask("telegram:1", 9000) == "")
check("fill it up again and you're told straight away, same day",
      ask("telegram:1", 50000) != "")
check("another place is told separately", ask("desktop", 55000) != "")
check("below the mark, nothing", ask("telegram:2", 20000) == "")
check("0 means never",
      ask("telegram:3", 60000, dict(CFG, compact_hint_pct=0)) == "")
check("no reading yet, nothing",
      compact_hint("Nobody", CFG, "telegram:4", HOW, real_tokens=None) == "")

tg = open(os.path.join(HERE, "telegram_bot.py"), encoding="utf-8").read()
i = tg.index("def _handle_group_message")
check("Telegram DMs ask after a reply",
      'compact_hint(' in tg[:i] and 'f"telegram:{user_id}"' in tg[:i])
check("Telegram groups don't", "compact_hint(" not in tg[i:])
dc = open(os.path.join(HERE, "discord_bot.py"), encoding="utf-8").read()
check("Discord asks, in direct messages only",
      'compact_hint(self.agent_name' in dc
      and 'getattr(message, "guild", None) is None' in dc)
fm = open(os.path.join(HERE, "frame", "file_menu_mixin.py"),
          encoding="utf-8").read()
check("the desktop uses the same check", "compact_hint(" in fm)

print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("all passed")
