# Per-turn recall — review follow-ups (for the next agent)

Review of `f5fbb83` (group/cron/rooms wiring + visibility readout) by the agent
that built the engine + desktop/DM wiring. **Verdict: the wiring is correct,
fail-soft, compiles, suite green — these are polish items, not blockers.** Two
small fixes + one optional gap. Each has the exact location so you can act
without reloading the whole context.

---

## 1. ~~Fix a misleading comment in the rooms wiring~~ DONE (reworded; logic unchanged)

**Where:** `hearthkin.pyw`, the two room inject sites in
`_run_room_streaming_inline` (~line 7715) and `_run_room_tool_loop_inline`
(~line 7741). Also the `f5fbb83` commit-message framing.

**Problem:** the comment says recall is "framed as a system note so it never
reads as another speaker." The code does **not** do that — it calls
`inject_into_messages`, which **inlines** the block onto the latest user turn,
exactly like every other surface.

**Reality:** inline is actually correct/safe in rooms — the recall block is a
clearly bracketed `[hearthkin: …]` note, not a `[Name]:` speaker turn, so it
cannot trip the impersonation safeguards. So **the code is right; only the
comment is wrong.** Just reword the comment to say "inlined on the latest user
turn; the bracketed framing keeps it clear of the impersonation safeguards."
Don't change the logic.

## 2. Telegram `_recalled:` footer reads shared bot state — display-only race

**Where:** `telegram_bot.py` — `self._last_recall_used` is set in
`_handle_normal_message` (DM) and `_handle_group_message` (group), and read in
`_build_recall_footer` (~line 714) at the two send sites (~3052, ~3754).

**Problem:** `self._last_recall_used` is one shared slot on the bot instance.
If a DM and a group reply finalize concurrently (separate worker threads), the
footer can name the *other* reply's logs. **The injected memory each kin
receives is always correct — only the footer label can mismatch.** Cosmetic.

**Fix:** thread the `used` list returned by `inject_into_messages` through to the
send site as a **local variable** instead of via `self._last_recall_used`, and
pass it to `_build_recall_footer(full_cfg, used)`. Removes the shared mutable
state. (This pattern is inherited from the engine author's desktop/DM commits —
not new to f5fbb83.)

## 3. (Optional, minor) rooms have no visibility readout

The two room inject sites discard the `used` list (`messages, _ = inject…`), so a
room kin's recall is invisible. Rooms are lightly used; low priority. If wanted,
capture it and surface a cue.

---

## The real next build (not a fix — the remaining feature scope)

Per `per-turn-memory-retrieval.md`, still unbuilt:

- **Salience compute + one-time backfill** — currently dormant; recall ranks on
  relevance + recency only until this lands. This is the operator's "do it now,
  avoid the retrofit tangle" call. Write-time 1–10 rating stored in a sidecar
  (`memory/.salience.json`, the engine already reads it) + a one-time backfill
  over existing logs. Pair with the operator **pin/boost** lever.
- **Settings → Memory UI** for the per-kin knobs (`recall_enabled`,
  `recall_budget_pct` as a light/medium/rich choice, `recall_fence`,
  `recall_boost`) — config-only right now.

Coordinate on the branch before editing shared files.
