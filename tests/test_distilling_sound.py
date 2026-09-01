# SPDX-License-Identifier: CC0-1.0
"""Guard test: a plain distillation gets its own distinct, audible cue.

Reported live: "Distill selected surface now" and "Distill all surfaces
now" gave no way to tell by ear that a distillation specifically was
running — they only ever got the same generic send/working/done ticks
_tick_work_sounds already plays for a chat reply, a cron wake-up, or a
heartbeat. All identical, so "is a distillation actually happening" had
no answer, on top of these calls sometimes running 20-40 minutes with
gemma4:31b under contention.

The cue also PACES itself by what the call has actually produced: flat
and low while the model is still reading its bite, then rising as the
summary streams back. A flat "still alive" beep repeated for forty
minutes is barely distinguishable from silence; a rising one answers the
question anybody actually has, which is whether it's getting anywhere.

It runs during a redistill walk too. A walk's own cue (_chime_progress)
fires once per CHUNK, in a higher register; this one fires inside a
chunk. Standing it down for walks meant each chunk was 20-40 minutes of
complete silence.

Run: python tests/test_distilling_sound.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from frame.status_voice_mixin import StatusVoiceMixin  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


class Frame:
    _chime_setting = StatusVoiceMixin._chime_setting
    _tick_distilling_sound = StatusVoiceMixin._tick_distilling_sound
    _distilling_progress_chars = StatusVoiceMixin._distilling_progress_chars
    _distilling_freq = StatusVoiceMixin._distilling_freq
    _DISTILLING_TONE = StatusVoiceMixin._DISTILLING_TONE
    _DISTILLING_WRITE_STEPS = StatusVoiceMixin._DISTILLING_WRITE_STEPS
    _DISTILLING_FULL_CHARS = StatusVoiceMixin._DISTILLING_FULL_CHARS
    _CHIME_TONES = StatusVoiceMixin._CHIME_TONES

    def __init__(self, config=None, distilling=None, walking=None,
                 progress=None):
        self.config = config if config is not None else {"reply_chime": True}
        self._distilling = dict(distilling or {})
        self._walking_from_start = dict(walking or {})
        self._distill_progress = dict(progress or {})


import frame.status_voice_mixin as _mod  # noqa: E402

_played = []


def _fake_play_chime(freq, dur, volume=0.8, name=None):
    _played.append((freq, dur, name))


_real_play_chime = _mod.play_chime
_mod.play_chime = _fake_play_chime

_real_monotonic = _mod.time.monotonic
_fake_now = [0.0]
_mod.time.monotonic = lambda: _fake_now[0]


def reset():
    _played.clear()
    # A real time.monotonic() never starts at exactly 0.0 relative to a
    # process's own uptime -- start the fake clock well clear of the
    # default "never played" sentinel (_distilling_sound_last defaults to
    # 0.0 via getattr) so "nothing has ever played" reads as "a long time
    # ago", the way it always does for real, not as a coincidental tie.
    _fake_now[0] = 1000.0


# --- the tone itself is genuinely distinct from every existing cue ------

existing = set(StatusVoiceMixin._CHIME_TONES.values())
check("the distilling tone doesn't collide with any existing cue's tone",
      StatusVoiceMixin._DISTILLING_TONE not in existing)
check("...and is a different FREQUENCY from every existing one specifically",
      StatusVoiceMixin._DISTILLING_TONE[0]
      not in {f for f, _d in existing})


# --- nothing distilling: silence, and the "last played" clock resets ----

reset()
f = Frame(distilling={})
f._tick_distilling_sound()
check("no distillation running: no sound", _played == [])


# --- a plain distillation gets the distinct tone ------------------------

reset()
f = Frame(distilling={"Lark": 1234.0})
f._tick_distilling_sound()
check("a plain distillation plays the distinct cue",
      len(_played) == 1 and _played[0][2] == "distilling")
check("...at the distinct (lower) frequency, not any existing cue's",
      _played[0][0] == StatusVoiceMixin._DISTILLING_TONE[0])


# --- it paces itself rather than sounding on every 5s tick --------------

reset()
f = Frame(distilling={"Lark": 1234.0})
f._tick_distilling_sound()
_fake_now[0] = 1005.0   # one ordinary tick later, well under the 20s default
f._tick_distilling_sound()
check("a tick before the interval elapses plays nothing further",
      len(_played) == 1)
_fake_now[0] = 1021.0  # past the default 20s
f._tick_distilling_sound()
check("...but once the interval elapses, it sounds again",
      len(_played) == 2)


# --- a WALK still gets this cue, inside each chunk ----------------------

reset()
f = Frame(distilling={"Lark": 1234.0},
         walking={("Lark", "desktop"): True})
f._tick_distilling_sound()
check("a walk chunk is NOT silent — _chime_progress fires once per chunk, "
      "which leaves 20-40 minutes inside one with nothing at all",
      len(_played) == 1 and _played[0][2] == "distilling")
check("...and stays below the walk's own per-chunk cue register, so the "
      "two are never confused",
      _played[0][0] < StatusVoiceMixin._CHUNK_TONE_LOW)


# --- the pitch reports how much summary has actually been written -------

reset()
f = Frame(distilling={"Lark": 1234.0}, progress={"Lark": 0})
f._tick_distilling_sound()
check("nothing written yet (still reading the bite): the flat low tone",
      _played[0][0] == StatusVoiceMixin._DISTILLING_TONE[0])

reset()
f = Frame(distilling={"Lark": 1234.0}, progress={"Lark": 40})
f._tick_distilling_sound()
check("the first words of the summary lift it off the reading tone, so "
      "'it started writing' is audible on its own",
      _played[0][0] > StatusVoiceMixin._DISTILLING_TONE[0])

reset()
f = Frame(distilling={"Lark": 1234.0}, progress={"Lark": 40})
f._tick_distilling_sound()
early = _played[0][0]
f._distill_progress["Lark"] = 2000
_fake_now[0] += 25.0
f._tick_distilling_sound()
mid = _played[1][0]
f._distill_progress["Lark"] = 3900
_fake_now[0] += 25.0
f._tick_distilling_sound()
late = _played[2][0]
check("the pitch RISES as the summary accumulates — rising means getting "
      "somewhere, the same note twice means stuck",
      early < mid < late)
check("...and never climbs into the reply cues' register, so a "
      "distillation is never mistaken for a kin answering",
      late < StatusVoiceMixin._CHIME_TONES["send"][0])
check("...and no rung lands on an existing cue's exact frequency",
      not (set(StatusVoiceMixin._DISTILLING_WRITE_STEPS)
           & {f for f, _d in StatusVoiceMixin._CHIME_TONES.values()}))

# The rungs are far enough apart to be heard as different notes 20
# seconds apart. Below about 3 semitones (a ratio of ~1.19) a listener
# comparing across that gap is being asked to do something people can't
# do, and the cue silently degrades into the flat beep it replaced.
_steps = StatusVoiceMixin._DISTILLING_WRITE_STEPS
check("the rungs are spaced widely enough to actually tell apart across "
      "a 20-second gap",
      all(b / float(a) >= 1.15 for a, b in zip(_steps, _steps[1:])))
check("...and the first rung is that far clear of the reading tone too, "
      "so 'it has started writing' is heard, not inferred",
      _steps[0] / float(StatusVoiceMixin._DISTILLING_TONE[0]) >= 1.15)

reset()
f = Frame(distilling={"Lark": 1234.0},
         progress={"Lark": StatusVoiceMixin._DISTILLING_FULL_CHARS * 9})
f._tick_distilling_sound()
check("an unusually long run holds the top note rather than running off "
      "the top of the range",
      _played[0][0] == StatusVoiceMixin._DISTILLING_WRITE_STEPS[-1])

reset()
f = Frame(distilling={"Lark": 1234.0})   # slot held, no progress recorded
f._tick_distilling_sound()
check("a run with no progress recorded at all still sounds — a missing "
      "counter must not silence the cue",
      len(_played) == 1
      and _played[0][0] == StatusVoiceMixin._DISTILLING_TONE[0])


# --- respects the same on/off + volume plumbing as every other cue -----

reset()
f = Frame(config={"reply_chime": False}, distilling={"Lark": 1234.0})
f._tick_distilling_sound()
check("reply_chime off (the master switch) silences this cue too, "
      "same as every other cue here",
      _played == [])

reset()
f = Frame(config={"reply_chime": True, "chime_volume": 0.0},
         distilling={"Lark": 1234.0})
f._tick_distilling_sound()
check("volume zero silences it too", _played == [])

reset()
f = Frame(config={"reply_chime": True, "chime_distilling_secs": 0},
         distilling={"Lark": 1234.0})
f._tick_distilling_sound()
check("chime_distilling_secs=0 turns the repeat off entirely", _played == [])


# --- clean rebound: distillation ending resets the pacing clock --------

reset()
f = Frame(distilling={"Lark": 1234.0})
f._tick_distilling_sound()
check("first tick sounds immediately", len(_played) == 1)
f._distilling = {}
f._tick_distilling_sound()
check("once nothing is distilling, it goes quiet", len(_played) == 1)
f._distilling = {"Lark": 5678.0}
_fake_now[0] = 1000.5  # a NEW distillation, moments later -- should sound
                       # again immediately, not wait out the old interval
f._tick_distilling_sound()
check("a fresh distillation starting sounds again right away, rather "
      "than waiting out whatever was left of the previous interval",
      len(_played) == 2)


_mod.play_chime = _real_play_chime
_mod.time.monotonic = _real_monotonic

if _fails:
    print(f"\n{len(_fails)} FAILED: " + ", ".join(_fails))
    sys.exit(1)
print("\nall distilling-sound checks passed")
