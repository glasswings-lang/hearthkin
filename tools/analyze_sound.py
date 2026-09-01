# SPDX-License-Identifier: CC0-1.0

"""Analyze an audio file and return its acoustic facts (no model, pure DSP)."""

from ._io import find_existing_path, resolve_kin_path


def analyze_sound(
    path: str,
    start: float = 0.0,
    end: float = 0.0,
    skip_edges: float = 0.0,
    agent_name: str = "",
) -> str:
    """Listen to the measurable facts of an audio file: its tonal centre and pitch set (with tuning in cents — so you can see non-standard tunings like A=432), any detuning/beating between close tones, brightness, loudness, the slow swell/pulse rate, stereo width, and how it evolves across its length. This is the reliable, factual half of hearing a sound — accurate where an ear's *impression* of pitch would not be. Use it when someone shares a piece of music or a sound with you and you want to actually meet it, not just take their word for what it is.

    Paths follow the same rules as read_file: a relative path resolves inside
    your own kin directory; an absolute path (e.g. `D:\\Music\\track.wav` on
    Windows, or `~/Music/track.wav`) reads from wherever it points.

    Region selection (seconds): pass `start` and `end` to analyze only that
    window, or `skip_edges` to trim that many seconds off BOTH ends — handy for
    ignoring a fade-in/fade-out so it doesn't skew the loudness reading. Leave
    them at 0 to analyze the whole piece (a window from the middle is sampled for
    the detailed readout, so fades at the very ends are already avoided).

    Returns a readable readout, or a brief error message on a missing/unreadable
    file. The facts are objective; the *feeling* of the sound is yours to bring.
    """
    try:
        from audio_spectrum import analyze_audio, format_sound_card
    except Exception as e:  # numpy / soundfile not installed
        return (
            f"analyze_sound: audio support is unavailable in this build "
            f"({e}). It needs the numpy and soundfile packages installed.")

    p, err = resolve_kin_path(path, agent_name)
    if err:
        return f"analyze_sound: {err}"
    if not p.exists():
        healed = find_existing_path(p)
        if healed is None:
            return f"analyze_sound: no file at {p}"
        p = healed
    if not p.is_file():
        return f"analyze_sound: {p} is not a regular file."

    try:
        facts = analyze_audio(
            str(p),
            start=start or None,
            end=end or None,
            skip_edges=skip_edges or None,
        )
        return format_sound_card(facts)
    except Exception as e:
        return f"analyze_sound: could not analyze {p}: {e}"
