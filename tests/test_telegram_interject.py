# SPDX-License-Identifier: CC0-1.0
"""Standalone tests: on Telegram, what the person sends while their reply is
still running reaches that reply (no network, no disk).

A kin went round its journals with dozens of commands in one reply. The
person typed /stop; it only denied the one command waiting for approval, and
the next stepped up. A plain "what are you doing" queued behind the reply it
was about. Now:

  - /stop (or a bare "stop") while a command waits for approval denies it
    AND stops the whole reply;
  - any other plain message while a command waits denies it and is handed
    to the running reply, to be read before the next step;
  - a plain message mid-reply with nothing waiting is handed to the reply;
  - a held message the reply never got to is queued as an ordinary message
    when the reply ends, so nothing is lost;
  - commands, other people, and other chats are left alone.
"""

import os
import queue
import sys
import tempfile
import threading

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="hk-tginter-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import telegram_bot as T  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


class FakeBot(T.TelegramBot):
    """Turn, stop, approval and held-message state only. No __init__."""

    def __init__(self):
        self._queue = queue.Queue()
        self._stop = threading.Event()
        self._pending_lock = threading.RLock()
        self._pending_approvals = {}
        self._turn_lock = threading.RLock()
        self._active_turn = None
        self._cancelled_turns = set()
        self.agent_name = "Wren"
        self.sent = []

    def get_config(self):
        return {}

    def _send_chunked(self, chat_id, text):
        self.sent.append((chat_id, text))


def upd(text, user=7, chat=7, private=True):
    return {"update_id": 1, "message": {
        "message_id": 1, "from": {"id": user},
        "chat": {"id": chat, "type": "private" if private else "group"},
        "text": text}}


def waiting(bot, user=7, chat=7):
    p = T._PendingApproval(event=threading.Event(), command="Get-Content x",
                           args_summary="", chat_id=chat)
    bot._pending_approvals[user] = p
    return p


def main():
    # --- A plain message mid-reply goes to the reply -------------------- #
    bot = FakeBot()
    bot._begin_turn(7, 7)
    check("a plain message mid-reply is taken for the reply",
          bot._maybe_interject_from_poll(upd("what are you doing?")) is True)
    check("...and the person is told it will be read before the next step",
          any("before its next step" in t for _, t in bot.sent))
    turns = bot._take_interjections(7)
    check("the running reply gets it as a user turn",
          len(turns) == 1 and turns[0]["role"] == "user"
          and turns[0]["content"] == "what are you doing?" and turns[0].get("ts"))
    check("...only once", bot._take_interjections(7) == [])

    # Left alone: commands, nothing running, other people, other chats.
    bot = FakeBot()
    check("no reply running: the message queues as normal",
          bot._maybe_interject_from_poll(upd("hello")) is False)
    bot._begin_turn(7, 7)
    check("a command is not taken", bot._maybe_interject_from_poll(upd("/help")) is False)
    check("someone else's message is not taken",
          bot._maybe_interject_from_poll(upd("hi", user=8, chat=8)) is False)
    check("the same person in a different chat is not taken",
          bot._maybe_interject_from_poll(upd("hi", chat=-100, private=False)) is False)

    # In a group, a message from the person whose reply is running may be
    # meant for someone else: it is not pulled into the kin's turn.
    bot = FakeBot()
    bot._begin_turn(7, -100)
    check("in a group, the same person's message is not taken",
          bot._maybe_interject_from_poll(upd("lol same", chat=-100, private=False)) is False)
    check("...and nothing is posted in the group", bot.sent == [])

    # Never lost: a held message the reply never reached is queued at the end.
    bot = FakeBot()
    bot._begin_turn(7, 7)
    bot._maybe_interject_from_poll(upd("one more thing"))
    bot._end_turn(7)
    check("a held message the reply never reached is queued when it ends",
          bot._queue.qsize() == 1
          and bot._queue.get()["message"]["text"] == "one more thing")

    # --- While a command waits for approval ----------------------------- #
    bot = FakeBot()
    bot._begin_turn(7, 7)
    p = waiting(bot)
    check("/stop while a command waits is consumed",
          bot._maybe_resolve_approval_from_poll(upd("/stop")) is True)
    check("...denies that command", p.event.is_set() and p.decision == "deny")
    check("...and stops the whole reply", bot._turn_cancelled(7) is True)

    bot = FakeBot()
    bot._begin_turn(7, 7)
    p = waiting(bot)
    bot._maybe_resolve_approval_from_poll(upd("stop"))
    check("a bare 'stop' while a command waits stops the reply too",
          p.decision == "deny" and bot._turn_cancelled(7) is True)

    bot = FakeBot()
    bot._begin_turn(7, 7)
    p = waiting(bot)
    check("'no' still just denies the one command",
          bot._maybe_resolve_approval_from_poll(upd("no")) is True
          and p.decision == "deny" and bot._turn_cancelled(7) is False)

    bot = FakeBot()
    bot._begin_turn(7, 7)
    p = waiting(bot)
    check("'yes' still approves",
          bot._maybe_resolve_approval_from_poll(upd("yes")) is True
          and p.decision == "allow" and bot._turn_cancelled(7) is False)

    bot = FakeBot()
    bot._begin_turn(7, 7)
    p = waiting(bot)
    check("any other message while a command waits is consumed",
          bot._maybe_resolve_approval_from_poll(upd("you're going in circles")) is True)
    check("...denies the waiting command", p.event.is_set() and p.decision == "deny")
    check("...and hands the message to the reply",
          [t["content"] for t in bot._take_interjections(7)] == ["you're going in circles"])
    check("...without stopping it outright", bot._turn_cancelled(7) is False)

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
