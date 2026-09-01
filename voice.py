# SPDX-License-Identifier: CC0-1.0

"""voice — the kin's spoken voice, and the microphone.

Owns:
  - ElevenLabs HTTP client for text-to-speech (raw urllib, no SDK dep)
  - Audio playback queue: sentences fired sequentially through
    sounddevice's PCM output stream so the kin "speaks" in order
    even when sentences arrive out of generation order
  - Microphone capture for dictation
  - Voice catalog (cached per VoiceEngine instance)

Turning that captured audio into text is `stt.py`'s job, not this
module's. The split is deliberate: recording needs a microphone and
transcription does not, so keeping them apart is what lets dictation
have three interchangeable backends — local Whisper by default, a
Whisper server on another machine, or ElevenLabs Scribe — and what lets
all three be tested without a sound card.

Nothing here is required for dictation. A kin having a paid voice and a
person being able to speak to it are unrelated capabilities, and they
used to share a switch; see docs/voice-design.md for the reversal.

Single instance per Hearthkin frame. Per-kin voice settings (voice_id,
model_id, stability, similarity_boost, style, speed) live on the
agent_cfg dict, not on the engine — the engine is stateless re: which
kin is talking; callers pass the per-kin settings on each call.

Threading model:
  - speak_sentence() runs on the calling thread; the actual HTTP fetch
    + audio playback happen on the engine's worker thread (drained from
    a queue). Returns immediately so the chat path doesn't block.
  - Mic capture uses sounddevice's callback thread. stop_recording_*()
    methods are safe to call from any thread.
  - All HTTP calls are blocking on whichever thread initiates them.
"""

import json
import queue
import threading
import urllib.error
import urllib.parse
import urllib.request

import stt


_BASE_URL = "https://api.elevenlabs.io"
_USER_AGENT = "Hearthkin/0.2 (+local model agent; voice subsystem)"

# We request raw PCM at 22050 Hz mono int16 for two reasons:
#   1. sounddevice plays it directly without decoding (no ffmpeg dep).
#   2. Bandwidth is low enough that the streaming feels real-time over
#      most home connections — ~44 KB/s sustained.
_PCM_SAMPLE_RATE = 22050
_PCM_FORMAT = "pcm_22050"


def _import_audio_libs():
    """Import sounddevice + numpy lazily and return them, or None on
    failure. Imports are slow (CFFI binding load) and we'd rather take
    the cost on first voice use than at hearthkin startup. Also lets
    the rest of the app keep working if the sounddevice install is
    broken."""
    try:
        import sounddevice as sd
        import numpy as np
        return sd, np
    except Exception:
        return None, None


class VoiceEngineError(RuntimeError):
    """Voice subsystem couldn't do the requested thing — bad API key,
    network failure, audio device unavailable, etc. Caller decides
    whether to surface to the user or swallow."""
    pass


class VoiceEngine:
    """One per Hearthkin frame. Owns the audio playback worker, the
    mic capture state, and the cached voice catalog.

    Construct with a callable that returns the current API key (so
    the engine doesn't have to be reinitialized when the user edits
    the key in Preferences)."""

    def __init__(self, get_api_key, on_async_error=None):
        """
        Args:
            get_api_key: callable returning the ElevenLabs API key string.
                Called lazily on each request so a key edit applies
                immediately without re-init.
            on_async_error: optional callable(str_message) invoked from
                the playback worker thread when a sentence fails (HTTP
                error, network failure, etc.). Without this hook, async
                failures get silently dropped because the worker
                deliberately catches its own exceptions to keep the
                loop alive. The callback should marshal back to the
                main thread itself (e.g. wx.CallAfter) — we call it
                from the worker thread as-is.
        """
        self._get_api_key = get_api_key
        self._on_async_error = on_async_error
        self._voices_cache = None  # list[dict] or None until fetched
        self._voices_cache_lock = threading.Lock()

        # Playback queue: items are tuples of (audio_bytes, on_done)
        # where on_done is an optional callable invoked after that
        # chunk finishes playing (or is cancelled). Sentinel `None`
        # tells the worker to exit.
        self._play_queue = queue.Queue()
        self._play_thread = None  # lazy-started on first speak call
        self._play_thread_started = threading.Event()
        self._play_thread_lock = threading.Lock()
        # True while the worker is processing the item it dequeued —
        # the queue is empty during playback of the current sentence,
        # so is_speaking() needs this flag to not lie mid-sentence
        # (audit L-B24). Plain bool write/read under the GIL.
        self._playing = False
        # Cancellation. Bumped every time stop_speaking() is called.
        # The worker checks this between chunks and discards anything
        # whose generation predates the latest bump.
        self._cancel_gen = 0
        self._cancel_lock = threading.Lock()

        # Mic capture state. The recording stream is created on
        # start_recording() and stopped on stop_recording_*().
        self._mic_stream = None
        self._mic_buffer = bytearray()
        self._mic_lock = threading.Lock()

    # ─── HTTP helpers ─────────────────────────────────────────────

    def _api_key(self):
        key = (self._get_api_key() or "").strip()
        if not key:
            raise VoiceEngineError(
                "ElevenLabs API key not set. Add one under "
                "Tools → Preferences → Connections."
            )
        return key

    def _http_get(self, path, timeout=30):
        url = f"{_BASE_URL}{path}"
        req = urllib.request.Request(url)
        req.add_header("xi-api-key", self._api_key())
        req.add_header("User-Agent", _USER_AGENT)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            raise VoiceEngineError(
                f"ElevenLabs HTTP {e.code}: {body[:200] or e.reason}"
            ) from e
        except Exception as e:
            raise VoiceEngineError(f"ElevenLabs request failed: {e}") from e

    # ─── Voice catalog ────────────────────────────────────────────

    def list_voices(self, force_refresh=False):
        """Return the user's voice catalog. Cached after first fetch
        — call with force_refresh=True after the user has added
        voices in ElevenLabs's web UI and wants the picker to update.

        Returned dicts have at minimum: voice_id, name, category,
        labels (dict of accent/age/gender/use_case), preview_url."""
        with self._voices_cache_lock:
            if self._voices_cache is not None and not force_refresh:
                return list(self._voices_cache)
        data = self._http_get("/v1/voices")
        voices = data.get("voices") or []
        with self._voices_cache_lock:
            self._voices_cache = voices
        return list(voices)

    def get_voice(self, voice_id):
        """Return the catalog entry for one voice, or None if not in
        the user's library."""
        for v in self.list_voices():
            if v.get("voice_id") == voice_id:
                return v
        return None

    def test_api_key(self):
        """Hit /v1/user as a cheap auth check. Returns (ok, label)."""
        try:
            data = self._http_get("/v1/user", timeout=10)
            sub = data.get("subscription") or {}
            tier = sub.get("tier") or "(unknown tier)"
            chars_used = sub.get("character_count")
            chars_limit = sub.get("character_limit")
            if chars_used is not None and chars_limit:
                return True, (
                    f"OK — tier {tier}, "
                    f"{chars_used:,}/{chars_limit:,} chars used this month"
                )
            return True, f"OK — tier {tier}"
        except VoiceEngineError as e:
            return False, str(e)

    # ─── Text → speech ─────────────────────────────────────────────

    def speak_sentence(self, text, voice_settings, on_done=None):
        """Queue a sentence for TTS + playback. Returns immediately;
        the actual fetch + audio playback happens on the engine's
        worker thread.

        `voice_settings` must include voice_id and model_id; may also
        include stability, similarity_boost, style, speed. Bad/missing
        settings raise VoiceEngineError synchronously.

        `on_done` is called (no args) when this sentence finishes
        playing OR is cancelled. Use it to clean up per-sentence
        state in the caller (status field, NVDA announcements, etc.)."""
        text = (text or "").strip()
        if not text:
            return
        voice_id = (voice_settings or {}).get("voice_id") or ""
        if not voice_id:
            raise VoiceEngineError(
                "speak_sentence: no voice_id in voice_settings"
            )

        # Snapshot the cancellation generation. The worker will skip
        # this chunk if cancel_gen has advanced past this snapshot
        # by the time the chunk is dequeued.
        with self._cancel_lock:
            gen_snapshot = self._cancel_gen

        self._ensure_play_thread()
        self._play_queue.put(
            ("speak", text, dict(voice_settings), gen_snapshot, on_done)
        )

    def stop_speaking(self):
        """Cancel any in-flight or queued TTS playback. Safe to call
        from any thread. Pending on_done callbacks for cancelled
        chunks ARE still invoked (so caller-side cleanup runs)."""
        with self._cancel_lock:
            self._cancel_gen += 1

    def is_speaking(self):
        """True if a sentence is currently playing OR the queue has
        anything pending. The in-flight item is dequeued before it
        plays, so the queue alone is empty mid-sentence — `_playing`
        covers that window (audit L-B24). Cheap; no locking."""
        return self._playing or not self._play_queue.empty()

    def _ensure_play_thread(self):
        """Lazy-start the playback worker on first speak. Locked with
        a double-check so two first-callers racing can't each start a
        worker (audit L-B25) — two workers would drain the queue in
        parallel and play sentences out of order."""
        if self._play_thread_started.is_set():
            return
        with self._play_thread_lock:
            if self._play_thread_started.is_set():
                return
            sd, np = _import_audio_libs()
            if sd is None:
                raise VoiceEngineError(
                    "sounddevice / numpy not available — voice playback disabled"
                )
            self._play_thread = threading.Thread(
                target=self._play_worker, args=(sd, np), daemon=True,
            )
            self._play_thread.start()
            self._play_thread_started.set()

    def _play_worker(self, sd, np):
        """Worker loop: pull chunks off the queue, fetch TTS over
        HTTP, stream PCM into sounddevice. One sentence at a time
        so the audio plays in queue order."""
        while True:
            item = self._play_queue.get()
            if item is None:
                return  # explicit shutdown sentinel
            # The item is out of the queue now — flag it as in-flight
            # so is_speaking() stays True while it plays (audit L-B24).
            self._playing = True
            try:
                try:
                    tag, text, voice_settings, gen_snapshot, on_done = item
                except Exception:
                    continue

                # Skip if this chunk was cancelled before we got to it.
                with self._cancel_lock:
                    cancelled = (gen_snapshot < self._cancel_gen)
                if cancelled:
                    if on_done is not None:
                        try:
                            on_done()
                        except Exception:
                            pass
                    continue

                try:
                    self._fetch_and_play_one(text, voice_settings, gen_snapshot, sd, np)
                except Exception as e:
                    # Don't let one failed sentence kill the worker;
                    # the user will hear silence for that turn but the
                    # next one still plays. BUT do surface the failure
                    # via the optional error callback so the caller can
                    # paint an Activity-field notice — otherwise the user
                    # gets silence with no explanation (the original
                    # reason this hook was added: ElevenLabs 401 due to
                    # missing TTS permission on the API key, which
                    # only surfaces here in the worker since speak_sentence
                    # returns synchronously before the request fires).
                    cb = self._on_async_error
                    if cb is not None:
                        try:
                            cb(str(e))
                        except Exception:
                            pass
                if on_done is not None:
                    try:
                        on_done()
                    except Exception:
                        pass
            finally:
                self._playing = False

    def _fetch_and_play_one(self, text, voice_settings, gen_snapshot, sd, np):
        """Single sentence: POST to ElevenLabs streaming endpoint,
        read PCM chunks as they arrive, write them to sounddevice's
        output stream. Honors the cancellation generation —
        if cancel_gen advances during fetch or playback, we stop
        immediately."""
        voice_id = voice_settings["voice_id"]
        model_id = voice_settings.get("model_id") or "eleven_turbo_v2_5"
        body = {
            "text": text,
            "model_id": model_id,
            "voice_settings": {
                "stability": float(voice_settings.get("stability", 0.5)),
                "similarity_boost": float(
                    voice_settings.get("similarity_boost", 0.75)
                ),
                "style": float(voice_settings.get("style", 0.0)),
                "use_speaker_boost": True,
            },
        }
        # Speed is a separate top-level field on some accounts; safe
        # to include — the API ignores unknown fields gracefully.
        speed = voice_settings.get("speed")
        if speed is not None:
            body["voice_settings"]["speed"] = float(speed)

        path = (
            f"/v1/text-to-speech/{urllib.parse.quote(voice_id, safe='')}"
            f"/stream?output_format={_PCM_FORMAT}"
        )
        url = f"{_BASE_URL}{path}"
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), method="POST",
        )
        req.add_header("xi-api-key", self._api_key())
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "audio/pcm")
        req.add_header("User-Agent", _USER_AGENT)

        # Open the streaming output device. Mono int16 at our PCM
        # sample rate. blocksize=0 lets sounddevice pick the optimal
        # buffer; latency='low' keeps perceived lag tight.
        stream = sd.RawOutputStream(
            samplerate=_PCM_SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=0,
            latency="low",
        )
        stream.start()

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                while True:
                    # Cancellation check at every chunk boundary.
                    with self._cancel_lock:
                        if gen_snapshot < self._cancel_gen:
                            return
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    try:
                        stream.write(chunk)
                    except Exception:
                        return
        except urllib.error.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            raise VoiceEngineError(
                f"TTS HTTP {e.code}: {body_text or e.reason}"
            ) from e
        except urllib.error.URLError as e:
            raise VoiceEngineError(f"TTS network error: {e.reason}") from e
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    # ─── Speech → text ─────────────────────────────────────────────
    #
    # Recording lives here because it needs a microphone. The actual
    # transcription lives in `stt.py`, which is a plain function over
    # WAV bytes and knows three backends — local Whisper (the default,
    # free and offline), a Whisper server on another machine, and
    # ElevenLabs Scribe. Splitting those two is what lets the backend be
    # a setting rather than a rewrite, and what lets the tests drive
    # every backend without a sound card.

    def start_recording(self, samplerate=16000):
        """Begin capturing mic audio into an internal buffer. Call
        stop_recording_and_transcribe() to finish and transcribe.

        16 kHz mono is what every Whisper-family model wants natively
        and what Scribe accepts, so one capture format serves all three
        backends with no resampling anywhere."""
        sd, np = _import_audio_libs()
        if sd is None:
            raise VoiceEngineError(
                "sounddevice / numpy not available — voice capture disabled"
            )
        with self._mic_lock:
            if self._mic_stream is not None:
                return  # already recording — silent no-op
            self._mic_buffer = bytearray()
            self._mic_samplerate = samplerate

            def _cb(indata, frames, time_info, status):
                # indata is a numpy array; copy bytes into the buffer.
                # status flags (overflows etc.) get dropped — not worth
                # surfacing for a hobby chat client.
                with self._mic_lock:
                    self._mic_buffer.extend(bytes(indata))

            self._mic_stream = sd.RawInputStream(
                samplerate=samplerate,
                channels=1,
                dtype="int16",
                callback=_cb,
            )
            self._mic_stream.start()

    def cancel_recording(self):
        """Stop the microphone and throw the audio away, for the case
        where a recording has to end without becoming a transcript —
        quitting mid-dictation, say. Never raises."""
        with self._mic_lock:
            stream = self._mic_stream
            self._mic_stream = None
            self._mic_buffer = bytearray()
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def _finish_recording(self):
        """Stop the stream and return (wav_bytes, samplerate). Returns
        empty bytes when nothing was captured; the caller decides what
        that means."""
        with self._mic_lock:
            stream = self._mic_stream
            buf = bytes(self._mic_buffer)
            samplerate = getattr(self, "_mic_samplerate", 16000)
            self._mic_stream = None
            self._mic_buffer = bytearray()
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        if not buf:
            return b"", samplerate
        return stt.wav_from_pcm(buf, samplerate=samplerate), samplerate

    def stop_recording_and_transcribe(self, settings=None):
        """Stop the mic stream and transcribe what was captured with the
        configured backend. Returns the text, or raises. Blocking — call
        from a worker thread.

        `settings` is the app-level dictation config dict (see
        DEFAULT_CONFIG["dictation"]). None means all defaults, which is
        local Whisper: free, offline, no account."""
        wav_bytes, _rate = self._finish_recording()
        if not wav_bytes:
            raise VoiceEngineError(
                "Empty recording — press Stop talking after speaking, "
                "not before."
            )
        try:
            return stt.transcribe(
                wav_bytes, settings, get_api_key=self._get_api_key)
        except stt.SttError as e:
            # Re-raise in the type this module's callers already handle,
            # keeping the message — which is written to be shown to a
            # person as-is.
            raise VoiceEngineError(str(e)) from e

    # ─── Shutdown ─────────────────────────────────────────────────

    def shutdown(self):
        """Stop the playback worker on app exit. Best-effort — drops
        anything still queued.

        Also closes the microphone if a dictation was still running.
        Quitting mid-sentence is an ordinary thing to do, and it must
        not leave an open input stream behind holding the device."""
        try:
            self.cancel_recording()
        except Exception:
            pass
        try:
            self.stop_speaking()
            self._play_queue.put(None)
        except Exception:
            pass
