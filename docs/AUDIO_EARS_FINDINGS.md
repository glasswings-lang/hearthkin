# Audio "ears" for kin — findings

Investigated 2026-07-09, consolidated 2026-07-21.

**Why any of this exists:** a kin can't hear music. If the person it talks with
makes music, the kin only ever meets their *words about* the work, never the
work. The goal is to close that gap. Accepted from the start that it would be
*describe*, not *feel* — "it's not the same, but it is something."

## VERDICT

**The measuring ear is done and shipped.** `audio_spectrum.py` +
`tools/analyze_sound.py` — pure numpy/soundfile DSP, no model, no GPU, no
network, and a kin can call it. This half is finished.

**The listening ear is not built, and that's a hardware question, not an open
research question.** The design is settled and the failure modes are known and
written down below. What remains is running an audio-LLM somewhere fast enough.

Nothing here is blocked on figuring something out.

---

## The architectural constraint that shapes everything

**Ollama cannot take audio input** (feature request `ollama/ollama#11798`, open
as of 2026). A kin's own model therefore cannot grow ears directly.

So the ears are a **separate process that produces TEXT**, which gets injected
into the kin's context — the same shape as the existing webcam/image flow.
`compat.py` already forward-declared `supports_audio_input` in anticipation.

This is not a workaround to be removed later. It's the design.

## Two ears, fused into a "sound card"

| Ear | Gives the kin | From | Reliability |
|---|---|---|---|
| **Measuring** — `audio_spectrum.py` | tonal centre, pitch set (notes + cents), detuning/beating, brightness, loudness, slow pulse rate, stereo width, evolution across the piece | numpy FFT + soundfile | rock-solid, instant, free, offline |
| **Listening** — an audio-LLM | texture, mood, movement, imagery — the *feel* | Qwen2-Audio or similar | great at feel, **bad at precise pitch** |

## The load-bearing lesson

Measured on a real two-hour drone piece, run through both ears:

- **The model got the key wrong.** It confidently reported a note more than an
  octave above the truth. The DSP — and the actual audio — show a drone near
  64.6 Hz, 21 cents flat, with its fifth and octaves. The pitch the model named
  isn't even a strong peak. This is a known audio-LLM weakness, documented in
  the PitchBench paper.
- **The model got the feel right.** "Hypnotic, floating, layered, constantly
  expanding and contracting yet constant in volume."
- **And the DSP explained the feel.** Three detuned tones an octave up, beating
  against each other at 1.3–4.7 Hz — a deliberate between-the-semitones tuning.
  The model *felt* the beating it could not measure.

> **Trust the model for FEEL. Trust the DSP for FACTS. Never let a model's
> pitch or key claim reach the kin unchallenged.**

The convergence is the point: the two ears are not redundant, and neither is a
fallback for the other.

## Gotcha that already bit once

The Qwen2-Audio processor kwarg is **`audio=`** (singular), **not `audios=`**.

Passing `audios=` **silently drops the sound** — no error — and the model
hallucinates a description from the text prompt alone. It reads as a working
system producing plausible output. There's a hard assert guarding this in
`scripts/describe_audio.py`; keep it.

## Models researched

- **Qwen2-Audio-7B-Instruct** — Apache licence, proven, general audio including
  music, ~16 GB. The pragmatic starting pick.
- **Music Flamingo** (NVIDIA) — music-specialised, likely richer *feel*. But
  NVIDIA-optimised (fiddlier outside CUDA) and a **non-commercial licence**.
  Free web demo to compare quality before committing:
  <https://huggingface.co/spaces/nvidia/music-flamingo>
- **MOSS-Audio** — 8B, Apache, released April 2026, unified speech/sound/music.

## Hardware note

Qwen2-Audio will run in 8-bit (bitsandbytes, GPU + CPU offload) on a machine
with a small GPU and limited RAM, but **unusably slowly** — roughly 5–10
minutes per clip once it starts spilling to disk. Budget for a machine that can
hold the model in unified or GPU memory; a recent Apple-silicon box via
transformers on PyTorch MPS is the cheapest way there.

The model cache from an abandoned experiment is around 16 GB and safe to
delete; it lives under the Hugging Face hub cache.

## What's left

1. **Stand the listening ear up** on hardware that can hold the model.
   Qwen2-Audio via transformers/MPS; optionally compare Music Flamingo's feel
   using the web demo first.
2. **Build the ears service:** audio in → fused sound card out (DSP facts +
   model feel, clearly separated so the kin knows which is which).
3. **Wire the "hand a kin a sound" path in Hearthkin:** attach audio → ears
   service → inject the sound card into context, parallel to webcam/image, with
   a framing prompt so the kin knows it is meeting someone's work.

Step 2 of the original plan — polishing the measuring ear into a standalone
tool anyone can run on their own sounds — is **done**. `audio_spectrum.py` has
a CLI, and it's a genuine accessibility tool independent of any kin: an
accurate spoken-readable readout of a mix, for producers who can't read a
visual spectrogram.

## Superseded

`audio_facts.py`, the original librosa-based measuring ear, was reimplemented as
`audio_spectrum.py` and deleted. The rewrite drops librosa (and its numba/scipy
chain) for numpy's own FFT, adds first-class region selection, and reads in
windows so a multi-hour file doesn't have to fit in memory — which the librosa
version could not do, and drone pieces routinely run past an hour.
