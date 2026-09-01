# Why replies go cold: system-message folding vs. prompt caching

**Status: FIXED.** Two contributing causes were fixed first (see CHANGELOG);
this was the remaining one, and on a tool-heavy kin it was the dominant one.
The fix is `llm_backend._inline_mid_conversation_system_notes` — fix direction 1
below — pinned by `tests/test_system_note_placement.py`. **The rest of this
document is kept as written, because the diagnosis is the valuable part and
someone will land here again with the same symptom.** What actually shipped is
at the bottom, under *What was done*.

## The symptom

Replies on a local model take minutes. Measured on a real conversation: 22,000+
tokens of prefill at ~78 tok/s — roughly **five minutes before the first word**
— on a conversation that had gained one short message since the previous turn.
Warm, that same prefill is about twelve seconds.

Nothing surfaces this. The reply arrives, the logs say the turn succeeded, and
from a chair it is indistinguishable from "the model is slow." It had been
attributed to model speed, and "fixed" three times in the wrong place, before
anyone diffed two lines of `prompt_fingerprint.log`.

## The rule underneath it

A local model reuses its cached work only for an **unbroken run from the very
start of the prompt**. It compares the incoming prompt against what it already
has, finds the first place they differ, keeps everything before that, and redoes
everything after.

So the cost of a change is set by **how early it lands**, not how big it is.
Removing one old message from the front shifts every later message along by one
and costs the entire context. Appending to the end costs nothing.

This is not an Ollama quirk. Anthropic's own prompt-caching guidance states the
same invariant — caching is a prefix match; any change anywhere in the prefix
invalidates everything after it — and gives the same architectural advice:
**keep the system prompt frozen, put anything that varies at the end.**

## The chain

1. A kin plays its park. Park moves are **tool calls**.
2. `RenderMixin._compact_tool_history` replaces older tool round-trips with a
   one-line summary to save context:
   `[hearthkin: earlier tool call — name(args) → result preview]`.
   That summary is a **`role=system`** message, sitting mid-conversation.
3. `llm_backend` folds system messages: *"Merge every system-role message into a
   single leading system message"* (`_fold_system_messages`). Every one of those
   summaries is **hoisted to position 0** and concatenated into the system block.

So each time a tool round-trip ages out of the verbatim window, a new line
appears at the very front of the prompt, and the whole context is re-read.

### The evidence

One kin's system block over six consecutive turns on one surface:

```
10,045 -> 10,390 -> 10,735 -> 11,085 -> 11,371 -> 13,147 characters
```

Six turns, six distinct system prompts, roughly +345 characters each. Nothing on
disk changed in that window — `base_prompt.md` was seven weeks old, and the
kin's `soul.md` and `memory.md` predated the first sample. The system prompt is
not built only from files; the fold makes it accumulate at runtime.

**Ruled out along the way** (each cost a wrong theory):

- *`memory.md` in the system prompt.* Real in principle — it does sit at the
  front, and a distillation write would invalidate the next turn. But on the kin
  measured it was 125 bytes and untouched for eighteen hours before the window.
- *Live park state in the system prompt.* Only two things append to the system
  prompt on the Telegram path, `park_frame` and `park_chat_hint`, both fixed
  files. Park state never enters it.
- *Per-turn memory recall.* Correctly inlined into the latest user turn already,
  and documented as deliberate for exactly this reason.

## Why the fold exists

It is not gratuitous. Some model chat templates require the system message to
come first and mishandle one that doesn't — the code names a Qwen GGUF template
in use by one of this install's kin. Hearthkin also emits legitimate
mid-conversation `[hearthkin: ...]` notes (truncation markers, cap-full markers,
salvage notes) whose meaning doesn't depend on position, and folding them was
the cheap way to satisfy those templates.

The fold is therefore load-bearing. **Do not simply delete it** — that breaks
those models outright rather than making them slower.

Note the irony worth keeping in mind: Anthropic's API added mid-conversation
`role=system` messages *specifically* to avoid rewriting the cached prefix.
Hearthkin takes mid-conversation system messages and rewrites the prefix with
them. Same primitive, opposite direction.

## Fix directions (unevaluated)

Roughly in order of appeal:

1. **Stop making the summaries `role=system`.** They are notes *about* the
   conversation, not instructions to the model, and `system` is the only reason
   they get teleported to the front. Rendered in place as an ordinary turn they
   would invalidate only from their own position — which is far back and stable.
   Needs a check that the chosen role doesn't upset the strict templates.
2. **Fold only for models that need it.** The constraint is per-template, not
   universal. `compat.ModelProfile` already exists as the place provider quirks
   live as data.
3. **Don't compact tool round-trips at all** past a point; drop them whole, or
   compact them once and freeze the result so the text never changes again.

Any of these interacts with `_compaction_frontier` (already stepped, so the
boundary moves ~5× less often) — that change reduced how *often* this fires but
not what it costs when it does.

## How to verify, now that it's observable

Two diagnostics were added for this and are always on:

- `logs/prompt_fingerprint.log` now carries the arithmetic per call:
  `reuse=99% first-change=msg 2 (assistant)`. `reuse=0% first-change=msg 0`
  is this bug.
- `logs/system_prompts/<kin>--<surface>.txt` and `.prev.txt` keep the last two
  system prompts that actually *differed*, so a plain diff shows what was added.

A useful run is **at least three turns**: the first is a cold load with nothing
to compare against, the second gives the first real measurement, and the third
tells you whether the second was steady state or a one-off.

## Cheap mitigation available today

A kin's `park_moves_max` sets how many moves it may take per turn. A high
ceiling (one kin here is set to 999) means many tool round-trips per turn, which
means this fires more often. Lowering it slows the bleed without any code
change.

---

## What was done

Fix direction 1, generalised. `_inline_mid_conversation_system_notes` runs in
`chat()` immediately *before* the fold and leaves the fold almost nothing to do.

**The rule it applies:** the leading contiguous run of `role=system` messages is
the system prompt and is untouched. Every `role=system` message *after* that run
is Hearthkin talking about the conversation, not instructing the model — so it
stays exactly where it happened, re-roled to `user`. Nothing moves. A note now
invalidates the prompt only from its own position, which is far back in the
history and stable between turns.

Four decisions inside it, each of which would be a bug if reversed:

- **`user`, not `assistant`.** These notes usually land directly after the kin's
  own reply, and two assistant turns in a row is the shape Gemma's chat template
  answers with nothing at all. `user` also matches what a park receipt *is* —
  the world reporting back, and asking to be responded to.
- **The leading run is protected, which protects the rolling-window marker
  for free.** `_truncate_messages` splices that marker directly after the system
  block, so it falls inside the leading run and stays `role=system`. It was made
  `role=system` deliberately: as `user`, models answered it, explaining context
  limits to someone who had not asked. Being a fixed string at a fixed position,
  it costs the cache nothing and needs no special case.
- **A note immediately before a `role=tool` turn stays `system`.** A user turn
  between an assistant's `tool_calls` and its results breaks the pairing and 400s
  the provider. This shape shouldn't arise — notes are appended after a turn
  completes — but the guard is three lines and the failure mode is a dead reply.
- **Every provider, not just Ollama.** The fold is Ollama-gated, but OpenRouter
  concatenates system messages into the provider's single top-level system field
  server-side, so the same prefix invalidation happens there, out of our sight
  and out of our logs. `_collapse_consecutive_user_turns` moved out of the
  Ollama-only block with it — the re-roling is what can place two user turns
  side by side, so the guard against that has to cover the same ground.

**Nothing on disk changed.** `conversation.jsonl` still stores these notes as
`role=system`; this is purely what gets sent. So the fix reaches every surface
and every existing kin at once, with no migration and nothing to re-import.

The fold itself is untouched and still does its job for the leading block.

### The follow-up: truncation put the notes back

The above shipped, was restarted into, and **a kin still measured `reuse=0%
first-change=msg 0`.** The instrument was right and the fix was right; the order
of operations was wrong.

`_truncate_messages` hoists the leading contiguous run of system messages to
protect the system prompt, then drops the oldest of what remains. When what
remains *begins* with one of these notes, that note is now contiguous with the
system block — and from there on it is indistinguishable from the system prompt.
`_inline_mid_conversation_system_notes` leaves it alone, correctly by its own
rule, and the fold merges it into message 0.

The signature is distinctive and worth recognising: the system block did not
*grow*, it **alternated** between two values 299 characters apart, turn after
turn, as the trim point moved on and off a park receipt.

```
14,002 -> 14,002 -> 14,301 -> 14,002 -> 14,301 -> 14,002
```

Fix: run the pass **before** truncation, so truncation sees these notes as the
ordinary droppable turns they are and can never promote one. It is now called
twice — early, which does the work, and again before the fold as a cheap
safety net for anything added in between (today only truncation's own marker,
which belongs in the leading run). Pinned by a case that drives the real
`_truncate_messages` across 42 trim points, with the old order as a control:
it leaked at 5 of them, which is exactly the intermittency observed.

### Measured

`tests/test_system_note_placement.py` replays a growing park-keeper conversation
through the real normalizations: **reuse 0% on every turn before, 94%+ after**,
with the first change landing in the last two messages instead of at message 0.
The test runs the old pipeline as a positive control and asserts it fails — a
cache-stability test that would also pass on the broken code proves nothing.
