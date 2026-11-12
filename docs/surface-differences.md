# Where Hearthkin's surfaces differ

You can talk to a kin in several places: the desktop chat, rooms, Telegram (private chats and groups), Discord, and scheduled wake-ups. Each place was built at a different time, and things added to one haven't always reached the others. This page lists the differences that matter, in plain words, so you know what to expect where.

Each item says whether the difference is **on purpose** (and why) or **drift** (nobody meant it), and whether it's **fixed**. Last checked 12 November 2026.

## Stopping a kin, and talking to it mid-reply

- **Scheduled wake-ups can't be stopped once they start.** Drift. Nothing stops a wake-up that's running on its own, not even Control Shift X. To do, first.
- **A message sent mid-reply only reaches the reply in Telegram private chats.** On Discord it waits until the reply is done. Discord also quietly drops a second message sent within three seconds of the first. Drift; to do. In Telegram groups the waiting is on purpose, because a message there might be meant for another person.
- **The limit on tool calls in one reply covers Discord and Telegram, not the desktop, rooms or wake-ups.** On purpose for the desktop and rooms, since that's you. Open question for wake-ups.
- **The limit is the same rule on both.** Fixed 12 November 2026. Each person gets their own number if you gave them one, otherwise the kin's. That includes you: sharing a conversation with the desktop doesn't lift it, because those are separate things. Put 0 in your own entry for no limit.

## Per-person settings

- **Telegram and Discord now have the same person window.** Fixed 12 November 2026. One window, built from one list, used by both. For each person: a name, which tools they get, sharing with the desktop, mirroring desktop messages to them, the webcam (ask me, auto-approve, always refuse), a tool-call limit, whether their commands ask first, and whether their files stay inside the kin's folder. Someone on both has an entry in each tab, set separately, but the choices are always the same. A new setting added to the list shows up on both.
- **Commands are now set per person, the same on both.** Fixed 12 November 2026. Each person can be "use the kin's setting", "ask me first" or "run without asking". When a command asks, it asks you, never the person who wanted it. If that person is you on Telegram, you're asked in the chat. Anyone else's request comes to a window on your desktop. Before this, one box for the whole kin decided for everyone. With that box off, a Telegram person with full tools approved their own commands.

## Files and safety

- **Discord could save files outside the kin's folder** when the kin wrote a file out in its reply. Fixed 12 November 2026.
- **Wake-ups now have shell commands either way.** Fixed 12 November 2026. Before, a wake-up running on its own had no commands, while one landing in your open chat did. Now both do. On its own, a command runs if the kin is trusted or full, or you told it to remember that command. Otherwise it waits for no one, doesn't run, and the kin is told nobody was there to ask. Commands that keep running in the background can't start on a wake-up, because nothing would be left to stop them.
- **The webcam is still off for wake-ups that run on its own,** but on when a wake-up lands in your open chat. On purpose for now: a camera in your room shouldn't fire when nobody's there.

## What a kin remembers of its own turns

- **On Discord, a kin never sees its own past tool use, or the notes Hearthkin gives it.** They're saved, but left out when it gets ready to reply. So "you described doing it but didn't" never reaches a Discord kin. Drift; to do.
- **Rooms don't keep tool use either.** On purpose for now.
- **The "tool history kept" setting only works on the desktop.** On Telegram, old tool use comes back in full. Drift; to do.

## Tidying a reply before it's kept

- **One kind of wake-up skips all the tidying.** That's when Hearthkin is open but set not to send wake-ups into the chat. Its reply skips: the clean-up that stops a kin speaking as someone else, removing stray thinking text, saving files written out in the reply, and the "you only described it" notes. That reply still goes to Telegram and the journal. Drift; to do.
- **Wake-ups don't rescue a reply that came back empty.** Elsewhere, if the final reply is blank, Hearthkin keeps what the kin said just before. Drift; to do.
- **On Discord, an empty reply leaves no note for next time.** Elsewhere the kin is told its last reply came back empty. Drift; to do.

## Everyday things

- **Telegram groups don't read text files people send.** Private chats do. Drift; to do.
- **Discord has no commands** like regenerate, undo, clear or status. Telegram groups only have help and whoami. Partly on purpose.
- **Some replies arrive whole instead of growing as they're written:** Telegram replies from a kin without tools, and room replies. Drift.
- **Rooms are the barest place to talk:** no images, voice, regenerate, or saving files written out in a reply. Mostly on purpose, marked "not yet".
- **Wake-ups that land in your open chat are counted as desktop use** in the usage log. Drift; small.

## Keeping this true

Hearthkin's tests include a check that compares the surfaces feature by feature. It doesn't yet cover rooms or every feature on this page. The plan is for every difference here to be either written down as on purpose, with its reason, or to make that check fail with a plain sentence saying what drifted. The details for developers are in surface-differences-technical.md.
