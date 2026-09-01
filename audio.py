# SPDX-License-Identifier: CC0-1.0

"""
audio — optional NVDA speech announcements and generated reply chimes.

Extracted from hearthkin.pyw. Self-contained: depends only on
stdlib (ctypes, os, sys, threading, struct, math, io, wave) and the optional
winsound module on Windows. _load_nvda_dll runs at import to bind to
NVDA's controller DLL if it can find one; nvda_speak is a no-op when
the DLL isn't present.

The DLL is shipped with Hearthkin under vendor/nvda/ (LGPL 2.1, unmodified
from NV Access). The env-var override (HEARTHKIN_NVDA_DLL) is consulted
FIRST so users can point at their own build of the controller client for
LGPL §6(b) compliance; then the bundled copy, so users without a
system-wide NVDA install — or with NVDA installed at a nonstandard path —
still get speech; system NVDA installs are the final fallbacks.

A one-line load-result trace is written to ~/.hearthkin/logs/nvda_status.log
so users can tell what happened without console output (pythonw.exe routes
stderr to the void).

The chime generator builds sine-wave WAVs in-process and plays them
via winsound, so the app has no external audio asset files to ship.
play_chime is safe to call from any thread — it spawns a daemon
thread per chime to avoid blocking the worker that called it.
"""

import ctypes
import datetime as _dt
import io
import math
import os
import struct
import sys
import threading
import wave

from hearthkin_paths import config_dir, logs_dir

try:
    import winsound
except ImportError:
    winsound = None


# --- NVDA speech (optional) --------------------------------------- #

# Set by _load_nvda_dll; surfaced via nvda_status() so the UI can show
# "loaded from <path>" in Preferences and the startup log can record it.
_nvda_dll_path = None
_nvda_dll_status = "not yet attempted"


def _candidate_dll_paths():
    """Yield candidate DLL paths in preference order.

    Order: env-var override → bundled (next to .exe / in vendor/nvda
    for dev) → system NVDA install. HEARTHKIN_NVDA_DLL is FIRST — it
    exists so a user can substitute their own build of the controller
    client (the LGPL §6(b) replacement mechanism), and an override
    that loses to the bundled copy can never win on a normal install
    (audit M-P7). Bundled beats system paths so a user without NVDA
    on PATH still gets speech.
    """
    arch64 = ctypes.sizeof(ctypes.c_void_p) == 8
    dll_name = "nvdaControllerClient64.dll" if arch64 else "nvdaControllerClient32.dll"

    candidates = []

    # 1. Env override — explicit user intent always wins.
    env_override = os.environ.get("HEARTHKIN_NVDA_DLL")
    if env_override:
        candidates.append(env_override)

    # 2. Bundled alongside the running .exe / .pyw. For PyInstaller frozen
    #    builds the .exe and the DLL both live in the install dir (e.g.
    #    C:\Program Files\Glasswings\Hearthkin\) — this is true for both
    #    onedir (current) and onefile (historical) modes, since
    #    sys.executable points at the .exe in {app}\ either way. For dev
    #    runs __file__ points at the source tree where the DLL lives
    #    under vendor/nvda/.
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.append(os.path.join(exe_dir, dll_name))
        # PyInstaller _MEIPASS fallback in case the spec is ever changed
        # to bundle the DLL inside the PyInstaller archive. The canonical
        # path is the install dir (LGPL §6(b) — user can replace the file).
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, dll_name))
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(script_dir, "vendor", "nvda", dll_name))
        candidates.append(os.path.join(script_dir, dll_name))

    # 3. System NVDA installs (the original behavior, kept as fallback).
    if arch64:
        candidates.extend([
            r"C:\Program Files (x86)\NVDA\nvdaControllerClient64.dll",
            r"C:\Program Files\NVDA\nvdaControllerClient64.dll",
        ])
    else:
        candidates.extend([
            r"C:\Program Files (x86)\NVDA\nvdaControllerClient32.dll",
            r"C:\Program Files\NVDA\nvdaControllerClient32.dll",
        ])
    # Per-user / portable NVDA install (some users have it under
    # %APPDATA%\NVDA).
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(os.path.join(appdata, "NVDA", dll_name))

    return candidates


def _write_nvda_status_log(message):
    """Append a single line to ~/.hearthkin/logs/nvda_status.log.

    Always-on log (like empty_replies.log) so users running under pythonw.exe
    can diagnose silent NVDA failures by reading the file. Best-effort —
    never raises into the import path.
    """
    try:
        log_dir = str(logs_dir())
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "nvda_status.log")
        ts = _dt.datetime.now().isoformat(timespec="seconds")
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"{ts} {message}\n")
    except Exception:
        pass


def _load_nvda_dll():
    """Try each candidate path; return the first that loads.

    Sets module-level _nvda_dll_path and _nvda_dll_status as a side effect
    so the UI can report what happened. Writes one log line summarizing
    the outcome.
    """
    global _nvda_dll_path, _nvda_dll_status

    tried = []
    for path in _candidate_dll_paths():
        if not path or not os.path.exists(path):
            tried.append(f"{path or '(empty)'} [missing]")
            continue
        try:
            dll = ctypes.windll.LoadLibrary(path)
        except Exception as e:
            tried.append(f"{path} [load error: {e!r}]")
            continue
        _nvda_dll_path = path
        _nvda_dll_status = f"loaded from {path}"
        _write_nvda_status_log(f"loaded {path}")
        return dll

    if tried:
        _nvda_dll_status = "no DLL found — checked: " + " ; ".join(tried)
    else:
        _nvda_dll_status = "no candidate paths"
    _nvda_dll_path = None
    _write_nvda_status_log(_nvda_dll_status)
    return None


_nvda_dll = _load_nvda_dll()


# --- a test run must never speak, and never make a sound ------------------
#
# Tests drive real handlers, and real handlers talk. `test_distill_walk_pacing`
# calls the actual "Cancel distilling" handler seven times to check what it
# does; that handler ends with nvda_speak("Distilling cancelled...."). So a
# suite run said "Distilling cancelled. Progress kept." four times in a row
# into a live screen reader, over whatever the person was actually reading.
# Nothing was wrong with the test or the handler — the two were simply never
# meant to meet a real speech channel.
#
# Silenced HERE, at the two choke points every sound and every announcement
# goes through, rather than by asking each test to patch what it might reach.
# A test cannot be expected to know that some handler five calls down ends in
# speech; that is exactly the kind of rule that gets forgotten, and the cost
# lands on someone mid-sentence. Same reasoning as the widget/foreground gate
# living in the test runner.
#
# Deliberately NOT keyed on HEARTHKIN_HOME: a second profile is a legitimate
# way to run the real app, and it must still speak.
def _suppressed():
    """True when this process must stay silent — a test run, in other words.

    Two ways in. `HEARTHKIN_SILENT` is the explicit one, set by
    `tests/run_all.py` for every child. The main-script heuristic covers a test
    someone runs directly, which the runner never sees.
    """
    if (os.environ.get("HEARTHKIN_SILENT", "").strip().lower()
            in ("1", "true", "yes")):
        return True
    try:
        main = os.path.basename(getattr(sys.modules.get("__main__"),
                                        "__file__", "") or "")
    except Exception:
        return False
    if main.startswith("test_") or main in ("run_all.py", "_gui_runner.py"):
        # Propagate, so subprocesses this test spawns are silent too — several
        # tests shell out to a fresh interpreter, and those children have no
        # test-looking script name of their own to be recognised by.
        os.environ["HEARTHKIN_SILENT"] = "1"
        return True
    return False


def speak_result(text):
    """Say ONE deliberate result out loud, even during a test run.

    The single, narrow exception to the silence above, and it exists for the
    person this project is for: a suite run's verdict is a line of terminal
    output that scrolls past, which is no use to someone who reads by screen
    reader. "I haven't seen a thing" was the report — the run had finished
    fine and simply never reached her.

    The rule this bends is about INCIDENTAL chatter: handlers announcing
    themselves mid-run, dozens of times, over whatever is being read. A single
    line at the very end, saying whether the suite passed, is the opposite of
    that — it is the thing that was asked for.

    Called from exactly one place, `tests/run_all.py`, at the end of a run.
    **Don't reach for this from a test.** `tests/test_suite_is_silent.py`
    asserts that no test file calls it.
    """
    if _nvda_dll is not None and text:
        try:
            _nvda_dll.nvdaController_speakText(text)
        except Exception:
            pass


def nvda_speak(text):
    if _suppressed():
        return
    if _nvda_dll is not None and text:
        try:
            _nvda_dll.nvdaController_speakText(text)
        except Exception:
            pass


def nvda_status():
    """Return (loaded: bool, message: str, path: str | None).

    Surfaced via Preferences → Connections so users can see whether speech
    will work and which file is in use. Stable shape — UI code reads it.
    """
    return (_nvda_dll is not None, _nvda_dll_status, _nvda_dll_path)


def _make_beep_wav(freq, dur_ms, volume=0.8, sample_rate=22050):
    """Generate a sine-wave WAV (in memory) at the given frequency, duration,
    and amplitude. Returned bytes are a complete RIFF/WAVE file ready for
    winsound.PlaySound(buf, SND_MEMORY).

    Volume is 0.0–1.0; winsound.Beep has no volume control, hence the wav.
    Brief fade-in/out avoids the click you'd otherwise get from starting
    mid-cycle. All stdlib.
    """
    n_samples = max(1, int(sample_rate * dur_ms / 1000))
    vol = max(0.0, min(1.0, float(volume)))
    amp = int(32767 * vol)
    fade = min(n_samples // 8, int(sample_rate * 0.008))
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        two_pi_f = 2.0 * math.pi * freq
        for i in range(n_samples):
            t = i / sample_rate
            sample = math.sin(two_pi_f * t)
            if fade > 0:
                if i < fade:
                    sample *= i / fade
                elif i > n_samples - fade:
                    sample *= max(0.0, (n_samples - i) / fade)
            frames.extend(struct.pack('<h', int(sample * amp)))
        wf.writeframes(bytes(frames))
    return buf.getvalue()


# --- User-replaceable sound files -------------------------------- #
#
# Every sound the app plays first looks for a WAV the user dropped in
# ~/.hearthkin/sounds/<name>.wav and plays THAT instead of the generated
# tone. This is how an operator ships their own set: the built-in sine
# tones are a fallback that guarantees *a* sound with no assets to ship,
# but a file always wins. The volume slider still applies to a user file
# when it's plain 16-bit PCM (decoded, scaled, replayed); an exotic format
# is played as-is at its own baked-in loudness.
#
# Names in use: "send" / "first" / "done" / "chunk" (reply-chime stages,
# plus per-chunk redistill progress), "approval" (a kin is waiting for
# you to approve a gated tool), and
# "problem" (background work — a distillation, a redistill-from-start —
# stopped early).

def sounds_dir():
    """~/.hearthkin/sounds/ — created lazily on first custom-sound save.
    Public so the UI can open it / tell the operator where to drop files."""
    return str(config_dir() / "sounds")


def _user_wav_path(name):
    if not name:
        return None
    try:
        p = os.path.join(sounds_dir(), str(name) + ".wav")
        return p if os.path.isfile(p) else None
    except Exception:
        return None


def _scale_wav_bytes(raw, volume):
    """Return 16-bit-PCM WAV bytes scaled by `volume`, or None if the file
    isn't cleanly scalable (wrong sample width, unreadable) so the caller
    can play it as-is instead of corrupting it."""
    try:
        with wave.open(io.BytesIO(raw), "rb") as wf:
            if wf.getsampwidth() != 2:
                return None
            nch, fr, n = wf.getnchannels(), wf.getframerate(), wf.getnframes()
            frames = wf.readframes(n)
        vol = max(0.0, min(1.0, float(volume)))
        count = len(frames) // 2
        samples = struct.unpack("<%dh" % count, frames)
        packed = struct.pack("<%dh" % count,
                             *[max(-32768, min(32767, int(s * vol))) for s in samples])
        out = io.BytesIO()
        with wave.open(out, "wb") as wf2:
            wf2.setnchannels(nch)
            wf2.setsampwidth(2)
            wf2.setframerate(fr)
            wf2.writeframes(packed)
        return out.getvalue()
    except Exception:
        return None


def _make_alert_wav(volume=0.8, sample_rate=22050, descending=False):
    """A distinct two-tone attention cue, so an alert doesn't sound like an
    ordinary reply chime. Built as one WAV buffer.

    Rising (low → high) asks for something: a kin is waiting on you.
    Falling (high → low) reports something stopping: a distillation or a
    redistill that failed. Direction is the whole distinction, and it is
    the one shape you can tell apart without having learned which beep is
    which — the same reasoning as the rising reply-chime sequence.
    """
    lo = _make_beep_wav(560, 110, volume=volume, sample_rate=sample_rate)
    hi = _make_beep_wav(840, 150, volume=volume, sample_rate=sample_rate)
    if descending:
        lo, hi = (_make_beep_wav(840, 110, volume=volume,
                                 sample_rate=sample_rate),
                  _make_beep_wav(500, 200, volume=volume,
                                 sample_rate=sample_rate))
    try:
        with wave.open(io.BytesIO(lo), "rb") as wf:
            a = wf.readframes(wf.getnframes())
        with wave.open(io.BytesIO(hi), "rb") as wf:
            b = wf.readframes(wf.getnframes())
        gap = b"\x00\x00" * int(sample_rate * 0.05)  # 50ms silence between
        out = io.BytesIO()
        with wave.open(out, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(a + gap + b)
        return out.getvalue()
    except Exception:
        return hi  # single tone is a fine fallback


def _play_async(name, gen_wav, volume):
    """Shared player: user file (scaled if we can) → generated WAV → Beep.
    `gen_wav` is a zero-arg callable so we don't synthesize a tone we won't
    use when a user file is present. Runs on a daemon thread.

    Every chime and cue in the app funnels through here, so this one check
    keeps a test run silent no matter which of them it reaches."""
    if winsound is None or volume <= 0 or _suppressed():
        return

    def _play():
        path = _user_wav_path(name)
        if path:
            try:
                raw = open(path, "rb").read()
                scaled = _scale_wav_bytes(raw, volume)
                if scaled is not None:
                    winsound.PlaySound(scaled, winsound.SND_MEMORY)
                else:
                    winsound.PlaySound(path, winsound.SND_FILENAME)
                return
            except Exception:
                pass  # fall through to the generated tone
        try:
            winsound.PlaySound(gen_wav(), winsound.SND_MEMORY)
        except Exception:
            try:
                winsound.Beep(880, 140)
            except Exception:
                pass
    threading.Thread(target=_play, daemon=True).start()


def play_chime(freq=880, dur=140, volume=0.8, name=None):
    """Play a brief reply-chime tone asynchronously.

    Looks for ~/.hearthkin/sounds/<name>.wav first (see the file-sounds
    note above); otherwise generates an in-memory sine WAV and plays it via
    winsound.PlaySound — Beep has no volume control and is inaudibly quiet
    on many systems. Threaded so it doesn't block the UI for `dur` ms.

    No SND_ASYNC: CPython hard-rejects SND_MEMORY|SND_ASYNC (the buffer
    would have to outlive the call), so that combination raised
    RuntimeError on EVERY call and the WAV path silently fell through
    to Beep — making the volume preference dead (audit M-P1). Playing
    synchronously is fine here; we're already on a dedicated daemon
    thread, which was the whole point of the thread.
    """
    _play_async(name, lambda: _make_beep_wav(freq, dur, volume=volume), volume)


def play_alert(volume=0.8, name="approval"):
    """Play a two-tone attention cue asynchronously.

    Two callers today, each on its own toggle, both deliberately NOT reply
    chimes — NVDA speech can be interrupted by your own typing and a silent
    toast is easy to miss:

      name="approval" — a kin is blocked waiting for you to approve a gated
        tool. Rising, because it's asking for something.
      name="problem"  — background work stopped early (a distillation, a
        redistill-from-start). Falling, because it's reporting a stop.

    Either is replaceable via ~/.hearthkin/sounds/<name>.wav like any other
    sound.
    """
    descending = (name == "problem")
    _play_async(name,
                lambda: _make_alert_wav(volume=volume, descending=descending),
                volume)
