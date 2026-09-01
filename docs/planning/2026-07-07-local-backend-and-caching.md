# Local backend + prefill-caching — scoping pass (2026-07-07)

Triggered by Sage (gemma4:31b on the Mac, M4 Pro / 64 GB) taking **~11 minutes**
per reply — measured as a full **23,332-token re-prefill every turn** on a *warm*,
resident model. The operator asked whether moving off Ollama (to llama.cpp `llama-server`
or MLX) would fix it. This is the answer.

## TL;DR — do NOT migrate. Diagnose the cache-bust instead.

The Mac is running **Ollama 0.30.10**. Everything a migration would chase —
MLX-speed inference and modern prompt-cache reuse — **already shipped in Ollama
0.19 (2026-03-30)** and is in this build:

- Ollama switched its Apple-Silicon compute backend from llama.cpp Metal to **MLX**.
- KV-cache overhaul: **cross-conversation prefix reuse** ("previously the cache was
  discarded per conversation; now shared prefixes are reused"), **prefix-aware
  eviction** (shared prefixes survive when branches get evicted), snapshotting.

So the backend is *capable*. The 11-minute re-prefill means something is **defeating**
that cache every turn — and llama.cpp / MLX have the **same** cache-busting rules
("any change in the system-prompt prefix forces full reprocessing of the entire
context"). A migration would inherit the bug at large cost. Wrong tool.

## The two things that actually defeat a warm cache (chase these)

1. **Cross-model contention / eviction (most likely).** One Ollama serves ~6 kin on
   *different* models (`NUM_PARALLEL=2`). Sage's `keep_alive=-1` pins gemma's
   *weights*, but when another kin's model (Ash/Brook/Finch) loads between Sage's
   turns — or a cron fires (there was an Sage cron at 09:10) — Sage's **KV cache**
   (the prefill state) gets evicted. Prefix-aware eviction only protects *shared*
   prefixes; different kin have different system prompts, so nothing is shared to
   protect. Next Sage turn → cold prefix → full 23k re-prefill.
   - **Test (5 min, decisive):** two Sage turns back-to-back with *nothing else*
     touching the Mac (pause crons, no Telegram to other kin). If turn 2's
     `prefill=` time collapses to seconds → contention/eviction confirmed.

2. **A per-turn prefix-buster in Hearthkin's prompt.** llama.cpp/MLX/Ollama all match
   the token prefix byte-for-byte; anything that changes near the *front* of the
   prompt each turn invalidates the whole thing. Known-safe today: recall block is
   in the *tail* (last user turn), timestamps are user-turn-only. But this must be
   *verified*, not assumed — a stray dynamic value in the system block would produce
   exactly this symptom.
   - **Test:** capture two consecutive Sage prompts, diff the prefix. Any difference
     before the final user turn is the culprit. (Needs a debug dump of the built
     `messages` — small addition.)

If it's #1, fixes are free-to-cheap: reduce concurrent models / stagger crons /
dedicate the Mac to the active kin. If it's #2, it's a small prompt-ordering fix.
**Either way, no migration.**

## Is MLX even active?

0.19 shipped MLX as *preview*. On 0.30.10 it may be default on Apple Silicon or may
still need enabling. Worth confirming the box is actually on the MLX path (not the
legacy llama.cpp Metal backend) — that's a free speed lever independent of caching.
Can't confirm remotely; check on the Mac.

## Migration options — assessed, and why they lose right now

If diagnosis somehow shows Ollama genuinely can't hold the cache AND contention
can't be reduced, here's the honest cost/benefit. Both `llama-server` and MLX expose
OpenAI-compatible servers, so Hearthkin's existing OpenRouter path could be adapted —
but the **blast radius is large** (inventory below).

- **llama.cpp `llama-server`.** Same GGUF models. In-memory prefix reuse is automatic
  (`--cache-reuse`, up to ~93% TTFT reduction on cached prefixes). Its ONE edge over
  modern Ollama: **`--slot-save-path` disk save/restore** (restore ~7× faster than
  cold re-prefill: 1.4 s vs 9.9 s on a 5k chat) — which *would* survive eviction.
  BUT: (a) it is **not automatic** — Hearthkin must drive the save/restore API per
  kin around every turn (real new subsystem); (b) `llama-server` is **one model per
  instance** — supporting ~6 kin on different models means orchestrating multiple
  server processes or eating model-swap latency, i.e. **re-implementing the
  multi-model management Ollama gives for free.**
- **MLX (`mlx_lm.server`).** Faster for <14B models, but **converges with llama.cpp at
  27B+** because it's memory-bandwidth-bound — and Sage is 31B, so **the speed edge
  largely evaporates for your models.** Cross-request cache reuse is **less mature**
  than llama.cpp's slots ("KV caches reused only by the generation path that created
  them"). Different model format (re-pull everything). Weakest fit for this problem.

**Verdict:** migration buys, at best, disk-cache-survives-eviction (llama-server) —
achievable more cheaply by reducing contention — while *costing* the multi-model
management Ollama already does. Net negative today.

## Blast radius (if we ever DO migrate — for reference)

Every surface (desktop stream + tool-loop, rooms, Telegram DM/group/tool-loop, 3×
cron, distill, consolidate, mini-chat) funnels through `llm_backend.chat()`, so a
new backend is concentrated but touches all of these `llm_backend.py` seams:
`_chat_ollama_stream`, `_chat_ollama_blocking`, `_ollama_chat_raw`,
`_ollama_chat_callable`, `embed_texts`, `list_ollama_models`, `_ollama_show_raw`
(+ the 4 capability probes in `model_utils.py`), `pull_ollama_model`,
`set_ollama_keep_alive`, the timeout/watchdog helpers. Plus UI: `model_browser.py`,
`dialogs/{edit_kin,model_options,ollama_machines}.py`. **Good news:** host resolution
(`resolve_kin_ollama_host`, per-kin `ollama_host_name`), keep-alive, and watchdog
config are already backend-agnostic. Capability probing (`/api/show` → tools/vision/
thinking/context-length) is the fiddliest to re-create — no clean llama-server analog.

## Recommended sequence

1. **Confirm the cause** — back-to-back Sage test (contention) + prompt-prefix diff
   (prefix-buster). The `prefill=` logging added today reads out the result directly.
2. **If contention:** reduce concurrent models / stagger crons / one-model-at-a-time
   on the Mac while a kin is active. Free.
3. **If prefix-buster:** fix prompt ordering. Small.
4. **Confirm MLX is actually the active Ollama backend** on the Mac. Free speed check.
5. **Migration stays shelved** unless 1–4 prove Ollama structurally can't hold the
   cache under real multi-kin load — a bar it probably won't hit.
