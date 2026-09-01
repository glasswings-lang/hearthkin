# SPDX-License-Identifier: CC0-1.0

"""stt — speech to text: a transcription model, wherever it lives.

Dictation is the one thing in this app a person may need on every
single turn. That is why the default runs on your own machine, for
free, with no account: a per-turn cost is not a thing everyone can
carry, and an interface you can only afford sometimes is not an
interface.

HOW IT IS ADDRESSED, and why it is shaped this way

A transcription model is chosen exactly the way a chat model or a
distillation model already is in this app: **a model name, plus the
machine it lives on.** Two settings, one idea.

    model = "base.en"          host = ""                    -> this computer
    model = "large-v3"         host = "http://box:8080"     -> that computer
    model = "whisper-large-v3" host = "https://api.groq..."  -> a service
    model = "elevenlabs/scribe_v1"                           -> ElevenLabs

`host` empty means "here", the same way an empty Ollama host does. Any
host that speaks the ordinary OpenAI `/v1/audio/transcriptions`
interface works, which is nearly all of them: whisper.cpp's
``whisper-server``, ``speaches``, ``faster-whisper-server``, and the
hosted services that copied that shape. So "point it at a transcription
model anywhere" is one address in a settings box — not a different
feature, not a different code path to maintain, and not something that
has to be re-installed on every machine by hand.

The `elevenlabs/` prefix is the one special case, and it mirrors the
`openrouter/` prefix that ``llm_backend.chat`` already routes on: a
provider whose interface is its own gets named in the model string
rather than given a mode of its own.

WHAT THIS MEANS FOR A MACHINE WITHOUT A GOOD GRAPHICS CARD

Nothing, mostly. The local model falls back to the processor on its own
and is still fast enough to dictate with — and a server you point at
does not need a graphics card either. The graphics card is a speed-up,
never a requirement, and no part of this refuses to work without one.

WHY THIS IS A SEPARATE MODULE FROM ``voice.py``

Recording needs a microphone; transcription does not. Splitting them
means everything here is a plain function over WAV bytes, so the tests
drive every route without a sound card, a model download, or a network.

``faster_whisper`` is imported lazily, inside the call, and its absence
is a clear message rather than an import error at startup — this app's
standing rule for heavy optional libraries. The FIRST import in a
process costs tens of seconds (it loads the CTranslate2 native
libraries), which is why ``preload`` exists and why the desktop calls it
at startup: paying that cost after somebody has already spoken looks
exactly like the app having hung.
"""

import io
import json
import threading
import urllib.error
import urllib.request
import wave


# Where a transcription actually happens. Derived from (model, host) by
# `route_for` — never stored, never set by hand. Kept as names because
# error messages and the settings screen both need to say which one
# answered.
ROUTE_LOCAL = "local"
ROUTE_SERVER = "server"
ROUTE_ELEVENLABS = "elevenlabs"

# The prefix that means "this provider speaks its own interface", the
# same convention llm_backend.chat uses for openrouter/.
ELEVENLABS_PREFIX = "elevenlabs/"

# base.en, not base: this is one person dictating into their own
# microphone, and the English-only weights are both smaller and more
# accurate on English than the multilingual ones of the same size.
# Someone who dictates in another language picks another model in
# Preferences, which is exactly the kind of choice that belongs in a
# settings box rather than in code.
DEFAULT_MODEL = "base.en"

# The local models offered in the picker, smallest first, with what the
# choice actually costs. Whisper's own names are opaque — "small" is not
# small — so the picker shows these labels instead.
MODEL_CHOICES = [
    ("tiny.en",   "Tiny (English) — fastest, least accurate"),
    ("base.en",   "Base (English) — fast, good for dictation"),
    ("small.en",  "Small (English) — slower, more accurate"),
    ("medium.en", "Medium (English) — slow, very accurate"),
    ("tiny",      "Tiny (any language)"),
    ("base",      "Base (any language)"),
    ("small",     "Small (any language)"),
    ("medium",    "Medium (any language)"),
    ("large-v3",  "Large v3 (any language) — most accurate, needs a lot of memory"),
]

# Roughly what each local model costs to download, so the choice is an
# informed one rather than a surprise on a metered connection.
MODEL_SIZES = {
    "tiny.en": "75 MB", "tiny": "75 MB",
    "base.en": "145 MB", "base": "145 MB",
    "small.en": "480 MB", "small": "480 MB",
    "medium.en": "1.5 GB", "medium": "1.5 GB",
    "large-v3": "3 GB",
}

_USER_AGENT = "Hearthkin (+local kin chat; dictation)"
_ELEVENLABS_BASE = "https://api.elevenlabs.io"


class SttError(RuntimeError):
    """Transcription could not be done. Carries a message meant to be
    shown to a person as-is: what failed, and what they could change."""


# ─── Where does this model live? ───────────────────────────────────

def route_for(model="", host=""):
    """Decide which route a (model, host) pair names. Pure; no I/O.

    The order matters. A model naming its own provider wins over a host,
    because `elevenlabs/scribe_v1` means ElevenLabs no matter what else
    is filled in — a leftover address in a box the person has moved away
    from must not silently redirect their audio somewhere else."""
    m = (model or "").strip()
    if m.lower().startswith(ELEVENLABS_PREFIX):
        return ROUTE_ELEVENLABS
    if (host or "").strip():
        return ROUTE_SERVER
    return ROUTE_LOCAL


# The two endpoint shapes a transcription server might offer. Servers
# that copied OpenAI's interface use the first; whisper.cpp's own server
# uses the second, and does NOT have the first. Which one a machine
# speaks is not something anyone should have to know, so a bare address
# tries both.
#
# This exists because it was got wrong: the first version supported only
# the OpenAI path, and the whisper.cpp server already running on this
# household's other machine answered 404 to it while working perfectly
# on /inference. "Point it at your own machine" would have failed on the
# very machine it was written for.
_ENDPOINT_PATHS = ("/v1/audio/transcriptions", "/inference")

# Which path a given host turned out to speak, so the fallback is paid
# once rather than on every dictation.
_endpoints = {}
_endpoints_lock = threading.Lock()


def _base_url(host):
    base = (host or "").strip().rstrip("/")
    if not base:
        return ""
    if "://" not in base:
        base = "http://" + base
    return base


def candidate_endpoints(host):
    """Every URL worth trying for a host, best first.

    An address that already names an endpoint is taken at its word --
    someone who pasted a full URL out of a server's own documentation
    has told us exactly what they mean. Anything else is a base address
    and both shapes get a turn."""
    base = _base_url(host)
    if not base:
        return []
    if "/audio/transcriptions" in base or base.endswith("/inference"):
        return [base]
    with _endpoints_lock:
        known = _endpoints.get(base)
    if known:
        return [known]
    stem = base[:-3] if base.endswith("/v1") else base
    return [stem + p for p in _ENDPOINT_PATHS]


def normalise_host(host):
    """The endpoint a host will be tried at first.

    A bare host, a trailing slash, a `/v1`, or the full endpoint copied
    out of a server's own README all have to mean the same thing.
    Getting this wrong looks like the server being broken, and the
    person who pasted the URL has no way to tell the difference."""
    found = candidate_endpoints(host)
    return found[0] if found else ""


def models_endpoint(host):
    """The `/v1/models` listing URL for a host, so the picker can show
    what that machine actually has rather than asking someone to type a
    name and hope."""
    base = (host or "").strip().rstrip("/")
    if not base:
        return ""
    if "://" not in base:
        base = "http://" + base
    base = base.split("/v1/audio/transcriptions")[0].rstrip("/")
    if base.endswith("/v1"):
        return base + "/models"
    return base + "/v1/models"


def describe(model="", host="", loaded=None):
    """One line naming where transcription will happen, for a settings
    screen or a status message. Written to be read aloud."""
    route = route_for(model, host)
    name = (model or DEFAULT_MODEL).strip()
    if route == ROUTE_ELEVENLABS:
        return f"ElevenLabs ({name[len(ELEVENLABS_PREFIX):] or 'scribe_v1'})"
    if route == ROUTE_SERVER:
        return f"{name} on {(host or '').strip()}"
    where = ""
    if loaded:
        where = (" on your graphics card" if loaded[0] == "cuda"
                 else " on your processor")
    return f"{name} on this computer{where}"


# ─── Loaded-model cache ────────────────────────────────────────────
#
# Module level, keyed by (model, device, compute). Loading costs a
# second or two; the first import in the process costs far more. Doing
# either again per dictation is the difference between this feeling
# instant and feeling broken.

_models = {}
_models_loaded_as = {}
_models_lock = threading.Lock()


def available_locally():
    """True if faster-whisper can be imported in this process.

    Uses find_spec rather than a real import: this is called to decide
    whether to show the Talk button, which happens on the UI thread on
    every kin switch, and a real import there would freeze the window
    for half a minute the first time."""
    try:
        import importlib.util
        return importlib.util.find_spec("faster_whisper") is not None
    except Exception:
        return False


def downloaded_models():
    """Names of Whisper models already on this machine, so a picker can
    say which ones are ready and which would need downloading.

    Reads the Hugging Face cache directory names directly rather than
    importing anything — it is a listing, and it should not cost a
    model load. Returns an empty set if the cache cannot be read, which
    reads correctly as "we don't know", not as "none"."""
    import os
    from pathlib import Path

    roots = []
    env = os.environ.get("HF_HUB_CACHE") or os.environ.get("HF_HOME")
    if env:
        p = Path(env)
        roots.append(p if p.name == "hub" else p / "hub")
    roots.append(Path.home() / ".cache" / "huggingface" / "hub")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "huggingface" / "hub")

    found = set()
    for root in roots:
        try:
            if not root.is_dir():
                continue
            for entry in root.iterdir():
                name = entry.name
                # Systran publishes the CTranslate2 conversions that
                # faster-whisper downloads by default.
                prefix = "models--Systran--faster-whisper-"
                if name.startswith(prefix):
                    found.add(name[len(prefix):])
        except Exception:
            continue
    return found


def load_model(name=None, device="auto", compute_type="auto"):
    """Return a loaded faster-whisper model, cached per
    (name, device, compute_type).

    device "auto" prefers the GPU and falls back to the CPU on ANY
    failure. A GPU with no room left because a local language model is
    resident is the ordinary state of a machine that runs its own
    models — it is not a fault, and it should cost a slower
    transcription rather than an error message."""
    name = name or DEFAULT_MODEL
    key = (name, device, compute_type)
    with _models_lock:
        cached = _models.get(key)
    if cached is not None:
        return cached

    # Before faster-whisper is imported, not after: its audio module
    # imports PyAV at the top, and PyAV is where the GPL FFmpeg lives.
    _install_av_stub()
    try:
        from faster_whisper import WhisperModel
    except Exception as e:
        raise SttError(
            "Local speech recognition is not installed on this machine "
            "(faster-whisper). Either install it, or point dictation at a "
            "Whisper server or ElevenLabs under Preferences → Dictation. "
            f"The import said: {e}"
        ) from e

    if device == "auto":
        attempts = [("cuda", "float16"), ("cpu", "int8")]
    else:
        ct = compute_type
        if ct == "auto":
            ct = "float16" if device == "cuda" else "int8"
        attempts = [(device, ct)]

    last_err = None
    for dev, ct in attempts:
        try:
            model = WhisperModel(name, device=dev, compute_type=ct)
        except Exception as e:
            last_err = e
            continue
        with _models_lock:
            _models[key] = model
            _models_loaded_as[key] = (dev, ct)
        return model

    raise SttError(
        f"Could not load the speech model {name!r}: {last_err}"
    )


def loaded_as(name=None, device="auto", compute_type="auto"):
    """Return ("cuda", "float16") etc. for an already-loaded model, or
    None. Lets the self-check report what actually happened rather than
    what was asked for — "auto" resolving to the CPU is the fact worth
    showing someone, and it is invisible otherwise."""
    with _models_lock:
        return _models_loaded_as.get((name or DEFAULT_MODEL, device, compute_type))


def preload(settings):
    """Load the configured model now so the first dictation does not pay
    for it. Never raises: a warm-up that fails leaves the cost exactly
    where it already was."""
    s = dict(settings or {})
    # Only a model that lives HERE has anything to warm up. A model on
    # another machine is that machine's business, and pinging it at
    # startup would be a network call nobody asked for.
    if route_for(s.get("model"), s.get("host")) != ROUTE_LOCAL:
        return
    try:
        load_model(
            s.get("model") or DEFAULT_MODEL,
            s.get("device") or "auto",
            s.get("compute") or "auto",
        )
    except Exception:
        pass


def reset_cache():
    """Drop every loaded model. For the settings dialog, after a change
    that makes the loaded one no longer the configured one."""
    with _models_lock:
        _models.clear()
        _models_loaded_as.clear()


# ─── Decoding audio without FFmpeg ─────────────────────────────────
#
# faster-whisper will happily decode any media file for you, but it does
# that through PyAV, and the PyAV wheel ships an FFmpeg built with
# libx264 and libx265. Both are GPL, which makes that FFmpeg build GPL
# rather than the LGPL one most applications deliberately choose — and
# this project is CC0. Shipping it would drag copyleft obligations onto
# every release, including an offer of corresponding source for FFmpeg.
#
# None of which is necessary, because the only audio ever transcribed
# here is a WAV recorded a few lines away by our own microphone code.
# Decoding it with the standard library and handing Whisper a plain
# array skips faster-whisper's decode step entirely, so no FFmpeg code
# is reached, ~70 MB leaves the build, and the licence question does not
# arise.
#
# PyAV is imported at the top of faster_whisper/audio.py regardless, so
# the import still has to succeed. `_install_av_stub` satisfies it. The
# stub is installed in EVERY run, development included, deliberately:
# the alternative is stubbing only in the packaged build, which would
# make the shipped path the one nobody ever tests.

_AV_STUB_MESSAGE = (
    "Hearthkin decodes its own audio and does not ship FFmpeg, so "
    "faster-whisper's file-decoding path is not available here. This "
    "means something asked it to open an audio file directly instead of "
    "handing it samples. See _install_av_stub in stt.py."
)


def _make_av_stub():
    """Build a stand-in for PyAV that behaves like a real module.

    It must be a genuine module object with a real __spec__: other
    packages probe for PyAV with importlib.util.find_spec, which reads
    dunder attributes, and a stub that raises on those turns a harmless
    "is PyAV here?" check into a crash. Found exactly that way — the
    first version raised on av.__spec__ and broke the import chain three
    packages away.

    So dunders behave, and only a real API access complains — because a
    real API access means something is trying to decode a file, which is
    the thing this stub exists to make impossible."""
    import importlib.machinery
    import types

    mod = types.ModuleType("av")
    mod.__spec__ = importlib.machinery.ModuleSpec("av", loader=None)
    mod.__file__ = __file__
    mod.__path__ = []
    mod.__version__ = "0-stub"
    mod.__all__ = []

    def _blocked(name):
        raise RuntimeError(_AV_STUB_MESSAGE + f" (asked for av.{name})")

    mod.__getattr__ = _blocked        # PEP 562: only for names not found
    return mod


def _install_av_stub():
    """Put the stub in place before faster-whisper is imported.

    Idempotent, and it never replaces a stub with a second stub."""
    import sys
    existing = sys.modules.get("av")
    if getattr(existing, "__version__", None) == "0-stub":
        return
    sys.modules["av"] = _make_av_stub()


def wav_to_array(wav_bytes):
    """Turn WAV bytes into the mono float32 samples Whisper wants.

    Standard library plus numpy; nothing that could pull in a media
    library. Whisper works at 16 kHz, which is also exactly what the
    microphone records at, so the common path is a straight conversion
    with no resampling at all."""
    import wave as _wave

    try:
        with _wave.open(io.BytesIO(wav_bytes), "rb") as w:
            channels = w.getnchannels()
            width = w.getsampwidth()
            rate = w.getframerate()
            raw = w.readframes(w.getnframes())
    except Exception as e:
        raise SttError(f"That audio could not be read as a WAV file: {e}") from e

    try:
        import numpy as np
    except Exception as e:                                # pragma: no cover
        raise SttError(f"numpy is needed to read audio: {e}") from e

    if width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        # 8-bit WAV is unsigned, centred on 128.
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise SttError(
            f"That WAV uses {width * 8}-bit samples, which this reads no "
            "way of. Re-save it as 16-bit, or send it to a transcription "
            "model on another machine, which decodes it itself."
        )

    if channels > 1:
        usable = (len(data) // channels) * channels
        data = data[:usable].reshape(-1, channels).mean(axis=1)

    if rate != 16000 and len(data):
        # Linear resampling. Crude, and good enough for speech at the
        # rates a microphone or a phone produces — Whisper is robust to
        # far worse. The recordings this app makes are already 16 kHz, so
        # this runs only for a file somebody brought from elsewhere.
        count = max(1, int(round(len(data) * 16000.0 / rate)))
        data = np.interp(
            np.linspace(0.0, len(data) - 1.0, count),
            np.arange(len(data), dtype=np.float64),
            data,
        ).astype(np.float32)

    return np.ascontiguousarray(data, dtype=np.float32)


# ─── WAV helper ────────────────────────────────────────────────────

def wav_from_pcm(pcm_bytes, samplerate=16000, channels=1, sampwidth=2):
    """Wrap raw PCM in a WAV header. Every backend below takes a real
    audio file rather than bare samples."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(samplerate)
        w.writeframes(pcm_bytes)
    return buf.getvalue()


# ─── The dispatch ──────────────────────────────────────────────────

def transcribe(wav_bytes, settings=None, get_api_key=None):
    """Transcribe WAV bytes with whichever transcription model the
    settings name, wherever it lives.

    `settings` is the app-level dictation config (see
    DEFAULT_CONFIG["dictation"]). None means all defaults, which is the
    local model.

    `get_api_key` is a callable returning the ElevenLabs key; only the
    ElevenLabs route calls it, so the other two never need one to exist.
    Passing the resolver rather than the key means a key edited in
    Preferences applies to the very next dictation."""
    if not wav_bytes:
        raise SttError(
            "Empty recording — press Stop talking after speaking, not before."
        )
    s = dict(settings or {})
    route = route_for(s.get("model"), s.get("host"))
    if route == ROUTE_LOCAL:
        return _local_whisper(wav_bytes, s)
    if route == ROUTE_SERVER:
        return _remote_transcription(wav_bytes, s)
    return _elevenlabs(wav_bytes, s, get_api_key)


# ─── Route 1: the model on this computer ───────────────────────────

def _local_whisper(wav_bytes, s):
    name = s.get("model") or DEFAULT_MODEL
    device = s.get("device") or "auto"
    compute = s.get("compute") or "auto"
    try:
        return _run_local(wav_bytes, s, load_model(name, device, compute))
    except SttError:
        raise
    except Exception as e:
        # "Automatic" has to mean automatic all the way through, not only
        # at load time. A graphics card can accept the model and then
        # fail on the first real work — the packaged build does exactly
        # this, because it does not ship the CUDA libraries and the model
        # loads happily before anything notices. Falling back only on a
        # load failure left that as a hard error with a message naming a
        # DLL, which is nothing anybody can act on.
        if device != "auto" or loaded_as(name, device, compute) != ("cuda", "float16"):
            raise SttError(f"Speech recognition failed: {e}") from e
        with _models_lock:
            _models.pop((name, device, compute), None)
            _models_loaded_as.pop((name, device, compute), None)
        try:
            model = load_model(name, "cpu", "int8")
        except SttError:
            raise
        except Exception as e2:
            raise SttError(f"Speech recognition failed: {e2}") from e2
        with _models_lock:
            _models[(name, device, compute)] = model
            _models_loaded_as[(name, device, compute)] = ("cpu", "int8")
        return _run_local(wav_bytes, s, model)


def _run_local(wav_bytes, s, model):
    """One attempt on one loaded model.

    Deliberately does NOT wrap failures: _local_whisper needs to see the
    raw error to decide whether a fall back to the processor is worth
    trying, and a wrapped one would look like a decision already made."""
    lang = (s.get("language") or "").strip() or None
    # Samples, not a file object. Nothing is spooled to disk —
    # dictation is somebody speaking in their own room and should
    # not leave a file behind for anyone to find later — and passing
    # an array also skips faster-whisper's own decoder, which is the
    # part that would need FFmpeg. See wav_to_array above.
    segments, _info = model.transcribe(
        wav_to_array(wav_bytes),
        language=lang,
        beam_size=int(s.get("beam_size") or 5),
        # Voice-activity filtering trims the silence either side of a
        # click-to-start, click-to-stop recording. Without it Whisper
        # will happily invent a plausible sentence to fill leading
        # silence, which in dictation means words nobody said.
        vad_filter=bool(s.get("vad", True)),
        # Each dictation is its own utterance. Conditioning on the
        # previous one makes Whisper repeat itself when two are
        # recorded back to back.
        condition_on_previous_text=False,
    )
    return " ".join(seg.text.strip() for seg in segments).strip()


# ─── Route 2: the model on some other machine ──────────────────────

def _remote_transcription(wav_bytes, s):
    """POST the audio to an OpenAI-compatible transcription endpoint.

    This is the route that means "anywhere". It does not care whether
    the address is a spare box on the shelf, the machine that already
    runs the language models, or a hosted service — the interface is the
    same one, and that sameness is the point: one code path, one setting,
    no per-destination special cases to keep working."""
    urls = candidate_endpoints(s.get("host"))
    if not urls:
        raise SttError(
            "No address is set for the transcription model "
            "(Preferences → Dictation)."
        )
    fields = {"model": (s.get("model") or "").strip() or "whisper-1"}
    lang = (s.get("language") or "").strip()
    if lang:
        fields["language"] = lang
    # whisper.cpp's own server wants this and ignores "model"; servers
    # copying OpenAI's interface ignore this and want "model". Sending
    # both means one request works on either without asking anyone which
    # kind of server they are running.
    fields["response_format"] = "json"
    headers = {}
    token = (s.get("host_key") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    timeout = int(s.get("timeout_secs") or 120)
    last = None
    for i, url in enumerate(urls):
        try:
            data = _post_multipart(
                url, wav_bytes, fields, headers, timeout,
                label="The transcription server",
            )
        except SttError as e:
            # Fall through ONLY when the path itself is absent. Any other
            # failure came from the right endpoint, and retrying a
            # different one would replace a true error with a confusing
            # one.
            if "HTTP 404" in str(e) and i + 1 < len(urls):
                last = e
                continue
            raise
        base = _base_url(s.get("host"))
        if base and base != url:
            with _endpoints_lock:
                _endpoints[base] = url
        return (data.get("text") or "").strip()
    raise last if last else SttError("The transcription server did not answer.")


def list_server_models(host, host_key="", timeout=15):
    """Ask a host what transcription models it has, so the picker can
    show a real list instead of asking someone to type a name and hope.

    Returns a list of model ids. Raises SttError with something a person
    can act on — the usual failure here is an address that is almost
    right, and "connection refused" on its own does not say which half
    was wrong."""
    url = models_endpoint(host)
    if not url:
        raise SttError("No server address to ask.")
    req = urllib.request.Request(url)
    req.add_header("User-Agent", _USER_AGENT)
    if (host_key or "").strip():
        req.add_header("Authorization", f"Bearer {host_key.strip()}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # The machine answered -- it simply has no listing to give.
            # whisper.cpp's server is the common case: it runs the one
            # model it was started with. Saying "the address may be
            # wrong" here would send somebody off to re-check an address
            # that is perfectly correct.
            raise SttError(
                "That machine is reachable but does not offer a list of "
                "its models. This is normal — whisper.cpp's server runs "
                "the one model it was started with and ignores the name "
                "you give here, so you can leave it as it is."
            ) from e
        raise SttError(
            f"{url} answered HTTP {e.code}. The address may be right but "
            "the path wrong, or a key may be needed."
        ) from e
    except urllib.error.URLError as e:
        raise SttError(
            f"Could not reach {url} ({e.reason}). Check the machine is on, "
            "the server is running, and the address and port are right."
        ) from e
    except Exception as e:
        raise SttError(f"Could not ask {url} for its models: {e}") from e

    try:
        data = json.loads(raw)
    except Exception as e:
        raise SttError(
            f"{url} answered with something that was not a model list."
        ) from e
    rows = data.get("data") if isinstance(data, dict) else data
    out = []
    for row in rows or []:
        if isinstance(row, dict):
            mid = row.get("id") or row.get("name")
        else:
            mid = row
        if mid:
            out.append(str(mid))
    return out


# ─── Route 3: ElevenLabs Scribe ────────────────────────────────────

def _elevenlabs(wav_bytes, s, get_api_key):
    key = ""
    if get_api_key is not None:
        try:
            key = (get_api_key() or "").strip()
        except Exception:
            key = ""
    if not key:
        raise SttError(
            "No ElevenLabs API key is set. Add one under Preferences → "
            "Connections, or choose a transcription model that runs on "
            "this computer or on a machine of your own "
            "(Preferences → Dictation), which needs no key."
        )
    model = (s.get("model") or "").strip()
    if model.lower().startswith(ELEVENLABS_PREFIX):
        model = model[len(ELEVENLABS_PREFIX):]
    data = _post_multipart(
        f"{_ELEVENLABS_BASE}/v1/speech-to-text",
        wav_bytes,
        {"model_id": model or "scribe_v1"},
        {"xi-api-key": key},
        int(s.get("timeout_secs") or 120),
        label="ElevenLabs",
    )
    return (data.get("text") or "").strip()


# ─── Shared multipart upload ───────────────────────────────────────

def _post_multipart(url, wav_bytes, fields, headers, timeout,
                    label="Transcription"):
    """Hand-rolled multipart POST of one WAV plus some text fields,
    returning the decoded JSON body.

    Shared by both network routes because the shape is identical, and
    two copies of a hand-rolled multipart writer is two places to get
    the line endings wrong."""
    boundary = "----HearthkinDictationBoundaryQ8n2Xr"
    body = io.BytesIO()
    for name, value in (fields or {}).items():
        body.write(f"--{boundary}\r\n".encode())
        body.write(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        )
        body.write(str(value).encode())
        body.write(b"\r\n")
    body.write(f"--{boundary}\r\n".encode())
    body.write(
        b'Content-Disposition: form-data; name="file"; '
        b'filename="audio.wav"\r\n'
    )
    body.write(b"Content-Type: audio/wav\r\n\r\n")
    body.write(wav_bytes)
    body.write(b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(url, data=body.getvalue(), method="POST")
    req.add_header("Content-Type",
                   f"multipart/form-data; boundary={boundary}")
    req.add_header("User-Agent", _USER_AGENT)
    for k, v in (headers or {}).items():
        req.add_header(k, v)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise SttError(f"{label} answered HTTP {e.code}: {detail or e.reason}") from e
    except urllib.error.URLError as e:
        raise SttError(f"{label} could not be reached: {e.reason}") from e
    except Exception as e:
        raise SttError(f"{label} failed: {e}") from e

    try:
        return json.loads(raw)
    except Exception:
        # Some transcription servers answer text/plain when asked for the
        # default response format. Treat the body as the transcript
        # rather than failing on it.
        return {"text": raw}


# ─── Self-check, for the settings screen ───────────────────────────

def self_check(settings=None, get_api_key=None):
    """Prove the configured transcription model can actually do the job,
    and say so in words a person can act on. Returns (ok, message).

    Deliberately end-to-end: it transcribes a real (silent) WAV rather
    than only loading or pinging, because "it is reachable" and "it
    transcribes" are different claims and only the second is the one
    being asked about. The audio is silence, so an empty transcript is
    the correct answer and is not treated as a failure.

    The load and the transcription are timed SEPARATELY, and the message
    says which is which. Rolled together, a first run reports something
    like "one second of audio took seven seconds", which is both alarming
    and untrue — nearly all of it was a one-off startup cost the next
    dictation will not pay."""
    import time

    s = dict(settings or {})
    route = route_for(s.get("model"), s.get("host"))
    silence = wav_from_pcm(b"\x00\x00" * 16000, samplerate=16000)

    load_secs = None
    if route == ROUTE_LOCAL:
        name = s.get("model") or DEFAULT_MODEL
        device = s.get("device") or "auto"
        compute = s.get("compute") or "auto"
        already_warm = loaded_as(name, device, compute) is not None
        t0 = time.time()
        try:
            load_model(name, device, compute)
        except SttError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Dictation check failed: {e}"
        if not already_warm:
            load_secs = time.time() - t0

    t0 = time.time()
    try:
        transcribe(silence, s, get_api_key=get_api_key)
    except SttError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Dictation check failed: {e}"
    elapsed = time.time() - t0

    if route == ROUTE_LOCAL:
        name = s.get("model") or DEFAULT_MODEL
        where = loaded_as(name, s.get("device") or "auto",
                          s.get("compute") or "auto")
        on = "this computer"
        if where:
            on = ("your graphics card" if where[0] == "cuda"
                  else "your processor")
        msg = (
            f"Ready. The {name} model is transcribing on {on}, entirely "
            f"offline. It handled a second of audio in {elapsed:.1f} seconds."
        )
        if where and where[0] != "cuda":
            msg += (
                " That is the processor rather than a graphics card, which "
                "is normal and quite fast enough to dictate with."
            )
        if load_secs is not None:
            msg += (
                f" Getting the model ready took another {load_secs:.0f} "
                "seconds, but that is a one-off — it is loaded now, and "
                "Hearthkin normally does it in the background at startup."
            )
        return True, msg
    if route == ROUTE_SERVER:
        name = (s.get("model") or "").strip() or "whisper-1"
        return True, (
            f"Ready. {(s.get('host') or '').strip()} transcribed a second of "
            f"audio with {name} in {elapsed:.1f} seconds. Nothing is running "
            "on this computer for dictation."
        )
    return True, f"Ready. ElevenLabs answered in {elapsed:.1f} seconds."
