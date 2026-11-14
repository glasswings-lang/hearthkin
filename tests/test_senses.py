# SPDX-License-Identifier: CC0-1.0
"""Ears and eyes for a kin: the listen and look tools (senses.py).

What is pinned here:

  * Which stretches of a sound go out: a short piece whole, a window when the
    kin asks for one, a few labelled stretches across a long piece.
  * The shape each kind of model is sent. A hosted model gets MP3 in an
    `input_audio` block and pictures as `image_url`; a local Ollama model
    gets WAV and pictures in the message's `images` field. Checked on the
    request that actually reaches the wire, through the real llm_backend.chat.
  * The bug found while building this (13 Nov 2026): llm_backend stripped the
    `images` field from every message before sending, so a local kin whose
    model could see was never shown a picture, attachments and webcam shots
    included. The positive control proves this check fails on the old
    field list.
  * No model chosen: the kin is told plainly, nothing is sent.
  * Remote surfaces confine paths for listen, look and analyze_sound, as they
    do for read_file.
  * The tools are registered and sit in the read bucket.

Run: python tests/test_senses.py
"""

import base64
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="senses-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import numpy as np            # noqa: E402
import soundfile as sf        # noqa: E402

import senses                 # noqa: E402
import llm_backend            # noqa: E402
import tools as kin_tools     # noqa: E402
from tools._buckets import BUCKETS  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="senses-files-"))


def _tone(path, secs, sr=44100, channels=2):
    t = np.arange(int(secs * sr)) / sr
    wave = (0.2 * np.sin(2 * np.pi * 220 * t)).astype("float32")
    data = np.stack([wave] * channels, axis=1)
    sf.write(str(path), data, sr)
    return path


SHORT = _tone(TMP / "short.wav", 10)
LONG = _tone(TMP / "long.flac", 600, sr=8000, channels=1)

PNG = TMP / "dot.png"
# A real 1x1 PNG.
PNG.write_bytes(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA"
    "hKmMIQAAAABJRU5ErkJggg=="))
NOT_PNG = TMP / "fake.png"
NOT_PNG.write_bytes(b"this is not a picture at all")


print("--- which stretches go out ---")

check("a short piece goes whole", senses.plan_clips(120) == [(0.0, 120.0)])
survey = senses.plan_clips(7200)
check("a two-hour piece is sampled, not sent whole",
      len(survey) == len(senses.SURVEY_POINTS)
      and all(n == senses.SURVEY_CLIP_SECS for _, n in survey))
check("...from across the whole piece, in order",
      survey[0][0] < 600 and survey[-1][0] > 6000
      and [s for s, _ in survey] == sorted(s for s, _ in survey))
check("...and no stretch runs past the end",
      all(s + n <= 7200 for s, n in survey))
check("a window can be asked for in minutes:seconds",
      senses.plan_clips(7200, "31:00", 90) == [(1860.0, 90.0)])
check("a window is capped",
      senses.plan_clips(7200, 0, 10000) == [(0.0, senses.MAX_WINDOW_SECS)])
check("a window near the end stops at the end",
      senses.plan_clips(100, "1:30", 60) == [(90.0, 10.0)])
check("a start past the end sends nothing", senses.plan_clips(100, 200) == [])
check("times read as seconds, m:ss and h:mm:ss",
      senses.parse_time("90") == 90 and senses.parse_time("1:30") == 90
      and senses.parse_time("1:02:03") == 3723
      and senses.parse_time("") is None and senses.parse_time("soon") is None)


print("--- what reaches the wire ---")

sent = []


class _FakeResp:
    def __init__(self, body):
        self._b = json.dumps(body).encode()
        self.status = 200

    def read(self, *a):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_ollama_callable(ollama_host=None, request_timeout_secs=None):
    def chat_fn(**kwargs):
        sent.append(("ollama", kwargs.get("messages")))
        return {"message": {"role": "assistant", "content": "a low hum"},
                "done": True, "prompt_eval_count": 10, "eval_count": 3}
    return chat_fn


def _fake_or_blocking(model, messages, options, think_effort, tools, cache,
                      show_thinking=True, cache_ttl="auto",
                      provider_routing=None):
    sent.append(("hosted", messages))
    return llm_backend.ChatResult(content="a low hum", thinking="",
                                  tool_calls=[], usage={})


_orig = (llm_backend._ollama_chat_callable, llm_backend._chat_openrouter_blocking,
         llm_backend.is_hosted_model, senses._is_hosted)
llm_backend._ollama_chat_callable = _fake_ollama_callable
llm_backend._chat_openrouter_blocking = _fake_or_blocking
try:
    hosted_name = "openrouter/test/listener"
    is_hosted = (lambda m: m == hosted_name)
    llm_backend.is_hosted_model = is_hosted
    senses._is_hosted = is_hosted
    _orig_dispatch = llm_backend._is_openrouter_model
    llm_backend._is_openrouter_model = is_hosted

    sent.clear()
    out = senses.hear(SHORT, "", agent_name="Bracken", model=hosted_name)
    kind, msgs = sent[-1] if sent else (None, [])
    blocks = msgs[-1]["content"] if msgs and isinstance(msgs[-1]["content"], list) else []
    audio = [b for b in blocks if b.get("type") == "input_audio"]
    check("a hosted listener is sent the sound as an input_audio block",
          kind == "hosted" and len(audio) == 1)
    _mp3 = base64.b64decode(audio[0]["input_audio"]["data"]) if audio else b""
    check("...as MP3", audio and audio[0]["input_audio"]["format"] == "mp3"
          and (_mp3[:3] == b"ID3" or (_mp3[:1] == b"\xff" and _mp3[1] & 0xE0 == 0xE0)))
    check("the kin is told whose impression it is and what was heard",
          out.startswith("[heard by " + hosted_name) and "a low hum" in out)

    sent.clear()
    senses.hear(LONG, "", agent_name="Bracken", model=hosted_name)
    blocks = sent[-1][1][-1]["content"] if sent else []
    labels = [b["text"] for b in blocks if b.get("type") == "text"
              and b["text"].startswith("Clip starting at")]
    check("a long piece goes as several labelled stretches",
          len([b for b in blocks if b.get("type") == "input_audio"]) == len(senses.SURVEY_POINTS)
          and len(labels) == len(senses.SURVEY_POINTS))

    sent.clear()
    senses.hear(SHORT, "is it low?", agent_name="Bracken", model="localear:latest")
    kind, msgs = sent[-1] if sent else (None, [])
    imgs = msgs[-1].get("images") if msgs else None
    check("a local listener gets the sound in the images field, which "
          "reaches the wire", kind == "ollama" and isinstance(imgs, list) and len(imgs) == 1)
    raw = base64.b64decode(imgs[0]) if imgs else b""
    info = sf.info(__import__("io").BytesIO(raw)) if raw else None
    check("...as 16 kHz mono WAV",
          raw[:4] == b"RIFF" and info is not None
          and info.samplerate == 16000 and info.channels == 1)

    sent.clear()
    out = senses.see(PNG, "", agent_name="Bracken", model=hosted_name)
    blocks = sent[-1][1][-1]["content"] if sent else []
    check("a hosted looker is sent the picture as an image_url block",
          any(b.get("type") == "image_url"
              and b["image_url"]["url"].startswith("data:image/png;base64,")
              for b in blocks))

    sent.clear()
    senses.see(PNG, "", agent_name="Bracken", model="localeye:latest")
    kind, msgs = sent[-1] if sent else (None, [])
    check("a local looker gets the picture in the images field, on the wire",
          kind == "ollama" and bool(msgs and msgs[-1].get("images")))

    sent.clear()
    out = senses.see(NOT_PNG, "", agent_name="Bracken", model=hosted_name)
    check("a file that isn't a picture is refused before anything is sent",
          not sent and "isn't a picture" in out)

    sent.clear()
    out = senses.hear(SHORT, "", agent_name="Bracken", model="")
    check("with no model chosen nothing is sent and the kin is told why",
          not sent and "No listening model is chosen" in out)
    out = senses.see(PNG, "", agent_name="Bracken", model="")
    check("...for looking too", not sent and "No looking model is chosen" in out)
finally:
    (llm_backend._ollama_chat_callable, llm_backend._chat_openrouter_blocking,
     llm_backend.is_hosted_model, senses._is_hosted) = _orig
    llm_backend._is_openrouter_model = _orig_dispatch


print("--- the images field survives the send (13 Nov 2026 bug) ---")

msg = [{"role": "user", "content": "x", "images": ["AAAA"], "ts": "1"}]
kept = llm_backend._strip_extra_message_fields(msg)
check("pictures for a local model are no longer stripped",
      kept[0].get("images") == ["AAAA"])
check("...while storage bookkeeping still is", "ts" not in kept[0])
_old_fields = llm_backend._API_MESSAGE_FIELDS
llm_backend._API_MESSAGE_FIELDS = _old_fields - {"images"}
try:
    check("the check CAN fail: the old field list drops the pictures",
          "images" not in llm_backend._strip_extra_message_fields(msg)[0])
finally:
    llm_backend._API_MESSAGE_FIELDS = _old_fields


print("--- remote surfaces confine paths ---")

for name in ("listen", "look", "analyze_sound"):
    fn = kin_tools._REGISTRY[name]
    res = fn(str(SHORT), agent_name="Bracken", confine_paths=True)
    check(f"{name} refuses a path outside the kin's folder when confined",
          "absolute" in res.lower() or "outside" in res.lower()
          or "not allowed" in res.lower() or "refus" in res.lower())


print("--- registered, and where remote people can reach them ---")

check("listen and look are registered tools",
      {"listen", "look"} <= set(kin_tools.list_available()))
check("both sit in the read bucket, with the other readers",
      {"listen", "look"} <= BUCKETS["read"])
schemas, _ = kin_tools.load_tools(["listen", "look"], context={"agent_name": "Bracken"})
props = {s["function"]["name"]: set(s["function"]["parameters"]["properties"]) for s in schemas}
check("the model can't set its own path confinement",
      all("confine_paths" not in p and "agent_name" not in p for p in props.values()))
check("listen offers a question and a window",
      {"path", "question", "start", "length"} <= props.get("listen", set()))


print()
if _fails:
    print(f"test_senses: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_senses: all checks passed")
