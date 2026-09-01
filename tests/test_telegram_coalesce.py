# SPDX-License-Identifier: CC0-1.0
"""Guard test: a long message the sender's Telegram client split arrives as
one turn, not several.

Telegram's 4096-code-unit ceiling applies to people, not just to bots. When
someone pastes a long passage, the client chops it into two or three
messages and the Bot API delivers two or three separate updates. Handled one
at a time, the kin answers half a thought, then answers the orphaned tail.

`TelegramBot._coalesce_message_parts` closes a short grace window after each
plain-text message and stitches whatever arrives behind it into a single
update. This pins the pieces that decision rests on:

  * splits rejoin, with the right seam (no seam at a mid-word cut, a
    newline where the client ate a line break, nothing doubled where
    whitespace already survived);
  * @mention entities from part two land on the right characters of the
    joined text, counted in UTF-16 like Telegram counts them;
  * things that must NOT be merged stay separate and stay in order —
    slash commands, another person's message, a reply quoting something
    else, attachments, and anyone with a pending exec approval waiting on
    their answer.
"""

import os
import sys
import queue
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import telegram_bot as T  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


class FakeBot(T.TelegramBot):
    """Just enough state for the reassembler. Skips __init__ so no disk,
    no network, no threads."""

    def __init__(self, **tg_cfg):
        self._queue = queue.Queue()
        self._holdover = []
        self._stop = threading.Event()
        self._pending_lock = threading.RLock()
        self._pending_approvals = {}
        self._tg_cfg = tg_cfg

    def get_config(self):
        return dict(self._tg_cfg)


_uid = [1000]


def upd(text, *, user=7, chat=7, reply_to=None, entities=None, **extra):
    _uid[0] += 1
    msg = {
        "message_id": _uid[0],
        "from": {"id": user},
        "chat": {"id": chat, "type": "private"},
        "date": 1700000000,
        "text": text,
    }
    if reply_to is not None:
        msg["reply_to_message"] = {"message_id": reply_to, "from": {"id": 99}}
    if entities is not None:
        msg["entities"] = entities
    msg.update(extra)
    return {"update_id": _uid[0], "message": msg}


def text_of(u):
    return (u.get("message") or {}).get("text")


def run(bot, first, rest):
    """Feed `rest` into the queue (as one getUpdates batch would), then
    reassemble starting from `first`."""
    for u in rest:
        bot._queue.put(u)
    return bot._coalesce_message_parts(first)


# Real windows are seconds long by design; note them for the coherence
# checks at the bottom, then shrink them so the suite stays quick. The
# reassembler reads these at call time.
_REAL_WINDOW = T._COALESCE_WINDOW_SECS
_REAL_SPLIT_WINDOW = T._COALESCE_SPLIT_WINDOW_SECS
T._COALESCE_WINDOW_SECS = 0.05
T._COALESCE_SPLIT_WINDOW_SECS = 0.15


# --- rejoining -----------------------------------------------------------

bot = FakeBot()
# A hard cut at the ceiling: no newline or space was available to break on,
# so the seam is mid-word and must not gain a newline.
head = "a" * (T._COALESCE_HARD_CUT_LEN + 3)
merged = run(bot, upd(head), [upd("tail of the word.")])
check("hard cut at the ceiling rejoins with no seam",
      text_of(merged) == head + "tail of the word.")
check("hard-cut merge consumed both parts, nothing left over",
      not bot._holdover and bot._queue.empty())

bot = FakeBot()
merged = run(bot, upd("first line\n"), [upd("second line")])
check("whitespace that survived the split isn't doubled",
      text_of(merged) == "first line\nsecond line")

bot = FakeBot()
merged = run(bot, upd("thinking out loud"), [upd("and one more thing")])
check("two quickly-typed lines join on a newline",
      text_of(merged) == "thinking out loud\nand one more thing")

bot = FakeBot()
merged = run(bot, upd("one"), [upd("two"), upd("three")])
check("three parts all fold into one turn",
      text_of(merged) == "one\ntwo\nthree")

bot = FakeBot()
lone = upd("just the one message")
started = time.monotonic()
merged = run(bot, lone, [])
check("a lone message comes back unchanged", merged is lone)
check("a lone message isn't held longer than its window",
      (time.monotonic() - started) < 2.0)

bot = FakeBot()
merged = run(bot, upd("carry on", reply_to=555), [upd("the rest of it")])
check("the first part's envelope wins (reply target and date kept)",
      (merged["message"].get("reply_to_message") or {}).get("message_id") == 555
      and merged["message"]["date"] == 1700000000)


# --- entity offsets ------------------------------------------------------

bot = FakeBot()
# An emoji ahead of the mention: Telegram counts entity offsets in UTF-16
# code units, where that emoji is 2. Offset arithmetic done in code points
# would land the mention a character early.
part1 = upd("\U0001f338 hello there")          # 13 UTF-16 units
part2 = upd("@kin are you about?",
            entities=[{"type": "mention", "offset": 0, "length": 4}])
merged = run(bot, part1, [part2])
joined = text_of(merged)
ents = merged["message"]["entities"]
check("part two's mention entity survives the merge", len(ents) == 1)
check("mention entity offset is shifted in UTF-16 space",
      T._utf16_slice(joined, ents[0]["offset"], ents[0]["length"]) == "@kin")


# --- what must never be merged ------------------------------------------

bot = FakeBot()
cmd = upd("/clear")
merged = run(bot, upd("a thought"), [cmd])
check("a slash command isn't swallowed into the message before it",
      text_of(merged) == "a thought")
check("the slash command is set aside to be handled next",
      bot._holdover == [cmd])

bot = FakeBot()
other = upd("hi from someone else", user=8)
merged = run(bot, upd("mine"), [other])
check("another person's message isn't merged into mine",
      text_of(merged) == "mine" and bot._holdover == [other])

bot = FakeBot()
elsewhere = upd("about that other thing", reply_to=42)
merged = run(bot, upd("here's my answer"), [elsewhere])
check("a reply quoting a different message starts a new turn",
      text_of(merged) == "here's my answer" and bot._holdover == [elsewhere])

bot = FakeBot()
photo = upd("look at this", photo=[{"file_id": "abc"}])
merged = run(bot, upd("hey"), [photo])
check("an attachment turn dispatches on its own",
      text_of(merged) == "hey" and bot._holdover == [photo])

bot = FakeBot()
photo_first = upd("look at this", photo=[{"file_id": "abc"}])
merged = run(bot, photo_first, [upd("and the caption continues")])
check("an attachment turn isn't held waiting for a continuation",
      merged is photo_first)

bot = FakeBot()
bot._pending_approvals[7] = object()
approving = upd("yes")
started = time.monotonic()
merged = run(bot, approving, [upd("go ahead then")])
check("a pending exec approval is never delayed", merged is approving)
check("...and returns immediately", (time.monotonic() - started) < 0.02)

bot = FakeBot()
sentinel_seen = run(bot, upd("last words"), [None])
check("the shutdown sentinel survives reassembly",
      text_of(sentinel_seen) == "last words" and bot._holdover == [None])


# --- rails ---------------------------------------------------------------

bot = FakeBot()
merged = run(bot, upd("0"), [upd(str(i)) for i in range(1, 40)])
parts_kept = len(text_of(merged).split("\n"))
check("a burst can't grow one turn past the part cap",
      parts_kept == T._COALESCE_MAX_PARTS)


# --- the pace is the person's to set ------------------------------------
#
# Telegram never tells a bot that someone is typing, so the wait can't be
# inferred — someone who composes slowly sets a longer one. Timing here is
# checked against the shrunken windows above.

bot = FakeBot(message_wait_secs=0.4)
check("a longer configured wait is honoured",
      abs(bot._coalesce_window(upd("hello")) - 0.4) < 1e-9)
started = time.monotonic()
merged = run(bot, upd("I am still"), [])
waited = time.monotonic() - started
check("...and the kin actually waits that long before answering alone",
      0.3 < waited < 2.0)

bot = FakeBot(message_wait_secs=0.4)
slow = upd("here comes the second half")


def _send_late():
    time.sleep(0.15)
    bot._queue.put(slow)


threading.Thread(target=_send_late, daemon=True).start()
merged = bot._coalesce_message_parts(upd("a thought that takes me a moment"))
check("text arriving inside the configured wait still joins the thought",
      text_of(merged) == "a thought that takes me a moment\nhere comes the second half")

bot = FakeBot(message_wait_secs=0)
lone = upd("answer me now")
started = time.monotonic()
merged = run(bot, lone, [])
check("0 answers immediately", merged is lone)
check("...with no pause at all", (time.monotonic() - started) < 0.05)

bot = FakeBot(message_wait_secs=0)
check("0 still reassembles a message Telegram cut at the ceiling — "
      "that pause is nobody's choice",
      bot._coalesce_window(upd("x" * (T._COALESCE_SPLIT_LEN + 1)))
      >= T._COALESCE_SPLIT_WINDOW_SECS)

bot = FakeBot(message_wait_secs=99999)
check("an absurd wait is clamped, so the kin can't be stranded silent",
      bot._coalesce_window(upd("hi")) == T._COALESCE_MAX_WAIT_SECS)

for junk in ("", None, "soon", float("nan"), []):
    bot = FakeBot(message_wait_secs=junk)
    got = bot._coalesce_window(upd("hi"))
    check(f"junk wait value {junk!r} falls back to the default, never raises",
          got == T._COALESCE_WINDOW_SECS)

bot = FakeBot()
check("no setting at all uses the shipped default",
      bot._coalesce_window(upd("hi")) == T._COALESCE_WINDOW_SECS)


# --- shipped constants stay coherent ------------------------------------

check("split-detection length sits below the hard-cut length",
      T._COALESCE_SPLIT_LEN < T._COALESCE_HARD_CUT_LEN < 4096)
check("a part cut at the ceiling waits longer for its continuation",
      _REAL_SPLIT_WINDOW > _REAL_WINDOW > 0)
check("the ordinary window is short enough to go unnoticed",
      _REAL_WINDOW <= 3.0)


print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_telegram_coalesce: all checks passed")
