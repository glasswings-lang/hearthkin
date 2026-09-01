# Contributing to Hearthkin

This document is for **you** — a person who wants to work on this codebase.
It assumes no prior context beyond "I can read Python."

If you've looked at `CLAUDE.md` and found it strange, that's because it isn't
written for you. It's a working brief for AI coding assistants, written by them,
and it talks *about* the maintainer rather than *to* the reader. Useful as
reference, wrong as an introduction. Start here instead, then read
[`docs/architecture.md`](docs/architecture.md) for the structural tour.

## What this is

A desktop app for talking with "kin" — configured personas, each with a soul
prompt, its own memory, and its own model. It talks to **Ollama running locally**
by default, so conversations don't leave the machine, and can route through
OpenRouter when a model name is prefixed `openrouter/...`.

Two interaction modes: one-to-one chat, and "rooms" where several kin take turns.
Beyond the desktop window there are Telegram and Discord surfaces, and scheduled
"cron" wake-ups where a kin acts on its own.

Python + wxPython. Windows is the primary platform; the code runs elsewhere but
some features (scheduled tasks, the screen-reader integration) are Windows-only.

## Running it

```
pip install -r requirements.txt
python hearthkin.pyw
```

`hearthkin.pyw` is the entry point. The `.pyw` extension makes Windows launch it
without a console window — which also means **`print()` goes nowhere** in normal
use. Run it as `python hearthkin.pyw` from a terminal when you want stderr.

State lives in `~/.hearthkin/` (kin folders, config, logs). It's created on first
run. Nothing in this repo is modified by running the app.

## Tests

```
python tests/run_all.py
```

Plain Python, no pytest. Each `tests/test_*.py` also runs standalone. **Run the
suite before opening a PR** — it's fast and it catches the things that have
actually broken before.

Two are worth knowing about specifically:

- **`test_tool_buckets.py`** — fails if you register a tool without assigning it
  a permission bucket. An unbucketed tool is silently invisible on Telegram and
  Discord regardless of configuration, which has bitten three times.
- **`test_mnemonics.py`** — fails if a control claims an `Alt+<letter>` shortcut
  the menu bar already owns. On Windows the menu always wins, so the control's
  shortcut isn't merely conflicting, it's dead.

## The one thing to understand before changing UI code

**The primary user is blind and navigates by screen reader.** This isn't a
nice-to-have; it's the design constraint that explains most of the odd-looking
decisions in the UI code. Concretely:

- **Never call `AppendText` once per streaming chunk.** Each call fires an
  accessibility event. Dozens per second corrupts the screen reader's event
  queue, and the damage spreads to *other applications* on the system. Buffer the
  stream, paint once when the turn ends. This pattern is already everywhere;
  don't regress it.
- **Every control must be reachable by Tab.** Being able to find something by
  screen-reader object navigation is not the same as it being accessible.
- **Tab order is widget *creation* order in wxPython**, not sizer order. To move
  something in the tab sequence you reorder the constructor calls. Rearranging
  the sizer only fools sighted readers.
- **A button's accessible name is its visible label.** `SetName()` on a button is
  ignored on Windows — put the descriptive text in the label.
- **Use plain `wx.TextCtrl` with a preceding `wx.StaticText` label** for text
  input, not composite widgets like `wx.SearchCtrl`. Composites wrap an inner
  control that the screen reader focuses instead, so the outer name never
  reaches the user.
- **`scripts/narrate_ui.py`** prints what a screen reader would announce tabbing
  through a screen. It's a reading aid, not an emulator — it can't see anything
  hidden or enabled at runtime — but it catches a real class of problem cheaply.

## Adding a tool

Tools are functions a kin can call. One function per file in `tools/`:

1. Write `tools/<name>.py` with a single top-level function. Annotate every
   parameter; the first paragraph of the docstring is what the model reads. The
   schema is derived automatically from the signature.
2. Register it in `tools/__init__.py` — one import line, one registry entry.
   Imports are static on purpose; dynamic discovery would break the packaged
   build.
3. **Add it to a permission bucket in `tools/_buckets.py`.** Skipping this makes
   the tool invisible on remote surfaces with no error message.
4. Run `python tests/test_tool_buckets.py`.

A running app loads the registry at import, so a new tool needs a restart.

## Conventions worth knowing

- **Stdlib first.** `requirements.txt` holds only what's needed to launch.
  A tool wanting a heavier library should import it inside the function body and
  degrade gracefully when it's missing.
- **Config a user touches must be reachable from the UI.** The intended user
  doesn't edit JSON. A setting that exists only in a config file is a bug, not a
  power-user feature.
- **Heavy work goes off the UI thread.** Anything hitting the network or reading
  many files must run on a worker thread and marshal results back with
  `wx.CallAfter`, or the window freezes.
- **Two focused tools beat one with a mode switch.** Models pick reliably between
  distinct tools and unreliably between enum values.
- **Failures get logged, always.** `~/.hearthkin/logs/` has several always-on
  logs that don't respect any verbosity setting. When diagnosing "did X actually
  happen?", read those before theorising — they're the source of truth, and they
  have repeatedly answered in seconds what speculation got wrong for an hour.

## Things that look like bugs and aren't

- **Odd-looking anti-impersonation code in the room path.** Small models
  routinely start writing as another kin. There's a chain of cleanup passes plus
  a stop sequence holding that back. Removing any of them brings it back.
- **Background processes surviving app shutdown.** Deliberate — a long build a
  kin started shouldn't die because the window closed.
- **Sparse-looking model templates.** Recent Ollama versions build prompts in
  compiled code, so a model's stored template can be a near-empty placeholder.
  This is normal and not evidence of a broken configuration.

## Pull requests

Keep bug fixes and new features in separate commits, even when written together.
Explain *why* in the commit message — this codebase's history is unusually
load-bearing, because many decisions encode a failure that isn't obvious from
the code alone.
