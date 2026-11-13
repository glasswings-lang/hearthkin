# SPDX-License-Identifier: CC0-1.0
"""Guard test: a streamed reply only ever ADDS. Nothing already shown is
taken back.

Telegram and Discord render a streaming reply into messages that fill in
place, one per talking turn (since 13 Nov 2026). That is fine while it only grows and wrong the moment it doesn't, and
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
    """Records every state each message is put into, in order, per message.

    Since 13 Nov 2026 each talking turn is its own message, so "only adds" is
    checked message by message: no message ever loses text it showed."""

    def __init__(self, clean=_clean, max_len=4000):
        self.states = []          # (message id, text), in order
        self.ed = _TelegramStreamEditor(
            self._send, self._edit, throttle_secs=0.0,
            max_len=max_len, clean=clean)

    def _send(self, text):
        mid = "m%d" % (len({m for m, _ in self.states}) + 1)
        self.states.append((mid, text))
        return mid

    def _edit(self, mid, text):
        self.states.append((mid, text))

    def messages(self):
        """Each message's final text, in the order the messages appeared."""
        last = {}
        for mid, text in self.states:
            last[mid] = text
        return list(last.values())

    def never_shrank(self):
        """True when, for every message, each state keeps the whole of the one
        before it."""
        by_mid = {}
        for mid, text in self.states:
            by_mid.setdefault(mid, []).append(text)
        for texts in by_mid.values():
            for before, after in zip(texts, texts[1:]):
                if not after.startswith(before[:len(after)]):
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
check("the sentence said before the tool call is its own message, kept whole",
      r.messages()[0] == "Let me open that file and see.")
check("...and the answer after it is a second message, after it",
      len(r.messages()) == 2 and r.messages()[1] == "It has three lines in it.")

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

# The way it actually happened to a kin on 13 Nov 2026: a long apology, many
# tool calls, the tool-call limit, then a short "Fixed." asked for WITHOUT
# streaming. The last answer must come as a new message, through the tool
# loop itself, not replace the apology.
import llm_backend                                            # noqa: E402


class _Res:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.thinking = ""
        self.tool_calls = tool_calls or []
        self.usage = {}
        self.stopped = False


_calls = {"n": 0}


def _fake_stream(model, history, *, on_content, should_stop=None, **kw):
    _calls["n"] += 1
    on_content("I'm sorry. I took a name that isn't mine.")
    tcs = [{"id": "c%d" % i, "type": "function",
            "function": {"name": "note", "arguments": "{}"}} for i in range(5)]
    return _Res("I'm sorry. I took a name that isn't mine.", tcs)


def _fake_chat(model, history, **kw):
    return _Res("Fixed. The name is gone.")


_orig = (llm_backend._chat_collect_streaming, llm_backend.chat)
llm_backend._chat_collect_streaming = _fake_stream
llm_backend.chat = _fake_chat
try:
    r3 = _Run()
    res = llm_backend.run_tool_loop(
        "fake-model", [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "note"}}],
        tool_executor={"note": lambda args: "ok"},
        on_content=r3.ed.feed, on_turn=r3.ed.reset_turn,
        max_tool_calls=2)
    r3.ed.finalize(res.content)
finally:
    llm_backend._chat_collect_streaming, llm_backend.chat = _orig
check("the tool-call limit was what ended that reply",
      getattr(res, "hit_tool_limit", False))
check("after the tool-call limit, the apology is still on screen, whole",
      r3.messages()[0] == "I'm sorry. I took a name that isn't mine.")
check("...and the last answer is a message of its own",
      r3.messages()[1:] == ["Fixed. The name is gone."])
check("...and nothing shrank", r3.never_shrank())


print("--- cleanup must not shrink it either ---")

r = _Run()
r.ed.feed("[Bracken]: Reading it now.")
r.ed._flush()
r.ed.finalize("Reading it now.")
check("a self-tag never appears rather than appearing and being removed",
      all("[Bracken]:" not in t for _, t in r.states))
check("...and the message still never shrank", r.never_shrank())

r = _Run()
r.ed.feed("Let me check.")
r.ed._flush()
r.ed.reset_turn()
r.ed.feed("[Bracken]: It has three lines.")
r.ed._flush()
r.ed.finalize("It has three lines.")
check("a tag emitted AFTER a tool call is handled the same way",
      all("[Bracken]:" not in t for _, t in r.states) and r.never_shrank())
check("...and the pre-tool sentence survives it",
      r.messages()[0] == "Let me check.")

# Cleanup that eats the last turn entirely: what was said before it is real,
# is already on screen, and removing it would be the exact fault above.
r = _Run()
r.ed.feed("Let me check.")
r.ed._flush()
r.ed.reset_turn()
r.ed.finalize("")
check("when cleanup empties the final turn, the earlier words stay put",
      r.never_shrank() and r.messages() == ["Let me check."])

# A caller that salvages an earlier turn's words as the reply (Discord does,
# when the last turn comes back empty) must not post them twice.
r = _Run()
r.ed.feed("Here is my answer.")
r.ed._flush()
r.ed.reset_turn()
check("a salvaged earlier turn counts as sent",
      r.ed.finalize("Here is my answer.") is True)
check("...and is not posted a second time", r.messages() == ["Here is my answer."])


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
      r.messages() == ["Reading it now. Nothing unusual in it."])

r = _Run()
handled = r.ed.finalize("A reply that never streamed at all.")
check("a reply with no streamed content is still sent once", handled)
check("...as a single message", r.messages() == ["A reply that never streamed at all."])


print("--- both surfaces pass a cleaner, or neither is protected ---")

import inspect                                               # noqa: E402
import telegram_bot                                          # noqa: E402
import discord_bot                                           # noqa: E402

# Since 12 Nov 2026 Telegram builds its editor in one place for the tool and
# no-tools paths alike; the tool loop gets it from there.
tg = inspect.getsource(telegram_bot.TelegramBot._make_stream_editor)
dc = inspect.getsource(discord_bot.DiscordBot._generate)
check("Telegram hands the editor a cleaner",
      "clean=" in tg and "self._make_stream_editor(" in inspect.getsource(
          telegram_bot.TelegramBot._run_tool_loop_telegram))
check("Discord hands the editor a cleaner", "clean=" in dc)

reset = inspect.getsource(_TelegramStreamEditor.reset_turn)
check("a turn boundary finishes the turn's message rather than dropping it",
      "self._closed.append" in reset)


print()
if _fails:
    print(f"test_stream_only_adds: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_stream_only_adds: all checks passed")
