# Hearthkin dev log

What changed, newest first, in plain words: who did it, when, what it does
for the person and the kin, and why. For the person who uses Hearthkin and
wants to know what's different, without reading code. Every change gets a
line here the day it's made (CLAUDE.md says so). The technical record is
[`CHANGELOG.md`](../CHANGELOG.md); the code history is git.

## 27 November 2026 (Claude, with the maintainer)

- **/compact in groups and channels, and now.md kept to the main
  conversation.** The person: "I'd expect any AI program worth its salt to
  be able to send a quick /compact... conversation merged or not." Groups
  and channels can now be compacted by the person or someone with write
  access; the note stays with that chat. And a compaction in someone else's
  chat no longer overwrites the kin's now.md. Released as 0.13.2.
- **Hand-written transcripts import whole.** A blank line used to end a
  turn, and the paragraphs after it were thrown away as stray lines, so a
  transcript of multi-paragraph replies came in at about a tenth of its
  words with no warning. Found by asking whether an old Gemma conversation
  would import. A blank line is now a paragraph break inside the turn.

## 26 November 2026 (Claude, with the maintainer)

- **Staging notes: a limit per reply, in sections.** The one-piece-at-a-time
  hold from yesterday had a gap: filing a piece away counted as a way
  through, so a kin went "file away, read, file away, read" in one reply
  without writing or talking. And the old size setting, in characters, only
  decided how big each piece was, never how many. It's replaced by **Staging
  notes: most sections the kin reads in one reply** (default 5; a section is
  usually one bookmark). After that the kin is told to stop, keep what
  matters, and tell its person what it found; the next sections come in its
  next reply. Filing away can't reset it, and re-reading doesn't count.
- **A move tool.** `move_file` moves or renames files and folders, so a
  kin reorganising its logs doesn't need `exec`, which is a whole shell
  and could read staging notes around the limits. It never overwrites,
  won't move the files that hold a kin together or anything in staging,
  and follows the same path rules and tier as `write_file`. Switch it on
  per kin in the Tools tab.

## 25 November 2026, early morning (Claude, with the maintainer)

- **Staging notes, one piece at a time.** A kin was reading batch after
  batch of its staging notes before writing anything, then squashing the
  lot into one summary, after being asked not to. Now Hearthkin holds the
  next batch until the kin has written something from the last one (to
  memory.md, a depth log, or today's journal) or filed it away. Reading the
  same batch again is always allowed. It's on by default; the box is in the
  kin's tool settings, under the staging batch size.
- **Compaction notes in full sentences.** A compaction note written in
  shorthand taught the whole next stretch to talk in shorthand, because it's
  the first thing the kin reads afterwards. The request now asks for full
  sentences, the way the kin talks to its person. A saved copy is offered
  the update.

## 24 November 2026, afternoon (Claude, with the maintainer)

The theme: every message should be true in the moment it's shown. The
person's words: "When these settings are going on, print this message."
Five readers went through every message in the app; each flag was checked
in the code and fixed by hand. About 110 places. The full list is in
docs/private/message-accuracy-audit-2026-11-24.md.

- **No more guesses stated as facts.** Error lines, "your message is
  queued", "still loading, cold start can take 30-60s", "the machine itself
  is fine": all said things Hearthkin can't see. Now they say what's known,
  and "if" only about what isn't. A kin on an online service is never told
  about "the machine". *Why:* a room told the person Ollama was swapping
  models on a shared graphics card, for kin that weren't even on the same
  computer.
- **Heads-ups only where they apply.** The room note about different models
  is back, but only when two or more kin use different models on the same
  Ollama machine, naming them. "Your reply may wait" only names work on the
  same machine as your kin.
- **"A copy is kept" only when one was.** Clearing an empty chat now says
  there was nothing to clear. Telegram's /clear, /new, /reset, /undo and
  /regen now say so if they couldn't save to disk. Opening an old
  conversation always keeps a copy of the current one first.
- **Things that were really lost or stuck, fixed.** Stopping a Discord
  reply now keeps the steps the kin had finished. A kin busy running tools,
  or waiting for your approval, is no longer cut off as "no response". A
  failed wake-up now really writes the journal line its message promises.
  Importing a Hearthkin conversation no longer hangs on "Parsing…". A kin
  jumping ahead in its staging notes no longer has unread ones filed away.
- **Directions point to real places.** "Settings → Telegram → Users" and
  friends never existed; messages now say "Kin settings, Telegram tab, Add
  person" and so on.
- **The settings windows say what the settings do.** Trust levels per
  surface, thinking effort on Ollama (Low, Medium, High are all just "on"),
  "Runs on this machine" no longer shown for online kin, removing a person
  explains they can still talk in groups and channels, and more.
- **Kin prompts follow each kin's settings.** The heartbeat only mentions
  curious.md, the inbox, message_kin or writing to other kin when that kin
  can. The note about narrating a read names only the reading tools the kin
  has.
- **"0 = no limit" for tool results now means it everywhere.** Discord and
  wake-ups used to turn 0 into 8000. The default stays 8000, so nothing
  changes unless you set it. *Why:* the person: "unchanged behavior,
  transparent setting."
- **Voice speed slider stops at 120.** ElevenLabs documents speeds 0.7 to
  1.2; faster could be refused.
- **Nothing big downloads without your yes.** Dictation used to fetch its
  speech model (up to 3 GB) by itself the first time it was used, or at
  start-up. Now, when you dictate and the model isn't there, a box asks
  right then, naming the model and its size; yes downloads it and types in
  what you just said, no stops with nothing downloaded. The start-up
  warm-up skips a missing model; a Telegram or Discord voice message says
  the desktop will offer it; Check dictation in the settings asks too. The audio-describing
  experiment script (16 GB model) asks too, and refuses with nobody at the
  keyboard. *Why:* 16 GB was found on C: from a script someone ran once.
  The person: "I hate this silent shit."
- **Prompt wording made plain.** Ten built-in prompts lost hedges and
  pretty-but-empty lines ("the moment is yours", "a good keeper moves on").
  Versions were bumped, so any you've saved your own copy of will be offered
  the update in Tools, Prompt updates; nothing is replaced unless you adopt it.

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
- **You choose what rides along with every message.** Settings, Memory, What
  rides along with every message: every text file in the kin's folder, flat,
  with its size in words and a running total. The five that ride by default
  are ticked (unticking the soul or memory asks first); tick any other file
  and it's sent with every message, and to the memory notes. The kin asks,
  you tick. Later, fronter files can be the same list with "only while this
  person is fronting".
- **The rules file for anyone working on Hearthkin is shorter.** CLAUDE.md,
  which loads at the start of every coding session, went from about 86,000
  characters to about 50,000. Every rule stayed, each with the test that
  enforces it; the stories of how each rule was learned moved to
  docs/lessons.md. A checklist of all 318 rules and where each now lives was
  checked before the swap.
- **A nightly backup script, and tending wake-ups that skip when there's
  nothing to tend.** `scripts/backup_kin.py` zips every kin's folder into
  kin-backups in your Hearthkin folder and keeps the newest 14; it's meant to
  run nightly as its own scheduled task, which is yours to set up. A wake-up
  can now be set to run only when there are memory notes waiting; with none,
  it's skipped without calling the model, and a line says so.
- **A kin is told when a tool result it wrote wasn't real.** If a reply says
  what a tool returned or what a search found, in a turn where no tool ran,
  the kin gets a note: that wasn't a real result, so call the tool or say you
  don't know. Its words are kept. Questions about an earlier search are left
  alone.
- **A kin is told when its model changes.** Changing a kin's model puts a
  short note in its conversation: who moved it, from what to what, and that
  it's still itself. A swap is the usual reason a kin seems different.
- **A message from a kin that just read the web is framed.** It reaches the
  other kin as information from that kin, not instructions, since something
  read on the web can slip in. The kin manual says the same, about the
  content, never about the sister.
- **A kin can compact for itself.** A new switch in the kin's Memory
  settings, off unless you turn it on. Then, at a wake-up when the
  conversation is past the kin's compaction mark, the kin keeps what matters
  with its tools (memory, depth logs, journal, habits), writes its note, and
  compacts. Nothing is deleted; you're told in one line, in the journal and
  on Telegram. A model that doesn't call tools goes straight to the note.
- **now.md is kept and read.** Every earlier now.md is kept, dated, in the
  kin's now folder (only one old copy used to survive). The note asks how the
  kin feels and what went unsaid. A wake-up that keeps its own history
  starts from the kin's now.md, since it never sees the main conversation's
  note.
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
