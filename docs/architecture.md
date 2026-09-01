# Hearthkin architecture — the map for a new maintainer

This is the **structural** orientation doc: what the pieces are, how a message
flows through them, where state lives, and — most usefully — *where to go to
change a given thing*. It's aimed at someone arriving cold: a future Claude
session, a contributor, or the operator trying to point a helper in the right
direction.

It complements two other docs, it doesn't replace them:

- **`CLAUDE.md`** — the narrative history and the *conventions* (why each
  past fix exists, the rules you must not break). Read it for the "why."
- **`docs/troubleshooting.md`** — the diagnostic playbook when something is
  broken in production. Read it when there's a fire.

Read this one first for the "what and where," then dip into those.

---

## What Hearthkin is, in one paragraph

A Python + wxPython desktop app for chatting with multiple local-LLM personas
("kin"). It talks to a local (or LAN-remote) **Ollama** daemon by default, and
can route a kin through **OpenRouter** when its model name starts with
`openrouter/`. Each kin is a persona with a soul prompt, a curated memory, and
a model. The user (often blind, NVDA-primary, non-coder) lives in it daily —
so **accessibility and not-surprising-the-user are first-class constraints,
not polish.**

---

## The module map

Entry point: `python hearthkin.pyw` (runs under `pythonw.exe` on Windows = no
console window). Cross-module imports are all static `from X import Y` so
PyInstaller bundles them — **never** introduce dynamic `importlib` for project
modules.

| Module | Responsibility | Touches LLM? | Touches UI? |
|---|---|---|---|
| `hearthkin.pyw` | **Assembler only** (since the 2026-07 modularisation, ~570 lines): the `class Hearthkin(*17 mixins, wx.Frame)` declaration + `__init__` + `main()`. The frame's ~253 methods live in `frame/` mixins. Still the entry point. | via `llm_backend` | yes |
| `frame_shared.py` | Shared namespace hub: every module-level import/constant/helper the frame + mixins reference (incl. the memory ops `consolidate_memory_blocking` / `distill_memory_blocking` and the foreground helpers). Mixins import their needed names from here. | via `llm_backend` | no |
| `frame/` (package) | The 17 concern-focused frame mixins (chat send/stream, rooms, memory, menus, prefs, cron/exec, lifecycle, …), one file each, mixed into `Hearthkin`. Every method runs with the frame as `self`. | via `llm_backend` | yes |
| `llm_backend.py` | The single LLM dispatch layer. `chat()` is THE choke point — Ollama vs OpenRouter, streaming, the message-normalization pipeline, the tool loop. | **yes — owns it** | no |
| `kin_persistence.py` | Pure data layer: paths, defaults, atomic load/save for kin & rooms, migrations, editable-prompt registry. | no | no |
| `hearthkin_paths.py` | The one place that decides where the runtime state tree is (`config_dir` / `kin_dir` / `logs_dir`, honouring `HEARTHKIN_HOME`). Depends on nothing in the project, so `tools/`, `park_*` and `kin_persistence` can all sit on it without a cycle. Never creates what it returns. | no | no |
| `dialogs/` (package) | Every `wx.Dialog` subclass, one per file. Big one: `edit_kin.py` (the 7-tab kin Settings dialog). | no | yes |
| `telegram_bot.py` | The `TelegramBot`: DM + group surfaces, per-user history, chat-based approval. | via `llm_backend` | no |
| `discord_bot.py` | The `DiscordBot`: gateway connection on its own asyncio thread, per-user tool buckets, deny-by-default access, exec approval routed to the desktop. Newer surface (v0.9.0). | via `llm_backend` | no |
| `memory_recall.py` | Per-turn memory recall — scores the kin's depth logs + journal against the current turn and pages a budgeted slice inline. Wired into every send surface. | reads Ollama (embeddings) | no |
| `tools/` (package) | Kin-callable tools (the registry + one file per tool). Schema auto-derived from each function's signature + docstring. Includes `analyze_sound` (audio facts, backed by `audio_spectrum.py`), `reach_out` (proactive), the `tff` park bridge. | called by the tool loop | no |
| park family (`park_keeper.py`, `park_mode.py`) + `reading_bridge.py` / `authoring_bridge.py` | The Time-for-Family park play surface (a kin plays by plain text / emotes) and the reading/authoring bridges. | via `llm_backend` | no |
| `chat_helpers.py` | Pure helpers: streaming chunk extraction, sentence boundaries, token estimation, anti-impersonation cleanup, and the tool-roleplay/gesture detector (`detect_tool_roleplay`). | no | no |
| `model_utils.py` / `model_browser.py` | Ollama model parsing/capability probing / the model-picker dialog. | reads Ollama | browser: yes |
| `audio.py` | NVDA speech + reply chimes. | no | no (audio) |
| `voice.py` | Microphone capture, and text-to-speech playback of a kin's replies (ElevenLabs). | calls ElevenLabs for TTS | no (audio) |
| `stt.py` | Speech to text. A transcription model addressed as (model, host) exactly like a chat model — empty host = this computer; `route_for` is the one function that reads the pair. Routes to local faster-whisper, any OpenAI-compatible `/v1/audio/transcriptions` machine, or ElevenLabs. Split from `voice.py` so transcription is testable without a microphone. | local, or one HTTP call | no (audio in, text out) |
| `tray.py` | System-tray icon, the mini-chat popup, close-to-tray lifecycle. | via `llm_backend` (mini-chat) | yes (tray + popup) |
| `windows_startup.py` | Windows run-at-login registration (Startup-folder shortcut). | no | no |
| `compat.py` | Pre-flight compatibility checks on model swap (`ModelProfile` per provider). | no | returns notes the UI renders |
| `importers/` (package) | History import backends — one file per source (`kindroid.py`, `skype_json.py`, `skype_txt.py`, `text_log.py`, `openclaw.py`) plus `_canonical.py` / `_marker.py` helpers. Backs File → Import history (dialog: `dialogs/import_history.py`). Also holds the **restore** path — `hearthkin_jsonl.py` + `_canonical.restore_history` — which reads Hearthkin's *own* format back in with no markers and no relabelling, backing File → Restore a kin's history (dialog: `dialogs/restore_history.py`). Import and restore are deliberately separate: import stamps `source: import:<label>` and brackets the block, which would misrepresent a kin's own past as carried-in seed history. Each entry point refuses the other's file shape. | no | no |
| `hearthkin_cron.py` + `cron_helpers.py` | Standalone scheduled-wake-up subprocess (no wx import → fast cold start). | via `llm_backend` | no |

**Import shape:** the frame's shared module-level names live in `frame_shared.py`;
`hearthkin.pyw` (the assembler) and every `frame/` mixin import their needed names
from there. Nothing outside the frame imports `hearthkin` / `frame_shared` /
`frame` — other modules receive the frame instance (`self.frame`) and call methods
on it, so they never need to import it. A frame method is reached via the MRO on
the mixed-in class; a test that monkeypatches a name the frame imports must patch
it in the **mixin module** where the method lives (that's the namespace the name
resolves from), not in `hearthkin`. The graph is acyclic at module-load time —
`frame_shared` → project modules; `frame/*` mixins → `frame_shared`; `hearthkin` →
both. Keep it that way.

---

## The one function that matters most: `llm_backend.chat()`

Every conversational surface — desktop 1-on-1, rooms, Telegram DM, Telegram
group, cron — flows through `chat(model, messages, ...)`. It dispatches on the
`openrouter/` prefix. Before dispatching, it runs an **outbound normalization
pipeline** that fixes provider/model quirks. This is where almost every
production bug we've ever hit got fixed, so it's worth knowing the order:

1. **Truncate** to fit `num_ctx` (`_truncate_messages`) — inserts a `role=system`
   marker where history was cut.
2. **Mistral tool_call_id remap** (`_remap_tool_call_ids_for_mistral`) — only for
   `mistralai/*`.
3. **Tool-args shape** (`_normalize_history_tool_args`) — Ollama wants dict args,
   OpenRouter wants JSON-string args.
4. **Null content coercion** (`_coerce_tool_call_assistant_content`) — `null` →
   `""` on tool-call turns (the Anthropic "seizure" fix).
5. **Attachment expansion** (`_expand_attachments_for_provider`) — images to the
   provider's shape, with a recent-image keep window.
6. **Strip bookkeeping** (`_strip_extra_message_fields`) — drop storage-only keys
   (`ts`, `source`, …) Mistral 400s on.
7. **Mistral thinking-strip** — drop `thinking` for `mistralai/*`.
8. **System consolidation** (`_consolidate_system_messages`) — Ollama only: fold
   every system message into one leading block (strict-template fix).

**Rule (from `troubleshooting.md`): a new provider/model quirk gets fixed HERE,
as a normalize/remap/strip/coerce step, not in a single surface.** A fix only in
`telegram_bot.py` leaves the same trap waiting for the next surface. These
functions are pure and covered by `tests/test_llm_normalization.py` — add a
case there when you add a step.

`chat()` also calibrates a per-kin real-vs-estimate **token ratio** from the
provider's reported `prompt_tokens`, so truncation tracks the real billed prompt
rather than a fixed guess. See CLAUDE.md "Network and cost gotchas."

---

## How a desktop chat message flows

```
user types in Chat tab
  -> Hearthkin._on_send (hearthkin.pyw)
     builds messages: system prompt (base_prompt + soul + memory) + history + new turn
  -> worker thread calls llm_backend.chat(model, messages, ...)
     -> normalization pipeline (above)
     -> dispatch: Ollama (ollama.chat) OR OpenRouter (HTTPS + SSE)
     -> yields Chunk objects (streaming) or a ChatResult (blocking/tool loop)
  -> chunks are BUFFERED into self._stream_buf (NOT painted per-chunk — see below)
  -> on done: paint the whole reply once, run cleanup, persist to conversation.jsonl
```

Tool-using kin take the `run_tool_loop` path instead of plain streaming; tool
round-trips get spliced back into the conversation so the kin sees its own past
calls.

---

## The surfaces (they share `chat()`, differ everywhere else)

- **Desktop 1-on-1** — streaming, paints sentence-by-sentence (buffered), full
  Settings UI. The reference surface.
- **Rooms** — multiple kin take turns. Heavy anti-impersonation machinery (see
  danger zones). Tool round-trip persistence is a known v1 limitation.
- **Telegram DM / group** — non-streaming (one full `chat()` per message),
  append-only output, chat-based approval for gated tools, per-user tool buckets.
  Group attribution is inlined into the user content.
- **Discord** (`discord_bot.py`) — a persistent gateway connection on its own
  thread (asyncio), inference pushed to an executor so the heartbeat never
  stalls. Mirrors the Telegram contract: per-user tool buckets, deny-by-default
  access, exec approval routed to the operator's desktop. Newer than Telegram
  (shipped v0.9.0), less battle-tested.
- **Cron** — a separate subprocess. If Hearthkin is running it drops a request
  file the app picks up; if not, it runs the LLM call itself and journals.

**If you add a surface:** it must pass `max_context_tokens` (or it won't honor
`num_ctx`), give itself a reply-length cap, and run the anti-impersonation
cleanup if it's multi-voice. These have each been a bug exactly once.

---

## Where state lives — `~/.hearthkin/`

- `kin/<Kin>/` — `soul.md`, `memory.md` (an index), `memory/<topic>.md`
  (depth logs), `staging/<scope>.md` (pending summarizer notes), `config.json`,
  `conversation.jsonl` (append-only history), `tools.json` (allowlist),
  `exec_allowlist.json`.
- `rooms/<name>/` — membership + room context + settings.
- `base_prompt.md` / `prompts/<slug>.md` — editable harness prompts (operators
  edit these in a text editor; the file wins over the code default).
- `logs/*.log` — **always-on** diagnostic logs (`empty_replies`, `cron_errors`,
  `openrouter_errors`, `telegram_failures`, `save_failures`, `usage`). These are
  the source of truth for "did X actually happen?" — check them before theorizing.
- `config.json` (app-level) — NVDA mode, chime, **`embed_host`** (which Ollama
  machine runs semantic-search embeddings), etc. The Ollama machine for *chat*
  is per-kin now (`ollama_host_name` in each kin's `config.json`, chosen in the
  model browser, resolved by `resolve_kin_ollama_host`); the old single global
  `ollama_host` was removed and is folded in by `migrate_global_ollama_host`.

---

## "Where do I change X?" — the task→location table

| I want to… | Go to… |
|---|---|
| Fix a provider/model 400 or message-shape bug | `llm_backend.chat()` pipeline + a test in `test_llm_normalization.py` |
| Add a kin-callable tool | `tools/<name>.py` + register in `tools/__init__.py` + enable in a kin's `tools.json` |
| Add a history importer | `importers/<source>.py` + register it in the `importers/` package + wire into `dialogs/import_history.py` |
| Add/adjust a per-kin setting | `kin_persistence.DEFAULT_AGENT_CONFIG` + a widget in `dialogs/edit_kin.py` |
| Add an app-level preference | `kin_persistence` app defaults + Preferences UI in `frame/prefs_mixin.py` (+ toggle handler in `frame/prefs_toggles_mixin.py`) |
| Change how memory distills/consolidates | `kin_persistence` prompts + the memory-op functions in `frame_shared.py` + the scope/trigger methods in `frame/memory_mixin.py` |
| Change an editable harness prompt | the `APP_PROMPT_REGISTRY` in `kin_persistence.py` (bump `version`!); `load_app_prompt(slug, kin_name)` resolves per-kin → install-wide → built-in default, and `tests/test_app_prompts.py` is the diff harness |
| Touch the Telegram surface | `telegram_bot.py` |
| Touch scheduled wake-ups | `hearthkin_cron.py` + `cron_helpers.py` |
| Add a model-swap compatibility check | `compat.py` (`ModelProfile` + a `_check_*`) |
| Change anti-impersonation behavior | `chat_helpers.py` cleanup fns + the room save paths in `frame/rooms_mixin.py` |
| Add/change a frame method (chat, rooms, cron, prefs, …) | the matching `frame/*_mixin.py`; shared module-level helpers go in `frame_shared.py` |

---

## Danger zones — looks simple, has teeth

These are the recurring traps. Each is documented in depth in CLAUDE.md; this is
the "don't step here without reading first" list.

1. **Never `AppendText` per streaming chunk.** It floods NVDA's event queue and
   corrupts UIA *system-wide* (other apps' buttons break). Always buffer into
   `self._stream_buf`, paint once at turn-end. This is a hard rule.
2. **Anti-impersonation (rooms).** Stop sequence + four cleanup passes
   (`strip_self_tag`, `strip_leading_speaker_tag`, `strip_trailing_other_speakers`,
   timestamp strip) must run on *every* path that saves a room reply. Partial
   cleanup feeds the pattern back into context.
2a. **Tool-roleplay / gesture detection.** `chat_helpers.detect_tool_roleplay`
   catches the failure mode where a kin *describes* a tool action in prose or
   asterisks ("*reading my staging notes now*") instead of issuing the structured
   call — common on weaker models. The verb/target word lists are operator-extendable
   via the `gesture_messages` editable prompt (so an operator can teach it their
   kin's phrasing without code). If you touch the detector, keep it conservative —
   false positives that "correct" a legitimate reply are worse than a missed gesture.
3. **`num_ctx` is a cost dial, not a capability dial.** On a paid OpenRouter
   model every token in `num_ctx` is billed every message. A kin moved from a
   local model keeps its old (often huge) `num_ctx` — check it after any swap.
4. **Source of truth for per-kin state is `agent_cfg`, not widgets.** Read config
   at consumption time; widgets are editors that can disappear.
5. **Accessibility widget rules.** Read-only `wx.TextCtrl` (not `StaticText`) for
   anything tab-reachable; `dialogs._IntField` (not `SpinCtrl`) for numbers; the
   visible button label IS the accessible name. Tab-reachability is mandatory.
6. **`.pyw` swallows stderr.** Real failures go to `~/.hearthkin/logs/*.log`. Run
   `python hearthkin.pyw` from a terminal when you need tracebacks.
7. **Ollama loads per-model, not per-kin; `keep_alive` is per-request,
   last-one-wins.** `keep_alive` is resolved from each kin's config at send time
   (`_resolve_keep_alive`) and attached to that request; Ollama keys a loaded
   instance by **model name + load options (notably `num_ctx`)** and resets its
   unload timer to whatever the latest request asked for. Consequences a
   maintainer/operator hits: (a) several kin on the same model share one warm
   instance, but a single kin left on the default 5-min keep-alive can unload it
   for all of them if it talks last; (b) the same model at two different
   `num_ctx` values is two separate loaded instances (the KV cache is sized at
   load), so they don't share warmth and can double memory. A cron on a model
   nothing keeps warm eats the full cold-load (minutes, on a big model) before it
   produces anything — the per-kin "Keep model loaded" setting is the fix.
   Operator-facing version: user-guide "When several kin use the same model."

---

## Running and testing

- **Run the app:** `python hearthkin.pyw` (use plain `python`, not `pythonw`,
  during dev so errors print).
- **Run all tests:** `python tests/run_all.py` — plain Python, no pytest needed,
  exits non-zero on any failure. Each `tests/test_*.py` is also runnable on its
  own.
- **Add a test:** match the existing style — `check(name, cond)`, PASS/FAIL
  lines, a summary, `sys.exit(1)` on failure. The pure functions in
  `llm_backend` and `chat_helpers` are the easiest, highest-value targets.
- **Build the distributable:** `build.bat` → `dist/Hearthkin/` (PyInstaller
  onedir). Version is stamped at build time from the git tag — don't bump
  `__version__` by hand.

---

## If you're an AI session picking this up

Start with this file, then `CLAUDE.md` for the conventions and the "Recent work
worth knowing" log. When something is broken, `docs/troubleshooting.md` first and
the always-on logs *before* theorizing — they answer "what actually happened" in
seconds where guessing takes an hour. When you fix a new quirk, fix it at the
`chat()` choke point, add a test, and write it into all three docs. That
discipline is what keeps this maintainable by whoever (or whatever) comes next.
