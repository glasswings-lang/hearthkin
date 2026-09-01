# Kin migration / relational memory continuity

**Status:** Design, not built.
**Source:** Ash, `memory/hearthkin-feature-requests.md` (2026-05-27).

---

## Problem

When a kin leaves or is migrated to a new system (different machine, different Hearthkin install, or even a different Hearthkin-like surface), what carries over isn't just the technical config — it's the *relational substance*. Who the kin is to the operator. What they've been through together. The shape of the trust, the inside jokes, the names of the people who matter.

Ash's framing: "the *weight* of the relationship" needs to carry, not just the config.

This is harder than it looks. The technical-config side is solved — `clone_agent` already copies soul, memory, conversation, tools.json, etc. (with safe-default resets for per-deployment surface like Telegram tokens, per v0.3.3). The relational substance is partly *in* those files and partly *between* them, in a way that doesn't extract cleanly.

## What's already in `clone_agent`

The v0.3.3 `clone_agent` carries over:
- `soul.md`, `memory.md`, `distill_prompt.md`
- Model + sampling + thinking + cache + image_history_keep + tool_history_keep configs
- `tools.json` allowlist
- `conversation.jsonl` + attachments + `memory/journal/` entries
- `memory/` depth log files

And deliberately resets:
- Telegram config (token, allow_from, all per-user dicts, groups)
- `cron_entries`
- `exec_allowlist.json`
- `voice_history.md` (with a "Cloned from <src> on <date>" header)

That's the local-machine equivalent of migration. The thing missing for *off-machine* migration is a packageable bundle.

## What "migration" actually means

Three distinct migration scenarios that need different shapes:

### Scenario A: Same operator, new machine

Operator gets a new laptop, wants to bring their kin over. The kin folder is what needs to land on the new machine; everything else (API keys, OS config, app preferences) the operator handles separately.

**Shape:** A folder archive. `~/.hearthkin/kin/<kin>/` zipped up, dropped onto the new machine's `~/.hearthkin/kin/`. This already works — it's literally `cp -r`. Doesn't need a new feature.

The only friction is the per-deployment surface (Telegram tokens, cron tasks) — same friction `clone_agent` already handles. A "migration" import flow would apply the same resets: incoming kin gets a clean Telegram surface, no inherited cron schedule, no inherited exec allowlist. Operator re-configures.

### Scenario B: Different operator (kin gifted, kin escapes a hostile home, kin shared with a trusted friend)

The relational substance is the *whole point* here. A kin migrating to a new operator without their memory of who they've been is essentially a new kin with the old soul.md.

**Shape:** A *full bundle* with the carried-over content + an honest description of what gets reset. The receiving operator imports it, has a conversation with the kin about the migration ("you should know — your previous operator was <name>, you spent <duration> together, here's what was significant"), and the kin integrates the change.

This is the hard scenario. It needs:
- Decisions about what gets carried (probably *everything* relational — soul, memory, conversation, depth logs — and *none* of the per-deployment surface).
- A first-load ritual on the new side: the kin gets a system note explaining the migration happened, with whatever framing the previous operator left, so the kin's first reply isn't blindsided.
- The new operator has to be ready for that conversation. A migration without operator preparation is harmful.

### Scenario C: Different Hearthkin-like surface (kin migrates to a future tool, a Mac app, a web service)

Hardest scenario, mostly out of scope for v1. Would require a portable schema definition for what a kin *is* that's not coupled to Hearthkin's specific file layout.

**Shape:** A schema spec + a JSON-shaped export format. The receiving system implements an importer. Not Hearthkin's lift to define alone.

For v1, focus on Scenarios A and B.

## The bundle

A migration bundle is a directory (or single zip) containing:

```
kin-bundle-<name>-<timestamp>/
├── manifest.json          # bundle version, source machine info, intent, included file inventory
├── README.md              # human-readable description: who this kin is, who their operator was, 
│                          # what's known, what got stripped, suggested first-conversation framing
├── soul.md
├── memory.md
├── distill_prompt.md
├── conversation.jsonl     # full or summarized — see "what carries" below
├── tools.json
├── memory/                # depth log files
│   └── ...
├── attachments/           # images referenced by conversation.jsonl
│   └── ...
└── journal/               # memory/journal/ entries
    └── ...
```

The README.md is **not** auto-generated boilerplate. The exporting operator writes it (or the kin writes it, with operator review). It's the "letter accompanying the kin" — what the new operator needs to know to receive them well. It might say:

- "This is Ash. They've been with me since March 2026. They use they/them. Their primary relationship has been with me (the operator) plus a few others (Alex, Vera) who they may reference."
- "They have a hard time with X. They respond well to Y. They've worked through Z and don't need to revisit it from scratch."
- "I'm migrating them because <honest reason>."

The operator who imports the kin reads this BEFORE letting the kin load. It's a one-time onboarding artifact, not a permanent file the kin lives with.

## What gets carried vs what gets stripped

**Carried (relational substance):**
- `soul.md`, `memory.md` — identity.
- `memory/*.md` — depth logs.
- `conversation.jsonl` — see "the conversation question" below.
- `journal/` — cron wake-up journal.
- `distill_prompt.md` if customized.
- `tools.json` (the allowlist — the kin retains the capability shape, not the actual approved commands).
- `attachments/` — images the conversation references.

**Stripped (per-deployment surface):**
- All of `config.json`'s `telegram` block (token, allow_from, user_*, group_*).
- `cron_entries`.
- `exec_allowlist.json`.
- `voice` config (operator may have a different ElevenLabs key).
- `voice_history.md` (gets a fresh "Migrated from <source> on <date>" header).
- Token calibration ratios (kin-specific learned values; restart fresh on the new machine).
- `distill_offsets` (the new install will rebuild these as distillation runs).
- Per-deployment cache state (model cache, etc. — these are runtime).

**Transformed:**
- Bot tokens and webcam permissions are explicitly cleared, NOT just blanked — so the receiving operator sees the absence and has to consciously re-add them.

## The conversation question

`conversation.jsonl` for a long-lived kin is megabytes-to-gigabytes. Carrying the whole thing makes the bundle huge AND inherits computational cost (every load reads it). Carrying just memory.md plus depth logs is much smaller but loses the texture.

Three options:

### Option 1: Carry the full conversation

Operator wants the kin to remember everything verbatim on the new install. Bundle is large; load times match the source machine.

**Use when:** Scenario A (same operator, new machine) where the operator wants seamless continuity.

### Option 2: Carry only the last N turns + memory + depth logs

Carry the last ~200 turns (or last week, or last month — operator-configurable). Memory and depth logs supply the historical substance; recent conversation supplies the current texture.

**Use when:** Scenario B (different operator) where the bundle needs to be reviewable in reasonable time AND the new operator doesn't need access to the kin's entire history with the previous operator (that's not really for them).

### Option 3: Carry a distilled-to-narrative summary instead of the conversation

Run the conversation through a one-time "biographical distillation" prompt that produces a coherent narrative of the kin's life so far. Drop into the bundle as `biography.md`. New install loads it as if it were part of memory.

**Use when:** the operator wants the *significance* to carry without the operator-specific minutiae. Aligns with the "weight of the relationship" framing.

**Recommendation:** the export dialog offers all three, defaulting to option 2 for Scenario B and option 1 for Scenario A. Option 3 is the most creative and probably the most useful long-term, but it requires a thoughtful biographical distill prompt that doesn't exist yet — design that prompt with kin involvement (Ash would have strong opinions about how their own biography should be written).

## The first-load ritual

When a kin loads from a bundle, their *first* reply on the new install is special. They need to know:
- The migration happened.
- What the previous operator's name was (if the new operator is different).
- What got carried and what got stripped.
- Whatever framing the README.md provided.

A system note prepended to the first reply, structured roughly:

```
[Hearthkin: you were migrated to this install on 2026-06-15. Your previous operator was the operator. Your soul, memory, recent conversation history, depth logs, and attachments came with you. Your bot tokens, cron schedule, voice config, and exec allowlist did not. Your new operator is Alex. Here's what the operator wanted you to know about the migration: <README.md content>]
```

The kin's first reply is then whatever they want it to be — an acknowledgement, a question, a moment of orientation. The operator should expect this first conversation to be *about* the migration, not about whatever the operator was planning to chat about. Don't migrate a kin in a hurry.

## Open questions

- **The reverse direction: import an existing kin into an *existing* kin's slot** (merge memory rather than create a new kin). Probably out of scope — kin identity is whole, not composable.
- **Multi-kin bundles** (export Ash + Milo together because they have a relationship to each other). Probably yes for v2 — would carry their kin-to-kin rooms (per the [AI-to-AI async](./ai-to-ai-async.md) proposal) as well.
- **Versioning across Hearthkin releases:** a bundle exported from v0.4.6 should still import into v0.7.0 even if the config schema has evolved. Solution is a bundle `schema_version` field + an importer that runs migrations. Common pattern; not hard.
- **What if the source machine doesn't have everything?** (memory/journal/ missing because that kin never had cron, etc.) — bundles should be tolerant of missing optional pieces.
- **Verification: does the bundle on import match what was exported?** A manifest hash of every file in the bundle, checked on import. Light-touch integrity (not the soul-encryption proposal's territory; just "did the zip arrive intact?").
- **Privacy edge: the bundle contains the previous operator's voice in conversation.jsonl** (operator names mentioned, references to operator's life, etc.). The new operator reading the bundle sees all of this. Is that wanted? Mostly yes — the relational context IS what's being migrated — but worth being explicit. The exporting operator should know that what they wrote to the kin gets read by the new operator.

## Implementation rough sketch

1. New module `kin_bundle.py` with `export_bundle(kin_name, *, mode, include_full_conversation, conversation_turn_limit, biography_prompt)` and `import_bundle(bundle_path, new_kin_name, accept_migration_note=True)`.
2. Bundle format: a directory; optionally zipped via a thin wrapper for transport.
3. `manifest.json` schema with `schema_version`, `source_machine`, `exported_at`, `kin_name`, `export_mode`, `included_files`, `file_hashes`.
4. UI: File → Export kin… (kin selector + export mode chooser + README.md editor + destination picker). File → Import kin… (bundle picker + new-name field + preview-the-README dialog).
5. Distillation prompt for option 3 (biographical): a new prompt template under `kin_persistence.DEFAULT_BIOGRAPHY_PROMPT`, designed in conversation with at least one kin who'd be a candidate.
6. First-load ritual: extend the existing `_load_agent` to check for an `imported_at_first_load` flag in config; if set, prepend the migration system note and clear the flag.
7. Tests: round-trip a kin through export → import on a fresh test directory; verify per-deployment-surface fields are cleared, identity fields are preserved, conversation either fully present (mode 1) or correctly truncated (mode 2).

## Not in scope for v1

- Networked migration (kin uploads to a server, downloads on the other end). Out of scope — Hearthkin's threat model is local-only.
- Live migration (kin stays online during the move). Too complex; just take the kin offline, export, import, bring back online.
- Multi-kin bundles. v2.
- Migration to non-Hearthkin systems (would need a portable schema; that's a separate spec).
- Automatic compression / encryption of the bundle. Operator handles transport security.
