# Gemma 4 tool-calling investigation — 2026-07-17

Chasing the unattended-side gesturing problem (kin narrate a tool action instead
of calling it, worst on crons). Operator runs **gemma4:31b** on the Mac (Ollama).
Question: is Hearthkin failing to use Gemma 4's native function-calling, or is
gesturing a behavioral/load thing?

## VERDICT (session 2, 2026-07-17)

**Gemma 4 is not the problem, and neither is Hearthkin's wiring. Tool gesturing
is a solved problem on the current model — it was a property of the models the
kin used to run.** All three open suspects from session 1 are closed. The one
real residual is park-keeper narration on Tarn's crons (~12%), which is a
different failure than the one this doc set out to chase.

Nothing needs fixing for tool calling. See "What's actually left" at the bottom.

---

## What was verified (session 1)

- **Hearthkin uses the NATIVE tool path.** `llm_backend._chat_ollama_blocking` /
  `_chat_ollama_stream` / `_ollama_chat_raw` all pass `tools=` to `ollama.chat`,
  and `run_tool_loop` passes `tools=tools`. It is NOT text-injecting tool schemas
  into the prompt. So the wiring is native, not the old prompt-based fallback.
- **`gemma4:31b` declares tool capability.** `/api/show` on the Mac →
  `"capabilities":["completion","vision","tools","thinking"]`.
- **Web (Google + tutorials, Apr 2026):** Gemma 4 was TRAINED for function
  calling with dedicated special tokens, pitched as more reliable than other open
  models.

## Suspect 1 — Ollama's gemma4 template. **CLOSED: not a problem.**

The session-1 worry was that the `/api/show` template looked "thin" (~3 literal
tool markers) and might be feeding gemma4 a generic tool format instead of its
trained native lifecycle.

The template is thin because **it does nothing at all**. Full template is 13
characters: `{{ .Prompt }}`. The modelfile shows why:

```
TEMPLATE {{ .Prompt }}
RENDERER gemma4
PARSER   gemma4
```

Ollama ≥ 0.20.0 uses a **compiled Go renderer/parser** for gemma4 rather than a
Go text template. Prompt construction (including the native tool special-token
lifecycle) and `tool_calls` parse-back both happen in Ollama's compiled `gemma4`
code path. The Go template is a vestigial placeholder. Mac runs **Ollama
0.30.10**, well past the `requires: 0.20.0` floor.

**So the native format IS being used.** Counting literal markers in the template
was measuring the wrong object.

## Suspect 2 — legacy `tool_use_hint` muddying it. **CLOSED: no measurable effect.**

A/B'd directly against `gemma4:31b` over the Ollama HTTP API — 3 tool schemas,
3 prompt shapes (direct ask / cron-tending wake-up / soft indirect ask), hint
stripped vs. Hearthkin's real `tool_use_hint` text, 3 samples each:

| prompt shape | hint OFF | hint ON |
|---|---|---|
| direct ("read notes.md") | 3/3 tool call | 3/3 tool call |
| cron tending wake-up | 3/3 tool call | 3/3 tool call |
| soft ("wondering what you wrote yesterday") | 3/3 tool call | 3/3 tool call |

**18/18.** Gemma 4 called the right tool with the right arguments every single
time, hint or no hint — including on the cron-tending prompt shape, which was
the suspected worst case. The hint neither helps nor hurts here. Leave it (it
still earns its keep on other models); it is not a lever on gemma4.

## Suspect 3 — load / cold-cron behavior. **CLOSED for tool calling.**

### Synthetic: real prompt, real tools, real history

Rebuilt Tarn's actual send using Hearthkin's own `kin_persistence.build_system_prompt`
and `tools.load_tools(..., cron_turn=True)` — not an approximation — and fired the
nightly tending wake-up at `gemma4:31b` under growing context:

| condition | prompt size | tool calls |
|---|---|---|
| A: real system prompt + 9 tools, no history | 4,318 tok | 3/3 |
| B: + 28 messages of real history | 9,623 tok | 3/3 |
| C: + 85 messages of real history | 20,951 tok | 3/3 |
| D: bare soul (no base prompt) + 9 tools | 2,835 tok | 3/3 |

**12/12, correct tool (`read_staging`) every time.** No degradation from a 2.8k
prompt to a 21k one — two-thirds of Tarn's 32k `num_ctx`. Context load does not
break gemma4's tool calling. Combined with the suspect-2 A/B: **30/30 overall.**

### Production: live history

Checked against live production data as well: every gemma4
kin's real `conversation.jsonl`, scanned with Hearthkin's own
`chat_helpers.detect_tool_roleplay` against each kin's actual enabled tool list.

| kin | assistant turns | cron replies | gesture hits |
|---|---|---|---|
| Tarn | 342 | 81 | 0 |
| Bracken | 253 | 14 | 1 (false positive — see below) |
| Opal | 280 | 21 | 0 |
| Vesper | 110 | 0 | 0 |
| quill | 119 | 0 | 0 |
| hollis | 7 | 0 | 0 |
| Vesper | 4 | 0 | 0 |

The single Bracken hit was `narrative-intent` on *"Let me read the current soul
file so I can understand the structure…"* — and that turn **carried a real
`tool_calls` payload**. Bracken said it and then did it. Correct behavior; the
`narrative-intent` variant is documented as ambiguous and deliberately not
auto-corrected.

**Real total: zero tool-gesturing across ~1,100 gemma4 assistant turns.**

The crons are genuinely live, not dormant — Tarn fires 5/day, Opal and Bracken
2/day, uninterrupted through today. Since 2026-07-09: Tarn 30/42 wake-ups
invoked tools, Bracken 7/10, Opal 8/16. (The non-tool ones are mostly wake-ups
with nothing to tend, which is correct, plus Opal's journaling cron which isn't
a tool prompt at all.)

### Where the gesturing actually came from

`~/.hearthkin/logs/empty_replies.log` records every gesture the detector has
ever caught. Every single incident names a model that is **not** gemma4:

- `hermes3:70b` — 2× `asterisk-action` (2026-06-26)
- `qwen36-opus-q4:latest` — `narrative-intent` + a long run of empty replies
- `qwen3.5:35b-a3b-coding-nvfp4` — `asterisk-action` salvage (2026-06-21)
- `mistral:latest` / `mistral-small:latest` — empties (2026-06-09)

Last entry of any kind: **2026-07-08**, on `qwen36-opus-q4`. Nine days of
gemma4 since, with nothing logged. The gesturing problem was a model problem,
and moving the kin to gemma4 fixed it.

---

## What's actually left: park-keeper narration (a DIFFERENT bug)

Tarn runs `park: keeper`. Its cron wake-up is a park turn: it should either call
the `tff` tool or end its reply with a `> command` line for `park_keeper.route_reply`
to harvest. Since 2026-07-09, of 42 wake-ups:

| outcome | count |
|---|---|
| acted via `tff` **tool call** | 30 |
| acted via `> command` line | 7 |
| **neither — pure narration** | **5 (12%)** |

The 5 failures narrate the action in the past tense without ever issuing it:
*"I've just finished the last care round…"*, *"Then, I'm going to dig a massive
haul. […] Let's get started."* — announcing intent, or claiming completion, with
no command and no tool call.

Two things worth noting:

1. **`detect_tool_roleplay` cannot catch these.** Its asterisk-action whitelist
   is scoped to tool-ish verbs against tool-ish targets (soul / memory / journal
   / staging / file). A park action — "finished the care round" — matches
   nothing. If park narration is worth correcting, the detector needs a park
   vocabulary, or `park_keeper` needs its own "you claimed a move but issued
   none" nudge on the retry path.
2. **A park_keeper design premise is now stale.** The doc's reasoning was that
   the `tff` structured tool call is "the register-switch small models won't
   make" — hence the `> command` bridge. Gemma 4 makes that call comfortably:
   30 of 37 successful park turns went through the **tool**, only 7 through the
   `>` line. The bridge is now the minority path, not the primary one.

88% action rate overall. This is a modest polish item, not a broken system.

## Recommended next steps

1. **Nothing for tool calling.** Don't set a custom Modelfile TEMPLATE — that
   would *break* the working compiled renderer. Don't strip the hint.
2. **Optional, low priority:** decide whether the 12% park narration is worth a
   corrective. Cheapest version is an outcome-based retry in the keeper cron
   path (same shape as the existing `tend_retry`): if the reply produced neither
   a `tff` call nor a `>` command, re-prompt once with a short "you described a
   move but didn't make one — issue it as `> command`" note.
3. **Revisit the park_keeper docs** to reflect that on gemma4 the tool path
   works, so the `>` bridge is a fallback rather than the main mechanism.

## Access notes
- The models run on a separate machine on the LAN. Reach its Ollama daemon over
  HTTP at `http://<that machine's hostname or IP>:11434` — no shell access
  needed for any of the API work below. On the same LAN the machine's `.local`
  hostname usually resolves; off-LAN, a VPN/mesh (e.g. Tailscale) address works
  the same way. Never expose port 11434 to the open internet.
- Query it with the HTTP API rather than the `ollama` CLI — over a bare SSH
  session the CLI often isn't on `PATH`, and the API is the same interface
  Hearthkin itself uses:

      curl http://<host>:11434/api/show -d '{"model":"gemma4:31b"}'

## Method note (for whoever picks this up next)

`detect_tool_roleplay(content, tool_names)` returns a **2-tuple**
`(variant, tool_name)`. Two traps bit this session, both producing confidently
wrong answers:

- calling it with one argument raises `TypeError`; a broad `except` around the
  loop swallowed it and reported **0 hits** across every kin.
- `bool((None, None))` is `True` — a truthiness check reports **every** turn as
  a gesture, including "Good morning."

Test `variant is not None`, and positive/negative-control the detector on known
strings before trusting any count it produces.
