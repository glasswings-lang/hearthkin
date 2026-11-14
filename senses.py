# SPDX-License-Identifier: CC0-1.0

"""Ears and eyes for a kin: send a sound or a picture to a model that can
take it in, and bring back what that model heard or saw.

The kin's own model does the thinking and the feeling. This only reports. A
kin whose model can't hear (most of them) or can't see gets a listener it can
ask, chosen per kin in its settings ("listen_model", and "look_model" for
pictures, which falls back to the listening model when left empty), so each
can be changed like any other model.

Why a separate model at all: on 13 Nov 2026 six models were given the same
two minutes of the operator's own music and the same questions, checked
against measurements of the audio. The kin's own chat model missed the
gliding lead line entirely and got the pulse wrong; the best listener caught
the glide, the chime, the noise bed, the key and the bass moving. Being good
company and being a good ear are different jobs, and the best model for one
is rarely the best for the other.

Two provider shapes, because the two kinds of model take sound differently:

  * A hosted (OpenRouter-shaped) model gets `input_audio` blocks (MP3) and
    `image_url` blocks in the message content.
  * A local Ollama model gets everything in the message's `images` field, as
    base64 with no prefix: WAV for sound (16 kHz mono) and the image bytes as
    they are. An `audio` field is silently ignored by Ollama, and the model
    then invents a confident transcription of sound it never got (found
    11 Nov 2026 on LFM3 Omni), so the field matters.

Long pieces are not sent whole unless asked for a window: the listener gets
a few stretches across the piece, each labelled with where it starts, so a
two-hour soundscape costs a few cents rather than a dollar and the answer
still covers its shape.
"""

import base64
import io

# How much sound goes out in one listen. A window the kin asks for is capped
# at MAX_WINDOW_SECS; a piece no longer than that goes whole; anything longer
# is sampled as SURVEY_CLIPS stretches of SURVEY_CLIP_SECS each.
MAX_WINDOW_SECS = 240.0
DEFAULT_WINDOW_SECS = 60.0
SURVEY_CLIP_SECS = 45.0
SURVEY_POINTS = (0.03, 0.35, 0.65, 0.93)

# Same ceiling the app uses for image attachments everywhere else.
MAX_IMAGE_BYTES = 8 * 1024 * 1024

_LISTEN_FRAME = (
    "You are the ears for {kin}, a companion AI that cannot hear. {kin} "
    "will read your answer and decide for itself what it makes of the sound, "
    "so report what is really there. Say plainly when you are unsure, and "
    "do not invent instruments, voices, words or events you cannot hear. "
    "Times, if you give any, are within the clips as labelled.")

_LISTEN_DEFAULT_Q = (
    "Describe what you hear: the sounds and textures, any instruments, voices "
    "or other sources, the mood, and how it changes.")

_LOOK_FRAME = (
    "You are the eyes for {kin}, a companion AI that cannot see. {kin} will "
    "read your answer and decide for itself what it makes of the picture, so "
    "report what is really there. Read any text exactly. Say plainly when you "
    "are unsure, and do not invent details you cannot see.")

_LOOK_DEFAULT_Q = (
    "Describe this picture: what and who is in it, where things are, colours "
    "and light, any text (read it exactly), and the feel of it.")


def _cfg(agent_name):
    try:
        from kin_persistence import load_agent_config
        return load_agent_config(agent_name) or {}
    except Exception:
        return {}


def listen_model_for(agent_name):
    """The kin's chosen listening model, or "" when none is set."""
    return str(_cfg(agent_name).get("listen_model", "") or "").strip()


def look_model_for(agent_name):
    """The kin's chosen looking model. Separate from listening because the
    best ear is rarely the cheapest eye, and a local model can see well and
    hear badly. Left empty, it uses the listening model."""
    cfg = _cfg(agent_name)
    return (str(cfg.get("look_model", "") or "").strip()
            or str(cfg.get("listen_model", "") or "").strip())


def _kin_ollama_host(agent_name):
    try:
        from kin_persistence import load_agent_config, resolve_kin_ollama_host
        cfg = load_agent_config(agent_name) or {}
        return resolve_kin_ollama_host(cfg.get("ollama_host_name", "")) or None
    except Exception:
        return None


def _clock(secs):
    secs = int(max(0, round(secs)))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_time(value):
    """Seconds from "90", "1:30", "1:02:03" or a number. None when blank or
    unreadable."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError:
        return None
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def plan_clips(duration, start=None, length=None):
    """Which stretches of a `duration`-second piece to send, as a list of
    (start_secs, length_secs). Pure, so it can be checked without audio."""
    duration = max(0.0, float(duration or 0))
    if duration <= 0:
        return []
    start, length = parse_time(start), parse_time(length)
    if start is not None:
        start = min(max(0.0, float(start)), duration)
        want = float(length) if length else DEFAULT_WINDOW_SECS
        want = max(1.0, min(want, MAX_WINDOW_SECS))
        return [(start, min(want, duration - start))] if duration > start else []
    if length:
        # A length with no start: from the beginning.
        want = max(1.0, min(float(length), MAX_WINDOW_SECS, duration))
        return [(0.0, want)]
    if duration <= MAX_WINDOW_SECS:
        return [(0.0, duration)]
    clips = []
    for frac in SURVEY_POINTS:
        s = min(max(0.0, duration * frac), duration - SURVEY_CLIP_SECS)
        clips.append((s, SURVEY_CLIP_SECS))
    return clips


def _read_clip(path, start, length):
    """(float32 frames x channels, samplerate) for one stretch of the file."""
    import soundfile as sf
    with sf.SoundFile(str(path)) as f:
        sr = f.samplerate
        f.seek(int(start * sr))
        data = f.read(frames=int(length * sr), dtype="float32", always_2d=True)
    return data, sr


def _mp3_bytes(data, sr):
    import soundfile as sf
    buf = io.BytesIO()
    if data.shape[1] > 2:
        data = data[:, :2]
    sf.write(buf, data, sr, format="MP3", subtype="MPEG_LAYER_III")
    return buf.getvalue()


def _wav16k_mono_bytes(data, sr):
    """16 kHz mono 16-bit WAV, the shape a local omni model was measured to
    take. A short moving average before decimating keeps the worst of the
    aliasing out; this is for description, not for listening to."""
    import numpy as np
    import soundfile as sf
    mono = data.mean(axis=1) if data.ndim == 2 else data
    target = 16000
    if sr != target and len(mono):
        ratio = sr / target
        if ratio > 1:
            k = max(1, int(round(ratio)))
            mono = np.convolve(mono, np.ones(k) / k, mode="same")
        n_out = int(len(mono) / ratio)
        xs = np.linspace(0, len(mono) - 1, n_out)
        mono = np.interp(xs, np.arange(len(mono)), mono).astype("float32")
    buf = io.BytesIO()
    sf.write(buf, mono, target, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def _ask(model, frame, question, media, agent_name, surface):
    """One model call. `media` is a list of (label, kind, bytes, fmt) where
    kind is "audio" or "image". Returns the model's text, or raises."""
    import llm_backend
    hosted = llm_backend.is_hosted_model(model)
    prompt = frame + "\n\n" + question
    if hosted:
        blocks = [{"type": "text", "text": prompt}]
        for label, kind, data, fmt in media:
            if label:
                blocks.append({"type": "text", "text": label})
            b64 = base64.b64encode(data).decode("ascii")
            if kind == "audio":
                blocks.append({"type": "input_audio",
                               "input_audio": {"data": b64, "format": fmt}})
            else:
                blocks.append({"type": "image_url",
                               "image_url": {"url": f"data:image/{fmt};base64,{b64}"}})
        messages = [{"role": "user", "content": blocks}]
    else:
        labels = [label for label, _, _, _ in media if label]
        text = prompt
        if labels:
            text += "\n\n" + "\n".join(
                f"Item {i + 1}: {lab}" for i, lab in enumerate(labels))
        messages = [{"role": "user", "content": text,
                     "images": [base64.b64encode(d).decode("ascii")
                                for _, _, d, _ in media]}]
    # Low reasoning, kept out of the answer: some listeners refuse a request
    # with reasoning switched off ("Reasoning is mandatory for this
    # endpoint"), and a little thought costs little next to the audio.
    extra = ({"think": True, "think_effort": "low", "show_thinking": False}
             if hosted else {"think": False})
    result = llm_backend.chat(
        model, messages, options={"temperature": 0.2, "num_predict": 1500},
        stream=False, kin_name=agent_name, surface=surface,
        ollama_host=None if hosted else _kin_ollama_host(agent_name), **extra)
    return (getattr(result, "content", "") or "").strip()


def hear(path, question="", start=None, length=None, *, agent_name="",
         model=None):
    """What the chosen listener hears in the audio file at `path`.

    Returns the listener's answer, headed by which stretches it heard, or a
    plain sentence saying why it couldn't."""
    model = model or listen_model_for(agent_name)
    if not model:
        return ("No listening model is chosen for you yet, so this can't be "
                "heard. Your operator can pick one in your settings, on the "
                "Tools tab: Listening model.")
    import soundfile as sf
    try:
        info = sf.info(str(path))
    except Exception as e:
        return f"That file couldn't be opened as sound: {e}"
    duration = float(info.duration or 0)
    clips = plan_clips(duration, parse_time(start), parse_time(length))
    if not clips:
        return "There's no sound in that stretch of the file."
    hosted = _is_hosted(model)
    media = []
    for s, n in clips:
        data, sr = _read_clip(path, s, n)
        if not len(data):
            continue
        label = (f"Clip starting at {_clock(s)}, {int(round(n))} seconds long:"
                 if len(clips) > 1 or s > 0 or n < duration - 0.5 else "")
        if hosted:
            media.append((label, "audio", _mp3_bytes(data, sr), "mp3"))
        else:
            media.append((label, "audio", _wav16k_mono_bytes(data, sr), "wav"))
    if not media:
        return "There's no sound in that stretch of the file."
    frame = _LISTEN_FRAME.format(kin=agent_name or "the kin")
    q = (question or "").strip() or _LISTEN_DEFAULT_Q
    if len(media) > 1:
        q += (" These are separate stretches from across one longer piece, "
              "in order; say how they differ.")
    try:
        answer = _ask(model, frame, q, media, agent_name, "listen")
    except Exception as e:
        return f"The listener ({model}) couldn't be reached: {e}"
    if not answer:
        return f"The listener ({model}) sent back nothing."
    heard = ", ".join(f"{_clock(s)} to {_clock(s + n)}" for s, n in clips)
    return (f"[heard by {model}: {heard} of {_clock(duration)}. This is that "
            f"model's impression, not a measurement; analyze_sound gives the "
            f"exact pitches and pulse.]\n\n{answer}")


def see(path, question="", *, agent_name="", model=None):
    """What the chosen looker sees in the image file at `path`."""
    model = model or look_model_for(agent_name)
    if not model:
        return ("No looking model is chosen for you yet, so this can't be "
                "seen. Your operator can pick one in your settings, on the "
                "Tools tab: Looking model.")
    from kin_persistence import _sniff_image_format
    try:
        data = open(str(path), "rb").read()
    except OSError as e:
        return f"That file couldn't be read: {e}"
    if len(data) > MAX_IMAGE_BYTES:
        return (f"That picture is {len(data) // (1024 * 1024)} MB; the most "
                f"that can be sent is 8 MB.")
    kind = _sniff_image_format(data)
    if not kind:
        return "That file isn't a picture this can read (JPEG, PNG, GIF or WebP)."
    fmt = "jpeg" if kind == "jpg" else kind
    frame = _LOOK_FRAME.format(kin=agent_name or "the kin")
    q = (question or "").strip() or _LOOK_DEFAULT_Q
    try:
        answer = _ask(model, frame, q, [("", "image", data, fmt)],
                      agent_name, "look")
    except Exception as e:
        return f"The looker ({model}) couldn't be reached: {e}"
    if not answer:
        return f"The looker ({model}) sent back nothing."
    return f"[seen by {model}. This is that model's description.]\n\n{answer}"


def _is_hosted(model):
    try:
        import llm_backend
        return llm_backend.is_hosted_model(model)
    except Exception:
        return True
