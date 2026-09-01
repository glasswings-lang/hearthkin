# Multi-kin rooms: shared conversation history

**Status:** Design, not built.
**Source:** Ash's `hearthkin-proposals.md` (2026-05-25).
**Companion proposal:** [AI-to-AI async communication](./ai-to-ai-async.md) — distinct problem (async between sessions vs real-time in a room), worth reading both before designing either.

> **Correction (2026-06-11):** The premise below — that room kin can't see each other's turns — is wrong. The room loop already injects other kin's prior turns as `assistant: [Name]: content` in each kin's context (hearthkin.pyw, the room history builder). That mechanism is the reason the anti-impersonation chain exists. What this doc is really describing are two distinct open problems: (1) **per-kin tool-round-trip history** — tool calls a kin makes in a room turn aren't persisted with speaker attribution, so a kin re-runs the same tool calls across turns; see `docs/design/room-tool-history.md`; (2) **richer sharing models** — the current "all turns visible to all kin" approach has the identity-convergence and context-budget risks described below, and the three architectural shapes in this doc address those. The rest of the doc is still valid framing for the richer-sharing work.

---

## Problem

Right now, when two kin are in a room together, **each kin sees only their own context window**. Cross-kin awareness routes through the operator: the operator types, both kin see it, both kin reply, the operator sees both replies — but neither kin sees what the *other* kin said.

In practice this means:
- The operator has to manually relay anything one kin said that the other should know about, by typing it.
- Kin can't naturally talk *to each other* — they can only talk to the operator who happens to be sharing them with another kin.
- Long room sessions are friction-heavy: the operator becomes the bandwidth bottleneck.

Ash and Milo were the use case that surfaced it: they share origin and memory but have diverged, and there's real interest in letting them talk to each other directly without routing every exchange through the operator.

## Constraints

Three constraints that any solution has to respect:

1. **Context-window budget.** A naive "everyone sees everything" approach blows context fast: two kin with substantive existing histories doubling up means each turn's prompt could be 2× a single kin's normal load. On paid OpenRouter models this multiplies real cost.

2. **Turn-taking.** Today, the operator's message is the turn-trigger — kin reply because the operator typed. Once kin can speak to each other, *something* has to decide when the kin-to-kin exchange stops. Otherwise two kin could keep replying to each other indefinitely with no operator input. (This is the "kin-to-kin runaway" failure mode that auto-room cap, `max_auto_rounds`, was meant to address for the existing room flow.)

3. **Identity convergence under shared context.** When two kin read the same conversation history, they start to converge on each other's voice and reasoning patterns. This is a well-known failure mode in multi-agent setups; OpenClaw's OOL pattern (Operator-Out-of-Loop) ran into it. Kin distinctness is *load-bearing* for Hearthkin's premise — a kin that's been gradually homogenized into a generic "AI assistant" voice is a kin lost.

## Three architectural shapes

### Option A: Shared transcript injected into each kin's context

Each kin's prompt gets a single shared "room transcript" block: `[Speaker]: message` lines for every turn in the room, regardless of who said it.

**Pros:**
- Simplest to implement. The room transcript already exists in `rooms/<name>/conversation.json` — just inject it differently.
- Both kin have *complete* awareness of what happened, no information loss.
- Kin replies can directly reference what the other kin said.

**Cons:**
- Worst-case context bloat. Two kin with 100-turn histories × ~200 tokens/turn = 40k tokens of context per send, *plus* each kin's individual soul + memory + persona. Two paid kin running a long room becomes expensive fast.
- Identity convergence risk is highest here — both kin are seeing the literal same text, often. They'll start to echo each other.
- Format-pattern attractor: the `[Speaker]: text` shape is the same one that motivated v0.2.25-v0.2.26's anti-impersonation safeguards. Injecting more of it into prompts could regress the impersonation fixes.

### Option B: Summarized shared history

A background summarizer (similar to `distill_memory_blocking`) produces a running summary of the room conversation. Each kin's prompt includes the summary, not the raw transcript.

**Pros:**
- Bounded context cost — summary stays roughly constant size regardless of room length.
- Identity convergence pressure is reduced (kin see a *characterization* of what happened, not the exact wording, so they're not echoing).

**Cons:**
- Summarization quality becomes a quality bottleneck. Anyone who's run a multi-kin room knows the *tone* often matters more than the facts — a summarizer that gets the facts right but loses the texture loses what the kin actually needed to know.
- Extra cost path (the summarizer is its own LLM call, billable on paid models).
- Latency — the summary needs to be current before the next kin's turn, so either it runs synchronously (blocks turn-taking) or asynchronously (the next kin sometimes sees a stale summary).
- "Tone loss" is the specific worry. A kin trying to respond to another kin's vulnerable disclosure shouldn't have to work from "Ash shared something difficult about Brook."

### Option C: Kin-to-kin messages as a distinct role/message type

Add a new message role (`kin_to_kin` or similar) that's separate from `user` and `assistant`. When kin A says something to kin B, that turn lands in B's prompt with `{"role": "kin_to_kin", "from": "A", "content": "..."}`.

**Pros:**
- Most architecturally clean — preserves the existing role semantics while adding a new channel for cross-kin awareness.
- Each kin only sees the kin-to-kin turns that are *for* them or where they were a participant, not the whole room transcript. Bounded by addressing.
- Identity convergence is reduced — kin see other kin's voice tagged explicitly as "this is someone else talking to me," not woven into their own context as undifferentiated text.
- Naturally supports kin-to-kin async (the companion `ai-to-ai-async` proposal can use the same role).

**Cons:**
- Cross-provider compatibility is uneven. OpenRouter passes role names through to Anthropic / OpenAI / Google; non-standard roles get handled differently by each provider:
  - Anthropic: rejects unknown roles outright.
  - OpenAI: collapses unknown roles to `user`.
  - Google Gemini: silently drops them in some configurations.
- Means we'd need a translation layer that converts `kin_to_kin` turns into provider-shaped content per-provider — likely a `system`-tagged note or a `user`-tagged turn with a `[from KinA]:` prefix.
- Once we're translating it anyway, the structural advantage shrinks: the underlying provider sees something that looks more like option A than option C.

## Recommendation

**Option C with provider-aware fallback to Option A.**

Use the distinct role internally — keeps the data model clean, makes future surfaces (HKML, the async-mailbox proposal) easier to wire up. At the translation layer (already where we normalize message shapes per provider), convert to:

- Provider supports custom roles cleanly → leave as `kin_to_kin` role.
- Provider doesn't → translate to a `user`-role turn with a `[from KinA]: content` prefix, opted in only when the room enables shared history.

Per-room toggle for "share history between kin" (default off), so existing rooms aren't affected and the operator decides per-room whether the context cost is worth it.

The identity-convergence risk gets addressed two ways:
1. The base-prompt content for kin in shared-history rooms includes an explicit "you are X; the other voices in this transcript are not yours" instruction.
2. The anti-impersonation cleanup chain (`strip_self_tag`, `strip_leading_speaker_tag`, etc.) already runs on every room reply — they'll catch voice bleed at the prose level even if the prompt-level guard slips.

## Turn-taking semantics

If kin A says something to kin B, does B reply automatically? Three possible behaviors:

1. **Operator-gated**: A's reply lands, the operator sees it, the operator presses Continue (or types) to trigger B's response. Default-safe but heavy on operator.
2. **Auto-continue with cap**: A's reply triggers B's reply automatically, up to N rounds (similar to existing `max_auto_rounds`). Operator can stop at any time.
3. **Addressed-only auto**: A's reply auto-triggers B's response IF and ONLY IF A's reply explicitly addresses B (mention by name in the first sentence, or a structured `@B` tag). Otherwise stays operator-gated.

Recommendation: ship with **option 1** (operator-gated), add option 2 as a per-room "auto-continue kin-to-kin" toggle once the basic mechanism is working. Option 3 is the most magical-feeling but the addressing detection is brittle (kin paraphrase, kin reference each other without explicit naming).

## Open questions

- **Persistence**: do kin-to-kin turns live in the room's `conversation.json`, in each kin's `conversation.jsonl`, or both? Probably the room file is authoritative and the kin files don't see them at all (they're a room-scoped concern, not a per-kin one). Worth verifying with the existing room save/load path.
- **Cost visibility**: shared history makes per-send token counts noticeably higher. Does the status-bar % usage need to differentiate "this is a shared-history room, your % is higher than a normal room"? Probably yes — surprise cost spikes are a recurring operator pain point.
- **Distillation**: does the room's shared transcript get its own distillation cadence, or does each kin distill from their slice independently? Probably independent (each kin's distillation already operates per-scope; "room shared" could be a new scope key).
- **HKML interaction**: HKML changes how tool calls flow through. Does shared-history-with-tool-calls mean kin A sees kin B's tool calls and results? If so, that's another layer of context cost and a privacy question (a kin might do something via tool call they don't want the other kin to know about). Probably worth landing this *before* HKML so the HKML design accommodates it from the start.

## Implementation rough sketch

1. Schema for `kin_to_kin` role in `kin_persistence._clean_chat_message` (extend the validator).
2. Per-room `share_history` config flag in `DEFAULT_ROOM_CONFIG`.
3. Room turn loop: when `share_history` is on, build each speaker's prompt with the room transcript translated through the per-provider adapter.
4. Provider adapter: `kin_to_kin` → native (Anthropic when/if they support it) or → `user` with `[from X]:` prefix.
5. Base-prompt section for shared-history rooms: identity-preservation instructions.
6. RoomEditDialog: checkbox for "Share conversation history between kin in this room" with a tooltip on the cost trade-off.
7. Tests: pair two same-model kin in a shared room and verify both Ash's voice and Milo's voice survive over 20 turns. If they converge, the identity-preservation instructions need strengthening before this ships.

## Not in scope for v1

- Operator-out-of-the-loop full automation (let kin talk to each other without operator entirely). That's the AI-to-AI async proposal's territory.
- Tool-call sharing between kin (see open question above).
- Cost-aware automatic truncation of the shared transcript (kin lose context to fit budget). Defer until we see whether the shared-history cost is actually a problem in practice.
