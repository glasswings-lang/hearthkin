# SPDX-License-Identifier: CC0-1.0

"""Timestamped append to a file in the kin's directory.

Lower cognitive load than `edit_file` or `write_file` when the model
just wants to jot something down — no need to read the existing file
first or worry about overwriting anything. The append is unconditional
and the timestamp is always present, so the model can't accidentally
clobber prior notes."""

import datetime
from pathlib import Path

from hearthkin_paths import kin_dir as kin_folder

from ._io import atomic_write_text


def note(content: str, file: str = "memory.md", agent_name: str = "") -> str:
    """Append a timestamped line to one of your own files. By default
    the file is `memory.md`; pass a different name (e.g. `scratch.md`
    or `daily_2026-05-11.md`) to write somewhere else. The path is
    always relative to your own kin directory — you cannot use this to
    write outside it.

    Each note is prefixed with an ISO timestamp on its own line, then
    your content, then a blank line, so the file stays readable as it
    grows. Returns a confirmation including the path written. Use this
    when you want to record something without disturbing the file's
    existing contents.
    """
    if not content:
        return "note: content was empty."
    if not agent_name:
        return "note: no kin context (framework bug)."

    # `file` must be a relative path inside the kin directory. Reject
    # traversal attempts — the resolved-path containment check below
    # is the real guard, but we catch the obvious cases early.
    if not file or file in (".", ".."):
        return (
            f"note: {file!r} is not a usable filename. Pass a "
            f"filename or relative path in your kin directory "
            f"(e.g. 'memory.md', 'memory/2026-05-12.md')."
        )

    kin_dir = kin_folder(agent_name).resolve()
    if not kin_dir.exists():
        return f"note: no kin directory for {agent_name!r}."

    # Belt and suspenders: even after the filename validation above, do
    # the resolved-path containment check too in case the filesystem has
    # symlink games we didn't anticipate.
    target = (kin_dir / file).resolve()
    try:
        target.relative_to(kin_dir)
    except ValueError:
        return f"note: {file!r} resolves outside the kin directory."

    # soul.md is the kin's identity prompt — appending to it would change
    # the kin's system prompt on the next turn. Edits to it go through
    # the operator (or, deliberately, via write_file/edit_file when those
    # higher-tier tools are enabled).
    if target == kin_dir / "soul.md":
        return (
            "note: soul.md is the kin's identity prompt — edits to it "
            "go through the operator. Pick a different file "
            "(e.g. memory.md or memory/<topic>.md)."
        )

    # Ensure parent directories exist
    target.parent.mkdir(parents=True, exist_ok=True)

    ts = datetime.datetime.now().isoformat(timespec="seconds")
    existing = ""
    if target.exists():
        try:
            existing = target.read_text(encoding="utf-8")
        except Exception as e:
            return f"note: could not read existing {target}: {e}"
        if existing and not existing.endswith("\n"):
            existing += "\n"

    addition = f"\n## {ts}\n\n{content.strip()}\n"
    try:
        atomic_write_text(target, existing + addition)
    except Exception as e:
        return f"note: could not write {target}: {e}"
    return f"note: appended {len(addition)} chars to {target}"
