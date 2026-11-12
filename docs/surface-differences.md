# Where Hearthkin's surfaces differ

A kin can be talked to in several places: the desktop chat, rooms, Telegram (one-to-one and groups), Discord, scheduled wake-ups, and heartbeats. Each place was built at a different time, and features added to one have not always reached the others. This page lists the differences that matter, in plain words, so an operator who doesn't read code can know what to expect where.

Made from a full read-through of the code on 12 November 2026. Items marked **checked** were confirmed by hand against the code. The rest come from the read-through and are very likely right, but treat them as "probably" until checked.

Each item says whether the difference is **on purpose** (with the reason) or **drift** (nobody meant it), and whether it has been **fixed**.

## Stopping a kin, and talking to it mid-reply

- **Scheduled wake-ups can't be stopped once they start.** Drift. A wake-up that runs on its own, which is any time Hearthkin is closed and often when it's open, has no stop at all. Ctrl+Shift+X doesn't reach it either. **Checked.** To do.
- **Messages sent mid-reply reach the reply only on Telegram private chats.** On Discord, a message sent while the kin is replying waits until the reply is finished, and a second message sent within three seconds of the first is silently thrown away (a flood guard). **Checked.** The Discord part is drift; to do. In Telegram groups this is on purpose: someone might be talking to another person, not the kin. That was fixed tonight; it briefly applied to groups too.
- **The per-reply limit on tool calls applies on Discord and Telegram, not on the desktop, rooms or scheduled wake-ups.** On purpose for the desktop and rooms, since that's you. For scheduled wake-ups, it's an open question.
- **On Discord, the limit also applies to you.** Discord has no way to tell the operator apart from anyone else yet. Telegram counts you as the operator when "share with desktop" is on for you. Drift-ish; to decide.

## Per-person settings

- **Discord has fewer per-person settings than Telegram.** Telegram lets you set, per person: a name, tool access, a tool-call limit, sharing with the desktop conversation, mirroring desktop replies to them, and webcam permission (ask, always allow, never). Discord has tool access and the tool-call limit only. Sharing is one setting for the whole kin, and webcam always asks. Drift; to do.
- **Who approves a remote command differs.** On Discord, any shell command goes to a dialog on your desktop, so only you can approve it. On Telegram, the approval prompt goes to the person who asked, in the chat, and they answer it; your desktop is only told. Probably drift, and the more important of the two: on Telegram, someone you've given full tool access approves their own commands. To decide.

## Files and safety

- **Discord saved files from fenced blocks outside the kin's folder.** Drift, and a real gap: Discord's file tools were kept inside the kin's folder, but a file written out in a reply wasn't. **Checked. Fixed 12 Nov 2026.**
- **Scheduled wake-ups have different powers depending on which kin is on screen.** When a wake-up runs on its own, shell commands and the webcam are taken away from it. When it's delivered into the open desktop chat (the kin is on screen and nothing else is happening), it gets the kin's full tools, shell commands included. Drift; to decide.

## What a kin remembers of its own turns

- **On Discord, the kin never sees its own past tool calls or the notes it's been given.** They're saved, but the code that rebuilds the conversation for the next reply keeps only plain messages. So "you described a tool call but didn't make one" notes never reach a Discord kin, and it can't see what its tools did last time. **Checked.** Drift; to do.
- **Rooms don't keep tool calls either.** On purpose for now; the code says so.
- **"Tool history kept" (Settings, Tools) only works on the desktop.** On Telegram, old tool calls come back in full, not summarised. Drift; to do.

## Tidying a reply before it's kept

- **One kind of scheduled wake-up skips all the tidying.** When Hearthkin is open but set not to send wake-ups into the chat, the wake-up's reply skips: the clean-up that stops a kin speaking as someone else, the removal of stray thinking text, saving files from fenced blocks, and the "you only described it" notes. That reply still goes to Telegram and the journal. Drift; to do.
- **Scheduled wake-ups don't recover a reply that came back empty.** Other places keep what the kin said before its last tool call, if the final reply is blank. Drift; to do.
- **On Discord, an empty reply leaves no note.** Elsewhere the kin is told next turn that its reply came back empty. Drift; to do.

## Everyday features

- **Telegram groups don't read text files people send.** Private chats do; groups only see the caption. Drift; to do.
- **Discord has no commands** (no regenerate, undo, clear or status). Telegram groups have only help and whoami. Partly on purpose.
- **Telegram replies from a kin without tools don't appear as they're written**; they arrive whole. Kin with tools, and Discord, show the reply growing. Room replies also arrive whole. Drift.
- **Rooms are the thinnest surface:** no images, voice, regenerate, park, or saving files from fenced blocks. Mostly on purpose, written down as "not yet".
- **Scheduled wake-ups delivered into the desktop chat are counted as desktop use** in the usage log, so they can't be told apart there. Drift; small.

## Keeping this true

The code already has a check that compares four surfaces (desktop, Telegram, Discord, scheduled wake-ups) on about thirty features: `tests/_surface_matrix.py` and `tests/test_surface_parity.py`. It doesn't yet cover rooms, heartbeats, or the Telegram group/private split, and it doesn't know about the features added on 11 and 12 November (stopping between tool calls, mid-reply messages, the tool-call limits, the long-reply warning, folder rules for fenced saves). The next step is to extend that check, so that every difference on this page is either written down as on purpose, with its reason, or makes the tests fail and say so in a sentence.
