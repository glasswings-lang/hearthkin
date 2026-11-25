# SPDX-License-Identifier: CC0-1.0
"""Message audit, second batch (24 Nov 2026): say it worked only when it did.

Pinned:

  * clearing a room keeps a copy in the room's backups/cleared first;
  * a prompt whose old copy can't be backed up is not overwritten, and
    adopting it reports failure instead of "backed up your old copy";
  * a kin's own prompt backup carries the kin's name;
  * Telegram /undo whose save is refused says nothing changed;
  * Telegram /compact confirm whose marker can't be saved says so and keeps
    the note waiting;
  * Discord's history append can be told to raise, so its /compact can tell.

Run: python tests/test_says_only_what_saved_2.py
"""

import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="saved2-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import kin_persistence as kp  # noqa: E402
import telegram_bot as tb  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# Room copy.
kp.save_room_conversation("den", [{"role": "user", "content": "hello room"}])
copy = kp.keep_room_copy("den")
check("clearing a room keeps a copy first", copy is not None and copy.exists()
      and "hello room" in copy.read_text(encoding="utf-8")
      and copy.parent.name == "cleared")

# Prompt backup refuses.
slug = "staging_status_empty"
kp.save_app_prompt(slug, "my own wording")
real = kp.shutil.copy2
kp.shutil.copy2 = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
try:
    try:
        kp.save_app_prompt(slug, "replaced")
        refused = False
    except OSError:
        refused = True
    adopted = kp.adopt_prompt_update(slug)
finally:
    kp.shutil.copy2 = real
check("a prompt whose old copy can't be backed up is not overwritten",
      refused and kp._app_prompt_path(slug).read_text(encoding="utf-8") == "my own wording")
check("...and adopting reports failure", adopted is False)

kin_dir("Sorrel").mkdir(parents=True, exist_ok=True)
kp.save_kin_app_prompt("Sorrel", slug, "first")
kp.save_kin_app_prompt("Sorrel", slug, "second")
names = [p.name for p in (kp.PROMPTS_DIR / "backups").glob("*.bak")]
check("a kin's own prompt backup carries the kin's name",
      any(n.startswith("Sorrel__") for n in names))


# Telegram undo and compact.
def bot(shares):
    b = tb.TelegramBot.__new__(tb.TelegramBot)
    b.agent_name = "Sorrel"
    b.sent = []
    b._send_chunked = lambda chat_id, text: b.sent.append(text)
    b._user_shares_desktop = lambda uid: shares
    b._last_thinking = {}
    b._histories = {}
    b._histories_lock = threading.RLock()
    b._save_histories = lambda: None
    return b


b = bot(True)
real_save = kp.save_agent_conversation
kp.save_agent_conversation = lambda *a, **k: (_ for _ in ()).throw(OSError("no copy"))
try:
    ok = b._truncate_history_for(1, 0)
finally:
    kp.save_agent_conversation = real_save
check("Telegram's history cut reports a refused save", ok is False)
src = (ROOT / "telegram_bot.py").read_text(encoding="utf-8")
check("...and /undo and /regen say nothing changed",
      "Couldn't undo: the conversation couldn't be saved" in src
      and "Couldn't regenerate: the conversation couldn't be saved" in src)

b = bot(True)
b._append_turns_for = lambda uid, turns: False
b._compact_user_name = lambda uid: "you"
b.__dict__["_pending_compact"] = {5: "My note."}
b.COMPACT_KEEP = 6
b._cmd_compact(5, 5, "confirm")
check("/compact confirm whose marker can't be saved says so",
      b.sent and "Couldn't compact" in b.sent[-1])
check("...and keeps the note waiting", b.__dict__["_pending_compact"].get(5) == "My note.")


# Third batch (24 Nov 2026, the accuracy audit): private chats.
def failing_save():
    raise OSError("disk full")


b = bot(False)
b._cmd_clear(9, 9)
check("private /clear with nothing there says so, and claims no copy",
      b.sent and "nothing to clear" in b.sent[-1]
      and "copy" not in b.sent[-1])

b = bot(False)
b._histories[tb._hkey(9)] = [{"role": "user", "content": "hi"},
                             {"role": "assistant", "content": "hello"}]
b._archive_telegram_history = lambda uid, h: Path("telegram-9-copy.jsonl")
b._save_histories = failing_save
b._cmd_clear(9, 9)
check("private /clear whose save fails says it will come back after a restart",
      b.sent and "couldn't be saved to disk" in b.sent[-1])
check("...and no longer says the kin won't remember anything",
      "won't remember anything" not in b.sent[-1])

b = bot(False)
rows = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
b._histories[tb._hkey(9)] = list(rows)
b._save_histories = failing_save
ok = b._truncate_history_for(9, 0)
check("a private /undo whose save fails reports it",
      ok is False)
check("...and puts the history back, so 'nothing changed' is true",
      b._histories[tb._hkey(9)] == rows)

# /help in a group says whether the kin talks there at all.
b = bot(False)
b.get_config = lambda: {"groups": {"-100": {}}}
b._cmd_help(-100, is_private=False)
check("group /help in an added group says to just write",
      "just write in the group" in b.sent[-1] and "/front" in b.sent[-1])
b._cmd_help(-200, is_private=False)
check("group /help in a group it isn't added to says it won't answer",
      "isn't set up to talk in this group" in b.sent[-1]
      and "just write" not in b.sent[-1])

# /tools lists what a real turn would have.
b = bot(False)
b.get_config = lambda: {}
b._user_tool_bucket = lambda uid: "safe"
b._user_is_me = lambda uid: True
b._park_users = set()
real_load = kp.load_kin_tools
kp.load_kin_tools = lambda name: ["read_file"]
try:
    b._cmd_tools(9, 9)
finally:
    kp.load_kin_tools = real_load
check("/tools for 'This is me' includes message_kin, as a real turn does",
      "message_kin" in b.sent[-1])

# The webcam window names the surface the request came from.
_bi = (ROOT / "frame" / "bot_integration_mixin.py").read_text(encoding="utf-8")
_wa = (ROOT / "dialogs" / "webcam_approval.py").read_text(encoding="utf-8")
check("a Discord webcam request is shown as Discord, not Telegram",
      'surface="Discord"' in _bi and "{surface} user {requester_id}" in _wa
      and "Settings, Telegram, Users" not in _wa)

import inspect  # noqa: E402
check("Discord's history append can be told to raise",
      "raise_on_failure" in inspect.signature(kp.append_discord_turn).parameters)

print()
if _fails:
    print(f"test_says_only_what_saved_2: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_says_only_what_saved_2: all checks passed")
