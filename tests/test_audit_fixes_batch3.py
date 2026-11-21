# SPDX-License-Identifier: CC0-1.0
"""Third batch of fixes from the 20 Nov 2026 surface audit.

  * Telegram /regen re-runs a turn with no message; the document check
    called msg.get on None every time (caught, logged, and file sharing
    skipped for the regenerated turn).
  * "*" (anyone) in Telegram's tool access and webcam settings is honoured,
    as the person window and Discord already did.
  * Sharing with the desktop is never taken from "*", on either app: it
    would merge any stranger's messages into the person's private chats.
  * Discord, having posted "[no reply produced]", tells the kin a
    placeholder went out, not that nothing was posted.
  * A Telegram group voice message whose caption @mentions the kin counts
    as spoken to.

Run: python tests/test_audit_fixes_batch3.py
"""

import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="auditfix3-", dir=(os.environ.get("HEARTHKIN_HOME") or None))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import telegram_bot as _tb  # noqa: E402
from tools._person_settings import person_value  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def bot(conf):
    b = _tb.TelegramBot.__new__(_tb.TelegramBot)
    b.agent_name = "Tensor"
    b.get_config = lambda: conf
    b._bot_user_id = 99
    b._bot_username = "kinbot"
    b._pending_lock = threading.Lock()
    b._pending_approvals = {}
    return b


b = bot({})
check("/regen's missing message no longer breaks the document check",
      b._text_document_of(None) is None)

b = bot({"user_tools": {"*": "read", "7": "full"}})
check("'anyone' tool access applies to someone not listed",
      b._user_tool_bucket(8) == "read")
check("...and a person's own setting still wins", b._user_tool_bucket(7) == "full")
check("no entry at all is still no tools", bot({})._user_tool_bucket(8) == "none")
src = (ROOT / "telegram_bot.py").read_text(encoding="utf-8")
check("'anyone' webcam rule is read on Telegram",
      'perm_map.get("*")' in src)

for surface in ("telegram", "discord"):
    sec = {"user_share_desktop": {"*": True}}
    check(f"{surface}: 'anyone' never merges strangers into your private chats",
          person_value(sec, surface, "8", "share_desktop") is False)
check("...a person's own sharing box still works",
      person_value({"user_share_desktop": {"8": True}}, "telegram", "8",
                   "share_desktop") is True)

dsrc = (ROOT / "discord_bot.py").read_text(encoding="utf-8")
check("Discord's empty-reply note says a placeholder went out",
      'load_app_prompt("empty_reply_note",' in dsrc
      and 'load_app_prompt("empty_reply_note_group",' not in dsrc)

# A group voice message with an @mention in its caption.
heard = []
b = bot({"bot_token": "T", "allow_from": ["7"],
         "groups": {"-5": {"label": "G", "policy": "mention_only"}}})
b.transcribe_voice = lambda data: heard.append(1) or "hello"
b.sent, b.normal = [], []
b._send_chunked = lambda cid, t: b.sent.append(t)
b._handle_group_message = lambda msg, *a, **k: b.normal.append(msg.get("text"))
b._handle_normal_message = lambda *a, **k: None
_tb._download_telegram_file = lambda token, fid, max_bytes=0, **k: (b"OGG", None)
cap = "@kinbot listen"
upd = {"update_id": 1, "message": {
    "message_id": 1, "date": 0, "from": {"id": 7},
    "chat": {"id": -5, "type": "group"},
    "voice": {"file_id": "V", "duration": 2, "mime_type": "audio/ogg"},
    "caption": cap,
    "caption_entities": [{"type": "mention", "offset": 0, "length": 7}]}}
b._handle_update(upd)
check("a group voice message @mentioning the kin in its caption is heard",
      heard == [1] and b.normal and "hello" in b.normal[0])

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_audit_fixes_batch3: all checks passed")
