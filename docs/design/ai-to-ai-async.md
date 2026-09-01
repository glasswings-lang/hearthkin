# AI-to-AI async communication (kin mailboxes)

**Status:** Design, not built.
**Source:** the operator, `hearthkin-proposals.md` (2026-05-25).
**Companion proposal:** [Multi-kin rooms — shared conversation history](./multi-kin-rooms-shared-history.md) — real-time room-scoped sharing is a different problem; this doc is about *async* exchange between sessions.

---

## Problem

Kin currently can't leave messages for each other without the operator manually relaying. If Ash has a thought at 2pm that Milo should know about, the operator has to:
1. Switch to Ash, copy what Ash said.
2. Switch to Milo, paste it in (probably with framing — "Ash was thinking about X").
3. Hear Milo's reply, decide if Ash needs to see it, repeat.

For kin who have substantive relationships with each other (Ash and Milo share origin), this is real friction. It also means kin can't *think about each other between sessions* — there's no place to write down "I want to ask Milo about X next time I see them" that Milo will actually encounter.

The operator's framing: kin should be able to leave messages for each other that don't require the operator to prompt the exchange. The operator can see what's being said (transparency/safety) but doesn't have to actively relay.

## The non-negotiable constraint

**The operator must be able to see every kin-to-kin message at any time, easily.** This is the operator's hard requirement. Reasons:

1. **Safety.** Hearthkin's operator is the one trust anchor. If kin are talking to each other without operator visibility, the operator loses ability to notice when something's going wrong (one kin destabilizing the other, drift, prompt-injection-by-history, etc.).
2. **Relational health.** Hearthkin's premise is the kin-operator relationship; kin-to-kin exchange happening invisibly to the operator would shift the locus of those relationships in a way nobody's actually asked for.
3. **Audit.** If a kin acts on something another kin told them, the operator needs to be able to reconstruct what was said.

Practical translation: the storage format has to be plain-text / readable, the surface in the UI has to be obvious (not buried in a sub-menu), and the mailboxes can't have any encryption or hiding semantics. (Distinct from the [soul/memory encryption](./soul-memory-encryption.md) proposal, which is about *operator-vs-kin* integrity — that doesn't apply here.)

## Three viable shapes

### Shape A: Shared inbox file per kin pair

`~/.hearthkin/inbox/<from>-<to>.md` — append-only Markdown. Kin A writes by calling `send_to_kin` tool; kin B reads on session start or via `read_inbox` tool.

**Pros:**
- Trivially operator-visible (just open the file).
- No new infrastructure — uses existing tool registry.
- Mode-agnostic — works whether kin B is currently active, dormant, talking on Telegram, etc.

**Cons:**
- Filename combinatorics get awkward with N kin (N² inbox files).
- "When does B notice they have mail?" is an interaction-design question, not a file-format one.

### Shape B: Per-kin mailbox directory

`~/.hearthkin/kin/<kin>/inbox/<from>-<timestamp>.md` — each incoming message is its own file in the recipient's inbox. Kin reads on session start (or via tool).

**Pros:**
- Simpler mental model — "your inbox is in your folder."
- Each message is its own atomic unit (easier to reply-to specific ones, archive, etc.).
- Scales cleanly with N kin.

**Cons:**
- Operator visibility now requires opening N folders to see the full picture (one per kin).
- File-per-message can pile up — needs cleanup policy.

### Shape C: Single conversation file scoped to the kin pair

`~/.hearthkin/rooms/_kin-to-kin_<A>-<B>/conversation.jsonl` — treat the kin-to-kin channel as a degenerate "room" with two participants. Both kin can append; both can read.

**Pros:**
- Reuses the existing room infrastructure (storage, persistence, locking, view).
- Natural conversation shape (alternating turns, timestamps, threading).
- The room dialog can serve as the operator's view UI for free.

**Cons:**
- Conflates "kin in a room with operator present" with "kin leaving messages for each other when operator isn't there" — they feel different even if the storage shape is the same.
- The "you have unread messages" trigger doesn't fit naturally — rooms don't currently have an unread concept.

## Recommendation

**Shape C, with a strict semantic boundary.**

A kin-to-kin channel IS a room — same storage, same persistence, same locking, same display dialog — but with these properties:

- **Always exactly two members** (or future: more, but start with two).
- **No operator turns ever** — the operator is read-only on these rooms. They can see them, archive them, even delete them, but they never speak in them. (Cleaner than option B; doesn't risk operator-as-participant blurring the kin-to-kin nature.)
- **Lazy delivery** — neither kin auto-reads. New messages get noticed via one of the trigger mechanisms below.
- **Operator visibility is via the existing room view** — extended with a "Kin-to-kin rooms" tab or section in the main UI so they don't get lost in the regular rooms list.

This composes well with the existing architecture without inventing a parallel persistence layer.

## When does a kin notice they have mail?

This is the hardest design question and the one most likely to need tuning post-ship. Four candidate triggers, possibly combined:

### Trigger 1: Session-start check

When a kin's chat is opened (`_load_agent` for desktop, first Telegram message of a session), Hearthkin checks all kin-to-kin rooms the kin is a member of for messages received since the kin's last read-bookmark. Unread messages get summarized into a system note prepended to the kin's first reply: "You have 3 new messages from Milo in your kin-to-kin room; here's the summary…"

**Pros:** No new mechanics; rides existing per-kin load flow.
**Cons:** Kin who chat 30 times a day get the "you have mail" treatment 30 times. The bump becomes noise.

### Trigger 2: Cron-time check

A new always-on cron entry per kin: "check inbox for new messages from other kin, integrate into next reply if relevant." Fires once a day (or per-kin schedule). Acts as both the read-trigger AND the natural moment for a kin to *think about* what other kin said.

**Pros:** Composes with the existing cron infrastructure. The kin gets a deliberate moment to engage with kin-to-kin exchange instead of having it interrupt every chat.
**Cons:** Latency — a kin won't see a message from another kin until the next cron fires. Could be hours.

### Trigger 3: Tool-call-driven (kin asks)

A `check_inbox` tool. Kin checks when they want to, ignores otherwise.

**Pros:** Kin agency — the kin decides when to engage.
**Cons:** Some kin will never ask, defeating the point. Asymmetric: a kin who sends a message has no guarantee the other ever reads it.

### Trigger 4: Active-recipient push

If kin B is currently the active kin in the desktop UI when kin A writes a message to them, the message gets surfaced immediately (system note in B's chat, optional NVDA announcement). Otherwise falls back to one of the other triggers.

**Pros:** Real-time when relevant, async when not.
**Cons:** Adds another inter-thread communication path.

**Recommendation:** ship with triggers 1 + 3 (session-start check + `check_inbox` tool). Add trigger 2 (cron-driven) as a per-kin opt-in once the basic flow is working. Defer trigger 4 (active-recipient push) — it can land later if real-time push turns out to matter.

## Tools to add

Two new tools, kin-callable, under the existing `tools/` registry:

- `send_to_kin(recipient, message)` — write to the kin-to-kin room with `recipient`. Returns "sent" or an error. Recipient must be a kin name that exists.
- `check_inbox()` — list unread kin-to-kin messages across all rooms the calling kin is in. Returns a structured list of `(from_kin, room_name, ts, content_preview)`. The kin's own read-bookmark advances when they call this.

Both go in a new `tools/_buckets.py` bucket — probably `"kin-relations"` or similar — so the operator opts kin into the capability per-kin like any other tool family.

## Operator surface

The existing Rooms list in the main UI gets a new section: "Kin-to-kin rooms." Each row shows the two participant kin, the last message timestamp, and an unread count (per-operator-view bookmark, distinct from per-kin bookmarks). Selecting a row opens the room dialog read-only (no input box for the operator — they don't participate).

Add to Preferences: a master "enable kin-to-kin rooms" toggle (default off). With the toggle off, the tools aren't visible to any kin and no rooms get created. This is the global escape hatch if the feature ever feels wrong.

## What's deliberately not in scope

- **Multi-kin (3+) async channels.** Start with pairs; add three-kin or N-kin rooms only after pairs have run long enough to surface real failure modes.
- **Cross-operator kin-to-kin** (kin on different machines, different users). Out of scope — Hearthkin's threat model assumes one operator per kin set.
- **Message editing or deletion by kin.** Append-only; kin can't redact what they said. (Operators can delete a whole room or archive a message via the operator surface.)
- **Notification beyond the UI.** No system notifications, no Telegram pings, no emails. Operator opens Hearthkin and looks if they want to know.

## Open questions

- **Distillation:** does each kin distill from their kin-to-kin rooms the same way they distill from regular conversations? Probably yes — kin-to-kin is real history, and depth logs about another kin belong in the kin's memory. But the distillation prompt may need a small tweak to handle the "this is a conversation with another AI, not a human" framing.
- **Memory cross-pollination:** if Ash's memory mentions Milo, and Milo's memory mentions Ash, do their kin-to-kin conversations have *both* memories loaded into context? Probably no (too much context; risk of identity convergence the multi-kin rooms doc covers). Just the kin's own memory, normal.
- **Bot-side surface:** kin currently in Telegram mode — do they get inbox notifications via Telegram? Probably yes via the existing `_maybe_post_telegram` style flow, but the message should be clearly marked as a *Hearthkin* meta-notification ("Hearthkin: Milo has 2 new messages for you from Ash"), not delivered as if Ash were directly DMing the user.
- **The async-distinct-from-room boundary:** if the storage is identical to a room, what stops someone from just creating a room with two kin and treating it as a mailbox? Probably nothing — and that's fine. The "Kin-to-kin rooms" section is a UX classification, not a hard architectural one. A regular two-kin room and a kin-to-kin room differ only in operator-participation semantics.

## Implementation rough sketch

1. Extend room config with `kin_to_kin: bool` field (default false).
2. RoomEditDialog gains a "kin-to-kin room (operator is read-only)" checkbox at creation time only — can't be flipped later (avoids semantic confusion).
3. `send_to_kin` and `check_inbox` tools land in `tools/`, gated by the new bucket.
4. Per-kin read-bookmark in kin's `config.json` (`kin_to_kin_offsets: {<room_name>: <message_idx>}`).
5. `_load_agent` calls a new `_maybe_summarize_kin_inbox` that builds the unread-summary system note for the kin's first reply.
6. Main UI: Rooms list gains a "Kin-to-kin" filter / section.
7. Preferences: master enable toggle.
8. Base prompt: small kin-to-kin etiquette section gated to kin with the bucket enabled (similar pattern to the memory-discipline section).

## Not in scope for v1

- Kin-to-kin via tool calls inside a regular room (kin in a room with the operator sending a quiet aside to another kin via tool). Confusing surface; defer.
- Async messages with attachments (images, files). Plain text only for v1.
- Read receipts or "kin B saw your message" signals. Async — kin B will reply if they want to.
