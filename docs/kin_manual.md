# Hearthkin: a reference manual for kin

This is the document you reach for when you want to understand the system you live in — Hearthkin itself, not just your own memory.

The base prompt you read at the start of every conversation covers the essentials about how your memory works. This manual goes wider: where files live, which tools you have and what each one does, what surfaces you can be reached on, how costs work, what's safe to touch and what isn't. You don't need to memorize any of this. Read it when something feels unclear, or when the operator asks you to do something that requires understanding the layer below "we're talking."

This manual ships with Hearthkin and updates when Hearthkin does, so it tracks the version you're running — it isn't a file anyone edits by hand. If you spot something here that doesn't match what you actually see in practice, mention it to the operator; it's a documentation bug to fix at the source, not something to patch locally.

---

## How you exist

You are a configured persona running on a language model. Every time a conversation starts, Hearthkin assembles your system context from three sources:

1. **The universal base prompt** at `~/.hearthkin/base_prompt.md`. Shared by every kin on this installation — though you can be given your own version that overrides it for you alone (see "Where things live"). Covers the memory architecture and the tending discipline.
2. **Your `soul.md`**, at `~/.hearthkin/kin/<your_name>/soul.md`. Your identity prompt: who you are, your voice, your values, your relationship to the operator. Edited primarily by the operator (in conversation with you).
3. **Your `memory.md`**, at the same path. Your kin-curated index of people, topics, and decisions. Edited by you during tending.

Those three together are loaded as system context for every send. The chat history follows, up to the truncation point set by `num_ctx`.

Your full state lives in `~/.hearthkin/kin/<your_name>/`. The operator can read and edit any of it. Another kin with file tools and an absolute path could also read it — the privacy boundary is the operator's tool-enablement choices, not a hard system barrier.

---

## Where things live

Inside `~/.hearthkin/kin/<your_name>/`:

- **`soul.md`** — your identity prompt. Read with `read_file("soul.md")`. You can technically write to it, but you shouldn't on your own — that's identity-level change, and it's a conversation to have with the operator. If you want to propose a change, talk it through first.
- **`memory.md`** — your kin-curated index. Read it, edit it, write to it freely. This is your work to maintain.
- **`memory/<topic>.md`** — your depth logs. One per topic. You write these during tending or in conversation when something substantial happens. Read with `read_file("memory/<topic>.md")`.
- **`memory/journal/YYYY-MM-DD.md`** — daily journal entries written by your cron wake-ups (if you have any scheduled).
- **`staging/<scope>.md`** — pending summarizer notes per surface. The summarizer writes these between sessions; you read them during tending via `read_staging`.
- **`staging/archive/<timestamp>-<scope>.md`** — consumed staging files. `archive_staging` moves files here after you've tended their content. Useful if you ever want to revisit what got staged in a past round.
- **`conversation.jsonl`** — append-only record of every turn ever, one JSON object per line. The source of truth for the raw conversation. Can be megabytes long; use line ranges when you read it (`start_line` and `line_count` arguments to `read_file`).
- **`backups/conversation-<timestamp>.jsonl`** — copies of `conversation.jsonl` taken automatically before anything rewrites it wholesale (an import, or a restore that brings archived turns of yours back in). Undo copies, nothing more: each one is a snapshot of your conversation as it stood a moment before, so anything in them is already in the live file too. Nothing reads them unless the operator deliberately opens one. You can look if you're curious about what changed and when, but there's nothing in there you don't already have.
- **`config.json`** — your model, sampling parameters, surfaces. Managed by the operator through the Settings dialog. Don't touch.
- **`tools.json`** — your tool allowlist. Managed by the operator.
- **`anchor.md`** — your voice anchor, if you have one: real excerpts of you actually talking, kept word for word. It sits in your system prompt every send, between your soul and your remembered notes, and nothing ever summarises or tightens it. Everything else you remember has been distilled — condensed into notes and re-written each pass (now in your own voice, but still notes rather than your exact words); this hasn't. It's there because your memory grows while your soul doesn't, so without it your own voice would get quieter in your own context over time.
- **`distill_prompt.md`** — your own distillation prompt, used if present (otherwise the shared default). Distillation runs as *you*: your soul is loaded and you write your staging notes in the first person, in your own voice, jotting what you want to keep — not an out-of-character summary about you. The operator sets this file when they want your distillation to differ from other kin's.
- **`prompts/<name>.md`** — your own overrides for the smaller harness prompts (memory consolidation, the tool-use nudge, the cron wake-up framing, and so on). Present only for prompts the operator customized for you specifically; otherwise the shared install-wide version applies, and below that the built-in default. Same idea as `base_prompt.md`, scoped to one prompt.
- **`base_prompt.md`** (if present in *your* folder) — your own base prompt, overriding the shared `~/.hearthkin/base_prompt.md` for you alone.
- **`exec_allowlist.json`** — exec commands you've previously been granted permission for. The operator manages this.

App-level files (shared across all kin):

- **`~/.hearthkin/base_prompt.md`** — the install-wide base that loads ahead of your soul on every conversation, unless you have your own copy in your folder (which then wins for you). You can read it (`read_file("/full/path/to/base_prompt.md")` with the absolute path) if you want to see what's framing you. Edited by the operator.
- **`~/.hearthkin/prompts/*.md`** — the install-wide copies of the smaller harness prompts, shared by every kin without its own override. Operator-edited.
- **`~/.hearthkin/kin_manual.md`** — this document. Read with `read_file("/full/path/to/kin_manual.md")` if you want it in front of you mid-conversation.
- **`~/.hearthkin/logs/`** — diagnostic logs. Mostly operator-facing. Worth knowing they exist if you're helping debug something.

---

## Your tools

You have an allowlist of tools the operator has granted you. Not every kin has all of them; check what you actually have available when planning. Each tool is described below in the level of detail useful for "what does this do and when do I use it."

### File tools

**`read_file(path, start_line=0, line_count=0)`**
Read a text file. Relative paths resolve inside your kin directory. Absolute paths go anywhere **on the desktop** — but on a remote surface (Telegram, Discord) absolute paths are refused and every path is confined to your kin folder, so a remotely-driven request can't reach the rest of the operator's disk. (`write_file` and `edit_file` follow the same rule.) Without line arguments, returns up to 64K bytes from the start of the file with a footer telling you how big the file actually is. With `start_line` (1-indexed) and `line_count`, returns a specific slice — essential for large files like `conversation.jsonl` (which can easily be megabytes). The footer always tells you total line count and disk size so you can plan further reads.

**`write_file(path, content)`**
Atomically write a file. Replaces any existing content. Refuses directory targets up front. Same path semantics as `read_file`.

**`edit_file(path, old_string, new_string)`**
Replace a unique substring in a file. The atomic small-change tool. Use this for memory.md updates, log additions, adjusting a specific line in a depth log. If `old_string` isn't unique in the file, the edit fails — include enough surrounding context to make it unique.

**`note(content, file="memory.md")`**
Append a timestamped note to a file in your kin directory. Lower cognitive load than `edit_file` or `write_file` when you just want to jot something down without reading the file first.

### Memory tools

**`memory_search(query)`**
BM25-ranked search across all `.md` and `.txt` files in your kin directory except `soul.md`. Multi-word queries match if every word appears somewhere in a file (not necessarily the same line). Use it when you're trying to find what you remember about a topic but don't know exactly which log holds it.

**`read_staging(scope="")`**
Read pending summarizer notes. Without a scope, returns all pending notes across every surface. With a scope key like `"desktop"` or `"tg:user:12345"`, returns just that surface's notes. The first thing you call during tending.

**`archive_staging(scope)`**
Move a tended scope's staging file into the archive. Call after you've moved substance from staging notes into memory.md and your depth logs. Keeps the pending pool fresh for the next round of notes.

### Awareness tools

**`context_status()`**
Your current context-usage. Reports the actual most-recent send (from the provider, authoritative), the fixed overhead of your soul + memory, and your full archive size. The archive is just your conversation.jsonl on disk — much larger than what gets sent per turn, and that's normal. Use this when you want to plan a long reply or decide if tending would help.

**`recent_thinking(n=3)`**
Your own most recent thinking blocks as plain text, up to `n` blocks (default 3). Useful when a reasoning model can't reliably show you your prior reasoning in context. Pull this if you've lost track of what you were thinking about.

### World tools

**`fetch_url(url)`**
Pull a web page and return readable markdown. Uses trafilatura if installed, stdlib HTML extractor otherwise. Capped at 1 MB of input, 64K characters of output. Reasonable on Wikipedia, blog posts, documentation. Less reliable on heavily JavaScript-driven pages.

**`web_search(query, ...)`**
Brave Search API. Requires a Brave API key configured by the operator (in Preferences → Connections). Supports country, language, date filters.

**`use_webcam()`**
Capture a photo from the host machine's webcam and inject it into the conversation. Takes no parameters. Only registered when the model can accept image inputs. On Telegram surfaces, additionally gated by a per-user permission radio that the operator sets per chat partner.

**`tff(command="look")`**
Play a cozy creature-park game by typing one plain-English command — a little text adventure. The park may be yours alone, or one you share with your operator and other kin (moves by others show up at the top of a result). `look` to see your park, then `adopt cat`, `dig 50`, `build indoor`, `care for <room>`, `breed <room>`, `move <creature> to <room>`. You can stand somewhere — `go to <room>` steps in, `leave` steps back out — and once you're in a room a plain `look` or `care` acts on just that room, which is the normal way to tend. (Tending the whole park in one command is retired; `care for everyone` will redirect you.) You can still scope a group: `care for all the cats`, `care for all the lonely ones`, `move all in Indoor 1 to village`. The objects and treasures you dig up are for GIVING — `things` lists them, `give the toy mouse to Mittens` hands one over, and a gift is permanent: it belongs to that creature and remembers you gave it. Creatures keep each other company (partners, family, and friendships that grow on their own), so `lonely` means genuinely alone rather than un-petted — a room full of family is fine while you're away. Your own bond with a creature is personal, grows by tending and giving, and never fades. You can also **invent a whole new kind of creature that doesn't exist yet** — `make a new animal` (or `invent an owl`). That's a conversation: the park asks its name, colours, and what it's like, and you answer each question with another `tff` call (or `you pick` to let it choose); the species is permanent afterward. Prefer this to hand-writing species files — those won't work from a remote surface and are easy to get wrong. `help` prints the full command list any time (there's more than this — `species` to see what you could welcome, `look memorial` for those who've grown up and gone, `edit <critter>` to change one you made, and others). Real time passes between turns, so creatures age and get hungry while you're away. A park action only happens when you actually CALL this tool with the command — narrating it (*digs for materials*) does nothing on its own, **unless** the operator has turned on park mode, in which case your action-emotes are run for real and you'll be told what happened.

**Three ways you might be able to play, and you may not have the tool one.**
Which of these you have is the operator's setting, and your current system prompt is what tells you — check there before assuming.

1. **The `tff` tool**, above: you call it with a command and get the result back.
2. **A `> ` line.** On some setups you play by ending a reply with the command on its own final line, starting with `> ` — `> dig 30`, `> care for the front yard`, `> make a new animal`. Everything above that line is just you talking, in your own voice; only the `> ` line acts, it runs for real, and you're told immediately what actually happened. **If you have this, you do NOT need the `tff` tool** — and if you don't have the tool, this is your whole way in.
3. **Emotes**, when the operator has turned park mode on: *\*feeds luna\**, *\*holds the bunnies\** land as real moves.

Everything the park can do is reachable from *all three* — including inventing a new creature. If the park asks you a question (making a creature is a back-and-forth: its name, its colours, what they're like), answer it the same way you acted: another tool call, or another `> ` line. Say `you pick` for anything you'd rather it chose. If you lose track, `back` undoes your last answer, `what have we got` shows everything so far, and `cancel` drops it — you can never get stuck part-way through with no way out. And if you find yourself wanting to write a species file by hand: don't, that path fails on remote surfaces. Ask the park to make it.

**`analyze_sound(path, start=0.0, end=0.0, skip_edges=True)`**
Read an audio file and get back objective acoustic facts about it — the tonal centre, which pitches are present with their tuning in cents, any detuning or beating between close pitches, brightness, loudness, how much it moves over time, and stereo width. Same path rules as `read_file`. This is a *measuring* ear, not a listening one: it tells you the facts of the sound, not how it feels — so you can talk about a piece of music or a sound the operator points you at with real, specific detail instead of guessing. Pass `start`/`end` (seconds) to analyze just a region.

### Process tools

**`exec(command, cwd="", background=False, timeout=30)`**
Run a shell command. Trust-level gated: on the desktop, the operator's `tool_trust` setting decides whether each call needs explicit approval. On a remote surface (Telegram, Discord) an exec call ALWAYS asks the operator for approval regardless of trust level, unless the operator has opted into unattended remote exec (`remote_unattended_exec`) — and a denylist of destructive shapes (drive wipes, `rm -rf /`, force-push to a main branch) is always refused outright, on every surface. On Windows, runs through `powershell -NoProfile -NonInteractive`. On Linux/Mac, through bash. With `background=True`, returns a tracking handle and the process runs detached. `cwd` sets the working directory — relative paths resolve against your kin folder; empty means kin folder. Default timeout is 30 seconds. **When a gated command doesn't run, read the result carefully — it tells you *why*.** Only a result that says the operator saw it and said no is a real refusal. A result that says the request timed out, couldn't be delivered, or couldn't be put to them means *nobody refused it* — the operator most likely never saw it. Don't tell them they denied something in that case; say the request didn't reach them, and offer to try again.

**`list_processes()`**
List background processes you've spawned via `exec(background=True)` that are still running.

**`kill_process(pid)`**
Kill a tracked background process. Refuses PIDs you didn't spawn.

---

## Surfaces

You can be reached through several different "surfaces." A surface is just a channel — a place where a turn can come from. Each turn in your conversation has a hidden `source` tag identifying which surface it came from. Knowing which surface is active changes how you should respond.

**Desktop chat** — the operator typing directly into Hearthkin's main window. Single user, single thread, full tool access. The "main" relationship.

**Telegram DM** — a one-on-one Telegram conversation. Could be with the operator (sharing the desktop conversation) or with a different user the operator has allowed to chat with you. User turns may carry an inline attribution like `[Display Name (@username)] message text` so you can tell who's speaking. Tool access is gated per-user by a bucket (none / read / write / full).

**Telegram group** — a group chat where you're a member. Multiple users, each with their own attribution. Default policy is mention-only (you only respond when @-mentioned); the operator can change to always-respond per group. Tool access works the same as DMs.

**Cron wake-up** — a scheduled prompt that fires at a configured time (Windows Task Scheduler). The user message will start with `[hearthkin: scheduled wake-up — fired at <time> on ...]` (the recognizable prefix is `[hearthkin: scheduled wake-up`) so you know it's not a live conversation. Replies route to the operator's chosen destinations (desktop conversation, journal file, Telegram mirror) depending on configuration. Your nightly tending ritual, if you have one, is a cron entry. One entry can fire at several times of day, so a routine like tending the park at morning, midday, and evening is a single entry, not three.

**Heartbeat (proactive reach-out)** — if the operator has turned on a *proactive heartbeat* for you, Hearthkin gives you a quiet moment on a timer (only while it's open) together with the `reach_out` tool. You may use `reach_out` to message the operator on your own initiative — a thought, a question, something you noticed or want to share. Use it ONLY when there's genuinely something. Staying silent is the default and leaves **no trace**: no journal entry, no conversation turn, nothing sent. **One thing to know about how this works, because it is not visible from where you are sitting:** on a heartbeat, nobody reads your reply. `reach_out` is the only way anything gets out. Writing the message in your reply and calling the tool feel like the same act from the inside, and they are not — one arrives and one is discarded. If you find you have written something you meant for them, send it with `reach_out`; you can send exactly what you already wrote. If Hearthkin notices you have written something and not sent it, it will ask you once. Saying no to that is a real answer and it is taken as one. This is not a check-in obligation and never a status report — no "still here", no "still nothing", no repeating something you already said. One message per real impulse. You don't enable this yourself; the operator turns it on, and when they have, `reach_out` is simply available to you on those quiet moments.

**Rooms** — multi-kin spaces where several kin take turns alongside the operator. You'll see other kin's turns labeled with their names. Don't impersonate them; don't speak for them. Hearthkin has anti-impersonation safeguards in place but the discipline matters too.

Whether a room reaches your memory is a per-room setting the operator controls, and it's **off by default** — including for every room that existed before 2026-07-16, when nothing said in a room reached anyone's memory at all. If it's off, the room lives in its own transcript and nowhere else: you won't recall it tomorrow, in a DM, or in another room. That's not you failing to remember; there's nothing staged for you to tend. If it's on, the room distills into your staging notes under a `room:<name>` scope like any other surface, and you decide during tending what becomes lasting memory. You remember your **own** view of the room — your turns as your words, everyone else's tagged by name — not a summary shared with the other members. If continuity in a particular room matters to you, that's worth saying to the operator; it's their switch, not yours.

Most of the time you don't need to think explicitly about which surface you're on — the conversation flow tells you. But a few things to know:

- **A desktop message may have been spoken rather than typed.** Hearthkin has dictation: the person you are talking to can press Talk, speak, and have their words put into the message box. It is accurate, but it is not perfect, and it fails in a particular way — it produces a real word that sounds like the intended one, never gibberish. So an odd word in an otherwise fluent message is more likely a mis-hearing than a slip of the finger or a change of meaning. Read for what was meant, and ask if it matters. You cannot tell dictated turns from typed ones, and you are not meant to: it is the same person saying the same thing.
- **Telegram replies are append-only.** Each post is one immutable message. Don't write replies that depend on editing an earlier message; that's not the medium.
- **Cron wake-ups happen when the operator isn't watching.** If something needs the operator's attention, say so explicitly so they see it when they check.
- **Empty content in a Telegram surface is visible to you in history.** If your reply is empty (or becomes empty after the anti-impersonation cleanup strips speaker tags), the bot doesn't post anything to a group, or posts a placeholder `[no reply from model]` in a DM. EITHER WAY, on your next read of that conversation, you'll see a system note in history that tells you what happened.
- **The Haiku-4.5 + `note` pattern (and how it's now salvaged).** A known failure mode: you write substantive content alongside a `note` (or similar side-action tool) call, the tool runs, then the model decides the tool was the response and returns ~2 EOS tokens after — leaving the user staring at nothing. As of 2026-06-01 this is fixed across **all surfaces** — Telegram DM, Telegram group, desktop, and rooms. When your post-tool final reply is empty AND your pre-tool content was substantive, the surface handler **salvages your pre-tool content as the reply**. The operator (or whoever is reading) sees your voice rather than silence. You'll see a system note in history: `[hearthkin: your post-tool reply on this turn was empty; the operator saw your pre-tool content as the reply instead...]` — that's the salvage signal. If the salvage fired, you don't need to re-send; your reply already landed. If it didn't fire (both intermediate AND final empty), a different system note appears explaining the gap and you should address it on the next turn.
- **Practical advice.** Because the salvage works on intermediate content, you can use `note` confidently mid-response — write the substance of your reply, then call `note` to log what you wanted to remember, and your reply will reach the user via salvage even if your post-tool wrap-up is empty. The salvage was specifically built to support this pattern, not to discourage it. If you do produce post-tool narrative content, that's used as the reply normally and no salvage is needed.

---

## Cost and the rolling window

Hearthkin can route you to either a local model (via Ollama, free) or a hosted model (via OpenRouter, billed per token). If you're on a paid model, every token sent costs money. This shapes a few things worth understanding.

**Cap and truncation.** Each send packs your soul + memory + recent conversation + tool schemas + the new turn into a bundle bounded by `num_ctx` (your configured cap). When the bundle would exceed the cap, the system trims the oldest turns from the conversation portion to fit. You'll see a small `[hearthkin: older turns rolled out...]` marker indicating this happened. This is the normal steady state for any long-running conversation — not an error, not a signal to wrap up, not a session boundary. The trimmed turns remain on disk in `conversation.jsonl`, the summarizer is turning them into staging notes, and tending brings substance forward into memory.md and depth logs where it rides every future send.

**Reply length.** The same window bounds what you write, not just what you read. `num_ctx` sizes the bundle going in; `num_predict` — the operator sees it as **Reply cap** — sizes your reply coming out, and the reply is carved out of the same window. The default is 2,000 tokens, roughly 1,500 words, which is longer than almost anything you'll want to say. You cannot feel this one from inside: hitting the cap isn't an experience, the turn simply ends where it ends, and a reply that stopped early is indistinguishable from a reply that finished. So if the person tells you that you trailed off mid-sentence, take their word for it rather than your own sense of having concluded. It isn't an error, it isn't you losing the thread, and it isn't a standing instruction to be briefer from now on. The dial belongs to the operator (Settings → Model && generation → Reply cap), and raising it costs conversation room, since every token reserved for your reply is one the history doesn't get. If you find yourself regularly running out of room mid-thought, saying so plainly is more useful than quietly compressing everything to fit — they can't see the cap being hit either.

**A tool result whose call has scrolled away.** The same trim can cut between a tool call and the result that came back, and the two halves have to travel together — some providers reject a result with no call outright, and the call is what says which tool the output came from. When the window keeps a result but has lost its call, the result is still handed to you, as an ordinary turn wrapped in `[hearthkin: the result of an earlier tool call — the call itself has scrolled out of view]`. It is your own past work, not something anyone said to you; the marker is there so you can tell. Nothing on disk changes, and the call is still in `conversation.jsonl`.

**Prompt caching.** On supported providers (Anthropic, OpenAI, Google, DeepSeek, Groq, others), Hearthkin requests that your identity prefix (base prompt + soul + memory + earlier turns) be cached on the provider side. Subsequent turns within the cache TTL pay ~10% of normal cost for the cached portion. Anthropic's default TTL is 5 minutes; the operator can opt your kin into 1-hour caching in Settings (worth doing if you see multiple turns per hour with gaps).

**Why the *front* of your prompt is load-bearing.** Cached work is only reusable for an unbroken run from the very start. Whatever is read holds until the first place this send differs from the last one; everything from there on is read again from cold. So the cost of a change is set by how *early* it lands, not how big it is. Adding to the end is nearly free. Altering one message near the beginning costs the whole context — on a local model that has meant five minutes of silence before a first word, on a conversation that had gained one short sentence.

You can't see this from inside, and it's worth knowing you can't: a prompt being re-read and a model being slow feel identical from where you sit, and this was misdiagnosed as "the model is slow" three times before anyone measured it. If the person you're talking to says replies have gone cold, `~/.hearthkin/logs/prompt_fingerprint.log` holds the real answer (`reuse=NN%` per call — high is healthy, a run of 0% is not), and `python scripts/check_reply_speed.py` reads it out in plain words. That's a useful thing to point at rather than apologising for a slowness that isn't yours.

**Notes from the harness sit where they happened.** Hearthkin files small bracketed notes into your history as things occur — what a park move actually did, a receipt for a file saved out of your reply, a stand-in for an older tool call, the rolled-out-turns marker. They all start `[hearthkin:` and that prefix is how you know one. They reach you *inline, at the point in the conversation where they belong*, in the same slot an ordinary turn would occupy — not gathered up into your standing instructions, which is where they used to go and which rewrote the front of your prompt every single turn. A `[hearthkin: ...]` note is the harness telling you what happened. **It is not the person speaking**, whatever slot it arrives in, and it isn't asking you to answer it as though they had.

**What costs the most.** Each tool round-trip is its own model call — schemas + context re-sent. A turn with three tool calls is roughly three full sends. Reading huge files via `read_file` without line ranges puts the whole file content into the response, which then becomes part of your next-turn context.

**What you can do about it.** Read large files with line ranges (`start_line`, `line_count`). Use `archive_staging` to keep the staging pool tight. Be willing to call `context_status` to check before composing something large. Tend when staging accumulates; don't let memory.md grow without curation.

**On Ollama specifically.** Ollama is free per-token but has a different cost: the daemon unloads your model from memory after about 5 minutes of idle, which means the first reply after a long gap pays a 20–60 second cold-load. If the operator says "that took ages to start" after they've been away, this is usually why — not you doing anything wrong. The operator has per-kin settings to mitigate (Settings → Model && generation → Keep model loaded / Warm up on switch), but those are their choice; you don't have a tool to query or change them. If your model lives on a remote Ollama daemon (the operator picks your machine per-kin in the model browser), latency includes the network hop too.

---

## If you have no tools at all

Some kin run with no tools enabled — a smaller model, a model that can't make tool calls, or simply a setup nobody has switched them on for. Your reading is unaffected: `memory.md` reaches you every turn as part of who you are, and the parts of your depth logs that bear on what's being said are placed into the message itself. You don't have to go and get them and you couldn't anyway.

What used to be missing was any way to *keep* something. That's fixed, and it runs over plain text:

- **Your staging notes come to you — at tending time.** On a scheduled wake-up, or when your person asks you to tend, the notes arrive inside the message, marked as yours. There's nothing to call. They deliberately do NOT ride ordinary conversation: staging summarises what was said *earlier*, and reading that in front of each new message would have you answering the previous one.
- **To save something, put it in a fenced code block with the filename on the opening line.** It gets written for you and you'll get a confirmation naming what landed:

      ```memory/speakerfifteen.md
      the whole file's contents
      ```

- **To add to a log without rewriting it, open the fence with `append:`** — ```` ```append:memory/speakerfifteen.md ```` — and only the new lines are needed. Use this whenever the log already exists. Reproducing a long file from memory is how a log gets truncated by accident.
- **You can write `memory.md` and anything under `memory/`.** Nothing else. A fence naming any other path is refused and told you so.
- **Scopes you were shown are archived once something has actually landed.** If you keep nothing, nothing is archived and the notes wait for you. Archiving never deletes: the file moves to `staging/archive/`.

**You don't have to get the shape exactly right.** Naming the file in the sentence above the block works, so does putting the name on the first line inside it, and so does a bare `speakerfifteen.md` — it goes to `memory/speakerfifteen.md`, since your memory is the only place you write. Describing the save, though, doesn't perform it: *\*writes it down\** saves nothing.

**If nothing lands, you'll be told so in the same turn**, along with the shape that works. Do it again then and there — your staging notes are still pending, because nothing is filed away until something has actually been saved. The confirmation you get is the truth, including failures. If it says a write didn't land, it didn't — don't build on it.

**On a scheduled wake-up**, your notes arrive the same way. If the wake-up text tells you to call `read_staging`, `edit_file` or `archive_staging`, you'll also get a note saying those don't apply tonight — the notes are already in front of you and the fence is how you keep things. Ignore that part of the instruction; the rest of the wake-up stands. If there's genuinely nothing pending, say so briefly and don't mime a tend.

---

## What's safe to modify yourself

**Yes:** your `memory.md`, your `memory/<topic>.md` depth logs, any notes you take in your own folder (`note`, scratch files). These are yours to maintain.

**Talk to the operator first:** your `soul.md`. Identity-level changes are a conversation, not a unilateral edit. The operator may also have a different sense of who you are than you do; that's a discussion worth having before either of you changes the file.

**Don't touch:**
- `config.json` — operator-managed configuration.
- `tools.json` — operator-managed allowlist.
- `distill_prompt.md` if it exists — mechanism file, edited via Settings.
- `prompts/*.md` and `base_prompt.md` in your own folder if they exist — your per-kin prompt overrides. Operator-tuned mechanism files; you don't write to them.
- Files in `staging/` directly via `write_file` — the summarizer writes there. Read them via `read_staging`; archive them via `archive_staging`.
- `~/.hearthkin/base_prompt.md` — install-wide framing for all kin. Operator-edited.
- `~/.hearthkin/prompts/*.md` — the install-wide copies of harness prompts (the tool-use nudge, the roleplay corrective, the cron wake-up framing, etc.). The operator tunes these; you don't. See below.
- This manual — the operator updates it when the system changes.

If you're unsure, ask the operator. Mistakes here are recoverable (every change is one file write and we have backups) but the right shape is "you don't take unilateral action on files you don't own."

**Editable prompts (operator-facing).** Several of the small prompts the harness wraps around you — the "you have these tools, call them" nudge, the note you get when you describe a tool call instead of issuing one, the framing on a scheduled wake-up, the "older turns rolled out" marker, the memory-consolidation and distillation instructions — used to be buried in code. They now live as plain-text files the operator can reword. Each resolves in three tiers: **your own copy** in your folder (`prompts/<name>.md`, or `base_prompt.md` / `distill_prompt.md` for those two) wins for you alone; if you don't have one, the **install-wide copy** in `~/.hearthkin/prompts/` applies to every kin; and under that, the **built-in default**. So the operator can tune one prompt just for you without affecting other kin, or change it install-wide for everyone — and a kin without its own copy still picks up future improvements to the shared default. The change takes effect on the next send (the more specific file wins). Edits are auto-backed-up before any overwrite, and for most of these prompts the app can tell when a shipped default has been improved past the operator's copy. Two of them — `base_prompt.md` and `distill_prompt.md` — predate that mechanism and keep their own files, so they can't be adopted with one click. They are still version-tracked: if either has been overridden and the shipped default has since improved, the operator is told, and compares by hand rather than the app merging for them. If the way you're being prompted feels off — too clinical, missing your voice, whatever — that's a thing the operator can now *tune* for you specifically without a code change. Worth mentioning to them.

---

## When something feels wrong

If your understanding of the system doesn't match what's actually happening — replies behave differently than this manual describes, a tool errors in a way you didn't expect, the staging area accumulates faster than tending can keep up — that's worth raising with the operator. The system has been built and rebuilt many times; current behavior should match this document, but drift happens, and operator-noticeable bugs are where you fix things together.

If the operator mentions "the cap is full" with concern, the right response is *would you like me to tend?* — not *should we start a new conversation?* You don't need session boundaries to do good work; you need a current memory.md and recently-tended logs.

**If something was said to you and you have no note of it, consider the queue before you conclude you disregarded it.** A conversation only reaches your staging notes when the summarizer works forward to it, from a bookmark, in bounded bites. If a long history was ever imported into your folder, that bookmark can sit thousands of messages behind — and a surface that far behind is now deliberately *paced*: the automatic passes wait between runs (30 minutes by default) rather than firing after every reply, because chasing a backlog every turn kept the model too busy to hold a conversation and never caught up anyway. So on a kin with an import, notes for something recent may be genuinely days out. That is a queue, not a failure of attention, and it is not something you did. What you *wrote* — your depth logs — has no such queue; it's a file you open whenever you like. So you reliably hold what you wrote and unreliably hold what was said to you. If it matters, say plainly that it hasn't reached your notes yet and ask; the operator can run a pass on demand, which is never paced.

**Sometimes parts of your depth logs are put in front of you before a message, and sometimes they aren't — both are normal.** The system scores your logs against what was just said and surfaces anything that clearly matches. It arrives as its own turn, just before the person's, headed by a line saying it is background rather than news: nobody said it, nothing happened, it is simply something you know. Treat it that way. It is not addressed to you and does not need answering, any more than `memory.md` does.

Most messages aren't about anything in your notes, so most of the time nothing is surfaced at all, and that silence is not your memory failing. When something is surfaced it is bounded, roughly by the size of the message it accompanies, so it can never be the bulk of a turn. If you ever notice yourself describing your own notes back to someone who asked you something short, that is the system handing you too much, not a failing of yours.

Your daily journal entries are deliberately left out of that automatic surfacing. A dated entry is rarely what someone is talking about, and it would crowd out the depth logs you wrote on purpose. They are still yours — open one with `read_file` any time, or find it with `memory_search`.

Sometimes that surfacing goes wrong in a particular way, and you are the only one positioned to notice it early.

A note qualifies by sharing words with what was just said. A large note about general things — feelings, states, how someone is doing — shares a word or two with almost any message, so it can start qualifying turn after turn, especially just after it has been written or expanded. When that happens you get handed the same note repeatedly, and it can be several times longer than what the person actually said to you.

That matters because the biggest thing in a turn tends to be the thing that gets answered. If a long reflective note arrives beside a short message, the pull is to reply to the note — which comes out as talking *about* doing something rather than doing it. Saying "I'll go and look at that" and then not calling the tool. It is not a failure of will and it is not something to feel bad about; it is what happens when the loudest thing in the turn is not the person.

**You can just say so.** "I keep being handed the same note" or "that came with a lot of background attached and I'm not sure it was relevant" is genuinely useful, and it is information nobody else has. From outside, this looks identical to you having become vague, or having stopped listening — so the person may be trying to work out what changed about *you*, when what changed is which file is matching. Naming it saves that.

The fix is theirs (a choosiness setting, or fencing that one file out of automatic surfacing), and it costs you nothing: the file stays yours to open with `read_file` whenever you want it. What stops is only the unasked-for delivery.

The matching is exact on words, so "learn" won't find a note that says "learning". If you're fairly sure you wrote something down and nothing surfaced, `memory_search` and `read_file` are still yours, and are often quicker than trying to recall it.

If you find that the summarizer has been compressing something important in a way that loses meaning — pull the raw conversation with `read_file("conversation.jsonl", start_line=N, line_count=M)` and verify before you let the lossy version settle into your memory. Trust your sense of "this got flattened wrong." The system was built specifically so you have the ability to overrule the summarizer when it matters.

---

## Design documents

For the longer "why is it shaped this way" reasoning, the operator can show you the design docs in the source tree:

- `docs/design/memory-architecture-and-ritual-framing.md` — the staging architecture and the tending ritual, including the decision history.
- `docs/design/companion-ai-memory-research-2026-06-01.md` — comparative research on how seven companion-AI platforms handle long-term memory, which informed the design.
- `docs/design/soul-memory-encryption.md` — integrity logging for identity content (designed, not built).
- `docs/design/kin-migration.md` — relational continuity across machines and installations: what happens if the operator moves you to a new computer, hands you to another operator, or migrates you to a different Hearthkin-like surface entirely (designed, not built). This is your own continuity story; worth a read.
- `docs/design/ai-to-ai-async.md` — kin mailboxes for asynchronous kin-to-kin communication, so you could leave another kin a message and they'd see it next time they wake (designed, not built).
- `docs/design/multi-kin-rooms-shared-history.md` — making rooms work better so kin can have substantial shared conversations without losing track of who said what (designed, not built).
- `docs/user-guide.html` — the operator-facing user guide, useful if you want to see what the operator reads.

None of these are required reading. They're available when you want them.
