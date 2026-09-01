# Per-turn memory retrieval — closing the depth gap

**Status:** design, not built. Written 2026-06-22.

**One line:** before every send, automatically put the most relevant slice of a
kin's *own depth logs* in front of the model — no tool call required — so it has
real material to be present with, and so `num_ctx` can finally come down.

---

## The problem, plainly

A kin's memory lives in layers:

- `soul.md` — identity. **Always loaded.**
- `memory.md` — a brief **index** (pointers, not stories). **Always loaded.**
- `memory/<topic>.md` — the **depth logs**. Where the substance actually lives.
- `memory/journal/<date>.md` — daily journal entries from tending / cron.

The depth *exists* — a long-running kin accumulates dozens of topic logs. The problem is
that **almost none of it reaches the conversation.** The only way a depth log
gets in front of the model is if the kin *calls `memory_search`* — a tool call.
So the rich layer is gated behind the one behaviour weak / local models are
worst at: issuing a structured tool call instead of *narrating* one (the whole
"gesture" problem). In practice the depth sits on disk unread, and the live
conversation falls back on raw scrollback. **That is why `num_ctx` has to be
huge — it's doing memory's job by brute force.**

The cost of this shows up hardest in emotionally weighted conversation. A kin on a
small local model (observed on a 24B), reaching a moment that needed specifics and
having none in front of it, falls into an inescapable safe-soothing loop — fluent,
coherent, and empty. The model failure and the memory failure are the *same*
failure: nothing was in front of the model to be present *with*.

## The fix

**Before each send, Hearthkin retrieves a scored, budgeted slice of the kin's
depth and injects it as a context block — automatically, no tool call.** The
always-loaded index (`memory.md`) stays; this adds the *depth* on top, every
turn, regardless of whether the model would ever think to reach for it. Once the
relevant material is reliably present, continuity stops depending on a giant
raw-history window — which is the thing that finally lets `num_ctx` drop.

This is "borrow #1" from `companion-ai-memory-research-2026-06-01.md`: every good
companion app stores unbounded memory but injects only a tiny *scored* slice per
turn (Kindroid pulls 3 / 5 / 9 items by tier). Hearthkin already has the
warehouse and the forklift — it just only runs the forklift when the kin asks.

---

## The mechanism — calls I'm making

These are the systems calls. They don't need taste; they need to be correct.
Listed so they're legible, not so they're yours to litigate — flag anything that
smells wrong.

1. **What it searches:** the depth logs (`memory/<topic>.md`) and journals
   (`memory/journal/*.md`). **Not** `soul.md` or `memory.md` (already loaded
   every turn), **not** `conversation.jsonl` (that *is* the live context), **not**
   `staging/` (untended, not yet canonical).
2. **The query:** the last ~3 messages of the live conversation, concatenated,
   with the most recent user turn weighted heaviest. That's "what are we talking
   about *right now*."
3. **Scoring:** hybrid — lexical (BM25, the existing `memory_search` engine) +
   semantic (embedding cosine, the existing `embed_texts` / `nomic-embed-text`),
   each normalised and blended, then weighted by a gentle **recency** factor
   (journals by date, logs by file mtime) **and by salience** (see 3a). A
   **diversity cap** (≈2 chunks per source file) keeps one log from hogging the
   block. The operator can **pin/boost** a log (always favour) or **fence** one
   (never auto-surface) — both override the automatic score.
3a. **Salience — built in v1, not deferred, because it's the one piece with a
   retrofit tangle.** It's a *write-time data stamp*: ship without it and you're
   re-rating all of memory later. So when a journal or depth log is written
   (tending / distillation / a kin's own `write_file`), the memory model rates
   its significance 1–10 and stores it — frontmatter on new journals, a sidecar
   score-index for existing kin-authored logs we shouldn't rewrite. A **one-time
   backfill** rates the existing corpus on first enable, so scoring is uniform
   from day one. **Caveat:** LLM-rated salience is itself a model judging what
   matters, and it *can* miss what's actually load-bearing to *you* — which is
   exactly why the operator pin/boost lever sits alongside it.
4. **Chunking:** reuse `tools/memory_search.py:_chunk_text` — paragraph
   boundaries, line numbers tracked. Embeddings are sharper on a paragraph than
   on a whole file.
5. **Budget:** a token budget scaled to `num_ctx` (default ≈18%), with a hard cap
   of ≈6 chunks. *This is the one number that's secretly a taste call — see
   below.*
6. **Where it goes:** a single `role=system` block, placed **immediately before
   the latest user message**, framed as the kin's own kept notes (e.g.
   *"[hearthkin: relevant notes from your own memory, for this moment — not
   spoken by anyone]"*). Framing matters: it must **not** read as the user or
   another speaker — that's the documented impersonation trap. The framing text
   is an **editable prompt** (`load_app_prompt`) so an operator can reword it.
7. **It is ephemeral.** Generated fresh at send-time, **never written to
   `conversation.jsonl`.** It's derived, not authored — so it can't compound or
   poison history the way a saved cascade does. Modelled on
   `staging_status_line`'s inject-don't-persist pattern.
8. **Prompt-cache safe.** The stable prefix (soul + base prompt + `memory.md`)
   stays first and cacheable; the recall block lives in the *volatile tail* near
   the user turn, which changes every turn anyway. So this does **not** break the
   prompt-cache reuse the hysteresis-trimming work bought us.
9. **Cheap on the remote Mac.** Chunk embeddings are cached on disk keyed by
   `(file, content-hash)`; only *changed* logs get re-embedded. Each turn embeds
   only the short query → one tiny embedding call to the Mac's Ollama. (This is a
   lightweight slice of the "persistent vector index" the roadmap flagged for
   later.)
10. **Fails soft.** Any error — embed model down, no logs, host unreachable —
    skips the block and the send proceeds normally. It can never break a reply.
    (Mirrors how `semantic_memory` already degrades.)
11. **Per-kin toggle**, on by default for any kin that *has* depth logs (the
    whole point is that it requires nothing of the kin).

---

## Your calls (2026-06-22 review)

The four questions got restructured, and the restructure is right:

- **Recent vs. weighty → built in v1 now, as real salience.** It's the one piece
  with a retrofit tangle (write-time data stamp), so it goes in up front +
  backfilled rather than approximated-now-and-redone-later (mechanism 3a).
  Paired with an operator **pin/boost** lever so you can overrule the model's
  sense of what matters.
- **Visible vs. invisible → built in v1 now, visible.** If a paid product shows
  users which memories a reply drew on, the self-hosted user deserves it *more*,
  not less. Cheap now because retrieval already knows its own sources. **No new
  widget — reuse the two existing read-only, NVDA-reachable surfaces** (both are
  the multi-line read-only `wx.TextCtrl` pattern, the one wxMSW reads reliably;
  single-line read-only is the pattern that gets skipped, so we avoid it):
  - **Reviewable detail → the Usage dialog** (`_build_usage_tab`, Tools menu) —
    a "Memories used in the last reply" section listing each item's source log,
    snippet, and score, alongside the existing token breakdown. Persistent;
    NVDA arrows through it.
  - **In-the-moment cue → the Activity field** (`_set_status`, + optional
    `nvda_speak`) — a one-liner like *"3 memories surfaced"* after the reply.
    Transient by design, so it's a signal, not the record.
  - **Telegram → footer** (like the tool-receipt footer) — plain message text,
    so NVDA reads it natively.

  It doubles as the inspectability/integrity surface the research doc noted
  nobody has.
- **How present + off-limits → user settings, not baked defaults.** Both live in
  **Settings → Memory**, shipped with sane defaults so it works untouched:
  *how present* = a light / medium / rich choice (default **medium**, ≈18% of
  context, ~6 items); *off-limits* = a per-kin **fence list** (default empty);
  with the **pin/boost list** as its mirror image. Per the house rule that
  anything a user touches must be UI-reachable.

So nothing here is left as a feel-question to answer later — your three calls
turned all four into either built behaviour or an adjustable setting.

---

## What it builds on (already in the tree)

- `llm_backend.embed_texts(texts, model)` — the embedding call (honours the
  configured Ollama host, so it works against the Mac).
- `tools/memory_search.py:_chunk_text` + the inline BM25 — the lexical half and
  the paragraph chunker, both already written for the semantic-search feature.
- `kin_persistence.staging_status_line` — the working pattern for an ephemeral,
  inject-at-send, never-persisted system note.
- `kin_persistence.load_app_prompt(slug, kin_name)` — so the recall-block framing
  is operator-editable like every other harness prompt.
- The per-surface send assembly already threads `kin_name` and
  `max_context_tokens` (e.g. `build_system_prompt` → `chat(...)` in
  `hearthkin.pyw`), so wiring is small.

## Interactions and risks

- **Truncation:** the recall block counts against `max_context_tokens` and sits
  in the tail next to the user turn, so the existing truncation (which drops the
  *oldest* turns) leaves it intact. Must not be orphaned from the user turn.
- **Attribution / impersonation:** framed as a clearly-labelled system note, no
  `[Name]:` speaker shape — per the documented impersonation safeguards.
- **Cost:** one small query-embedding per turn against the Mac; log chunks cached
  so they aren't re-embedded. `nomic-embed-text` is tiny. Negligible.
- **Cache:** addressed by placement (tail) — see mechanism #8.

## Scope: v1 vs later (revised 2026-06-22)

The principle, applied precisely: **do the upgrade that has a retrofit tangle
now; defer the one that doesn't.**

- **v1 (now):** hybrid retrieval over logs + journals; recency **+ salience**
  scoring with a one-time backfill; diversity; budget; disk-cached chunk
  embeddings; ephemeral injection; per-kin toggle; **how-present + fence +
  pin/boost settings**; **visible "memories used" readout**.
- **Later — and genuinely fine to wait, because it has *no* tangle:** a true
  persistent **vector index** for recall *beyond* the BM25 candidate set. It's
  pure optimisation — it only bites on a very large corpus, it changes nothing
  about how memory is *stored*, and v1's disk-embed cache already covers the
  per-turn cost. Deferring it costs nothing later; deferring salience would
  have. Also later: time-balanced coverage like Kindroid's 5/12/2026 overhaul.

## Where it lands in the code

A new helper — say `memory_recall.py:retrieve_recall_block(kin_name,
recent_messages, budget_tokens)` returning the framed system block (or `None`).
Each conversational surface calls it while assembling messages and splices the
block in just before the latest user turn: desktop (`hearthkin.pyw` send path),
Telegram DM + group (`telegram_bot.py`), cron (`hearthkin_cron.py`), and rooms.
Putting it in a shared helper rather than inside `chat()` keeps `chat()`
file-system-agnostic while still reaching every surface — the same shape the
project already uses for per-surface concerns.
