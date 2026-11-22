# SPDX-License-Identifier: CC0-1.0
"""Rooms get what groups and channels have (21 Nov 2026): compaction, and
the two sharing switches, through the same shared rule.

Agreed 20 Nov 2026: a room kept apart gets a compaction point per kin (its
own note, its own "keep" setting); a room can be shared into each kin's own
conversation, with the same two switches as a Telegram group or a Discord
channel. Driven through the real room code with a stand-in window.

Run: python tests/test_rooms_like_groups.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="roomshare-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import kin_persistence as kp  # noqa: E402
from chat_helpers import (PRIVATE, compact_marker, place_of,  # noqa: E402
                          sharing_settings, view_after_compaction,
                          visible_rows)
from frame.rooms_mixin import RoomsMixin  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


for k in ("Wren", "Opal", "Ash"):
    kp.agent_dir(k).mkdir(parents=True, exist_ok=True)
    kp.save_agent_config(k, dict(kp.DEFAULT_AGENT_CONFIG))


class Room(RoomsMixin):
    def __init__(self):
        self.current_room = "Den"
        self.current_agent = None
        self.config = {"user_name": "Sam"}
        self.room_cfg = {"members": ["Wren", "Opal"]}
        self.room_conversation = []
        self.conversation = []
        self.agent_cfg = {}

    def _room_opened_by_kin(self):
        return False


# --- compaction, per kin ----------------------------------------------------
rows = [{"role": "user", "content": "a", "speaker": "You", "ts": "1"},
        {"role": "assistant", "content": "b", "speaker": "Wren", "ts": "2"},
        {"role": "assistant", "content": "c", "speaker": "Opal", "ts": "3"},
        dict(compact_marker("Wren's note", 1, ts="4"), speaker="Wren"),
        {"role": "user", "content": "d", "speaker": "You", "ts": "5"}]
w = view_after_compaction(rows, owner="Wren")
o = view_after_compaction(rows, owner="Opal")
check("a kin that compacted is sent its own note and what follows",
      w[0]["content"] == "Wren's note" and w[0]["speaker"] == "Wren"
      and [r["content"] for r in w[1:]] == ["c", "d"])
check("another kin in the room never sees that note",
      all("note" not in str(r.get("content")) for r in o) and len(o) == 4)
r = Room()
r.room_conversation = rows
hist = r._room_history_for("Wren")
check("the room sends that kin its own note",
      any(h["role"] == "system" and "Wren's note" in h["content"] for h in hist))
check("...and not to the other kin",
      not any("Wren's note" in str(h.get("content"))
              for h in r._room_history_for("Opal")))

# --- the two switches ---------------------------------------------------------
r = Room()
r._room_write_sharing("Den", ["Wren", "Opal"], True, True)
wcfg = kp.load_agent_config("Wren")
check("the room's switches are written into each member's config",
      ("room:Den" in sharing_settings(wcfg)[0])
      and ("room:Den" in sharing_settings(wcfg)[1]))
check("...and not into a kin that isn't a member",
      "room:Den" not in sharing_settings(kp.load_agent_config("Ash"))[0])
check("a room's rows belong to the room as a place",
      place_of({"source": "room:Den"}) == "room:Den")

# share out
person = {"role": "user", "content": "hello all", "ts": "2026-11-21T07:00:00",
          "speaker": "You", "fronting": "Juniper"}
r._room_share_out(person)
r._room_share_out({"role": "assistant", "content": "hi Sam",
                   "ts": "2026-11-21T07:00:05", "speaker": "Wren"})
wc = kp.load_agent_conversation("Wren")
oc = kp.load_agent_conversation("Opal")
check("the person's message reaches each member's own conversation",
      any(x.get("content") == "hello all" and x.get("source") == "room:Den"
          and x.get("sender_name") == "Sam" and x.get("fronting") == "Juniper"
          for x in wc))
check("a kin's own room reply is its own in its conversation",
      any(x.get("role") == "assistant" and x.get("content") == "hi Sam"
          for x in wc))
check("...and another kin's is that kin's, as someone speaking",
      any(x.get("role") == "user" and x.get("content") == "hi Sam"
          and x.get("sender_name") == "Wren" for x in oc))
priv = visible_rows(wc, PRIVATE, kp.load_agent_config("Wren"))
check("the kin's private side sees the room, marked as said there",
      any('(said in the room "Den")' in str(x.get("content")) for x in priv))

r._room_write_sharing("Den", ["Wren", "Opal"], False, False)
before = len(kp.load_agent_conversation("Wren"))
r._room_share_out(dict(person, content="secret"))
check("switched off, nothing more goes out",
      len(kp.load_agent_conversation("Wren")) == before)
check("...and what went before is hidden from the private side again",
      not any("said in the room" in str(x.get("content")) for x in visible_rows(
          kp.load_agent_conversation("Wren"), PRIVATE,
          kp.load_agent_config("Wren"))))

# take in
kp.append_agent_conversation_turn("Wren", {
    "role": "user", "content": "just between us", "ts": "2026-11-21T06:00:00"})
r._room_write_sharing("Den", ["Wren", "Opal"], False, True)
r.room_conversation = [person]
hist = r._room_history_for("Wren")
check("a room that gets the private side shows the kin its private chat, "
      "marked", any("just between us" in str(h.get("content"))
                    and "said in your private chats" in str(h.get("content"))
                    for h in hist))
check("...in time order, before the later room message",
      [i for i, h in enumerate(hist) if "just between us" in str(h.get("content"))][0]
      < [i for i, h in enumerate(hist) if "hello all" in str(h.get("content"))][0])
check("...and only to that kin",
      not any("just between us" in str(h.get("content"))
              for h in r._room_history_for("Opal")))

if _fails:
    print(f"{len(_fails)} FAILED: " + "; ".join(_fails))
    sys.exit(1)
print("test_rooms_like_groups: all checks passed")
