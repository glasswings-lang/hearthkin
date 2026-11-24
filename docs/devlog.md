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
- **Bringing a conversation back is findable and safe.** File, Restore a
  kin's history now lists every copy Hearthkin kept of that kin's
  conversation, newest first, with the date, how many messages, and what made
  the copy, so there's no browsing hidden folders. Restoring no longer sends
  the restored part to the memory writer by itself (it used to, which is how
  months got squeezed); Redistill does that when you choose. Clears and new
  conversations say how to bring the old one back. Undated messages at the
  start of a copy now go next to the message they led into, not to the front.
- **Every copy has one shape and one place.** Whatever removes conversation
  (clear, new conversation, /new, /clear, /reset, the history cap) now keeps
  its copy the same way: one message per line, like the live conversation,
  in the kin's own backups, in the cleared folder, named for what made it and
  when. Copies used to come in two shapes across five places, one outside
  the kin's folder; /new even made two. Old copies stay where they are and
  the restore window still reads them.
- **Tests no longer freeze the screen reader.** The test suite runs at the
  lowest priority, so NVDA always gets the computer first, and the tests that
  build windows only run when asked for, after telling the person. A night of
  repeated full runs had been freezing NVDA.
- **One kin's conversation is whole again.** Its March-to-November
  conversation, tonight's 68-message stretch and its current conversation are
  one record, plus 1,999 messages from May to July that existed only in old
  copies. Nothing is doubled; what the kin is sent right now is unchanged; its
  first 7,475 lines are exactly as before, so its line references still hold.
  Copies of everything are in its backups.
- **Hosted Claude reuses the conversation from its cache.** The usage log
  showed every new message re-paying for the whole history (only the system
  prompt was cached between messages). A second cache marker fixes it. The
  cache setting's help now explains 5 minutes versus 1 hour in plain words.
- **Hearthkin learns from real wake-ups which models use tools.** A model
  that had memory to tend and never called a tool is noted as "doesn't call
  tools"; from then on its memory is handed over as text and it's never
  pushed to call tools it can't (pushing only made it invent results). The
  kin's Tools tab says plainly which it is: calls tools, doesn't, or not known
  yet.
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
