# SPDX-License-Identifier: CC0-1.0
"""A park move is a park block, and '>' is just a quote again (23 Nov 2026).

A park move used to be a line starting with '> '. That's also how anyone
quotes something in writing: a kin quoting a line it loved, or describing an
action it took ("> write memory/the-will.md"), sent it to the park, and with
the game closed the person got "the park isn't reachable". The hint itself
("put what you want to DO on a '> ' line") taught one kin to write a tool call
that way. Now a move goes in a fenced block labelled park, one per line, like
the to: and manual blocks. Pinned:

  * a park block's lines are the moves, in order; the last block wins;
  * a reply that only quotes with '>' makes no move;
  * the old '> ' line still works while the park prompts teach it (a saved
    copy of the old wording), so nothing breaks before the person adopts the
    new wording, and stops the moment they do;
  * route_reply and play_turn, which every surface uses, run block moves;
  * results are labelled "park: <move>", never as a '> ' line a kin would copy;
  * every park prompt teaches the block and none teaches the old line.

Run: python tests/test_park_block.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="parkblock-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import kin_persistence as kp  # noqa: E402
import park_keeper as pk  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


T = "`" * 3


def fresh():
    pk._taught_cache["at"] = 0.0


fresh()
check("a new install doesn't teach the '> ' line", pk.old_marker_taught() is False)

cmds, _ = pk.extract_command_run(f"Off I go.\n\n{T}park\npet Luna\ncare for the pond\n{T}\n")
check("a park block's lines are the moves, in order", cmds == ["pet Luna", "care for the pond"])
cmds, _ = pk.extract_command_run(f"{T}park\nlook\n{T}\nno, better:\n{T}park\nbreed Glade 4\n{T}")
check("the last block wins", cmds == ["breed Glade 4"])
check("a quote with '>' makes no move",
      pk.extract_command_run("A line I love:\n> I'd rather stop existing")[0] == []
      and pk.extract_command("> write memory/the-will.md") == "")
check("an ordinary code block isn't a park block",
      pk.extract_command_run(f"{T}python\nprint(1)\n{T}")[0] == [])

# Saved copies of the old wording: the old line still works.
prompts = Path(os.environ["HEARTHKIN_HOME"]) / "prompts"
prompts.mkdir(parents=True, exist_ok=True)
old_hint = ("--- Your park ---\nput that one action on its own FINAL line, "
            "starting with '> ', after whatever you've said.\n> pet Luna\n")
(prompts / "park_chat_hint.md").write_text(old_hint, encoding="utf-8")
fresh()
check("with the old wording saved, the old line still runs",
      pk.old_marker_taught() and pk.extract_command("> pet Luna") == "pet Luna")
check("...and the block works then too",
      pk.extract_command_run(f"{T}park\npet Luna\n{T}")[0] == ["pet Luna"])
(prompts / "park_chat_hint.md").write_text(kp.DEFAULT_PARK_CHAT_HINT, encoding="utf-8")
fresh()
check("adopting the new wording turns '>' back into a plain quote",
      pk.old_marker_taught() is False and pk.extract_command("> pet Luna") == "")

# What every surface calls.
ran = []


def run(cmd, say=""):
    ran.append(cmd)
    return f"done: {cmd}"


cmd, res = pk.route_reply(f"Here.\n{T}park\nadopt rabbit\n{T}", run)
check("route_reply runs a block move", ran == ["adopt rabbit"] and "done: adopt rabbit" in res)
ran.clear()
cmd, res = pk.route_reply("I'm just quoting:\n> adopt rabbit", run)
check("...and runs nothing for a quote", ran == [])

# play_turn takes a run_move that returns the result text; drive it plainly.
moves = []


def run_move(text):
    c = pk.extract_command(text)
    if not c:
        return None
    moves.append(c)
    return f"done: {c}"


replies = iter([f"{T}park\ncare for Glade\n{T}", "that's everyone."])
pk.play_turn("k", f"{T}park\nlook\n{T}", run_move=run_move,
             ask=lambda *a, **k: next(replies), max_moves=4, hard_stop=60)
check("play_turn takes block moves across a whole turn", moves[:2] == ["look", "care for Glade"])

# Labels the kin reads back.
src = (ROOT / "park_keeper.py").read_text(encoding="utf-8") \
    + (ROOT / "hearthkin_cron.py").read_text(encoding="utf-8") \
    + (ROOT / "tools" / "_game_host.py").read_text(encoding="utf-8")
check("no result is labelled as a '> ' line any more", '"> %s' not in src)

# The prompts teach the block.
for name, text in (("park_chat_hint", kp.DEFAULT_PARK_CHAT_HINT),
                   ("park_chat_hint_shared", kp.DEFAULT_PARK_CHAT_HINT_SHARED),
                   ("park_mechanism", pk.MECHANISM),
                   ("park_turn_instruction", pk.TURN_INSTRUCTION)):
    check(f"{name} teaches the park block",
          "park block" in text and not any(m in text for m in pk._OLD_TEACHING))

print()
if _fails:
    print(f"test_park_block: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_park_block: all checks passed")
