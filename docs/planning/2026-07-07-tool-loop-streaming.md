# Tool-loop streaming — keystone DONE, surface wiring remaining (2026-07-07)

Goal: stream a tool-using kin's reply as it's generated — sentence-by-sentence
to NVDA on desktop, in-place-edit on Telegram — instead of dropping a finished
monolith after a slow tool loop. Especially valuable on the slow local Mac
models: turns a 90-second dead-air wait into watching the kin compose.

## ✅ DONE this session (landed, tested, zero regression)

The **keystone**, in `llm_backend.py`:

- `run_tool_loop(...)` gained an **opt-in `on_content=None`** param. When a
  caller passes it, each turn runs streaming and every content delta is
  forwarded to `on_content(text)` live; tool-call resolution is unchanged.
  When `None` (every existing caller — cron, rooms, Telegram-as-is), the loop
  runs the **byte-identical** non-streaming path. **No regression possible.**
- `_chat_collect_streaming(model, messages, *, on_content, **kwargs)` — runs a
  streaming `chat()` call, forwards content deltas to `on_content`, and returns
  a blocking-shaped `ChatResult` (content + thinking + tool_calls + usage) that
  is a drop-in for `chat(stream=False)`.
- `_accumulate_stream_tool_calls(acc, chunk_tcs)` — assembles tool calls from
  the stream: OpenRouter **deltas** (indexed, string-fragment arguments) AND
  Ollama **whole** tool_calls (one chunk). Read via `_tc_field` (dict/pydantic).
- Test: `tests/test_tool_loop_streaming.py` (13 checks, auto-run by run_all.py).

`on_content` fires on the **loop's thread** — the caller marshals to its UI.

## ✅ DONE — Desktop (tool kin → sentence-paint + NVDA), `hearthkin.pyw`

Landed 2026-07-07 (uncommitted). Finishes the morning's NVDA reply-speech work
for tool kin — they now stream sentence-by-sentence like tool-less kin instead
of only speaking the whole reply at the end.

- `_run_tool_loop_inline`: passes `on_content` (→ `wx.CallAfter(_on_stream_chunk)`)
  and `on_turn` (→ `wx.CallAfter(_reset_tool_stream_buf)`); sets
  `self._tool_stream_active = True`. Reuses the existing sentence-paint +
  `_maybe_speak_sentence` machinery.
- Double-paint solved via `_tool_stream_active`: `_on_tool_loop_done` no longer
  resets `_stream_buf`/`_paint_cursor` when streaming happened, so
  `_on_stream_done` paints only the unpainted tail.
- Preamble solved via `on_turn` → `_reset_tool_stream_buf`: the buffer clears at
  each turn boundary, so `_stream_buf` ends up holding ONLY the final talking
  turn's content (tool-turn preamble stays in `messages_added`, not duplicated
  into the saved reply). chat_display keeps everything painted; only the buffer
  resets.
- Thinking + `_pending_tool_history` handling unchanged. Empty-reply salvage
  unchanged (intermediate content still in `messages_added`).

## 🟡 PARTIAL — Telegram (tool kin → in-place edit), `telegram_bot.py`

**✅ ENGINE DONE (2026-07-07, uncommitted, unit-tested, touches no live path):**
module-level `_TelegramStreamEditor` — `feed(text)` (create-on-first-content +
throttled edit, default 1.2s, NEVER per-token), `reset_turn()` (per-turn buffer
clear, same discipline as desktop on_turn), `finalize(cleaned)` (edit the
streamed message with the cleaned reply; sends fresh if nothing streamed; splits
overflow past 4000 chars; returns True when it handled the send so the caller
skips its own). Injected send/edit/clock → testable without live Telegram.
`tests/test_telegram_stream_editor.py` (13 checks).

**⬜ REMAINING — wire it in (needs the operator's live Telegram-desktop testing):**
1. `_run_tool_loop_telegram` (~4448): build
   `ed = _TelegramStreamEditor(send=..., edit=...)` where `send(text)` =
   `telegram_api_call(token, "sendMessage", {chat_id, text})` → returns
   `result["message_id"]`, and `edit(mid, text)` =
   `telegram_api_call(token, "editMessageText", {chat_id, message_id, text})`.
   Pass `on_content=ed.feed, on_turn=ed.reset_turn` to `run_tool_loop`. Return
   `ed` alongside `(content, added, thinking)`.
2. **DM caller** `_handle_normal_message` and **group caller**
   `_handle_group_message`: after the existing cleanup pipeline produces the
   final `content`, replace the final-reply `_send_chunked(chat_id, content)`
   with `if not ed.finalize(content): _send_chunked(chat_id, content)` — i.e.
   the editor owns the reply message when it streamed, else fall back to a
   normal send. **This is the tangled part** — each handler's final send is
   woven through salvage / persistence / mirror-to-user; find the ONE
   final-reply send in each and swap only that. Persistence still saves the
   raw `content` unchanged.
3. Verify (the operator, live): message fills in as the kin composes; no "edited"
   footer (confirmed clean on her setup); no doubled message; no flood-wait
   (bump `throttle_secs` if 429s appear); tool-call display (🔧/→) still
   separate; the mirror-to-user path still fires once.

### Design notes (settled)

Design settled with the operator: **in-place edit** (OpenClaw-style, one message that
fills in). She's on Telegram **desktop**; edits don't notify her and a bot
editing its OWN message shows no "edited" footer — so no downside, no
multi-bubble needed.

- `_run_tool_loop_telegram` (~4448) → `run_tool_loop(...)`: pass an
  `on_content` that appends to a per-call buffer and, on a **throttle**, calls
  Telegram `editMessageText` on a single placeholder message.
- **Sequence:** on first content delta, `sendMessage` a placeholder (e.g. "…")
  and keep its `message_id`; subsequent deltas accumulate; a throttle
  (~1 edit / 1–1.5s, NOT per token — Telegram rate-limits edits and we've been
  flood-banned before) rewrites that message with the accumulated text; on loop
  completion, one final `editMessageText` with the finished reply.
- **GOTCHAs:** (a) Telegram message length cap ~4096 chars — if the reply grows
  past it, finalize the current message and start a new one (rare; matches the
  existing `_send_chunked` split). (b) The final cleaned reply still goes
  through the anti-impersonation / footer pipeline — apply it to the FINAL edit,
  not to every throttled interim. (c) Keep the existing append-only tool-call
  display (`🔧 name` / `→ result`) as separate messages; only the kin's spoken
  reply streams into the editable message. (d) Reuse `TelegramAPIError` handling
  — if an edit 429s, back off / skip interim edits, never abort the reply.
- `editMessageText` params: `{chat_id, message_id, text}`. Confirm no "edited"
  marker appears on bot self-edits during build (the operator's OpenClaw experience
  says clean; verify).

## Open design points (small)

- Throttle interval + whether to also flush on sentence boundaries (nicer for a
  screen reader than mid-word edits). Lean: flush on sentence boundary OR every
  ~1.2s, whichever first.
- Forwarding tool-CALLING turns' preamble content: currently forwarded (usually
  empty). If a model narrates before a tool call, it streams then the tool runs
  then more streams — acceptable/natural. Revisit only if noisy in practice.

## Why keystone-only was the right stopping point this session

`run_tool_loop` is shared by desktop, Telegram, rooms, and 3 cron paths — the
most-load-bearing module in the app. The opt-in keystone changes nothing for any
current caller and is unit-tested. The surface wiring touches live UI paths
(the desktop double-paint interaction; the Telegram throttle/rate-limit) that
deserve real device testing (NVDA; the Telegram desktop client) rather than a
rushed low-context landing. Do each surface as its own testable slice.
