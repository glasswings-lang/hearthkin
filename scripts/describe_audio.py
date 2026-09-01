"""Describe an audio file with Qwen2-Audio-7B-Instruct (CPU).

Usage: python describe_audio.py <audio_path> [start_seconds]
Qwen2-Audio hears up to ~30s at a time, so we take a 30s window (from
start_seconds, default 0). For a long evolving drone, pass a start offset to
sample a different stretch.
"""
import sys
import time

import librosa
import torch
from transformers import (
    AutoProcessor, Qwen2AudioForConditionalGeneration, BitsAndBytesConfig,
)

audio_path = sys.argv[1]
start = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
model_id = "Qwen/Qwen2-Audio-7B-Instruct"

print(f"[{time.strftime('%H:%M:%S')}] loading model (slow part)...", flush=True)
t0 = time.time()
processor = AutoProcessor.from_pretrained(model_id)
have_cuda = torch.cuda.is_available()
if have_cuda:
    # 8-bit (int8) roughly halves memory to ~8-9 GB, split across the 4 GB GPU
    # and system RAM. fp32_cpu_offload lets layers that don't fit on the tiny
    # GPU ride in CPU RAM. Keeps the GPU budget under 4 GB (display uses some).
    quant = BitsAndBytesConfig(
        load_in_8bit=True, llm_int8_enable_fp32_cpu_offload=True)
    model = Qwen2AudioForConditionalGeneration.from_pretrained(
        model_id, quantization_config=quant,
        device_map="auto", max_memory={0: "3200MiB", "cpu": "12GiB"},
        low_cpu_mem_usage=True)
else:
    model = Qwen2AudioForConditionalGeneration.from_pretrained(
        model_id, torch_dtype=torch.bfloat16, low_cpu_mem_usage=True)
model.eval()
print(f"[{time.strftime('%H:%M:%S')}] model loaded in {time.time()-t0:.0f}s "
      f"(cuda={have_cuda})", flush=True)

sr = processor.feature_extractor.sampling_rate  # 16000
audio, _ = librosa.load(audio_path, sr=sr, offset=start, duration=30.0, mono=True)
print(f"loaded {len(audio)/sr:.1f}s of audio from {audio_path!r} (offset {start:.0f}s)", flush=True)

default_prompt = (
    "This is a piece of experimental / drone music, not speech. Listen closely "
    "and describe it in rich, sensory detail: its texture, its movement and how "
    "it evolves, its pitch and density, and the mood or feeling it carries. "
    "Describe what it actually sounds like."
)
prompt_text = sys.argv[3] if len(sys.argv) > 3 else default_prompt
print("QUESTION:", prompt_text, flush=True)
conversation = [
    {"role": "user", "content": [
        {"type": "audio", "audio_url": audio_path},
        {"type": "text", "text": prompt_text},
    ]},
]
text = processor.apply_chat_template(
    conversation, add_generation_prompt=True, tokenize=False)
inputs = processor(
    text=text, audio=audio, sampling_rate=sr, return_tensors="pt", padding=True)
# Sanity: the audio must actually have made it into the inputs, or the model is
# just hallucinating from the text prompt (the bug in the first run).
assert "input_features" in inputs, "AUDIO NOT ATTACHED — model would hallucinate"
print("audio attached: input_features shape =", tuple(inputs["input_features"].shape), flush=True)
if have_cuda:
    inputs = inputs.to("cuda:0")

print(f"[{time.strftime('%H:%M:%S')}] generating description...", flush=True)
t1 = time.time()
with torch.no_grad():
    out = model.generate(**inputs, max_new_tokens=300, do_sample=False)
out = out[:, inputs.input_ids.shape[1]:]
resp = processor.batch_decode(out, skip_special_tokens=True)[0].strip()
print(f"[{time.strftime('%H:%M:%S')}] generated in {time.time()-t1:.0f}s\n", flush=True)
print("=== WHAT QWEN2-AUDIO HEARS ===")
print(resp)
