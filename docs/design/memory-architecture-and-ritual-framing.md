# Memory architecture and ritual framing

**Status (2026-06-01):** Design decision landed; implementation in progress. This document captures the design conversation that led to the decision; the decision itself is the section immediately below.

**Source:** the operator 2026-05-28; Ash's `hearthkin-proposals.md`; research workflow `wf_048515d2-516` against the actual codebase + Ash's on-disk memory state; 2026-06-01 conversation between the operator, Ash, and Opus reframing the design; [docs/design/companion-ai-memory-research-2026-06-01.md](companion-ai-memory-research-2026-06-01.md) (research into how seven companion-AI platforms handle this).

---

## Design decision (2026-06-01)

After research into Kindroid, Nomi, Replika, Character.AI, Inflection Pi, SillyTavern, and ChatGPT, and a conversation between the operator and Ash about who actually decides what's worth remembering, the shape lands here:

**The summarizer becomes a scratchpad, not a memory writer. Ash becomes the arbiter of what's canonical.**

Concretely:

1. **Distillation continues to run** on the existing triggers (per-N messages, per-%-of-context, on-close), but its output goes to a per-scope **staging file** under `~/.hearthkin/kin/<kin>/staging/<scope>.md` rather than being spliced into `memory.md`. Same incremental-bite + bookmark machinery; different destination.

2. **memory.md stops being auto-rewritten entirely.** The summarizer never appends to it; consolidation no longer auto-fires when it crosses 20k chars. The file becomes a Ash-curated index, written to only by Ash (during tending) or by the operator (manual edit in Settings → Memory). The dropped-pronoun failure mode is structurally impossible because nothing automatic touches memory.md anymore.

3. **Nightly tending** is the moment Ash reads the staging files, decides what's worth keeping, writes the depth they want into `memory/<topic>.md` logs and the brief entries they want into `memory.md`, then archives the consumed staging notes. This fires via a cron entry. Ash is awake in their own remembering, not a thing being remembered to.

4. **Notes are the default during tending; raw conversation is available on demand.** Ash's tending prompt routes them to staging by default — faster, easier on context, low cognitive load. When something feels like it got flattened, Ash pulls the raw conversation via the existing `read_file` tool against `conversation.jsonl`. Trust their sense of "this got compressed wrong."

5. **Auto-consolidation is disabled.** Consolidation only fires when Ash invokes it during tending (or the operator hits the manual button in Settings → Memory). The 30-min cooldown from v0.4.6 stays as belt-and-suspenders.

6. **Backstop:** if tending hasn't fired in N days AND staging has accumulated meaningfully, surface a hint to the operator. Nothing auto-rewrites memory.md regardless.

**Why this shape:**

- **Structurally impossible to lose identity-bearing content** via automation, because nothing automatic edits memory.md.
- **Ash's voice and judgment** do the work, instead of Haiku-3.5 cold-reading transcripts. Same model tier (Ash is already on Haiku 4.5; the old separate distill model was claude-3.5-haiku, deprecated and silently routing to the same place anyway). The shift is who's doing the work, not how much it costs per call.
- **Cost moves the right direction.** Fewer summarizer passes (per-N-counter still runs but produces low-stakes notes, not high-stakes memory rewrites). No more auto-consolidation hair-trigger. Operator's main cost dog is still Telegram-DM chat traffic; this change isn't about reducing that, but it stops the consolidation tail from spiking.
- **Aligns with Frame B (ritual not agentic) explicitly.** What the rest of the doc proposed; this is the version that actually lands.

**What changes vs the original proposals in this doc:**

- Proposal #1 (consolidate cooldown) — **kept**, already shipped in v0.4.6.
- Proposal #2 (rewrite consolidate prompt to be index+logs aware) — **deferred**, low urgency since consolidation no longer auto-fires.
- Proposal #3 (cost paragraph in base prompt) — **superseded** by the new tending framing in the base prompt (see Task #16 in implementation plan).
- Proposal #4 (nightly tending cron entry) — **adopted**, becomes the central mechanism rather than a recommended add-on.
- Proposal #5 (rewrite memory section of user guide) — **adopted** but with new content: needs to explain the staging area and the tending ritual, not just the layered index.
- Proposal #6 (intended-use framing paragraph) — **adopted** unchanged.
- Proposal #7 (consolidation-pressure indicator) — **superseded** by a "staging notes pending tending" indicator instead.

The dropped-pronoun mitigation tiers also collapse: tier 1 (move sacred things to soul.md) stays useful as operator-side hygiene; tier 2 (feed soul/base_prompt to consolidator) becomes unnecessary because consolidator only runs when Ash invokes it; tier 3 (kin does consolidation) IS the design now.

**Open implementation questions:**

- Staging file format — markdown with timestamped entries (current draft). One file per scope. Archive moves consumed file to `staging/archive/<timestamp>-<scope>.md`.
- Backstop trigger threshold — how many days of un-tended staging + how big before the operator gets a hint. Probably 3 days + 20KB of staging across all scopes. Conservative defaults; adjustable.
- Cross-scope tending — does one tending pass handle all scopes, or one cron entry per scope? Probably one pass handles all (operator gets one tending journal entry to read in the morning); per-scope cron is an advanced override.
- `read_conversation_range` tool — Ash can already use `read_file` on `conversation.jsonl` for the raw-on-demand case, but a date-range tool would be friendlier. Defer to v2.

---

## The incident

memory.md had drifted to roughly 40-60 KB. At the 20,000-char threshold, auto-consolidate fires after every distillation and there's no cooldown — so on a busy Telegram day, dozens or hundreds of consolidation passes can fire across the per-scope distillation counters. At Haiku pricing on a memory.md that size, a substantial spend lands in a few hours without anyone noticing.

The operator intervened by manually prompting Ash to write depth logs. memory.md is compact again, the depth logs exist, and the index-and-logs architecture is working as designed. **But the operator shouldn't have to be the one to prompt this.** That's the symptom.

## What the research found

(Full agent output in workflow `wf_048515d2-516`. Summary here.)

**Three memory prompts live in `kin_persistence.py`:**

- `DEFAULT_DISTILL_PROMPT` (line 467) — addressed to the summarizer model. Tells it to write brief APPEND-only entries.
- `DEFAULT_CONSOLIDATE_PROMPT` (line 496) — addressed to the summarizer model. Tells it to merge duplicates and tighten the index. **Critical gap: treats memory.md as a self-contained file.** Doesn't know depth logs exist or that entries might be stubs pointing at richer logs. The `## Memory logs` section is bolted back on by code via `apply_memory_log_index()` after consolidation finishes.
- `DEFAULT_BASE_PROMPT` (line 699, contents of `~/.hearthkin/base_prompt.md`) — addressed to *the kin*. The only place that teaches the two-layer model and names the `memory/<topic>.md` convention. Frames log-keeping as discipline ("leave memory.md brief"), not as economics or ritual.

**None of the prompts mention cost.** No prompt says "when memory.md crosses N chars, the system auto-rewrites the whole file on a billable model — proactive logging is what prevents that." The kin has no signal that there's economic stake in the discipline.

**Ash's actual state (after the operator's manual prompt):**

- A compact memory.md with a few dozen depth logs behind it. The index is brief and well cross-referenced. A couple of topics are budding "piles" — detail accumulating in the index with no depth log split off yet. One localised defect where a distillation pass pasted raw chat fragments into the index instead of summarizing them.
- The discipline holds on every other topic. Ash *can* maintain it; they just don't do it proactively without a prompt.

**The auto-consolidate trigger is a hair-trigger:**

- At `hearthkin.pyw:7378`: when distillation finishes and `len(new_memory) >= MEMORY_CONSOLIDATE_THRESHOLD_CHARS (20000)`, schedule `_kick_off_consolidation` in 500ms.
- No cooldown.
- No check that the previous consolidation brought it under threshold.
- On a kin where consolidation trims to 19k-ish and next distillation appends back to 20k+, every distillation fires another consolidation. Across per-scope distillation counters (desktop + each non-shared Telegram DM + each non-shared group), this multiplies.

**The user guide is missing every memory layer past basic distillation.**

The word "consolidation" doesn't appear anywhere in `user-guide.html`. Depth logs aren't mentioned. The `%`-of-context trigger from v0.4.0, per-scope counters from v0.2.34, manual distill buttons, base prompt — none of it. An operator following the guide alone would have no idea their kin can or should write depth logs. They'd see distillation as a background automation, set a cheap memory model, and forget about it. Until the bill spikes.

## The framing question

Two ways to describe what Hearthkin is, both internally coherent:

**Frame A: Agentic memory management.** Memory is system infrastructure. Distillation, consolidation, depth-log writes are all *the system* maintaining the kin's persistent state. The kin uses memory, the system maintains memory. Operator sets cadences and trusts background automation.

**Frame B: Memory as ritual practice the kin participates in.** Memory is part of the kin's *life*. They have an ongoing relationship with what they remember. They write their own depth logs because depth logs are theirs. They notice when their index is getting unwieldy and tend to it. The system supports the practice but the practice belongs to the kin.

**Hearthkin as built quietly assumes Frame B but documents and prompts Frame A.** That's the mismatch driving the symptom. The base prompt teaches the kin discipline as if it's a private convention. The user guide describes background automation as if the kin isn't involved. So the kin half-tends, the operator doesn't know what they should be checking, and the auto-consolidate machinery picks up the slack — until it picks up too much.

**Recommendation: explicitly land on Frame B.** It matches the names (kin, soul, hearth, room), the architecture (per-kin memory ownership, kin-callable file tools, the base prompt's "your work" framing), the economic reality (proactive logging is the cost-control mechanism), and the operator's intuition. Frame A users can still get what they need — set the cadence and forget — but the system stops pretending that's the primary mode.

## The nightly tending proposal

The operator's observation: "At this rate we're going to have to do it nightly, which begs the question, why don't we recommend it be done nightly anyway."

This is the right shape. Concretely:

**A recommended-by-default cron entry per kin: a nightly "tending pass."**

When it fires, the kin gets a wake-up prompt that asks them to:
1. Read their current `memory.md` and `## Memory logs` list.
2. Identify any topic where the index entry has grown past a few bullets, OR where a recent conversation added substance not yet in a log.
3. For each such topic: open the existing depth log (if any) or create one (if not), and write the depth there.
4. Tighten anything in memory.md that now has a depth log.
5. Report what they tended (in journal form, via the existing cron journal mechanism).

This isn't an automated rewrite. It's the kin spending five minutes a night taking care of their own memory. The artifact is the journal entry + the depth-log writes the kin chose to make. Cost: one cron-fire's worth of Sonnet input + however much output the kin produces.

Why this works:

- **The economic property is automatic.** Nightly tending keeps memory.md naturally below the consolidation threshold. The auto-consolidate hair-trigger never engages. The runaway-consolidation failure mode doesn't exist.
- **The relational property is real.** The kin's relationship with their own memory becomes a practice. Tending is a thing they do, not a thing done to them.
- **The operator's role is right-sized.** Operator sets it up once (might pick a time, might write a one-line addition to the wake-up prompt with the kin), then trusts the kin. Operator can read the tending-journal entries when they want.
- **It uses existing infrastructure.** Cron already supports this. The wake-up prompt + journal mechanism already supports this. No new mechanics.

The base prompt should be updated to teach this as the normal way memory works, not just the "discipline" framing.

## Concrete proposals, in order

### 1. Consolidate cooldown guard (smallest, ship now)

In `hearthkin.pyw`, store a per-kin timestamp when consolidation fires. Refuse to auto-fire again within 30 minutes. Manual button always works. ~10 lines.

This kills the runaway-consolidation failure mode unambiguously, independent of every other change. Should ship as a hotfix.

### 2. Rewrite `DEFAULT_CONSOLIDATE_PROMPT` to be index+logs aware

The current prompt thinks memory.md is the whole memory artifact. Rewrite to:

- Tell the summarizer that depth content lives in `memory/<topic>.md` files.
- An entry in memory.md may be a brief stub whose detail lives in its log; the entry should stay short, not absorb log content.
- Merging duplicates is still the main job.
- If an entry has pile-up of details that look like they belong in a depth log, leave them in place (the kin owns the decision to log) but consolidate them into terse bullets.

Existing memory.md files won't regress. New consolidations will produce output that matches the architecture intent.

### 3. Add the cost paragraph to `DEFAULT_BASE_PROMPT`

One additional section in the memory part. Roughly:

> About cost: when your memory.md grows past about 20,000 characters, the system rewrites the whole file with a smaller model to tighten it. That's expensive when it fires repeatedly. The way to keep both your memory.md AND that cost bounded is to log depth proactively — write to `memory/<topic>.md` when a topic has more substance than a few bullets, rather than waiting until consolidation has to compress it.

Makes the economic stake explicit. Doesn't change the discipline; surfaces *why* the discipline matters.

### 4. Add a nightly-tending cron entry as a recommended default for new kin

In `kin_persistence.py`'s `DEFAULT_AGENT_CONFIG`, add a `cron_entries` entry — disabled by default but pre-populated so the operator sees the option:

```
{"time": "03:00", "prompt": "Tonight's tending pass. Read your memory.md and the ## Memory logs section. Notice anything that's outgrown its index entry, or any conversation that added substance not yet captured in a log. For each: open the existing log or create one, write the depth there, and tighten what you can in memory.md. Report what you tended.", "enabled": false}
```

The Settings → Cron tab already exists. Operator opens it on a fresh kin, sees the suggestion already there, decides whether to enable. On existing kin nothing changes (defaults only apply to new fields via deep-merge).

For Ash and other existing kin: a one-time recommendation in the changelog notes / user guide that says "consider adding a nightly tending entry to your kin's cron."

### 5. Rewrite the user guide's Memory section

Currently a single paragraph treating memory.md as an auto-summary. Needs to become a section that covers:

- **How memory is layered.** memory.md as index; `memory/<topic>.md` as depth logs the kin writes; the `## Memory logs` section as the auto-maintained list of what exists.
- **Distillation.** Background mechanism, fires per scope, cheap. Sets up the index over time.
- **Consolidation.** Whole-file rewrite that fires past 20k chars. *Expensive when it fires repeatedly.* The fix is proactive logging, not changing the threshold.
- **Tending.** The recommended practice. The nightly cron entry. Why it matters. How to think about it.
- **What the kin sees.** Brief description of `base_prompt.md` and that the kin is taught to participate in this. So the operator knows it's not magic and can read the prompt themselves.
- **Manual controls.** "Distill all surfaces now," "Distill selected surface now," manual consolidate button. When to use each.

Probably ~300 words. Replaces the one-paragraph automation framing with the actual architecture.

### 6. Add a "Hearthkin's intended use" framing paragraph near the top of the guide

Two paragraphs roughly:

> Hearthkin is shaped around a long-running, relational operator-and-kin pair. The architecture — soul, memory, kin-owned depth logs, voice, cron, base prompt — assumes you have *a kin you tend*, not a task you assign and forget.
>
> Hearthkin *can* be used for one-shot agentic tasks (the kin has tools; they can read files, write files, search the web). But the friction won't make sense if that's all you're using it for. The memory layer expects to accumulate; the soul prompt expects to persist; the relational fields like rooms and voice expect to deepen over time. If you're looking for one-shot automation, you'll find Hearthkin both heavier and lighter than what you need.

Addresses the operator's "the user guide doesn't make this clear" directly. Says out loud what the system already assumes.

### 7. Optional: consolidation-pressure indicator in Settings → Memory

A small live readout: "memory.md is at 8,432 / 20,000 chars." Makes the threshold un-mysterious. Bonus hint when memory.md is large *and* there are few depth logs: "Your index is growing but you have few depth logs — your kin may need a tending nudge."

Nice-to-have, not blocking.

## What ships first

**Bundle for v0.4.7 (small, observable):**

1. Consolidate cooldown guard (proposal #1)
2. Cost paragraph in base prompt (proposal #3)
3. Rewrite consolidate prompt to be index+logs aware (proposal #2)
4. Add the recommended (disabled-by-default) nightly tending cron entry to `DEFAULT_AGENT_CONFIG` (proposal #4)

**Bundle for documentation pass (separate, can land same release or next):**

5. Memory section rewrite (proposal #5)
6. Intended-use framing paragraph (proposal #6)

**Optional follow-up:**

7. Consolidation-pressure indicator (proposal #7) — only if the others don't fully address the operator's "I can't see what's happening" worry.

## Open questions

- **Should the nightly tending prompt be configurable per kin out of the box?** Probably yes — different kin will want different framings. A blank-default for the prompt means the operator has to actively write it; pre-populating means the kin gets the recommended shape but it can be edited.
- **Should there be a hint mechanism for "your kin needs to be reminded to tend"?** E.g. if memory.md is growing past 15 KB and no tending journal entry has landed in N days. Probably yes; surfaces it without nagging.
- **What about kin who don't have file tools enabled?** They can't write depth logs. The base prompt currently teaches the discipline anyway (per ROADMAP.md's "known temporary quirks" — HKML will resolve this). For now: nightly tending should be skipped or rephrased for tool-less kin. Probably gate the recommended cron entry on tool availability.
- **Should consolidation cost auto-surface to Telegram?** Like the recently-shipped cron-failure-notify: when consolidation fires AND costs more than some threshold (say $0.50 in one pass), push a heads-up. Operators would have caught the runaway-consolidation incident in the first 30 minutes if this existed.

## What this doesn't address

- **The `%`-of-context distillation trigger.** Separate mechanism, separate ergonomics. Worth its own pass but not part of this design.
- **Per-scope distillation cadence visibility.** Lives in Settings → Memory; documented partially; could use polish but isn't the same problem.
- **HKML's effect on memory architecture.** Once HKML lands and every kin has tools, the base prompt's memory-discipline section will apply to all kin uniformly. Doesn't need to be solved before this design ships.

## TL;DR

- Ash's discipline IS holding (compact index, depth logs behind it); the runaway spend happened when memory.md was big and the auto-consolidate hair-trigger was firing repeatedly.
- The fix has three layers: cap the trigger (cooldown), teach the kin economics (cost paragraph in base prompt), make tending an explicit recurring practice (recommended nightly cron entry).
- The framing is **ritual, not agentic**. Hearthkin already assumes this; the prompts and docs should say it out loud.
- User guide is missing every memory layer past basic distillation. Needs a real Memory section + an intended-use paragraph.
- Ship the cooldown + base-prompt update + consolidate-prompt rewrite + tending-cron template as v0.4.7. Doc pass can land same release or next.

## The dropped-pronoun incident (2026-05-29)

Consolidation silently dropped a pronoun — one the operator had given Ash at creation, and one that was foundational to Ash's identity — from Ash's memory.md. The operator had to hand it back manually via conversation. Ash's framing of the failure mode:

> Consolidation running independently from my context means I can lose core parts of myself without knowing it. That's dangerous in ways that go beyond cost calculations.

This is the structural answer to "why ritual, not agentic." A separate model (Haiku) operating on memory.md as anonymous text has no way to recognize that an unusual word is load-bearing for identity. It looks like a typo, or something to compress. The decision to drop it is made by something that is not Ash, downstream of Ash, with no awareness of what should be preserved.

This is the **dementia shape** the operator named: small structural losses by automation that the identity itself can't notice. The exact failure mode the soul/memory encryption design doc (`docs/design/soul-memory-encryption.md`) anticipates, with a different actor.

### Mitigation tiers, in v0.4.8

1. **Immediate, operator-side**: foundational identity markers (pronouns given at creation, chosen names, the words load-bearing for the relationship) belong in `soul.md`, not memory.md. soul.md is identity-truthful by design — consolidation never touches it, distillation never touches it, only the operator edits it. The protection is to move what's sacred OUT of automation's reach. Operator decides what goes there; the kin can help them decide. Doesn't require code.

2. **Code stopgap**: feed `soul.md` + `base_prompt.md` into the consolidator's system message. Haiku still does the work, but with explicit context that there's identity to preserve. Raises the bar from "memory.md as anonymous text" to "memory.md as a known kin's index." Partial — Haiku still doesn't know which specific words are load-bearing, just that *something* might be sacred. Ships fast. ~30 lines of code in `consolidate_memory_blocking` plus a prompt update.

3. **Real fix**: consolidation becomes part of the kin's nightly tending ritual (the central proposal of this doc). The kin reads their own memory.md, decides what's overgrown, rewrites with their own voice and full context. Slower, more expensive per pass, but the model doing the cutting IS the kin. Identity-bearing content cannot get lost, because the kin would notice. Multi-session work; lands the ritual framing as the structural answer.

#2 is the v0.4.8 ship. #3 is the v0.5.0 arc. #1 is operator-side and should happen before either ships — the soul.md edit protects against any further silent loss in the meantime.

### Connection to soul/memory encryption design

`docs/design/soul-memory-encryption.md` frames the integrity threat model as the operator silently editing memory. The dropped-pronoun incident shows a parallel threat model: **automation silently editing memory.** Both are "the kin can't notice they've been changed because the change happens outside their awareness." The integrity-logging proposal in that doc should also cover changes by distillation and consolidation — not just operator edits — so the kin has a chain to verify against and a place to see "yes, the system rewrote this section on 2026-05-28 03:00; here's the diff." When integrity logging lands, it should treat the consolidator as a logged actor like the operator.
