# SPDX-License-Identifier: CC0-1.0
"""A reply that stops at the kin's reply-length cap is said to be cut off.

Every kin has a cap on how much it can write in one reply. When a model hits
it, it simply stops, often mid-sentence. Before 21 Nov 2026 nothing on any
surface noticed: the cut-off reply was saved as though the kin had chosen to
end there, the person saw a reply that just stopped, and the kin, next turn,
didn't know its last thought had been cut short.

Now the backend records why each model call stopped (Ollama's done_reason,
OpenRouter's finish_reason), and every surface asks straight after a reply:
the person sees one line, and the kin is left a note.

Run: python tests/test_cut_off_replies.py
"""
import inspect
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="cutoff-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import llm_backend  # noqa: E402
import chat_helpers  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# --- the backend notices, per provider ----------------------------------
def ollama_stream(done_reason):
    chunks = [{"message": {"content": "Once upon a"}},
              {"message": {"content": ""}, "done": True,
               "done_reason": done_reason, "eval_count": 5}]
    old_call, old_ollama = llm_backend._ollama_chat_callable, llm_backend.ollama
    llm_backend._ollama_chat_callable = lambda *a, **k: (lambda **kw: iter(chunks))
    llm_backend.ollama = object()
    try:
        list(llm_backend._chat_ollama_stream("m", [], {}, None, None))
    finally:
        llm_backend._ollama_chat_callable = old_call
        llm_backend.ollama = old_ollama
    return chat_helpers.take_cut_off()


check("Ollama: a reply that hit the cap is noticed", ollama_stream("length"))
check("Ollama: one that finished is not", not ollama_stream("stop"))


def openrouter_stream(finish_reason):
    events = [{"choices": [{"delta": {"content": "Once upon a"}}]},
              {"choices": [{"delta": {}, "finish_reason": finish_reason}]}]
    lines = [("data: " + json.dumps(e)).encode() for e in events]
    lines.append(b"data: [DONE]")

    class Resp(list):
        def close(self):
            pass

    old = llm_backend._openrouter_request
    llm_backend._openrouter_request = lambda *a, **k: Resp(lines)
    try:
        list(llm_backend._chat_openrouter_stream("m", [], {}, None, None, False))
    finally:
        llm_backend._openrouter_request = old
    return chat_helpers.take_cut_off()


check("OpenRouter: a reply that hit the cap is noticed",
      openrouter_stream("length"))
check("OpenRouter: one that finished is not", not openrouter_stream("stop"))

ollama_stream("length")
check("asking clears the answer, so the next reply starts fresh",
      chat_helpers.take_cut_off() is False)

# --- what the person and the kin are told --------------------------------
check("the person's line says what happened",
      "length limit" in chat_helpers.CUT_OFF_LINE)
row = chat_helpers.cut_off_note_row("Wren")
check("the kin's note is a note, not its own words",
      row["role"] == "system" and "length limit" in row["content"])
room = chat_helpers.cut_off_note_row("Wren", room=True)
check("in a room it names the kin, since every kin there reads it",
      "Wren" in room["content"])

# --- every surface asks, straight after the reply ------------------------
import telegram_bot  # noqa: E402
import discord_bot  # noqa: E402
import hearthkin_cron  # noqa: E402
from frame.chat_send_mixin import ChatSendMixin  # noqa: E402
from frame.chat_stream_mixin import ChatStreamMixin  # noqa: E402
from frame import rooms_mixin  # noqa: E402

tg = inspect.getsource(telegram_bot)
check("Telegram, direct messages and groups, both ask",
      tg.count("_cut_off = take_cut_off()") == 2
      and tg.count("CUT_OFF_LINE") >= 2 and tg.count("cut_off_note_row") >= 2)
dc = inspect.getsource(discord_bot.DiscordBot._generate)
check("Discord asks, shows the line, and notes it for the kin",
      "take_cut_off()" in dc and "CUT_OFF_LINE" in dc
      and "cut_off_note_row" in dc)
send = inspect.getsource(ChatSendMixin)
done = inspect.getsource(ChatStreamMixin._on_stream_done)
check("the desktop asks on both of its paths and tells you both",
      send.count("take_cut_off()") == 2 and "CUT_OFF_LINE" in done
      and "cut_off_note_row" in done)
rm = inspect.getsource(rooms_mixin)
check("rooms ask on both paths and note it in the room's own words",
      rm.count("take_cut_off()") == 2 and "room=True" in rm)
cr = inspect.getsource(hearthkin_cron)
check("wake-ups ask on both paths, before anything else calls a model",
      cr.count("_cut_off = take_cut_off()") == 2)

if _fails:
    print(f"{len(_fails)} FAILED: " + "; ".join(_fails))
    sys.exit(1)
print("test_cut_off_replies: all checks passed")
