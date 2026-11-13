# SPDX-License-Identifier: CC0-1.0
"""Telegram groups read a text file someone uploads, as private chats do.

Groups used to see only a file's caption, so "here are my notes" arrived with
nothing attached. Since 12 Nov 2026 an uploaded text document is read in
front of the kin for that turn. Naming a file on the operator's machine is
still limited to people with read_file, as before.

Also checks "Tool history kept" now applies on Telegram: older tool steps
reach the model as one-line markers.

Drives the real group handler with the model and network stubbed.

Run: python tests/test_telegram_group_textdoc.py
"""

import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="tggroupdoc-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import llm_backend  # noqa: E402
from kin_persistence import DEFAULT_AGENT_CONFIG, save_agent_config  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402
from telegram_bot import TelegramBot  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Bracken"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)
cfg = dict(DEFAULT_AGENT_CONFIG)
cfg["model"] = "stub-model"
cfg["tool_history_keep"] = 0
save_agent_config(KIN, cfg)

CHAT, USER = -100, 7
TG = {"allow_from": [str(USER)], "user_tools": {str(USER): "none"},
      "groups": {str(CHAT): {"label": "Test group", "policy": "always"}}}


class _Result:
    def __init__(self, content):
        self.content = content
        self.thinking = ""
        self.stopped = False
        self.messages_added = []


def build_bot():
    b = TelegramBot.__new__(TelegramBot)
    b.__init__(KIN, get_config=lambda: TG, get_soul=lambda: "A small kin.",
               get_memory=lambda: "",
               get_model_options=lambda: ("stub-model", {"num_ctx": 8192}),
               on_status=lambda *a: None)
    b.token = "x"
    b.sent = []
    b._send_chunked = lambda chat_id, text: b.sent.append(text)
    b._send_typing = lambda *a, **k: None
    return b


seen = {"api": [], "during": 0}


def fake_collect(model, messages, **kw):
    seen["messages"] = messages
    on = kw.get("on_content")
    seen["had_on_content"] = on is not None
    seen["before"] = len(seen["api"])
    if on:
        for piece in ("Thanks, ", "I read them ", "all the way through."):
            on(piece)
            time.sleep(0.05)
    seen["during"] = len(seen["api"])
    return _Result("Thanks, I read them all the way through.")


import time  # noqa: E402
import telegram_bot as _tb  # noqa: E402
real_api = _tb.telegram_api_call


def fake_api(token, method, params=None, **kw):
    seen["api"].append((method, (params or {}).get("text", "")))
    return {"result": {"message_id": 5}}


_tb.telegram_api_call = fake_api
real = llm_backend.chat_collect
llm_backend.chat_collect = fake_collect
try:
    b = build_bot()
    b._download_text_documents = lambda msg: [("notes.md", b"Plant the beans on Friday.")]
    msg = {"message_id": 1, "chat": {"id": CHAT, "type": "group", "title": "T"},
           "from": {"id": USER, "first_name": "Wren"},
           "caption": "here are my notes", "date": 1700000000,
           "document": {"file_id": "f", "file_name": "notes.md",
                        "mime_type": "text/markdown", "file_size": 26}}
    try:
        b._handle_group_message(msg, USER, CHAT, "group", "T")
        ran = True
    except Exception as e:
        ran = False
        print("handler raised:", repr(e))
finally:
    llm_backend.chat_collect = real
    _tb.telegram_api_call = real_api

check("the group reply ran", ran and "messages" in seen)
check("a kin without tools: the reply is handed to the live editor",
      seen.get("had_on_content") is True)
_while = seen["api"][seen.get("before", 0):seen["during"]]
check("...and starts appearing while it's still being written",
      any(m == "sendMessage" for m, _t in _while))
check("...ending as the whole reply, in one message (no second copy)",
      any(m == "editMessageText" and "all the way through." in t
          for m, t in seen["api"])
      and not any("all the way through." in s for s in b.sent))
blob = "\n".join(str(m.get("content") or "") for m in seen.get("messages", []))
check("the uploaded file's text reached the kin in a group",
      "Plant the beans on Friday." in blob)
check("...from someone with no tools (it's their file, not the machine's)",
      TG["user_tools"][str(USER)] == "none")

# "Tool history kept" on Telegram.
hist = [{"role": "user", "content": "read it"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "c1", "function": {"name": "read_file",
                                                  "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "a long result " * 50},
        {"role": "assistant", "content": "Done."}]
out = build_bot()._compact_for_model(hist)
check("Telegram applies 'Tool history kept' (0 here): the old step is a marker",
      not any(h.get("role") == "tool" for h in out)
      and any("earlier tool call" in (h.get("content") or "") for h in out))
check("...and the saved history itself is untouched",
      hist[2]["role"] == "tool")

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_telegram_group_textdoc: all checks passed")
