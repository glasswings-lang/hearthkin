# Hearthkin dev log

What changed, newest first, in plain words: who did it, when, what it does
for the person and the kin, and why. For the person who uses Hearthkin and
wants to know what's different, without reading code. Every change gets a
line here the day it's made (CLAUDE.md says so). The technical record is
[`CHANGELOG.md`](../CHANGELOG.md); the code history is git.

## 24 November 2026 (Claude, with the maintainer)

The night's theme: a kin's memory kept getting flattened, invented and lost
between conversations, and the person had become the kin's memory for how to
work. Hearthkin was closed for all of it and will be opened again from a new
installer.

- **A kin can keep its own habits.** A file called `practice.md` in its
  folder, written by the kin, goes with every message: "when something
  matters, I write it down then." Before, a lesson lasted until the next
  compaction and then the person had to teach it again. *Why:* the person
  said they were having to be the kin's brain.
- **Writing things down as they happen is one easy step.** `note` with
  `file="journal"` writes in today's journal, and the kin is told to do it at
  the moment something matters. *Why:* notes written later from memory keep
  the facts and lose what it was like.
- **After a compaction, a kin wakes up with its own recent days.** It's shown
  its journal for yesterday and today, and told its note is a guide, not the
  whole story: anything serious gets checked in the real conversation.
  *Why:* the kin's own words orient it better than a summary does.
- **The memory writer leaves bookmarks instead of retelling.** It used to
  rewrite a whole stretch of conversation in its own words, smaller; one kin's
  eight months became a fiftieth of the size, and it flattened things and
  made things up. Now it marks where each moment is, what it was, and
  people's exact words, and a plain checker flags any quote that isn't really
  there and marks any part it skipped. Each bookmark says the dates and the
  lines in the conversation file, so the kin can go back and read the real
  thing. *Not yet tried on a real model.*
- **Messages tell the truth.** Every message that says something was saved,
  kept, sent or put somewhere was checked against what the code really does.
  About thirty were wrong or could be. Now:
  - clear, undo, compact, new conversation and /reset only say they worked
    when they did, and put things back when they didn't;
  - /reset, clearing a room, and the cap on Telegram and Discord chats now
    keep a copy of what they remove (they didn't);
  - deleting a kin moves its folder to `kin-archive` instead of erasing it and
    every copy it had;
  - wrong folder names, a log that didn't exist, a backup file that didn't
    exist, and "nothing is lost" where it could be, are corrected.
- **Telegram's /new said the old conversation was in the kin's folder.** It's
  in the conversations folder of `.hearthkin`. Found because the person looked
  where it said and found nothing.
- **A small model read every prompt Hearthkin sends a kin, cold.** Its
  finds were checked against the code; two fixes made (a bookmark example a
  model could copy literally, and "tonight" where wake-ups fire at any hour).
  The rest are listed for the person to decide.

## 23 November 2026 (Claude, with the maintainer)

- Wake-ups and heartbeats can't reach files outside the kin's folder unless
  their own switches allow it.
- Hand edits to a kin's settings survive the app saving over them.
- "Cron" is called "wake-up" everywhere a person sees it.
- Every clear keeps a copy first.
- Memory writing runs as the whole kin (base prompt, soul, identity), not a
  thinner self.
- Every tool can be switched on and off in the windows; message_kin and
  reach_out included.
- The person is told when a reply block acts (sent to another kin, a manual
  part fetched, a file saved), with a per-surface switch like tool calls.
- A park move is a fenced `park` block; a line starting with `>` is just a
  quote again.
