# SPDX-License-Identifier: CC0-1.0

"""Move or rename a file or a folder.

Added 26 Nov 2026. Kin reorganising their own logs (splitting merged logs,
archiving superseded ones) were being given `exec` just to move files, and
exec is a whole shell: it reads staging notes around every per-reply limit,
and runs anything. This does one thing.

Same rules as the other file tools: relative paths stay inside the kin's
folder; absolute paths reach outside only where the surface allows it
(`confine_paths`, set for Telegram, Discord and, unless the person turned
it off, wake-ups and heartbeats). It sits in the "write" bucket.

It never overwrites anything, and it won't move the files that hold a kin
together or the staging notes (which have their own tools and limits).
"""

import shutil
from pathlib import Path

from ._io import path_within_kin, resolve_kin_path

# Files a kin's continuity depends on, by name, at the top of its folder.
_KEEP_IN_PLACE = {
    "soul.md", "memory.md", "identity.md", "now.md", "practice.md",
    "config.json", "tools.json", "conversation.jsonl",
}
# Folders a kin must not move wholesale or move things into or out of.
_KEEP_FOLDERS = {"staging"}


def _kin_root(agent_name):
    try:
        from hearthkin_paths import kin_dir
        return kin_dir(agent_name).resolve()
    except Exception:
        return None


def _protected(p, root):
    """Why `p` can't be moved, or None."""
    if root is None:
        return None
    try:
        p = p.resolve()
    except OSError:
        return None
    if p == root or root.is_relative_to(p):
        return "that's your whole folder, or a folder containing it."
    if p.parent == root and p.name.lower() in _KEEP_IN_PLACE:
        return (f"{p.name} holds part of who you are or how Hearthkin runs "
                f"you, so it stays where it is. Edit it instead if it needs "
                f"changing.")
    try:
        rel = p.relative_to(root)
    except ValueError:
        return None
    if rel.parts and rel.parts[0].lower() in _KEEP_FOLDERS:
        return ("staging notes are moved only by archive_staging, which "
                "files away just what you've read. Use read_staging and "
                "archive_staging.")
    return None


def move_file(source: str, destination: str, agent_name: str = "",
              confine_paths: bool = False) -> str:
    """Move or rename a file or a folder. Use it to reorganise your own
    files: rename a log, split work into a new folder, put superseded logs
    in an archive folder.

    `source` is what to move. `destination` is where it goes: a new name
    (`memory/walks.md`), or an existing folder to move it into
    (`archive/old-logs`, and it keeps its name). Missing folders on the way
    are created.

    Paths: a relative path like `memory/old.md` is inside your own kin
    folder. Absolute paths go where they point, where this surface allows
    it.

    It never overwrites: if something is already at the destination, the
    move is refused and nothing changes. It won't move your soul,
    memory.md, identity.md, now.md, practice.md, your config, tool list or
    conversation, or anything in staging/ (use archive_staging for those
    notes). Moving a log doesn't change its contents, so you don't need to
    read it first.
    """
    if not agent_name:
        return "move_file: no kin context (framework bug)."
    src, err = resolve_kin_path(source, agent_name, confine=confine_paths)
    if err:
        return f"move_file: {err}"
    dst, err = resolve_kin_path(destination, agent_name, confine=confine_paths)
    if err:
        return f"move_file: {err}"
    if not src.exists():
        return (f"move_file: there's nothing at {src}. Check the name with "
                f"list_directory.")
    root = _kin_root(agent_name)
    if dst.exists() and dst.is_dir():
        dst = dst / src.name
    for p, which in ((src, "move"), (dst, "move it there")):
        why = _protected(p, root)
        if why:
            return f"move_file: can't {which}: {why}"
    if confine_paths and not (path_within_kin(src, agent_name)
                              and path_within_kin(dst, agent_name)):
        return "move_file: both places have to be inside your kin folder here."
    try:
        if src.resolve() == dst.resolve():
            return "move_file: that's already where it is."
        if src.is_dir() and dst.resolve().is_relative_to(src.resolve()):
            return "move_file: a folder can't be moved inside itself."
    except OSError:
        pass
    if dst.exists():
        return (f"move_file: {dst} already exists, so nothing was moved. "
                f"Pick another name, or look at what's there first.")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    except Exception as e:
        return (f"move_file: the move failed ({e}). Check both places with "
                f"list_directory before trying again; a folder may be "
                f"partly moved.")
    if not dst.exists():
        return f"move_file: the move didn't land at {dst}."
    kind = "folder" if dst.is_dir() else "file"
    return f"move_file: moved the {kind} {src} to {dst}."
