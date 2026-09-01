# Hearthkin

Multi-kin local-LLM chat for Windows. Accessibility-first.

> **Released as-is, and unmaintained.**
>
> This is a finished thing rather than an ongoing project. It works, it is
> tested, and nobody is answering questions about it. Issues are turned off
> deliberately — not to be unfriendly, but because promising support that
> will not arrive is worse than saying so plainly. It is CC0: fork it,
> change it, ship your own, no permission needed and no credit required.
>
> **Discord has not been run against a live server.** Its logic has tests —
> permissions, per-channel history, recall, stop — but the connection itself
> is unproven. Everything else (desktop, rooms, Telegram, scheduled wake-ups)
> is in daily use.
>
> The reasoning behind how things are built is in [`CHANGELOG.md`](CHANGELOG.md),
> [`docs/`](docs/), and [`docs/history/`](docs/history/) — every commit message
> from development, grouped by release. Read those before changing something
> that looks odd; most odd things here are load-bearing.

## What is this?

Hearthkin is a Python + wxPython desktop app for chatting with one or
several locally-running language models. Each "kin" is a configured
persona — a soul prompt, a kin-curated memory index with depth logs,
a chosen model, and an optional tool allowlist. You can talk
one-on-one with a single kin, or set up rooms where multiple kin take
turns alongside you.

Core ideas:

- **Local first.** Default backend is [Ollama](https://ollama.ai),
  running models on your own machine — or on another machine on your
  network (point a kin at a remote Ollama host, e.g. a dedicated
  inference box). No conversation data leaves your control unless you
  opt into OpenRouter routing per kin (with optional provider pinning).
- **Accessibility is non-negotiable.** Designed against NVDA from day
  one. Every control is tab-reachable. No status-only surface that
  requires object navigation. Phase changes ("Thinking" / "Typing" /
  "Still loading") are spoken so you don't have to hunt for state.
- **Persistent identity.** Kin remember across sessions via a
  three-layer memory model: a brief index they curate themselves, depth
  logs they write into for substance, and a staging area where the
  summarizer leaves notes between sessions for the kin to review during
  tending. Nothing automatic rewrites the canonical memory — the kin is
  the arbiter. Conversations land in append-only JSONL transcripts you
  can audit, export, or hand-edit.
- **Tools when you want them.** Eighteen of them, per-kin opt-in: read,
  write and edit files; fetch a web page; web search; memory search (BM25,
  optionally semantic-reranked via local embeddings); shell exec (gated by
  your approval) and background processes; a webcam glance and sound
  analysis; notes and staging; and scheduled wake-ups via Windows Task
  Scheduler. A detector nudges a kin that *describes* a tool action
  instead of calling it.
- **Multiple surfaces.** Desktop chat, multi-kin rooms, Telegram bot and
  Discord bot (both with per-user tool gating), cron wake-ups that work
  whether the app is open or closed.
- **A park to keep.** Time for Family is a small creature-park game a kin
  can play in plain language, or *keep* — tending it on its own scheduled
  wake-ups. Park turns run through the same front door the console uses, so
  you and a kin can share one park and each see what the other did.
- **Yours to tune and to seed.** The prompts the app wraps around a kin
  live as plain text files you can edit (`~/.hearthkin/prompts/`), and you
  can import existing chat history to give a kin a past to stand on —
  OpenClaw session folders, Skype (the official JSON/`.tar` bundle or a
  third-party `.txt` export), claude.ai exports, Kindroid, Telegram, and
  plain text or markdown logs. The format is detected for you. A kin's own
  Hearthkin transcript is deliberately *refused* here and has its own
  restore path instead, so its turns are never relabelled as borrowed
  history.

For what's planned next — and a short explanation of a couple of
currently-intentional quirks — see [`ROADMAP.md`](ROADMAP.md).

## Requirements

- Windows 10 or later (Linux/macOS will run the chat path; cron and
  some Windows-specific niceties won't)
- Python 3.10 or later
- [Ollama](https://ollama.ai) installed and running, with at least one
  model pulled (`ollama pull qwen2.5:7b-instruct` is a reasonable
  default)
- Enough RAM/VRAM for whatever model you want to run. 7B–8B models run
  comfortably on most modern laptops; bigger models need bigger
  hardware.

Optional:

- An [OpenRouter](https://openrouter.ai) account if you want to route
  any kin to a hosted model (Claude, GPT-4, Gemini, Llama, etc.).
- A [Brave Search](https://brave.com/search/api/) API key if you want
  the `web_search` tool.
- A Telegram bot token (via [@BotFather](https://t.me/botfather)) if
  you want to talk to a kin from your phone.

## Install

**From source:**

```
git clone https://github.com/glasswings-lang/hearthkin.git
cd hearthkin
pip install -r requirements.txt
python hearthkin.pyw
```

**Prebuilt builds** are published on the
[Releases page](https://github.com/glasswings-lang/hearthkin/releases) in
two shapes:

- `Hearthkin-Setup-<version>.exe` — installs to
  `C:\Program Files\Glasswings\Hearthkin`, adds a Start Menu entry, and
  offers optional desktop-shortcut and "start with Windows" checkboxes
  during setup. SmartScreen will likely warn the first time (the build is
  unsigned for now); click *More info → Run anyway*.
- `Hearthkin-Portable-<version>.zip` — unzip anywhere and run
  `Hearthkin.exe` from inside the unzipped folder. The `_internal` folder
  must stay next to the exe.

To build your own installer locally, run `build.bat` — it runs
PyInstaller against `Hearthkin.spec`, bundles third-party licenses
into `licenses/`, and (if Inno Setup 6 is installed) produces
`dist/Hearthkin-Setup-<version>.exe`.

## Quick start

1. Launch Hearthkin. The kin selector lists existing kin (or shows
   none on first run).
2. **New Agent…** — give it a name. Either fill in the soul prompt
   now, or check "Skip identity setup" to chat with the raw model
   first and let identity emerge.
3. Open **Settings** and pick a model under **Model & generation →
   Change model…**. Local Ollama models show up automatically; for
   OpenRouter, switch the provider radio inside the model browser.
4. Type into the input field. Enter sends; Shift+Enter is a newline.
5. The Activity field reports state inline; the status bar shows
   kin / model / context-usage at a glance.

The full **user guide** is at
[`docs/user-guide.html`](docs/user-guide.html). Open in any browser.
It covers installation, the first kin, soul/memory, model choices,
rooms, tools, the Telegram bot, scheduled wake-ups, accessibility,
troubleshooting, and where everything lives on disk.

## Project layout

See [`CLAUDE.md`](CLAUDE.md) for the architecture, module map, and
convention notes. It's written for contributors and AI-assisted
development; not strictly required reading to use the app.

## License

[CC0 1.0 Universal](LICENSE) — public domain dedication. Do whatever
you want with this. No attribution required (but appreciated).

## Third-party software

Hearthkin's own source is CC0, but the installer also bundles
third-party components under their own licenses. Every bundled
component's license text ships in `licenses\` next to the installed
`Hearthkin.exe`.

The notable one to call out, because it's a screen-reader-critical
runtime library:

- **NVDA Controller Client** — LGPL 2.1, © NV Access. Hearthkin ships
  an unmodified copy of `nvdaControllerClient64.dll` next to
  `Hearthkin.exe` (loaded via `ctypes`) so users on systems without
  NVDA installed at one of the standard paths still get speech.
  Source: <https://github.com/nvaccess/nvda>. The DLL is shipped
  external to the executable so it remains user-replaceable per LGPL
  2.1 §6(b) — drop in your own build of the same-named DLL to
  override.

  **Written offer (LGPL 2.1 §6(c)):** for the source corresponding to
  the bundled `nvdaControllerClient64.dll`, open an issue at
  <https://github.com/glasswings-lang/hearthkin/issues> or pull it
  directly from <https://github.com/nvaccess/nvda> (the canonical
  upstream).

See [`vendor/nvda/README.md`](vendor/nvda/README.md) for the full
LGPL compliance notes and provenance.
