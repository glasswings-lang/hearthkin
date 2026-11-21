# SPDX-License-Identifier: CC0-1.0
"""Long replies are split one careful way on Telegram and Discord.

Both apps have a per-message ceiling (Telegram 4096 UTF-16 units, Discord
2000 characters, and Discord refuses anything over rather than trimming
it). Before 21 Nov 2026:
- Telegram cut every 4000 characters, mid-word, and counted an emoji as one
  when Telegram counts it as two, so an emoji-heavy piece could be refused.
- Discord counted the same plain way, cut mid-word when no line break was
  handy, and if Discord refused a piece, everything after it was silently
  gone.
- The live message (the one that grows as the kin types, on both apps) cut
  by raw slices of its own.

Now one splitter (chat_helpers.split_for_limit) serves all of them, and a
refused piece on Discord is told to the person.

Run: python tests/test_long_replies_split.py
"""
import asyncio
import inspect
import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="longsplit-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chat_helpers import split_for_limit, utf16_len  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# --- the splitter -------------------------------------------------------
words = ("gentle " * 900).strip()
p = split_for_limit(words, 2000)
check("every piece fits", all(utf16_len(x) <= 2000 for x in p))
check("no word is cut in half",
      all(w == "gentle" for x in p for w in x.split()))
check("nothing is lost", " ".join(p).split() == words.split())

emoji = "\U0001F600" * 1500          # 1500 characters, 3000 UTF-16 units
p = split_for_limit(emoji, 2000)
check("an emoji counts as two, so emoji-heavy pieces still fit",
      all(utf16_len(x) <= 2000 for x in p) and "".join(p) == emoji)
check("...where a plain character count would have overflowed",
      utf16_len(emoji[:2000]) > 2000)

lines = "\n".join(f"line {i} " + "x" * 40 for i in range(200))
p = split_for_limit(lines, 2000)
check("a line break is preferred over a space",
      all(x.split("\n")[-1].startswith("line ") for x in p))

blob = "y" * 5000
p = split_for_limit(blob, 2000)
check("one enormous word is still cut, rather than never sent",
      "".join(p) == blob and all(len(x) <= 2000 for x in p))
check("empty in, nothing out", split_for_limit("", 100) == [])


# --- Telegram uses it ---------------------------------------------------
import telegram_bot  # noqa: E402
src = inspect.getsource(telegram_bot.TelegramBot._send_chunked)
check("Telegram's sender goes through the shared splitter",
      "split_for_limit" in src and "LIMIT = 4000" not in src)

from telegram_bot import _TelegramStreamEditor  # noqa: E402
sends, edits = [], []


def send(t):
    sends.append(t)
    return f"m{len(sends)}"


ed = _TelegramStreamEditor(send, lambda mid, t: edits.append((mid, t)),
                           throttle_secs=0.0, max_len=30, now=lambda: 0.0)
ed.feed("one two three four five six seven eight nine ten eleven")
ed.finalize("one two three four five six seven eight nine ten eleven")
shown = [t for _, t in edits] + sends
check("the live message splits at a space too",
      all(not t.endswith(("thr", "fou", "fiv", "elev")) for t in shown))
check("...and nothing is lost",
      "eleven" in " ".join(sends + [edits[-1][1]] if edits else sends))


# --- Discord uses it, and tells the person about a refused piece --------
import discord_bot  # noqa: E402
from discord_bot import DiscordBot  # noqa: E402


class _Chan:
    id = 5

    def __init__(self, refuse_at=None):
        self.sent = []
        self.refuse_at = refuse_at

    async def send(self, text):
        if self.refuse_at is not None and len(self.sent) == self.refuse_at:
            self.refuse_at = None
            raise RuntimeError("400 Must be 2000 or fewer in length")
        self.sent.append(text)


b = DiscordBot.__new__(DiscordBot)
b.agent_name = "Bracken"
ch = _Chan()
asyncio.run(b._send_chunked(ch, words))
check("Discord: every piece fits its limit",
      all(utf16_len(x) <= discord_bot.DISCORD_MSG_LIMIT for x in ch.sent))
check("Discord: no word cut in half",
      all(w == "gentle" for x in ch.sent for w in x.split()))

ch = _Chan(refuse_at=1)
asyncio.run(b._send_chunked(ch, words))
check("Discord: a refused piece is TOLD, not silently dropped",
      any("couldn't be sent" in x for x in ch.sent))
check("...and says the kin still has all of it",
      any("still has the whole of it" in x for x in ch.sent))

if _fails:
    print(f"{len(_fails)} FAILED: " + "; ".join(_fails))
    sys.exit(1)
print("test_long_replies_split: all checks passed")
