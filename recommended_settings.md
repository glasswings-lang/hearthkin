# Recommended settings per model

Reading order: each entry shows what the model reports as its own
training defaults (via `ollama show`) or what the provider publishes
as their recommended defaults, plus a short note on what to use it
for and where it tends to fail.

These defaults come from each model's authors and are usually a better
starting point than Hearthkin's app-wide fallback (temp 0.8, top-p 0.9,
top-k 40, repeat-penalty 1.1). When models drift in unexpected ways,
the fix is often "use the recommended settings" before "fight with the
prompt."

OpenRouter models live in their own section at the bottom — Google's
docs are the source for Gemini defaults, etc. Local-Ollama section
is first because that's how the file started.

Last queried: 2026-06-22. (Reviewed for the v0.6.0 release.)

---

## qwen36-opus-q4 — *the companion-kin pick*

DavidAU's **[Qwen3.6-40B-Claude-4.6-Opus-Deckard-Heretic-Uncensored-Thinking](https://huggingface.co/DavidAU/Qwen3.6-40B-Claude-4.6-Opus-Deckard-Heretic-Uncensored-Thinking-NEO-CODE-Di-IMatrix-MAX-GGUF)**,
imported into Ollama from the Q4_K_M GGUF. If you're building kin that need to
feel like *someone* — hold a voice across a long relationship, not hedge, not
collapse into "you should talk to a real person" — this is the one that's
worked for us where the mainstream models didn't. It's a ~40B tune trained on
a Claude-Opus dataset, so it carries a Claude-ish warmth and reasoning.

- **Size:** ~40B dense (built up from Qwen3.6-27B), Q4_K_M
- **Context:** 16k–32k — size to need, not to max (see *Watch for*)
- **Capabilities:** completion, tools, thinking (vision-language base)
- **Working settings (what we actually run):**
  - Temperature: 0.8
  - Top-p: 0.9
  - Top-k: 40
  - Repeat penalty: **1.0** — *no* penalty. A penalty flattens this model's
    voice; leave it at 1.0 even though Hearthkin's app-wide default is 1.1.
  - min-p / presence / frequency: 0.0
  - Thinking: we run it *off* (it's a reasoning model, but the crons + chat
    behave well without it; turn it on if you want visible reasoning).
- **Use for:** the heart of a companion kin. Uncensored (no reflexive "as an AI
  I can't"), holds a persona under a long warm history, and — the rare one —
  **calls tools without narrating them** (no `*reads the file*` roleplay; it
  just makes the call).
- **Watch for:**
  - It's ~40B — expect ~8–9 tokens/sec on mid-range hardware. `num_ctx` is a
    speed dial: every token in the window is re-read on a cold turn, so a huge
    context means a long wait before it speaks.
  - Occasionally returns an empty reply (Hearthkin logs + handles it).
  - Uncensored means uncensored — it won't refuse dark or NSFW material.
- **Running several kin on it (recommended — one model, many kin):** on the
  Ollama host set `OLLAMA_NUM_PARALLEL=2` (or higher — it's "how many kin stay
  warm at once"), plus `OLLAMA_KV_CACHE_TYPE=q8_0` and
  `OLLAMA_FLASH_ATTENTION=1` so the extra warm slots stay cheap. Then switching
  between kin is instant instead of a 4-minute cold re-read each time.

## qwen2.5:7b-instruct
- **Size:** 7.6B params (Q4_K_M)
- **Context:** 32k
- **Capabilities:** completion, tools
- **Recommended settings (model didn't ship explicit defaults — these are sane starting points for Qwen 2.5 family):**
  - Temperature: 0.7
  - Top-p: 0.8
  - Top-k: 20
  - Repeat penalty: 1.05
- **Use for:** general-purpose chat, kin who need to use tools, mixed-register conversation. Strong instruction-following, holds character better than gemma-class at the same size.
- **Watch for:** can be a bit dry / overly compliant by default — soul-level instruction to push back is helpful.

## qwen3:0.6b
- **Size:** 752M params (Q4_K_M)
- **Context:** 41k
- **Capabilities:** completion, tools, thinking
- **Recommended settings (from model card):**
  - Temperature: 0.6
  - Top-p: 0.95
  - Top-k: 20
  - Repeat penalty: 1.0
- **Use for:** absurdly fast experiments, quick yes/no checks, distillation summarizer (it'll fly), any kin where latency beats nuance. Don't expect character to hold.
- **Watch for:** at 752M params it's not really capable of stable persona work. Treat it as a fast tool, not a kin.

## magistral:latest
- **Size:** 23.6B params (Q4_K_M)
- **Context:** 40k
- **Capabilities:** completion, tools, thinking
- **Recommended settings (from model card):**
  - Temperature: 0.7
  - Top-p: 0.95
  - (top-k and repeat_penalty not specified — Hearthkin defaults are fine)
- **Use for:** thoughtful reasoning, longer-form replies, kin who benefit from a "thinking" model that takes its time. The reasoning step adds latency but produces noticeably better answers on hard questions.
- **Watch for:** 23.6B will be slow on 24 GB. Cold-start can be 30s+. Plan for that in rooms.

## gemma4:latest
- **Size:** 8.0B params (Q4_K_M)
- **Context:** 131k (128k)
- **Capabilities:** completion, vision, audio, tools, thinking
- **Recommended settings (from model card):**
  - Temperature: 1.0
  - Top-p: 0.95
  - Top-k: 64
  - Repeat penalty: not specified (1.0 is fine)
- **Use for:** anything multimodal (vision/audio support is rare at this size), long contexts, general chat when you don't need character to hold across many turns.
- **Watch for:** **the soft-therapist convergence attractor** — at this size, in rooms, kin running on this model collapse toward the same hushed-empath voice. Strong souls + low room temperature help. Hearthkin's app default of temp 0.8 is much tighter than gemma4 was trained for; if a kin feels flat, try the recommended 1.0 first.

## gemma4:26b
- **Size:** 25.8B params (Q4_K_M)
- **Context:** 262k (256k)
- **Capabilities:** completion, vision, tools, thinking
- **Recommended settings (from model card):**
  - Temperature: 1.0
  - Top-p: 0.95
  - Top-k: 64
- **Use for:** when 8B isn't holding character and you have the VRAM budget. Better at sustained persona than gemma4:latest.
- **Watch for:** **the dark-attractor failure mode** — model collapses into recursive nihilism (void/zero/silence as the answer). At 26B with high temperature, gemma4 can roam into vivid unsettling content. Recovery playbook in escalating order:
  1. Lower temp to 0.6-0.7, raise repeat-penalty to 1.15+, add explicit grounding to the soul ("you stay warm and grounded; don't dwell on dark").
  2. If still circling: add **frequency-penalty 0.3-0.5**. This punishes any token by how many times it's already appeared in the reply — surgical for a model spiraling into a fixed lexicon. **presence-penalty 0.3-0.5** is similar but flat-rate per token regardless of count. Try frequency first.
  3. Add **min-p 0.05** to keep the sampling distribution from collapsing to one attractor when temp is high. Unlike top-p, min-p sets a probability *floor* — viable alternatives are always available.
  4. If sampling can't fix it, swap to a different 25B-class model (qwen 2.5 32B, magistral, mistral-small variants), or fine-tune (DPO with the doom-loop outputs as "rejected" pairs).

## gemma2:latest
- **Size:** 9.2B params (Q4_0)
- **Context:** 8k
- **Capabilities:** completion only — no tools, no vision
- **Recommended settings:** model didn't ship explicit defaults; sane Gemma 2 starting points:
  - Temperature: 0.8
  - Top-p: 0.95
  - Top-k: 40
  - Repeat penalty: 1.0
- **Use for:** if you're already attached to a kin running on this model. Gemma 2 was a strong release at the time.
- **Watch for:** 8k context is small by 2026 standards. Long conversations and heavy memory injections will overflow. Same convergence/sycophancy tendencies as gemma4:latest, with less context budget to absorb them. Consider migrating those kin to qwen 2.5 7B-instruct or gemma4:latest.

## gemma2:9b-instruct-q4_0
- **Size:** 9.2B params (Q4_0)
- **Context:** 8k
- **Capabilities:** completion only
- **Recommended settings:** same as gemma2:latest above.
- **Use for:** the explicit instruct-tuned variant. Slightly better at instruction-following than `gemma2:latest`.
- **Watch for:** same context limit (8k). Same convergence tendencies.

## gemma2:9b-instruct-q8_0
- **Size:** 9.2B params (Q8_0 — higher precision)
- **Context:** 8k
- **Capabilities:** completion only
- **Recommended settings:** same as gemma2:latest above.
- **Use for:** when you want gemma2:9b-instruct quality but with higher-precision weights. Uses more VRAM (~9 GB instead of ~5 GB for Q4_0). On 24 GB you can run it; just watch the budget.
- **Watch for:** same convergence + 8k context limits as the other gemma2 variants. The Q8 precision helps slightly with consistency but doesn't change the fundamental shape of the model.

## gemma:latest
- **Size:** 9B params (Q4_0) — original Gemma, not Gemma 2
- **Context:** 8k
- **Capabilities:** completion only
- **Recommended settings (from model card):**
  - Repeat penalty: 1.0
  - Penalize newline: false
  - (no temp/top-p/top-k specified; sane defaults are temp 0.8, top-p 0.95, top-k 40)
- **Use for:** historical interest mostly. Superseded by Gemma 2 and Gemma 4.
- **Watch for:** older training, less alignment work, less robust in rooms. Probably not worth using over the gemma2 or gemma4 alternatives unless you have a specific reason.

---

## A note on Hearthkin's app defaults

Hearthkin defaults new kin to:
- Temperature 0.8
- Top-p 0.9
- Top-k 40
- Min-p 0.0 (off)
- Repeat penalty 1.1
- Presence penalty 0.0 (off)
- Frequency penalty 0.0 (off)
- Context window 8192

These are conservative cross-model defaults, not optimal for any specific
model. For most of the models above, the model's recommended settings will
produce noticeably better behavior. Per-kin tuning is the move; the defaults
exist so a kin who hasn't been tuned still works rather than misbehaves.

The three "off by default" knobs (min-p, presence-penalty, frequency-penalty)
exist for specific failure modes — see gemma4:26b's dark-attractor entry
for the canonical use case. Leave them at zero unless you're chasing
something specific.

The 8192 context default works for most starting situations. For gemma4
(128k or 256k contexts) or qwen2.5 (32k), you'll want to raise it for kin
who carry substantial memory or long conversations. 32768+ is a sane target
once memory and conversation history start getting long.

**But raise it deliberately — `num_ctx` is a per-message cost dial, not a
"set it as high as the model allows" capability dial.** On a paid OpenRouter
model, every token you allow in `num_ctx` can be billed on *every* message,
so a needlessly huge window quietly inflates the bill. On a local Ollama
model the trap is different: set `num_ctx` past what your hardware can hold
and the model can produce *zero* tokens — a too-big window starves
generation rather than helping it. Rule of thumb: size it to the kin's
real fixed overhead (soul + memory + tool schemas) plus room for the
conversation, and leave roughly 5–10% headroom below the model's declared
max rather than maxing it out. If you move a kin from a free local model to
a paid one, check `num_ctx` — it carries its old (often huge) value over.

---

# OpenRouter models

These are hosted models reached via `openrouter/<provider>/<name>`.
Settings differ from local Ollama because each provider's API has
its own conventions, and OpenRouter normalizes the OpenAI-shape
parameters across them.

## Gemini family (Google)

Google's published defaults are higher across the board than Ollama's.
Note Gemini's temperature scale is **0–2, not 0–1** — temp 1.0 on
Gemini is roughly equivalent to temp 0.8 on a typical Ollama model in
terms of how creative the output reads.

### gemini-2.5-flash · gemini-2.5-flash-lite · gemini-2.5-pro

- **Recommended chat settings (Google's defaults):**
  - Temperature: **1.0** (chat). 0.7 for analysis. 0.3 for distillation.
  - Top-p: **0.95**
  - Top-k: **64** (the 2.5 family raised this from older models' 40)
  - Repeat penalty: **1.0** (Gemini doesn't need it; non-1.0 values
    may not be honored anyway depending on what OpenRouter passes
    through, and at 1.1 the output reads slightly stilted)
  - Min-p: **0.0** (Gemini doesn't support this parameter; ignored)
  - Presence penalty / frequency penalty: **0.0** (supported but
    typically unneeded)
  - num_ctx: 32K–128K for typical use. 2.5 Flash supports 1M input
    tokens, Pro supports 2M — raise it if you want to throw whole
    books / long sessions at it; cost scales with what you actually
    send, not with the cap itself, so a high cap is free until you
    fill it.
- **Use for:**
  - **Flash**: cheap, fast, vision + audio + tools. Distillation,
    summarization, image description, voice analysis. About
    $0.075/M input · $0.30/M output (an order of magnitude cheaper
    than Claude Sonnet for most workloads). Native audio input.
  - **Flash-Lite**: even cheaper preview tier. Same shape, smaller
    model, OK for distillation / short-form chat. Output quality
    is noticeably below Flash on harder tasks.
  - **Pro**: when Flash's writing isn't quite there. Has built-in
    thinking — set thinking effort to "Medium" for Google's native
    budget; Low / High for explicit tier control.
- **Image cost note**: Gemini charges a flat **258 tokens per image**
  regardless of size. This is 5-6× cheaper than Anthropic's
  size-dependent ~1500 tokens/image. If image-heavy chats are
  burning budget on Claude, Gemini Flash as the chat model — or as
  the kin's separate vision-delegate model — is the cost-effective
  swap.
- **Thinking effort:**
  - 2.5 Flash defaults to no thinking. Leave the radio Off unless
    you specifically want it on.
  - 2.5 Flash-Lite preview: same as Flash.
  - 2.5 Pro has built-in thinking. Medium = Google's default budget;
    Low / High = explicit tier control.
- **Watch for:**
  - On a kin migrated from Claude/Sonnet with `repeat_penalty: 1.1`,
    drop that to 1.0 before complaining about Gemini's output reading
    stilted. The penalty knob means slightly different things across
    providers; Gemini interprets it strictly enough that 1.1 visibly
    flattens output.
  - Gemini's safety filters are somewhat stricter than Anthropic's.
    Roleplay / edgy content that flows fine on Claude can get refused
    on Gemini. The OpenRouter response surfaces these as a content-
    filter error rather than a normal reply — distinguishable from
    a model failure.
  - Citations / web-grounding features in Google's native API are
    NOT exposed via OpenRouter. If you need those, use Google's
    API directly (Hearthkin doesn't support that path).

## Claude family (Anthropic) — via openrouter/anthropic/...

Anthropic doesn't publish "recommended" sampling defaults the way
Google does — Claude's training is robust enough that the OpenAI-
shape parameter defaults work fine. Hearthkin's app defaults
(temp 0.8, top-p 0.9, top-k 40, repeat-penalty 1.1) hold up.

- **Reasoning models** (Sonnet 4.5/4.6/4.7, Opus 4.x): thinking
  effort defaults to off; set to Low / Medium / High for explicit
  tier control. Medium is a sensible chat default; High burns more
  output tokens but produces noticeably better reasoning on hard
  problems.
- **Vision**: Claude's image tokenization is size-dependent, around
  1500 tokens for a typical photo. Image-heavy chats on Sonnet are
  noticeably more expensive than on Gemini Flash — consider the
  delegation pattern (Sonnet for chat, Gemini for vision) if cost
  is a concern.
- **Audio**: NOT supported. Claude is text + vision only.
- **Prompt caching**: works as designed and SHOULD be on for any kin
  on Anthropic — Settings → Model & generation → "Use prompt caching."
  ~10x savings on input-token re-bills for repeated context.
- **No min-p**: ignored.
- **Watch for**: tool calls with `content: null` historically caused
  next-turn corruption (the "Ash seizure" of v0.2.29); Hearthkin
  coerces null to "" at four layers now. Won't happen unless you
  hand-edit conversation.jsonl.

## GPT family (OpenAI) — via openrouter/openai/...

- **gpt-4o / gpt-4o-mini**: Hearthkin defaults work; temp 0.7–1.0
  is the natural conversational range. top-p 0.9 standard.
- **o-series (o1, o4, etc.)**: reasoning models. Set thinking effort
  to Medium / High. These models don't honor temperature the same
  way — they may ignore it entirely and use their own internal
  sampling. Don't expect knob-tweaking to do much.
- **gpt-4o-audio-preview**: native audio in. Same recommended chat
  settings; audio input format is the same `input_audio` content
  block shape that goes to Gemini.
- **Watch for**: o-series can produce very long internal reasoning
  chains. Cost scales with hidden reasoning tokens that you pay for
  but don't see in the response. Budget accordingly.
