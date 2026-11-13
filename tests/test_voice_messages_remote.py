# SPDX-License-Identifier: CC0-1.0
"""A voice message to a kin on Telegram or Discord reaches it as words
(12 Nov 2026). Both used to ignore one without a word.

Drives the real message handlers (Telegram's _handle_update, Discord's
_on_message) with a voice message in a private chat, the download and the
speech model stubbed, and checks: the person is told what was heard, the kin
receives the words marked as a voice message, a failure is explained rather
than dropped, and in a group or server channel nothing is transcribed.

Run: python tests/test_voice_messages_remote.py
"""

import asyncio
import os
import sys
import tempfile
import threading

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="voicemsg-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import telegram_bot  # noqa: E402
from discord_bot import DiscordBot  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


heard_calls = []


def hear(data):
    heard_calls.append(data)
    if data == b"bad":
        raise RuntimeError("no words could be made out")
    return "hello Tensor, it's me"


# --- Telegram -------------------------------------------------------------
telegram_bot._download_telegram_file = (
    lambda token, fid, max_bytes=0, **k: (b"bad" if fid == "BAD" else b"OGG",
                                          None))


def tg_bot(transcribe=hear):
    b = telegram_bot.TelegramBot.__new__(telegram_bot.TelegramBot)
    b.agent_name = "Tensor"
    b.transcribe_voice = transcribe
    b.get_config = lambda: {"bot_token": "T", "allow_from": ["7"]}
    b._pending_lock = threading.Lock()
    b._pending_approvals = {}
    b.sent = []
    b.normal = []
    b._send_chunked = lambda chat_id, text: b.sent.append(text)
    b._handle_normal_message = (
        lambda text, uid, cid, **k: b.normal.append(text))
    b._handle_group_message = lambda *a, **k: b.normal.append("GROUP")
    return b


def voice_update(fid="F1", chat_type="private", caption=None):
    msg = {"message_id": 1, "date": 0, "from": {"id": 7},
           "chat": {"id": 7 if chat_type == "private" else -5,
                    "type": chat_type},
           "voice": {"file_id": fid, "duration": 3, "mime_type": "audio/ogg"}}
    if caption:
        msg["caption"] = caption
    return {"update_id": 1, "message": msg}


b = tg_bot()
b._handle_update(voice_update())
check("Telegram: a voice message is transcribed and reaches the kin as words",
      b.normal == ["[voice message, transcribed] hello Tensor, it's me"])
check("...and you're told what was heard first",
      b.sent and b.sent[0] == "🎙 Heard: hello Tensor, it's me")

b = tg_bot()
b._handle_update(voice_update("BAD"))
check("...a voice message that can't be made out is explained, not dropped",
      b.normal == [] and b.sent and "couldn't make out" in b.sent[-1])

b = tg_bot(transcribe=None)
b._handle_update(voice_update())
check("...with dictation unavailable, you're told to type it",
      b.normal == [] and "Please type it" in b.sent[-1])

b = tg_bot()
heard_calls.clear()
b._handle_update(voice_update(chat_type="group"))
check("...in a group, nothing is transcribed", heard_calls == []
      and b.normal == [])


# --- Discord --------------------------------------------------------------
class _Att:
    def __init__(self, data):
        self.data = data
        self.size = len(data)
        self.content_type = "audio/ogg"
        self.filename = "voice-message.ogg"

    def is_voice_message(self):
        return True

    async def read(self):
        return self.data


class _User:
    def __init__(self, uid=7, bot=False):
        self.id = uid
        self.name = self.display_name = "Wren"
        self.bot = bot


class _Chan:
    id = 99

    def __init__(self):
        self.sent = []

    def typing(self):
        class _T:
            async def __aenter__(s):
                return s

            async def __aexit__(s, *a):
                return False
        return _T()


class _Msg:
    def __init__(self, ch, data=b"OGG", guild=None, content=""):
        self.content = content
        self.author = _User()
        self.channel = ch
        self.mentions = []
        self.attachments = [_Att(data)]
        self.guild = guild


class _Client:
    user = _User(1, bot=True)


def dc_bot(transcribe=hear):
    cfg = {"discord": {"policy": "mention_only", "allow_from": ["*"]},
           "telegram": {"message_wait_secs": 0}}
    d = DiscordBot.__new__(DiscordBot)
    d.agent_name = "Tensor"
    d.transcribe_voice = transcribe
    d._turn_lock = threading.Lock()
    d._active_turns = {}
    d._user_last_reply = {}
    d._channel_locks = {}
    d._gen_semaphore = None
    d._warned_empty_allow = False
    d.on_activity = None
    d.get_busy_label = None
    d.get_config = lambda: cfg
    d.generated = []

    async def gen(message, content, imgs, docs):
        d.generated.append(content)
    d._generate_in_turn = gen

    async def send(channel, text):
        channel.sent.append(text)
    d._send_chunked = send

    async def none(m):
        return []
    d._download_images = none
    d._download_text_documents = none
    return d


def run(d, msg):
    async def go():
        d._loop = asyncio.get_running_loop()
        await d._on_message(_Client(), msg)
    asyncio.run(go())


d, ch = dc_bot(), _Chan()
run(d, _Msg(ch))
check("Discord: a voice message in a direct message reaches the kin as "
      "words, with no @-mention needed",
      d.generated == ["[voice message, transcribed] hello Tensor, it's me"])
check("...and you're told what was heard first",
      ch.sent and ch.sent[0] == "🎙 Heard: hello Tensor, it's me")

d, ch = dc_bot(), _Chan()
run(d, _Msg(ch, b"bad"))
check("...one that can't be made out is explained, not dropped",
      d.generated == [] and "couldn't make out" in ch.sent[-1])

d, ch = dc_bot(), _Chan()
heard_calls.clear()
m = _Msg(ch, guild=object())
m.mentions = [_Client.user]
run(d, m)
check("...in a server channel, nothing is transcribed",
      heard_calls == [] and d.generated == [])

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_voice_messages_remote: all checks passed")
