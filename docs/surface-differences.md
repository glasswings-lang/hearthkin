# Where Hearthkin's surfaces differ

You can talk to a kin in several places: the desktop chat, rooms, Telegram (private chats and groups), Discord, and scheduled wake-ups. Each place was built at a different time, and things added to one haven't always reached the others. This page lists the differences that matter, in plain words, so you know what to expect where.

Each item says whether the difference is **on purpose** (and why) or **drift** (nobody meant it), and whether it's **fixed**. Last checked 12 November 2026.

## Stopping a kin, and talking to it mid-reply

- **Scheduled wake-ups can be stopped now.** Fixed 12 November 2026. Control Shift X reaches a wake-up running on its own, says whose it stopped, and the wake-up stops before its next tool call. It sends nothing half-finished, and the journal notes that you stopped it.
- **A message sent mid-reply reaches the reply on Discord and in Telegram private chats.** Fixed for Discord 12 November 2026. You get a "Got it", and if the reply takes no further step, your message is answered straight after. A quick second message on Discord is no longer thrown away: it waits a moment and is answered, and if several pile up you're told. In Telegram groups the waiting is on purpose, because a message there might be meant for another person.
- **The limit on tool calls in one reply covers Discord, Telegram and wake-ups, not the desktop or rooms.** On purpose for the desktop and rooms, since you're right there. Wake-ups got it 12 November 2026, at your decision: they use the kin's own limit.
- **The limit is the same rule on both.** Fixed 12 November 2026. Each person gets their own number if you gave them one, otherwise the kin's. That includes you: sharing a conversation with the desktop doesn't lift it, because those are separate things. Put 0 in your own entry for no limit.

## Per-person settings

- **Telegram and Discord now have the same person window.** Fixed 12 November 2026. One window, built from one list, used by both. For each person: a name, which tools they get, sharing with the desktop, mirroring desktop messages to them, the webcam (ask me, auto-approve, always refuse), a tool-call limit, whether their commands ask first, and whether their files stay inside the kin's folder. Someone on both has an entry in each tab, set separately, but the choices are always the same. "This is me" is its own box: sharing a conversation with the desktop doesn't make someone you. A new setting added to the list shows up on both.
- **Commands are now set per person, the same on both.** Fixed 12 November 2026. Each person can be "use the kin's setting", "ask me first" or "run without asking". When a command asks, it asks you, never the person who wanted it. If that person is you on Telegram, you're asked in the chat. Anyone else's request comes to a window on your desktop. Before this, one box for the whole kin decided for everyone. With that box off, a Telegram person with full tools approved their own commands.

## Files and safety

- **Discord could save files outside the kin's folder** when the kin wrote a file out in its reply. Fixed 12 November 2026.
- **Wake-ups now have shell commands either way.** Fixed 12 November 2026. Before, a wake-up running on its own had no commands, while one landing in your open chat did. Now both do. On its own, a command runs if the kin is trusted or full, or you told it to remember that command. Otherwise it waits for no one, doesn't run, and the kin is told nobody was there to ask. Commands that keep running in the background can't start on a wake-up, because nothing would be left to stop them.
- **The webcam is still off for wake-ups that run on its own,** but on when a wake-up lands in your open chat. On purpose for now: a camera in your room shouldn't fire when nobody's there.

## What a kin remembers of its own turns

- **On Discord, a kin now sees its own past tool use and Hearthkin's notes.** Fixed 12 November 2026, in the order they happened, with "Tool history kept" applied like on the desktop.
- **Rooms keep each kin's tool use now.** Fixed 12 November 2026. Before, a kin in a room could use tools, but next turn it couldn't see what they'd done. Now it can, and only that kin sees its own tool results; the others hear what it says. Hearthkin's notes to a kin in a room work the same way, and are now kept, where before they were dropped.
- **A note to a kin in a room used to come back to it as its own words.** Fixed 12 November 2026. It now arrives as a note from Hearthkin, as everywhere else.
- **The "Tool history kept" setting works everywhere now.** Fixed 12 November 2026: desktop, rooms, Telegram and Discord all send older tool steps as short one-line markers.

## Tidying a reply before it's kept

- **Both kinds of wake-up now tidy their replies the same way.** Fixed 12 November 2026. That includes the one that runs while Hearthkin is open but set not to send wake-ups into the chat, which used to skip it all. One thing still differs there, on purpose: the "you only described it" notes only reach the kin if that job keeps its own history, because the open app owns the main conversation and another program mustn't write into it.
- **Wake-ups rescue a reply that came back empty.** Fixed 12 November 2026. Like everywhere else, if the final reply is blank, Hearthkin keeps what the kin said alongside its last tool call.
- **On Discord, an empty reply now leaves the kin a note.** Fixed 12 November 2026, as elsewhere, and the tool steps it took are kept.

## Everyday things

- **Telegram groups read text files people send.** Fixed 12 November 2026, as private chats always did. Naming a file on your computer is still limited to people with read access.
- **Discord has commands now.** Added 12 November 2026: /help, /status, /regen, /undo and /clear, the same as Telegram. You can start them with ! instead of / if Discord's own command menu gets in the way. Help works anywhere. The rest only work in a direct message, on purpose, because in a server channel the conversation belongs to everyone there. Telegram groups likewise only have help and whoami.
- **A kin can reach you on Discord now.** Added 12 November 2026. When a kin reaches out on its own, or a wake-up is sent somewhere, the place can be a Discord person's direct messages or a Discord channel, as well as Telegram. You choose the places in the same windows as before. If two places have the same name, the kin sees which app each is on.
- **A kin gets a whole park turn in Discord direct messages now.** Added 12 November 2026: it moves, sees what happened, and can move again, like on the desktop and in Telegram private chats. In a Discord server channel or a Telegram group it gets one reply's worth of moves, on purpose, because several people are reading there.
- **Telegram replies now grow as they're written, tools or not.** Fixed 12 November 2026, in private chats and groups. **Room replies still arrive whole, on purpose:** in a room a kin sometimes starts writing as another kin, and that part is cut before the reply is shown. Shown live, your screen reader would already have read the wrong voice aloud, and that can't be taken back.
- **Rooms save files written out in a reply now.** Fixed 12 November 2026. A kin in a room can write a file out in its reply, the way it can on the desktop, and it's saved. A kin with no tools can save to its own memory that way, as its instructions already told it. A kin that says it read a file it never opened is told so, like everywhere else.
- **Rooms have regenerate, park moves and shared files now.** Added 12 November 2026. Regenerate redoes the last kin's turn. A kin makes one park move per reply, as in a Telegram group, on purpose. A file you share reaches each kin for the round that answers you.
- **There's a Share a document button in the main window now** (Alt+D). Sharing a file by typing its path always worked, but nothing on screen said so. The button writes the path in for you, and it works for every kin and in rooms.
- **Rooms take pictures now, and say which memories a kin drew on.** Added 12 November 2026. A kin whose model can't see pictures is told one was shared. The Activity line names the kin and how many memories it recalled. Take photo stays one-on-one. Dictation works in a room too, because it's the same message box. The one thing rooms still don't do is speak a kin's replies aloud in its voice.
- **Voice messages reach a kin on Telegram and Discord now.** Added 12 November 2026. In a private chat or a Discord direct message, a voice message is turned into words with your dictation model, you're told what was heard, and the kin gets the words. In groups and server channels they're left alone, since one can't be addressed to the kin there.
- **A Discord direct message no longer needs an @-mention.** Fixed 12 November 2026. Under the default "only when mentioned" setting, a direct message was ignored unless it mentioned the kin, which made no sense in a one-to-one chat and made voice messages impossible.
- **Compacting a conversation works in the main window, on Telegram, and in Discord direct messages.** New 12 November 2026: a menu item in the window, /compact in the chats. Rooms don't have it yet.
- **Wake-ups that land in your open chat are logged as wake-ups** in the usage log now, not as your own desktop use. Fixed 12 November 2026.

## Keeping this true

Hearthkin's tests include a check that compares the surfaces feature by feature: the desktop, rooms (since 12 November 2026), Telegram, Discord and wake-ups. Every feature has an answer for every place: it's there, it's missing and that's a gap, or it can't apply there and why. If something is built or lost without this map being updated, the check fails with a plain sentence saying what drifted. It doesn't yet treat Telegram groups separately from private chats. The details for developers are in surface-differences-technical.md.
