# SPDX-License-Identifier: CC0-1.0
"""Nothing big is downloaded without the person saying yes (25 Nov 2026).

faster-whisper fetches a missing speech model by itself, so the first
dictation, or the start-up warm-up, quietly pulled up to 3 GB. And a
16 GB audio model was found on the person's C: drive, pulled by a script
someone ran once. The person: "I hate this silent shit."

Pinned: dictation never downloads unless the settings screen asked first;
the describe_audio script asks before downloading and refuses with nobody
at the keyboard.

Run: python tests/test_no_silent_downloads.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import stt  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


FAKE = "no-such-whisper-model-for-tests"
check("a model that isn't here reads as not on disk",
      stt.model_on_disk(FAKE) is False)

# Whatever happens, WhisperModel must not be reached for a missing model.
reached = []
try:
    import faster_whisper
    real = faster_whisper.WhisperModel
    faster_whisper.WhisperModel = lambda *a, **k: reached.append(a) or (_ for _ in ()).throw(RuntimeError("would download"))
except Exception:
    faster_whisper = None

try:
    stt.load_model(FAKE, "cpu", "int8")
    caught = None
except stt.ModelNotDownloaded as e:
    caught = e
check("a missing model raises ModelNotDownloaded instead of downloading",
      caught is not None and not reached and caught.name == FAKE)
# Read on Telegram/Discord by someone who usually isn't the person running
# Hearthkin, so it names the kin's person, not "you" (25 Nov 2026).
check("...whose message says nothing was downloaded and who can get it",
      caught is not None
      and "Nothing was downloaded" in str(caught)
      and "The kin's person can download it" in str(caught)
      and "you dictate" not in str(caught))

ok, msg = stt.self_check({"model": FAKE, "device": "cpu", "compute": "int8"})
check("Check dictation without the person's yes downloads nothing",
      ok is False and not reached and "Nothing was downloaded" in msg)

# The desktop keeps the recording so it can ask, then carry on (the person,
# 25 Nov 2026: "just pop a dialog… do them in context").
import voice  # noqa: E402
eng = voice.VoiceEngine.__new__(voice.VoiceEngine)
eng._get_api_key = lambda: ""
wav = stt.wav_from_pcm(b"\x00\x00" * 1600, samplerate=16000)
try:
    eng.transcribe_wav(wav, {"model": FAKE, "device": "cpu", "compute": "int8"})
    kept = None
except voice.VoiceModelMissing as e:
    kept = e
check("the voice engine hands back the recording with the model's details",
      kept is not None and kept.wav_bytes == wav and kept.name == FAKE
      and not reached)
mixin = (ROOT / "frame" / "status_voice_mixin.py").read_text(encoding="utf-8")
check("the desktop asks right then, and on yes transcribes the same words",
      "_ask_to_download_speech_model" in mixin
      and '"retry_wav": missing.wav_bytes, "allow_download": True' in mixin)

if faster_whisper is not None:
    faster_whisper.WhisperModel = real

dlg = (ROOT / "dialogs" / "dictation_settings.py").read_text(encoding="utf-8")
check("the settings screen asks before downloading, naming the size",
      "Download the speech model?" in dlg and "allow_download=allow_download" in dlg)

script = (ROOT / "scripts" / "describe_audio.py").read_text(encoding="utf-8")
check("describe_audio asks before downloading its 16 GB model",
      "local_files_only=True" in script and "Type yes to download" in script)
check("...and refuses with nobody at the keyboard",
      "isatty()" in script and "nothing was downloaded" in script)

print()
if _fails:
    print(f"test_no_silent_downloads: {len(_fails)} FAILED")
    sys.exit(1)
print("test_no_silent_downloads: all checks passed")
