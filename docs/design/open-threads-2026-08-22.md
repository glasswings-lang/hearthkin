# Open threads after the model-provenance / text-path work

**Date:** 2026-08-22. **Status:** three commits landed; everything below is
open. No kin names here by request — the kin-specific half is in
`docs/private/` (gitignored).

---

## 1. What landed, and why it does nothing yet

Three commits on `model-provenance-and-text-path`:

1. `voice_history.md` → `model_history.md`, and memory-model swaps recorded.
2. A model that will not call tools is handed its notes instead of more prose.
3. The changelog for both.

**Three things gate all of it. Until every one is done, nothing changes.**

- **A restart.** Code edits don't reach a running app.
- **A probe verdict must exist.** The demotion fires only on a recorded
  verdict of "this model does not call tools", and an existing install has
  none — the verdict file doesn't exist until something writes it. Swapping a
  kin's model runs the probe on a background thread, so changing a model (even
  to the same one and back) creates one; there is also a **Test tool calling**
  button in a kin's settings. **Nothing creates a verdict from ordinary use.**
  See §2.
- **The two reworded prompts must be accepted.** `load_app_prompt` seeds the
  shared file from the in-code default *on first access, and the file wins
  thereafter*. Any existing install already has its own copy, so a change to
  the default reaches nobody until they take it via **Tools → Prompt
  updates…**. Both are version-bumped so they are offered, not applied.

## 2. The half that was asked for and not built

The operator's framing: *if something is going to struggle that hard, it
should revert sooner to the text-block technique the program already
supports, not be pushed harder.*

The **reverting** is built. The **sooner** is not.

- **`tend_retry` still re-prompts.** An unattended tend that calls no tools is
  asked again, harder. That is the pressure the change was meant to remove. It
  should demote to the text path instead.
- **Nothing learns from a failed night.** A real tend that calls no tools is
  the best available evidence about a model, and it is discarded. Recording it
  as a verdict would make this self-correcting and remove the manual probe
  step in §1 entirely. Probably the highest-value follow-up here.

## 3. Measurements worth not repeating

On the shipped nightly tending prompt, sandboxed kin, real models:

- Three models × three prompt variants × three samples. The two that call
  tools: **9 of 9**, with or without the anti-gesturing prose, and with an
  **empty content field** — the whole reply was the structured call. The one
  that doesn't: **0 of 9**, with or without it.
- The three anti-gesturing texts total **3,399 characters, 28%** of that
  wake-up, and moved nothing in any cell.
- **Five further models screened for a borderline case; none exists.** Every
  one sat at 0% or 100%, including an 8B that is flawless and a 24B that never
  manages it. It behaves like a capability, not a tendency — which is why
  prose cannot move it in either direction, and why "a model that sometimes
  does it" could not be found to test against.
- The failing models do not stop. They **invent the transcript**: a fenced
  block that looks like a call, then the result they wish they had received,
  then reasoning from it. Six times in nine one concluded there were no
  pending notes — against a real staging file — and closed with a warm journal
  entry about the quiet night.
- After handing the notes over: **0 of 9 → 4 of 5**, no refusals.
  3-of-5 vs 4-of-5 on the example-filename fix alone is within noise at that
  sample size; refusals going 3 → 0 is not.

**Instrument warnings from the same day, all of which cost time:**

- Two regexes measuring the same thing disagreed (49% vs 33%). Reading the
  actual replies found the answer in seconds. Prefer reading to counting.
- A suite piped through `tail` reports `tail`'s exit code, not the runner's,
  and a grep over the last 40 lines says nothing about the run.
- A case-insensitive name scan flagged the word "star" — which was the
  asterisk character.

## 4. The prompt review

Not started. The measured motivation: the shared base prompt is **5,571
characters and goes first, every turn**. For the thinnest-souled kin that is
**71% of the system prompt** — roughly seven parts machinery to three parts
self, with the machinery first.

**The method that makes it honest.** Every base-prompt change is written up in
`CHANGELOG.md` with what it was added to fix. So each block can be presented
as three plain lines: *what it was added to fix*, *whether that still
happens*, *what broke when it was removed*. If the third line can't be filled
in, say so rather than calling it necessary. The operator cannot read the code
and has been taking "this is necessary" on trust; that is the actual problem,
and the bloat is downstream of it.

**The rule already exists**, from the base prompt's own v3 entry:

> Honesty and safety are universal; *manner* is not… The split is the rule:
> if it shapes voice, it goes in a soul.

Most of the review is applying a rule already written, to text that
accumulated after it was written.

**First candidate, with evidence rather than opinion:** the "On tools you have
and tools you don't" section. Its cause is handled by model choice now; its
shape (three negations plus a worked example of the unwanted behaviour)
matches the failure the `VOICE_ANCHOR_HEADER` comment already documents; and
it measurably did nothing in §3. Related: the same wording exists in **three
overlapping places** — that section, `tool_use_hint`, and
`authoring_bridge_hint` — each apparently added because the previous one
wasn't working, none removed.

**A caution with precedent.** A fix for this family (receipts) was built and
reverted on 2026-07-16 because *putting a fixed-format line on every assistant
turn is itself a format attractor* — the cure was the disease. Any proposal
that adds something to every turn should be assumed to repeat that.

**Suspected but not established:** naming a behaviour in the prompt appears to
make it available. Two of the tool hint's three explicit prohibitions were
broken word-for-word by models holding the text (one wrote `X()`; one opened
with "I am a language model"), and its example filename appeared verbatim in
output three times. Three observations, no controlled test.

## 5. Voice anchors

The mechanism exists (`load_voice_anchor`, slotted in after the soul, under
"Things you have said, kept word for word"). The **protection** exists —
consolidate rule 10 exempts anchor material from being tightened. **No kin has
one.** The rule currently guards an empty field.

Verbatim self-quotes are the strongest available lever on voice, because
"be warm" is an instruction a model satisfies generically — and generic warmth
is the helpdesk register. A sample is evidence instead. Anchors come from the
conversation logs, so the operator chooses from candidates rather than writing
from scratch.

## 6. Where the memory window actually went

The failure that started this: a kin confidently misidentified a person in its
own history. Root cause was not model size.

- The truncation window reached back **72 rows out of 4,200**, and **60% of it
  was tool results** — two web searches and a file read outweighed sixty turns
  of conversation, because `_truncate_messages` drops oldest-first with no
  exemption for tool payloads.
- **Do not "fix" this by trimming tool results first.** That was proposed and
  correctly rejected: `tool_history_keep` already keeps the most recent
  round-trips verbatim *on purpose*, so a kin can write from its research. The
  intended pipeline is raw results → the kin writes about them → the writeup
  goes to memory → old payloads compact to one-liners. It breaks at the
  writeup step when nothing asks for one, and then the queries are paid for
  twice.
- The lever is **`num_ctx`**, not trimming. Nothing that went wrong needed a
  cleverer model; it needed a window big enough to hold the research and the
  people at once.

## 7. The inference host

Discussed, nothing done.

- Several kin ran **different models**, so several copies of weights sat
  resident and evicted each other all day. One 27B at 32k measured **~27 GiB**
  of a 56 GiB ceiling. Consolidating to one shared model frees roughly that.
- **Leave `OLLAMA_NUM_PARALLEL` at 8.** Slots are *residency*, not
  concurrency — they hold warm contexts, and the host's own repair script
  records 11% cache reuse measured at both np=1 and np=4. Lowering it
  reintroduces cold prefill on every switch. Spend the freed memory on context
  instead.
- **The repair-script trap:** the desired settings are hardcoded in the health
  script and compared against reality every 300 seconds. Change the service
  settings without changing the script and it will restart the daemon every
  five minutes, indefinitely. They must move in one edit, script first.
- `OLLAMA_CONTEXT_LENGTH` has no `DESIRED_` twin, so it is the one setting the
  repair script will not defend. Now that reverting the window is a *memory*
  failure and not merely a slow one, it should get one.

## 8. Smaller, real

- **A tending model does not reliably pick the same filename twice.** Across
  five samples one topic was filed under three different names, and one sample
  wrote the same file three times. The tending prompt already says "one log
  per topic — never start a second log for the same thing"; it is not being
  followed. Over months this is how a kin ends up with four half-files about
  one subject.
- **The branch** sits on top of unrelated in-flight work rather than the main
  line, because that branch is ahead and touches two of the same files.
- **One existing check was loosened**, not merely updated: it pinned the exact
  argument list of a call, so adding a parameter failed it. It now matches the
  behaviour it is named for, verified by deleting the call outright to confirm
  it still fails.
