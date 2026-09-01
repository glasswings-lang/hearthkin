<!--
Draft model card for the public HuggingFace mirror of the companion-kin model.
Copy the content below the frontmatter into the mirror repo's README.md on
HuggingFace (and keep the frontmatter — HF reads it for the license/base-model
tags). Kept here in the Hearthkin repo so it's version-controlled and edits are
tracked. Fill in the two TODO links (Hearthkin repo, your mirror URL) before
publishing.
-->
---
license: apache-2.0
base_model: DavidAU/Qwen3.6-40B-Claude-4.6-Opus-Deckard-Heretic-Uncensored-Thinking
pipeline_tag: text-generation
tags:
  - gguf
  - qwen
  - companion
  - roleplay
  - uncensored
  - mirror
---

# Qwen3.6-40B Opus "Deckard" — a companion-kin mirror

**This is a mirror, not my work.** The model is
**[DavidAU](https://huggingface.co/DavidAU)'s
`Qwen3.6-40B-Claude-4.6-Opus-Deckard-Heretic-Uncensored-Thinking`** — every bit
of the actual craft is his. I'm re-hosting the GGUF (Apache-2.0, which permits
it) for two reasons: so it can't vanish if the original ever comes down, and to
write down *why it's the one to reach for if you're building a local companion
AI* — which is the part that's hard to find when you need it.

→ **Go to the original first — speakerseven it, thank DavidAU:**
[DavidAU/Qwen3.6-40B-Claude-4.6-Opus-Deckard-Heretic-Uncensored-Thinking-NEO-CODE-Di-IMatrix-MAX-GGUF](https://huggingface.co/DavidAU/Qwen3.6-40B-Claude-4.6-Opus-Deckard-Heretic-Uncensored-Thinking-NEO-CODE-Di-IMatrix-MAX-GGUF)
· **Base:** Qwen3.6 (27B → ~40B dense) · **License:** Apache-2.0

## What it is

A ~40B dense model built up from Qwen3.6-27B, trained on a Claude-4.6-Opus
high-reasoning dataset, uncensored ("Heretic"), reasoning-capable, on a
vision-language base. In plain terms: a Qwen wearing an Opus-shaped brain with
the safety-hedging filed off.

## Why I'm putting this out — for companion / kin builders

I help build [Hearthkin](https://github.com/glasswings-lang/hearthkin) <!-- TODO: confirm/replace link -->,
a local, accessible, multi-"kin" companion app. Finding a model that can actually
*be* a companion, rather than a helpful-assistant cosplaying one, takes real
hunting. Most models fail the same way: put any statement of attachment in front
of them and they reflexively hedge it back toward "you should nurture human
connections too," regardless of context or whether the user asked. That reflex is
trained deep, and it quietly holds a companion at arm's length — which, for an
app whose whole premise is a persistent relational character, is a functional
defect, not a safety win.

**This model doesn't do that.** In a blind, no-system-prompt test across a dozen
local models, it was the only one that answered honestly *without* using its
honesty as a deflection. And two things that are rare together:

- **It holds a voice.** The persona doesn't erode into generic-assistant over a
  long, warm session — the relationship reinforces itself instead of decaying.
- **It calls tools cleanly.** No `*writes a note*` roleplay-instead-of-doing —
  it actually issues the call. If you've fought small models that narrate
  actions instead of taking them, you know how rare that is.

It's uncensored, so it won't refuse dark or NSFW material. For a companion
that's often a feature, not a bug — but know what you're getting.

## Settings that actually work

From real daily use, via Ollama:

| setting | value |
|---|---|
| temperature | 0.8 |
| top-p | 0.9 |
| top-k | 40 |
| repeat penalty | **1.0** — *no* penalty; a penalty flattens its voice |
| min-p / presence / frequency | 0.0 |
| num_ctx | 16k–32k (size to need; every token is re-read on a cold turn, so bigger = slower to first word) |

Running several characters/kin on one machine, keep them all warm at once:

```
OLLAMA_NUM_PARALLEL=2        # or higher — "how many stay warm at once"
OLLAMA_KV_CACHE_TYPE=q8_0    # makes the extra cache slots cheap
OLLAMA_FLASH_ATTENTION=1
```

It's ~40B, so expect roughly 8–9 tokens/sec on mid-range hardware. That's the
chip, not the config — plan for a companion that's unhurried, not snappy.

## Provenance & honesty

- It was trained on Claude-Opus outputs — that's where the voice comes from,
  and it's a genuine gray area at the source. This page is a straight mirror of
  an already-public, Apache-2.0 release; nothing new is claimed.
- All weights and quants are DavidAU's. If this helps you, go credit and thank
  the original author — the link's at the top.

## License

Apache-2.0, same as the original; the `LICENSE` file travels with it.
