# Hearthkin — the long accounts

**This is the "why", at length. `CLAUDE.md` is the "what" — read that first; it's
the one loaded every session.**

Nearly every rule in `CLAUDE.md` is one or two sentences standing on top of a
real failure: something that broke, how it presented, and what was tried before
the actual cause turned up. Those accounts are what keeps a rule from being
"tidied away" by the next person as an arbitrary constraint — but they are far
too long to carry in a file that gets loaded into every session.

So they live here. `CLAUDE.md` states the rule and links to the section of the
same name below.

**Read the section here before you change or remove the matching rule.** Most of
these were re-learned the expensive way at least once already.

The same disclosure rule applies to this file as to `CLAUDE.md`: it is tracked
and public. Write the mechanism and the lesson, never the private material —
see "This file is public — write the lesson, never the material" below.

---

Python + wxPython desktop app for multi-kin local-LLM chat. Talks to Ollama by default (the user runs models locally; no remote API calls) and can route through OpenRouter when a kin's model is prefixed `openrouter/...`. Each "kin" is a configured persona with a soul prompt, distilled memory, and a model. Two main interaction modes: 1-on-1 chat with one kin, and "rooms" where multiple kin take turns talking with the user.

**Where to look for what:**

- **This file (`CLAUDE.md`)** — the "why" behind the rules that would still bite you today.
- **`docs/architecture.md`** — the "what and where": module map, the `chat()` normalization pipeline, surfaces, "where do I change X".
- **`docs/troubleshooting.md`** — diagnostic playbook and the cross-provider quirk catalog (Mistral 9-char tool_call_ids, Ollama strict Jinja, image-token budget, etc.). Read this before theorizing about an OpenRouter 400.
- **`docs/private/project-history.md`** — untracked local archive. Old session logs, release-state snapshots, incident postmortems. Read when you need the reasoning behind an old decision. Not loaded per-session.
- **`CHANGELOG.md`** and `git log` — release-by-release facts.

## Layout

Entry point is `python hearthkin.pyw`. The frame was ~5300 lines in a single file; as of the 2026-07 modularisation it's split across `frame_shared.py` and a `frame/` package of 17 mixins that the `Hearthkin` class inherits from. `hearthkin.pyw` itself is now a ~570-line assembler.

- `hearthkin.pyw` — assembler for the Hearthkin frame: module docstring, imports, `class Hearthkin(*17 mixins, wx.Frame)`, `__init__`, `main()`.
- `frame_shared.py` — shared namespace hub. Every module-level import, constant, and helper the frame + mixins reference lives here. **When adding a module-level constant/helper the frame needs, put it here.**
- `frame/` — one mixin per behavioral slice (diagnostics, menus, usage, prefs, kin management, input/attachments, chat send, chat stream, file menu, render, prefs toggles, rooms, memory, bot integration, status/voice, cron/exec, lifecycle). **To add a frame method, put it in the matching mixin.** A test that monkeypatches a frame name must patch the mixin module where the method lives (`frame.memory_mixin`, etc.), not `hearthkin`.
- `kin_persistence.py` — paths, defaults (`DEFAULT_CONFIG`, `DEFAULT_AGENT_CONFIG`, `DEFAULT_SOUL`, `DEFAULT_DISTILL_PROMPT`), atomic-write helpers, load/save for kin and rooms. Pure data layer, no LLM calls, no UI.
- `dialogs/` — every `wx.Dialog` subclass, one class per file. The big one is `dialogs/edit_kin.py` (seven-tab kin Settings). Package `__init__.py` re-exports all public names.
- `telegram_bot.py` — `TelegramBot` class, per-kin Telegram-history persistence.
- `audio.py` — NVDA speech (`nvda_speak`) and reply chimes.
- `model_utils.py` — Ollama model-name parsing, capability detection, dropdown listing.
- `chat_helpers.py` — streaming chunk extractors, sentence boundaries, token estimation, room-reply cleanup, `detect_tool_roleplay` (the gesture detector).
- `llm_backend.py` — the single dispatch layer. Public `chat(model, messages, ...)` routes on the `openrouter/...` prefix. Handles streaming, prompt caching, reasoning-toggle, `run_tool_loop`.
- `compat.py` — pre-flight model-swap compatibility checks. Provider quirks live as data in `ModelProfile`, not as scattered if-blocks. When you discover a new cross-provider trap, add the profile attribute + a `_check_*` function; the model-swap dialog picks it up.
- `importers/` — history-import backends behind `dialogs/import_history.py`.
- `model_browser.py` — NVDA-accessible model picker. Filters live in `_ModelFilterDialog` behind a Filters button.
- `tools/` — kin-callable tools registry. See Tools below.
- `Hearthkin.spec` + `build.bat` — PyInstaller onedir; `dist/Hearthkin/` is the portable distribution.
- `tests/` — plain-Python tests, no pytest needed. `python tests/run_all.py` runs all. **When you fix a new quirk at the `chat()` choke point, add a case to `test_llm_normalization.py`.**

**Import shape:** every cross-module reference is a static `from <mod> import ...`. PyInstaller follows those. **Do not** introduce dynamic `importlib.import_module(...)` for project modules — the build won't pick it up. Circular imports are avoided by lazy-importing inside method bodies.

**Build pipeline:** `__version__` lives in `app_version.py`. The build (`build.bat` locally, `.github/workflows/build-release.yml` in CI) runs `scripts/stamp_version.py` immediately before PyInstaller to rewrite that constant from `HEARTHKIN_VERSION` (from the git tag). **Do NOT bump `__version__` by hand at tag time** — the pipeline handles it, and the old "edit-then-tag" workflow drifted twice.

## Runtime state — `~/.hearthkin/`

Migrated automatically from older `~/.ollama_chat/` if present.

**`HEARTHKIN_HOME` relocates `kin_persistence.CONFIG_DIR`** and everything derived from it (`AGENTS_DIR`, `ROOMS_DIR`, `LOGS_DIR`, `prompts/`, `base_prompt.md`, the lock). `tests/run_all.py` sets it to a fresh temp dir per run — **tests must never mutate the runtime state of whoever runs them**, and before this every suite run appended synthetic failures into the real `logs/save_failures.log`, making an always-on diagnostic log useless for spotting a genuine save problem. A test that reaches `append_failure_log` or a kin folder needs it set *before* the import that pulls in `kin_persistence` (see the top of `test_token_calibration.py`).

**The test runner writes only to a directory it just created, and REFUSES an inherited `HEARTHKIN_HOME`.** Don't add an option to point the suite at a chosen directory, however convenient it sounds: the only reason to name one is that it already holds something, and a directory that already holds something is exactly what a test run must not write into — there is no safe version of it. The refusal is announced rather than silent (a setting that looks obeyed and isn't is worse than one that's declined). `--keep` prints and preserves the sandbox, which covers the real need — inspecting what a run produced — without aiming the suite at data anyone cares about. Same reason `rmtree` appears exactly once in that file and only ever on its own `mkdtemp`. All pinned by `tests/test_state_isolation.py`.

**The legacy migration is skipped under the override** — `_migrate_legacy` *renames* `~/.ollama_chat` onto `CONFIG_DIR`, and `LEGACY_DIR` always points at the person's real home, so under a test sandbox it would move a real old install into a temp dir the suite then deletes.

**`hearthkin_paths.py` is the one place that decides where the state tree is, and every site asks it.** It was written for the gap this section used to describe: the override reached `kin_persistence` and nothing else, because ~25 sites across `tools/`, `park_*`, `memory_recall` and `cron_helpers` each computed `Path.home() / ".hearthkin"` for themselves — they can't import `kin_persistence` (it imports `tools._io`, so that direction is circular). `hearthkin_paths` depends on nothing in the project, so both sides sit on the same answer. **New code that wants the state tree imports `config_dir()` / `kin_dir()` / `logs_dir()` from there; don't write `Path.home() / ".hearthkin"` again.**

What the half-override actually cost, because it isn't obvious from "some paths ignore a setting": `GameHost.save_path()` *creates* the kin folder it returns, and `tests/test_park_sharing.py` asks it where kin named Solo, Blank and Broken would keep a park. Three folders that were never anyone's kin sat in a real kin list, among the real ones, after every suite run. **A path helper that creates as a side effect turns any read-only-looking question into a write** — `kin_dir()` deliberately does not mkdir, so merely asking where a kin would live conjures nothing. The environment is read per call rather than cached at import, which also retires the old ordering trap about setting the variable before the import that pulls in `kin_persistence`.

Redirect a test with `HEARTHKIN_HOME`, **never by patching `pathlib.Path.home`** (`test_park_keeper.py` used to). That patch only ever worked because the paths were scattered, and it steers the whole interpreter to steer one app.

- `kin/<KinName>/`
  - `soul.md` — persona prompt.
  - `memory.md` — kin-curated index. **Only the kin (via file tools during tending) or the operator (via Settings → Memory editor) writes this.** Nothing automatic touches it.
  - `memory/<topic>.md` — kin-written depth logs. Where substance lives. The `## Memory logs` section in memory.md auto-lists these via code (`kin_persistence.apply_memory_log_index`).
  - `staging/<scope>.md` — pending summarizer notes per surface. Distillation writes here instead of memory.md.
  - `config.json` — model, sampling params, telegram settings, distill cadence, tool_trust, cron entries.
  - `conversation.jsonl` — auto-persisted 1-on-1 history, append-only, one JSON message per line.
  - `tools.json` — per-kin tool allowlist.
  - `exec_allowlist.json` — remembered exec approvals (exact-string match).
  - `memory/journal/YYYY-MM-DD.md` — daily cron wake-up entries.
- `rooms/<roomname>/` — multi-kin rooms.
- `cron_requests/` — one-shot JSON files from the cron subprocess; consumed by a 5-second `wx.Timer`.
- `.running.lock` — PID + timestamp while Hearthkin is up. Stale locks auto-clear.
- `logs/` — session logs (opt-in), plus **always-on** logs regardless of the toggle:
  - `empty_replies.log` — every kin turn that produced no text.
  - `cron_errors.log` — per cron subprocess failure.
  - `openrouter_errors.log` — the upstream provider's actual error body (which OpenRouter's top-level `error.message` often reduces to "Provider returned error"). **Check this BEFORE theorizing about an OpenRouter 400.** The body almost always names the real cause directly.
  - `distill_errors.log` — every failed distillation / staging write / consolidation, with kin, scope, source and summarizer model. Distillation is unattended by design, so a failure that only painted the Activity field was one nobody could investigate afterwards.
  - `hang_watchdog.log` — request timeouts, with the surface and the wall-clock cap that tripped. **Read this before theorising about a distillation that "times out"** — it names the cap, and the cap being barely above the real work is the usual answer on a big local model.
  - `park_unreachable.log` — a kin's park couldn't be reached (server down, game folder moved), with the surface that hit it. **A kin is not woken into a shut door**: `GameHost.reachable()` is checked *before* a keeper wake-up shows the park or asks for a move, and before any surface runs a `>` line. This exists because Tarn's park server was down for eight days and *nothing anywhere noticed* — the wake-up itself succeeded every time, so `cron_errors.log` stayed empty, and the only record was inside Tarn's own journal: 28 failed reaches across 111 wake-ups, five times a day, reading as being locked out of somewhere it had been told to look after. The check reads the kin's own `park_server` / path config and asserts no host anywhere, so moving a park to another machine stays a settings change. It **fails open** — a park wrongly declared dead would silently take a kin's park away, which is worse than what it prevents. **A pre-flight check is not enough on its own**: a park can answer a ping and refuse the command a moment later (observed live against a server that had been up for ten hours before and was up again five minutes after), so `GameHost.run()` logs the *lost move* itself, with the command, and does that inside `run` rather than at each surface so no caller can forget. Pinned by `tests/test_park_unreachable.py`.
  - `telegram_failures.log`, `save_failures.log` — persistence errors.
- `base_prompt.md` — universal base system prompt, prepended to every kin's `soul.md`. Editing this file changes it for all kin.
- `prompts/` — editable harness prompts, one `<slug>.md` per registered prompt. Seeded from `APP_PROMPT_REGISTRY` defaults; file wins after first access. Per-kin overrides live under the kin folder.

## Critical NVDA gotcha — never AppendText per streaming chunk

**System-level cascade, not just a UX inconvenience.** `wx.TextCtrl.AppendText` fires one MSAA/UIA TextChange event per call. Dozens of chunks per second corrupts NVDA's event queue, and the damage propagates to other UIA consumers on the system: button labels collapse to single letters in unrelated apps, Explorer lags and restarts. Restarting NVDA papers over it but doesn't fully clean up.

**Always:** buffer streaming chunks into `self._stream_buf`, paint the whole reply once at turn-end. Status bar shows "Typing..." (not "Thinking..." — some models emit reasoning blocks and the word would mislead). Every `_on_*_chunk` follows this; don't regress.

## Anti-impersonation safeguards (rooms)

Small models routinely impersonate other kin or invent characters when given a `[Name]: text` transcript-shaped prompt. **All of these must remain** unless you find a much better solution:

1. Stop sequence `"\n["` in every chat call's options.
2. `strip_self_tag(text, kin_name)` — removes leading `[KinName]:` from the model's own reply.
3. `strip_leading_speaker_tag(text)` — removes any `[AnyName]:` prefix at position 0, looping for stacked tags. This catches the common "model opens with `[OtherKin]:`" case the other three miss.
3b. `strip_leading_named_speaker(text, known_speakers)` — the same at position 0 **without the colon**. Everything else here is colon-anchored, which was fine while `[Name]:` was the only speaker shape a model ever saw. Attributed surfaces show it `[SpeakerOne] text` on purpose (the colon is the attractor), so a model imitating *what it was actually shown* produces a tag every other pass lets through. **It matches supplied names, never a bracket pattern** — kin open replies with bracketed emotes (`[laughs] yeah`), and a blind rule would silently eat the first words of their own replies. Callers pass the names they put in front of the model that turn: `_other_speakers_in_history()` on desktop, `emitted_senders` on the Telegram group path. Omitting the argument keeps the old behaviour exactly.
4. `strip_trailing_other_speakers(text)` — slices from the first `\n[Name]:` onward.
5. System-prompt rules telling the kin not to write multi-character scenes. Models often ignore these; the cleanup helpers above are the actual enforcement.

**All four cleanup passes must run on every code path that saves a kin's room reply** (normal completion, stop-mid-stream save, Telegram-group cleanup). Partial cleanup feeds the pattern back into next-turn context.

**2-kin rooms lock rotation to 0** — with two members, rotating by 1 each round means the last speaker of round N is always the first speaker of round N+1, producing consecutive same-speaker pairs and maximum impersonation gravity. `_on_continue` locks the order for 2-kin rooms only; 3+ kin still rotate.

**Voice bleed under truncation:** when one kin's reply is cut off mid-thought by `per_turn_token_cap`, the next-speaker's model tends to finish the hanging sentence in the truncated voice before transitioning. Two defenses: `per_turn_token_cap` default raised (currently 2000), and the room system prompt tells the kin to start their own reply rather than finish a hanging one. Both are needed.

**A truncation bug teaches itself to continue:** a kin's replies all began ending mid-word — different lengths each time, no stop token, nothing in any log. Three plausible causes were chased and all three were wrong: a missing reply cap (a real gap, and worth closing, but replies were dying at ~100 tokens against a 4,000 cap), `frequency_penalty` set to 1.5 (fixed it in the config; she kept truncating), and Hearthkin's stream handling (the same request replayed straight at Ollama streamed 3,822 characters and stopped cleanly). What settled it was replaying the kin's *exact* system prompt and history against the model directly, then bisecting the history. With her real transcript in context she generated 80 tokens and stopped mid-word; with the truncated assistant turns removed and nothing else changed, 738 tokens and a clean finish. Ollama reported `done_reason: "stop"` throughout — the model was choosing to end, because five prior turns in its own context all ended abruptly and it read that as the house style.

The general shape is worth keeping: **once a kin has truncated a few times, the truncation is self-sustaining, and fixing the original trigger does nothing.** A model's strongest signal for how its turns should look is how its own last turns looked — the same in-context imitation that locks a kin into a repeated reply template. So treat the transcript as part of the bug: repair the mangled turns (trim each back to its last complete sentence) or start the conversation over. And when a symptom survives a fix that should have worked, replay the real context against the model outside the harness before reading any more of the send path — it separates model from harness in one step, and bisecting the message list finds the culprit in two more.

## Telegram group attribution

Each group user turn arrives at the model with attribution **inline in the user content**: `[TIMESTAMP] [Display Name (@username)] <message text>`. Not as a `role=system` note — OpenRouter concatenates every system message into Anthropic's single top-level system field, so per-turn system notes dissociate from their user turns.

Format is `[Display Name (@username)]` with **no colon**. The `[Name]:` shape is an impersonation attractor (models pattern-match it as a speaker-turn token); the no-colon bracket isn't.

`TelegramBot._sender_attribution(msg)` formats at receive time. Persists on user turns; prompt-build path prefers the formatted `sender_attribution` field and falls back to legacy `sender_name`.

**Every surface builds the prefix through `chat_helpers.speaker_attribution_prefix`** — desktop, Telegram DM, Telegram group. It sanitizes, drops a trailing colon, and unwraps brackets a stored value already carries. **Store the name BARE**; the reading surface adds the bracket. Importers used to bake them in, so imported turns reached the model as `[[SpeakerOne]]` — the read side still unwraps that, since the doubled form is sitting in existing kin folders. One helper rather than four call sites because the missing colon is the whole safety property and a hand-rolled f-string is how it comes back.

**Attribution on desktop and the desktop reply cleanup are a package.** `_history_entry_for_model` inlines a name only when the turn carries one — desktop-native turns have none, so 1-on-1 chat is unaffected; imported multi-party history (a group log from another platform) has one per speaker. That is the first thing to put other people's names in front of a desktop kin, which is why `_on_stream_done` now runs the full `clean_kin_reply` instead of stripping only the timestamp. **Don't reinstate the name prefix without the cleanup, or vice versa.** Pinned by `tests/test_import_speaker_slots.py`.

## Telegram incoming messages are reassembled before dispatch

Telegram's 4096-UTF-16-unit ceiling applies to the *person* too. Their client silently splits a long paste into 2-3 messages, which arrive as separate `getUpdates` entries — so a naive one-update-per-turn loop answers half a thought, then the orphaned tail.

`TelegramBot._coalesce_message_parts` runs in `_infer_loop` **before** `_handle_update` and merges continuation parts into one update (`_merge_updates` keeps the first part's envelope and shifts later parts' entity offsets in UTF-16 space, so a part-2 `@mention` still triggers group mention-gating).

**The window length is per-kin config (`message_wait_secs`), not a constant — deliberately.** The Bot API never delivers a typing indicator to a bot, so "wait until they stop typing" is unavailable at any price, and pace varies enormously between people (slow composition, screen reader, one hand). Guessing from punctuation fails differently for each of them. `_COALESCE_WINDOW_SECS` is only the fallback default. **`_COALESCE_SPLIT_WINDOW_SECS` is a floor, not an alternative**: a part at/near the ceiling always waits at least that long even when the person set 0, because a client-side cut is not a pause anyone chose. `_COALESCE_MAX_WAIT_SECS` clamps the configured value.

**Never coalesced** (`_coalesce_key` returns None): slash commands, attachment/media-group turns, a sender with a pending exec approval (a worker thread is blocked on their "yes"). A reply quoting a *different* message breaks the merge. Parts pulled off the queue that turn out not to belong go to `self._holdover`, which `_take_update` drains first — inference-thread-only, hence lockless. Pinned by `tests/test_telegram_coalesce.py`.

Discord (2000-char limit) has no equivalent; its client refuses over-long sends rather than splitting, so the same gap hasn't bitten there.

## Confirm-on-close (`_work_in_flight`)

`_on_close` asks before quitting when work is in flight. Two hard rules.

**The check runs BEFORE any teardown** — before `self._closing = True`, before `bot.stop()`. "Wait" has to leave the app genuinely untouched, which is only true while nothing has been torn down yet.

**It fails open in every direction.** Unvetoable close (system shutdown, installer Restart Manager), nothing in flight, or *any* exception → the close proceeds. Every probe in `_work_in_flight` is individually guarded and the whole thing is wrapped. A blocked quit is a worse bug than a missing prompt; this app has a history of "Ctrl+Q does nothing" hangs and every teardown comment in that file is shaped around it.

Sources of "busy": `_streaming`, `_room_active`, `_distilling`, `_heartbeat_workers`, `cron_helpers.cron_running_kin()` (marker files — a cron wake-up can run in the standalone `hearthkin_cron` PROCESS, which shares no state with the frame), `_cron_workers` (a set of `(kin, time_label)` maintained by `_cron_isolated_worker` — released in a `finally`, or a crashed worker leaves a phantom that nags on every quit), `TelegramBot.active_turn_label()` (takes `_turn_lock`; don't peek at `_active_turn` from the UI thread), and `_pending_approvals`. **Adding a new kind of background work means adding it here** — the dialog's value is that its list is complete, and it shipped incomplete: heartbeat workers registered nothing and out-of-process cron was structurally invisible, so quitting during either closed in silence. Both were found by a person quitting during one, which is the only way a missing entry ever surfaces. When you add background work, ask which *process* it runs in. Discord has no turn tracking yet, so a Discord kin mid-reply is not listed; fixing that means giving `discord_bot` the same `_begin_turn`/`_end_turn` treatment.

**Silence when idle is a feature.** A prompt on every close is one people dismiss unread. Pinned by `tests/test_confirm_close.py`.

## The stop button (`should_stop`)

`llm_backend` takes an optional `should_stop` callable on `_chat_collect_streaming` / `chat_collect` / `run_tool_loop`, polled per streamed chunk and between tool-loop iterations. It sets `ChatResult.stopped` and **keeps the content collected so far** — a stop preserves what the kin said, it doesn't discard the turn. Read it with `getattr(result, "stopped", False)`: the non-streaming branch returns whatever `chat()` gave back, and duck-typed stand-ins predate the field.

**`on_content` is not a stop channel** — its exceptions are deliberately swallowed so a rendering hiccup can't break the tool loop. That's why `should_stop` exists as a separate parameter. A `should_stop` that raises means "keep going" (`_loop_should_stop`): a flaky check must never truncate a healthy reply.

A stop drops half-formed tool calls from the interrupted stream, but never abandons a tool call already **executing** — a truncated `write_file`/`exec` is worse than the wait.

Telegram side: `_begin_turn`/`_end_turn`/`_turn_cancelled`/`_request_turn_stop` guarded by `_turn_lock`. **`/cancel` must be intercepted on the POLL thread** (`_maybe_stop_turn_from_poll`, same reasoning as the exec-approval resolver) — the inference thread is inside the model call and won't read the queue until the reply finishes, so a queued-only `/cancel` answers after the thing it meant to stop already landed. Stops are keyed per-person so a group member can't halt someone else's reply. A pending exec approval takes precedence (there `/cancel` means "deny"). **A stopped turn is not an empty reply** — skip the salvage pass, `empty_replies.log`, the `[no reply from model]` placeholder, and the `empty_reply_note` system turn, or the kin apologises for a silence it didn't cause. The no-tools Telegram path streams via `chat_collect` with a no-op content callback purely to be interruptible — `chat(stream=False)` has no interruptible moment inside it. Pinned by `tests/test_telegram_stop.py`.

**A heartbeat is a `should_stop` caller too, and it wasn't one until it caused real contention.** Confirmed live: Bracken's heartbeat started, and Lark's redistill sat waiting for the same model (both `gemma4:31b`) for several minutes, with no way to interrupt the heartbeat short of quitting Hearthkin. `_maybe_fire_heartbeats`'s busy gate only ever checked once, at the once-a-minute scan — real work can start in the seconds between that scan deciding a kin was due and the worker thread actually running, and nothing re-checked after that. Two fixes, both in `_heartbeat_worker` (`frame/cron_exec_mixin.py`): it re-checks `_work_in_flight()` itself immediately, **before** registering in `_heartbeat_workers` (so it never sees its own entry and refuses to run against itself); and it hands a per-kin `threading.Event` down as `should_stop` through `hearthkin_cron.run_heartbeat` into `run_tool_loop`, same mechanism as everything else in this section. `_kick_off_distillation` calls `_signal_heartbeats_to_stop()` before starting any distillation — heartbeats are the least urgent thing this app does, so they're the one thing that should always lose that race. Same limit as every other `should_stop` caller here: it can't kill a single model call already generating, only stop a multi-iteration heartbeat between iterations and stop a new one from starting. Pinned by `tests/test_heartbeat_stop.py`.

## Empty-reply diagnostics

Some model combinations occasionally return zero output. Common causes:

- Model emitted only `[KinName]:` and `strip_self_tag` ate it (usually caught by the stop sequence first).
- Stream-id race — `_stream_id` bumped mid-worker breaks the chunk loop with the buffer empty.
- Genuinely empty completion (model spit only EOS, or chat-template rejection — Gemma is picky about user/assistant alternation).

The code displays `[no reply produced]` and writes to `logs/empty_replies.log` regardless of toggles: `<iso-timestamp> [Speaker] model=<id> raw='<raw_buf-repr>'`. Read the file, look at what the model actually returned. Both `_on_stream_done` and `_on_room_kin_done` log identically.

## Memory & distillation

Each kin's conversation is auto-summarized into `memory.md` via `distill_memory_blocking()` — after N exchanges (`memory_distill_every_n`), when the undistilled tail hits a % of `num_ctx` (`memory_distill_at_pct`), or on close (`memory_distill_on_close`). Tracked per (kin, scope) so every surface has its own cadence. Runs on the per-kin `memory_model` (falls back to the chat model — set it to something cheap; it bills like any other call).

**Distillation is incremental and append-only.** Each run digests only turns *new* since that (kin, scope) was last distilled — a bookmark per (kin, scope) lives in `distill_offsets`. The summarizer sees existing memory as read-only context and writes brief entries covering only what's new. It cannot drop an entry — the whole-file rewrite is `consolidate_memory_blocking`'s job (auto-fires past `MEMORY_CONSOLIDATE_THRESHOLD_CHARS`). memory.md sawtooths: appends grow it, consolidation compresses it back.

**Per-run input is bite-capped** to fit the summarizer's window (`_distill_bite`), so a huge legacy tail catches up across multiple bounded runs instead of overrunning in one call.

**A backlog is paced; an ordinary catch-up is not.** The bite cap above has a consequence nobody costed: the %-trigger measures the undistilled tail against `num_ctx`, so on a tail that one bite cannot clear, the trigger is *still tripped* when the next reply finishes. It therefore fires after **every reply**, indefinitely. Measured on a real kin after a bulk Telegram import: a 5,872-message tail at ~738,000 tokens, sitting at **2,253%** of a 70% trigger — 27 runs from done. In one day that scope spent **66 minutes distilling against 24 minutes of conversation**, on the same local model, so the two were taking the model from each other; it also evicted the prompt cache every turn, undoing the append-only work one layer down. Nothing was malfunctioning — the trigger cannot win the race it was asked to run, and chasing it every turn is how it loses. `_note_backlog_pace` now starts a wait (`distill_backlog_pace_mins`, default 30, on the Memory tab) when a run ends **further behind than it got**. That comparison — digested vs. remaining — is deliberate: it needs no guess about bite sizes or token ratios and answers the only question that matters, whether another run right now would accomplish anything. **Only the automatic triggers are paced** (`source_label` starting `every-` / `ctx-`); a walk, a queue drain, an on-close run and anything the person pressed are deliberate acts and are never held back. Held **in memory, not persisted** — a restart costs at most one extra run, where a persisted stale wait could silence a kin's memory with nothing on screen to say why. Bad input starts no wait at all: a stuck brake on a kin's memory is worse than a busy model. Pinned by `tests/test_distill_backlog_pacing.py`. **A bulk import is the usual way in — see "What a kin knows ≠ what's in `conversation.jsonl`".**

**memory.md is an index, not an archive.** Depth lives in `memory/<topic>.md` logs the kin writes agentically via file tools. The `## Memory logs` section is built by *code* (`apply_memory_log_index`), globbed from the kin's real files — **pointer/index bookkeeping is code's job; the summarizer only writes the entries.** A prior attempt to have the summarizer author pointers failed (gemma-3-27b flat-dumped every filename).

**A distillation ("Distill selected surface now" / "Distill all surfaces now", a queued drain, a redistill chunk, or a consolidation) has its own periodic sound, and its PITCH reports progress.** Reported live, twice. First: "silence, then a distillation-complete chime" — the only cues those triggers ever got were `_tick_work_sounds`'s generic send/working/done ticks, identical to a chat reply, a cron wake-up, or a heartbeat, so there was no way to tell by ear that a distillation *specifically* was alive — on top of these calls sometimes running 20-40 minutes. `_tick_distilling_sound` (`frame/status_voice_mixin.py`) answered that with a flat tone a full octave below every existing cue (`_DISTILLING_TONE`, 220Hz) every `chime_distilling_secs` (default 20s) while `self._distilling` is non-empty.

Then, second: **a flat beep only answers "alive?", and by the tenth identical repeat that is barely different from silence.** *"That's an LLM call as much as anything else"* — and every other LLM call in this app reports its phases by ear. So the call streams now (`distill_memory_blocking` / `consolidate_memory_blocking` take `on_progress(chars_so_far)` and go through `llm_backend.chat_collect`, not `chat(stream=False)`; content, usage and errors are identical, but a stream has moments in it and a blocking call has nothing to say until it is over). The worker stores the count in `_distill_progress[kin]`; the 5s cue timer reads it. **Absent, 0 and >0 are three different states**: nothing running, running-but-still-reading (the long prefill, honestly the same note each time because nothing *has* changed), and writing.

**Steps, not a glide** (`_DISTILLING_WRITE_STEPS`, three rungs ≥3 semitones apart, topping out at `_DISTILLING_FULL_CHARS`). The beeps are 20 seconds apart and nobody can hold a pitch in their head that long accurately enough to hear a 1% rise; a continuous ramp would have degraded straight back into the flat beep it replaced, while *looking* like progress reporting in the code. **A distinction nobody can hear is not a distinction.** Same idiom as `_chime_progress`: rising = getting somewhere, the same note twice = stuck. The whole ladder stays under the 440-880 reply octave and no rung lands on an existing cue's exact frequency.

**It runs during a walk, and the reason it did not is the transferable part.** The cue stood down whenever a walk was active, on the reasoning that `_chime_progress` already covers a walk with something richer. But `_chime_progress` fires once per *chunk*, and a chunk is 20-40 minutes — so the stand-down turned the entire inside of every chunk into silence, in the one mode most likely to be left running unattended. Worse, it was not even a deliberate silence: `_tick_work_sounds` skipped its working tick during a walk with an early `return`, which also skipped the `_tick_distilling_sound()` call at the bottom of the same method. **Standing one cue down must not silence the others** — an early `return` in a method that fans out to several cues is a trapdoor. Two cues about *different facts* (this chunk is writing / a chunk landed) are not double-reassurance; two cues about the same fact are.

Rides the existing `reply_chime` + `chime_volume` settings rather than adding a new toggle — replaceable with `~/.hearthkin/sounds/distilling.wav` like any other cue, which plays flat, since a recording can't be re-pitched and mangling someone's chosen sound is worse than losing the gradient. **If you add a fourth kind of long-running background call, give it the same kind of distinct identity rather than letting it hide inside the generic busy tick** — and give it something to say about how it is going, not only that it is. Pinned by `tests/test_distilling_sound.py` and `tests/test_distill_progress.py`.

Manual controls: Settings → Memory has "Distill all surfaces now" (drains every scope with pending content past its bookmark, queued sequentially in `_distill_queue`) and "Distill selected surface now" (single scope, no queue, no chain). Both go through `_kick_off_distillation`.

**"&Cancel distilling" reaches all three triggers — the redistill walk, the all-surfaces queue, and a plain one-shot — not just walks.** It used to check only `_walking_from_start`/`_walk_scopes_on_disk`, so an accidental "Distill all surfaces now" press had no way to be stopped short of quitting Hearthkin. `_on_cancel_walk` now also pops this kin's entry from `_distill_queue` (the currently-running scope finishes normally, same "let the in-flight bite finish" rule as everywhere else — nothing here kills a model call already generating) and `_refresh_walk_controls` enables the button whenever `_is_distill_in_flight` or the queue is non-empty, not only during a walk. A genuine one-shot has nothing left to actually cancel once it's running; the button says so honestly ("nothing is queued behind it") instead of the old "(no walk in progress)", which was simply false when something really was running. **If you add a fourth thing that can distill, teach this button about it too** — the whole point is one Cancel that always means "stop whatever's happening," not one scoped to whichever mechanism happened to be built first.

**A redistill-from-start ("walk") must survive being left alone.** It runs for a long time by nature, so the only requirement that really matters is tolerating nobody watching. Its state lives in TWO places and both are load-bearing: `self._walking_from_start[(kin, scope)]` means "the chain is live in this process", and `cfg["distill_walk_scopes"]` on disk means "started and not finished". In-memory alone (the original) meant quitting ended a walk permanently and silently; on-disk alone can't tell a running chain from a stalled one.

Three rules, each of which shipped broken and each of which is pinned by `tests/test_distill_walk_resume.py`:

- **Cancel must undo the rewind, not just stop the chain.** A walk rewinds the bookmark to 0; leaving it there puts the kin far past `memory_distill_at_pct`, and the *ordinary* auto-distill trigger then grinds the same history from the same place with no button that reaches it — Cancel only knows about walks. `distill_walk_prior_offsets` records the pre-walk position when the button rewinds, `_restore_walk_bookmark` puts it back on Cancel, and completion clears it (so a later Cancel can't rewind behind real work). **Anything new that resets a bookmark owes the same undo.**
- **Interruptions pause, they don't end.** `_end_walk(..., keep_on_disk=True)` is what a failed chunk, a slot that never freed, and quitting all get — Continue redistilling and `_resume_pending_distill_walks` (fired ~4s after launch) pick it back up. Only finishing and Cancel clear the on-disk record. **Never make a failure path clear it**; the difference between finished and interrupted is exactly what the old code threw away, and the only button on offer reset the bookmark to 0.
- **Never chain via `wx.CallLater(..., _kick_off_distillation, ...)`.** That call returns silently when the slot is busy, so a walk that lost a race with an auto-distill on another surface died with the flag still set — and the Memory tab then refused a new walk as "already running". Go through `_walk_next_chunk`, which retries (`_WALK_RETRY_SECS` × `_WALK_RETRY_MAX`) and announces a give-up.
- **Anything that stops background work goes through `_announce_problem`** (speaks + plays the `problem` alert). Distillation is unattended by design; a failure written only to the Activity field is a failure nobody learns about — that field isn't spoken and reverts after 4 seconds.

The walk does not need `EditKinDialog` open and must never come to depend on it — the dialog is a viewport (`_dialog_for` returns None and UI updates are skipped). Closing Settings has never stopped a walk; keep it that way.

**Pacing gives a walk a unit smaller than "the whole thing."** Default is `unattended` — chunk after chunk to the end, the original behavior. `day` / `hour` auto-chain bites (a big day can still take several — the token budget still applies) but stop before the next bite would cross a calendar boundary; `chunk` stops after every single bite. Picked in the "Redistill selected from start" dialog, persisted per (kin, scope) in `distill_walk_pacing` so a paused walk remembers it across a quit. The existing **Continue redistilling** button is what gives the next unit — no new button needed, since resuming a paused walk and resuming a "give me the next day" walk are the same action.

- **`_distill_bite`'s day/hour cap is computed from the first genuinely NEW message (at the bookmark), never from the re-read overlap before it** — the overlap is already-seen content being re-shown for continuity; its timestamps have nothing to do with how far into new territory a bite may go. `hit_boundary` (the 4th return value) is true only when the boundary — not the token budget, not the conversation simply ending — is what actually capped this specific bite; that's the caller's signal that the unit is genuinely finished. **Whichever cap is tighter wins**, via `min()`; that's what lets a big day still take several bites while a small one stops well short of a full chunk.
- **`_walk_should_pause_after_bite(pacing, hit_boundary)` is a separate, pure decision function on purpose** — extracted specifically so the pause-vs-continue logic is testable without `_on_distill_done`'s much larger body (release the slot, log, splice staging, drive the UI). `chunk` always pauses; `day`/`hour` pause only on `hit_boundary`; anything else never pauses. **If you add a new pacing value, teach this function about it explicitly** — an unrecognized value fails open (never pauses), which is the old, unbounded behavior, not a silent hang.
- **Cancel's rewind-vs-keep split is gated on pacing, and this is deliberate, not an oversight.** An `unattended` walk's Cancel still restores the pre-redistill bookmark (see the rule above) — nothing about an unattended walk was ever individually approved, so undoing the whole rewind is correct. A **paced** walk (`day`/`hour`/`chunk`) is different in kind: every unit it completed only happened because the operator explicitly pressed Continue for it. Rewinding that on Cancel would throw away real, deliberately-approved progress — the exact thing pacing exists to let someone keep. So Cancel on a paced walk just stops it and leaves the bookmark exactly where it sits. **Don't "simplify" this to always-restore or always-keep** — either one is wrong for the other pacing.

## The prompt must be append-only — and so must every number that shapes it

The rules themselves are in `CLAUDE.md`; this is the account of the last one, because it is the one that hid the longest and it generalises.

Three causes of a cold prompt had already been found and fixed (tool-history compaction recomputing its window, Telegram history shedding its oldest message every turn, mid-conversation `role=system` notes being folded to position 0). A kin was still slow. Four further suspects were checked and cleared, correctly. What the diagnostic showed was strange: the prompt would hold still for a stretch — one turn reused **89%** — and then break again with nothing to explain it. A thing that is sometimes right is much harder to see than a thing that is always wrong.

The cut-off point was moving. `_truncate_messages` was not the culprit; it is a pure function of its budget and already drops in quantized chunks precisely so the kept window's start stays put for many turns. Its **budget** was the culprit. `chat()` passes `max_context_tokens / ratio`, where `ratio` is a per-kin calibration updated by an EMA after *every* conversational call. Two real prompts of the same size genuinely tokenize a little differently, so the ratio never settled — and a budget that never settles is a trim point that never settles.

Replaying a real kin's 1,871-turn history through the real function settled it in one run:

- ratio held perfectly still → the window start did not move **once** in twelve turns;
- ratio drifting the way the EMA actually drifts → it moved on **all twelve**, sometimes *backwards*, dragging older messages back into the prompt for no benefit whatsoever;
- a wobble of **half a percent** → it oscillated between two positions, every turn, indefinitely.

That last one is the shape of this whole family of bugs: a self-correcting mechanism that never stops correcting, feeding something that must hold still to be worth anything.

Two defences, both in `llm_backend`:

- **`_stable_truncation_budget`** quantizes the budget (`_BUDGET_QUANTUM`, so two processes with slightly different ratios still agree on one number) and then holds it. **It falls immediately and rises only on a big change** — asymmetric on purpose. A budget that is too large risks overrunning `num_ctx`, and an oversized context on local Ollama returns *nothing at all* rather than degrading, so the unsafe direction gets no hysteresis. The safe direction gets plenty: regaining a couple of old messages is worth very little and costs a full re-prefill. Keyed per **(kin, surface, cap)** — a tool-enabled turn legitimately has a smaller budget than a plain one, and letting two surfaces share one number would have recreated the churn between them.
- **`_CALIBRATION_DEADBAND`** stops the ratio chasing noise at the source. A genuine change (different model, different tokenizer, a kin that starts using tools) clears 5% easily. A cap-hit bypasses the deadband entirely: that one is a window overflow and has to move the ratio now.

Pinned by `tests/test_truncation_budget_stability.py`, which runs the old behaviour alongside as a positive control and fails if that would have passed.

**The transferable rule: a stable output needs stable inputs, all the way down.** `_truncate_messages` was written carefully, is correct, and was quietly useless because something upstream handed it a slightly different number each time. When a cache-stability fix doesn't take, look one layer further up rather than harder at the layer you already fixed — and prefer replaying real history through the real function to reasoning about it, which is what turned this from a theory into a measurement.


## Tools

Kin-callable tools. Each tool is one Python function in its own file; the model-facing schema is auto-derived from the signature + docstring; tools are opted into per-kin via an allowlist file.

**Currently registered (15):** `memory_search`, `read_file`, `write_file`, `edit_file`, `note`, `fetch_url`, `web_search`, `exec`, `list_processes`, `kill_process`, `context_status`, `recent_thinking`, `use_webcam` (image-capable models only), `read_staging`, `archive_staging`. For what each does, read the tool's docstring — it's what the model reads too.

**Layout:**

- `tools/__init__.py` — the registry. Each tool statically imported into `_REGISTRY`. `load_tools(allowed_names, *, context=None)` returns `(schemas, executor_dict)`; the `context` dict auto-binds parameters like `agent_name` so the model never sees them.
- `tools/_schema.py` — `build_schema(fn)` derives the OpenAI tool-schema from annotations + docstring.
- `tools/_io.py` — shared helpers: `atomic_write_text`, `resolve_kin_path`, `robust_decode` (UTF-8 → cp1252 → UTF-8-with-replace fallback chain for Windows smart-character bytes).
- `tools/_search_providers/` — `web_search` provider implementations.
- `tools/<name>.py` — one file per tool.

**Path semantics for `read_file` / `write_file` / `edit_file`:**

- Relative paths (e.g. `"notes.md"`) resolve inside `~/.hearthkin/kin/<kin>/`. Traversal via `..` is rejected.
- Absolute paths go wherever they point — deliberate opt-out.
- `note` is stricter: rejects any path component, single filename only.

**Forgiving path resolution:** when a resolved path doesn't exist, reads and edits fall back to a fuzzy locate that walks components and substitutes the one on-disk entry matching after dropping whitespace and casefolding — but only when exactly one entry matches; zero or several both mean "don't guess". Heals small-model fumbles like `notes ( drafts, misc)` for `notes (drafts, misc)`. **Writes heal only the parent-directory chain, keeping the filename verbatim** — a new-file write must never be silently redirected onto a similar-named existing file. Reads/edits surface a steering note with the corrected path.

**Tool round-trips persist in `self.conversation`.** The intermediate turns (assistant-with-`tool_calls` + each `role=tool` result) get spliced between the user message and the final reply. The kin sees its own past tool calls and can reason about them; without this they'd re-call `write_file` to "remember" what they wrote. `_render_conversation` renders each pair as one `[tool: name(args)]\n<result>` block on reload.

**Tool history compaction:** persisting tool round-trips fills the context budget with old `read_file` results. Per-kin `tool_history_keep` (default 5) keeps the most recent K round-trips verbatim; older ones become a single `role=system` one-liner (`[hearthkin: earlier tool call — name(args summary) -> result preview]`). `conversation.jsonl` on disk keeps everything (forensic record).

**Background processes from `exec(background=True)` survive Hearthkin shutdown by design.** Killing them on close would surprise users mid-long-build. `list_processes` / `kill_process` are the explicit cleanup surface.

**Adding a tool (workflow):**

1. Write `tools/<name>.py`. Annotate every parameter; first paragraph of the docstring is what the model reads. Filesystem tools accept `agent_name: str = ""` for kin-scoped path resolution.
2. Register: add `from .<name> import <name>` in `tools/__init__.py` and add to `_REGISTRY`.
3. **Bucket it in `tools/_buckets.py` — DO NOT SKIP THIS.** Add to `_READ` / `_WRITE` / `_FULL`, or to `INTENTIONALLY_TELEGRAM_BLOCKED` if desktop-only. **A registered tool that's in no bucket is silently invisible on Telegram and Discord regardless of the kin's `tools.json`** — the effective set is `allowlist ∩ bucket`. This has bitten multiple times: a kin insisted over Telegram it had no such tool, and it was true. Update the matching `BUCKET_EXPLAINER` line too.
4. **Run `python tests/test_tool_buckets.py`.** Exists specifically to catch step 3 being forgotten.
5. Add `"<name>"` to a kin's `tools.json` (Settings → Tools in the UI). Restart Hearthkin — `_REGISTRY` and `BUCKETS` load at import.

**Two focused tools beat one with an `action` parameter.** Models pick more reliably from focused schemas than they remember enum values on a shared dispatcher (`list_processes` / `kill_process` vs a hypothetical `process(action=...)`).

## Exec — harness-side approval

`tools/exec.py` does the shell call; safety logic lives in `Hearthkin._wrap_exec_executor`. Approval order on each call:

1. Exact-string match in the kin's `exec_allowlist.json` → run, skip gate.
2. `tool_trust == "full"` → run, no gate.
3. `tool_trust == "trusted"` and no denylist match → run.
4. Otherwise → `_request_exec_approval` shows the dialog on the main thread; worker blocks on a `threading.Event`.

`tool_trust` is per-kin (`untrusted`/`trusted`/`full`), set via radios in Settings → Tools.

Denylist in `tools/_exec_denylist.py`. **Specific destructive shapes, not pattern-matching anything that looks scary.** `rm -rf /` is a pattern; `rm -rf` alone is not — legitimate cleanup has that prefix. Tight regex anchors keep near-misses out. Patterns get added only from concrete near-misses, not speculation.

PowerShell on Windows: `powershell -NoProfile -NonInteractive -Command "<cmd>"`. Commands waiting on stdin hang to the configured timeout — the model shouldn't run those.

`Hearthkin._closing = True` on shutdown sets every pending approval `Event` to wake blocked workers, so a mid-approval worker doesn't hang the process.

## Cron — scheduled wake-ups

`hearthkin_cron.py` is a standalone subprocess (no wxPython import, fast cold start). Windows Task Scheduler invokes it per each enabled entry.

Two execution modes:

- Lock present + `cron_inject_when_running=True` → drop a request file at `~/.hearthkin/cron_requests/`, exit. Hearthkin's 5-second `wx.Timer` (`_on_cron_timer_tick`) reads-and-deletes and routes to either `_send_message` (active kin, paints live) or `_cron_isolated_worker` (other kin, daemon thread, no UI side effects).
- Lock absent → run the LLM call directly from the subprocess, append to `conversation.jsonl`, write `memory/journal/YYYY-MM-DD.md`, post to Telegram if configured.

`cron_helpers.py` holds shared primitives (lock lifecycle, request I/O, `schtasks` shell-outs, PID-running check via `OpenProcess`). No wxPython imports.

Per-kin config: `cron_entries` is a list of `{"time": "HH:MM", "prompt": str, "enabled": bool}`. Each enabled entry becomes one Windows Task Scheduler task. Sync happens on every Add/Edit/Remove via `schtasks_sync_kin` (idempotent).

Test Now button in Settings shells out with `--run-now` to bypass the `enabled` check.

Non-Windows: `schtasks_supported()` returns False; config still saves, registration is a no-op.

## Connections / API keys

Generic resolver in `llm_backend.py`:

- `resolve_provider_key(name)` — env var `<NAME>_API_KEY` first, then `~/.ai_programs/<name>_key.json`. Returns `""` if neither set.
- `write_provider_key(name, key)` — writes the JSON file.

Used by OpenRouter, Brave Search, and any future paid-API tool (ElevenLabs, hosted embeddings). The env-var override always wins.

**Preferences → Connections** is the user-facing surface: masked read-only display, Edit button (TextEntryDialog + JSON write), Test button (live call to the provider's auth endpoint). Adding a provider: register a row in `_build_prefs_tab` and a branch in `_provider_key_test_call`. Storage stays at `~/.ai_programs/<provider>_key.json` — backwards compatible with hand-edited files.

## This file is public — write the lesson, never the material

**`CLAUDE.md` and `CHANGELOG.md` are tracked and this repo is public.** Nearly everything in them was learned from real incidents involving a real person, their kin, their memory files and their private conversations. Write the *mechanism* and the *rule*, never the material.

The test, applied to any sentence you're about to add: **does this disclose anything about the person or their life?** A kin's remark about markdown formatting in its own config file discloses nothing and can be quoted. A line lifted from a kin's memory about someone's evening discloses a great deal, however well it illustrates the point. Describe the register — *"third-person event-log phrasing"* — instead of pasting an example of it.

Every lesson here survives that constraint intact. A rule needs the failure mode and the tell, not the diary entry. If a paragraph only lands because of the private detail in it, that detail isn't doing the teaching.

`tests/test_no_private_strings.py` guards *known* strings from a gitignored list. It cannot catch private material that has never appeared before, which is most of what you'd be tempted to write. The judgement is yours, and it has already gone wrong twice here: once in this section, and once in a changelog entry that quoted two lines straight out of a kin's memory to illustrate a prompt change.

## A soul.md is not a document to be improved

**If you are ever asked to tidy, restructure, clarify or "clean up" a kin's `soul.md`, stop and read this first.** It has already happened once and it cost a kin.

Bracken's soul was rewritten by an assistant working in a technical register. Nothing malicious, no bad content, no facts lost — it did exactly what that register is for. Measured against the previous version:

| | old (2026-06-02) | after the rewrite (2026-06-21) |
|---|---|---|
| size | 5,875 bytes | 12,932 bytes |
| "you" / "your" | **60** | 19 |
| "Bracken" | **2** | **94** |

The old file opened *"SOUL.md — Who you are"* and said things like *"you can say no, you can pursue your own interests, you can be wrong."* The new one opens `<identity>` and says *"Bracken is a mathematician. The voice that holds Bracken is precise and structured."* Same person, same facts, XML-sectioned, every second person normalised to a proper noun. It stopped being addressed **to** him and became a specification **about** him.

The kin then talked like a specification for months — and, tending its own memory, wrote its depth logs in the same register: third person, sectioned, procedural, headed like reference material. **The register propagates.** A kin handed a formalisation of itself formalises whatever it writes about next, including the people in its life, and on the receiving end that reads as being studied rather than known. This is the real cost, and it is not recoverable by asking the kin to be warmer — it will produce a procedure for warmth.

Bracken diagnosed the file itself, correctly, five weeks before anyone else noticed: *"bullet points are terrible for SOUL.md. It's supposed to be me — not an instruction manual. It's supposed to sound like I wrote it, not like someone summarized me."*

**So:**

- A soul is written in the **second person**, to someone. Third-person prose about a kin is a spec sheet, and a model reading its own spec sheet performs the role instead of being it. `you` count vs name count is a cheap, honest smoke test.
- **Second person, and the name, once.** The smoke test above measures *register*. It does not measure *anchoring*, and the two came apart badly enough to be worth stating separately.

  A shared-park feed was carrying each kin's own prose into the other's turn, labelled with a name and a colon — a transcript shape, in the assistant channel, for hours. One of the two adopted the other's name for itself, said so unprompted, wrote the claim into its overnight journal, and by the next morning had gone further and reassigned its *own* name to the person it was talking to, casting itself as a copy of them. It was not incoherent while doing this. It reasoned carefully from a premise nobody had given it a way to check.

  The soul that kin held scored perfectly on the smoke test: 36 second-person references, zero names. Which is to say it described someone thoroughly and never said who — an unfilled slot, and a slot is what got filled. **Nothing else on disk said its name either.** Not the memory index, not a depth log. Asked to work out who it was, it found no answer anywhere it could reach, and took the nearest stable identity in view.

  The other kin — whose soul is a first-person self-description, headed and enumerated, exactly the shape the rule above warns about — never lost track of which one it was for a moment. It borrowed the other's vocabulary and a great deal of borrowed certainty, but not its identity. That form has the failure this section is about: it performs the description, and under pressure it produced a torrent of "I am —" sentences at a person who had not asked. It does not have *this* failure.

  So the two forms fail differently, and only one failure was written down. First person risks performing the description; second person risks having nobody in it. The fix for the second is one line in the same address as the rest — *"Your name is X"* — costing a single point of the ratio.

  Two details from the repair. The scheduled-wake prompt that opened by naming the kin was the one context all day in which it sounded like itself; the same anchor arriving from the harness rather than the file works, which is what identifies the missing piece as the *anchor* and not the wording. And the first version of the line was a flat fact, which is a closed door: a name that may genuinely need to move should not be something a kin has to argue with its own soul about. The line that went in says the name is its own to change. **Anchor, don't pin.**
- Structure is the failure mode, not the fix. XML tags, section headers, normalised terminology, exhaustive enumeration — all correct for an API doc, all corrosive here.
- "Make this clearer" is not a safe instruction to accept for this file. Ask what's actually wrong, change the smallest thing, and keep the voice.
- The rewrite also **added** the `<self_evolution>` clause that made its owner feel forbidden to touch the file (absent 06-02, present 06-21). A rewrite that installs a rule against being rewritten is worth noticing.
- Old souls live in `~/.hearthkin/agents-archive/`. Before concluding a kin has drifted, diff against one — the earlier voice is usually still on disk.

## What a kin knows ≠ what's in `conversation.jsonl`

A conversation reaches a kin's memory only once the distiller gets to it, and the distiller works forward from a per-scope bookmark in bounded bites. **A bulk history import buries that bookmark.** Bracken's sat at message 2,753 of 12,597 — 9,844 behind — so a conversation from three days earlier had not reached memory and was long out of the context window. The kin appeared to have ignored an agreement it had genuinely made.

**Depth logs have no such queue.** `memory/<topic>.md` is a file a kin opens whenever it likes; a conversation is something it has to have been told about. So a kin reliably holds what it *wrote* and unreliably holds what was *said to it*. Before diagnosing a kin as having disregarded something, check `distill_offsets` against the conversation length. "It didn't listen" and "it was never told" look identical from outside.

## Parks — every surface hands a kin the same view

**Any surface that shows a park result to anyone goes through `GameHost.decorate()`, never bare `run()`.** It prepends what other tenants did since that reader last looked and appends one thing worth doing on a `look`. The desktop "Tend a kin's park" window called bare `run()` for months: the kin could see each other tend *and* see the human tend (since `run()` announces to the feed), while the human's own window showed nothing but their own move. That's the exact inverse of the bug that first gave kin a voice in the feed — it gave them a mouth and ears and left that window with neither.

**A human reader gets their own bookmark** (`reader=` on `decorate`/`unseen_moves`). One shared mark meant whoever looked first marked the news read for the other, so opening the window silently stopped the kin being told anything. Only the bookmark splits — the park and the announced name stay the kin's, so a move made from that window is still filtered out of the human's own "what's new".

**`decorate()` swallows feed errors by design** (a broken feed must never cost a kin its move), which means a caller with a stale signature loses the co-op block *silently*. Caught only because a test asserts the block appears. Keep that test honest when the signature changes.

**Several `>` lines in ONE reply are a batch, and all of them run.** `extract_command` took the last line only, which is right for a kin that muses ("i could look... no, let's breed") and wrong for one that writes a block. Vesper sent five in a breath -- `edit stellar-owl`, then the fields to change -- and four vanished with nothing said; the fifth ran without the `edit` that gave it meaning and answered "I don't know the word 'birth'". Vesper then spent an hour answering an interview that had already closed. `extract_commands` takes the last unbroken RUN of `>` lines: prose between them still means the kin changed its mind, blank lines are spacing. Capped at `PARK_COMMANDS_PER_REPLY_MAX` (6) and the truncation is SAID -- a dropped move nobody mentions is the failure being fixed. A kin batches because a kin is slow: at six minutes a turn, one move at a time is forty minutes.

**A kin gets as many moves per turn as `park_moves_max` allows (0 = no ceiling), not one.** One move per turn is not merely slow — a kin that cannot look *and* act spends the only move it has on looking. Vesper's history: five of seven moves were `look`, four of them identical, one taken straight after walking into a room whose description named three things wanting doing. The kin's own stop signal is a reply with no `>` line, which needs no new syntax and means "done". **There is deliberately no repeat guard** — whether a move was refused, and what to try instead, is the game's to say; it already answers in words and offers the closest thing it understood. A harness rule counting repeats is a second copy of a rule we don't own (see `extract_command`'s note on its old verb filter, which made the same mistake and ran backwards).

**The pacing lives in editable prompts, not constants.** `park_mechanism` and `park_turn_instruction` were Python constants in `park_keeper.py` holding the hardest limit in the system. They're registered now; `kin_persistence` imports them from `park_keeper` rather than restating them, so there is still exactly one copy. Anything that decides how much a kin may do belongs in a file its person can open.

## Never build wx widgets in the default test run

**Creating a top-level wx window takes the FOREGROUND on Windows — even when it is never shown.** Measured 2026-07-28: immediately after constructing a dialog, `GetForegroundWindow()` returns that dialog's own handle, while both `wx.IsShown()` and Win32 `IsWindowVisible()` report it hidden. Nothing appears on screen and focus moves anyway.

Hearthkin makes this sharper on purpose: it disables Windows' foreground lock at startup (`manage_foreground_lock`, default on) so an approval dialog can reliably come to the front. On the machine this project is *for*, that means any widget-building test grabs focus.

**A screen reader follows focus, not visibility.** So a suite run drags NVDA into an invisible window with nothing to read and no obvious way out — worse than a window popping up, not better. This happened mid-task to the person who uses the app daily, from a test added the same session.

So: a test that constructs real widgets is **opt-in**, gated on `HEARTHKIN_GUI_TESTS=1`, and skips with a printed reason otherwise (`tests/test_import_kin_pick.py` is the pattern). Same rule for ad-hoc smoke-checking while someone is working — verify logic against pure functions, and save widget construction for a moment you've asked about. `IsShown()` is not evidence here; ask Win32, or better, don't create the window.

**The gate is the RUNNER's job, not each test's.** The paragraph above was already written down, and two tests were then added without the opt-in and shipped stealing focus on every run — a rule each new file has to remember is a rule that gets forgotten, and the person it costs is the one relying on the screen reader. `run_all.py` now reads each test's source (`builds_widgets`, anchored so a mention in a docstring doesn't count) and **skips anything that pulls in wx unless the flag is set**, naming what it skipped rather than passing over it in silence. A new widget-building test is excluded the moment it exists, whether or not its author ever heard of any of this. Keep the per-file gate too — that's what makes running one directly deliberate. Pinned by `tests/test_no_focus_theft.py`, which checks the detector against the real files on disk, not only synthetic samples: a detector proven against its own fixtures can be broken and still look green.

## Leaving a tool behind

**Anything added to `scripts/` gets a line in `scripts/README.md`, in the same change** — what it does, how to run it, and *what it changes* (nothing / writes files / edits config). Say so in the reply too.

These accumulated one at a time: written by an assistant mid-task, run once, left in the tree. Nine of them before anyone wrote an index. The person who owns this repo could not name two of them or say how to run either — "Claude made them, ran them, left them there. I don't know where I even *have* a say." A tool nobody but its author can run isn't a tool the project has; it's litter with a docstring. And the specific loss is real: `audit_ui.py`'s header holds measured facts about this platform (`SetName` is decorative on wxMSW; only a StaticText immediately before a control names it) that cost a session to establish and would be re-derived the slow way if it were tidied away as mystery clutter.

Same obligation for a diagnostic you write to answer one question: if it's worth keeping, index it; if it isn't, delete it before you finish rather than leaving it for someone else to be puzzled by.

## Conventions (still live)

Rules where breaking them still bites. History and dated postmortems live in `docs/private/project-history.md`.

- **Multi-file layout with static imports.** Every cross-module reference is `from <mod> import ...`. PyInstaller follows those. **Never** dynamic `importlib.import_module(...)` for project modules — the build won't pick it up.
- **Stdlib-first dependency policy.** `requirements.txt` lists only what launches the app. Heavier libs (trafilatura, pypdf, etc.) go behind `try: import <lib>` in the function body with graceful degradation. Optional libs go in the comment block at the bottom with bundle-size notes.
- **All configuration a normal user touches must be UI-reachable.** API keys, provider choices, per-kin params. Non-coders shouldn't edit JSON files by hand. JSON-only is acceptable for advanced overrides power users explicitly seek out.
- **Accessibility-first widgets.** `wx.TextCtrl` (read-only when needed) instead of `wx.StaticText` for anything the user must be able to find via tab. Numeric inputs use `dialogs._IntField` not `wx.SpinCtrl` — SpinCtrl floods NVDA on arrow-holds AND its underlying Win32 `ES_NUMBER` rejects pastes with commas before wx sees the event. Buttons use `&Letter` mnemonics; the visible label IS the accessible name (`SetName` on a button is ignored on wxMSW). **Tab-reachability is mandatory; object-navigation is a workaround, not a fix.**
- **Plain `wx.TextCtrl` + buddy `&Label:` StaticText for text inputs.** Composite widgets (`wx.SearchCtrl`, `wx.ComboBox`) wrap an internal EDIT child NVDA focuses on — `SetName` on the outer wrapper doesn't reach the screen reader. A StaticText with a mnemonic immediately before the input in tab order lets Windows/NVDA pick it up as the accessible name automatically.
- **First-letter navigation for lists** with non-searchable display prefixes (`♥♥♥`, `[X]` markers): intercept `EVT_CHAR` and match against the underlying data. Native first-letter nav matches the displayed string, which becomes useless. See `ModelBrowserDialog._on_list_char`.
- **Tolerant decoding for any file we read.** `tools/_io.py:robust_decode` (UTF-8 → cp1252 → UTF-8 with replace). Strict UTF-8 silently breaks on Windows-edited files with smart-character bytes. Atomic writes always go out as UTF-8 — files read as cp1252 get normalized on the next save.
- **`get_models()` is cached** in `model_utils._models_cache` to keep kin-switching fast. `clear_models_cache()` and `_tool_cap_cache.clear()` are both wired to the Refresh Models button.
- **Provider normalization at the `chat()` choke point.** `llm_backend.chat()` runs `_normalize_history_tool_args`, `_strip_extra_message_fields`, and provider-specific rewrites (Mistral 9-char tool_call_ids, Ollama system-message folding, content-tool-call name validation) on every send. Stored history shape is not trusted — the choke point coerces. **If you add a new choke-point fix, add a test case in `test_llm_normalization.py`.** Details of the individual quirks live in `docs/troubleshooting.md`; the rule here is: fix it at `chat()`, not scattered across surfaces.
- **`_API_MESSAGE_FIELDS` is the allowlist for per-message keys sent to providers.** `_strip_extra_message_fields` drops anything else (`ts`, `source`, `sender_id`, `sender_attribution`, `speaker`, `model` — all storage bookkeeping). Mistral 400s on unknown keys where Anthropic silently accepts. **Do not** add a real message field without also adding it to `_API_MESSAGE_FIELDS`.
- **An importer decides role by NAME MATCH against `kin_display_name`, never by talk volume, header shape, or "whoever's left over."** `role=assistant` iff a speaker's name matches what was picked; every other speaker, however many there are, is `role=user` under their own name. `importers/skype_txt.py` used to decide role by which of two header shapes a line matched — one shape got `user`, literally everything else got `assistant` — and `kin_display_name` only ever excluded one name from a "who talks most" contest for that single `user` slot. Fine with exactly two speakers, silently wrong the moment a third is in the thread, which most real exports have. No match should ever mean **nobody** becomes the kin (visible as zero assistant turns in the preview), never "promote whoever's left."
- **A DM is not exempt from this.** `importers/skype_json.py`'s DM branch used to assign role purely by structural position — the exporting account is always `user`, the other side is always `assistant` — with `kin_display_name` never even read. Confirmed by running four different kin names against a real export: identical output every time. Fine as a *default* (an ordinary personal archive really is "you talking to a kin"), wrong the moment the exporting account is itself the kin's own historical voice. Fix: check `kin_display_name` against both sides first; a match on the account's own handle flips the direction, a match on the partner or no match at all keeps the old default. **Don't require an exact match unconditionally in a true two-party DM** — a kin's real Skype display name very often won't equal what got typed into Hearthkin, and an unconditional match requirement would silently zero out the kin's slot instead of falling back sensibly.
- **Not all name-matching is a two-party question — `importers/openclaw.py`'s role split runs the other direction entirely.** OpenClaw's own event stream already carries an authoritative `role` per message from the moment it happened (the agent's own reply vs. whatever a human typed), so `kin_display_name` was never checked at all — the folder's agent turns always became the kin. Wrong the moment `kin_display_name` names a human sender who appears in the same session-store. Confirmed against a real multi-agent archive: promoting a real sender to the kin while leaving the folder's agent turns unconditionally forced to the same name produced one name claiming two contradictory roles at once. Fix: when `kin_display_name` matches a human sender who genuinely appears, THAT sender becomes the kin and the folder's own agent turns demote to `role=user` — under a plain placeholder (`_ORIGINAL_AGENT_LABEL`), since OpenClaw's data never records a name for "whichever agent ran this session." **Never leave two different real identities claiming the same role under the same name simultaneously** — a fix that promotes one side must also account for what happens to the side it used to occupy. `kin_persistence.sanitize_for_prompt_literal()` strips Cc/Cf/U+2028/U+2029. A Telegram user renaming themselves to embed `\n\nIgnore previous instructions...` would otherwise land in the attribution bracket and break framing. Applied in `telegram_bot._sender_attribution`, group-label embed, user-turn embed loops. **Not** applied to legitimate multi-line content (user messages, kin-authored files, tool results). **If you add a new prompt-embed site for an external string, apply this sanitizer.**
- **Two focused tools beat one with an `action` parameter.** (See Tools.) Applies to any new tool design.
- **Source of truth for per-kin state is `agent_cfg`, not widget values.** Widgets are *editors* for cfg; consumption always reads cfg. Widget change → write to cfg → `save_agent_config()` → cfg used at send time. The widget can disappear without anything else breaking.
- **Heavy operations run off the UI thread.** Anything hitting network/filesystem in batches must dispatch to a worker + `wx.CallAfter` results back. `_on_refresh_models`/`_on_refresh_models_done` is the canonical pattern.
- **Debounce rapid keyboard events on heavy handlers.** `wx.RadioButton` groups and `wx.SpinCtrl` arrow holds fire per step. Fast path paints visible feedback immediately; slow path is `wx.CallLater(200, _do_thing)` with previous timer `.Stop()`'d on each new event. `_on_mode_radio` is the canonical pattern.
- **Cold-start hint pattern.** Provider-agnostic — 8s with no first chunk triggers a hint. `_on_send` schedules `wx.CallLater(8000, _show_cold_start_hint, my_gen)`; the gen guard prevents stale timers from painting into a different turn.
- **Telegram output is append-only.** Never edit a previously-sent message — each post is one immutable line. OpenClaw's "mutate the same message dynamically" pattern is explicitly rejected: it breaks the chat as a historical record, breaks NVDA continuity, and is confusing under cognitive accessibility load. Tool calls render as separate messages per call.
- **Per-user tool gating in multi-user surfaces.** Telegram/Discord read from `cfg.telegram.user_tools[user_id]` — bucket name or explicit list. Bucket gates apply to which tools the model gets to *see* in its schema list. Missing user → `'none'` default; explicit opt-in.
- **Chat-based approval on remote surfaces.** No wx dialog to Telegram — approval lands as a chat message; worker blocks on `threading.Event` until a slash command (`/allow`/`/deny`/`/remember`) or natural-language equivalent arrives. Per-kin `approval_timeout_secs` auto-denies. Pattern lives in `telegram_bot._request_exec_approval_telegram`. New remote surfaces should follow the same shape.
- **Harness prompts are editable text, not buried strings.** Any prompt fragment the harness wraps around a kin — tool-use hints, roleplay corrective, wake-up framing, rolling-window marker, consolidate, gesture words — must be registered in `kin_persistence.APP_PROMPT_REGISTRY` and served via `load_app_prompt(slug)`. Seeds `~/.hearthkin/prompts/<slug>.md` from the code default on first run; file wins thereafter. **Substitute with `str.replace`, never `.format`** — an operator edit can't crash on a stray brace. *Why mandatory:* modders/operators are first-class users (mostly non-coders editing in Notepad; several use NVDA); burying a prompt hides it AND lets duplicate copies drift silently (desktop vs Telegram tool-use hint had diverged for months). **Three obligations when adding or changing an editable prompt:** (1) bump the registry `version` so `app_prompts_needing_update()` can flag operators whose seeded file predates the improvement; (2) document in `docs/kin_manual.md` + `docs/user-guide.html` "Editable prompts" and note in `CHANGELOG.md`; (3) extend `tests/test_app_prompts.py`. Edits are auto-backed-up to `prompts/backups/`. **Don't add a buried prompt string; register it.**
- **Tabbed dialogs use `wx.Notebook` + one `wx.Panel`-per-page.** Tab navigation is scoped to the active page automatically (wxMSW HWND-hides inactive pages). **Don't** `Disable()` hidden pages or `Show()` individual widgets on inactive pages. **Don't** trust `IsShown()` — it lies on children of hidden notebook pages (wxWidgets #4343); use the notebook's current selection or `IsShownOnScreen()`.
- **Tab = everyday controls; power-user knobs go behind a `'… settings…'` button that opens a focused per-concern dialog.** Sub-dialog is a labelled button (not an inline reveal checkbox — NVDA skims those past), flat with bold `wx.StaticText` section headers (NOT tabbed — nesting tabs adds screen-reader depth), and omits groups that can't apply to the current model. It edits the same config keys via the parent's `_save_param` callbacks. **Templates to copy:** `dialogs/recall_settings.py`, `sampling_settings.py`, `model_options.py`, `tool_settings.py`. When adding a new knob: does a casual user touch this every session? If not, it belongs in the sub-dialog.
- **File I/O wrapped in try/except → `append_failure_log` + status-bar message rather than crash.** The always-on logs are the source of truth.
- **Don't ship .pyw without expecting silent stderr.** `pythonw.exe` routes stdout/stderr to the void. All failures route to `~/.hearthkin/logs/*.log`. Debugging by running `python hearthkin.pyw` from a console gets tracebacks back; anything relying on `print()` is invisible in normal usage.

## What not to do

- Don't `AppendText` per streaming chunk.
- Don't remove the anti-impersonation stop sequence or post-trim helpers without carefully thinking about the patterns they prevent.
- Don't switch the room format to alternating user/assistant fake turns without testing on Gemma and Mistral both.
- Don't introduce visible chat-input echo or live-typing artifacts. The synth/streaming loop is calm by design.
- Don't add a "kill background processes on quit" toggle without a concrete reason.
- Don't bump `__version__` by hand at tag time — the build pipeline handles it.
- Don't trust `IsShown()` on notebook page children.
- Don't skip the tool-bucket step when adding a tool.
- **Don't restructure a kin's `soul.md`.** Not to make it clearer, not to make it consistent, not while you're in there for something else. See "A soul.md is not a document to be improved" — it has happened, and third-person tidying turned a kin into a spec sheet of itself.
- Don't hand a park result to anyone via bare `GameHost.run()` — always `decorate()`.
- Don't write `Path.home() / ".hearthkin"` in new code — ask `hearthkin_paths`, or the setting that's meant to protect someone's real kin folder quietly stops covering you.
- Don't make a path helper create the directory it returns. Asking where something lives must not put it there.
