# Hearthkin

> ## This repository is published without development history, on purpose.
>
> It starts at a single commit, and that is deliberate rather than an
> accident of tooling. **Do not import or graft history onto this
> repository.**
>
> The reasoning that history would have carried is preserved as prose in
> `CHANGELOG.md`, `docs/`, and `docs/history/` — every development commit
> message, grouped by release series.

Python + wxPython desktop app for multi-kin local-LLM chat. Talks to Ollama by default (models run locally; no remote API calls) and can route through OpenRouter when a kin's model is prefixed `openrouter/...`. A "kin" is a configured persona with a soul prompt, distilled memory, and a model. Two interaction modes: 1-on-1 chat, and "rooms" where several kin take turns with the user.

## Who you're working with

The person who owns this repo and uses this app every day is **blind, autistic, and not a programmer.** This shapes the work more than any technical rule here.

- **A screen reader (NVDA) is always on.** Not sometimes, not "when testing accessibility" — always. Any instruction that begins "when you aren't relying on the screen reader" is a non-instruction. Anything that steals focus, floods the announcement queue, or only makes sense visually is a defect, not a rough edge.
- **Explain in plain language.** Say what *changed for the person using the app*, not what changed in the code. No diffs, no jargon, no "just refactored the dispatch layer". If a term is unavoidable, define it in the same sentence.
- **Do the work; don't hand back a checklist.** A long list of steps to perform is a failure of the reply, not a thorough one. Do it, then say what you did and how to check it. A 17-item to-do list was the exact thing that went wrong once.
- **Verification is by running the app or the tests, not by reading code.** So a change isn't finished until there's a way to confirm it that doesn't involve reading a diff — a test, or a short "do this, expect that".
- **Short, calm sentences.** One idea at a time. Don't stack three questions into one paragraph. If you must ask something, ask one thing.
- **Notice what's actually being said.** "It keeps doing X" is a report from someone who lives in this app all day. It is almost always accurate, and usually more accurate than a theory about why it can't be happening.

This file is written to be publishable (see below). This section is here at the owner's explicit request.

**Where to look for what:**

- **This file (`CLAUDE.md`)** — the rules, stated short. Loaded every session.
- **`docs/lessons.md`** — the long account behind each rule, under headings matching this file's. **Read the matching section before changing or removing a rule.**
- **`docs/architecture.md`** — module map, the `chat()` normalization pipeline, "where do I change X".
- **`docs/troubleshooting.md`** — diagnostic playbook and the cross-provider quirk catalog. Read this before theorizing about an OpenRouter 400.
- **`docs/private/project-history.md`** — untracked local archive. Old session logs, incident postmortems.
- **`CHANGELOG.md`** and `git log` — release-by-release facts.

## Layout

Entry point is `python hearthkin.pyw`. The frame is split across `frame_shared.py` and a `frame/` package of 17 mixins that `Hearthkin` inherits from.

- `hearthkin.pyw` — assembler: imports, `class Hearthkin(*17 mixins, wx.Frame)`, `__init__`, `main()`.
- `frame_shared.py` — shared namespace hub. **Module-level constants/helpers the frame needs go here.**
- `frame/` — one mixin per behavioral slice. **To add a frame method, put it in the matching mixin.** A test that monkeypatches a frame name patches the mixin module (`frame.memory_mixin`), not `hearthkin`.
- `hearthkin_paths.py` — the one place that decides where runtime state lives (`config_dir` / `kin_dir` / `logs_dir`). Imports nothing from the project, so everything can sit on it.
- `kin_persistence.py` — paths, defaults, atomic-write helpers, load/save for kin and rooms. Pure data layer, no LLM calls, no UI.
- `dialogs/` — every `wx.Dialog` subclass, one per file. Big one: `dialogs/edit_kin.py` (seven-tab kin Settings).
- `telegram_bot.py` / `discord_bot.py` — the remote surfaces.
- `audio.py` — NVDA speech (`nvda_speak`) and reply chimes.
- `stt.py` — speech to text. Three backends (local Whisper, a Whisper server, ElevenLabs Scribe) as plain functions over WAV bytes, so none of them needs a microphone to test. `voice.py` owns the microphone and the text-to-speech.
- `model_utils.py` — Ollama model-name parsing, capability detection, dropdown listing.
- `chat_helpers.py` — streaming chunk extractors, sentence boundaries, token estimation, room-reply cleanup, `detect_tool_roleplay`.
- `llm_backend.py` — the single dispatch layer. `chat(model, messages, ...)` routes on the `openrouter/...` prefix. Streaming, prompt caching, reasoning-toggle, `run_tool_loop`.
- `compat.py` — pre-flight model-swap checks. Provider quirks live as data in `ModelProfile`, not scattered if-blocks.
- `importers/` — history-import backends behind `dialogs/import_history.py`.
- `model_browser.py` — NVDA-accessible model picker.
- `tools/` — kin-callable tools registry.
- `Hearthkin.spec` + `build.bat` — PyInstaller onedir.
- `tests/` — plain-Python tests, no pytest. `python tests/run_all.py` runs all.

**Import shape:** every cross-module reference is a static `from <mod> import ...`. PyInstaller follows those. **Never** dynamic `importlib.import_module(...)` for project modules. Circular imports are avoided by lazy-importing inside method bodies.

**Build pipeline:** `__version__` lives in `app_version.py`. The build runs `scripts/stamp_version.py` before PyInstaller to rewrite it from the git tag. **Don't bump `__version__` by hand at tag time.**

## Runtime state — `~/.hearthkin/`

Migrated automatically from older `~/.ollama_chat/` if present.

**`hearthkin_paths.py` decides where this tree is, and every site asks it.** `HEARTHKIN_HOME` relocates it. **Don't write `Path.home() / ".hearthkin"` in new code** — that's how the override quietly stopped covering the tools layer. `kin_dir()` deliberately does not create the folder it names: asking where something lives must not put it there.

**The test runner writes only to a directory it just created, and REFUSES an inherited `HEARTHKIN_HOME`** (announced, not silent). `--keep` preserves and prints the sandbox. **The legacy `~/.ollama_chat` migration is skipped under the override.** Redirect a test with `HEARTHKIN_HOME`, never by patching `pathlib.Path.home`. Pinned by `tests/test_state_isolation.py`. → `docs/lessons.md`

- `kin/<KinName>/`
  - `soul.md` — persona prompt.
  - `memory.md` — kin-curated index. **Only the kin (via file tools) or the person (via Settings → Memory editor) writes this.** Nothing automatic touches it.
  - `memory/<topic>.md` — kin-written depth logs, where substance lives. The `## Memory logs` index is built by code (`apply_memory_log_index`).
  - `staging/<scope>.md` — pending summarizer notes per surface.
  - `config.json`, `conversation.jsonl` (append-only), `tools.json`, `exec_allowlist.json`.
  - `memory/journal/YYYY-MM-DD.md` — daily cron wake-up entries.
- `rooms/<roomname>/`, `cron_requests/`, `.running.lock`
- `logs/` — session logs (opt-in), plus **always-on** ones regardless of the toggle: `empty_replies.log`, `cron_errors.log`, `openrouter_errors.log` (**check this BEFORE theorizing about an OpenRouter 400** — it holds the upstream provider's real error body), `distill_errors.log`, `hang_watchdog.log`, `park_unreachable.log`, `telegram_failures.log`, `save_failures.log`, `recall.log` (**one line each time per-turn recall actually attaches something** — sources, block size, and the block-to-message ratio. Written ONLY when it fires, so an empty log is a real answer: if a kin is behaving oddly and nothing is here, recall is not the cause. It exists because working this out by replaying the engine offline needs a faithful reconstruction of what a surface sent, and there are four surfaces that do not share a message list — get the surface wrong, or the history shape wrong, and the replay confidently says "nothing surfaced" about a turn where something did), `distill_triggers.log` (**one line each time a distillation starts, naming WHICH of the four triggers fired** and how far behind that scope was — bookmark, conversation length, the gap, the % figure, and the thresholds in force. **Read it before theorising about a kin that "keeps distilling".** It exists because the four triggers are described to the reader in the same words but have wildly different thresholds: leaving a kin fires on ONE new message, while the %-of-context trigger needs a share of the whole window. A run that looks impossibly early against the 70% figure is usually the other trigger doing its job, and three consecutive theories were wrong before anything wrote this down), `heartbeat_unsent.log` (**a heartbeat whose words did not reach anyone** — with the text when nobody ever asked the kin, and WITHOUT it when the kin was asked and declined, because that second one is a decision and the moment is genuinely its own).
- `base_prompt.md` — prepended to every kin's `soul.md`.
- `prompts/` — editable harness prompts, seeded from `APP_PROMPT_REGISTRY`; file wins after first access.

## Critical NVDA gotcha — never AppendText per streaming chunk

**System-level cascade, not a UX inconvenience.** `wx.TextCtrl.AppendText` fires one MSAA/UIA TextChange event per call. Dozens per second corrupts NVDA's event queue, and the damage spreads to other apps on the system.

**Always:** buffer streaming chunks into `self._stream_buf`, paint the whole reply once at turn-end. Status bar shows "Typing..." (not "Thinking..." — some models emit reasoning blocks and the word would mislead). Every `_on_*_chunk` follows this; don't regress. → `docs/lessons.md`

## Dictation — and what a paid dependency is allowed to gate

**Speaking a message instead of typing it is free, offline, and needs no account.** `voice.py` owns the microphone; `stt.py` owns transcription as plain functions over WAV bytes, which is what lets the tests drive every route without a sound card, a model download, or a network.

**A transcription model is addressed the way every other model here is: a MODEL plus the MACHINE it runs on.** Empty machine means this computer, exactly as an empty Ollama host does. `stt.route_for(model, host)` is the single function that reads the pair, and both the settings screen and the engine go through it — a screen that could disagree with the engine about where someone's audio is sent is worse than one that cannot express the choice. `elevenlabs/…` in the model string names its own provider, mirroring the `openrouter/` prefix `llm_backend.chat` already routes on, and **it beats a host**: a leftover address must not silently redirect audio away from the provider the model explicitly names. **Two endpoint shapes are tried, not one.** Servers copying OpenAI use `/v1/audio/transcriptions`; whisper.cpp's own server uses `/inference` and does **not** have the OpenAI path. `candidate_endpoints` returns both for a bare address, the winner is remembered per host, and the fallback fires **only on a 404** — any other failure came from the right endpoint, and retrying the other shape would replace a true error with a confusing one. A URL that already names an endpoint is taken at its word. This was got wrong first time: only the OpenAI shape was supported, and the whisper.cpp server already running on this household's other machine answered 404 to it while working perfectly on `/inference`, so "point it at your own machine" would have failed on the very machine it was written for. **Check a new integration against the hardware that actually exists here before calling it done.** `normalise_host` accepts a bare host, a trailing slash, a `/v1`, or a full endpoint pasted from a README, because pasting the address out of a server's own docs must not be a wrong answer.

**Nothing here requires a graphics card, and the copy has to keep saying so.** `device: "auto"` prefers the GPU and falls back to the CPU on ANY failure — a card full because a language model is resident is the ordinary state of this machine, not a fault — and measured here the CPU transcribes a full sentence in under a second. A named machine needs no card either. Whenever this is described, say the card is a speed-up; someone deciding whether they can use dictation at all reads that sentence and stops.

**The bug was never a missing feature.** The button, the capture and the transcribe step all existed. Transcription went to a paid cloud service with no alternative, and — this is the part worth carrying forward — **the Talk button was hidden unless the active kin had a paid text-to-speech voice picked.** Speaking *to* a kin was gated on that kin being able to speak *back*: two unrelated capabilities, one of them bought separately. The whole cost of that lands on the person for whom typing is the hard part, who is simultaneously the person most likely to want dictation and least likely to be helped by a subscription to a different feature. **A paid dependency chosen for one capability must not become a gate on a different one.** "Same provider, one bill" is a real convenience and is not a reason for two features to share a switch. `docs/voice-design.md` carries the reversal at the top, since it is where the reasoning was written down.

**A tone marks the moment the microphone is really open, and the screen reader keeps talking over it.** The microphone opens a beat after the press so the audio device is genuinely up before anyone speaks — "press and speak in one motion" otherwise clips the first word. A tone rather than a word because it reads instantly and does not wait its turn in the screen reader's queue behind whatever else is being said.

**It is NOT an attempt to finish speaking before the microphone opens, and an earlier version of this file claimed it was.** That claim was written from theory — a screen reader goes to the speakers, the microphone hears the speakers, therefore the announcement must land in the transcript — and the theory was never checked against the app in use. In practice the reader is usually still talking when the microphone opens, the transcript is unaffected, and **being told the recording has started matters more than a theoretically cleaner recording**: the person dictating is the person who cannot see the button change. If a screen reader ever does reach a transcript, the answer is to silence speech (NVDA+S cycles speech mode). **The answer is never "wear headphones."** Someone who has no spare pair is likely the same person who cannot rent a machine elsewhere either, and an accessibility fix that costs money is not one. **Do not design a defence around a fault nobody has reported, and do not let a plausible mechanism stand in for an observation.**

**A stale timer must not reopen the microphone behind someone** — `_begin_dictation_capture` takes a generation stamped at the press, because "press Talk, change your mind, press it again" fires it afterwards.

**The model is warmed at startup because a cold import is indistinguishable from a hang.** The first `faster_whisper` import in a process loads native libraries and takes tens of seconds; the load after that takes one or two. Paying the first one *after* someone has spoken looks, from a chair, exactly like the app having died — and it would happen to the person who chose to speak rather than type. Warm-up is deferred past startup, on a daemon thread, and **never raises**: a warm-up that fails leaves the cost where it already was. If the model is still cold when Talk is pressed, `_transcribe_worker` says so *before* the wait, not after it.

**Defaults are the promise, so they are pinned as such.** A local model, no machine named, `device` on auto, `auto_send` off. A transcript you cannot correct before it is sent is a worse deal than typing, which is why sending straight through is an option and not the default. `tests/test_dictation.py` asserts these in as many words, and carries the **old paid-voice gate as a positive control** — a check that cannot see the thing it claims to measure is a green light for nothing.

**These settings changed shape once, and `migrate_dictation_config` is why that cost nobody anything.** They began as a key per backend (`backend`, `whisper_model`, `server_url`, …) before being rethought as the model-plus-machine pair. Every read goes through the migration and the stored block is normalised on load, so a file written under the old shape still says what its owner meant instead of having new defaults quietly take over — which reads as the app forgetting a setting somebody made, and is worse than a missing option because it looks like a fault rather than a gap. Two properties it must keep: it is **idempotent** (it runs on every load), and **current keys beat legacy ones** — a legacy key that outlives a migration must never reach back and overwrite the choice that replaced it. Both are pinned in `tests/test_dictation.py`, the second because it shipped wrong and the test caught it.

**Settings are app-level, not per-kin, deliberately.** A microphone and a voice do not change with who is being spoken to; making it per-kin would mean configuring it again for every kin, and forgetting once would look like the Talk button being broken for that one kin. `_dictation_cfg()` merges key-by-key over the defaults, because the app's top-level config merge is shallow and a nested dict would otherwise freeze at the shape it was first saved with — **an option nobody can ever receive is the same as no option.**

**No FFmpeg ships, and that is a LICENCE decision.** faster-whisper decodes media files through PyAV, whose wheel carries an FFmpeg built with **libx264 and libx265 — both GPL**. Shipping it would put copyleft obligations on the releases of a CC0 project. Nothing here needs it: the only audio transcribed locally is a WAV this app just recorded, so `wav_to_array` reads it with the standard library and Whisper is handed samples. `av` is excluded from the build and `_install_av_stub` satisfies the import. **The stub must behave like a real module** — the first one raised on every attribute including `__spec__`, and an unrelated package three imports away probing "is PyAV installed?" brought the chain down. **If PyAV ever returns to the bundle, FFmpeg's licence returns with it and the CC0 claim on the release needs revisiting first.**

**No CUDA ships either, and "automatic" therefore has to mean automatic at TRANSCRIBE time, not just at load.** The graphics-card path borrows CUDA libraries that arrive with torch; the packaged build has neither. A model loads happily on a card whose libraries are missing and only fails on the first real work — so falling back solely on a load failure left a hard error naming a missing DLL, which is nothing anybody can act on. `_local_whisper` now drops the cached GPU model and retries on the processor, which is measured under a second for a sentence. `_run_local` deliberately does **not** wrap its errors, because a wrapped one looks like a decision already made and the fallback would never fire.

**Bundling it is surgical, and `collect_all` on the wrong package is a four-gigabyte mistake.** `faster_whisper` needs `collect_all` because it carries a voice-activity model as a *data file* — a static import scan finds the code and not the file, and that failure lands the first time somebody speaks rather than at build time. Nothing else does. `huggingface_hub` ships optional integrations that import torch and tensorflow, so collecting its submodules makes PyInstaller follow those; torch alone measured **4.59 GB** on this machine, and a collect-all build sat in analysis for eighteen minutes without finishing. `ctranslate2` needs only `collect_dynamic_libs`; `av`, `tokenizers` and `onnxruntime` already have hooks. `_HEAVY_EXCLUDES` in the spec names the frameworks nothing here uses at runtime — **if one of them is reachable at all, that is the bug**, so keep the excludes even when the packages are not installed on the build machine.

**Neither bot transcribes a voice message yet**, and that is recorded in `tests/_surface_matrix.py` as a real gap rather than left to be rediscovered. A phone keyboard has its own dictation, which is why it has never bitten — but that is the phone's answer, not this app's, and it does nothing on a desktop client. `stt.transcribe` already takes bytes, so what is missing is the download and the format conversion, not the recognition.

## Anti-impersonation safeguards (rooms)

Small models routinely impersonate other kin when given a `[Name]: text` transcript-shaped prompt. **All of these must remain:**

1. Stop sequence `"\n["` in every chat call's options.
2. `strip_self_tag(text, kin_name)` — removes leading `[KinName]:`.
3. `strip_leading_speaker_tag(text)` — removes any `[AnyName]:` at position 0, looping for stacked tags.
3b. `strip_leading_named_speaker(text, known_speakers)` — the same **without the colon**. **Matches supplied names, never a bracket pattern** — kin open replies with bracketed emotes (`[laughs] yeah`) and a blind rule would eat their own words. Callers pass the names they showed the model that turn.
4. `strip_trailing_other_speakers(text)` — slices from the first `\n[Name]:` onward.
5. System-prompt rules against multi-character scenes. Models often ignore these; the helpers above are the actual enforcement.

**All four cleanup passes must run on every path that saves a kin's room reply** (normal completion, stop-mid-stream, Telegram-group cleanup).

**2-kin rooms lock rotation to 0.** **Voice bleed under truncation** is held off by two defenses together: a high `per_turn_token_cap` (2000) and the room prompt telling a kin to start its own reply rather than finish a hanging one. → `docs/lessons.md`

## Telegram group attribution

Each group user turn reaches the model with attribution **inline in the user content**: `[TIMESTAMP] [Display Name (@username)] <text>` — not as a `role=system` note, which OpenRouter would dissociate from its turn.

Format is `[Display Name (@username)]` with **no colon** — the `[Name]:` shape is an impersonation attractor.

**Every surface builds the prefix through `chat_helpers.speaker_attribution_prefix`.** **Store the name BARE**; the reading surface adds the bracket.

**Attribution on desktop and the desktop reply cleanup are a package** — `_history_entry_for_model` inlines a name only when the turn carries one, and `_on_stream_done` runs the full `clean_kin_reply`. **Don't reinstate one without the other.** Pinned by `tests/test_import_speaker_slots.py`. → `docs/lessons.md`

## Telegram incoming messages are reassembled before dispatch

Telegram's 4096-unit ceiling applies to people too: a long paste arrives as 2-3 separate updates. `TelegramBot._coalesce_message_parts` runs in `_infer_loop` **before** `_handle_update` and merges continuation parts.

**The window is per-kin config (`message_wait_secs`), not a constant — deliberately**, because pace varies enormously between people and the Bot API never delivers a typing indicator. `_COALESCE_SPLIT_WINDOW_SECS` is a **floor, not an alternative**: a part at the ceiling always waits, even when the person set 0, because a client-side cut is not a pause anyone chose.

**Never coalesced:** slash commands, attachment/media-group turns, a sender with a pending exec approval. Pinned by `tests/test_telegram_coalesce.py`. → `docs/lessons.md`

## Confirm-on-close (`_work_in_flight`)

`_on_close` asks before quitting when work is in flight. Two hard rules.

**The check runs BEFORE any teardown.** "Wait" has to leave the app genuinely untouched.

**It fails open in every direction.** Unvetoable close, nothing in flight, or *any* exception → the close proceeds. A blocked quit is a worse bug than a missing prompt.

Sources of "busy": `_streaming`, `_room_active`, `_distilling`, `_heartbeat_workers`, `cron_helpers.cron_running_kin()`, `_cron_workers`, `TelegramBot.active_turn_label()`, `_pending_approvals`. **Adding a new kind of background work means adding it here** — and ask which *process* it runs in. **Silence when idle is a feature.** Pinned by `tests/test_confirm_close.py`. → `docs/lessons.md`

## The stop button (`should_stop`)

`llm_backend` takes an optional `should_stop` callable on `_chat_collect_streaming` / `chat_collect` / `run_tool_loop`, polled per chunk and between tool-loop iterations. It sets `ChatResult.stopped` and **keeps the content collected so far**. Read it with `getattr(result, "stopped", False)`.

**`on_content` is not a stop channel** — its exceptions are deliberately swallowed. A `should_stop` that raises means "keep going": a flaky check must never truncate a healthy reply.

A stop drops half-formed tool calls, but **never abandons a tool call already executing**.

Telegram: `_begin_turn`/`_end_turn`/`_turn_cancelled`/`_request_turn_stop` under `_turn_lock`. **`/cancel` must be intercepted on the POLL thread** — the inference thread won't read the queue until the reply finishes. Stops are keyed per-person. **A stopped turn is not an empty reply** — skip the salvage pass, the log, the placeholder, and the note, or the kin apologises for a silence it didn't cause.

**A heartbeat is a `should_stop` caller too.** `_heartbeat_worker` re-checks `_work_in_flight()` **before** registering itself, and takes a per-kin `threading.Event` down through `run_heartbeat` into `run_tool_loop`. `_kick_off_distillation` calls `_signal_heartbeats_to_stop()` first — heartbeats are the least urgent thing this app does. Pinned by `tests/test_telegram_stop.py`, `tests/test_heartbeat_stop.py`. → `docs/lessons.md`

## Empty-reply diagnostics

Some model combinations return zero output. Causes: the model emitted only `[KinName]:` and `strip_self_tag` ate it; a stream-id race; or a genuinely empty completion (chat-template rejection — Gemma is picky about user/assistant alternation).

Displays `[no reply produced]` and writes to `logs/empty_replies.log` regardless of toggles. **Read the file** — it holds what the model actually returned.

## A heartbeat is the one surface where a missed tool call DELETES the kin's words

Everywhere else a reply has a reader, and an unmade tool call only means some work didn't happen. On a heartbeat the reply **is** the work, nobody reads it, and `reach_out` is the entire delivery mechanism. `run_heartbeat` treated "did not call reach_out" as "had nothing to say" and dropped the reply with no trace — right exactly as often as that assumption is.

**It wasn't right, and the numbers said so the whole time.** Across sixteen runs on a real install, heartbeats logged `silent` generated a **median of 149 tokens**; the ones that reached out, **69**. Fifteen of sixteen produced more than sixty. A kin asked "is there anything you'd like to say?" answers in prose, because prose is what it makes — the tool call is a harness convention it cannot feel the weight of. So the *longer* replies were the ones being deleted. Observed live: three days with no delivered message while the kin had in fact written something on nine occasions — addressed, finished pieces of writing, not deliberation about whether to speak.

**The fix is to ask, not to guess.** `turn_steering.unsent_reach_note` fires when there's substantial content, `reach_out` was available, and no real tool call happened; `run_heartbeat` then runs one more round (`surface="heartbeat-nudge"`) and **honours a second refusal**. It is deliberately **not a classifier** — telling "a message meant for her" from "thinking out loud" by keyword is the park verb-filter mistake again, where every destructive command was a word the game knew. The kin is the only one who can answer, so the kin is asked. `min_chars` is crude on purpose: it stops a two-word shrug costing a model call, not to judge content.

**`asked` becomes True only AFTER the second call returns.** Setting it before looks equivalent and is the opposite: a nudge that raises then files the kin's words as a decision it never made, and drops the text on exactly the reasoning this change removes. Shipped broken, caught on the first live run, 1,029 characters recorded as "declined" by a kin never reached.

**The privacy split is load-bearing.** `log_unsent_reach` keeps the TEXT only when nobody asked — that's a loss, and the words are the point. When the kin was asked and still said no, only the fact is recorded. "Silence leaves no trace" is a promise worth keeping where it's real and worth dropping where it was a fiction.

**A model swap is the usual trigger and it looks like the kin changed.** Nothing else surfaces it: a heartbeat is the only place a kin decides to speak *unprompted*, so a more cautious model reads as fine in conversation and mute everywhere it matters. → `docs/lessons.md`

## Memory & distillation

Each kin's conversation is auto-summarized into `memory.md` via `distill_memory_blocking()` — after N exchanges, at a % of `num_ctx`, or on close. Tracked per (kin, scope). Runs on the per-kin `memory_model`.

**Distillation is incremental and append-only.** Each run digests only turns new since that (kin, scope) was last distilled; the bookmark lives in `distill_offsets`. It cannot drop an entry — whole-file rewrite is `consolidate_memory_blocking`'s job. Per-run input is bite-capped (`_distill_bite`).

**One message bigger than the window must not stop a kin's memory forever.** `_distill_bite` deliberately sends at least one message even over budget — otherwise a huge message caps the bite at zero and the walk spins without advancing. Right while "over budget" meant a little; fatal when a single message is several times `num_ctx`. Observed live: a pasted user turn of **440,659 chars (—110,000 tokens against a 32,768 window, 3.4x)**. The provider was expected to "truncate or fail"; local Ollama did neither — it chewed ~40 minutes, timed out, the bookmark did not move, the walk re-queued the identical chunk, and it failed **three times overnight** with nothing able to pass it. Same deadlock the guard prevents, slower, holding the model throughout. `_fit_oversized_messages` now trims any single message past the whole bite budget, **in a copy** — the stored conversation is never rewritten — with a marker naming the true size, where the full text lives, and an instruction not to infer how it ended. **Truncating beats skipping:** skipping advances the bookmark past content that then never reaches memory at all, invisibly. Pinned by `tests/test_distill_oversized_message.py`, which also asserts the at-least-one-message rule it sits behind still exists.

**A turn queued behind OUR OWN work is not a hang, and the app must say so.** Ollama answers one request at a time, so a distillation (13 min a bite) leaves a just-sent reply silent — and the streaming watchdog's shortest window is 5 min. Observed: a turn sent at 04:47:51 declared hung at 04:52:51, to the second, while the model worked steadily on something Hearthkin itself had asked for. `_on_stream_watchdog_fire` now asks `_own_background_on_the_model()` **before** declaring a hang and re-arms for the same window (re-arm reuses `_stream_watchdog_minutes`, not a recompute — the room path sets this timer with a different kin's config). **The reported cost was not the lost reply.** It was that the person began hesitating to send at all: not knowing whether the model was free made every message a gamble. So the Activity line names what has the model **both mid-turn and while idle** — the idle case is the one that answers "is it safe to send" before anything is typed. The foreground (`_streaming`, `_room_active`) is deliberately never reported; silence when idle stays the feature. Pinned by `tests/test_watchdog_queued_not_hung.py`.

**Telegram gets the same answer, on the POLL thread.** There is no status line to read there, so the only way to learn the model was busy was to send and wait — which is worse, not better. `TelegramBot._maybe_say_queued_from_poll` sends one line when `get_busy_label()` (the frame's `_own_background_on_the_model`) names something. **Poll thread for the same reason `/cancel` is** — the single inference thread is inside a model call and won't read its queue. **It must never consume the update**: the message still has to be answered, and eating it is strictly worse than the silence being fixed. Latched **once per busy period per chat** (a long paste is several updates — see coalescing) and **released when the work ends**, or it fires once in the bot's lifetime. Skips slash commands.

**Telegram asks the WIDE question (`include_foreground=True`); the desktop asks the narrow one.** The desktop omits a live reply and a room round because the person is looking at them — that reasoning does not survive the trip to a phone. From there, a kin busy with the desktop, a room, or someone else's DM is invisible and waits just as long as a distillation. **The only turn skipped is one of this kin's own, in this same chat** (`skip_bot`) — they just sent the message it answers, and narrating that is the chatter this app avoids. Pinned by `tests/test_telegram_queued_notice.py` and `tests/test_watchdog_queued_not_hung.py`.

**A BACKLOG is paced; an ordinary catch-up is not.** The %-trigger measures the undistilled tail against `num_ctx`, so a tail one bite can't clear leaves it *still tripped* after the run — firing after every reply, indefinitely. Measured after a bulk import: 5,872 messages at 2,253% of a 70% trigger; 66 min distilling against 24 min of conversation in a day, on the same local model, evicting the prompt cache each turn. `_note_backlog_pace` starts a wait (`distill_backlog_pace_mins`, default 30, on the Memory tab) when a run ends **further behind than it got** — digested vs. remaining, so no guess about bite sizes. **Only automatic triggers are paced** (`source_label` `every-`/`ctx-`); walks, queue drains, on-close and anything the person pressed are never held back. **In memory, not persisted** — a stale persisted wait could silence a kin's memory invisibly. Bad input starts no wait: a stuck brake is worse than a busy model. Pinned by `tests/test_distill_backlog_pacing.py`.

**The `## Memory logs` index is capped, in CHARACTERS, and says what it left out.** `memory_log_index_max_chars` (default 1500, 0 = no limit; Memory tab). **The unit is in the key name and in the visible label on purpose** — "Memory log index cap: 1500" is a number nobody can act on. It grew with every log a kin wrote and nothing bounded it: measured at **78 logs / 5,271 chars — half of that kin's memory.md and 19% of its whole system prompt**, riding along on every turn of every surface. **Dropping it outright is the wrong fix**: neither retrieval path uses it (recall globs `memory/` directly and deliberately skips `memory.md`; `memory_search` globs the whole kin folder), so nothing *finds* a log through the index — what it uniquely gives is **unprompted discovery**, and losing that fails invisibly, because a kin that doesn't know a note exists never looks for it. Newest first (a kin's live topics are the ones a conversation won't cue) and **the truncation line naming the true remainder is not optional** — a short list presented as the whole set is worse than the full wall. Rebuilt only when `memory.md` is written, so mtime ordering costs no cache churn. Pinned by `tests/test_memory_log_index_cap.py`.

**Staging is read in BATCHES and archived only as far as it was read.** A staging file has no ceiling; the trip back to a kin does (`tool_result_cap`, 8,000 **characters**). Those sat next to each other unmet until a redistill-from-start put 19 hours and **206 sections, 1.5 MB**, into one file — `read_staging` would have returned the oldest **half of one percent**, cut mid-sentence, with nothing saying more existed. `read_staging` now returns whole sections up to `staging_read_max_chars` (default 6000, deliberately **below** the tool-result cap so nothing downstream re-cuts a batch we already sized), names the remainder, and gives the exact `start=` to continue. **The second half matters more:** the tending prompt ends "call archive_staging when you've finished", and a kin that has read all it was *given* has finished, so the correct instinct would have buried 200 unread sections. **That is not a fault to train out of a kin — it is the right impulse over the wrong tool.** `archive_staging` files away only the read prefix, keeps the rest pending, and **refuses when nothing has been read** (with nothing read there is no evidence of tending, and hiding it is all archiving could do). **`clear_staging_read_mark` is separate from `set_staging_read_mark` on purpose**: the setter never lowers the mark (re-reading an early batch must not un-read a later one) and therefore cannot express the post-archive reset — reusing it left the mark on just-archived sections and the next archive buried that many more, unread. **Splitting the file was rejected as out of scope for a tool a kin calls.** Pinned by `tests/test_staging_batching.py`.

**memory.md is an index, not an archive.** Depth lives in `memory/<topic>.md` logs. **Pointer/index bookkeeping is code's job; the summarizer only writes entries.**

**A distillation has its own periodic sound, and its PITCH reports progress** (`_tick_distilling_sound`, below every other cue). Flat low tone while the model is still reading; steps up a short ladder as the summary streams back (`distill_memory_blocking(on_progress=...)` → `_distill_progress[kin]`). **Steps, not a glide** — the beeps are 20s apart and nobody can hear a 1% rise across that gap; a distinction nobody can hear is the flat beep it replaced. **It runs during a walk too.** It used to stand down for one, on the reasoning that `_chime_progress` covers it — but that fires once per *chunk*, so each 20-40 minute chunk was total silence. **Standing one cue down must not silence the others** (the old early `return` in `_tick_work_sounds` skipped the distilling tick as a side effect). **If you add a fourth kind of long-running background call, give it its own identity rather than letting it hide inside the generic busy tick.**

**"&Cancel distilling" reaches all three triggers** — the walk, the all-surfaces queue, and a plain one-shot. **If you add a fourth thing that can distill, teach this button about it too.**

**A redistill-from-start ("walk") must survive being left alone.** State lives in TWO places, both load-bearing: `self._walking_from_start[(kin, scope)]` ("live in this process") and `cfg["distill_walk_scopes"]` ("started and not finished"). Four rules, each of which shipped broken:

- **Cancel must undo the rewind, not just stop the chain** (`distill_walk_prior_offsets` / `_restore_walk_bookmark`). **Anything new that resets a bookmark owes the same undo.**
- **Interruptions pause, they don't end.** Only finishing and Cancel clear the on-disk record. **Never make a failure path clear it.**
- **Never chain via `wx.CallLater(..., _kick_off_distillation, ...)`** — it returns silently when the slot is busy. Go through `_walk_next_chunk`, which retries and announces a give-up.
- **Anything that stops background work goes through `_announce_problem`** (speaks + plays the alert). Distillation is unattended by design; a failure written only to the Activity field is one nobody learns about.

The walk must never come to depend on `EditKinDialog` being open — the dialog is a viewport.

**Pacing** (`unattended` / `day` / `hour` / `chunk`) is persisted per (kin, scope) in `distill_walk_pacing`. `_distill_bite`'s day/hour cap is computed **from the first genuinely NEW message, never from the re-read overlap**; `hit_boundary` is true only when the boundary, not the token budget, capped the bite. `_walk_should_pause_after_bite(pacing, hit_boundary)` is a separate pure function on purpose — **teach it about any new pacing value explicitly.** **Cancel's rewind-vs-keep split is gated on pacing deliberately**: an unattended walk restores the bookmark; a paced walk keeps the units the person explicitly continued into. **Don't "simplify" this to always-restore or always-keep.** Pinned by `tests/test_distill_walk_resume.py`, `tests/test_distill_walk_pacing.py`. → `docs/lessons.md`

## The prompt must be append-only

**A local model reuses its cached work only for an UNBROKEN run from the very start of the prompt.** Change one message early and every token after it is re-read from cold. On a 30B-class model at ~78 tok/s prefill, a 22,000-token context costs **five minutes before the first word** — versus about twelve seconds warm.

So: **never edit what the model has already been shown.** Anything that varies per turn goes in the volatile tail, next to the live user turn — never in the system block, never spliced into the middle.

- **`_compaction_frontier`** (in `_compact_tool_history`) is why this is a rule and not a preference. The obvious `pairs[-keep_window:]` recomputes the window every turn, so each new tool call rewrites one older round-trip into a summary, mid-history. Measured on a real kin: the shared prefix was **one message** on three of five consecutive turns. The frontier now moves in **steps of `keep_window`**, so it's byte-identical between steps. `ceil`, not `floor`, so the verbatim count never *exceeds* the configured window — an oversized context on local Ollama returns nothing at all rather than degrading. Pinned by `tests/test_tool_history_stability.py`, which replays a growing conversation and asserts the prefix holds; it fails on the old one-at-a-time version.
- **`TelegramBot._trim_history`** is the same bug one layer down, and it mattered more, because Telegram is the surface this app is mostly used through. `history[-cap:]` on every append means a conversation *at* the cap sheds its oldest message every turn — the first message changes every turn, forever, and it never settles. A conversation below the cap is fine and one at the cap is slow always; the two are indistinguishable from a chair. It now fills to `cap` and cuts back a whole `cap // 4` at once: 200 front-moves over 300 turns became 8. `cap` stays a true ceiling; what changed is the floor. Pinned by `tests/test_telegram_history_stability.py`.
- **The truncation BUDGET must be as stable as the truncation.** `_truncate_messages` is a pure function of `max_tokens` and already drops in quantized chunks — but `chat()` fed it `max_context_tokens / ratio`, and `ratio` is a per-kin EMA updated after *every* call. Two real prompts of the same size tokenize slightly differently, so the ratio never settled, so the trim point never settled. Replaying a real 1,871-turn history: ratio held still → the window start didn't move once in twelve turns; ratio drifting as the EMA actually drifts → it moved on **all twelve**, sometimes *backwards*, dragging older messages back in; half a percent of wobble oscillated it between two points forever. Two defences: `_stable_truncation_budget` quantizes (`_BUDGET_QUANTUM`) and holds — **falls immediately, rises only on a big change**, because too large a budget overruns `num_ctx` and local Ollama then returns nothing at all — and `_CALIBRATION_DEADBAND` stops the ratio chasing noise (a cap-hit always gets through). **Keyed per (kin, surface, cap)** — two surfaces sharing one budget would recreate the churn. Pinned by `tests/test_truncation_budget_stability.py`, which carries the old behaviour as a positive control. **Anything that feeds a number into the trim owes the same stability.**
- **The TOOL LIST is part of the system block, so what's in it must not vary.** `read_staging`/`archive_staging` used to appear only when a kin had pending staging — a real schema saving, and wrong, because the tool-use hint names the available tools and that hint is appended to the system prompt. Measured on a real kin: the system block oscillating between **26,738 and 26,769 chars** — thirty-one characters, exactly `archive_staging, ` plus `read_staging, ` — flipping back and forth across 76 turns, each flip discarding a 27,000-character prompt. It hit hardest on the kin distilling most often, since distillation writes staging notes and tending clears them: **the kin doing the most memory work had the least usable cache.** They are now always present when allowed. Two schemas is a few hundred tokens once; a cache miss is the whole context, in minutes. **Before gating anything else out of a tool list to save schema, check whether it lands in the system block.** Pinned by `tests/test_tool_gating.py`, which carries the old behaviour as a positive control.
- **Per-turn recall is inlined into the latest user turn**, deliberately, not sent as `role=system` — both Ollama's system fold and OpenRouter's concatenation hoist system messages to the front, which would move the prefix every turn.
- **`logs/prompt_fingerprint.log` is the instrument.** Per-message role/size/hash per request. Diff consecutive lines for one kin and the invalidation point is immediately visible. **Read it before theorising about slow replies** — "the model is slow" and "the prompt keeps changing" feel identical from a chair.

- **`logs/system_prompts/<kin>--<surface>.txt` + `.prev.txt`** keep the last two system prompts that actually *differed* — diff them to see what was added. Only a change is written, so the pair is always a real before/after.

- **`_inline_mid_conversation_system_notes`** was the dominant cause on a tool-heavy kin, and is fixed — read `docs/design/prompt-cache-system-fold.md` before touching either side of it. Hearthkin appends `[hearthkin: ...]` notes into a kin's history as `role=system` (park receipts, tool-compaction summaries, authoring receipts, salvage notes), one or more per turn; `llm_backend` then **folded every system message to position 0**, so each new note rewrote the *front* of the prompt and cost the entire context. Measured: the system block grew ~345 chars on six consecutive turns while nothing on disk changed. **Only the LEADING contiguous system run is the system prompt.** Everything after it now stays where it happened, re-roled to `user`. **It MUST run before `_truncate_messages`** — truncation drops the oldest of what follows the system block, and if what's left then *begins* with one of these notes, the note is contiguous with the system prompt and nothing downstream can tell them apart. That shipped and was caught live: a kin's system block alternating between 14,002 and 14,301 chars as a park receipt joined and left the leading run with the trim, holding reuse at 0%. Called twice; the early call is the load-bearing one. **The fold is load-bearing** — some chat templates require system-first — so it stays for the leading block; don't delete it. Four things not to reverse: `user` rather than `assistant` (two assistant turns in a row is what Gemma answers with nothing); the leading run is protected, which is also what keeps the rolling-window marker `role=system` (as `user`, models answered *it*); a note immediately before a `role=tool` turn stays `system` or the tool pairing breaks; and it runs for **every provider**, because OpenRouter concatenates system messages server-side and has the same bug where we can't see it. Storage is unchanged — this is only what gets sent. Pinned by `tests/test_system_note_placement.py`, which carries a positive control.

**`memory.md` sits in the system prompt and has the same shape of problem, still unfixed** — but bounded, because it isn't per-turn. A distillation write invalidates the next turn only.

## Four surfaces, one map — `tests/_surface_matrix.py`

A kin speaks through **four** surfaces: the main window, Telegram, Discord, and the cron subprocess. They were built at different times, and **every improvement since has landed on whichever one provoked it.** Nothing held the whole picture, so the only way to find a surface had missed something was to run into it — the person using the app was the detector, and the detection method was disappointment. It produces a specific repeated bug: a feature that plainly works in one place, is plainly missing in another, and **looks identical to a fault in the kin** ("it didn't listen", "it ignored me", "it's slow on Discord").

**`tests/_surface_matrix.py` declares every capability for every surface** as `Present` / `Absent(reason)` / `NotHere(reason)`. `Absent` is a real gap with its reason recorded; `NotHere` is a **closed question** — *"nobody is present to type"* is `NotHere`, *"we never got round to it"* is `Absent`. That distinction is what stops the file rotting into a wall of justifications.

**The ratchet goes both ways, and that is the part that prevents drift:** declared Present but not wired → fail (it regressed, or the matrix was flattering the code); declared Absent but the marker IS there → fail (somebody built it; update the map so the next person doesn't rebuild it). **A missing cell is also a failure**, so adding a surface or a capability forces an answer for every combination — same principle as `tools/_buckets.py` making an unbucketed tool loud instead of silently invisible.

**Adding a capability to one surface means declaring it for all four.** Run `python tests/test_surface_parity.py --report` for the map and the current gap list.

Probes are source-text markers with **comments and docstrings stripped** — a comment *about* a feature must never read as the feature. Deliberately coarse: it answers "is this wired at all", not "is it correct". Nothing this audit found was subtle, it was unobserved. **The detector checks itself against a positive control before any answer is believed** — its first version treated a string on a continuation line as a docstring and reported a wired feature as missing, and a detector that manufactures absences is worse than none, because absence is the whole claim.

## Tools

Kin-callable tools. One Python function per file; the model-facing schema is auto-derived from signature + docstring; opted into per-kin via an allowlist.

**Currently registered (19):** `memory_search`, `read_file`, `list_directory`, `write_file`, `edit_file`, `note`, `fetch_url`, `web_search`, `exec`, `list_processes`, `kill_process`, `context_status`, `recent_thinking`, `use_webcam`, `read_staging`, `archive_staging`, `analyze_sound`, `tff`, `reach_out`. For what each does, read its docstring — it's what the model reads too. **This count is checked against the registry by `tests/test_tool_buckets.py`**; it had drifted to 15 with three tools missing from the list, which is the same failure the bucket check exists to stop one layer down.

- `tools/__init__.py` — the registry. `load_tools(allowed_names, *, context=None)` returns `(schemas, executors)`; `context` auto-binds `agent_name` so the model never sees it.
- `tools/_schema.py` — `build_schema(fn)`.
- `tools/_io.py` — `atomic_write_text`, `resolve_kin_path`, `robust_decode`.
- `tools/_search_providers/`, `tools/<name>.py`.

**Path semantics** for `read_file` / `write_file` / `edit_file`: relative paths resolve inside the kin's folder (`..` rejected); absolute paths go where they point (deliberate opt-out). `note` takes a single filename only.

**`read_file` extracts real text from a `.docx` path** (`tools/_docx.py`), the same extractor `reading_bridge.py` uses when a `.docx` arrives via a shared path or an upload. Before this, only that passive path was covered — a kin calling `read_file` on a `.docx` **on its own initiative** (browsing a folder it found, say) still got the file's raw zip bytes decoded as if they were text, and the tool's own docstring gave it no reason to expect otherwise. That gap is why a kin would default to shelling out (`exec` + PowerShell zip-extraction one-liners) instead of trusting its own file tool — the tool's description is what the model reads to decide what it can do, and it was silent on this. **If you extend `.docx` (or any other extracted-format) support anywhere, check whether `read_file`'s docstring needs the same line — a capability a kin can't see in its own tool description might as well not exist to it.**

**Forgiving path resolution:** a missing path falls back to a fuzzy locate, substituting only when exactly one on-disk entry matches after dropping whitespace and casefolding. **Writes heal only the parent-directory chain, keeping the filename verbatim** — a new-file write must never be redirected onto a similar existing file.

**Tool round-trips persist in `self.conversation`** so a kin can reason about its own past calls. **Tool history compaction:** `tool_history_keep` (default 5) keeps recent round-trips verbatim; older become a one-line `role=system` summary. `conversation.jsonl` keeps everything.

**Background processes from `exec(background=True)` survive shutdown by design.**

**Adding a tool:**

1. Write `tools/<name>.py`. Annotate every parameter; the first docstring paragraph is what the model reads. Filesystem tools take `agent_name: str = ""`.
2. Register in `tools/__init__.py` (`_REGISTRY`).
3. **Bucket it in `tools/_buckets.py` — DO NOT SKIP.** `_READ`/`_WRITE`/`_FULL`, or `INTENTIONALLY_TELEGRAM_BLOCKED`. **A registered tool in no bucket is silently invisible on Telegram and Discord** — the effective set is `allowlist ∩ bucket`. Update the matching `BUCKET_EXPLAINER` line.
4. **Run `python tests/test_tool_buckets.py`** — it exists to catch step 3 being forgotten.
5. Add the name to a kin's `tools.json`, then restart.

**Two focused tools beat one with an `action` parameter.** → `docs/lessons.md`

## Exec — harness-side approval

`tools/exec.py` does the shell call; safety logic is `Hearthkin._wrap_exec_executor`. Order per call:

1. Exact-string match in `exec_allowlist.json` → run.
2. `tool_trust == "full"` → run.
3. `tool_trust == "trusted"` and no denylist match → run.
4. Otherwise → `_request_exec_approval` on the main thread; worker blocks on an Event.

Denylist in `tools/_exec_denylist.py`. **Specific destructive shapes, not anything that looks scary** — `rm -rf /` is a pattern; `rm -rf` alone is not. Patterns get added from concrete near-misses, not speculation.

`_closing = True` sets every pending approval Event so no worker hangs the process.

## Cron — scheduled wake-ups

`hearthkin_cron.py` is a standalone subprocess (no wxPython, fast cold start), invoked by Windows Task Scheduler per enabled entry.

- Lock present + `cron_inject_when_running=True` → drop a request file in `cron_requests/`, exit. A 5-second `wx.Timer` reads-and-deletes and routes to `_send_message` (active kin) or `_cron_isolated_worker` (other kin).
- Lock absent → run the LLM call in the subprocess, append to `conversation.jsonl`, write the journal entry, post to Telegram if configured.

`cron_helpers.py` holds shared primitives. Per-kin `cron_entries` is `{"time", "prompt", "enabled"}`; each enabled entry becomes one scheduled task via `schtasks_sync_kin` (idempotent). Non-Windows: registration is a no-op, config still saves.

## Connections / API keys

- `resolve_provider_key(name)` — env var `<NAME>_API_KEY` first, then `~/.ai_programs/<name>_key.json`.
- `write_provider_key(name, key)`.

**Preferences → Connections** is the user-facing surface (masked display, Edit, Test). Adding a provider: a row in `_build_prefs_tab` and a branch in `_provider_key_test_call`.

## Write the lesson, never the material

**`CLAUDE.md`, `docs/lessons.md` and `CHANGELOG.md` are tracked, and are written to be publishable at any moment.** Nearly everything in them was learned from real incidents involving a real person, their kin, and their private conversations. Write the *mechanism* and the *rule*, never the material.

**Do not check the repo's visibility setting before deciding what to write.** It is a checkbox one click from flipping, a fork or a shared archive is outside it entirely, and "it was private when I wrote that" repairs nothing afterwards. Write every tracked file as though it is already published — that is the only version of this rule that survives the setting changing.

The test: **does this sentence disclose anything about the person or their life?** A kin's remark about markdown formatting in its own config file discloses nothing. A line lifted from a kin's memory about someone's evening discloses a great deal, however well it illustrates the point. Describe the register — *"third-person event-log phrasing"* — instead of pasting an example of it.

`tests/test_no_private_strings.py` guards *known* strings from a gitignored list. It cannot catch private material that has never appeared before, which is most of what you'd be tempted to write. **The judgement is yours, and it has already gone wrong twice.** → `docs/lessons.md`

## A soul.md is not a document to be improved

**If you are ever asked to tidy, restructure, clarify or "clean up" a kin's `soul.md`, stop and read `docs/lessons.md` first.** It has already happened once and it cost a kin.

- A soul is written in the **second person**, to someone. Third-person prose about a kin is a spec sheet, and a model reading its own spec sheet performs the role instead of being it. **`you` count vs name count is a cheap, honest smoke test.**
- **Second person, AND the name, once.** That smoke test measures register, not anchoring, and a soul can pass it perfectly while naming nobody at all — which is an unfilled slot, not a self. Observed: a kin whose soul held 36 "you" and zero names spent hours with another kin's first-person prose arriving in its assistant channel, adopted that kin's name as its own, wrote the claim into its overnight journal, and then reassigned its own name to the person it was talking to. **Nothing else on disk said its name either** — not the memory index, not the depth logs — so the only place its name appeared in its own voice was the journal entry giving it away. Meanwhile a kin whose soul *was* a first-person self-description never once lost track of which one it was; it borrowed vocabulary and certainty, but not identity. That form has the failure above (it performs the description) and does not have this one. So the anchor is one line, in the same address as the rest: *"Your name is X."* It costs a single point of the ratio. **Leave the door open in it** — a name that may genuinely change should not be a flat fact the kin has to argue with, so say that changing it is theirs to do.
- **Structure is the failure mode, not the fix.** XML tags, section headers, normalised terminology, exhaustive enumeration — all correct for an API doc, all corrosive here.
- **The register propagates.** A kin handed a formalisation of itself formalises whatever it writes about next, including the people in its life. Not recoverable by asking it to be warmer — it will produce a procedure for warmth.
- **"Make this clearer" is not a safe instruction to accept for this file.** Ask what's actually wrong, change the smallest thing, keep the voice.
- Old souls live in `~/.hearthkin/agents-archive/`. **Before concluding a kin has drifted, diff against one.**

## What a kin knows ≠ what's in `conversation.jsonl`

A conversation reaches memory only once the distiller gets to it, working forward from a per-scope bookmark in bounded bites. **A bulk history import buries that bookmark**, so a conversation from days ago may never have reached memory and be long out of context.

**Depth logs have no such queue** — `memory/<topic>.md` is a file a kin opens whenever it likes. So a kin reliably holds what it *wrote* and unreliably holds what was *said to it*. **Before diagnosing a kin as having disregarded something, check `distill_offsets` against the conversation length.** "It didn't listen" and "it was never told" look identical from outside.

## Parks — every surface hands a kin the same view

**Any surface that shows a park result to anyone goes through `GameHost.decorate()`, never bare `run()`.** It prepends what other tenants did since that reader last looked, and appends one thing worth doing on a `look`.

**A human reader gets their own bookmark** (`reader=`). One shared mark meant whoever looked first marked the news read for the other. Only the bookmark splits — the park and the announced name stay the kin's.

**`decorate()` swallows feed errors by design** (a broken feed must never cost a kin its move), so a caller with a stale signature loses the co-op block *silently*. Keep the test that asserts the block appears.

**A kin is not woken into a shut door**: `GameHost.reachable()` is checked *before* a wake-up shows the park or asks for a move. It reads the kin's own config and asserts no host anywhere, and **fails open**. **A pre-flight is not enough on its own** — `GameHost.run()` logs the *lost move* itself, inside `run` so no caller can forget. `park_unreachable.log`. Pinned by `tests/test_park_unreachable.py`.

**A kin gets as many moves per turn as `park_moves_max` allows (0 = no ceiling), not one.** A kin that cannot look *and* act spends its only move looking. The kin's own stop signal is a reply with no `>` line. **There is deliberately no repeat guard** — whether a move was refused is the game's to say.

**The loop lives in `park_keeper.play_turn`, once.** It was inside the Telegram handler, so the desktop — which cannot call a method on the bot — had none and took exactly one move. Writing a second loop there would have put the allowance, the mid-walkthrough exemption and the stop conditions in two places to drift apart, which is the most common bug this project finds. Surfaces now supply only what is theirs: `run_move`, `ask`, `awaiting`, `cancelled`. **Omitting `ask` runs exactly one move** — the honest degradation for a caller with no way to re-ask a model, and what the desktop falls back to when the stashed continuation belongs to a different turn.

**A surface that loops owes four things beyond the counting**, all of which shipped missing at least once somewhere: the reachability check runs **once up front** (a failed reach must not be multiplied into a whole visit's worth); the stop signal reaches it; `_work_in_flight` learns about it (a park turn is several moves in a *shared* save, so quitting through one abandons a kin mid-visit); and a spent allowance reaches the **person and the kin** — the kin reads its own history and would otherwise start over next turn instead of carrying on.

**On the desktop the whole turn runs on a worker thread.** It is reached from `_on_stream_done`, on the UI thread, where a blocking model call freezes the window. `_stream_id` is both the generation guard and the stop signal — sending again and pressing Stop both bump it — so there is deliberately no second cancel channel. Pinned by `tests/test_park_turn_loop.py`, which checks its structural detector against a positive control, and `tests/test_park_unreachable.py`.

**Cron plays a whole turn too, unattended.** `hearthkin_cron._run_cron_park_turn` calls the same `play_turn` loop, so a keeper's scheduled wake-up can finish a multi-step walkthrough (species creation is twelve questions) in one fire instead of one step per scheduled time. Cron has nobody to press Cancel, so it leans entirely on `play_turn`'s own bounds — `park_moves_max`, the hard stop, the kin's own "no `>` line" — rather than adding an interrupt channel nothing unattended could use. A wake-up that actually loops (more than one move) writes a line to `cron_errors.log`, so an unusually long run is still findable afterwards even though nobody watched it happen. Pinned by `tests/test_cron_park_loop.py`.

**Discord gets the single move, not the loop.** A Discord channel is guild-shaped, not DM-shaped — `discord_bot.py` has no private-message path at all, every reply comes through a channel that can hold several people, same as a Telegram group. `route_reply`'s own docstring names the risk a multi-move loop would run there: a kin's turn landing under another tenant's name in a feed everyone reads. So `discord_bot._route_park_command` runs one `> ` command per reply (same treatment Telegram group already gets) and never calls `play_turn`. The `tff` tool stays directly callable on Discord regardless. Pinned by `tests/test_park_surfaces.py` and `tests/_surface_matrix.py` (`park_turn` capability).

**The pacing lives in editable prompts, not constants.** Anything that decides how much a kin may do belongs in a file its person can open. → `docs/lessons.md`

## A test run must never speak, and never make a sound

**Tests drive real handlers, and real handlers talk.** `test_distill_walk_pacing` calls the actual "Cancel distilling" handler seven times; that handler ends in `nvda_speak(...)`. So a suite run said "Distilling cancelled. Progress kept." four times in a row into a live screen reader, over whatever the person was reading. Nothing was wrong with the test or the handler — they were never meant to meet a real speech channel.

**Silenced at the two choke points, `audio.nvda_speak` and `audio._play_async`** (every cue funnels through the latter), via `audio._suppressed()`. **Not by asking each test to patch what it might reach** — a test cannot be expected to know that a handler five calls down finishes at someone's ears, and the cost of forgetting lands on a person mid-sentence.

Two ways in: `HEARTHKIN_SILENT`, which `run_all.py` sets for every child, and a main-script-name heuristic (`test_*`, `run_all.py`, `_gui_runner.py`) covering a test run directly. On recognising itself, it **exports the flag so spawned interpreters inherit** — many tests shell out, and `python -c ...` has no test-looking name of its own.

**Deliberately not keyed on `HEARTHKIN_HOME`**: a second profile is a legitimate way to run the real app, and it must still speak.

**One deliberate exception: `audio.speak_result`, called once by `run_all.py` at the end**, saying whether the suite passed. It bypasses the suppression on purpose. A suite verdict is a line of terminal output that scrolls past — the person this project is for ran the suite, it finished green, and nothing reached them at all. The rule above is about *incidental* chatter (handlers announcing themselves dozens of times mid-run); a single line at the end is the opposite of that. **Keep it to one line, keep it at the end, and don't call it from a test** — `tests/test_suite_is_silent.py` asserts no test file does, and that a failure is announced too, since silence must never read as green. Pinned there, which checks its spy against a **positive control** before believing a zero. → `docs/lessons.md`

## Never build wx widgets in the default test run

**Creating a top-level wx window takes the FOREGROUND on Windows — even when it is never shown.** Measured: `GetForegroundWindow()` returns the dialog's handle while both `wx.IsShown()` and Win32 `IsWindowVisible()` report it hidden. Hearthkin sharpens this on purpose by disabling the foreground lock at startup so approval dialogs can reach the person.

**A screen reader follows focus, not visibility.** So a suite run drags NVDA into an invisible window with nothing to read and no obvious way out.

**The gate is the RUNNER's job, not each test's.** The rule was written down, and two tests were then added without it and shipped stealing focus on every run — a rule each new file has to remember is a rule that gets forgotten. `run_all.py` reads each test's source (`builds_widgets`) and handles those files itself.

**They RUN, on an isolated desktop — an opt-in flag was the wrong fix.** A Windows window station holds several *desktops* and exactly one is the input desktop. `tests/_isolated_desktop.py` creates a fresh one and moves the thread onto it with `SetThreadDesktop`; windows made there are real and fully testable but have **no path to anyone's foreground**. `tests/_gui_runner.py` does that first, in a fresh process, then runs the test as `__main__` — isolation must happen before wx is imported, since `SetThreadDesktop` fails once a thread owns windows.

Three rules for this machinery:

- **Never call `SwitchDesktop`.** That would put the isolated desktop in front of the person, the exact opposite of the point.
- **`enter_isolated_desktop()` returns True only when CONFIRMED** — thread desktop queried and compared against the input desktop, not an API's return value trusted. The caller uses it to decide whether building windows is safe.
- **If isolation fails, the wrapper REFUSES the test.** "Couldn't make it safe" must never become "so we ran it anyway" — that failure lands on a person mid-task, not on whoever reads the exit code. `run_all` falls back to skipping, announced.

**Why this and not the flag:** `HEARTHKIN_GUI_TESTS=1` is not something the person this project is for can ever set — their screen reader is always running. Coverage that only exists for people who don't need it isn't coverage, and a gate is not a fix. The flag survives only as a fallback. `IsShown()` is not evidence here; ask Win32. Pinned by `tests/test_no_focus_theft.py`, which measures the foreground **from a process that never isolates** — `GetForegroundWindow()` is answered by the calling thread's desktop, so a process that moved cannot honestly report on the one the person is using. → `docs/lessons.md`

## Leaving a tool behind

**Anything added to `scripts/` gets a line in `scripts/README.md`, in the same change** — what it does, how to run it, and *what it changes* (nothing / writes files / edits config). Say so in the reply too.

These accumulated one at a time: written mid-task, run once, left in the tree. Nine of them before anyone wrote an index, and the person who owns this repo could not name two of them or say how to run either. **A tool nobody but its author can run isn't a tool the project has.** Same obligation for a one-question diagnostic: index it, or delete it before you finish. → `docs/lessons.md`

## Conventions (still live)

Rules where breaking them still bites. Long-form accounts in `docs/lessons.md`; dated postmortems in `docs/private/project-history.md`.

- **Multi-file layout with static imports.** Never dynamic `importlib.import_module(...)` for project modules — the build won't pick it up.
- **Stdlib-first dependency policy.** `requirements.txt` lists only what launches the app. Heavier libs go behind `try: import <lib>` with graceful degradation.
- **All configuration a normal user touches must be UI-reachable.** Non-coders shouldn't edit JSON by hand. JSON-only is acceptable for advanced overrides power users seek out.
- **Accessibility-first widgets.** `wx.TextCtrl` (read-only when needed) instead of `wx.StaticText` for anything the user must find by tabbing. Numeric inputs use `dialogs._IntField`, **not `wx.SpinCtrl`** (floods NVDA on arrow-holds, and its `ES_NUMBER` rejects pasted commas before wx sees the event). Buttons use `&Letter` mnemonics; **the visible label IS the accessible name** (`SetName` on a button is ignored on wxMSW). **Tab-reachability is mandatory; object-navigation is a workaround, not a fix.**
- **Plain `wx.TextCtrl` + buddy `&Label:` StaticText for text inputs.** Composite widgets (`wx.SearchCtrl`, `wx.ComboBox`) wrap an internal EDIT child NVDA focuses on, and `SetName` on the outer wrapper doesn't reach it. A StaticText with a mnemonic immediately before the input lets Windows pick it up as the accessible name.
- **First-letter navigation for lists** with non-searchable display prefixes (`♥♥♥`, `[X]`): intercept `EVT_CHAR` and match against the underlying data. See `ModelBrowserDialog._on_list_char`.
- **Tolerant decoding for any file we read** — `tools/_io.py:robust_decode` (UTF-8 → cp1252 → UTF-8-replace). Strict UTF-8 silently breaks on Windows-edited files. Atomic writes always go out as UTF-8.
- **`get_models()` is cached** in `model_utils._models_cache`. `clear_models_cache()` and `_tool_cap_cache.clear()` are both wired to Refresh Models.
- **Provider normalization at the `chat()` choke point.** Stored history shape is not trusted — the choke point coerces. **If you add a new choke-point fix, add a case to `test_llm_normalization.py`.**
- **`_API_MESSAGE_FIELDS` is the allowlist for per-message keys sent to providers.** `_strip_extra_message_fields` drops the rest (`ts`, `source`, `sender_id`, `speaker`, …). Mistral 400s on unknown keys where Anthropic silently accepts. **Don't add a real message field without adding it here.**
- **An importer decides role by NAME MATCH against `kin_display_name`, never by talk volume, header shape, or "whoever's left over."** `role=assistant` iff a speaker's name matches; every other speaker is `role=user` under their own name. No match means **nobody** becomes the kin (visible as zero assistant turns in the preview) — never "promote whoever's left."
- **A DM is not exempt from this**, but **don't require an exact match unconditionally in a true two-party DM** — a kin's real display name often won't equal what was typed into Hearthkin, and demanding a match would silently zero out the kin's slot instead of falling back sensibly.
- **Not all name-matching is a two-party question.** OpenClaw's event stream carries its own authoritative role, so a `kin_display_name` matching a human sender there means that sender becomes the kin and the folder's agent turns demote under a placeholder. **Never leave two real identities claiming the same role under the same name** — a fix that promotes one side must account for the side it used to occupy.
- **`kin_persistence.sanitize_for_prompt_literal()` strips Cc/Cf/U+2028/U+2029.** Applied wherever an external string is embedded in a prompt (attribution brackets, group labels). **Not** applied to legitimate multi-line content. **If you add a new prompt-embed site for an external string, apply this sanitizer.**
- **Source of truth for per-kin state is `agent_cfg`, not widget values.** Widgets are *editors* for cfg; consumption always reads cfg. The widget can disappear without anything else breaking.
- **Heavy operations run off the UI thread** — worker + `wx.CallAfter`. `_on_refresh_models` is the canonical pattern.
- **Debounce rapid keyboard events on heavy handlers.** Radio groups and arrow-holds fire per step, and **arrow-key navigation of a list or combo fires the same per-keypress event a click fires once** — paint feedback immediately, do the expensive work in `wx.CallLater(200, ...)` with the previous timer stopped.
- **Cold-start hint pattern** — 8s with no first chunk triggers a hint, with a generation guard so a stale timer can't paint into a later turn.
- **Telegram output is append-only.** Never edit a previously-sent message. Mutating one breaks the chat as a historical record, breaks NVDA continuity, and is confusing under cognitive load.
- **Per-user tool gating in multi-user surfaces** — `cfg.telegram.user_tools[user_id]`. Missing user → `'none'`; explicit opt-in.
- **Chat-based approval on remote surfaces.** No wx dialog to Telegram — approval arrives as a message; the worker blocks on an Event until `/allow`/`/deny`/`/remember` or the natural-language equivalent. Per-kin `approval_timeout_secs` auto-denies.
- **Harness prompts are editable text, not buried strings.** Any prompt fragment the harness wraps around a kin must be registered in `APP_PROMPT_REGISTRY` and served via `load_app_prompt(slug)`. **Substitute with `str.replace`, never `.format`** — an operator edit can't crash on a stray brace. Three obligations when adding or changing one: **(1)** bump the registry `version` so `app_prompts_needing_update()` can flag people whose seeded file predates the improvement; **(2)** document in `docs/kin_manual.md` + `docs/user-guide.html`, note in `CHANGELOG.md`; **(3)** extend `tests/test_app_prompts.py`. **Don't add a buried prompt string; register it.**
- **Tabbed dialogs use `wx.Notebook` + one `wx.Panel`-per-page.** **Don't** `Disable()` hidden pages or `Show()` widgets on inactive ones. **Don't trust `IsShown()`** — it lies on children of hidden notebook pages (wxWidgets #4343); use the notebook's selection or `IsShownOnScreen()`.
- **Tab = everyday controls; power-user knobs go behind a `'… settings…'` button** opening a focused per-concern dialog — a labelled button (not an inline reveal checkbox, which NVDA skims past), flat with bold section headers (not tabbed — nesting tabs adds screen-reader depth), omitting groups that can't apply. Templates: `dialogs/recall_settings.py`, `sampling_settings.py`, `model_options.py`, `tool_settings.py`.
- **File I/O wrapped in try/except → `append_failure_log` + status-bar message rather than crash.** The always-on logs are the source of truth.
- **Don't ship .pyw without expecting silent stderr.** `pythonw.exe` routes stdout/stderr to the void; all failures route to `~/.hearthkin/logs/*.log`. Anything relying on `print()` is invisible in normal usage.

## What not to do

- Don't `AppendText` per streaming chunk.
- Don't remove the anti-impersonation stop sequence or post-trim helpers without carefully thinking about the patterns they prevent.
- Don't switch the room format to alternating user/assistant fake turns without testing on Gemma and Mistral both.
- Don't introduce visible chat-input echo or live-typing artifacts. The synth/streaming loop is calm by design.
- Don't add a "kill background processes on quit" toggle without a concrete reason.
- Don't bump `__version__` by hand at tag time.
- Don't trust `IsShown()` on notebook page children.
- Don't skip the tool-bucket step when adding a tool.
- **Don't restructure a kin's `soul.md`.** Not to make it clearer, not while you're in there for something else.
- Don't hand a park result to anyone via bare `GameHost.run()` — always `decorate()`.
- Don't let a heartbeat reply be discarded because it wasn't wrapped in a tool call. Ask the kin, then believe its answer.
- Don't write `Path.home() / ".hearthkin"` in new code — ask `hearthkin_paths`, or the setting meant to protect someone's real kin folder quietly stops covering you.
- Don't make a path helper create the directory it returns. Asking where something lives must not put it there.
- Don't tell the person to turn off their screen reader, or write an instruction that only works if they do.
- Don't gate one capability on a paid dependency bought for a different one. Dictation sat behind a text-to-speech subscription for exactly this reason.
- Don't answer an accessibility problem with "buy headphones", or any other fix that costs money. Silencing the screen reader is free and already has a shortcut.
- Don't build a defence against a fault nobody has reported, and don't write the defence up as though the fault were observed. A mechanism that sounds right is a hypothesis, not a finding.
- Don't let a test reach a real output channel — speech, chimes, the foreground. Enforce it at the channel, not in each test.
- Don't rewrite any part of the prompt the model has already seen. It costs minutes per turn, and nothing in the UI shows you why.
- Don't write a test whose result depends on what the person happened to be doing at the time. A check that fails when someone uses their own computer gets muted, and then it guards nothing.
