# SPDX-License-Identifier: CC0-1.0
"""Guard test: a streamed reply only ever ADDS. Nothing already shown is
taken back.

Telegram and Discord render a streaming reply into ONE message that fills in
place. That is fine while it only grows and wrong the moment it doesn't, and
it didn't: `reset_turn` used to CLEAR the buffer at each tool-loop turn
boundary, so a kin that said "let me go and look at that file" and then called
a tool had that sentence OVERWRITTEN by whatever it said afterwards. One
message, and the first thing in it destroyed.

Why that is worse here than it sounds. Telegram output is append-only
deliberately: the chat is a historical record, and a screen reader has already
read the earlier version ALOUD. So the reader hears one thing, the screen
afterwards says another, and nothing anywhere marks that it changed. Scrolling
back does not recover it — the text is simply gone. The rule is in CLAUDE.md
and the code had drifted straight through it.

There were two ways to shrink and both are closed here:

  1. a turn boundary discarding what the finished turn said — now BANKED;
  2. the end-of-turn cleanup removing something the live text had already
     shown, e.g. a model opening with its own name tag. The cleanup now runs
     on the DISPLAYED text too, so the artifact never appears rather than
     appearing and being edited away.

The invariant is asserted as a prefix relation across every rendered state,
which is the honest form of "only adds" — the message is capped in length, so
"both halves are visible" is not always true and is not what is being claimed.

Run: python tests/test_stream_only_adds.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="stradd-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


from telegram_bot import _TelegramStreamEditor                # noqa: E402
from chat_helpers import clean_kin_reply                      # noqa: E402

KIN = "Bracken"          # a fixture name, not a kin


def _clean(text):
    return clean_kin_reply(text, KIN)[0]


class _Run:
    """Records every state the message is put into, in order."""

    def __init__(self, clean=_clean, max_len=4000):
        self.states = []
        self.ed = _TelegramStreamEditor(
            self._send, self._edit, throttle_secs=0.0,
            max_len=max_len, clean=clean)

    def _send(self, text):
        self.states.append(text)
        return "m1"

    def _edit(self, mid, text):
        self.states.append(text)

    def never_shrank(self):
        """True when each state keeps the whole of the one before it.

        Truncation at max_len means a later state can be the same length; what
        must never happen is a state that drops text an earlier one showed."""
        for before, after in zip(self.states, self.states[1:]):
            keep = before[:len(after)]
            if not after.startswith(keep):
                return False
            if len(after) < len(before):
                return False
        return True


print("--- a tool call in the middle of a reply ---")

r = _Run()
r.ed.feed("Let me open that file and see.")
r.ed._flush()
r.ed.reset_turn()                       # the tool runs here
r.ed.feed("It has three lines in it.")
r.ed._flush()
r.ed.finalize("It has three lines in it.")

check("the message never gives anything back", r.never_shrank())
check("the sentence said before the tool call is still in the final message",
      "open that file" in r.states[-1])
check("...and so is the answer that came after it",
      "three lines" in r.states[-1])
check("...in the order they were said",
      r.states[-1].index("open that file")
      < r.states[-1].index("three lines"))

# The positive control. Without it, "never shrank" would pass just as happily
# on an editor that never edits at all, and this whole file would prove
# nothing. Reproduce the OLD behaviour by discarding the banked turn.
r2 = _Run()
r2.ed.feed("Let me open that file and see.")
r2.ed._flush()
r2.ed._buf = ""                          # what reset_turn used to do
r2.ed._dirty = True
r2.ed.feed("It has three lines in it.")
r2.ed._flush()
check("the check CAN fail — the old discarding behaviour is caught",
      not r2.never_shrank())


print("--- cleanup must not shrink it either ---")

r = _Run()
r.ed.feed("[Bracken]: Reading it now.")
r.ed._flush()
r.ed.finalize("Reading it now.")
check("a self-tag never appears rather than appearing and being removed",
      all("[Bracken]:" not in s for s in r.states))
check("...and the message still never shrank", r.never_shrank())

r = _Run()
r.ed.feed("Let me check.")
r.ed._flush()
r.ed.reset_turn()
r.ed.feed("[Bracken]: It has three lines.")
r.ed._flush()
r.ed.finalize("It has three lines.")
check("a tag emitted AFTER a tool call is handled the same way",
      all("[Bracken]:" not in s for s in r.states) and r.never_shrank())
check("...and the pre-tool sentence survives it",
      "Let me check." in r.states[-1])

# Cleanup that eats the last turn entirely: what was said before it is real,
# is already on screen, and removing it would be the exact fault above.
r = _Run()
r.ed.feed("Let me check.")
r.ed._flush()
r.ed.reset_turn()
r.ed.finalize("")
check("when cleanup empties the final turn, the earlier words stay put",
      r.never_shrank() and "Let me check." in r.states[-1])


print("--- the ordinary case is unchanged ---")

r = _Run()
r.ed.feed("Reading it now. ")
r.ed._flush()
r.ed.feed("Nothing unusual in it.")
r.ed._flush()
r.ed.finalize("Reading it now. Nothing unusual in it.")
check("a plain reply still fills in as it arrives", len(r.states) >= 2)
check("...growing the whole way", r.never_shrank())
check("...and ends on the complete reply",
      r.states[-1] == "Reading it now. Nothing unusual in it.")

r = _Run()
handled = r.ed.finalize("A reply that never streamed at all.")
check("a reply with no streamed content is still sent once", handled)
check("...as a single message", r.states == ["A reply that never streamed at all."])


print("--- both surfaces pass a cleaner, or neither is protected ---")

import inspect                                               # noqa: E402
import telegram_bot                                          # noqa: E402
import discord_bot                                           # noqa: E402

tg = inspect.getsource(telegram_bot.TelegramBot._run_tool_loop_telegram)
dc = inspect.getsource(discord_bot.DiscordBot._generate)
check("Telegram hands the editor a cleaner", "clean=" in tg)
check("Discord hands the editor a cleaner", "clean=" in dc)

reset = inspect.getsource(_TelegramStreamEditor.reset_turn)
check("a turn boundary banks the finished turn rather than dropping it",
      "_kept" in reset)


print()
if _fails:
    print(f"test_stream_only_adds: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_stream_only_adds: all checks passed")
