# SPDX-License-Identifier: CC0-1.0
"""Guard test: Telegram says when a message is going to wait on our own work.

Ollama answers one request at a time, so while Hearthkin has a distillation, a
scheduled wake-up or a heartbeat out on the same model, a Telegram message is
neither lost nor being worked on. It is queued, and completely silent, for as
long as that takes -- routinely thirteen minutes for a distillation bite.

The desktop at least has an Activity line to read. Telegram had nothing, so the
only way to find out was to send and wait an unknown amount of time. What that
produced was not confusion but hesitation: whether to send at all. That is the
defect being fixed; the notice is only how.

It has to run on the POLL thread, for the same reason `/cancel` does -- the one
inference thread is inside a model call and will not read its queue until the
current reply finishes, so anything that must be said DURING the wait cannot be
said from there.

What this pins:

  - it speaks when our background work has the model, and not otherwise;
  - it NEVER consumes the update -- the message must still be answered;
  - once per busy period per chat, so a long paste arriving as three updates
    does not produce three notices;
  - and the latch releases when the work finishes, or it would fire once in
    the lifetime of the bot and never again.

Run: python tests/test_telegram_queued_notice.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="tgqueue-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


from telegram_bot import TelegramBot  # noqa: E402


class _Bot(TelegramBot):
    """Just the poll-thread notice. No network, no threads, no config."""

    def __init__(self, busy="", active_turn=None):
        import threading
        self.busy = busy
        self.agent_name = "Bracken"
        self.asked_skip = []
        self._active_turn = active_turn      # (user_id, chat_id) or None
        self._turn_lock = threading.Lock()
        self._queued_notice_sent = set()
        self.sent = []

        def _label(skip_bot=None):
            self.asked_skip.append(skip_bot)
            return self.busy
        self.get_busy_label = _label

    def _send_chunked(self, chat_id, text):
        self.sent.append((chat_id, text))


def msg(text="hello", chat_id=7):
    return {"update_id": 1,
            "message": {"text": text, "chat": {"id": chat_id, "type": "private"},
                        "from": {"id": 99}}}


# --- speaks when something of ours has the model -------------------------

b = _Bot(busy="Bracken is saving notes to its memory")
b._maybe_say_queued_from_poll(msg())
check("a queued message is told what it is waiting on",
      len(b.sent) == 1
      and "Bracken is saving notes to its memory" in b.sent[0][1])
check("...and told the message is not lost, which is the actual worry",
      "queue" in b.sent[0][1] and "nothing is lost" in b.sent[0][1].lower())
check("...in the chat it came from", b.sent[0][0] == 7)


# --- silent when nothing of ours is running ------------------------------

b = _Bot(busy="")
b._maybe_say_queued_from_poll(msg())
check("a quiet app says nothing at all", b.sent == [])

b = _Bot(busy="   ")
b._maybe_say_queued_from_poll(msg())
check("a blank label is treated as quiet, not spoken", b.sent == [])

b = _Bot(busy="Bracken is saving notes to its memory")
b.get_busy_label = None
b._maybe_say_queued_from_poll(msg())
check("no callback wired means silence, not a crash", b.sent == [])

b = _Bot(busy="Bracken is busy")
def _boom():
    raise RuntimeError("frame went away")
b.get_busy_label = _boom
b._maybe_say_queued_from_poll(msg())
check("a probe that raises costs silence, never the message", b.sent == [])


# --- once per busy period, per chat --------------------------------------

b = _Bot(busy="Bracken is saving notes to its memory")
for _ in range(3):
    b._maybe_say_queued_from_poll(msg())
check("a long paste arriving as three updates gets ONE notice",
      len(b.sent) == 1)

b._maybe_say_queued_from_poll(msg(chat_id=8))
check("...but a different chat is told too", len(b.sent) == 2)

# The work finishes; the latch must release, or this fires once ever.
b.busy = ""
b._maybe_say_queued_from_poll(msg())
check("nothing is said once the work is done", len(b.sent) == 2)
b.busy = "Bracken is saving notes to its memory"
b._maybe_say_queued_from_poll(msg())
check("...and the NEXT busy period speaks again", len(b.sent) == 3)


# --- whose turn counts as a wait -----------------------------------------
#
# From a phone you cannot see the main window, a room, or somebody else's DM.
# All of them hold the model exactly as firmly as a distillation does, so all
# of them are worth being told about — except a reply to the message you just
# sent, which you already know about.

b = _Bot(busy="Bracken is replying to Sam on Telegram")
b._maybe_say_queued_from_poll(msg(chat_id=7))
check("with no turn of ours in this chat, nothing is skipped",
      b.asked_skip == [None] and len(b.sent) == 1)

b = _Bot(busy="", active_turn=(99, 7))
b._maybe_say_queued_from_poll(msg(chat_id=7))
check("a reply to THIS chat is skipped - you just sent the message it answers",
      b.asked_skip == ["Bracken"])

b = _Bot(busy="", active_turn=(55, 12))
b._maybe_say_queued_from_poll(msg(chat_id=7))
check("...but a reply to a DIFFERENT chat is not skipped, being invisible "
      "from here",
      b.asked_skip == [None])


# --- what it stays out of ------------------------------------------------

b = _Bot(busy="Bracken is saving notes to its memory")
b._maybe_say_queued_from_poll(msg(text="/cancel"))
check("slash commands are left alone", b.sent == [])

b = _Bot(busy="Bracken is saving notes to its memory")
b._maybe_say_queued_from_poll({"update_id": 2})
check("an update with no message is ignored", b.sent == [])
b._maybe_say_queued_from_poll({"update_id": 3, "message": {"text": "hi"}})
check("a message with no chat id is ignored", b.sent == [])


# --- it must never eat the message ---------------------------------------
#
# The notice is additive. If it ever returned a truthy "consumed" the poll
# loop would skip the queue.put and the message would vanish -- which is the
# one outcome strictly worse than the silence being fixed.

b = _Bot(busy="Bracken is saving notes to its memory")
check("the notice never reports the update as consumed",
      not b._maybe_say_queued_from_poll(msg()))

import inspect  # noqa: E402

src = inspect.getsource(TelegramBot._poll_loop)
_notice = src.index("_maybe_say_queued_from_poll")
_put = src.index("self._queue.put(upd)")
check("the poll loop still enqueues the update after speaking",
      _notice < _put)
check("...and the notice is wrapped, so a fault in it cannot cost intake",
      "except Exception" in src[_notice:_put])
check("it runs on the poll thread, beside the /cancel intercept",
      "_maybe_stop_turn_from_poll" in src)


print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_telegram_queued_notice: all checks passed")
