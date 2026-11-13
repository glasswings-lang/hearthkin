# SPDX-License-Identifier: CC0-1.0
"""Guard 4: back up a kin's own files before a shell command that could
delete or overwrite them (12 Nov 2026).

On 10 Nov 2026 a kin deleted three of its own journal files with
`Remove-Item ... -Force` after a misunderstanding. The exec denylist only
catches wipe-the-drive shapes, by design, so nothing stood in the way, and
there was no copy. The operator's decision: back up silently, then let the
command run (no approval question). So:

  - only commands containing a deleting/overwriting verb are looked at
    (Remove-Item, del, rm, Clear-Content, Set-Content, Out-File, Move-Item,
    Copy-Item, Rename-Item, a single `>` redirect, and their short forms);
  - every word in the command that names an existing file or folder INSIDE
    the kin's own folder (wildcards included) is backed up first, through
    the same rolling backup write_file uses (newest 5 per file kept);
  - anything outside the kin's folder is left alone; ordinary cleanup
    elsewhere isn't this guard's business.

Over-matching is harmless (an extra copy); missing a file is the failure, so
the word scan is deliberately generous. Never raises and never blocks: a
failed backup must not stop the command.
"""

import glob
import re
from pathlib import Path

_VERBS = (
    r"remove-item|ri|rm|rmdir|rd|del|erase|clear-content|clc|set-content|sc|"
    r"out-file|move-item|mi|mv|move|copy-item|cpi|cp|copy|rename-item|rni|ren|"
    r"truncate|new-item"
)
_DESTRUCTIVE = re.compile(
    rf"(?i)(?:^|[\s;|&(]){'(?:' + _VERBS + ')'}(?=$|[\s;|&)])"
    r"|(?<![>\d])>(?!>)")
_WORD = re.compile(r'"[^"]*"|\'[^\']*\'|[^\s;|&()<>,]+')
MAX_FILES = 200


def looks_destructive(command):
    return bool(command and _DESTRUCTIVE.search(command))


def _inside(p, root):
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _candidates(command, cwd_path, kin_dir):
    """Existing files inside the kin folder that the command names."""
    found = []
    backups = (kin_dir / "backups").resolve()
    for raw in _WORD.findall(command or ""):
        w = raw.strip().strip('"').strip("'")
        if not w or w.startswith("-") or "$" in w or w.lower() in _VERBS.split("|"):
            continue
        try:
            base = Path(w).expanduser()
            target = base if base.is_absolute() else Path(cwd_path) / base
        except (OSError, ValueError):
            continue
        matches = ([Path(m) for m in glob.glob(str(target))]
                   if any(c in w for c in "*?[") else [target])
        for m in matches:
            try:
                if not m.exists() or not _inside(m, kin_dir):
                    continue
                items = [m] if m.is_file() else [f for f in m.rglob("*")
                                                   if f.is_file()]
            except OSError:
                continue
            for f in items:
                if _inside(f, backups):
                    continue
                if f not in found:
                    found.append(f)
                if len(found) >= MAX_FILES:
                    return found
    return found


def backup_before_exec(command, cwd_path, kin_dir, agent_name):
    """Back up the kin's own files this command could delete or overwrite.
    Returns the list of paths (relative to the kin folder) that were
    copied; empty when the command isn't destructive or names none."""
    if not looks_destructive(command):
        return []
    try:
        from tools._io import (backup_file_before_change,
                               backup_identity_file,
                               is_canonical_identity_file)
    except Exception:
        return []
    done = []
    try:
        for f in _candidates(command, Path(cwd_path), Path(kin_dir)):
            try:
                # The soul and memory index have their own backup; the
                # general one skips them, so a command deleting the soul
                # must take that route or it gets no copy at all.
                saved = (backup_identity_file(f, agent_name)
                         if is_canonical_identity_file(f, agent_name)
                         else backup_file_before_change(f, agent_name))
                if saved:
                    done.append(str(f.resolve().relative_to(
                        Path(kin_dir).resolve())).replace("\\", "/"))
            except Exception:
                continue
    except Exception:
        pass
    return done
