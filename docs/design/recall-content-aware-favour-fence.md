# Content-aware recall favour / fence

**Status:** designed, not built (2026-06-26).
**Where it lives today:** `memory_recall.py` (`_matches_any`, `_build_recall_block_inner`), surfaced in `dialogs/recall_settings.py` (the "Always favour" / "Never auto-surface" fields).

## The problem

Per-turn recall's "Always favour" and "Never auto-surface" both match against the
**file path/name** of each depth log (`_matches_any` does a lowercase substring
test on the relative path; favour multiplies a matching log's score by
`_BOOST_MULT` = 1.5, fence excludes it). That assumes the operator knows their
kin's filenames.

For the operators this app is built for, that assumption is backwards. The whole
memory architecture is "the kin owns its files; the operator never touches them."
So an operator who wants to favour or fence a *topic* has no idea which log to
name — `harbour` only works if a log is literally pathed `harbour`, which the operator
has no reason to know.

## Guiding principle: breadth is a feature — show the blast radius, don't block it

The first instinct was to keep the fence path-based because a broad content term
("anxiety") would suppress a lot. That instinct is wrong, and naming why is the
load-bearing design decision:

**A broad term is often exactly what someone needs — especially on the fence.**
"Never auto-surface *anything* touching this theme" is a real, valuable thing: keep
a whole subject out of the kin spontaneously raising it every time the conversation
drifts near, *without erasing it* (the kin can still go there when it deliberately
runs `memory_search` — the current fence philosophy, preserved). Operators will
have their own reasons — a grief, a health matter, an ended relationship, a topic
that simply belongs to a different part of the day. The breadth is the entire
point, and the operator knows their own intent better than a guardrail does.

So the design does **not** add a limit. It adds **visibility**: when you type a
favour/fence term, show "this currently catches N logs" so breadth is a *seen*
choice, not a surprise. Power stays with the operator; they just get the blast
radius before they commit. (Same instinct as the rest of the app — the exec
denylist trusts intent over pattern-blocking; all config is UI-reachable; etc.)

This makes the **fence the more valuable half** of the feature — it's about
comfort and control, not just relevance tuning.

## Two independent knobs

**1. What a term matches against:**
- *Today:* the file path/name (substring).
- *Content (keyword):* the log's **text** — `harbour` matches any log mentioning harbour,
  wherever it lives. Solves "I don't know the filename" with a small change.
- *Content (meaning / semantic):* pulls topic-*adjacent* content even without the
  literal word. Needs embeddings (the `semantic_memory` path), fuzzier to tune,
  biggest build.

**2. How "favour" influences recall** — the behaviorally load-bearing fork:
- *(a) Boost-when-relevant:* multiply a topic's score so it ranks higher **when the
  conversation is already near it.** Catch, and it's a real one: recall relevance is
  driven by the current conversation, so an off-topic chunk scores ~0 from BM25, and
  `0 × 1.5 ≈ 0`. Boost **sharpens, it does not summon.**
- *(b) Pin-always:* reserve a small slice of the recall budget to pull the topic
  **every turn, regardless of what's being discussed.** This is the "never let this
  fall out of mind" behavior — the one the phrase "always lean toward it" actually
  points at.

## The refined feature (recommended shape)

- **Favour → content-keyword + pin-always.** Match log text, and *reserve* a slice
  of the per-turn budget for the pinned topic so it's present even when unmentioned.
  (Boost-when-relevant alone doesn't deliver "lean toward it" — see the `0 × 1.5`
  trap above.)
- **Fence → content-keyword, broad on purpose.** The emotional-safety half. Fenced
  content stays reachable via deliberate `memory_search`, exactly as today.
- **Visibility → "catches N logs" preview** next to each field, so breadth is chosen,
  not stumbled into.

Build tiers, smallest → largest:
1. **Small:** content-keyword matching for both fields + the "catches N" preview.
   (Favour stays a multiplier — honest but weak; document the `0 × 1.5` limit.)
2. **Medium (recommended):** the above, plus pin-always for favour (a reserved
   sub-budget). This is the version that matches the operator's intent.
3. **Large:** semantic matching (pin/fence by meaning), layered on the
   `semantic_memory` embedding path.

## Implementation sketch

In `memory_recall._build_recall_block_inner`, chunks already carry
`(path, lineno, text)`. The change is *what `_matches_any` is given*:
- **Fence (content):** skip a chunk when its **text** matches a fence term (not just
  its path). One-line change at the fence check; keep the path check too so existing
  path-style terms still work.
- **Favour (content + pin):**
  - *Boost half:* multiply when the chunk **text** matches a favour term.
  - *Pin half:* before the relevance-ranked selection, run a small dedicated
    retrieval for each pinned term against the corpus and reserve up to
    `PIN_BUDGET_FRAC` of `budget_tokens` for the best matches, so pinned content is
    present regardless of the query. The remaining budget runs the existing
    relevance loop. Dedupe so a pinned chunk isn't double-counted.
- **Visibility helper:** a pure function `count_matching_logs(kin_name, term, *, by_content=True)`
  that the dialog calls on field edit to render "catches N logs." Reuses the corpus
  gather + chunk + match path; no new index.

## Config

Reuse `recall_boost` / `recall_fence` (the term lists). Decisions to settle at build:
- A per-term or global **match mode** (path / content / semantic), or just move to
  content with path-substring as a natural subset (a content match of "harbour" also
  catches a file named harbour.md, so content is a strict superset — likely no mode
  switch needed, just broader behavior).
- **Migration note:** existing path-style terms will match *more* under content
  mode (broader). Surface this once via the "catches N" preview so the change is
  visible, not silent.
- Pin needs a budget fraction constant (`PIN_BUDGET_FRAC`, e.g. 0.33 of the recall
  budget) and a cap so many pins can't starve relevance recall.

## Open questions

- Pin budget split: fixed fraction, or per-pin? How many pins before they crowd out
  query-driven recall? (Cap + the "catches N" visibility mitigate.)
- Should fence be content-only, or content-OR-path? (Content-OR-path is safest for
  back-compat; costs nothing.)
- Semantic tier: worth it, or does content-keyword cover the real need? Defer until
  the keyword version is in use and the gap (if any) is felt.
