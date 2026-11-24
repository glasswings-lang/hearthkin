# Where things stand

For a developer joining the project. Written 20 November 2026; update it when
the picture changes.

Read in this order:

1. [`CONTRIBUTING.md`](../CONTRIBUTING.md): running it, tests, hooks, and the
   accessibility rules. Arm the hooks before your first commit.
2. [`docs/architecture.md`](architecture.md): the module map and the danger
   zones.
3. This page: what has changed lately, what's open, and what will trip you.

## Working with the maintainer

- **The maintainer is blind and uses a screen reader all the time.** Every
  change to a window has to work by keyboard and screen reader alone.
  CONTRIBUTING has the specifics; they are not optional.
- **The maintainer doesn't read code.** Code review isn't something to ask for.
  What they can do, and do well, is use the app and tell you exactly what
  happened. Treat that report as evidence: if your tests disagree with it,
  suspect the tests first.
- **Explain changes by what they do for the person**, in plain words. "Kin now
  answer when they run out of tool rounds" lands; a list of functions doesn't.
- **Pronouns are it/its.**

## The state of the code

- The last tag is `v0.12.1`. `master` carries a large unreleased round, listed
  under "Unreleased" in [`CHANGELOG.md`](../CHANGELOG.md). Local test builds
  are labelled 0.13.0 by setting `HEARTHKIN_VERSION=0.13.0` before
  `build.bat`.
- The suite (`python tests/run_all.py`) was green at 196 files on 20 Nov 2026.
  `test_ctrl_enter_sends.py` has failed once in a full run and passed alone;
  treat a single failure there as flaky, two as real.
- Most source files use CRLF line endings. If you rewrite a file with a script,
  keep them; `git diff --stat` hides the change, so check the bytes.

## What changed lately, and where it lives

These all landed in November 2026, each with its own test in `tests/`.

- **Compaction.** A person can compact a one-to-one conversation (Chat menu, or
  `/compact` on Telegram and Discord). The kin writes a note; a row with
  `compact: true` and a `keep` count goes into the conversation, and every
  surface sends only `chat_helpers.view_after_compaction(convo)`: the note, the
  last few kept rows, and everything after. Nothing is deleted from disk.
  - The person is told when a conversation passes the kin's `compact_hint_pct`,
    on every surface, again every 10 replies (`chat_helpers.compact_hint`).
  - The note has its own token allowance, `compact_note_tokens`
    (`chat_helpers.compact_options`).
  - A bug fixed on 20 Nov: `kin_persistence._clean_chat_message` dropped the
    `compact` and `keep` keys when reading the conversation back, so every
    restart silently undid compaction and the kin was sent the full window.
    `tests/test_compact_survives_reload.py` pins it.
  - Distillation into long-term memory is separate. It reads the full
    conversation on disk from its own bookmark, so compacting never stops
    anything from being distilled.
- **Memory recall hints.** For a kin that can open files, recall now gives a
  one-line pointer to a matching note, not the note itself, and skips notes the
  kin already has open (`memory_recall.py`, hint mode).
- **Conversation search.** `memory_search` also searches what was said in the
  kin's conversations (`tools/_said_search.py`). With anyone other than the
  kin's own person, the search is scoped by `said_scope`, a hidden parameter
  the surface fills in. A model can't set or widen it. Per-person setting:
  `reach_said` in `tools/_person_settings.py`.
- **Kins' own files.** `identity.md`, `now.md` and `curious.md` in the kin's
  folder, a shared `kin/inbox.md`, and a line saying who else is in the house
  (`kin_persistence.house_line`).
- **Wake-ups.** Scheduled wake-ups run in a subprocess (`hearthkin_cron.py`).
  They respect compaction, and when they send to a person's DM they show the
  `used: …` tool line again (`_wake_tool_lines`).
- **Which places see which messages** (`chat_helpers.visible_rows`). One
  rule every surface asks. The private side (desktop, shared DMs, wake-ups)
  sees a group or Discord channel only if that group's "your private chats
  include this group" switch is on; a group sees the private side only if its
  "this group gets your private chats" switch is on. Rows from elsewhere
  arrive marked with where they were said. Discord DM rows are stored with
  `dm: true` so they count as private.
- **The kin's own person is known by account** (`mark_own_person` in
  `tools/_person_settings.py`). In groups and channels, messages from an
  account marked "This is me" read "<name>, your person"; anyone else using
  that name reads "<name>, not your person".
- **Voice messages in groups and channels** are heard wherever a typed
  message would be answered.
- **Tool-loop limits** (`llm_backend.run_tool_loop`). There are four
  separate guards, set per kin: rounds per reply (`max_tool_iterations`,
  default 8), total calls per reply on Telegram and Discord
  (`remote_max_tool_calls`, default 15), calls in one round
  (`max_calls_per_round`, default 8), and a repeat guard. When the rounds or the total run out, the kin is told so and given one
  turn without tools to answer.

### 23 and 24 November 2026

The plain account, for the maintainer, is [`docs/devlog.md`](devlog.md);
every change gets a line there the same day. In code terms:

- **Copies before anything is removed.** `keep_copy_before_big_rewrite` on
  every conversation save, `keep_room_copy`, `keep_trimmed_rows` (history caps
  hand dropped rows to `history_rows.trim_front(keep=…)`), Telegram /reset
  archives first, `delete_agent` moves to `kin-archive/`. No copy, no change.
- **Say only what happened.** Desktop clear/undo/compact/new go through
  `frame.chat_stream_mixin.put_back_if_unsaved`; Telegram's
  `_truncate_history_for` and `_append_turns_for` return False on a refused
  save; Discord's `append_discord_turn(raise_on_failure=True)`;
  `_backup_prompt_file` raises.
- **Memory.** `practice.md` rides after the base prompt
  (`kin_persistence._practice_section`); `note(file="journal")`; compaction
  rows carry yesterday's and today's journal, frozen
  (`chat_helpers.compaction_startup`); distillation numbers rows and writes
  bookmarks, checked by `bookmarks.py` (quotes, gaps, dates, jsonl lines).
- **Prompt review**: `docs/private/prompt-review-2026-11-24.md`.

## Two things that will catch you out

- **The prompts in the code are only the defaults.** On first run, the base
  prompt is copied to `~/.hearthkin/base_prompt.md`, and that file wins from
  then on. A kin can have its own `base_prompt.md` that wins over both. The
  same goes for the files in `~/.hearthkin/prompts/`. Changing
  `DEFAULT_BASE_PROMPT` changes new installs only; an existing install has to
  have its file edited, with a backup.
- **Tool docstrings are prompts.** The first paragraph of a tool's docstring is
  what the model reads. Editing it changes kin behaviour.

## The surface map

`tests/_surface_matrix.py` lists every capability against every surface: the
desktop window, rooms, Telegram, Discord and wake-ups. Each cell is Present
(proved by a marker in the code), Absent (a real gap, with its reason) or
NotHere (the question doesn't arise there). `tests/test_surface_parity.py`
fails if a Present cell stops being wired, or an Absent one gets built without
the map being updated, and it prints the open gaps on every run.

When you add something to one surface, add the capability to the map. That
check is how the project finds out that a fix landed in one place and not the
others.

## Open work

- **The surfaces drifted apart, and are being brought back together.**
  Each place a kin talks through (desktop, rooms, Telegram DMs and groups,
  Discord DMs and channels, wake-ups) was built separately and carries its
  own copy of how Hearthkin does things. The agreed direction, from 20 Nov
  2026, is the standard one for multi-platform bots: one shared core, thin
  per-app adapters. Anything that exists in more than one place moves into
  the core, whether or not it has broken yet, and new features start there.
  An audit of conventions across surfaces (not just features) is under way.
  Discord's tab is being brought into line with Telegram's: a channel list
  with a window per channel (when the kin answers, a mute list, the two
  sharing switches), people only for direct messages.

- **Rooms can't be compacted.** This is the one open gap on the surface map. A
  long room sends every kin the whole transcript. The agreed design has two
  cases:
  - *A room shared into each kin's own conversation* would use that kin's
    existing compaction. That needs a new per-room option to share the room
    into each kin's conversation, with rows tagged by room. The existing
    per-room switch, `distill_to_memory`, only feeds the summariser; it does
    not share the conversation.
  - *A room kept apart* needs a compaction point per kin inside the room's own
    history, each sized by that kin's own context window and compaction
    settings, with a note per kin.
  - Once rooms can be compacted, also check the per-reply tool cap and the
    long-reply warning for rooms that run unattended.
- **The user guide** was caught up on 24 Nov 2026 (copies in one place, restore,
  bookmarks, compaction, a kin's own files, wake-up switches, tool verdicts, the
  model-swap note). Keep it current: a line in `docs/devlog.md` and the guide
  with every change a person can see.
- **Heavy reading is costly on hosted models.** Each file a kin reads stays
  attached for the next `tool_history_keep` rounds (default 5), and each
  further tool call in a reply resends everything. A kin reading deeply can
  fill a 64k window in one reply. The maintainer has chosen to keep the full
  reading for now. Don't change a kin's defaults to save cost without asking.

## Building a release

```
set HEARTHKIN_VERSION=0.13.0
build.bat < nul
```

Run it from a `cmd` window inside the repo folder. On some Windows setups
`cmd` won't run a `.bat` by bare name from another folder, so use
`cmd /c "cd /d <repo> && <repo>\build.bat < nul"`. Without
`HEARTHKIN_VERSION`, the script takes the version from a tag on HEAD.
The installer lands in `dist\`. To confirm a fix made it into the build, read
the module out of the PyInstaller archive (`PyInstaller.archive.readers`) and
look for a string the fix added. Don't trust the timestamp.
