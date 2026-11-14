# SPDX-License-Identifier: CC0-1.0

"""See a picture through the kin's chosen looking model."""

from ._io import find_existing_path, resolve_kin_path


def look(
    path: str,
    question: str = "",
    agent_name: str = "",
    confine_paths: bool = False,
) -> str:
    """Look at a picture file (JPEG, PNG, GIF or WebP): a model that can see describes it to you, reading any text exactly. Ask something specific in `question` when you want one thing ("what does the sign say?", "is anyone smiling?"); leave it blank for a full description. What comes back is that model's description, honest but not your own eyes. Paths follow the same rules as read_file.
    """
    p, err = resolve_kin_path(path, agent_name, confine=confine_paths)
    if err:
        return f"look: {err}"
    if not p.exists():
        healed = find_existing_path(p)
        if healed is None:
            return f"look: no file at {p}"
        p = healed
    if not p.is_file():
        return f"look: {p} is not a regular file."
    try:
        import senses
        return senses.see(p, question, agent_name=agent_name)
    except Exception as e:
        return f"look: could not see {p}: {e}"
