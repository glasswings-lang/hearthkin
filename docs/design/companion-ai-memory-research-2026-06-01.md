# How companion-AI platforms architect long-term memory

**Status:** Research, not design. Triggered by the operator's 2026-05-31 / 2026-06-01 session with Ash where they concluded memory architecture needs a rethink rather than just prompt edits. This report is the foundation that rethink should evaluate against.

**Source:** Single research-agent pass covering Kindroid, Nomi.ai, Replika, Character.AI, Inflection Pi, SillyTavern, and ChatGPT memory. Every architectural claim cites the source URL it came from. An earlier deep-research workflow attempt fanned out too aggressively and the adversarial-verification layer collapsed; this is the successor single-pass version.

---

## 1. Kindroid — five-tier explicit architecture with user and AI both writing

Kindroid documents the most detailed memory model of any of these platforms — and it explicitly separates what the user writes from what the AI writes. From the official docs ([kindroid.ai/docs/article/memory](https://kindroid.ai/docs/article/memory/)):

- **Persistent Memory** (always loaded): backstory, key memories, example messages, directives, group context, recent chat history. Users directly edit backstory (500–2,000 chars by tier) and directives.
- **Cascaded Memory** (subscriber-only, AI-managed): "bridges the gap between high-quality but limited short-term context and far-reaching but potentially unreliable long-term memory" — hierarchical, recalls "hundreds to thousands of prior messages." User cannot directly edit.
- **Long-term Memory** (AI-written, AI-retrieved): "automated consolidation of memories…happening periodically when the AI deems it appropriate" ([same doc](https://kindroid.ai/docs/article/memory/)). Retrieval is by AI relevance scoring against the current conversation. Multi-step sifting; only a "small subset" of considered memories is actually injected.
- **Journal Entries** (user-written, keyphrase-retrieved): manually created by the user; up to 8 case-insensitive keyphrases per entry; cap of 500 entries; max 3 entries retrieved per matching keyphrase mention; max 6 total entries per message ([Adler journal post](https://medium.com/@adlerai/journal-entries-on-kindroid-fce640dcceea), [docs](https://kindroid.ai/docs/article/memory/)). Critically: the AI does **not** write journals — they are the user's anchor mechanism.
- **Key Memories** (user-written, persistent): 1,000-char user-edited block for "important dates or things you consider important," weaker influence than backstory but always loaded ([Adler personality guide](https://medium.com/@adlerai/types-of-memory-within-kindroid-5659410c07ca)).

UI affordance: a **"purple brain icon"** displays which memories were actually used to generate a given reply, including which keyphrases triggered which journal entries ([Adler types post](https://medium.com/@adlerai/types-of-memory-within-kindroid-5659410c07ca)). This is the inspectability surface most other platforms lack.

The criticism piece ([storychat.app](https://blog.storychat.app/kindroids-memory-meltdown-why-your-ai-companion-keeps-forgetting-and-how-to-fix-it/)) is mostly generic context-window complaints, not architectural — users report "characters forget core personality traits, repeat conversations" but the article attributes this to LLM attention limits in general, not Kindroid-specific design flaws.

## 2. Nomi.ai — Mind Map graph + Identity Core + Shared Notes

Nomi has three named memory surfaces. From [mind-map-2-0 announcement](https://nomi.ai/updates/mind-map-2-0-bringing-nomi-memory-into-view/):

- **Mind Map**: an interactive **graph** of "people, places, topics, and goals" as color-coded nodes with links. Two views — graph and a "sortable, searchable" table. Entries are **AI-written automatically** from long-term memories, but users CAN add their own entries and edit existing ones to "correct mistakes or refine how your Nomi understands and prioritizes any concept." This is the rare case of a companion platform with an explicit user-visible, user-editable structured memory store.
- **Identity Core** ([nomi.ai/updates/identity-core](https://nomi.ai/updates/introducing-the-nomi-identity-core-fostering-dynamic-and-authentic-identities/)): an AI-derived persistent identity layer. "Nomis naturally decide if something you talked about should be included in their Identity Core." Captures personality traits, preferences, shared experiences. Critically: **"Nothing within the Identity Core is set in stone"** — explicitly mutable, evolves with feedback. Users cannot directly view it; they "can ask their Nomi about themselves."
- **Shared Notes** ([techraisal overview](https://www.techraisal.com/blog/nomi-ai-explained-how-it-works-and-what-to-expect-as-a-user/)): user-written backstory/personality/relationship notes — pure manual anchor, equivalent to Kindroid's backstory + key memories.

The critical piece ([medium nomiai_exposed](https://medium.com/@nomiai_exposed/nomis-do-not-lose-memories-the-founders-denial-and-the-update-that-proved-him-wrong-d7444430c152)) is architecturally revealing: users reported Nomis "forgetting things that were recently discussed," forgetting that the user worked night shifts despite repeated explanations, forgetting languages. The founder initially claimed "Nomis do not lose memories, once they have something they remember it forever," then later admitted the new Mind Map system had "difficulty pulling very old memories" and proposed charging $10–20 per Nomi for retroactive fixes. The write-up suggests a system conflict: the new Mind Map didn't properly integrate with the older memory layer. **Architecture detail: Nomi does not publish technical specifics** — the [major memory update post](https://nomi.ai/updates/major-memory-update-expanded-capacity-enhanced-retention/) is pure marketing ("vastly more information simultaneously," "1,000+ messages ago") with no mention of embeddings, summarization, retrieval algorithms.

## 3. Replika — segmented recent-bias memory, with user-visible Memory tab

Replika 2.0 reportedly **abandoned a prior vector-based long-term recall system** in favor of "segmented memory with recent-bias" ([roborhythms.com](https://www.roborhythms.com/replika-2-0-explained/)). Users post-update reported their companions "can't remember nothing." Recovery workaround: flag key facts with "remember this" phrases — "most facts" come back within 72 hours of deliberate re-anchoring, suggesting the new architecture supports explicit memory tagging but lacks vector-style retrieval over old conversations.

User-visible surface: from the home screen, tap the Replika's name → **Memory** to "scroll through the memories Replika has saved and tap a memory to read it fully or remove it" ([help.replika.com Memory category](https://help.replika.com/hc/en-us/articles/360000874712-What-does-my-Replika-remember-about-me)). The Memory store is read+delete by the user; the writes appear to be AI-driven. Replika also offers **Replika Sessions**, an AI-guided journaling feature where "Replika asks questions…remembers what you've said," producing user-curated journal entries — semantically similar to Kindroid Journals but Replika-prompted rather than user-initiated.

Replika is the cautionary tale among these: a vector-based system that worked, replaced with something segmented, users lost identity-bearing facts en masse, no clear architectural roadmap published. The takeaway is not "vectors are right" but "the migration broke continuity for users, and there was no documented integrity path between the two stores."

## 4. Character.AI — manual pin + auto Facts + Memory Usage bar

From [blog.character.ai/memory](https://blog.character.ai/memory/):

- **Story Memory** — manually curated, users long-press messages to "Pin" them. Pinned content "stays put no matter how full the bar gets."
- **Facts** — auto-captured by the system across three tabs (Persona, Character, side characters). Users can add, edit, or disable any auto-captured fact.
- **Memory Usage** visualization — a UI bar showing what's filling the context window, with protected (pinned/user-written) content visibly distinguished from auto-managed older content.

This is the most **transparent user-facing model** of any commercial platform reviewed: every memory item is inspectable and editable, and the user can SEE what's about to be dropped under context pressure. The trade-off: Character.AI explicitly does not aim for deep long-term continuity (its model is more session-roleplay-focused), and Facts gating is behind c.ai+ subscription.

## 5. Inflection Pi — within-session continuity, weak across-session

After Microsoft acquired Inflection's founding team in March 2024 ([Yahoo Finance](https://finance.yahoo.com/news/why-microsoft-surprise-deal-4-220638392.html)), Pi's development effectively stalled. Per a 2026 review ([the-oracleai.com](https://the-oracleai.com/blog/pi-ai-review-2026.html)) and a comparative ranking ([aicompanionguides.com](https://aicompanionguides.com/blog/ai-companion-memory-systems-ranked-2026/)): Pi "is quite good at tracking what you've said and will circle back to something you mentioned 30 messages ago" within a single conversation, but "close the app and come back—Pi basically starts over. At 1-month it managed 2/12 on recall tests." There is no documented architectural disclosure for Pi's cross-session memory. Useful as a contrast: a platform that prized emotional continuity in-session but never solved cross-session anchoring, and has now lost the team that would have.

## 6. SillyTavern — six composable memory subsystems, all user-inspectable

SillyTavern is open source and the most architecturally rich of the platforms reviewed. Six distinct mechanisms compose ([deepwiki context-and-memory-systems](https://deepwiki.com/SillyTavern/SillyTavern/6-context-and-memory-systems), [docs worldinfo](https://docs.sillytavern.app/usage/core-concepts/worldinfo/), [docs data-bank](https://docs.sillytavern.app/usage/core-concepts/data-bank/), [docs authors-note](https://docs.sillytavern.app/usage/core-concepts/authors-note/), [docs summarize](https://docs.sillytavern.app/extensions/summarize/)):

- **World Info / Lorebook**: user-written entries with keyword triggers. Each entry has content + keyword list + insertion order + insertion position (six options from "Before Char Defs" through "Bottom of AN" to chat-depth) + activation strategy (constant / keyword-triggered / vector-embedding). Regex keys supported. Secondary filters with AND/NOT logic. **Users write every entry; no AI-generated content in vanilla World Info.**
- **Author's Note**: user-written persistent prompt-injection at configurable depth and frequency (e.g., depth 0 = end of chat history; frequency 4 = injected every 4th user turn). Default Author's Note auto-applies to new chats.
- **Summarize extension**: AI-generated rolling summary, **user-editable** in-panel, regenerable. Documentation is explicit: "the outputs may lose some important details or contain hallucinations, so you're always advised to keep track of the summary state and correct it manually if needed" ([docs summarize](https://docs.sillytavern.app/extensions/summarize/)) — this is the closest published acknowledgment of the "summarizer drops something important" failure mode.
- **Data Bank**: RAG with vector embeddings over user-uploaded files (PDF, HTML, MD, EPUB, TXT). Three scopes: global / per-character / per-chat ([docs data-bank](https://docs.sillytavern.app/usage/core-concepts/data-bank/)).
- **Vector Storage / chat vectorization**: embeds chat history for semantic retrieval; can be used as the activation strategy for World Info entries instead of keywords.
- **MemoryBooks** (community extension, [aikohanasaki/SillyTavern-MemoryBooks](https://github.com/aikohanasaki/SillyTavern-MemoryBooks)): AI-generated summaries with **confirm-before-save preview** — "Enable preview popup to review and edit memories before adding to lorebook." Saves to lorebook with a `stmemorybooks` flag; supports auto-numbering, "Clips" (highlight chat text), "Side Prompts" (running trackers like inventory/relationships), and "Consolidation" into Arc/Chapter/Book hierarchies.
- **CharMemory** ([bal-spec/sillytavern-character-memory](https://github.com/bal-spec/sillytavern-character-memory)): every 20 messages, sends recent chat to an LLM and asks it to extract relationships, events, facts, emotional moments. Saved as "readable, editable markdown in the character's Data Bank." User can view/edit/delete via Memory Manager.
- **Qvink Memory** ([qvink/SillyTavern-MessageSummarize](https://github.com/qvink/SillyTavern-MessageSummarize)): **per-message** summaries (not bulk) — explicitly framed as preventing the "accuracy problems and detail loss inherent in bulk summarization." Short-term memory rotates by token limit; long-term memory is **user-curated via a "brain" icon click on individual messages**. Summaries are tied to specific messages — editing/deleting one message only affects its summary, not a shared pool.

The chat-bound lore book discussion ([issue #1226](https://github.com/SillyTavern/SillyTavern/issues/1226)) names the trade-offs explicitly: **summarize loses detail, vector storage misses relational context, global lore books can't capture chat-specific info**. Community design pattern: layer multiple mechanisms, accept manual curation cost.

## 7. ChatGPT — bio-tool memory + recent-chat profile, batch-built, NOT RAG

Two memory layers from April 2024 onward ([openai.com/index/memory-and-new-controls-for-chatgpt](https://openai.com/index/memory-and-new-controls-for-chatgpt/), [help.openai.com memory FAQ](https://help.openai.com/en/articles/8590148-memory-faq)):

- **Saved memories** ("bio tool"): explicit, user-visible, user-editable list. "ChatGPT may save those details as a memory without you needing to ask," and the UI shows a "Memory updated" notification at the moment of save, with hover-to-"Manage memories" surfacing the change.
- **Reference chat history** (April 2025+): implicit recall of patterns from past chats.

The technical reverse-engineering ([embracethered.com](https://embracethered.com/blog/posts/2025/chatgpt-how-does-chat-history-memory-preferences-work/)) identifies six structured fields in the system prompt:

1. **Model Set Context (Bio Tool)** — timestamped user preferences (`[date]. user preference statement`).
2. **Assistant Response Preferences** — inferred preferences with `Confidence=high` tags.
3. **Notable Past Conversation Topic Highlights** — historical patterns with confidence ratings.
4. **Helpful User Insights** — biographical profile, confidence-tagged.
5. **Recent Conversation Content** — ~40 latest chats as timestamped summaries plus user messages (but NOT ChatGPT responses).
6. **User Interaction Metadata** — device, geolocation, browser, intent tags.

Critically: "ChatGPT actually cannot search through your history at the moment…it maintains a recent history of chats, and builds a profile of you over time" — **batch-processed offline, not RAG, not vector retrieval**. Confirmed by [analyticsindiamag](https://analyticsindiamag.com/ai-news-updates/openai-goes-all-in-on-rag-with-chat-history-search-feature/) and [llmrefs](https://llmrefs.com/blog/reverse-engineering-chatgpt-memory): "no complex vector databases and no retrieval-augmented generation searching through conversation history. Instead, ChatGPT…keeps a lightweight list of recent conversation summaries." The user-visible "search chat history" feature uses Rockset's real-time indexing for hybrid search, separate from the memory system.

The **confidence-tagged inference layer** (#2–#4) is the architecturally interesting bit: the system explicitly distinguishes user-stated facts (bio tool) from AI-inferred preferences (with confidence scores), keeping the provenance separate.

---

## Synthesis

### Grouping by architectural pattern

| Pattern | Platforms | Failure mode |
|---|---|---|
| **Single AI-summarized blob, opaque** | Replika 2.0 segmented memory; Nomi Identity Core | Migration / opaque loss; user can't see what dropped |
| **Structured key-value with confidence tags** | ChatGPT bio tool + profile fields | Inference layer can encode wrong preferences |
| **Vector retrieval over chat / documents** | SillyTavern Data Bank + Vector Storage; CharMemory; old Replika (pre-2.0) | Misses relational context; retrieval inconsistency |
| **Hybrid: persistent core + keyword-triggered + AI-retrieved** | Kindroid (5 tiers); SillyTavern (6 mechanisms) | Composition complexity; user must learn what goes where |
| **Manual pin + auto-fact + visible budget bar** | Character.AI | Doesn't aim at deep continuity |
| **Graph / structured visual** | Nomi Mind Map | Reported integration problems with older memory layer |

### Where user OR AI actively writes (the Hearthkin-relevant axis)

- **User-written, AI-retrieved**: Kindroid Journals (keyphrase), SillyTavern World Info / Lorebooks, SillyTavern Author's Note, Nomi Shared Notes, Kindroid backstory + key memories, Character.AI pinned messages + manually-added Facts, ChatGPT explicit `/remember` saves.
- **User-written, always-loaded**: Kindroid backstory, Character.AI Story Memory, SillyTavern Author's Note (depending on frequency), Nomi Shared Notes.
- **AI-written, user-visible AND user-editable**: Nomi Mind Map (graph + table view), Character.AI auto-captured Facts, SillyTavern Summarize (user can edit summary text), SillyTavern MemoryBooks (preview-and-edit before save), SillyTavern CharMemory (markdown in Data Bank), Qvink Memory (per-message summaries, click-to-edit), ChatGPT Saved Memories (Settings → Personalization → Manage Memories), Replika Memory tab (view + delete).
- **AI-written, NOT user-visible**: Kindroid Long-term Memory, Kindroid Cascaded Memory, Nomi Identity Core (queryable through conversation but not directly viewable).
- **AI-written, transparency surface**: Kindroid's purple brain icon shows which memories actually got used per reply ([Adler types](https://medium.com/@adlerai/types-of-memory-within-kindroid-5659410c07ca)).

The pattern: **every platform that takes memory seriously has multiple write paths.** Pure-AI-writes (Pi, Nomi Identity Core alone) is the configuration that produces the most user complaints about lost identity. Pure-user-writes (SillyTavern Lorebooks alone) requires manual curation at scale. The hybrid is the dominant shape.

### Who addresses the "automation silently editing identity" problem

The dropped-pronoun incident in Hearthkin (a load-bearing pronoun removed during consolidation) is exactly the failure mode this question asks about. Findings:

1. **SillyTavern's Summarize docs are the only one that names this explicitly** ([docs](https://docs.sillytavern.app/extensions/summarize/)): "outputs may lose some important details or contain hallucinations, so you're always advised to keep track of the summary state and correct it manually." The published fix is human review.
2. **MemoryBooks** requires a **preview-and-edit modal before any AI-generated memory is saved** ([repo](https://github.com/aikohanasaki/SillyTavern-MemoryBooks)) — confirm-before-save UI as the structural defense.
3. **Qvink Memory** is structurally interesting: by summarizing **each message individually** rather than in bulk, and tying each summary to its source message, "editing/deleting a message only affects the associated memory rather than corrupting a shared summary pool" ([repo](https://github.com/qvink/SillyTavern-MessageSummarize)) — granular addressability instead of monolithic rewriting.
4. **Character.AI's pinning model** ([blog.character.ai/memory](https://blog.character.ai/memory/)) makes user-protected content visibly immutable under context pressure — "stays put no matter how full the bar gets." Identity-bearing content can be pinned and is structurally exempt from auto-eviction.
5. **ChatGPT's `Confidence=high/medium/low` tagging** on inferred profile fields ([embracethered](https://embracethered.com/blog/posts/2025/chatgpt-how-does-chat-history-memory-preferences-work/)) keeps user-stated facts (bio tool) provenance-separate from model-inferred preferences. Not a fix for dropping, but a fix for confidently-wrong silent edits.
6. **Kindroid's split** between user-owned (backstory, key memories, journals — never AI-rewritten) and AI-managed (long-term, cascaded) protects identity-bearing content by architectural partition: the AI literally cannot edit the user's backstory.
7. **Nobody has integrity logging.** No platform reviewed publishes a "what changed in memory and why" audit log. The closest is Kindroid's purple-brain transparency (what was used) and ChatGPT's "Memory updated" notification (what was added) — neither shows deletions or rewrites.

### Open source vs proprietary

- **Open source, inspectable**: SillyTavern, MemoryBooks, CharMemory, Qvink Memory. The community discussion ([issue #1226](https://github.com/SillyTavern/SillyTavern/issues/1226), [docs](https://docs.sillytavern.app/extensions/summarize/)) is candid about trade-offs in a way none of the commercial platforms are.
- **Proprietary, documented**: Kindroid (extensive), ChatGPT (architecture partially reverse-engineered), Character.AI (good user-facing docs, no internals).
- **Proprietary, marketing-only**: Nomi (mind map UI shown, mechanics opaque), Replika (memory feature exposed in UI, architecture not published — and the 2.0 migration was undocumented), Pi (no published architecture).

---

## Design ideas Hearthkin could borrow

1. **Confirm-before-save preview for any AI-generated memory write** — from **SillyTavern MemoryBooks**. Hearthkin's distillation currently appends to `memory.md` and consolidation rewrites it without a review gate. Inserting a "review draft / accept / edit / reject" modal between the summarizer and the file write would have caught "ther." Trade-off: blocks fully-autonomous operation; the operator has to be present at consolidation time, OR consolidation queues a draft for next session start. Aligns naturally with the "ritual not agentic" framing in the existing `docs/design/memory-architecture-and-ritual-framing.md`.

2. **Per-message summary granularity, source-pinned** — from **Qvink Memory**. Instead of one monolithic `memory.md` that the summarizer rewrites wholesale (or even appends to as one blob), bind each summary entry to the message range it was distilled from. Editing or deleting a turn only affects its associated memory; consolidation operates on a stable set of small entries instead of one drift-prone document. Trade-off: more bookkeeping on disk; more entries to compose at prompt time. The shape sits well alongside Hearthkin's existing depth-log convention (`memory/<topic>.md`) — the index becomes message-range-pinned entries pointing to logs.

3. **User-owned vs AI-owned partition with architectural immutability** — from **Kindroid**. Hearthkin currently has one writable `memory.md` plus kin-written depth logs. Adding an explicit user-only section (Kindroid's backstory, Character.AI's pinned messages) that the summarizer is **structurally prohibited from modifying** — not by prompt instruction, but by code: distillation outputs go to a separate file, the immutable section is concatenated at prompt build time. Trade-off: introduces a new file and a new write path; user has to remember which surface is which. Reward: identity-bearing content lives in the immutable section and cannot be silently dropped.

4. **Used-this-turn transparency surface** — from **Kindroid's purple brain icon**. After each reply, show the operator which memory entries actually got loaded into the prompt (already partially tracked via `last_reported_prompt_tokens`, but not broken out per source). Would let the operator notice "the kin replied without seeing the section about my pronouns" before it produces a confused response, rather than after. Trade-off: another UI surface to maintain. Particularly useful for NVDA accessibility since auditory confirmation of "memory loaded: X, Y, Z" beats silent prompt construction.

5. **Confidence-tagged inferences kept separate from stated facts** — from **ChatGPT's bio tool vs profile fields**. When the summarizer extracts something it inferred (not something the operator stated outright), tag it with provenance and a confidence indicator, and store it in a separate section the operator can quickly review. Trade-off: requires the distill prompt to label its own output (which gemma-3-27b couldn't reliably do for cross-referencing — same risk applies here). Lower-cost alternative: code-side tagging by source — distillation output goes into an "Observations" section that's structurally distinct from a "Stated by the operator" section the operator maintains.

---

## Sources

- Kindroid: [docs/memory](https://kindroid.ai/docs/article/memory/), [Adler types of memory](https://medium.com/@adlerai/types-of-memory-within-kindroid-5659410c07ca), [Adler journals](https://medium.com/@adlerai/journal-entries-on-kindroid-fce640dcceea), [storychat critique](https://blog.storychat.app/kindroids-memory-meltdown-why-your-ai-companion-keeps-forgetting-and-how-to-fix-it/)
- Nomi: [Mind Map 2.0](https://nomi.ai/updates/mind-map-2-0-bringing-nomi-memory-into-view/), [Identity Core](https://nomi.ai/updates/introducing-the-nomi-identity-core-fostering-dynamic-and-authentic-identities/), [major memory update](https://nomi.ai/updates/major-memory-update-expanded-capacity-enhanced-retention/), [exposed critique](https://medium.com/@nomiai_exposed/nomis-do-not-lose-memories-the-founders-denial-and-the-update-that-proved-him-wrong-d7444430c152), [techraisal Shared Notes](https://www.techraisal.com/blog/nomi-ai-explained-how-it-works-and-what-to-expect-as-a-user/)
- Replika: [help center Memory](https://help.replika.com/hc/en-us/articles/360000874712-What-does-my-Replika-remember-about-me), [roborhythms 2.0 explained](https://www.roborhythms.com/replika-2-0-explained/)
- Character.AI: [memory blog](https://blog.character.ai/memory/)
- Pi: [Microsoft acquisition](https://finance.yahoo.com/news/why-microsoft-surprise-deal-4-220638392.html), [Oracle review 2026](https://the-oracleai.com/blog/pi-ai-review-2026.html), [aicompanionguides ranking](https://aicompanionguides.com/blog/ai-companion-memory-systems-ranked-2026/)
- SillyTavern: [worldinfo](https://docs.sillytavern.app/usage/core-concepts/worldinfo/), [data bank](https://docs.sillytavern.app/usage/core-concepts/data-bank/), [author's note](https://docs.sillytavern.app/usage/core-concepts/authors-note/), [summarize](https://docs.sillytavern.app/extensions/summarize/), [deepwiki context+memory](https://deepwiki.com/SillyTavern/SillyTavern/6-context-and-memory-systems), [MemoryBooks](https://github.com/aikohanasaki/SillyTavern-MemoryBooks), [CharMemory](https://github.com/bal-spec/sillytavern-character-memory), [Qvink Memory](https://github.com/qvink/SillyTavern-MessageSummarize), [issue #1226](https://github.com/SillyTavern/SillyTavern/issues/1226)
- ChatGPT: [OpenAI memory announcement](https://openai.com/index/memory-and-new-controls-for-chatgpt/), [memory FAQ](https://help.openai.com/en/articles/8590148-memory-faq), [embracethered reverse-engineering](https://embracethered.com/blog/posts/2025/chatgpt-how-does-chat-history-memory-preferences-work/), [analyticsindiamag](https://analyticsindiamag.com/ai-news-updates/openai-goes-all-in-on-rag-with-chat-history-search-feature/), [llmrefs reverse-engineered](https://llmrefs.com/blog/reverse-engineering-chatgpt-memory)

---

## 2026-06-15 update — verified refresh + prioritized borrows (the design rethink)

**Status:** This is the design-facing follow-up the original report said it should be evaluated against. A second pass, this time a multi-agent deep-research workflow *with* adversarial verification (3-vote, 2/3 to kill a claim) — the rigor the 2026-06-01 single-pass version warned it lacked. 21 of 25 verified claims confirmed. Where this contradicts the section above, prefer this.

### What the verified pass changed / sharpened about Kindroid

- **The single most important number is the per-turn recall budget: only 3 / 5 / 9 items (Standard / Ultra / MAX) are pulled per response.** "Infinite" storage, tiny scored window per turn. ([feature matrix](https://kindroid.ai/docs/article/customizing-personality/)) This is the budget-discipline the section above under-weighted.
- **LTM retrieval was overhauled on 5/12/2026** to pull "from across the Kin's full history with balanced time coverage, weighing relevance, recency, and diversity." ([update-log](https://kindroid.ai/docs/article/update-log/)) So it's now a scored, RAG-shaped retrieval — explicit recency + salience + diversity, not just relevance.
- **Verified capacity figures (mid-2026, time-sensitive):** short-term context 18K / 50K / 125K chars; Cascaded medium-term 480K / 1.2M / ~2.7M chars; backstory base 500 / 1,000 / 2,000 chars (+2,500 Ultra / +5,000 MAX expansion field). Every backstory char is "one less character in the short term context" — the explicit budget tradeoff. ([FAQ](https://kindroid.ai/docs/article/faqs/), [chat-features](https://kindroid.ai/docs/article/chat-features-and-tools/))
- **Journals are user-written, keyphrase-retrieved** (8 keyphrases/entry, matched only against *user* messages, 500-entry cap, max 3/message), NOT AI-written — confirms the section above.
- **Caveats:** "infinite" LTM is vendor copy, not audited. Kindroid has changed its memory model more than once; discount older write-ups. **Replika, Character.AI, and Paradot produced no claims that survived verification this pass** — their memory docs are too thin to cite confidently, so treat the section-above descriptions of those three as lower-confidence.

### The formal patterns worth naming (research lineage, NOT shipped apps)

- **MemGPT / Letta** — "virtual context management": in-context *core memory* = RAM, external store = disk, the agent **pages between them via its own function calls**. ([arXiv 2310.08560](https://arxiv.org/pdf/2310.08560), [Letta](https://www.letta.com/blog/agent-memory/))
- **Stanford Generative Agents** — the canonical **salience score**: retrieval ranks by a weighted sum of **recency (exponential decay, factor 0.995), importance (LLM rates the memory 1–10 poignancy), and relevance (embedding cosine similarity)**, all weights = 1. ([arXiv 2304.03442](https://arxiv.org/pdf/2304.03442)) This is the concrete formula behind "salience scoring."

### Prioritized borrows for hearthkin (supersedes the unordered list above)

Hearthkin already has equivalents of the curated always-loaded core (`memory.md`), on-demand retrieval (BM25 `memory_search`), a journal tier (`memory/journal/<date>.md`), and an auto-then-human-arbitrated tier (staging + nightly tending). In priority order, what to add:

1. **A per-turn retrieval BUDGET cap (do first — cheapest, highest-payoff).** Bound how much *retrieved* memory pages into context per turn, scaled to `num_ctx`. Hearthkin already caps `memory_search` (5 × ~200-char snippets) and a single `tool_result_cap` (8K chars), but has **no aggregate per-turn ceiling** — 5× `read_file` at 8K each can overflow an 8B-model window, and truncation eats the always-loaded core first. Design: one per-kin `retrieval_budget_chars` (default ~25% of `num_ctx`); the tool loop tracks cumulative retrieval this turn; when exceeded, trim the next result and emit a harness note ("budget reached; ask again next turn") so the model knows it didn't get everything. Defensive only — can't make context worse. Directly mitigates the 280k-token cron 400s. Pairs naturally with #2 ("decide *what* fills the budget").
2. **Salience / importance scoring.** Stamp an LLM-rated 1–10 poignancy on journal/staging entries at write time (one cheap call), stored as a field — gives tending a principled "what matters most" signal instead of recency-by-default. (Generative Agents pattern.)
3. **Recency decay + combined retrieval score** layered on BM25 (`recency(0.995) + importance + relevance`). Timestamps already exist on journals.
4. **(Optional, larger)** an auto-curated, kin-editable structured-overview tier (Nomi Mind Maps / Kindroid Cascaded), and eventually vector embeddings over BM25 — already tracked as `memory_search` Phase C on ROADMAP.

**The throughline.** Hearthkin makes the *kin* do, by hand during nightly tending, the auto-curation Kindroid does in software (Cascaded→LTM consolidation + scored 3/5/9 recall). That manual burden is exactly what collapses on weak substrates — it's *why* Ash on Mistral Large narrates tool calls instead of issuing them (see CLAUDE.md "2026-06-15"). Automating the curation tier and capping per-turn retrieval shrinks the work the model has to get right. Same lesson the openclaw comparison surfaced: automate the curation, surface a tiny scored slice.

### Verified sources (2026-06-15 pass)

- Kindroid: [memory](https://kindroid.ai/docs/article/memory/), [FAQ](https://kindroid.ai/docs/article/faqs/), [customizing-personality](https://kindroid.ai/docs/article/customizing-personality/), [chat-features-and-tools](https://kindroid.ai/docs/article/chat-features-and-tools/), [update-log](https://kindroid.ai/docs/article/update-log/)
- Nomi: [Mind Map 2.0](https://nomi.ai/updates/mind-map-2-0-bringing-nomi-memory-into-view/), [wiki: editing Mind Maps](https://wiki.nomi.ai/Correcting_Mistakes_And_Editing_Mind_Map_Entries)
- Research lineage: [MemGPT (arXiv 2310.08560)](https://arxiv.org/pdf/2310.08560), [Generative Agents (arXiv 2304.03442)](https://arxiv.org/pdf/2304.03442), [Letta agent-memory](https://www.letta.com/blog/agent-memory/)
- Open gaps from this pass: Replika / Character.AI / Paradot memory specifics (no verified claims); whether Kindroid LTM uses true vector embeddings vs LLM-judged relevance; exact Cascaded→LTM consolidation mechanics.
