# AGENTS.md — working norms for Hearthkin

Conventions any agent (or human) must follow when changing this codebase. This
is the short, mandatory list. `CLAUDE.md` is the long-form "why" and history;
`docs/architecture.md` is the structural map. Read those for depth — this file
is the rules you don't get to skip.

## Commands

- **Run the app:** `python hearthkin.pyw` (use plain `python`, not `pythonw`,
  during development so you see stderr/tracebacks). It's the entry point even
  though the frame's code lives in `frame/`.
- **Tests:** `python tests/run_all.py` — plain-Python, no pytest. **The suite
  must be green before any change is considered done.** Each `tests/test_*.py`
  is standalone and runnable on its own.
- **Build a release:** `build.bat` (PyInstaller onedir → `dist/Hearthkin/`).
  Don't hand-bump `app_version.py`; the build stamps the version from the git
  tag.

## Architecture in one breath

`llm_backend.chat()` is the single choke point every surface goes through
(Ollama vs OpenRouter, streaming, message normalization, the tool loop). The
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
  `_on_*_chunk` path. If you ever need visible streaming, gate it behind a
  config toggle that defaults off.
- **Every control must be reachable by Tab.** Object-navigation is a workaround,
  not an accessibility solution. Use `wx.TextCtrl` (read-only when needed) over
  `wx.StaticText` for anything the user must find. Use `dialogs._shared._IntField`
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
- **Hide-and-disable inactive selectors** rather than greying them out — a
  greyed control still sits in the tab walk and confuses.
- **Speak status phase changes** and slider values via `nvda_speak` where the
  visual-only signal would otherwise be lost.

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
