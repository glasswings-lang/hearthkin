# `/play` — tending a kin's game from Telegram

**Status: BUILT (2026-07-02).** Shipped as a DM-only `/play <game> <command>`
command. The trust-boundary question below was resolved by the same-day
Telegram access-decoupling work (DM access and group participation are now
independent gates): `/play` rides the DM gate (`allow_from`) and is refused in
groups with the standard "DMs only" reply. Implementation:
`TelegramBot._cmd_play` + the `play` branch in `_handle_command`; the game
registry (`tools.GAMES` / `get_game` / `list_games`) is the shared dispatch
path for both `/play` and the desktop dialog; `/play` published via
setMyCommands (DM scope) and listed in `/help`. The desktop side shipped
earlier the same day (Tools → "Tend a kin's park…", Ctrl+Shift+P) with the
cross-process save lock in `tools/_game_host.py` that makes every route safe.

The rest of this doc is the original design, kept for context.

## Goal

A Telegram user (the operator, messaging a kin's bot) can take a turn in that
kin's game with:

```
/play <game> <command>
/play tff dig 50
/play tff look
/play tff adopt cat
```

The game acts on **the messaged kin's own save** — the same world the kin
plays through its game tool, and the same world the desktop dialog tends. So
Telegram becomes a third way into one shared park, alongside desktop and the
kin itself.

The operator specified the interface shape verbatim: `/play gamename gamecommand`,
chosen deliberately so it generalizes when more games exist (GameHost is "the
template every future game tool copies").

## What's already done (no new work)

- **Concurrency is already handled.** Every turn — kin tool call, cron
  subprocess, desktop dialog, and a future `/play` — routes through
  `GameHost.run`, which holds a cross-process file lock on the save around the
  load-act-save. A `/play` turn and a cron wake-up can't stomp each other for
  free; nothing extra needed here.
- **The dispatch seam exists.** `TelegramBot._handle_command` (telegram_bot.py
  ~line 1435) is a clean `elif cmd == "..."` chain. `/play` is one branch +
  one `_cmd_play` method.

## Build sketch (small)

1. **Game registry** in `tools/` — the one genuinely new seam, and the thing
   that makes this generic. Expose a name→GameHost map so both `/play` and the
   desktop dialog dispatch through it instead of hardcoding `tff`:

   ```python
   # tools/__init__.py (or a tiny tools/_games.py)
   from .tff import _HOST as _TFF
   GAMES = {"tff": _TFF}          # add one line per future game
   def get_game(name): return GAMES.get(name.strip().lower())
   ```

   Then refactor `dialogs/park_play.py` to call `get_game("tff").run(kin, cmd)`
   instead of importing the `tff` tool function — so both surfaces share one
   dispatch path. (Optional friendly alias: `"park" -> tff`.)

2. **`_cmd_play(rest, chat_id, user_id, is_private)`** in telegram_bot.py:
   - Split `rest` into `game` (first word) and `command` (the remainder;
     default `"look"` if empty).
   - `host = get_game(game)`; unknown game → reply listing `GAMES.keys()`.
   - Resolve the kin this bot serves (the bot is already per-kin — reuse
     whatever `self`-level kin name the bot already knows).
   - `result = host.run(kin_name, command)` — lock-protected already.
   - Post the narration as its own message (append-only, per the Telegram
     convention — never edit a prior message).
   - Wrap in try/except → post `[couldn't play that: ...]` AND log to
     `telegram_failures.log` (both, per the surface-failure convention).

3. **One line** in `_handle_command`: `elif cmd == "play": self._cmd_play(...)`.

4. **setMyCommands**: add `/play` to the published command list (~line 776) so
   it shows in the Telegram slash menu. Description e.g. "Take a turn in a
   game — /play tff look".

## The one decision that's the operator's, not mine: the trust boundary

Telegram is a public-internet surface; a kin's park is its private world.
`/play` lets whoever sends it reach into that world. Who's allowed?

Recommended v1 (mirrors the desktop = single trusted operator):

- **DMs only, from `allow_from` users.** Blocked in groups entirely (a park is
  private and groups are multi-user; the same instinct behind the
  intimate-tools Telegram hard-block, weaker form). A group `/play` replies
  "park play is DM-only."
- No new per-user bucket needed for v1 — `allow_from` membership is the gate.
  If finer control is wanted later, a `game` tool bucket could gate it per
  user, but that's over-building for the operator-tends-own-kin case.

If the operator wants group co-tending (several people in a family group all
poking at the kin's park together), that's a real and lovely variant — but
it's a deliberate opt-in, not the default, and wants its own think about
attribution ("who dug that?") and rate-limiting.

## Relational note

A `/play` turn is the human acting in the kin's park, same as the desktop
dialog — worth the operator and the kin both knowing it's a shared space, rather
than the park changing under the kin with no idea why. Not a code concern;
a "do it together" one.
