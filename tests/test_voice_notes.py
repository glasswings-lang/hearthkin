# SPDX-License-Identifier: CC0-1.0
"""Voice messages become words (12 Nov 2026).

Builds a real Ogg Opus voice message (two seconds of tone, encoded with the
same libopus Hearthkin carries, wrapped in real Ogg pages), decodes it with
voice_notes, and checks the WAV is the right length and shape for the speech
model: stt's own reader turns it into two seconds of 16 kHz samples. Then the
whole transcribe step with the speech model stubbed, and the failures a
person must be told about.

Run: python tests/test_voice_notes.py
"""

import io
import math
import os
import struct
import sys
import wave
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stt  # noqa: E402
import voice_notes  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def _ogg_crc(data):
    # Ogg's CRC-32 (polynomial 0x04C11DB7, no reflection).
    crc = 0
    for b in data:
        crc ^= b << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) if crc & 0x80000000 else crc << 1
            crc &= 0xFFFFFFFF
    return crc


def _page(packet, seq, granule, flags=0):
    lacing, n = [], len(packet)
    while n >= 255:
        lacing.append(255)
        n -= 255
    lacing.append(n)
    head = struct.pack("<4sBBqIIIB", b"OggS", 0, flags, granule, 1234, seq,
                       0, len(lacing)) + bytes(lacing)
    page = bytearray(head + packet)
    struct.pack_into("<I", page, 22, _ogg_crc(bytes(page)))
    return bytes(page)


def make_voice_note(seconds=2.0):
    import discord.opus as opus
    if not opus.is_loaded():
        opus._load_default()
    enc = opus.Encoder()
    rate, frame = 48000, 960
    pages = [
        _page(b"OpusHead" + bytes([1, 2]) + struct.pack("<HIhB", 312, rate,
                                                        0, 0), 0, 0, flags=2),
        _page(b"OpusTags" + struct.pack("<I", 4) + b"test" + struct.pack(
            "<I", 0), 1, 0),
    ]
    total = int(seconds * rate)
    seq, done = 2, 0
    while done < total:
        samples = []
        for i in range(frame):
            v = int(8000 * math.sin(2 * math.pi * 440 * (done + i) / rate))
            samples += [v, v]
        pkt = enc.encode(struct.pack(f"<{len(samples)}h", *samples), frame)
        done += frame
        pages.append(_page(pkt, seq, done, flags=4 if done >= total else 0))
        seq += 1
    return b"".join(pages)


note = make_voice_note(2.0)
wav = voice_notes.ogg_opus_to_wav(note)
with wave.open(io.BytesIO(wav), "rb") as w:
    secs = w.getnframes() / w.getframerate()
    shape = (w.getnchannels(), w.getsampwidth(), w.getframerate())
check("a voice message decodes to WAV the speech model can read",
      shape == (2, 2, 48000))
check("...the right length (two seconds in, two seconds out)",
      abs(secs - 2.0) < 0.05)
samples = stt.wav_to_array(wav)
check("...and stt turns it into two seconds of 16 kHz speech samples",
      abs(len(samples) / 16000.0 - 2.0) < 0.05)
check("...with sound in it, not silence",
      float(abs(samples).max()) > 0.05)

seen = {}


def fake_transcribe(wav_bytes, settings=None, get_api_key=None):
    seen["wav"], seen["settings"] = wav_bytes, settings
    return "  hello Tensor  "


real = stt.transcribe
stt.transcribe = fake_transcribe
try:
    got = voice_notes.transcribe(note, {"model": "base.en"})
finally:
    stt.transcribe = real
check("transcribing uses the dictation settings and returns the words",
      got == "hello Tensor" and seen["settings"] == {"model": "base.en"}
      and seen["wav"][:4] == b"RIFF")
check("the kin is told it was a voice message",
      voice_notes.as_message("hello") ==
      "[voice message, transcribed] hello")
check("...under any caption that came with it",
      voice_notes.as_message("hello", "for you") ==
      "for you\n[voice message, transcribed] hello")


def raises(fn):
    try:
        fn()
    except voice_notes.VoiceNoteError as e:
        return str(e)
    return ""


check("an empty message says so", "empty" in raises(
    lambda: voice_notes.ogg_opus_to_wav(b"")))
check("something that isn't a voice message says so",
      "didn't decode" in raises(
          lambda: voice_notes.ogg_opus_to_wav(b"not audio at all" * 10)))
stt.transcribe = lambda *a, **k: ""
try:
    msg = raises(lambda: voice_notes.transcribe(note))
finally:
    stt.transcribe = real
check("no words made out says so, rather than sending the kin nothing",
      "no words" in msg)
stt.transcribe = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
try:
    msg = raises(lambda: voice_notes.transcribe(note))
finally:
    stt.transcribe = real
check("a transcription failure comes back as a plain reason",
      "transcription failed: down" in msg)

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_voice_notes: all checks passed")
