# Surface differences: developer notes

The plain-words list is docs/surface-differences.md. This file is the part for people reading code.

The parity check is `tests/_surface_matrix.py` with `tests/test_surface_parity.py`. It compares desktop, Telegram, Discord and cron (`hearthkin_cron.py`) by source markers. Each capability cell is `Present`, `Absent` (drift, with a sentence) or `NotHere` (on purpose, with the reason).

Not yet covered:

- rooms and heartbeats as surfaces;
- the Telegram private/group split;
- the 11–12 Nov 2026 features: stop between tool calls (`should_stop`), mid-reply messages (`interject`), `max_tool_calls` and `resolve_tool_call_cap`, the long-reply warning (`on_long_reply`), fence confinement (`confine=`), per-person exec approval (`resolve_exec_approval`, `user_exec_approval`), cron exec (`_wrap_cron_exec`, now `Present` in the matrix).

Goal: every item in the plain page is either a `NotHere` with its reason or a failing cell, so new drift breaks the suite instead of waiting for an audit.

Known code-level facts behind the plain items (checked 12 Nov 2026):

- Discord `_channel_history` keeps only plain user/assistant text; tool and system turns are dropped.
- Discord's 3-second per-person cooldown drops the message rather than queueing it.
- The cron isolated path passes no `should_stop`; Ctrl+Shift+X (`_on_stop_everywhere`) doesn't reach it.
- The cron inject-off path skips the tidy chain (roleplay corrective, thinking-text strip, authoring bridge, read nudge).
- Tool-history compaction runs on the desktop path only.
