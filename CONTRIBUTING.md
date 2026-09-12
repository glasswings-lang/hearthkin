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
by default, so conversations don't leave the machine. A kin can use a hosted
model instead. OpenRouter is built in, and any service that speaks the OpenAI
chat format can be added as a provider: a name, an address and a key, from
"Manage providers…" in the model browser or as a line in
`~/.hearthkin/providers.md`. That includes servers on your own network. The
start of the model name picks the provider — `openrouter/...`, or the name you
gave the one you added.

Two interaction modes: one-to-one chat, and "rooms" where several kin take turns.
Beyond the desktop window there are Telegram and Discord surfaces, and scheduled
"cron" wake-ups where a kin acts on its own.

Python + wxPython. Windows is the primary platform; the code runs elsewhere but
some features (scheduled tasks, the screen-reader integration) are Windows-only.

## Running it

You need Python 3.10 or later, the same as the README says. Release builds are
made with Python 3.11.

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

Three are worth knowing about specifically:

- **`test_tool_buckets.py`** — fails if you register a tool without assigning it
  a permission bucket. An unbucketed tool is silently invisible on Telegram and
  Discord regardless of configuration, which has bitten three times.
- **`test_mnemonics.py`** — fails if a control claims an `Alt+<letter>` shortcut
  the menu bar already owns. On Windows the menu always wins, so the control's
  shortcut isn't merely conflicting, it's dead.
- **`test_no_private_strings.py`** — fails if a name from a private list
  reappears in any tracked file. The list is gitignored and is not in this
  repo, so in your clone this test prints a `GUARD DISARMED` banner and
  passes. That is correct: you have nothing of anyone's to protect. It only
  binds on a checkout that has a `docs/private/` directory.

### Writing a test

`tests/run_all.py` runs each test file in its own process. Before it starts, it
makes one fresh folder and points `HEARTHKIN_HOME` at it, so a run never
touches your real `~/.hearthkin`. It refuses a `HEARTHKIN_HOME` you already had
set, and says so. `--keep` leaves the folder behind so you can look at it.

That one folder is shared by every test file in the run. A test that makes real
`chat()` calls, or reads a log expecting only its own lines, should make a
folder of its own inside it. Do this before importing anything from the
project:

```python
os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="mytest-", dir=(os.environ.get("HEARTHKIN_HOME") or None))
```

When the test is run on its own, the `or None` puts that folder in the system
temp directory rather than your real one.

**Tests must never speak or make a sound.** You don't have to arrange that
yourself: `audio.py` silences speech and sound cues during a test run, at the
point where every sound goes out. Don't work around it.

**A test that builds wx windows runs on a separate desktop.** Creating a window
takes focus on Windows even when it is never shown, and a screen reader follows
focus. So the runner spots these tests and runs them through
`tests/_gui_runner.py`, on a Windows desktop with no path to the screen the
person is using. If that desktop can't be made, the test is skipped, and the
runner says so. In such a test, send keystrokes with `PostMessage` to the
test's own window. Never use `SendInput`, which goes to whatever has focus on
the real desktop.

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
- **`scripts/audit_ui.py`** builds a screen and asks Windows what name a screen
  reader would announce for each control. Run it with `--self-test` first. The
  older `scripts/narrate_ui.py` only reads the source and guesses, and it has
  cleared a dialog that had real problems, so prefer `audit_ui.py`.

## Adding a tool

Tools are functions a kin can call. One function per file in `tools/`:

1. Write `tools/<name>.py` with a single top-level function. Annotate every
   parameter; the first paragraph of the docstring is what the model reads. The
   schema is derived automatically from the signature.
2. Register it in `tools/__init__.py` — one import line, one registry entry.
   Imports are static on purpose; dynamic discovery would break the packaged
   build.
3. **Add it to a permission bucket in `tools/_buckets.py`.** Skipping this makes
   the tool invisible on remote surfaces with no error message. In the same
   file, add the tool to its bucket's line in `BUCKET_EXPLAINER` — that is the
   description a person reads when choosing what someone on Telegram may do.
4. Run `python tests/test_tool_buckets.py`.
5. Switch it on for a kin by adding its name to that kin's `tools.json` (or
   ticking it in Kin settings), then restart.

A running app loads the registry at import, so a new tool needs a restart.

## Adding a script

Anything added to `scripts/` gets a line in `scripts/README.md` in the same
change: what it does, how to run it, and what it changes — nothing, files it
writes, or config it edits. A tool nobody but its author can find isn't a tool
the project has.

## Conventions worth knowing

- **Stdlib first.** `requirements.txt` holds only what's needed to launch.
  A tool wanting a heavier library should import it inside the function body and
  degrade gracefully when it's missing.
- **Config a normal user touches must be reachable from the UI.** The intended
  user doesn't edit JSON. A setting only in a config file is acceptable for an
  advanced override that power users go looking for. Anything an ordinary user
  would need to change belongs in the UI.
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

## Git hooks

This repo keeps its hooks in a tracked `githooks/` directory. Git will not use
them until you point it there, once per clone:

```
git config core.hooksPath githooks
```

Check it took — it should print `githooks`:

```
git config core.hooksPath
```

Two hooks, both about not publishing private strings:

- **`pre-commit`** runs `tests/test_no_private_strings.py` over every tracked
  file and refuses the commit if it fails.
- **`commit-msg`** runs the same check over the message you just wrote. Commit
  messages are not files, so `git ls-files` cannot see them — and message text
  is the reason this project's development history had to be left behind rather
  than published. It gets its own gate for that reason.

Neither does anything in a clone without a `docs/private/` directory, so you can
arm them harmlessly and forget about them.

**Why this isn't automatic.** Git deliberately refuses to let a repository turn
on its own hooks when you clone it — otherwise cloning a stranger's project
would run their code on your machine. The one config line is that boundary, not
an oversight.

**If a hook is wrong**, `git commit --no-verify` skips both. Use it knowing what
you're skipping.

**On Windows**: no `chmod` is needed; there's no executable bit and Git for
Windows doesn't look for one. `.gitattributes` pins `githooks/` to LF, because a
shell script checked out with CRLF fails with a bad-interpreter error and takes
the guard down silently.

## Pull requests

Keep bug fixes and new features in separate commits, even when written together.
Explain *why* in the commit message — this codebase's history is unusually
load-bearing, because many decisions encode a failure that isn't obvious from
the code alone.
