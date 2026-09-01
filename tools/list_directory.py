# SPDX-License-Identifier: CC0-1.0

"""List the files and subfolders inside a directory."""

import os

from ._io import find_existing_path, path_within_kin, resolve_kin_path

# Hard cap on how many entries a single call returns, recursive or not — a
# huge folder (or a recursive walk into one) must degrade to "here's the
# first N, there's more" rather than either hanging or blowing the model's
# context with a thousand-line wall. Mirrors read_file's _MAX_BYTES: a
# generous default with an explicit truncation marker, never a silent cut.
_MAX_ENTRIES = 500

# How many folder levels a recursive listing descends by default. A real
# Dropbox/Documents tree can run many levels deep with large sibling
# folders at each one; an unbounded walk risks the entry cap being spent
# entirely on the first subfolder it happens to visit, before any of the
# ones the model actually asked about are shown.
_DEFAULT_MAX_DEPTH = 3


def list_directory(
    path: str = ".",
    recursive: bool = False,
    max_depth: int = 0,
    agent_name: str = "",
    confine_paths: bool = False,
) -> str:
    """List the files and subfolders inside a directory. Use this to see
    what's in a folder before trying to read_file a specific one — don't
    guess a filename, or shell out to list a directory, when this exists.

    Paths follow the same rule as read_file: a relative path like
    `memory` or `notes/drafts` is read from inside your own kin
    directory. Absolute paths (`C:\\Users\\...` on Windows, `/home/...`
    or `~/...` on POSIX) read from wherever they point. Pass "." (the
    default) for your own kin folder's top level.

    `recursive=False` (the default) shows only the immediate contents of
    `path` — one level. `recursive=True` walks subfolders too, up to
    `max_depth` levels deep (default 3; pass a larger number for a
    deeper walk, or 0 to use the default). Subfolders are marked with a
    trailing `/`; files are listed with their size in bytes.

    Entries are capped at 500 per call — a folder with more than that
    (especially recursively) returns the first 500 with a note that more
    exist, rather than either hanging or flooding your context. Narrow
    `path` to a specific subfolder if you hit the cap.

    Returns a brief error message instead of raising on a missing or
    non-directory path, so you get actionable feedback.
    """
    p, err = resolve_kin_path(path, agent_name, confine=confine_paths)
    if err:
        return f"list_directory: {err}"
    heal_note = ""
    if not p.exists():
        healed = find_existing_path(p)
        if healed is None:
            return f"list_directory: no folder at {p}"
        if confine_paths and not path_within_kin(healed, agent_name):
            return "list_directory: that path resolves outside your kin folder."
        p = healed
        heal_note = (
            f"\n[list_directory: note — the path you gave didn't exist "
            f"exactly; matched {p} (whitespace/case differences ignored). "
            f"Use that exact path next time.]"
        )
    if not p.is_dir():
        return f"list_directory: {p} is a file, not a folder — use read_file for it."

    depth = max_depth if max_depth and max_depth > 0 else _DEFAULT_MAX_DEPTH
    lines = []
    truncated = False
    total_seen = 0

    def _walk(dir_path, rel_prefix, level):
        nonlocal truncated, total_seen
        if truncated:
            return
        try:
            entries = sorted(os.scandir(dir_path), key=lambda e: e.name.casefold())
        except OSError as e:
            lines.append(f"{rel_prefix}[could not list: {e}]")
            return
        for entry in entries:
            total_seen += 1
            if total_seen > _MAX_ENTRIES:
                truncated = True
                return
            rel_name = rel_prefix + entry.name
            if entry.is_dir(follow_symlinks=False):
                lines.append(f"{rel_name}/")
                if recursive and level < depth:
                    _walk(entry.path, rel_name + "/", level + 1)
            else:
                try:
                    size = entry.stat(follow_symlinks=False).st_size
                except OSError:
                    size = None
                size_str = f" ({size:,} bytes)" if size is not None else ""
                lines.append(f"{rel_name}{size_str}")

    _walk(p, "", 0)

    if not lines:
        return f"list_directory: {p} is empty." + heal_note

    header = f"list_directory: {p}" + (" (recursive)" if recursive else "")
    footer = ""
    if truncated:
        footer = (
            f"\n\n[list_directory: showing the first {_MAX_ENTRIES:,} "
            f"entries; there are more. Narrow the path to a specific "
            f"subfolder to see the rest.]"
        )
    return header + "\n" + "\n".join(lines) + footer + heal_note
