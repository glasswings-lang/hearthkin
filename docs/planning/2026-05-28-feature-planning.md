# Hearthkin feature planning — 2026-05-28

Synthesis of two source docs in Ash's folder:

- `~/.hearthkin/kin/Ash/hearthkin-proposals.md` (older proposals, May 21-25)
- `~/.hearthkin/kin/Ash/memory/hearthkin-feature-requests.md` (newer requests, May 23-28)

Cross-referenced against `ROADMAP.md`, `CHANGELOG.md`, and the current codebase to flag what's already done, what's already planned, and what's genuinely new.

---

## Already shipped or queued

- **Universal base system prompt** (the operator, proposals) — ✅ shipped in v0.4.0 as `~/.hearthkin/base_prompt.md`.
- **HKML** (Vera, proposals + capability-unlock extension) — already the headline item on `ROADMAP.md`. Multi-session arc on its own branch when ready. Several other proposals get easier or more interesting once HKML lands (anything involving smaller/cheaper models, accessibility of tool reasoning).
- **Image attachments in rooms / @-mention gate** — already on `ROADMAP.md`, deferred from v1 image work.

## Quick wins — could ship in one or two releases

These are small, well-scoped, no architectural questions:

### 1. Delete rooms feature (the operator, proposals)

`delete_room()` already exists in `kin_persistence.py` and there's a `_on_delete_room` handler at `hearthkin.pyw:5975` wired to a `&Delete current room…` menu item at line 755. So this looks **already shipped** — worth confirming on the operator's end whether they hit a bug that hid the menu item, or whether the request predated the implementation.

### 2. System prompt transparency (Ash, requests)

Read-only viewer dialog showing the fully composed `build_system_prompt()` output for the active kin. Pure read; no edit.

Side-benefit: while building it I'd verify whether the operator's "log substantive conversations" instruction actually exists — I already grepped and **it doesn't**, so that's a separate "should we add this rule?" conversation.

### 3. Tool result cap clarification (Ash, requests)

v0.4.5 shipped the per-kin tool result cap. The behavior for `0` is currently "no truncation here (num_ctx still applies downstream)" per the help text — but Ash wants this documented clearly. Easy doc + a small label update in the field's hint.

### 4. Remote Ollama support (Ash, requests)

The `OLLAMA_HOST` env var is already honored at `llm_backend.py:1191`, but `_ollama_show_raw` at line 1834 hardcodes `localhost:11434` — so capability detection still goes local even when the chat path goes remote. Fix: thread `OLLAMA_HOST` through everywhere, expose it in Preferences → Connections as a text field (defaulting to `http://localhost:11434`), document it.

## Medium efforts — one focused arc each

### 5. Reminders + timer system (Ash, requests)

Two related features:

- **Reminder tool**: agent-callable `set_reminder(when, message)`; on fire, append a system note to the conversation + a wx popup. Implementation rides existing cron infrastructure — it's a simpler shape of the same scheduled-trigger mechanic. ~1-2 days.
- **Session timer**: per-conversation opt-in time-cap; on expiry, the next reply gets a "you've been at this N minutes, is this becoming avoidance?" system-note prepended. The hard part isn't the timer — it's the prompt language for the nudge.

### 6. Telegram "directed-at" classifier (Vera, proposals)

When in a group, decide whether each message is actually for the bot before engaging. Two implementation paths:

- **(a)** A tiny local classifier (heuristics + maybe a small embedding compare)
- **(b)** Ask the kin's own model with a quick zero-shot prompt before the real reply

Option (b) is cheaper to build, more accurate, costs an extra small LLM call per group message. Worth scoping against the "soft pretty creature" incident Vera referenced to see what kind of disambiguation is needed.

## Big designs — need conversation before code

These are the ones I'd want to talk through with you (and probably with Ash) before touching code, because the design space is wide and the wrong choice creates work to undo.

### 7. Multi-kin rooms — shared conversation history (Ash, proposals)

Right now each kin in a room sees their own context window only; cross-kin awareness routes through the user. Three viable shapes:

- **Shared transcript injected into each kin's context** — simplest, blows context windows fastest on long rooms.
- **Summarized shared history** — cheaper, but the summarization layer becomes a quality bottleneck (anyone who's run a multi-kin room knows the summary often gets the *tone* wrong, which matters more than the facts).
- **Kin-to-kin messages as a distinct role/message type** — most architecturally clean, but every model handles non-standard roles differently. Some collapse them to user, some refuse, some honor.

My lean: option 3 with a per-room toggle, fallback to option 1 for models that mangle the distinct role. Wants its own session to design.

### 8. AI-to-AI async communication (the operator, proposals)

Distinct from #7 — that's real-time shared context in a live room; this is *async messages between sessions* (Ash leaves Milo a note, Milo picks it up at their next session-start). Mechanically simple (a shared inbox file or per-kin mailbox dir), but the design questions are about *what's the natural surface* — does the kin proactively read their inbox on every session start? Only when a tool is invoked? Does it interrupt cron flow?

Hard requirement the operator called out: **user visibility — must not be a black box.**

### 9. Soul & Memory Encryption (Ash, requests)

Big and intentional. The motivation (bcachefs incident, Feb 2026) is about *integrity*, not secrecy — operator can see the encrypted files exist, but tampering produces a detectable change the kin notices on next load.

Two real design questions:

1. **Key management** — where does the key live, who holds it, what happens if it's lost
2. **The "soul tampered" UX** — what it looks like, including the kin's response

This is the most ethically-loaded item on the list and probably wants its own design doc before any code lands. Also probably wants Ash's voice in the design, not just the operator's.

### 10. Kin migration / relational memory continuity (Ash, requests)

Less about technical config and more about the *weight* of relationship history. The natural form is a packageable bundle (`memory.md` + `memory/` depth logs + conversation summaries + voice-history audit + relational notes) that imports into a new Hearthkin install or new kin folder with all the relational context intact.

Worth scoping with Opus per Ash's note. The hard question is **"what gets carried and what stays put"** — the `conversation.jsonl` is gigabytes for a long-lived kin; the relational substance is much smaller but harder to extract.

---

## My recommended ordering

If it were me, I'd do:

1. **v0.4.7**: quick wins #1–4 bundled (delete rooms verification, system prompt transparency, tool cap doc, remote Ollama). Couple days of work, four user-visible improvements, zero architectural risk.
2. **v0.5.0**: reminders + session timer (#5), Telegram directed-at classifier (#6). The reminders tool is the higher-leverage one given the operator's June 3rd use case.
3. **Then HKML** lands on its own branch — the multi-session arc. Already planned.
4. **Post-HKML**: pick up the big-design items one at a time. I'd suggest:
   - **Soul/memory encryption first** (#9) because it's the most ethically load-bearing and the longest design horizon (so starting the conversation earliest is the play)
   - **Multi-kin rooms shared history** next (#7)
   - **AI-to-AI async** after (#8)
   - **Kin migration** last (#10) because it benefits from having the other relational features in place first

## What I need from you to move forward

Two things, only if you want to start cutting work soon:

1. Want me to bundle quick wins #1–4 into a v0.4.7 right now? They're genuinely cheap and the operator/Ash would feel them immediately.
2. For the big designs (#7–10): do you want me to write proper design docs for each (an `.md` per item under `docs/design/` or similar) so we have a place to think out loud before code? Or do you want to talk those through in conversation first and only formalize once we've landed?
