# HKML — reassessment (why it shouldn't be "coming next")

**Status:** Reassessment of a prior proposal. Recommends *not* building HKML as designed.
**Supersedes:** the "Coming next: HKML" framing in `ROADMAP.md`.
**Date:** 2026-06-29.

---

## The short version (no code knowledge needed)

HKML was proposed as a friendlier format for *tool calls* — the messages a kin
emits when it wants to read a file, search the web, run a command, etc. Today
those are written in JSON (a format full of braces and quotes). The proposal
was to replace JSON with an XML-ish format like `<read-file path="..."/>`, on
three claimed benefits: it's **easier for screen readers**, it's
**model-agnostic** (works on any model, even ones without built-in tool
support), and it **unlocks cheaper/smaller models**.

Having looked at it against how Hearthkin actually works, **the proposal
doesn't hold up, and we should not build it as a project.** The three benefits
are either false or already handled:

1. **The screen-reader benefit is the headline reason, and it's wrong.** HKML
   isn't actually quieter to read than JSON (it just swaps one set of symbols
   for another), and more importantly *you never read the raw format anyway* —
   Hearthkin already shows tool calls as a clean line like
   `[tool: read_file memory/brook.md]`. Accessibility is handled by the display,
   not by changing what the model writes underneath.

2. **The "works on any model" benefit already exists in the code.** Hearthkin
   already reads tool calls written as plain text by models that lack built-in
   tool support (MiMo, Qwen, Llama variants). HKML would mostly re-invent a
   thing we already have, under a new name.

3. **The real problem is somewhere else entirely.** What actually makes tools
   hard on small/local models isn't the *format* of the call — it's whether the
   model reliably *decides to make the call at all* (instead of narrating
   "*reads the file*" in prose), and whether the harness is *forgiving* when the
   model gets the details slightly wrong. Changing JSON to XML helps neither of
   those.

So HKML is effort aimed at the one layer that isn't the bottleneck. The small
slice of it that *is* real — reliably pulling tool calls out of plain text — is
work already underway under a different name, and it should stay incremental
rather than become a big branded protocol project.

The rest of this doc is the detailed reasoning, for anyone who wants to verify
it.

---

## What HKML was proposed as

From the `ROADMAP.md` "Coming next: HKML" section, the proposal's claims:

- **Readable.** `<read-file path="memory/brook.md"/>` instead of a JSON blob,
  on the theory that NVDA reads the XML-ish form as "readable English-ish text"
  rather than punctuation soup.
- **Model-agnostic.** Any model that can write text can write HKML; the harness
  translates HKML into whatever the underlying model's API expects, or executes
  it directly when the model has no tool-call API at all.
- **Cheaper.** Unlocks tool use on smaller / cheaper / non-tool-calling models.

And it was scoped as "its own branch, a multi-session effort," after which
"every kin effectively becomes tool-having."

## Why it doesn't survive contact with the constraints

### 1. The accessibility premise is false, two ways

**It isn't less punctuation-noisy.** Compare what a screen reader has to chew
through:

- JSON: `{"name":"read_file","arguments":{"path":"memory/brook.md"}}` — braces,
  quotes, colons, commas.
- HKML: `<read-file path="memory/brook.md"/>` — angle brackets, equals sign,
  slash, quotes.

HKML trades one set of symbols a screen reader stumbles on for another, and adds
the self-closing `<… />` shape on top. At NVDA's default "some" punctuation
level neither reads as English; at "all," both are a symbol slog. There is no
setting at which HKML reads cleanly and JSON doesn't.

**And it's the wrong layer regardless.** A screen reader never reads the wire
format. It reads what the *harness renders for display*, and Hearthkin already
collapses a tool call to a clean human line — `[tool: read_file
memory/brook.md]` — via `_on_tool_call_display` (desktop) and the Telegram
`on_tool_call` path. The serialization the model emits underneath is invisible
to the operator. Changing it to improve the reading experience is fixing the
wrong end of the pipe; the JSON was never reaching anyone's ears.

### 2. HKML is a serialization — it doesn't reduce harness work, it adds it

A tool call written as `<read-file/>` is exactly as inert as one written as
`{"name":"read_file"}`. Both are just text the model produced. Either way the
harness must: parse the call out of the model's output, validate the tool name,
dispatch to the executor, run the Python, and format the result back. HKML
removes none of that. It *adds* a translation step (HKML → each provider's
actual expected shape, or → direct execution) and a parser and a format to
version and maintain — a third tool-call lane alongside the structured-API path
and the content-extraction path the codebase already has.

(The "you're building a full API for your local models" reaction the proposal
drew is accurate — but that's the *cost* of the approach, not a feature of it.)

### 3. The "any model" mechanism already exists in the code

This is the decisive point. Hearthkin already extracts tool calls written as
plain text by models with no structured tool-call channel. See
`llm_backend._extract_content_tool_calls` and `_CONTENT_TOOL_CALL_PATTERNS`,
which already recognize:

- XML-nested (MiMo, some Llama):
  `<tool_call><function=name><parameter=key>value</parameter></function></tool_call>`
- Qwen / DeepSeek JSON payload: `<tool_call>{"name":"x","arguments":{...}}</tool_call>`
- Llama-3.1 function tag: `<function=name>{"arg":"val"}</function>`

Those *are* "an XML-ish tool format the harness translates for models that lack
a structured API." HKML's core mechanism is therefore not a thing to build — it
is a thing that ships today. A dedicated HKML project would largely be choosing
a Hearthkin-branded tag vocabulary and writing a parser for it, to do a job an
existing parser already does.

### 4. The real bottleneck is substrate + forgiveness, and HKML touches neither

The lived evidence (getting the `tff` game working as a tool took repeated
effort and a lot of changes to the game itself, not to the call format) points
at where the difficulty actually lives:

- **Substrate — does the model reliably *decide* to call a tool?** The worst
  failure mode is "gesturing": the model writes `*reads the next 100 lines*` in
  prose instead of issuing a call. This is **format-invariant** — a model that
  won't emit the JSON is the identical model that won't emit the HKML. You
  cannot reformat your way out of "the model didn't act." Fixing this is a
  *model* problem (better-suited or fine-tuned local models), not a wire-format
  problem.
- **Forgiveness — does the harness absorb the model's near-misses?** Malformed
  arguments, slightly-wrong tool names, whitespace, etc. This is the
  edit_file-self-heal / arg-coercion / content-extraction-recovery track the
  project is *already* on, and it pays off regardless of call format.

JSON-vs-XML moves neither needle.

## Verdict

HKML as proposed is a solution to a misdiagnosed problem. The headline benefit
(accessibility) is false and aimed at the wrong layer; the "any model" mechanism
already exists in the codebase; and the genuine bottlenecks (the model choosing
to act, and the harness forgiving mistakes) sit one layer below where a wire
format operates.

**Recommendation: do not build HKML as a standalone branded protocol/branch.**

## What to do instead (the 10% that's real)

The legitimate need underneath the proposal is: *tool use should work on
text-only / weaker local models.* The right way to serve it is incremental
hardening of what already exists, not a new format:

1. **Strengthen `_extract_content_tool_calls`.** Add patterns as new
   text-only models surface in real traffic; keep validating tool names
   (already done) so hallucinated calls fail safe.
2. **Keep widening harness forgiveness.** Arg coercion, name fuzzy-matching,
   malformed-call recovery — the same track as the edit_file self-healing.
   Every gain here helps *every* model and every call format.
3. **Treat the substrate as the real lever.** A local model that defaults to a
   kin's voice and reliably emits tool calls (the fine-tune track) does more for
   "every kin becomes tool-having" than any serialization change could. If the
   goal is cheap/local tool-having kin, that is where the effort belongs.

None of these need a new format, a new branch, or a multi-session protocol
effort. They're additive improvements to paths that already run in production.

## Suggested ROADMAP change

Replace the "Coming next: HKML — tool calls every model can do" section with a
short pointer to this doc, and fold the genuine need into the existing
incremental work (content-extraction hardening + the fine-tune track). The
NVDA-readability concern it raised is already addressed by the display layer and
needs no format change.
