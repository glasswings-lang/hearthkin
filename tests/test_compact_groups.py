# SPDX-License-Identifier: CC0-1.0
"""/compact in Telegram groups and Discord channels, and now.md kept to the
main conversation (27 Nov 2026).

The person: "I'd expect any AI program worth its salt to be able to send a
quick /compact and basically get what we've been getting, conversation
merged or not." Groups and channels answered "direct messages only" and were
only ever cut by the history cap. And anyone's /compact wrote the kin's
now.md, so a friend's direct-message note replaced where the kin was in its
own main conversation.

Pinned:
  * a group can be compacted by the kin's person, or someone whose tool
    access can write, and no one else;
  * the note goes into that group's own history, and the group is then read
    from the note on, as a direct message is;
  * a group kept with the desktop is refused with nothing changed;
  * a group's note, and a direct message not shared with the desktop, never
    write now.md; a shared direct message still does;
  * Discord: a channel's /compact goes the same way.

Run: python tests/test_compact_groups.py
"""

import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="compactgroups-")
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


KIN = "Sorrel"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)
now_md = kin_dir(KIN) / "now.md"
GROUP = -100123


def bot(*, me=False, bucket="read", group_shares=False, dm_shares=False):
    b = tb.TelegramBot.__new__(tb.TelegramBot)
    b.agent_name = KIN
    b.sent = []
    b._send_chunked = lambda chat_id, text: b.sent.append(text)
    b._user_is_me = lambda uid: me
    b._user_tool_bucket = lambda uid: bucket
    b._group_shares_desktop = lambda cid: group_shares
    b._user_shares_desktop = lambda uid: dm_shares
    b.get_config = lambda: {"groups": {str(GROUP): {"label": "Den"}}}
    b._histories = {}
    b._histories_lock = threading.RLock()
    b._save_histories = lambda: None
    b.COMPACT_KEEP = 2
    return b


rows = [{"role": "user", "content": f"msg {i}"} if i % 2 == 0 else
        {"role": "assistant", "content": f"reply {i}"} for i in range(10)]

# Who may.
b = bot(bucket="read")
b._cmd_compact_group(7, GROUP, "")
check("someone with read access can't compact a group",
      b.sent and "Only Sorrel's person" in b.sent[-1])
b = bot(bucket="write")
b._compact_note = lambda uid, user, history=None: "Group note."
b._histories[f"group:{GROUP}"] = list(rows)
b._cmd_compact_group(7, GROUP, "")
check("someone whose tool access can write may: the note is shown first",
      "Here's the note Sorrel wrote for this group" in b.sent[-1]
      and "Group note." in b.sent[-1])

# Confirm.
if now_md.exists():
    now_md.unlink()
b._cmd_compact_group(7, GROUP, "confirm")
check("/compact confirm in a group says it compacted, and that nothing was deleted",
      "Compacted. From now on, in this group" in b.sent[-1]
      and "Nothing was deleted" in b.sent[-1])
stored = b._histories[f"group:{GROUP}"]
check("...the note is saved into the group's own history, nothing removed",
      len(stored) == len(rows) + 1 and stored[:len(rows)] == rows)
seen = b._load_group_history(GROUP)
check("...and the group is read from the note on",
      len(seen) < len(stored)
      and any("Group note." in str(m.get("content", "")) for m in seen)
      and not any(m.get("content") == "msg 0" for m in seen))
check("...and the group's note did not become now.md", not now_md.exists())

# The kin's person, and a refused save.
b = bot(me=True)
b.__dict__["_pending_compact"] = {("group", str(GROUP)): "Note."}
b._try_save_histories = lambda: False
b._cmd_compact_group(1, GROUP, "confirm")
check("a group compaction that can't be saved says so and keeps the note",
      "Couldn't compact" in b.sent[-1]
      and b.__dict__["_pending_compact"].get(("group", str(GROUP))) == "Note."
      and not b._histories.get(f"group:{GROUP}"))

# A group kept with the desktop.
b = bot(me=True, group_shares=True)
b._cmd_compact_group(1, GROUP, "")
check("a group kept with the desktop is refused, nothing changed",
      "kept in the desktop's conversation" in b.sent[-1]
      and "Nothing was changed" in b.sent[-1])

# Routing and help.
src = (ROOT / "telegram_bot.py").read_text(encoding="utf-8")
check("a group's /compact reaches the group handler, not 'direct messages only'",
      'if cmd == "compact" and not is_private:\n            self._cmd_compact_group(' in src)
check("the group /help lists /compact", "/compact — {self.agent_name} writes itself a note for this" in src
      or "writes itself a note for this \"\n                \"group" in src)

# now.md from direct messages.
for shares, label in ((False, "a direct message not shared with the desktop"),
                      (True, "a direct message shared with the desktop")):
    if now_md.exists():
        now_md.unlink()
    b = bot(me=not shares, dm_shares=shares)
    b._append_turns_for = lambda uid, turns: True
    b._compact_user_name = lambda uid: "you"
    b.__dict__["_pending_compact"] = {5: "DM note."}
    b.COMPACT_KEEP = 6
    b._cmd_compact(5, 5, "confirm")
    wrote = now_md.exists() and "DM note." in now_md.read_text(encoding="utf-8")
    check(f"{label} {'writes' if shares else 'does not write'} now.md",
          wrote == shares)

# Discord.
dsrc = (ROOT / "discord_bot.py").read_text(encoding="utf-8")
check("Discord: a channel's /compact is let through for the person or write access",
      'if name == "compact" and not in_dm:' in dsrc
      and 'bucket in ("write", "full")' in dsrc
      and "await self._cmd_compact(message, arg, False, in_channel=True)" in dsrc)
check("...a channel kept with the desktop is refused",
      "/compact can't compact that from a channel yet" in dsrc)
check("...and only a shared direct message writes now.md",
      "            if share:\n                from kin_persistence import save_now_note" in dsrc)

print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("test_compact_groups: all checks passed")
