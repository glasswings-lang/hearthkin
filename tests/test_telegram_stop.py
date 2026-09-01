# SPDX-License-Identifier: CC0-1.0
"""Guard test: /cancel stops the reply being written.

It used to only deny a pending tool approval, while `/help` advertised it as
cancelling "a pending operation" — which reads as "stop the reply". Worse,
with no approval pending the command queued up BEHIND the very reply it was
meant to stop, so "nothing to cancel" arrived after the reply already had.

Two halves are pinned here:

  * `llm_backend` — a `should_stop` callback is polled per streamed chunk and
    between tool-loop iterations. Stopping KEEPS what the kin already said
    (throwing it away would be a worse answer to "stop") and drops the
    half-formed tool call it was mid-way through emitting, because running a
    truncated `write_file` is the opposite of stopping. A stop check that
    raises means "keep going" — a flaky callback must never truncate a
    healthy reply.

  * `TelegramBot` — the stop is requested from the POLL thread, since the
    inference thread is inside the model call and won't read the queue again
    until the reply it's generating is done. A stop is keyed to the person who
    asked, so one group member can't halt a reply being written for someone
    else, and it can't leak into the next turn.
"""

import os
import sys
import queue
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm_backend as L  # noqa: E402
import telegram_bot as T  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


class Chunk:
    def __init__(self, content="", thinking="", tool_calls=None, done=False, usage=None):
        self.content = content
        self.thinking = thinking
        self.tool_calls = tool_calls
        self.done = done
        self.usage = usage
        self.heartbeat = False


# --- llm_backend: the stop channel --------------------------------------

check("a missing stop check means keep going", L._loop_should_stop(None) is False)


def _raiser():
    raise RuntimeError("stop check exploded")


check("a stop check that raises means keep going, never truncate",
      L._loop_should_stop(_raiser) is False)
check("a truthy stop check stops", L._loop_should_stop(lambda: 1) is True)

_real_chat = L.chat
_streamed = []


def _fake_chat(model, messages, **kwargs):
    _streamed.append(True)
    for word in ("Hello", " there", " friend", " of", " mine"):
        yield Chunk(content=word)
    yield Chunk(done=True, usage={"prompt_tokens": 1})


L.chat = _fake_chat
try:
    got = []
    res = L._chat_collect_streaming(
        "m", [], on_content=got.append,
        # Stop once three words have landed.
        should_stop=lambda: len(got) >= 3)
    check("stopping mid-stream keeps what the kin already said",
          res.content == "Hello there friend")
    check("...and says so on the result", res.stopped is True)

    res = L._chat_collect_streaming("m", [], on_content=lambda _t: None)
    check("with no stop check the whole reply arrives",
          res.content == "Hello there friend of mine" and res.stopped is False)

    res = L.chat_collect("m", [])
    check("chat_collect needs no render callback to be interruptible",
          res.content == "Hello there friend of mine")

    # A stream that emits a tool call and keeps talking. Stopping must drop
    # the call rather than run it half-formed.
    def _fake_chat_with_tool(model, messages, **kwargs):
        yield Chunk(content="one moment")
        yield Chunk(tool_calls=[{"id": "abc123456", "function":
                                 {"name": "write_file", "arguments": "{\"pa"}}])
        yield Chunk(content=" still going")
        yield Chunk(done=True)

    L.chat = _fake_chat_with_tool
    ran = []
    res = L.run_tool_loop(
        "m", [], tools=[{"name": "write_file"}],
        tool_executor={"write_file": lambda a: ran.append(a) or "written"},
        on_content=lambda _t: None,
        should_stop=lambda: True)
    check("a stop drops the half-formed tool call instead of running it",
          ran == [] and res.tool_calls == [])
    check("...and reports itself stopped", res.stopped is True)

    # Between-iteration stop: the model wants a tool, the person says stop
    # before another model call is spent.
    calls = []

    def _fake_chat_loop(model, messages, **kwargs):
        calls.append(True)
        yield Chunk(content="working")
        yield Chunk(done=True)

    L.chat = _fake_chat_loop
    res = L.run_tool_loop(
        "m", [], tools=[], tool_executor={},
        on_content=lambda _t: None,
        should_stop=lambda: True)
    check("a stop before the first iteration spends no model call",
          calls == [] and res.stopped is True)
finally:
    L.chat = _real_chat

check("a caller that never asks to stop sees stopped=False",
      L.ChatResult().stopped is False)


# --- TelegramBot: who may stop what -------------------------------------

class FakeBot(T.TelegramBot):
    """Just the turn/stop state. Skips __init__ — no disk, no network."""

    def __init__(self):
        self._queue = queue.Queue()
        self._holdover = []
        self._stop = threading.Event()
        self._pending_lock = threading.RLock()
        self._pending_approvals = {}
        self._turn_lock = threading.RLock()
        self._active_turn = None
        self._cancelled_turns = set()
        self.sent = []

    def get_config(self):
        return {}

    def _send_chunked(self, chat_id, text):
        self.sent.append((chat_id, text))


def cancel_upd(text="/cancel", user=7, chat=7):
    return {"update_id": 1, "message": {
        "message_id": 1, "from": {"id": user},
        "chat": {"id": chat, "type": "private"}, "text": text}}


bot = FakeBot()
check("nothing to stop when no reply is being written",
      bot._request_turn_stop(7, 7) is False)

bot._begin_turn(7, 7)
check("a reply in flight can be stopped", bot._request_turn_stop(7, 7) is True)
check("the model call sees the stop", bot._turn_cancelled(7) is True)

bot = FakeBot()
bot._begin_turn(7, 7)
check("someone else can't stop a reply being written for me",
      bot._request_turn_stop(8, 7) is False)
check("...and mine keeps going", bot._turn_cancelled(7) is False)

bot = FakeBot()
bot._begin_turn(7, 7)
bot._request_turn_stop(7, 7)
bot._end_turn(7)
check("the stop is cleared when the turn ends", bot._turn_cancelled(7) is False)

bot = FakeBot()
bot._cancelled_turns.add(7)  # stale request left over from an earlier turn
bot._begin_turn(7, 7)
check("a stale stop can't kill the next turn", bot._turn_cancelled(7) is False)


# --- the poll-thread intercept ------------------------------------------

bot = FakeBot()
bot._begin_turn(7, 7)
check("/cancel is consumed on the poll thread, not queued behind the reply",
      bot._maybe_stop_turn_from_poll(cancel_upd()) is True)
check("...the person is told immediately",
      bot.sent and "Stopping" in bot.sent[0][1])
check("...and the model call sees it", bot._turn_cancelled(7) is True)

bot = FakeBot()
bot._begin_turn(7, 7)
check("/stop works the same", bot._maybe_stop_turn_from_poll(cancel_upd("/stop")) is True)

bot = FakeBot()
bot._begin_turn(7, 7)
check("/cancel@BotName works — Telegram appends the bot name in groups",
      bot._maybe_stop_turn_from_poll(cancel_upd("/cancel@SomeKinBot")) is True)

bot = FakeBot()
check("with nothing in flight, /cancel falls through to the queued handler "
      "that knows about approvals",
      bot._maybe_stop_turn_from_poll(cancel_upd()) is False)
check("...and nothing was said prematurely", bot.sent == [])

bot = FakeBot()
bot._begin_turn(7, 7)
bot._pending_approvals[7] = object()
check("a /cancel meaning 'deny this tool call' is left to the approval "
      "resolver, not stolen",
      bot._maybe_stop_turn_from_poll(cancel_upd()) is False)
check("...so the reply is not stopped by it", bot._turn_cancelled(7) is False)

bot = FakeBot()
bot._begin_turn(7, 7)
for other in ("/clear", "/help", "hello", "cancel", ""):
    check(f"{other!r} is not a stop command",
          bot._maybe_stop_turn_from_poll(cancel_upd(other)) is False)


# --- the queued fallback ------------------------------------------------

bot = FakeBot()
bot._begin_turn(7, 7)
bot._cmd_cancel(7, 7)
check("the queued /cancel still stops a turn the poll thread missed",
      bot._turn_cancelled(7) is True and "Stopping" in bot.sent[-1][1])

bot = FakeBot()
bot._cmd_cancel(7, 7)
check("with nothing to cancel, it says so plainly and claims no stop button "
      "is missing",
      "Nothing to cancel" in bot.sent[-1][1]
      and "no stop button" not in bot.sent[-1][1])


# --- what the two doc surfaces promise ----------------------------------

_cancel_menu = [c for c in T.TelegramBot.BOT_COMMANDS
                if c.get("command") == "cancel"]
check("the Telegram command menu describes stopping a reply",
      len(_cancel_menu) == 1 and "stop" in _cancel_menu[0]["description"].lower())


print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_telegram_stop: all checks passed")
