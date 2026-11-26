# SPDX-License-Identifier: CC0-1.0
"""move_file: kin reorganise their own files without needing exec.

26 Nov 2026. A kin splitting its merged logs was given exec just to move
files, and exec is a whole shell: it could read staging notes around the
per-reply limit. The person: "Tired of giving it access to exec... just so
it can move shit around in its own folder. That tool also needs the same
permission gating as the others."

Run: python tests/test_move_file.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="movefile-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import kin_persistence as kp  # noqa: E402
import tools  # noqa: E402
from tools._buckets import BUCKETS  # noqa: E402
from tools.move_file import move_file  # noqa: E402

KIN = "Fern"
home = kp.agent_dir(KIN)
(home / "memory").mkdir(parents=True, exist_ok=True)
(home / "memory" / "mixed.md").write_text("two threads\n", encoding="utf-8")
(home / "memory" / "keep.md").write_text("one\n", encoding="utf-8")
(home / "soul.md").write_text("me\n", encoding="utf-8")
(home / "memory.md").write_text("index\n", encoding="utf-8")
(home / "conversation.jsonl").write_text("{}\n", encoding="utf-8")
kp.staging_file_path(KIN, "desktop").parent.mkdir(parents=True, exist_ok=True)
kp.staging_file_path(KIN, "desktop").write_text("## a\n", encoding="utf-8")

r = move_file("memory/mixed.md", "memory/walks.md", agent_name=KIN)
check("renames a file inside the kin folder",
      (home / "memory" / "walks.md").exists()
      and not (home / "memory" / "mixed.md").exists() and "moved" in r)

r = move_file("memory/walks.md", "archive/superseded", agent_name=KIN)
check("moves into a new folder path, creating it",
      (home / "archive" / "superseded").exists())
(home / "archive" / "superseded").rename(home / "archive" / "superseded.md")
(home / "old").mkdir()
r = move_file("archive/superseded.md", "old", agent_name=KIN)
check("moving onto an existing folder puts it inside, keeping its name",
      (home / "old" / "superseded.md").exists())

r = move_file("old", "archive/old-logs", agent_name=KIN)
check("moves whole folders", (home / "archive" / "old-logs" / "superseded.md").exists())

r = move_file("memory/keep.md", "archive/old-logs/superseded.md", agent_name=KIN)
check("never overwrites: refused when something is already there",
      "already exists" in r and (home / "memory" / "keep.md").exists()
      and (home / "archive" / "old-logs" / "superseded.md").read_text(
          encoding="utf-8") == "two threads\n")

for name in ("soul.md", "memory.md", "conversation.jsonl"):
    r = move_file(name, f"archive/{name}", agent_name=KIN)
    check(f"won't move {name}", "can't move" in r and (home / name).exists())

r = move_file("staging/desktop.md", "archive/notes.md", agent_name=KIN)
check("won't move staging notes (they'd skip the reading limits)",
      "archive_staging" in r and kp.staging_file_path(KIN, "desktop").exists())
r = move_file("memory/keep.md", "staging/keep.md", agent_name=KIN)
check("...or put things into staging", "can't move it there" in r)
r = move_file("staging", "elsewhere", agent_name=KIN)
check("...or move the staging folder", "can't move" in r)

r = move_file("archive", "archive/inner", agent_name=KIN)
check("a folder can't go inside itself", "inside itself" in r)
r = move_file("nothing-here.md", "x.md", agent_name=KIN)
check("a missing source says so", "nothing at" in r)

outside = Path(tempfile.mkdtemp(prefix="outside-")) / "x.md"
outside.write_text("host file\n", encoding="utf-8")
r = move_file(str(outside), "memory/stolen.md", agent_name=KIN, confine_paths=True)
check("on a confined surface (Telegram, Discord, wake-ups), absolute paths "
      "are refused", outside.exists() and not (home / "memory" / "stolen.md").exists())
r = move_file("memory/keep.md", str(outside.parent / "y.md"), agent_name=KIN,
              confine_paths=True)
check("...both ways", (home / "memory" / "keep.md").exists())
r = move_file("../escape.md", "x.md", agent_name=KIN)
check("relative paths can't climb out of the folder", "moved" not in r)

check("it's in the write tier, like write_file and edit_file",
      "move_file" in BUCKETS["write"] and "move_file" not in BUCKETS["read"])
schemas, ex = tools.load_tools(["move_file"], context={
    "agent_name": KIN, "confine_paths": True})
props = schemas[0]["function"]["parameters"]["properties"]
check("the kin can't see or set the confinement",
      "confine_paths" not in props and "agent_name" not in props
      and set(props) == {"source", "destination"})
r = ex["move_file"]({"source": "memory/keep.md",
                     "destination": str(outside.parent / "z.md")})
check("...and a confined surface's tool really is confined",
      (home / "memory" / "keep.md").exists())

print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("test_move_file: all checks passed")
