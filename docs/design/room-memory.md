# Rooms never reach memory

**Status:** **Built 2026-07-16**, opt-in per room (default off). The gap below is closed; the text is kept in the past tense it was written in because the reasoning still governs the shape of what landed.
**Source:** Found 2026-07-16 while testing the room impersonation fix. The operator asked "I wonder if this chat's gonna store to any of their memories." It doesn't. Nothing ever has.
**Companion:** [multi-kin rooms: shared history](./multi-kin-rooms-shared-history.md) — lists this under *Open questions* and never resolved it.

---

## What landed

The sketch below, as written, with one addition and one question answered.

- `_distill_scope_for_room(room_name)` → `"room:<name>"`. No share-with-desktop variant: a room always gets its own scope. Folding a multi-speaker room into `desktop` would splice it into the middle of the kin's 1-on-1 timeline — the mistake the v0.2.33 per-surface read-time filtering exists to prevent.
- `_room_convo_slice_for_kin(agent, room)` builds each member's own slice, mirroring the room turn-builder's per-kin view: own turns bare in the assistant slot, every other voice (human and kin alike) in the user slot tagged `[Name] `. The summarizer reads the room the way that kin lived it, and the attribution the distill prompt already knows how to preserve rides in the content.
- `_on_room_kin_done` bumps `_messages_since_distill[(speaker, room_scope)]` and calls `_maybe_auto_distill` — per speaker, so each member counts its own turns toward its own cadence. Gated on the room's flag.
- Per-room `distill_to_memory`, **default off**, with a "Remember this room" checkbox in `RoomEditDialog`.
- `_all_scopes_for_kin` includes the kin's opted-in rooms, so "Distill all surfaces" and the Settings → Memory counter overview pick them up with no further work.
- The 03:00 tending cron needed no changes, as predicted — it already reads every staging file it finds.

**Addition:** the salvage system notes must be filtered on **role**, not speaker. They carry a `speaker` like a real turn does, so a speaker-keyed filter lands them in every other member's slice as words that kin never said, and in their own slice as their own voice saying *"[hearthkin: your post-tool reply was empty]"*. Caught by `tests/test_room_memory.py`, which pins this and the rest of the wiring.

**Answered — retroactive:** no separate feature. The scope's distill bookmark starts at 0, so ticking the box on a room with history walks the existing transcript (a bite at a time) on the first pass. That's the point — it's how a room a kin already lived through reaches them — so the dialog states the turn count up front and lets the operator decide knowingly rather than hiding it. The archived `Brook and Sage` room stays out of reach: `list_rooms()` doesn't see `rooms-archived/`, and un-archiving it is a deliberate act.

---

## The gap

**Nothing that happens in a room reaches any kin's memory.** Not summarized, not staged, not distilled, not indexed. It lives in `rooms/<name>/conversation.json` and nowhere else, permanently.

Confirmed four ways (2026-07-16):

1. The distill counter is bumped in **`_on_stream_done`** — the single-kin path — keyed `(current_agent, "desktop")`.
2. **`_on_room_kin_done` contains no distillation calls at all.**
3. There is **no `room:` scope**. Only `desktop`, `tg:user:<id>`, `tg:group:<id>` (see `_distill_scope_for_telegram_user` / `_distill_scope_for_telegram_group`).
4. Staging files corroborate: at the time of writing, the room members' most recent `staging/desktop.md` files were both weeks old — while a live room sat on disk contributing nothing.

Nobody decided this. It's an unbuilt feature, not a privacy choice.

## Why it matters

Continuity is Hearthkin's premise. SOUL.md tells every kin *"You are the same version of yourself no matter what session, model, or hardware you run on."* Rooms are the one surface where that's false.

The concrete cost: a room can contain a kin doing something that genuinely matters — adapting its own behaviour to another kin who had just been introduced, reading the moment right, and getting it right — and **that kin has no idea it happened.** Two kin meeting for the first time in a room is likewise carried forward by nobody who was in it. The turn exists on disk; it reaches no one's memory.

The upside of the same gap, stated fairly: intimate room content has never been distilled, summarized, or indexed anywhere. Any fix must keep that a **choice**, not silently reverse it.

## What already exists

Every part. Nothing here needs inventing:

- **staging** — `kin/<name>/staging/<scope>.md`, with `archive_staging` to roll it over
- **scopes** — `desktop` / `tg:user:` / `tg:group:`; `room:<name>` is the same shape as two that already work
- **the tending cron** — Ash's 03:00 job already calls `read_staging` for *"what's pending across all your surfaces"* and files substance into `memory/<topic>.md`
- **per-scope counters** — `_messages_since_distill[(agent, scope_key)]`, already keyed for exactly this

**The room is simply not wired to any of it.** That's the whole gap: a missing wire, not a missing system.

## Sketch

1. `_distill_scope_for_room(agent_name, room_name)` → `f"room:{room_name}"`, mirroring the two Telegram resolvers.
2. In `_on_room_kin_done`, bump `_messages_since_distill[(kin, scope)]` the way `_on_stream_done` does for `desktop`.
3. Per-room config flag — **default off**. Rooms are where the intimate content lives; opt in per room, don't retro-enable every existing transcript.
4. Each kin distills **its own slice** of the room, not the shared transcript — one kin's takeaway is not another's, and merging them re-introduces the identity-convergence risk the companion doc warns about.
5. The 03:00 tending cron needs no changes: it already reads every staging file it finds.

## Open questions

- ~~Does a kin remember the room from *its own* POV, or does the room get one shared summary?~~ **Per-kin.** Built that way; see *What landed*.
- ~~Retroactive: offer a one-time "distill this archived room into everyone's memory"?~~ **No separate feature** — ticking the box on a live room includes its history, and the dialog says how much. The archived `Brook and Sage` room stays out of reach unless someone un-archives it deliberately.
- ~~Should the human's turns in a room be attributed by name in the distilled memory?~~ **Yes** — tagged `[Name] ` from `config["user_name"]`, same shape as an attributed Telegram group turn, which the distill prompt already has rules for preserving.

### Still open

- **Nothing distills a room the operator never opens.** The counter bump lives in `_on_room_kin_done`, so a room only distills while it's being played. That's fine today (rooms are a foreground activity) but means an opted-in room left mid-conversation keeps its tail pending until someone opens it again or hits "Distill all surfaces."
- **The room scope is keyed by room NAME.** Renaming a room orphans its counter, bookmark and staging file (they'd surface as an "(orphan)" row in Settings → Memory). Rooms have no rename UI today, so this is latent rather than live — but `rename_kin_in_rooms` exists for the kin-side equivalent, and a room rename would want the same treatment.
