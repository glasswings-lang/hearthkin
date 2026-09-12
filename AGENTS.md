# AGENTS.md — working norms for Hearthkin

Conventions any agent (or human) must follow when changing this codebase. This
is the short, mandatory list. **`CLAUDE.md` is the authoritative, current rules
file** — where this file and it disagree, `CLAUDE.md` wins. `docs/lessons.md`
holds the long account behind each of its rules, and `docs/architecture.md` is
the structural map. Read those for depth — this file is the rules you don't get
to skip.

## Commands

- **Run the app:** `python hearthkin.pyw` (use plain `python`, not `pythonw`,
  during development so you see stderr/tracebacks). It's the entry point even
  though the frame's code lives in `frame/`.
- **Tests:** `python tests/run_all.py` — plain-Python, no pytest. **The suite
  must be green before any change is considered done.** Each `tests/test_*.py`
  is standalone and runnable on its own; one that builds wx windows runs via
  `python tests/_gui_runner.py <file>`.
- **Build a release:** `build.bat` (PyInstaller onedir → `dist/Hearthkin/`).
  Don't hand-bump `app_version.py`; the build stamps the version from the git
  tag.

## Architecture in one breath

`llm_backend.chat()` is the single choke point every surface goes through
(Ollama or a hosted provider, streaming, message normalization, the tool loop).
Hosted providers are a registry — `api_providers()` merges the built-ins with
`~/.hearthkin/providers.md` — and a model carrying a registered prefix is
hosted: ask `is_hosted_model(model)`, not `startswith("openrouter/")`. The
`Hearthkin(wx.Frame)` class is assembled in `hearthkin.pyw` (just `__init__` +
`main()` + the class declaration) from concern mixins in `frame/`; shared
module-level imports/constants/helpers live in `frame_shared.py`. Data layer is
`kin_persistence.py` (pure, no LLM/UI). Dialogs are one class per file under
`dialogs/`. Tools are one function per file under `tools/`. Surfaces: desktop,
rooms, Telegram, Discord, cron.

## Mandatory conventions

### Accessibility is not optional
The primary user is blind and screen-reader-primary (NVDA). Accessibility
regressions are correctness bugs.

- **NEVER call `wx.TextCtrl.AppendText` per streamed chunk.** This is a
  system-level cascade, not a UX nit: one MSAA/UIA event per chunk corrupts
  NVDA's event queue and damages *other* apps on the machine. Buffer streaming
  into `self._stream_buf` and paint once at turn-end. This applies to every
  `_on_*_chunk` path. No visible live-typing either, not even behind a
  toggle — the streaming loop is calm by design (`CLAUDE.md`, "What not to
  do").
- **Every control must be reachable by Tab.** Object-navigation is a workaround,
  not an accessibility solution. Use `wx.TextCtrl` over `wx.StaticText` for
  anything the user must find, and when it's read-only make it
  `TE_MULTILINE | TE_READONLY` — a single-line read-only TextCtrl is not
  keyboard-focusable on wxMSW. Speak a result that arrives while focus is
  elsewhere. Use `dialogs._shared._IntField`
  (a validated `wx.TextCtrl`) for numeric inputs, never `wx.SpinCtrl` (it floods
  NVDA and its Win32 `ES_NUMBER` rejects comma-formatted pastes).
- **A button's accessible name is its visible label** on wxMSW — `SetName()` is
  ignored. Put descriptive text in the label; use `&Letter` mnemonics.
- **Buddy-label pattern for text inputs:** a `wx.StaticText` created immediately
  before a plain `wx.TextCtrl` in z-order (= construction order) becomes its
  accessible name. Composite widgets (`wx.SearchCtrl`) hide their real edit
  child from `SetName` — prefer plain `TextCtrl` + buddy label.
- **Tab order = widget CONSTRUCTION order**, not sizer order. Reorder the
  constructor calls to fix tab placement.
- **Switching MODES: hide AND disable the inactive mode's controls** rather
  than greying them out — a greyed control still sits in the tab walk and
  says "unavailable" without saying why. `_apply_mode_visibility` (kin vs room
  header) does both, because `Hide()` alone doesn't reliably leave the tab
  walk. Two cases this is NOT: inside a `wx.Notebook`, the notebook already
  hides inactive pages, so don't `Disable()`/`Show()` widgets there; and
  transient state within one task (a picker that matters only sometimes)
  stays present and says it's inconsequential, because a control that
  appears and vanishes mid-task moves the map
  (`tests/test_stable_tab_order.py`).
- **Speak status phase changes** and slider values via `nvda_speak` where the
  visual-only signal would otherwise be lost.
- **A menu accelerator wins over the focused control** on wxMSW. Before giving
  a menu item a shortcut, check no text control relies on that key.

### Behavior over diffs
The user validates by testing, not by reading code. Describe behavior changes
in plain language and give a short test plan. Don't pad work for reviewability.

### The frame is mixins now
Add a frame method to the matching `frame/*_mixin.py` (or a new mixin added to
the `class Hearthkin(...)` bases + `frame/__init__.py`). Shared module-level
helpers/constants the frame and mixins reference go in `frame_shared.py`.
Nothing outside the frame imports `hearthkin` / `frame_shared` / `frame`;
other modules receive the frame instance (`self.frame`) and call methods on it.

### Tools
1. `tools/<name>.py` — one top-level function, annotated, first docstring
   paragraph is the model-facing description.
2. Register: import it + add to `_REGISTRY` in `tools/__init__.py`.
3. **Bucket it in `tools/_buckets.py`** (`_READ` / `_WRITE` / `_FULL`, or
   `INTENTIONALLY_TELEGRAM_BLOCKED`). A tool in no bucket is silently invisible
   on remote surfaces regardless of the kin's allowlist. **Run
   `python tests/test_tool_buckets.py`** — it fails if you forgot.
4. Filesystem tools should accept `agent_name: str = ""` (framework-injected,
   hidden from the model). Remote surfaces also inject `confine_paths=True` to
   keep paths inside the kin folder — respect it.

### Harness prompts are editable text
Any prompt fragment the harness wraps around a kin must be registered in
`kin_persistence.APP_PROMPT_REGISTRY` and served via `load_app_prompt(slug)` —
not buried as a string in code. Substitute with `str.replace`, never `.format`
(an operator edit must not crash on a stray brace). Bump the registry `version`
on a default change; extend `tests/test_app_prompts.py`.

### Config must be UI-reachable
Any setting a normal user touches (API keys, per-kin knobs, provider choices)
has to be reachable from the GUI (Preferences / the kin Settings dialog).
JSON-only config is acceptable ONLY for advanced overrides a power user seeks
out deliberately.

### Remote surfaces (Telegram / Discord)
- Output is **append-only** — never edit a previously-sent message.
- Per-user tool gating (`filter_tool_names` ∩ bucket, default `none`).
- Deny-by-default access; exec asks for approval (chat on Telegram, desktop
  dialog for Discord) and never trusts the local `tool_trust` dial to run a
  remote command unattended unless `remote_unattended_exec` is set.
- **Sanitize any external string** (usernames, display names, group titles)
  before it goes into a prompt — `kin_persistence.sanitize_for_prompt_literal`.
- Any new surface must pass `max_context_tokens` (or it ignores `num_ctx`) and
  give itself a reply-length cap.

### Security
Remote surfaces confine file paths to the kin folder and gate exec; the exec
denylist matches per shell-segment. Don't weaken these. When you fix a
cross-provider quirk at the `chat()` choke point, add a case to
`tests/test_llm_normalization.py` and an entry to `docs/troubleshooting.md`.

### Load-bearing rules stated in CLAUDE.md
One line each here; the rule and its reasons are in `CLAUDE.md`.

- **State paths come from `hearthkin_paths`** — never write
  `Path.home() / ".hearthkin"`.
- **A test run never speaks or chimes** — silenced inside `audio`; only
  `run_all.py` calls `audio.speak_result`, once, at the end.
- **Widget-building tests run only on an isolated desktop**
  (`tests/_gui_runner.py`), never on the live one.
- **The prompt is append-only** — never change what the model has already
  seen; per-turn variation goes in the tail.
- **Never restructure a kin's `soul.md`.**
- **Write the lesson, never the material** — tracked files are public. Arm
  the guard in a fresh clone: `git config core.hooksPath githooks`.
- **Anything added to `scripts/` gets a line in `scripts/README.md`**, in the
  same change.
- **A capability added to one surface is declared for all four** in
  `tests/_surface_matrix.py`.
- **All four anti-impersonation cleanup passes run on every path that saves a
  room reply.**
- **New background work is added to `_work_in_flight`** (confirm-on-close) —
  and ask which process it runs in.
- **A heartbeat reply without `reach_out` is asked about once, not dropped**
  (`turn_steering.unsent_reach_note`).
- **A paid dependency bought for one capability must not gate another.**
- **Importers decide role by name match against `kin_display_name`**, never by
  talk volume or position.
- **A real per-message field sent to providers goes into
  `_API_MESSAGE_FIELDS`**, or it is stripped.
- **Never ask the owner to verify code.** They can't read it. Verify by
  running the tests or the app, and report what you actually confirmed.

## Code style

- **Match the surrounding code** — comment density, naming, idiom. This codebase
  comments the *why* generously; keep that.
- **Static imports only for project modules** — no dynamic
  `importlib.import_module` for our own modules (PyInstaller's static analysis
  wouldn't bundle them). Use lazy `from x import y` inside a method body only to
  break an import cycle.
- **Stdlib-first dependencies.** `requirements.txt` lists only what's needed to
  launch. A tool wanting a heavier library does `try: import lib` in the
  function body and degrades gracefully when it's absent.
- **All file I/O in try/except** → `append_failure_log` + a status message,
  never a crash. The always-on logs under `~/.hearthkin/logs/` are the source of
  truth for "did X happen?" — check them before theorizing.
- **Preserve a file's existing line endings** when editing (e.g. `hearthkin.pyw`
  is CRLF).
- **Separate commits for bug fixes vs. new features**, even when developed
  together.
- Example personal names in docs (the operator and any kin) are illustrative
  placeholders — do not commit real user or persona names to the repo.

## Before you call a change done

1. `python tests/run_all.py` is green.
2. If you touched a frame method, it still resolves (the app imports; the method
   set is unchanged).
3. If you fixed a behavior, you can describe how to see it working from the UI.
4. Bug-fix and feature changes are in separate commits.
