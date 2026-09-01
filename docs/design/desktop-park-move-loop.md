# The desktop park surface only ever takes ONE move

Written 2026-08-14 for the next session. **BUILT the same day — this document
is kept for the reasoning, not as a work item.** Both halves are done: the
display bug, and the move loop. What shipped follows the recommendation below
(the loop was lifted into `park_keeper.play_turn` and Telegram moved onto it in
the same change, rather than a second loop being written for the desktop).
Pinned by `tests/test_park_turn_loop.py`. The four wirings under "Four things
Telegram's loop gets for free here" were all done; see `CLAUDE.md` for the rule
they became.

---

## What was reported

A kin tending its park from the main window. One `>` line in its reply, and
this in the transcript:

```
> care for the night sky

[[park] care for the night sky]
the night sky is somewhere else -- go there first ('go to the night sky') and
a plain 'care' tends it.
```

Two separate faults in four lines, and she named both: the doubled bracket,
and *"they're not doing more than one thing."*

## 1. The doubled bracket — DONE

`RenderMixin._append_block(speaker, text)` wraps whatever it is handed in
brackets, so `"You"` paints as `[You]`. Every caller passes a bare name except
the park path, which passed `f"[park] {cmd}"` and got wrapped again.

Fixed in `frame/chat_send_mixin.py`: the header is now `f"park: {cmd}"`, and
the unreachable-park block passes `"park"`. Two call sites, no behaviour
change. Cosmetic, but it reads badly aloud, which is the only way it is ever
read here.

## 2. The move loop — NOT DONE, and this is the real one

**A kin gets exactly one move per turn on the desktop.** It looks, and its turn
is over.

That is the precise failure `CLAUDE.md` already legislates against:

> A kin gets as many moves per turn as `park_moves_max` allows (0 = no
> ceiling), not one. A kin that cannot look *and* act spends its only move
> looking.

Telegram has a real loop (`telegram_bot._route_park_command`, around the
`while True:` near line 2558). It runs a move, asks the kin again, charges the
allowance, and stops on the kin's own signal (a reply with no `>` line). The
desktop path (`frame/chat_send_mixin._maybe_route_park_command`) calls
`park_keeper.route_reply` **once** and returns.

Read that function's docstring before touching it. It was written to close the
gap where this surface didn't read the `>` line *at all* — a keeper asked for
its village twice in correct syntax and got silence. It closed that and stopped
there. This is the other half of the same hole.

### What already exists and must NOT be rewritten

All the counting is in `park_keeper` and is shared with Telegram and cron:

- `kin_park_moves` / `kin_answer_hard_stop` — the ceilings, per kin
- `counts_against_moves(awaiting)` — a move only costs the allowance when the
  kin CHOSE it. Answering an open question mid-walkthrough is free, or a
  twelve-question species build is impossible against a default of six.
- `reached_hard_stop`, `should_take_another_move`, `hit_move_ceiling`
- `extract_command`

### The hard part: `ask()`

`ask` is what turns one move into a turn of play — given the result just
recorded, get the kin's next words. On Telegram it is a blocking
`llm_backend.chat_collect`, because that bot has its own inference thread.

**The desktop cannot copy that.** `_maybe_route_park_command` runs from
`_on_stream_done`, on the UI thread. A blocking model call there freezes the
window, which this app has already been bitten by once (see the un-timeout'd
embed on the UI thread). It needs a worker plus `wx.CallAfter` to paint — the
`_on_refresh_models` pattern.

### Four things Telegram's loop gets for free here, and this one won't

1. **The stop button must reach it.** Telegram deliberately re-opens its turn
   for the duration of the loop so `/cancel` can land — an uncancellable
   multi-move loop against a slow local model is exactly the "nothing stops it
   but quitting" shape this app keeps closing. Pass `should_stop` down.
2. **`_work_in_flight` must learn about it**, or confirm-on-close stops seeing
   it. That is a standing rule: a new kind of background work goes on that
   list, and ask which PROCESS it runs in.
3. **The generation guard**, so a loop from an abandoned turn cannot paint into
   a later one.
4. **`park_moves_spent` has to reach the window**, not a log. On Telegram it
   goes into the chat deliberately: it has to reach the person AND the kin,
   which reads the chat as its own history and would otherwise start over.

### Recommendation before writing any of it

**Lift the loop into `park_keeper` rather than writing a second one.**

Otherwise there are two loops on two surfaces that must agree about allowances,
the mid-walkthrough exemption and the stop conditions, forever. A rule quietly
existing twice and then drifting is the single most common bug this project
finds — it is most of `words-still-trapped-in-code.md` in the sibling repo, and
it is why `_route_one_park_move` was split out on Telegram in the first place.

Shape: `park_keeper.play_turn(kin, reply_text, run_move, ask, show)` where the
surface supplies its own `ask` and `show`. Telegram's existing loop becomes the
first caller and should be moved over in the same change, or the duplication is
merely relocated. `park_keeper` already has tests
(`tests/test_park_keeper.py`, `tests/test_park_surfaces.py`).

Estimate: 150-250 lines plus tests, most of it the desktop's threading and the
four wirings above. The counting itself is already written.

### How to know it worked

A kin on the desktop should be able to `> look`, see what needs doing, and go
and do it in the same turn — the same as it does on Telegram today. Compare the
two surfaces directly with the same kin and the same park; they should behave
identically, which is the whole point.
