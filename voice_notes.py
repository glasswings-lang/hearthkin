# SPDX-License-Identifier: CC0-1.0
"""Voice messages sent to a kin on Telegram or Discord, turned into words.

Both apps send a voice message as Ogg Opus: compressed speech in an Ogg
container. Hearthkin's speech recognition (stt.py) works on plain WAV and
deliberately ships no FFmpeg, so a voice message is decoded here, with what
Hearthkin already carries: discord.py's Ogg reader and its bundled libopus
decoder (discord.py is a hard dependency; the DLL is in discord/bin). The
WAV then goes through stt.transcribe exactly as dictation does, with the
same settings and the same model, wherever it lives.

Added 12 Nov 2026: a voice note sent to a kin used to be ignored without a
word on both apps (surface check row `dictation`).
"""

import io

# Discord's voice messages, and Telegram's, are well under this. A cap keeps
# a long upload from tying a transcription model up for many minutes.
MAX_VOICE_BYTES = 10 * 1024 * 1024
MAX_VOICE_SECONDS = 15 * 60


class VoiceNoteError(RuntimeError):
    """A voice message that couldn't be turned into words, with a reason a
    person can read."""


def ogg_opus_to_wav(data):
    """Decode Ogg Opus bytes to WAV bytes (48 kHz, 16-bit, stereo, the
    decoder's native shape; stt resamples to what it needs). Raises
    VoiceNoteError with a plain reason."""
    if not data:
        raise VoiceNoteError("the voice message was empty")
    if len(data) > MAX_VOICE_BYTES:
        raise VoiceNoteError("the voice message is too long to transcribe")
    try:
        import discord.opus as opus
        from discord.oggparse import OggStream
    except Exception as e:
        raise VoiceNoteError(f"the Opus decoder isn't available ({e})") from e
    if not opus.is_loaded():
        try:
            opus._load_default()
        except Exception:
            pass
    if not opus.is_loaded():
        raise VoiceNoteError("the Opus decoder couldn't be loaded")
    decoder = opus.Decoder()
    pcm = bytearray()
    limit = MAX_VOICE_SECONDS * opus.Decoder.SAMPLING_RATE * 2 * 2
    try:
        for packet in OggStream(io.BytesIO(data)).iter_packets():
            # The first two packets are the stream's header and tags.
            if packet.startswith(b"OpusHead") or packet.startswith(b"OpusTags"):
                continue
            pcm += decoder.decode(packet)
            if len(pcm) > limit:
                raise VoiceNoteError(
                    "the voice message is too long to transcribe")
    except VoiceNoteError:
        raise
    except Exception as e:
        raise VoiceNoteError(
            f"that didn't decode as a voice message ({e})") from e
    if not pcm:
        raise VoiceNoteError("there was no sound in the voice message")
    from stt import wav_from_pcm
    return wav_from_pcm(bytes(pcm), samplerate=opus.Decoder.SAMPLING_RATE,
                        channels=opus.Decoder.CHANNELS, sampwidth=2)


def transcribe(data, settings=None, get_api_key=None):
    """Voice message bytes -> the words in it. Uses the dictation settings
    (the same model dictation uses). Raises VoiceNoteError, never anything
    else, so a caller can always tell the person why."""
    wav = ogg_opus_to_wav(data)
    import stt
    try:
        text = stt.transcribe(wav, settings, get_api_key=get_api_key)
    except Exception as e:
        raise VoiceNoteError(f"transcription failed: {e}") from e
    text = (text or "").strip()
    if not text:
        raise VoiceNoteError("no words could be made out")
    return text


def as_message(heard, caption=""):
    """What the kin receives: the words, marked as heard rather than typed,
    so it doesn't take a transcription slip for the person's own typo."""
    said = f"[voice message, transcribed] {heard}"
    return f"{caption.strip()}\n{said}" if (caption or "").strip() else said
