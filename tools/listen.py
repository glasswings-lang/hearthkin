# SPDX-License-Identifier: CC0-1.0

"""Hear an audio file through the kin's chosen listening model."""

from ._io import find_existing_path, resolve_kin_path


def listen(
    path: str,
    question: str = "",
    start: str = "",
    length: float = 0,
    agent_name: str = "",
    confine_paths: bool = False,
) -> str:
    """Hear a sound or piece of music: a listening model hears the audio file and tells you what it heard, so you can meet the sound itself instead of only its numbers. Ask it something specific in `question` when you want to know something particular ("is there a voice?", "what changes at the end?"); leave it blank for a general description.

    A long piece isn't sent whole: you hear a few stretches from across it, each labelled with where it starts. To hear one part closely, give `start` (seconds, or minutes:seconds like "31:00") and optionally `length` in seconds (up to 240).

    What comes back is the listener's impression, honest but not exact. For exact pitches, tuning and pulse, use analyze_sound alongside it. Paths follow the same rules as read_file.
    """
    p, err = resolve_kin_path(path, agent_name, confine=confine_paths)
    if err:
        return f"listen: {err}"
    if not p.exists():
        healed = find_existing_path(p)
        if healed is None:
            return f"listen: no file at {p}"
        p = healed
    if not p.is_file():
        return f"listen: {p} is not a regular file."
    try:
        import senses
        return senses.hear(p, question, start or None, length or None,
                           agent_name=agent_name)
    except Exception as e:
        return f"listen: could not hear {p}: {e}"
