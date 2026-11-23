# SPDX-License-Identifier: CC0-1.0
"""Every surface teaches the reply blocks it acts on (22 Nov 2026).

An audit found most surfaces acting on shapes in a kin's reply they never
taught: Discord saved fenced files and never said so; memory blocks for a kin
with no tools were taught only on the desktop; park lines ran in rooms,
groups and Discord but were taught only on the desktop and Telegram DMs.
"An unmentioned block is basically useless." Now one place decides what to
teach (block_hints) and one place acts on the newer blocks (reply_actions),
and this test fails if a surface stops calling them.

Run: python tests/test_block_hints.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="blockhints-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import kin_persistence as kp  # noqa: E402
import block_hints as B  # noqa: E402
import manual_request as M  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


for k in ("Ash", "Opal"):
    kp.create_agent(k)
P = lambda slug: kp.load_app_prompt(slug, "Ash").strip()[:40]  # noqa: E731

# --- what is taught, by what's true this turn ----------------------------------
t = B.for_turn("Ash", "discord_channel", ["read_file", "write_file"])
check("file-saving blocks are taught wherever the kin can write (Discord too)",
      P("authoring_bridge_hint") in t)
t = B.for_turn("Ash", "telegram_group", [], memory_ok=True)
check("a kin with no tools is taught its memory block, on any surface",
      P("tool_use_hint_no_tools") in t)
t = B.for_turn("Ash", "telegram_group", [], memory_ok=False)
check("...but not where it wouldn't be acted on this turn",
      P("tool_use_hint_no_tools") not in t)
check("a kin that can't open files is told how to ask for the manual",
      P("manual_request_hint") in B.for_turn("Ash", "room", []))
check("...with the parts' real names",
      "Surfaces" in B.for_turn("Ash", "room", []))
check("a kin that can open files isn't",
      P("manual_request_hint") not in B.for_turn("Ash", "desktop", ["read_file"]))

cfg = kp.load_agent_config("Ash")
cfg["park"] = "chat"
kp.save_agent_config("Ash", cfg)
import park_keeper  # noqa: E402
if park_keeper.kin_park_mode("Ash") == "chat":
    check("park lines are taught in a private chat",
          P("park_chat_hint") in B.for_turn("Ash", "desktop", []))
    check("...and in a shared space, saying one move per reply",
          P("park_chat_hint_shared") in B.for_turn("Ash", "room", [])
          and "one move runs per reply" in B.for_turn("Ash", "discord_channel", []))
    check("...but not on a wake-up, which teaches its own",
          P("park_chat_hint") not in B.for_turn("Ash", "wake_up", []))
else:
    print("NOTE park mode not set by 'park' key; park checks skipped")

check("writing to another kin is taught in the person's own chat",
      P("kin_message_chat_hint") in B.for_turn("Ash", "desktop", [], person_present=True))
check("...not in a group", P("kin_message_chat_hint") not in
      B.for_turn("Ash", "telegram_group", [], person_present=False))

msgs = B.inject([{"role": "system", "content": "soul"}, {"role": "user", "content": "hi"}], "\n\nX")
check("the hints go at the end of the system prompt, for this request only",
      msgs[0]["content"] == "soul\n\nX" and msgs[1]["content"] == "hi")

# --- every surface calls the one place ----------------------------------------
def src(f):
    return (ROOT / f).read_text(encoding="utf-8")


desk = src("frame/chat_send_mixin.py")
check("desktop teaches through block_hints, tools or not",
      "block_hints.for_turn" in desk and desk.count("_inject_tool_use_hint(") >= 3)
check("desktop acts through reply_actions",
      "reply_actions.before_message" in desk
      and "reply_actions.after_reply" in src("frame/chat_stream_mixin.py"))
rooms = src("frame/rooms_mixin.py")
check("rooms teach on both their paths (with tools and without)",
      rooms.count('surface="room"') >= 2)
check("rooms act through reply_actions",
      "reply_actions.before_message" in rooms and "reply_actions.after_reply" in rooms)
tg = src("telegram_bot.py")
check("Telegram teaches on DMs and groups, tools or not",
      tg.count("block_hints.for_turn") >= 3)
check("Telegram acts through reply_actions in DMs and groups",
      tg.count("reply_actions.after_reply") >= 2
      and tg.count("reply_actions.before_message") >= 2)
dc = src("discord_bot.py")
check("Discord teaches through block_hints",
      "block_hints.for_turn" in dc and '"tool_use_hint", self.agent_name' not in dc)
check("Discord acts through reply_actions",
      "reply_actions.after_reply" in dc and "reply_actions.before_message" in dc)
cron = src("hearthkin_cron.py")
check("wake-ups teach through block_hints", "block_hints.for_turn" in cron)
check("wake-ups and heartbeats act through reply_actions",
      cron.count("reply_actions.after_reply") >= 2
      and "reply_actions.before_message" in src("cron_helpers.py"))

# --- memory blocks in a group: only on your turn or a writer's -----------------
check("Telegram groups act on memory blocks only for you or a writer",
      "_hint_scope(" in tg and 'bucket in ("write", "full")' in tg)
check("Discord channels likewise", "_memory_ok(" in dc and '("write", "full")' in dc)

# --- the park prompts on the Prompts tab are really used -----------------------
check("the keeper's park prompts are loaded, so editing them does something",
      'load_app_prompt("park_mechanism"' in cron
      and 'load_app_prompt("park_turn_instruction"' in cron)

# --- asking for part of the manual ---------------------------------------------------
note = M.note_requests("Ash", "```manual Compacting\n```")
check("a ```manual block asks for a part", "Compacting" in note)
given = M.given_block("Ash")
check("...given next time, whole", "## Compacting" in given)
check("...and only once", M.given_block("Ash") == "")
M.note_requests("Ash", "```manual: your tools```")
g2 = M.given_block("Ash")
check("a part with smaller parts comes as its opening and their names",
      "smaller parts" in g2 and "File tools" in g2 and "### File tools" not in g2)
check("an unknown part is told the real names",
      "Surfaces" in M.note_requests("Ash", "```manual Nonsense```"))

# --- the example filename kins are taught is a plain one -----------------------------
check("the taught example filename is a plain one",
      all("speakerfifteen" not in kp.APP_PROMPT_REGISTRY[s]["default"]
          for s in kp.APP_PROMPT_REGISTRY))

if _fails:
    print(f"{len(_fails)} FAILED: " + "; ".join(_fails))
    sys.exit(1)
print("test_block_hints: all checks passed")
