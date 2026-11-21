# SPDX-License-Identifier: CC0-1.0
"""Fifth batch of fixes from the 20 Nov 2026 surface audit.

  * Telegram /stop with nothing running says so, as /cancel does (it was
    "Unknown command").
  * Someone muted in a Telegram group gets no answer to commands there
    either (they used to).
  * A Telegram group ID of just "-" is refused.
  * Discord's own history file is written atomically, like every other.
  * The Discord status box shows the bot's state when settings open.

Run: python tests/test_audit_fixes_batch5.py
"""

import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="auditfix5-", dir=(os.environ.get("HEARTHKIN_HOME") or None))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import telegram_bot as _tb  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def bot(conf):
    b = _tb.TelegramBot.__new__(_tb.TelegramBot)
    b.__init__("Tensor", get_config=lambda: conf, get_soul=lambda: "",
               get_memory=lambda: "",
               get_model_options=lambda: ("m", {}),
               on_status=lambda *a: None)
    b.sent = []
    b._send_chunked = lambda cid, t: b.sent.append(t)
    b._bot_user_id = 99
    b._bot_username = "kinbot"
    return b


b = bot({"allow_from": ["7"]})
b._handle_command("/stop", 7, 7, chat_type="private")
check("/stop with nothing running says there's nothing to stop",
      b.sent and "Unknown command" not in b.sent[-1]
      and "othing to cancel" in b.sent[-1])

b = bot({"allow_from": ["7"],
         "groups": {"-5": {"label": "G", "policy": "always", "exclude": [9]}}})
upd = {"update_id": 1, "message": {"message_id": 1, "date": 0,
       "from": {"id": 9}, "chat": {"id": -5, "type": "group"},
       "text": "/status"}}
b._handle_update(upd)
check("a muted person's command in the group gets no answer", b.sent == [])
upd["message"]["from"]["id"] = 8
b._handle_update(upd)
check("...someone not muted still gets one", len(b.sent) == 1)

src = (ROOT / "dialogs" / "edit_kin.py").read_text(encoding="utf-8")
check("a group ID of just '-' is refused when adding and editing",
      src.count('.lstrip("-"):') >= 2)

from kin_persistence import append_discord_turn, load_discord_history  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402
kin_dir("Tensor").mkdir(parents=True, exist_ok=True)
append_discord_turn("Tensor", "555", {"role": "user", "content": "hi"})
check("Discord's history file saves and reads back",
      load_discord_history("Tensor").get("555", [{}])[0].get("content") == "hi")
ksrc = (ROOT / "kin_persistence.py").read_text(encoding="utf-8")
seg = ksrc[ksrc.index("def append_discord_turn"):]
seg = seg[:seg.index("\ndef ")]
check("...written atomically", "atomic_write_text(path" in seg
      and "path.write_text(" not in seg)

from discord_bot import DiscordBot  # noqa: E402
d = DiscordBot.__new__(DiscordBot)
d.on_status = None
check("a Discord bot that hasn't started reports Off", d.status_label() == "Off")
d._status("Discord connected as Kin#1.")
check("...and then its last status", d.status_label() == "Discord connected as Kin#1.")

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_audit_fixes_batch5: all checks passed")
