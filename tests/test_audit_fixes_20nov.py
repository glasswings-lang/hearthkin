# SPDX-License-Identifier: CC0-1.0
"""Fixes from the 20 Nov 2026 surface audit, each through the real code.

  * Telegram's "your message is in the queue, nothing is lost" notice goes
    only to someone whose message will actually be answered (it ran before
    every gate).
  * A Telegram DM reply goes through the same speaker-tag cleanup as every
    other surface (it only lost its timestamp).
  * Saved reasoning is sent back on Telegram only when the kin feeds it back.
  * Cloning a kin resets its Discord bot, as it always reset Telegram's.
  * A Discord picture or file sent with no words is answered.
  * A Discord error is said in plain words, and the raw details (paths,
    provider bodies) are kept from anyone but the kin's own person in a DM.

Run: python tests/test_audit_fixes_20nov.py
"""

import asyncio
import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="auditfix-", dir=(os.environ.get("HEARTHKIN_HOME") or None))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import llm_backend  # noqa: E402
from kin_persistence import (DEFAULT_AGENT_CONFIG, clone_agent,  # noqa: E402
                             load_agent_config, save_agent_config)
from hearthkin_paths import kin_dir  # noqa: E402
import telegram_bot as _tb  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Bracken"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)
cfg = dict(DEFAULT_AGENT_CONFIG)
cfg["model"] = "stub-model"
save_agent_config(KIN, cfg)

TG = {"allow_from": ["7"], "user_tools": {"7": "none"},
      "groups": {"-5": {"label": "G", "policy": "mention_only", "exclude": [9]},
                 "-6": {"label": "H", "policy": "always"}}}


def tg_bot(conf=None):
    b = _tb.TelegramBot.__new__(_tb.TelegramBot)
    b.__init__(KIN, get_config=lambda: conf if conf is not None else TG,
               get_soul=lambda: "A small kin.", get_memory=lambda: "",
               get_model_options=lambda: ("stub-model", {"num_ctx": 8192}),
               on_status=lambda *a: None)
    b._bot_user_id = 99
    b._bot_username = "kinbot"
    b.token = "x"
    return b


def m(chat_id, ctype, uid, text="hi", reply_to=None):
    msg = {"message_id": 1, "chat": {"id": chat_id, "type": ctype},
           "from": {"id": uid}, "text": text, "date": 0}
    if reply_to:
        msg["reply_to_message"] = {"from": {"id": reply_to}}
    return msg


print("--- the queued notice only goes to someone who'll be answered ---")
b = tg_bot()
check("an allowed person in a DM: yes", b._would_answer(m(7, "private", 7)))
check("a stranger in a DM: no", not b._would_answer(m(8, "private", 8)))
check("anyone, when the list says anyone",
      tg_bot({"allow_from": ["*"]})._would_answer(m(8, "private", 8)))
check("a group the kin isn't in: no", not b._would_answer(m(-4, "group", 7)))
check("a muted person: no", not b._would_answer(m(-5, "group", 9, reply_to=99)))
check("mentions-only group, not spoken to: no",
      not b._would_answer(m(-5, "group", 7)))
check("...a reply to the kin: yes",
      b._would_answer(m(-5, "group", 7, reply_to=99)))
check("a group that answers everything: yes",
      b._would_answer(m(-6, "group", 7)))
b = tg_bot()
b.get_busy_label = lambda skip=None: "Distilling memory"
sent = []
b._send_chunked = lambda cid, t: sent.append((cid, t))
b._maybe_say_queued_from_poll({"message": m(8, "private", 8)})
check("the notice really isn't sent to a stranger", sent == [])
b._maybe_say_queued_from_poll({"message": m(7, "private", 7)})
check("...and is sent to an allowed person",
      len(sent) == 1 and "may wait" in sent[0][1])

print("--- a Telegram DM reply is cleaned of speaker tags ---")


class _Result:
    def __init__(self, content):
        self.content = content
        self.thinking = ""
        self.stopped = False
        self.messages_added = []
        self.usage = {}


seen = {}


def fake_collect(model, messages, **kw):
    seen["messages"] = messages
    return _Result(f"[{KIN}]: Hello there.")


real_api, real_collect = _tb.telegram_api_call, llm_backend.chat_collect
real_chat = llm_backend.chat
_tb.telegram_api_call = lambda *a, **k: {"result": {"message_id": 5}}
llm_backend.chat_collect = fake_collect
llm_backend.chat = lambda model, messages, **kw: fake_collect(model, messages)
try:
    b = tg_bot()
    out = []
    b._send_chunked = lambda cid, t: out.append(t)
    b._send_typing = lambda *a, **k: None
    from kin_persistence import save_agent_conversation
    save_agent_conversation(KIN, [
        {"role": "assistant", "content": "earlier", "thinking": "secret reasoning",
         "ts": "2026-11-20T10:00:00"}])
    TG["user_share_desktop"] = {"7": True}
    b._handle_normal_message("hi", 7, 7, msg=m(7, "private", 7))
finally:
    _tb.telegram_api_call, llm_backend.chat_collect = real_api, real_collect
    llm_backend.chat = real_chat
    TG.pop("user_share_desktop", None)
from kin_persistence import load_agent_conversation  # noqa: E402
saved = [r for r in load_agent_conversation(KIN) if r.get("role") == "assistant"]
check("the reply ran", bool(seen.get("messages")))
check("the reply is saved without the kin's own speaker tag",
      saved and saved[-1].get("content") == "Hello there.")
check("saved reasoning isn't sent back when the kin doesn't feed it back",
      not any(isinstance(x, dict) and x.get("thinking")
              for x in seen.get("messages", [])))
check("the reasoning row helper keeps it when feeding back is on",
      _tb._without_thinking({"role": "assistant", "thinking": "t"}, True)
      .get("thinking") == "t")

print("--- cloning a kin resets its Discord bot ---")
c = load_agent_config(KIN)
c["discord"] = {"bot_token": "SECRET", "enabled": True, "allow_from": ["*"],
                "user_tools": {"*": "full"}}
save_agent_config(KIN, c)
clone_agent(KIN, "Bracken2")
dc = load_agent_config("Bracken2").get("discord") or {}
check("the clone has no Discord token, people or tool access",
      not dc.get("bot_token") and not dc.get("allow_from")
      and not dc.get("user_tools") and not dc.get("enabled"))

print("--- Discord: a picture with no words; errors in plain words ---")
from discord_bot import DiscordBot  # noqa: E402


class _User:
    def __init__(self, uid=7, bot=False):
        self.id = uid
        self.bot = bot
        self.name = self.display_name = "Wren"
        self.mention = f"<@{uid}>"


class _Chan:
    def __init__(self):
        self.id = 555
        self.sent = []

    def typing(self):
        class _T:
            async def __aenter__(s):
                return s

            async def __aexit__(s, *a):
                return False
        return _T()


class _Msg:
    def __init__(self, ch, guild=None, content="", attachments=None):
        self.content = content
        self.author = _User()
        self.channel = ch
        self.mentions = []
        self.attachments = attachments or []
        self.guild = guild


class _Client:
    user = _User(1, bot=True)


def dc_bot(dcfg):
    d = DiscordBot.__new__(DiscordBot)
    d.agent_name = KIN
    d.transcribe_voice = None
    d._turn_lock = threading.Lock()
    d._active_turns = {}
    d._user_last_reply = {}
    d._channel_locks = {}
    d._gen_semaphore = None
    d._warned_empty_allow = False
    d._dm_channels = set()
    d.on_activity = None
    d.get_busy_label = None
    d.get_config = lambda: {"discord": dcfg, "telegram": {"message_wait_secs": 0}}
    d.generated = []

    async def send(channel, text):
        channel.sent.append(text)
    d._send_chunked = send

    async def none(msg):
        return []
    d._download_images = none
    d._download_text_documents = none
    return d


def run(coro_fn):
    async def go():
        return await coro_fn()
    return asyncio.run(go())


d, ch = dc_bot({"policy": "mention_only", "allow_from": ["*"]}), _Chan()


async def gen(message, content, imgs, docs):
    d.generated.append(content)
d._generate_in_turn = gen


async def go_pic():
    d._loop = asyncio.get_running_loop()
    class _Pic:
        filename = "photo.png"
        content_type = "image/png"
        size = 10

        def is_voice_message(self):
            return False
    await d._on_message(_Client(), _Msg(ch, attachments=[_Pic()]))
run(go_pic)
check("a picture sent with no words still reaches the kin", d.generated == [""])

secret = r"C:\Users\someone\private\thing.txt"


def boom(*a, **k):
    raise RuntimeError(f"model failed reading {secret}")


for label, guild, is_me, shown in (
        ("in a public channel", object(), True, False),
        ("in a DM with someone else", None, False, False),
        ("in a DM with the kin's own person", None, True, True)):
    dcfg = {"policy": "always", "allow_from": ["*"],
            "user_is_operator": {"7": True} if is_me else {}}
    d, ch = dc_bot(dcfg), _Chan()
    d._generate = boom

    async def go_err():
        d._loop = asyncio.get_running_loop()
        d._gen_semaphore = asyncio.Semaphore(3)
        await d._generate_in_turn(_Msg(ch, guild=guild, content="hi"),
                                  "hi", [], [])
    run(go_err)
    said = " ".join(ch.sent)
    check(f"an error {label} is said in plain words",
          said.startswith("⚠️") and "[error:" not in said)
    check(f"...and the raw details are {'shown' if shown else 'kept back'}",
          (secret in said) == shown)

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_audit_fixes_20nov: all checks passed")
