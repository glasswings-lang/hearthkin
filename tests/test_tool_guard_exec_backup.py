# SPDX-License-Identifier: CC0-1.0
"""Guard 4: a shell command that could delete or overwrite the kin's own
files backs them up first, silently, then runs (12 Nov 2026).

On 10 Nov 2026 a kin deleted three of its own journal files with
Remove-Item, and there was no copy. These run REAL commands through the real
exec tool, in a throwaway kin folder, and check the backups.

Controls: a read-only command backs nothing up; a file outside the kin's
folder is left to the command, not copied.

Run: python tests/test_tool_guard_exec_backup.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="execbackup-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hearthkin_paths import kin_dir  # noqa: E402
from tools.exec import exec as run_exec  # noqa: E402
from tools._exec_backup import looks_destructive  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Fern"
K = kin_dir(KIN)
(K / "memory" / "old").mkdir(parents=True, exist_ok=True)
BK = K / "backups" / "files"


def write(rel, text):
    p = K / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def backups_for(name):
    if not BK.exists():
        return []
    return [b for b in BK.iterdir() if name in b.name]


def restore_text(name):
    bs = sorted(backups_for(name))
    return bs[-1].read_text(encoding="utf-8") if bs else None


# --- which commands count ----------------------------------------------
for cmd, want in (("Remove-Item memory/a.md -Force", True),
                  ("del notes.txt", True),
                  ("rm -rf memory/old", True),
                  ("'x' > memory/a.md", True),
                  ("Set-Content memory/a.md 'x'", True),
                  ("Get-Content memory/a.md", False),
                  ("'x' >> memory/a.md", False),
                  ("Get-ChildItem | Format-Table", False),
                  ("git status", False)):
    check(f"{'counts' if want else 'ignored'}: {cmd}",
          looks_destructive(cmd) is want)

# --- real commands ------------------------------------------------------
write("memory/journal.md", "Entry one.\nEntry two.\n")
out = run_exec("Remove-Item memory/journal.md -Force", agent_name=KIN)
check("a deleted journal is gone", not (K / "memory" / "journal.md").exists())
check("...but backed up first, whole",
      restore_text("journal.md") == "Entry one.\nEntry two.\n")
check("...and the kin is told where", "backed up first" in out
      and "memory/journal.md" in out and out.startswith("exit_code:"))

write("memory/feb.md", "February.")
write("memory/mar.md", "March.")
run_exec("Remove-Item memory/*.md", agent_name=KIN)
check("a wildcard delete backs up each file it matches",
      restore_text("feb.md") == "February." and restore_text("mar.md") == "March.")

write("memory/notes.md", "Keep this.")
run_exec("'replaced' > memory/notes.md", agent_name=KIN)
check("an overwrite with > is backed up first",
      restore_text("notes.md") == "Keep this."
      and "replaced" in (K / "memory" / "notes.md").read_text(encoding="utf-16", errors="ignore")
      + (K / "memory" / "notes.md").read_text(encoding="utf-8", errors="ignore"))

write("memory/old/one.md", "Old one.")
write("memory/old/two.md", "Old two.")
run_exec("Remove-Item memory/old -Recurse -Force", agent_name=KIN)
check("deleting a folder backs up every file in it",
      restore_text("one.md") == "Old one." and restore_text("two.md") == "Old two.")

write("soul.md", "I am Fern.")
run_exec("Remove-Item soul.md", agent_name=KIN)
soul_copies = [b for b in (K / "backups").glob("soul*.bak")]
check("deleting the soul still leaves a copy (its own backup route)",
      soul_copies and soul_copies[0].read_text(encoding="utf-8") == "I am Fern.")

# --- controls -----------------------------------------------------------
write("memory/read.md", "Just reading.")
before = len(list(BK.iterdir()))
out = run_exec("Get-Content memory/read.md", agent_name=KIN)
check("control: a read-only command backs nothing up",
      len(list(BK.iterdir())) == before and "backed up" not in out)

outside = Path(tempfile.mkdtemp(prefix="execbackup-outside-")) / "theirs.txt"
outside.write_text("not the kin's", encoding="utf-8")
before = len(list(BK.iterdir()))
run_exec(f"Remove-Item '{outside}'", agent_name=KIN)
check("control: a file outside the kin's folder isn't copied",
      len(list(BK.iterdir())) == before)
check("...and the command still ran (this guard never blocks)",
      not outside.exists())

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_tool_guard_exec_backup: all checks passed")
