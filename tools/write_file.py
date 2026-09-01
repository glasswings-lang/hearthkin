# SPDX-License-Identifier: CC0-1.0

"""Write text to a file atomically, creating it if missing."""

from ._io import (atomic_write_text, backup_identity_file, heal_parent_dirs,
                  identity_misplacement_note, path_within_kin,
                  resolve_kin_path)


def write_file(path: str, content: str, agent_name: str = "",
               confine_paths: bool = False) -> str:
    """Write `content` to the file at `path`. Creates the file (and any
    missing parent directories) if it doesn't exist. Overwrites the file
    completely if it does — there is no "append" mode here; use `note`
    for timestamped appends, or `edit_file` for surgical changes.

    Paths: a relative path like `notes.md` or `drafts/poem.txt` lands
    inside your own kin directory (`~/.hearthkin/kin/<your name>/`).
    Absolute paths (`C:\\Users\\...` on Windows, `/home/...` or `~/...`
    on POSIX) go wherever they point. You cannot traverse out of your
    folder with `..` in a relative path — use an absolute path if that's
    really what you want.

    The write is atomic: it goes to a temp file first, then renames over
    the destination. If anything fails partway through, the original
    file is unchanged. Returns a confirmation including the byte count
    so you can verify what actually landed.
    """
    if not isinstance(content, str):
        return "write_file: content must be a string."
    p, err = resolve_kin_path(path, agent_name, confine=confine_paths)
    if err:
        return f"write_file: {err}"
    # Forgiving fallback for the PARENT directory only. If the target folder
    # doesn't exist as typed but a whitespace/case-fuzzy sibling does (the
    # `notes ( drafts, misc)` vs `notes (drafts, misc)` fumble), redirect
    # the write into the real folder — otherwise atomic_write_text would
    # silently create a mis-spelled duplicate directory next to it. The
    # filename itself is never fuzzed: a new-file write must not be
    # redirected onto some pre-existing file with a similar name.
    path_heal_note = ""
    if not p.parent.exists():
        healed = heal_parent_dirs(p)
        if healed is not None:
            # Re-assert containment after healing on a confined surface
            # (audit J1) — a healed parent must not point outside the kin dir.
            if confine_paths and not path_within_kin(healed, agent_name):
                return "write_file: that path resolves outside your kin folder."
            p = healed
            path_heal_note = (
                f" (into existing folder {p.parent}, matched "
                f"ignoring whitespace/case)"
            )
    # Refuse to "overwrite" a directory with a file. The atomic-write helper
    # would rename a temp file over the destination, which on Windows fails
    # with a cryptic WinError 5 ("Access is denied") when the destination is
    # a directory. Catch it up-front with a message that steers the model
    # toward a real file path (typical mistake: passing the kin's folder
    # instead of a filename inside it).
    if p.exists() and p.is_dir():
        return (f"write_file: {p} is a directory, not a file. "
                f"Pass a file path that includes a filename, "
                f"e.g. {p / 'note.md'}.")
    # This tool replaces a file whole, so one call can end a kin's identity
    # with no error and no undo. Copy the soul / memory index aside first.
    backup_note = backup_identity_file(p, agent_name)
    try:
        atomic_write_text(p, content)
    except Exception as e:
        return f"write_file: could not write {p}: {e}"
    return (f"write_file: wrote {len(content.encode('utf-8'))} bytes to "
            f"{p}{path_heal_note}{backup_note}"
            f"{identity_misplacement_note(p, agent_name)}")
