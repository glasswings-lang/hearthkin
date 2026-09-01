# SPDX-License-Identifier: CC0-1.0
"""Guard test: sound cues fire for every kin, not just the one on screen.

Sound is load-bearing here rather than decorative. For someone who can't see
the window, a model call is otherwise silent — a prefill can run four minutes
producing nothing, and there is no way to tell "thinking" from "died". It has
to be sound rather than speech, because a screen reader running character echo
has no free moment to announce anything, and it can't be a Windows toast,
because those land in the same pile as Telegram and Signal and are lost.

Before this, `_chime` was wired by hand into desktop chat and rooms only. A
Telegram reply, a scheduled wake-up and a heartbeat made no sound at all —
exactly the cases where nobody is looking at the window and sound is the only
channel left. Reported as "only the kin in focus makes any chimes, which is
fucking annoying", which is a fair description of a feature that works
precisely when you don't need it.

The fix watches machine state on the existing timer instead of hooking each
surface, because hand-wiring surfaces is what caused the gap — the same
mistake that shipped the confirm-on-close dialog blind to two kinds of work.
State-watching covers surfaces that don't exist yet.

Pinned here:
  * a cue fires when work STARTS anywhere and when it FINISHES anywhere;
  * the repeating "still working" cue paces itself and can be switched off;
  * a direct call from the desktop path primes the detector so the tick
    doesn't sound the same transition twice;
  * "waiting for your approval" is NOT working — the machine is idle, and
    ticking at someone about their own unanswered prompt is nagging;
  * per-cue switches and volumes are honoured, and an install that has never
    seen them behaves exactly as it did on the old single switch.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from frame.status_voice_mixin import StatusVoiceMixin  # noqa: E402
from frame.lifecycle_mixin import LifecycleMixin  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


class Frame:
    _chime_setting = StatusVoiceMixin._chime_setting
    _tick_work_sounds = StatusVoiceMixin._tick_work_sounds
    _CHIME_TONES = StatusVoiceMixin._CHIME_TONES
    _CHUNK_TONE_LOW = StatusVoiceMixin._CHUNK_TONE_LOW
    _CHUNK_TONE_HIGH = StatusVoiceMixin._CHUNK_TONE_HIGH
    _machine_busy = LifecycleMixin._machine_busy

    def __init__(self, config=None, busy=()):
        self.config = config if config is not None else {"reply_chime": True}
        self._busy = list(busy)
        self.played = []

    def _work_in_flight(self):
        return list(self._busy)

    def _chime(self, stage):
        # Record instead of playing; keep the real priming behaviour so the
        # double-sound guard is genuinely under test.
        if stage in ("send", "done"):
            import time as _t
            self._work_sound_busy = (stage == "send")
            self._work_sound_last_tick = _t.monotonic()
        on, vol = self._chime_setting(stage)
        if on and vol > 0:
            self.played.append(stage)


# --- start and finish, from any surface ---------------------------------

f = Frame()
f._tick_work_sounds()
check("idle machine makes no sound", f.played == [])

f = Frame(busy=["Vesper is answering its scheduled wake-up from 12:00"])
f._tick_work_sounds()
check("a CRON wake-up sounds the start cue (was silent before)",
      f.played == ["send"])
f._busy = []
f._tick_work_sounds()
check("...and the finish cue when it ends", f.played == ["send", "done"])

f = Frame(busy=["Opal is part-way through a reply to SpeakerSeven on Telegram"])
f._tick_work_sounds()
f._busy = []
f._tick_work_sounds()
check("a TELEGRAM reply sounds too — nobody is looking at that window",
      f.played == ["send", "done"])

f = Frame(busy=["Tarn is deciding whether to reach out"])
f._tick_work_sounds()
check("a HEARTBEAT sounds too", f.played == ["send"])


# --- no double-sounding when the desktop path chimes directly -----------

f = Frame()
f._chime("send")                      # desktop calls it the instant you send
f._busy = ["Bracken is part-way through a reply in the main window"]
f._tick_work_sounds()                 # tick sees busy, but was already primed
check("a direct send is not re-sounded by the tick", f.played == ["send"])
f._chime("done")
f._busy = []
f._tick_work_sounds()
check("...and neither is a direct finish", f.played == ["send", "done"])


# --- the repeating cue paces itself -------------------------------------

f = Frame(config={"reply_chime": True, "chime_working_secs": 30},
          busy=["something running"])
f._tick_work_sounds()                 # start
f._tick_work_sounds()                 # 5s later, nowhere near 30
check("the working cue does not fire on every tick", f.played == ["send"])
f._work_sound_last_tick -= 31         # pretend 31s passed
f._tick_work_sounds()
check("...but does once the interval has elapsed",
      f.played == ["send", "working"])

f = Frame(config={"reply_chime": True, "chime_working_secs": 0},
          busy=["something running"])
f._tick_work_sounds()
f._work_sound_last_tick -= 9999
f._tick_work_sounds()
check("0 switches the repeat off without silencing the others",
      f.played == ["send"])


# --- waiting on a human is not the machine working ----------------------

f = Frame(busy=["2 tool approvals waiting on your answer"])
f._tick_work_sounds()
check("an unanswered approval makes no working sound — that would be nagging",
      f.played == [])

f = Frame(busy=["1 tool approval waiting on your answer",
                "Vesper is saving notes to its memory"])
f._tick_work_sounds()
check("...but real work alongside it still sounds", f.played == ["send"])


# --- per-cue settings ----------------------------------------------------

cfg = {"reply_chime": True, "chime_stages": {
    "send": {"on": False, "volume": 0.8},
    "done": {"on": True, "volume": 0.9}}}
f = Frame(config=cfg, busy=["x"])
f._tick_work_sounds()
check("a cue switched off stays silent", f.played == [])
f._busy = []
f._tick_work_sounds()
check("...while another stays audible", f.played == ["done"])

cfg = {"reply_chime": True, "chime_stages": {"done": {"on": True, "volume": 0.0}}}
f = Frame(config=cfg, busy=["x"])
f._tick_work_sounds(); f._busy = []; f._tick_work_sounds()
check("volume 0 silences a cue even when ticked", "done" not in f.played)

# Back-compat: an install that has never opened the new dialog.
f = Frame(config={"reply_chime": True, "chime_volume": 0.5})
on, vol = f._chime_setting("done")
check("with no per-cue config, the old switch and volume are used",
      on is True and abs(vol - 0.5) < 1e-9)
f = Frame(config={"reply_chime": False})
on, _ = f._chime_setting("done")
check("...and an install with chimes off stays off", on is False)

# Junk must not break sound, and must never break the turn it rides on.
for junk in ({"reply_chime": True, "chime_stages": "nonsense"},
             {"reply_chime": True, "chime_stages": {"done": "nonsense"}},
             {"reply_chime": True, "chime_volume": "loud"},
             {}):
    try:
        Frame(config=junk)._chime_setting("done")
        ok = True
    except Exception as e:
        ok = False
        print(f"   raised: {e!r}")
    check(f"survives config {str(junk)[:44]!r}", ok)


# --- the redistill progress cue -----------------------------------------
#
# A redistill's per-chunk report was SPOKEN, which means it never
# arrived: a screen reader running character echo has no free moment
# while its user types, and for the person this was written for that is
# a constant, not an occasional collision. So an hour-long redistill and
# a stalled one sounded exactly alike. The pitch carries the one number
# that matters -- rising means getting there, the same beep twice means
# stuck.

import frame.status_voice_mixin as _svm  # noqa: E402

_played_freqs = []
_real_play_chime = _svm.play_chime
_svm.play_chime = lambda freq, dur, volume=0.8, name=None: (
    _played_freqs.append((freq, name)))


class ChunkFrame(Frame):
    _chime_progress = StatusVoiceMixin._chime_progress


cf = ChunkFrame(config={"reply_chime": True, "chime_volume": 0.8})
for frac in (0.0, 0.5, 1.0):
    cf._chime_progress(frac)
freqs = [f for f, _n in _played_freqs]
check("the first chunk is the low tone", freqs[0] == 440)
check("halfway is halfway up", freqs[1] == 660)
check("the last chunk is the high tone", freqs[2] == 880)
check("the pitch actually rises", freqs == sorted(freqs))
check("it uses its own sound name, so chunk.wav can replace it",
      {n for _f, n in _played_freqs} == {"chunk"})

_played_freqs.clear()
for junk in (-5, 12, "nonsense", None, float("nan")):
    cf._chime_progress(junk)
check("a nonsense fraction still makes one in-range sound, never a crash",
      len(_played_freqs) == 5
      and all(440 <= f <= 880 for f, _n in _played_freqs))

_played_freqs.clear()
ChunkFrame(config={"reply_chime": False})._chime_progress(0.5)
check("chimes off means the progress cue is off too", _played_freqs == [])

_played_freqs.clear()
ChunkFrame(config={"reply_chime": True,
                   "chime_stages": {"chunk": {"on": False, "volume": 0.8}}}
           )._chime_progress(0.5)
check("...and it can be switched off on its own", _played_freqs == [])

_svm.play_chime = _real_play_chime

# The clock-driven "still working" repeat stands down for the length of a
# redistill: the per-chunk cue already reports at a similar rate and says
# more. Two reassurance cues at once is just noise.
f = Frame(config={"reply_chime": True, "chime_working_secs": 30},
          busy=["Lark is saving notes to its memory"])
f._tick_work_sounds()
f._walking_from_start = {("Lark", "desktop"): True}
f._work_sound_last_tick -= 31
f._tick_work_sounds()
check("the working repeat stands down during a redistill",
      f.played == ["send"])
f._walking_from_start = {}
f._work_sound_last_tick -= 31
f._tick_work_sounds()
check("...and comes back when the redistill is over",
      f.played == ["send", "working"])


print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_sound_cues: all checks passed")
