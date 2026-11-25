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

Python + wxPython desktop app for multi-kin local-LLM chat. Talks to Ollama by default (models run locally; no remote API calls) and can route through a hosted API provider when a kin's model carries that provider's prefix (`openrouter/...`, or any provider added in `providers.md`). A "kin" is a configured persona with a soul prompt, distilled memory, and a model. Two interaction modes: 1-on-1 chat, and "rooms" where several kin take turns with the user.

## Who you're working with

The person who owns this repo and uses this app every day is **blind, autistic, and not a programmer.** This shapes the work more than any technical rule here.

- **A screen reader (NVDA) is always on.** Not sometimes, not "when testing accessibility" — always. Any instruction that begins "when you aren't relying on the screen reader" is a non-instruction. Anything that steals focus, floods the announcement queue, or only makes sense visually is a defect, not a rough edge.
- **Explain in plain language.** Say what *changed for the person using the app*, not what changed in the code. No diffs, no jargon, no "just refactored the dispatch layer". If a term is unavoidable, define it in the same sentence.
- **Do the work; don't hand back a checklist.** A long list of steps to perform is a failure of the reply, not a thorough one. Do it, then say what you did and how to check it. A 17-item to-do list was the exact thing that went wrong once.
- **Verification is by running the app or the tests, not by reading code.** So a change isn't finished until there's a way to confirm it that doesn't involve reading a diff — a test, or a short "do this, expect that".
- **Short, calm sentences.** One idea at a time. Don't stack three questions into one paragraph. If you must ask something, ask one thing.
- **Notice what's actually being said.** "It keeps doing X" is a report from someone who lives in this app all day. It is almost always accurate, and usually more accurate than a theory about why it can't be happening.
- **Every message must be true in the situation it's shown in.** Before any text the person sees (a notice, a reply, a status line, a help line), trace the code path: is each claim true *there*? Show it only where it applies; claim only what Hearthkin actually knows (never where a model runs, why a provider failed, that a save worked when it wasn't checked). If one part is unknowable, say "if", once, about that part. A heads-up that fires in the wrong situation is worse than none, but the fix is to gate it, not delete it. The owner, 24 Nov 2026: "context-aware messaging… for everything." Example: `frame/rooms_mixin._shared_ollama_note`, `tests/test_room_shared_ollama_note.py`.

This file is written to be publishable (see below). This section is here at the owner's explicit request.

**Where to look for what:**

- **This file (`CLAUDE.md`)** — the rules, stated short: each rule, the test that pins it, and a pointer to its story. Loaded every session.
- **`docs/lessons.md`** — the stories behind the rules: measurements, incidents, what shipped broken and how it was found. Headings match this file's; a "→ lessons.md, '…'" pointer names the section. **Read the matching section before changing or removing a rule.** A new lesson: the rule goes here, the story goes there.
- **`docs/architecture.md`** — module map, the `chat()` normalization pipeline, "where do I change X".
- **`docs/troubleshooting.md`** — diagnostic playbook and the cross-provider quirk catalog. Read this before theorizing about an OpenRouter 400.
- **`docs/private/project-history.md`** — untracked local archive. Old session logs, incident postmortems.
- **`CHANGELOG.md`** and `git log` — release-by-release facts.
- **`docs/devlog.md`** — what changed, newest first, in plain words for the maintainer, who uses the app and doesn't read code. **Every change gets a line there the same day it's made**: who, what it does for the person and the kin, and why. The maintainer asked for this (24 Nov 2026): "I'm just a regular old user of Hearthkin, and I certainly can't account for what you do in there."

## Layout

Entry point is `python hearthkin.pyw`. The frame is split across `frame_shared.py` and a `frame/` package of 17 mixins that `Hearthkin` inherits from.

- `hearthkin.pyw` — assembler: imports, `class Hearthkin(*17 mixins, wx.Frame)`, `__init__`, `main()`.
- `frame_shared.py` — shared namespace hub. **Module-level constants/helpers the frame needs go here.**
- `frame/` — one mixin per behavioral slice. **To add a frame method, put it in the matching mixin.** A test that monkeypatches a frame name patches the mixin module (`frame.memory_mixin`), not `hearthkin`.
- `hearthkin_paths.py` — the one place that decides where runtime state lives (`config_dir` / `kin_dir` / `logs_dir`). Imports nothing from the project, so everything can sit on it.
- `kin_persistence.py` — paths, defaults, atomic-write helpers, load/save for kin and rooms. Pure data layer, no LLM calls, no UI.
- `dialogs/` — every `wx.Dialog` subclass, one per file. Big one: `dialogs/edit_kin.py` (nine-tab kin Settings: Identity, Model & generation, Memory, Tools, Telegram, Discord, Cron, Voice, Prompts).
- `telegram_bot.py` / `discord_bot.py` — the remote surfaces.
- `audio.py` — NVDA speech (`nvda_speak`) and reply chimes.
- `stt.py` — speech to text. Three backends (local Whisper, a Whisper server, ElevenLabs Scribe) as plain functions over WAV bytes, so none of them needs a microphone to test. `voice.py` owns the microphone and the text-to-speech.
- `model_utils.py` — Ollama model-name parsing, capability detection, dropdown listing.
- `chat_helpers.py` — streaming chunk extractors, sentence boundaries, token estimation, room-reply cleanup, `detect_tool_roleplay`.
- `llm_backend.py` — the single dispatch layer. `chat(model, messages, ...)` routes on any registered provider prefix — `api_providers()` merges the built-ins with `~/.hearthkin/providers.md`. Ask "is this hosted?" with `is_hosted_model(model)`; `provider_for_model(model) == "openrouter"` is only for OpenRouter's own catalogue and prices. Streaming, prompt caching, reasoning-toggle, `run_tool_loop`.
- `compat.py` — pre-flight model-swap checks. Provider quirks live as data in `ModelProfile`, not scattered if-blocks.
- `importers/` — history-import backends behind `dialogs/import_history.py`.
- `model_browser.py` — NVDA-accessible model picker.
- `tools/` — kin-callable tools registry.
- `Hearthkin.spec` + `build.bat` — PyInstaller onedir.
- `tests/` — plain-Python tests, no pytest. `python tests/run_all.py` runs all but the window tests; `--gui` adds those (tell the person first: they can freeze NVDA).

**Import shape:** every cross-module reference is a static `from <mod> import ...`. PyInstaller follows those. **Never** dynamic `importlib.import_module(...)` for project modules. Circular imports are avoided by lazy-importing inside method bodies.

**Build pipeline:** `__version__` lives in `app_version.py`. The build runs `scripts/stamp_version.py` before PyInstaller to rewrite it from the git tag. **Don't bump `__version__` by hand at tag time.**

## Runtime state — `~/.hearthkin/`

Migrated automatically from older `~/.ollama_chat/` if present. Stories for everything here: → lessons.md, "Runtime state".

- **`hearthkin_paths.py` decides where this tree is, and every site asks it.** `HEARTHKIN_HOME` relocates it. **Don't write `Path.home() / ".hearthkin"` in new code.** `kin_dir()` deliberately does not create the folder it names: asking where something lives must not put it there.
- **The test runner writes only to a directory it just created, and REFUSES an inherited `HEARTHKIN_HOME`** (announced, not silent). `--keep` preserves and prints the sandbox. **The legacy `~/.ollama_chat` migration is skipped under the override.** Redirect a test with `HEARTHKIN_HOME`, never by patching `pathlib.Path.home`. Pinned by `tests/test_state_isolation.py`.
- **Hand-editing a kin's `config.json` while Hearthkin is open: saves now merge, but the open app may not USE the edit yet** (the owner caught saves overwriting hand edits, 23 Nov 2026). `save_agent_config` merges (`_merge3`). Wake-ups and heartbeats read the disk fresh; the desktop chat and the bots may keep the old value until the kin is reloaded or Hearthkin restarts. Prefer the app's own window; check `tasklist` for `pythonw.exe` and say which applies. **Never hand-edit `tools.json` while the Tools window is open** (no merge). Pinned by `tests/test_config_save_merge.py`.

Tree:

- `kin/<KinName>/`: `soul.md` (persona prompt); `memory.md` (kin-curated index — **only the kin (via file tools) or the person (via Settings → Memory editor) writes this; nothing automatic touches it**); `memory/<topic>.md` (kin-written depth logs, where substance lives; the `## Memory logs` index is built by code, `apply_memory_log_index`); `staging/<scope>.md` (pending summarizer notes per surface); `config.json`, `conversation.jsonl` (append-only), `tools.json`, `exec_allowlist.json`; `memory/journal/YYYY-MM-DD.md` (daily cron wake-up entries).
- `rooms/<roomname>/`, `cron_requests/`, `.running.lock`
- `base_prompt.md` — prepended to every kin's `soul.md`. `prompts/` — editable harness prompts, seeded from `APP_PROMPT_REGISTRY`; file wins after first access.
- `logs/` — session logs (opt-in), plus **always-on** ones. **The complete list is `kin_persistence.ALWAYS_ON_LOGS`** (trimmed at launch; `tests/test_log_trimming.py` fails when a log is written but not listed). Know these first: `empty_replies.log`, `cron_errors.log`, `distill_errors.log`, `hang_watchdog.log`, `park_unreachable.log`, `telegram_failures.log`, `save_failures.log`, and:
  - `openrouter_errors.log` — the upstream provider's real error body. **Check this BEFORE theorizing about an OpenRouter 400.**
  - `recall.log` — one line each time per-turn recall actually attaches something. **Written only when it fires: if a kin is behaving oddly and nothing is here, recall is not the cause.**
  - `distill_triggers.log` — one line per distillation, naming WHICH of the four triggers fired and how far behind the scope was. **Read it before theorising about a kin that "keeps distilling".**
  - `heartbeat_unsent.log` — heartbeat words that reached no one: with the text when nobody asked the kin, WITHOUT it when the kin was asked and declined.

## Critical NVDA gotcha — never AppendText per streaming chunk

Each `wx.TextCtrl.AppendText` fires a TextChange event; dozens per second corrupt NVDA's event queue system-wide. **Always** buffer chunks into `self._stream_buf` and paint the whole reply once at turn-end. Status bar says "Typing..." (not "Thinking..."). Every `_on_*_chunk` follows this; don't regress. → lessons.md, "Critical NVDA gotcha"

## Dictation — and what a paid dependency is allowed to gate

Every rule here has its reasoning and measurements in → lessons.md, "Dictation — and what a paid dependency is allowed to gate".

- **Dictation is free, offline, and needs no account.** `voice.py` owns the microphone and text-to-speech; `stt.py` transcribes as plain functions over WAV bytes, so tests need no sound card, model download or network.
- **A transcription model is a MODEL plus a MACHINE** (empty machine = this computer). `stt.route_for(model, host)` is the one reader of the pair, used by both settings screen and engine. `elevenlabs/…` names its own provider and **beats a host**.
- **Both endpoint shapes are tried** (`/v1/audio/transcriptions`, whisper.cpp's `/inference`) via `candidate_endpoints`; winner remembered per host; fallback **only on a 404**; a URL naming an endpoint is taken at its word. `normalise_host` accepts anything pasted from a README.
- **Check a new integration against the hardware that actually exists here before calling it done.**
- **No graphics card is required, and the copy must keep saying so.** `device: "auto"` falls back to the CPU on ANY failure. Describe the card as a speed-up.
- **A paid dependency chosen for one capability must not become a gate on a different one.** `docs/voice-design.md` carries the reversal at the top.
- **The tone marks the microphone really opening; the screen reader keeps talking over it, by design.** If a screen reader ever reaches a transcript, the answer is silencing speech (NVDA+S), **never "wear headphones."** **Do not design a defence around a fault nobody has reported, and do not let a plausible mechanism stand in for an observation.**
- **A stale timer must not reopen the microphone** — `_begin_dictation_capture` takes a generation stamped at the press.
- **The model is warmed at startup**, deferred, on a daemon thread, and **never raises**. If still cold when Talk is pressed, `_transcribe_worker` says so *before* the wait.
- **Defaults are the promise:** local model, no machine, `device` auto, `auto_send` off. Pinned by `tests/test_dictation.py`, with the old paid-voice gate as a positive control.
- **`migrate_dictation_config`** must stay **idempotent**, and **current keys beat legacy ones** (`backend`, `whisper_model`, `server_url`, …). Pinned by `tests/test_dictation.py`.
- **Settings are app-level, not per-kin.** `_dictation_cfg()` merges key-by-key over the defaults — **an option nobody can ever receive is the same as no option.**
- **No FFmpeg ships (licence: GPL codecs vs a CC0 project).** `wav_to_array` reads the WAV; `av` is excluded and `_install_av_stub` satisfies the import — **the stub must behave like a real module.** **If PyAV ever returns, revisit the CC0 claim first.**
- **No CUDA ships, so the CPU fallback must work at TRANSCRIBE time, not just at load** (`_local_whisper` retries on the processor; `_run_local` deliberately does **not** wrap its errors).
- **Bundle surgically:** `collect_all` only for `faster_whisper`; never collect `huggingface_hub` submodules (pulls torch, 4.59 GB); `ctranslate2` needs only `collect_dynamic_libs`; `av`, `tokenizers`, `onnxruntime` have hooks. Keep `_HEAVY_EXCLUDES` even when those packages aren't installed — **if one is reachable at all, that is the bug.**
- **Neither bot transcribes a voice message yet** — a real gap, recorded in `tests/_surface_matrix.py`. `stt.transcribe` takes bytes; missing is the download and format conversion.

## Anti-impersonation safeguards (rooms)

Small models routinely impersonate other kin when given a `[Name]: text` transcript-shaped prompt. **All of these must remain:**

1. Stop sequence `"\n["` in every chat call's options.
2. `strip_self_tag(text, kin_name)` — removes leading `[KinName]:`.
3. `strip_leading_speaker_tag(text)` — removes any `[AnyName]:` at position 0, looping for stacked tags.
3b. `strip_leading_named_speaker(text, known_speakers)` — the same **without the colon**. **Matches supplied names, never a bracket pattern** (kin open replies with emotes like `[laughs] yeah`). Callers pass the names they showed the model that turn.
4. `strip_trailing_other_speakers(text)` — slices from the first `\n[Name]:` onward.
5. System-prompt rules against multi-character scenes (often ignored; the helpers are the real enforcement).

**All four cleanup passes must run on every path that saves a kin's room reply** (normal completion, stop-mid-stream, Telegram-group cleanup). **2-kin rooms lock rotation to 0.** **Voice bleed under truncation** needs both defenses: a high `per_turn_token_cap` (2000) and the room prompt telling a kin to start its own reply, not finish a hanging one. → lessons.md, "Anti-impersonation safeguards (rooms)"

## Telegram group attribution

Group turns carry attribution **inline in the user content**: `[TIMESTAMP] [Display Name (@username)] <text>` — never as a `role=system` note — with **no colon** (`[Name]:` is an impersonation attractor). **Every surface builds the prefix through `chat_helpers.speaker_attribution_prefix`.** **Store the name BARE.** **Desktop attribution (`_history_entry_for_model`) and the desktop reply cleanup (`_on_stream_done` running `clean_kin_reply`) are a package — don't reinstate one without the other.** Pinned by `tests/test_import_speaker_slots.py`. → lessons.md, "Telegram group attribution"

## Telegram incoming messages are reassembled before dispatch

A long paste arrives as 2-3 updates; `TelegramBot._coalesce_message_parts` merges them in `_infer_loop` **before** `_handle_update`. **The window is per-kin config (`message_wait_secs`), deliberately not a constant.** `_COALESCE_SPLIT_WINDOW_SECS` is a **floor**: a part at the ceiling always waits, even when the person set 0. **Never coalesced:** slash commands, attachment/media-group turns, a sender with a pending exec approval. Pinned by `tests/test_telegram_coalesce.py`. → lessons.md, "Telegram incoming messages are reassembled before dispatch"

## Confirm-on-close (`_work_in_flight`)

`_on_close` asks before quitting when work is in flight. **The check runs BEFORE any teardown.** **It fails open in every direction** (unvetoable close, nothing in flight, or any exception → close proceeds). Sources of "busy": `_streaming`, `_room_active`, `_distilling`, `_heartbeat_workers`, `cron_helpers.cron_running_kin()`, `_cron_workers`, `TelegramBot.active_turn_label()`, `_pending_approvals`. **Adding a new kind of background work means adding it here** — and ask which *process* it runs in. **Silence when idle is a feature.** Pinned by `tests/test_confirm_close.py`. → lessons.md, "Confirm-on-close"

## The stop button (`should_stop`)

- `llm_backend`'s `_chat_collect_streaming` / `chat_collect` / `run_tool_loop` take an optional `should_stop`, polled per chunk and between tool-loop iterations. It sets `ChatResult.stopped` and **keeps the content collected so far**; read it with `getattr(result, "stopped", False)`.
- **`on_content` is not a stop channel.** A `should_stop` that raises means "keep going". A stop drops half-formed tool calls but **never abandons a tool call already executing**.
- Telegram: `_begin_turn`/`_end_turn`/`_turn_cancelled`/`_request_turn_stop` under `_turn_lock`. **`/cancel` must be intercepted on the POLL thread.** Stops are keyed per-person. **A stopped turn is not an empty reply** — skip the salvage pass, the log, the placeholder and the note.
- **A heartbeat is a `should_stop` caller too:** `_heartbeat_worker` re-checks `_work_in_flight()` **before** registering itself and passes a per-kin `threading.Event` through `run_heartbeat` into `run_tool_loop`; `_kick_off_distillation` calls `_signal_heartbeats_to_stop()` first.

Pinned by `tests/test_telegram_stop.py`, `tests/test_heartbeat_stop.py`. → lessons.md, "The stop button"

## Empty-reply diagnostics

Zero-output replies (tag eaten by `strip_self_tag`, a stream-id race, or a genuinely empty completion — Gemma is picky about alternation) display `[no reply produced]` and always write to `logs/empty_replies.log`. **Read the file** — it holds what the model actually returned. → lessons.md, "Empty-reply diagnostics"

## A heartbeat is the one surface where a missed tool call DELETES the kin's words

On a heartbeat the reply **is** the work and `reach_out` is the only delivery. **Never treat "did not call reach_out" as "had nothing to say": ask, don't guess.** `turn_steering.unsent_reach_note` triggers one more round (`surface="heartbeat-nudge"`), and **a second refusal is honoured**. Deliberately **not a classifier**; `min_chars` is crude on purpose. **`asked` becomes True only AFTER the second call returns.** **The privacy split is load-bearing:** `log_unsent_reach` keeps the TEXT only when nobody asked. **A model swap is the usual trigger, and it looks like the kin changed.** Pinned by `tests/test_heartbeat_unsent.py`. → lessons.md, "A heartbeat is the one surface where a missed tool call DELETES the kin's words"

## Memory & distillation

Conversations are auto-summarized into `memory.md` by `distill_memory_blocking()` — after N exchanges, at a % of `num_ctx`, or on close — per (kin, scope), on the per-kin `memory_model`. Every story and measurement: → lessons.md, "Memory & distillation".

- **Incremental and append-only.** Each run digests only turns past the (kin, scope) bookmark in `distill_offsets`; it cannot drop an entry (whole-file rewrite is `consolidate_memory_blocking`'s job). Input is bite-capped (`_distill_bite`).
- **One oversized message must not stop a kin's memory forever.** `_distill_bite` always sends at least one message; `_fit_oversized_messages` trims an over-budget message **in a copy — the stored conversation is never rewritten** — with a marker naming the true size, where the full text lives, and not to infer the ending. **Truncate, never skip.** Pinned by `tests/test_distill_oversized_message.py`.
- **A turn queued behind OUR OWN work is not a hang, and the app must say so.** `_on_stream_watchdog_fire` asks `_own_background_on_the_model()` **before** declaring a hang, and re-arms with `_stream_watchdog_minutes` (never a recompute). The Activity line names what has the model **mid-turn and while idle**; the foreground (`_streaming`, `_room_active`) is never reported. Pinned by `tests/test_watchdog_queued_not_hung.py`.
- **Telegram gets the same notice, on the POLL thread** (`TelegramBot._maybe_say_queued_from_poll` via `get_busy_label()`). **It must never consume the update.** Once per busy period per chat, released when the work ends; skips slash commands. Telegram asks the WIDE question (`include_foreground=True`); **the only turn skipped is this kin's own, in this same chat** (`skip_bot`). Pinned by `tests/test_telegram_queued_notice.py`, `tests/test_watchdog_queued_not_hung.py`.
- **A BACKLOG is paced; an ordinary catch-up is not.** `_note_backlog_pace` waits `distill_backlog_pace_mins` (default 30, Memory tab) when a run ends **further behind than it got**. **Only automatic triggers are paced** (`source_label` `every-`/`ctx-`); walks, queue drains, on-close and anything the person pressed never are. **In memory, never persisted.** Bad input starts no wait. Pinned by `tests/test_distill_backlog_pacing.py`.
- **The `## Memory logs` index is capped in CHARACTERS and says what it left out.** `memory_log_index_max_chars` (default 1500, 0 = no limit, Memory tab); **the unit stays in the key name and the visible label.** **Don't drop the index outright.** Newest first; **the truncation line naming the true remainder is not optional.** Rebuilt only when `memory.md` is written. Pinned by `tests/test_memory_log_index_cap.py`.
- **Staging is read in BATCHES and archived only as far as it was read.** `read_staging` returns whole sections up to `staging_read_max_chars` (default 6000, **below** `tool_result_cap`), names the remainder, gives `start=`. `archive_staging` files only the read prefix and **refuses when nothing has been read**. **Keep `clear_staging_read_mark` separate from `set_staging_read_mark`** (the setter never lowers). Splitting the file was rejected as out of scope for a tool a kin calls. Pinned by `tests/test_staging_batching.py`.
- **memory.md is an index, not an archive.** **Pointer/index bookkeeping is code's job; the summarizer only writes entries.**
- **A distillation has its own sound, and its PITCH reports progress** (`_tick_distilling_sound`; `distill_memory_blocking(on_progress=...)` → `_distill_progress[kin]`). **Steps, not a glide.** **It runs during a walk too.** **Standing one cue down must not silence the others.** **A fourth kind of long-running background call gets its own identity, not the generic busy tick.** Pinned by `tests/test_distilling_sound.py`, `tests/test_distill_progress.py`.
- **"&Cancel distilling" reaches all three triggers** (walk, all-surfaces queue, one-shot). **If you add a fourth thing that can distill, teach this button about it too.**

**A redistill-from-start ("walk") must survive being left alone.** State lives in TWO places, both load-bearing: `self._walking_from_start[(kin, scope)]` (live in this process) and `cfg["distill_walk_scopes"]` (started, not finished). Four rules, each shipped broken once:

- **Cancel must undo the rewind** (`distill_walk_prior_offsets` / `_restore_walk_bookmark`). **Anything new that resets a bookmark owes the same undo.**
- **Interruptions pause, they don't end.** Only finishing and Cancel clear the on-disk record. **Never make a failure path clear it.**
- **Never chain via `wx.CallLater(..., _kick_off_distillation, ...)`** — go through `_walk_next_chunk`, which retries and announces a give-up.
- **Anything that stops background work goes through `_announce_problem`** (speaks + plays the alert).

The walk must never depend on `EditKinDialog` being open. **Pacing** (`unattended` / `day` / `hour` / `chunk`) persists per (kin, scope) in `distill_walk_pacing`. `_distill_bite`'s day/hour cap counts **from the first genuinely NEW message, never the re-read overlap**; `hit_boundary` is true only when the boundary capped the bite. **Teach `_walk_should_pause_after_bite(pacing, hit_boundary)` about any new pacing value explicitly.** **Cancel's rewind-vs-keep split is gated on pacing deliberately** (unattended restores; paced keeps) — **don't "simplify" it to always-restore or always-keep.** Pinned by `tests/test_distill_walk_resume.py`, `tests/test_distill_walk_pacing.py`.

## The prompt must be append-only

A local model reuses cached work only for an UNBROKEN run from the start of the prompt; one early change costs minutes before the first word. So: **never edit what the model has already been shown.** Anything that varies per turn goes in the volatile tail beside the live user turn — never in the system block, never spliced into the middle. Measurements and stories: → lessons.md, "The prompt must be append-only — and so must every number that shapes it".

- **`_compaction_frontier`** (in `_compact_tool_history`) moves in **steps of `keep_window`**, never the per-turn `pairs[-keep_window:]`; `ceil`, not `floor`. Pinned by `tests/test_tool_history_stability.py`.
- **`TelegramBot._trim_history`** fills to `cap` and cuts a whole `cap // 4` at once, never `history[-cap:]` per append. Pinned by `tests/test_telegram_history_stability.py`.
- **The truncation BUDGET must be as stable as the truncation:** `_stable_truncation_budget` quantizes (`_BUDGET_QUANTUM`), **falls immediately, rises only on a big change**, keyed per (kin, surface, cap); `_CALIBRATION_DEADBAND` stops the ratio chasing noise. **Anything that feeds a number into the trim owes the same stability.** Pinned by `tests/test_truncation_budget_stability.py`.
- **The TOOL LIST is part of the system block and must not vary.** `read_staging`/`archive_staging` are always present when allowed. **Before gating anything out of a tool list, check whether it lands in the system block.** Pinned by `tests/test_tool_gating.py`.
- **Per-turn recall is inlined into the latest user turn**, never `role=system`.
- **`logs/prompt_fingerprint.log` is the instrument — read it before theorising about slow replies.** `logs/system_prompts/<kin>--<surface>.txt` + `.prev.txt` hold the last two system prompts that differed.
- **`_inline_mid_conversation_system_notes`** — read `docs/design/prompt-cache-system-fold.md` before touching either side. **Only the LEADING contiguous system run is the system prompt**; later notes stay in place, re-roled to `user`. **It MUST run before `_truncate_messages`.** **The fold is load-bearing** for the leading block; don't delete it. Don't reverse: `user` not `assistant`; the leading run stays protected; a note right before a `role=tool` turn stays `system`; it runs for **every provider**. Pinned by `tests/test_system_note_placement.py`.

**`memory.md` in the system prompt has the same problem, still unfixed** — bounded: a distillation write invalidates the next turn only.

## Four surfaces, one map — `tests/_surface_matrix.py`

A kin speaks through **four** surfaces: main window, Telegram, Discord, cron subprocess. A gap on one **looks identical to a fault in the kin.** → lessons.md, "Four surfaces, one map"

- **`tests/_surface_matrix.py` declares every capability for every surface** as `Present` / `Absent(reason)` (a real gap) / `NotHere(reason)` (a closed question).
- **`tests/_surface_settings.py` (23 Nov 2026):** every setting in every surface's defaults (Telegram's, Discord's, a room's, the kin's `desktop_` ones) must be answered `EVERY(capability)`, `HERE(reason)` or `QUESTION(text)`; unanswered fails. **Adding a setting to one surface means answering whether the others need it, in that file.**
- **The ratchet goes both ways** (Present-but-unwired fails; Absent-but-wired fails), and **a missing cell fails.** **Adding a capability to one surface means declaring it for all four.** `python tests/test_surface_parity.py --report` prints the map and gaps.
- Probes are source markers with **comments and docstrings stripped**, deliberately coarse. **The detector checks itself against a positive control before any answer is believed.**
- **A stub must have the real callback's shape:** `get_model_options` returns a `(model, options)` PAIR to both bots. Pinned by `tests/test_discord_parity.py`.

## Tools

One Python function per file; schema auto-derived from signature + docstring; opted into per-kin via an allowlist. → lessons.md, "Tools"

**Every tool a kin can be given must be visible and switchable in the real windows.** The owner: "I can't fix what I can't see." (23 Nov 2026.) A new tool, or a new way of granting one, must pass `tests/test_every_tool_has_a_switch.py` (group, wake-up and heartbeat switches; kin manual AND user guide) and `tests/test_every_tool_in_the_windows.py` (the real Tools tab and wake-up list, on the isolated desktop). A test that only reads code is not a UI test.

**Currently registered (22):** `memory_search`, `read_file`, `list_directory`, `write_file`, `edit_file`, `note`, `fetch_url`, `web_search`, `exec`, `list_processes`, `kill_process`, `context_status`, `recent_thinking`, `use_webcam`, `read_staging`, `archive_staging`, `analyze_sound`, `listen`, `look`, `tff`, `reach_out`, `message_kin` (kin writing to each other, proactive-only like reach_out; see kin_mail.py). `listen` / `look` go to the kin's `listen_model` / `look_model` (Tools tab; empty `look_model` uses `listen_model`) through `senses.py`; local Ollama gets media in the `images` field, **which `_API_MESSAGE_FIELDS` must keep** (missing until 13 Nov 2026). Read a tool's docstring for what it does. **This count is checked against the registry by `tests/test_tool_buckets.py`.**

- `tools/__init__.py` — registry; `load_tools(allowed_names, *, context=None)` returns `(schemas, executors)`; `context` auto-binds `agent_name`. `tools/_schema.py` — `build_schema(fn)`. `tools/_io.py` — `atomic_write_text`, `resolve_kin_path`, `robust_decode`. `tools/_search_providers/`, `tools/<name>.py`.
- **Paths** for `read_file` / `write_file` / `edit_file`: relative resolves inside the kin's folder (`..` rejected); absolute goes where it points. `note` takes a single filename only.
- **`read_file` extracts `.docx` text** (`tools/_docx.py`, shared with `reading_bridge.py`). **Extending extracted-format support anywhere? Check `read_file`'s docstring says so** — a capability a kin can't see in its tool description doesn't exist to it.
- **Forgiving path resolution** substitutes only when exactly one on-disk entry matches (whitespace dropped, casefolded). **Writes heal only the parent-directory chain; the filename stays verbatim.**
- Tool round-trips persist in `self.conversation`; `tool_history_keep` (default 5) keeps recent ones verbatim, older become a one-line `role=system` summary; `conversation.jsonl` keeps everything.
- **Background processes from `exec(background=True)` survive shutdown by design.**

**Adding a tool:**

1. Write `tools/<name>.py`. Annotate every parameter; the first docstring paragraph is what the model reads. Filesystem tools take `agent_name: str = ""`.
2. Register in `tools/__init__.py` (`_REGISTRY`).
3. **Bucket it in `tools/_buckets.py` — DO NOT SKIP.** `_READ`/`_WRITE`/`_FULL`, or `INTENTIONALLY_TELEGRAM_BLOCKED`. **A registered tool in no bucket is silently invisible on Telegram and Discord** (effective set = `allowlist ∩ bucket`). Update the matching `BUCKET_EXPLAINER` line.
4. **Run `python tests/test_tool_buckets.py`.**
5. Add the name to a kin's `tools.json`, then restart.

**Two focused tools beat one with an `action` parameter.**

## Exec — harness-side approval

`tools/exec.py` runs the command; safety is `Hearthkin._wrap_exec_executor`. Per call: (1) exact-string match in `exec_allowlist.json` → run; (2) `tool_trust == "full"` → run; (3) `tool_trust == "trusted"` and no denylist match → run; (4) otherwise `_request_exec_approval` on the main thread, worker blocks on an Event. Denylist in `tools/_exec_denylist.py`: **specific destructive shapes, not anything that looks scary** (`rm -rf /` yes, `rm -rf` alone no); add patterns only from concrete near-misses. `_closing = True` sets every pending approval Event. → lessons.md, "Exec — harness-side approval"

## Cron — scheduled wake-ups

`hearthkin_cron.py` is a standalone subprocess (no wxPython), run by Windows Task Scheduler per enabled entry. Lock present + `cron_inject_when_running=True` → drop a request file in `cron_requests/` and exit; a 5-second `wx.Timer` routes it to `_send_message` (active kin) or `_cron_isolated_worker` (other kin). Lock absent → run the call in the subprocess, append to `conversation.jsonl`, write the journal, post to Telegram if configured. `cron_helpers.py` holds shared primitives. Per-kin `cron_entries` is `{"time", "prompt", "enabled"}`, synced by `schtasks_sync_kin` (idempotent). Non-Windows: registration is a no-op, config still saves. → lessons.md, "Cron — scheduled wake-ups"

## Connections / API keys

`resolve_provider_key(name)` — env var `<NAME>_API_KEY` first, then `~/.ai_programs/<name>_key.json`; `write_provider_key(name, key)`. **Preferences → Connections** is the user-facing surface (masked, Edit, Test); a new key-only service (today `openrouter`, `brave`, `elevenlabs`) needs a row in `_build_prefs_tab` and a branch in `_provider_key_test_call`. **A chat provider needs no code:** a `name = https://host/v1` line in `~/.hearthkin/providers.md` (by hand or via `dialogs/api_providers.py`, "Manage providers…"), used by prefixing the model name. **Keys never go in that file** — they go through `write_provider_key`. → lessons.md, "Connections / API keys"

## Write the lesson, never the material

**`CLAUDE.md`, `docs/lessons.md` and `CHANGELOG.md` are tracked and written to be publishable at any moment.** Write the *mechanism* and the *rule*, never the material. **Do not check the repo's visibility setting before deciding what to write** — write every tracked file as though it is already published.

The test: **does this sentence disclose anything about the person or their life?** A kin's remark about markdown in its own config discloses nothing; a line from a kin's memory about someone's evening discloses a great deal. Describe the register (*"third-person event-log phrasing"*) instead of pasting an example.

`tests/test_no_private_strings.py` guards *known* strings from a gitignored list. **In a fresh clone, arm the hooks first with `git config core.hooksPath githooks`** — without it nothing runs the guard, silently. It cannot catch new private material. **The judgement is yours, and it has already gone wrong twice.** → lessons.md, "This file is public — write the lesson, never the material"

## A soul.md is not a document to be improved

**Asked to tidy, restructure, clarify or "clean up" a kin's `soul.md`? Stop and read lessons.md, "A soul.md is not a document to be improved", first.** It has already cost a kin once.

- Write it in the **second person**, to someone. **`you` count vs name count is a cheap, honest smoke test.**
- **Second person, AND the name, once:** *"Your name is X."* **Leave the door open** — say that changing the name is theirs to do.
- **Structure is the failure mode, not the fix** (XML tags, section headers, normalised terminology, exhaustive enumeration).
- **The register propagates**, and asking the kin to be warmer does not undo it.
- **"Make this clearer" is not a safe instruction for this file.** Ask what's actually wrong, change the smallest thing, keep the voice.
- Old souls live in `~/.hearthkin/agents-archive/`. **Before concluding a kin has drifted, diff against one.**

## What a kin knows ≠ what's in `conversation.jsonl`

Conversations reach memory only as the distiller works forward from its bookmark; **a bulk history import buries that bookmark.** Depth logs have no queue, so a kin reliably holds what it *wrote*, not what was *said to it*. **Before diagnosing a kin as having disregarded something, check `distill_offsets` against the conversation length.** → lessons.md, "What a kin knows ≠ what's in `conversation.jsonl`"

## Parks — every surface hands a kin the same view

Stories: → lessons.md, "Parks — every surface hands a kin the same view".

- **Any surface that shows a park result to anyone goes through `GameHost.decorate()`, never bare `run()`.**
- **A human reader gets their own bookmark** (`reader=`); only the bookmark splits.
- **`decorate()` swallows feed errors by design**, so a stale caller loses the co-op block silently. **Keep the test that asserts the block appears.**
- **A kin is not woken into a shut door:** `GameHost.reachable()` is checked *before* a wake-up shows the park or asks for a move (own config, no host assumed, **fails open**). **A pre-flight is not enough:** `GameHost.run()` itself logs the lost move to `park_unreachable.log`. Pinned by `tests/test_park_unreachable.py`.
- **Moves per turn: as many as `park_moves_max` allows (0 = no ceiling).** A reply with no `>` line is the kin's stop. **Deliberately no repeat guard.**
- **The loop lives in `park_keeper.play_turn`, once — never a second loop in a surface.** Surfaces supply only `run_move`, `ask`, `awaiting`, `cancelled`; **omitting `ask` runs exactly one move.**
- **A looping surface owes four things:** reachability checked **once up front**; the stop signal reaches it; `_work_in_flight` knows about it; a spent allowance reaches **the person and the kin**.
- **Desktop: the whole turn runs on a worker thread**; `_stream_id` is both generation guard and stop signal — no second cancel channel. Pinned by `tests/test_park_turn_loop.py`.
- **Cron plays a whole turn too** (`hearthkin_cron._run_cron_park_turn` → `play_turn`), relying only on `play_turn`'s own bounds; a looping wake-up writes a line to `cron_errors.log`. Pinned by `tests/test_cron_park_loop.py`.
- **Discord gets one move per reply, never the loop** (`discord_bot._route_park_command`; guild-shaped, no DM path); `tff` stays directly callable. Pinned by `tests/test_park_surfaces.py` and `tests/_surface_matrix.py` (`park_turn`).
- **The pacing lives in editable prompts, not constants.** What decides how much a kin may do belongs in a file its person can open.

## A test run must never speak, and never make a sound

**Real handlers talk, so silence is enforced at the two choke points, `audio.nvda_speak` and `audio._play_async`, via `audio._suppressed()` — not by asking each test to patch what it might reach.** Two ways in: `HEARTHKIN_SILENT` (set by `run_all.py` for every child) and the main script's name (`test_*`, `run_all.py`, `_gui_runner.py`), which **exports the flag so spawned interpreters inherit.** **Deliberately not keyed on `HEARTHKIN_HOME`** — a second profile must still speak. **One exception: `audio.speak_result`, called once by `run_all.py` at the end** with the verdict. **Keep it to one line, at the end, never from a test**; a failure is announced too — silence must never read as green. Pinned by `tests/test_suite_is_silent.py` (spy checked against a **positive control**). → lessons.md, "A test run must never speak, and never make a sound"

## Never build wx widgets in the default test run

**Creating a top-level wx window takes the FOREGROUND on Windows even when never shown, and a screen reader follows focus, not visibility.** → lessons.md, "Never build wx widgets in the default test run"

- **The gate is the RUNNER's job:** `run_all.py` reads each test's source (`builds_widgets`).
- **Window tests RUN on an isolated desktop:** `tests/_isolated_desktop.py` (`SetThreadDesktop`); `tests/_gui_runner.py` isolates first, in a fresh process, before wx is imported.
- **Only with `--gui`, and only after telling the person (24 Nov 2026).** The owner: "some test or other is making NVDA freeze and it's unpredictable". Plain `python tests/run_all.py` skips every window test (and the isolation probe, and `test_no_focus_theft.py`'s end-to-end check) and says so; `python tests/run_all.py --gui` runs them — on purpose, after saying so, before every build, not on every change.
- **Never call `SwitchDesktop`.**
- **`enter_isolated_desktop()` returns True only when CONFIRMED** against the input desktop.
- **If isolation fails, the wrapper REFUSES the test**; `run_all` skips, announced.
- `HEARTHKIN_GUI_TESTS=1` survives only as a fallback. `IsShown()` is not evidence; ask Win32. Pinned by `tests/test_no_focus_theft.py`, measuring **from a process that never isolates**.

## Tests share one sandbox and have no per-test timeout

**`run_all.py` gives every test file ONE shared sandbox.** A test that calls `chat()` or reads logs makes its own folder inside it: `tempfile.mkdtemp(dir=os.environ.get("HEARTHKIN_HOME"))`. **No per-test timeout:** a raise inside a wx callback hangs the suite — record the failure and end the loop. → lessons.md, "Tests share one sandbox and have no per-test timeout"

## Leaving a tool behind

**Anything added to `scripts/` gets a line in `scripts/README.md` in the same change** — what it does, how to run it, *what it changes* (nothing / writes files / edits config) — and say so in the reply. **A tool nobody but its author can run isn't a tool the project has.** A one-question diagnostic: index it, or delete it before you finish. → lessons.md, "Leaving a tool behind"

## Conventions (still live)

Stories in lessons.md, "Conventions (still live)"; dated postmortems in `docs/private/project-history.md`.

- **Static imports only** — never dynamic `importlib.import_module(...)` for project modules.
- **Stdlib-first.** `requirements.txt` lists only what launches the app; heavier libs go behind `try: import <lib>` with graceful degradation.
- **All configuration a normal user touches must be UI-reachable.** JSON-only is fine only for advanced overrides.
- **Accessibility-first widgets.** `wx.TextCtrl` (multiline read-only when needed) instead of `wx.StaticText` for anything found by tabbing. Numbers use `dialogs._IntField`, **not `wx.SpinCtrl`**. Buttons use `&Letter` mnemonics; **the visible label IS the accessible name** (`SetName` on a button is ignored on wxMSW). **Tab-reachability is mandatory; object-navigation is a workaround, not a fix.**
- **A single-line read-only `wx.TextCtrl` cannot be reached by Tab** on wxMSW: use `TE_MULTILINE | TE_READONLY`, and speak a result that arrives while focus is elsewhere. Pinned by `tests/test_test_result_reachable.py`.
- **A menu accelerator beats the focused control** on wxMSW (Ctrl+Enter goes through `InputAttachMixin._on_ctrl_enter_menu`). **Before giving a menu item a shortcut, check no text control relies on that key.** Test keys by `PostMessage` with `SetKeyboardState` on an isolated desktop — **never `SendInput`**. Pinned by `tests/test_ctrl_enter_sends.py`.
- **Text inputs: plain `wx.TextCtrl` + a buddy `&Label:` StaticText** right before it — not composites (`wx.SearchCtrl`, `wx.ComboBox`), whose inner EDIT child `SetName` can't reach.
- **First-letter navigation for lists** with display prefixes (`♥♥♥`, `[X]`): intercept `EVT_CHAR`, match the underlying data (`ModelBrowserDialog._on_list_char`).
- **Tolerant decoding for every file read** — `tools/_io.py:robust_decode` (UTF-8 → cp1252 → UTF-8-replace). Atomic writes always UTF-8.
- **`get_models()` is cached** in `model_utils._models_cache`; `clear_models_cache()` and `_tool_cap_cache.clear()` are wired to Refresh Models.
- **Normalize providers at the `chat()` choke point**; stored history shape is not trusted. **A new choke-point fix gets a case in `test_llm_normalization.py`.**
- **`_API_MESSAGE_FIELDS` is the allowlist of per-message keys sent to providers** (`_strip_extra_message_fields` drops `ts`, `source`, `sender_id`, `speaker`, …). **Don't add a real message field without adding it here.**
- **An importer decides role by NAME MATCH against `kin_display_name`, never by talk volume, header shape, or "whoever's left over."** Match → `role=assistant`; everyone else `role=user` under their own name; no match → **nobody** becomes the kin.
- **A DM is not exempt**, but **don't require an exact match unconditionally in a true two-party DM** — fall back sensibly.
- **Not all name-matching is two-party** (OpenClaw carries its own role). **Never leave two real identities claiming the same role under the same name.**
- **`kin_persistence.sanitize_for_prompt_literal()`** (strips Cc/Cf/U+2028/U+2029) **goes on every new prompt-embed site for an external string** — not on legitimate multi-line content.
- **Per-kin state's source of truth is `agent_cfg`, not widget values.**
- **Heavy work runs off the UI thread** — worker + `wx.CallAfter` (`_on_refresh_models`).
- **Debounce rapid keyboard events on heavy handlers** (radio groups, arrow-holds, arrowing through a list or combo): paint feedback now, expensive work in `wx.CallLater(200, ...)` with the previous timer stopped.
- **Cold-start hint:** 8s with no first chunk shows a hint, with a generation guard.
- **Telegram output is append-only.** Never edit a previously-sent message.
- **Per-user tool gating in multi-user surfaces:** `cfg.telegram.user_tools[user_id]`; missing user → `'none'`.
- **Remote approval is chat-based:** the worker blocks until `/allow`/`/deny`/`/remember` or the natural-language equivalent; per-kin `approval_timeout_secs` auto-denies.
- **Harness prompts are editable text, not buried strings:** register in `APP_PROMPT_REGISTRY`, serve via `load_app_prompt(slug)`, **substitute with `str.replace`, never `.format`.** When adding or changing one: **(1)** bump the registry `version` (for `app_prompts_needing_update()`); **(2)** document in `docs/kin_manual.md` + `docs/user-guide.html`, note in `CHANGELOG.md`; **(3)** extend `tests/test_app_prompts.py`. **Don't add a buried prompt string; register it.**
- **Tabbed dialogs: `wx.Notebook` + one `wx.Panel` per page.** **Don't** `Disable()` hidden pages or `Show()` widgets on inactive ones. **Don't trust `IsShown()`** on hidden-page children (wxWidgets #4343); use the selection or `IsShownOnScreen()`.
- **Tabs hold everyday controls; power-user knobs go behind a `'… settings…'` button** to a focused, flat dialog with bold section headers (not tabbed, not an inline reveal checkbox), omitting groups that can't apply. Templates: `dialogs/recall_settings.py`, `sampling_settings.py`, `model_options.py`, `tool_settings.py`.
- **File I/O failures → `append_failure_log` + a status-bar message, never a crash.**
- **Expect silent stderr from `.pyw`:** `pythonw.exe` discards stdout/stderr; failures go to `~/.hearthkin/logs/*.log`; `print()` is invisible.

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
