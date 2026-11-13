# Surface differences: developer notes

The plain-words list is docs/surface-differences.md. This file is the part for people reading code.

The parity check is `tests/_surface_matrix.py` with `tests/test_surface_parity.py`. It compares five surfaces by source markers: desktop (`frame/*.py` except rooms), rooms (`frame/rooms_mixin.py`, added 12 Nov 2026), Telegram (`telegram_bot.py`), Discord (`discord_bot.py`) and cron (`hearthkin_cron.py`, `cron_helpers.py`). Each capability cell is `Present`, `Absent` (drift, with a sentence) or `NotHere` (on purpose, with the reason). Rooms' answers are one block, `ROOMS`, at the bottom of the matrix; the module refuses to load if a capability has no room answer.

Not yet covered:

- heartbeats as a surface (they run through the desktop frame);
- the Telegram private/group split (one column covers both).

Open gaps the check lists every run (12 Nov 2026): rooms have no images, no shared files, no park moves, and no recalled-memories footer; Telegram and Discord have no voice-note transcription.

Fixed on 12 Nov 2026, with the test that pins each:

- Discord reads back tool and system turns (`test_discord_catchup.py`), queues instead of dropping inside the cooldown, interjects, coalesces, says when it's busy, has commands (`test_discord_commands.py`) and whole park turns in DMs (`test_discord_park_turn.py`).
- Reach-out, wake-up places and the heartbeat share `reach_places.py`, with Discord (`test_reach_discord.py`).
- Cron: `should_stop` through `_cron_tool_loop` and Ctrl+Shift+X (`test_cron_stop.py`); both paths finish through `_finish_cron_reply` (`test_cron_finish.py`).
- Tool-history compaction is `chat_helpers.compact_tool_history`, used by desktop, rooms, Telegram and Discord.
- Rooms keep each kin's tool turns tagged with `speaker`, read back to that kin only; run the authoring bridge and read nudge through `turn_steering`; and route a stored `system` row to its kin as a note (it used to come back as the kin's own words). `test_room_tool_history.py`.
