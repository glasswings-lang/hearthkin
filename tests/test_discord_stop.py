# SPDX-License-Identifier: CC0-1.0
"""Guard test: a Discord reply can be stopped, and quitting says it's there.

Two absences that were really one: nothing on this surface knew a reply was
being written. So there was no way to end one, and no way for the app to warn
that quitting would abandon it.

Stopping. A reply against a slow local model runs for minutes, and until now
the only way out of one was to quit Hearthkin. That is the "nothing stops it
but quitting" shape this app keeps closing everywhere else. Discord has no
slash-command surface here, so the stop is a plain word typed on its own —
and it has to be an EXACT match on the whole message, or "stop that, it's
funny" becomes a control instruction instead of a remark to the kin.

Where it is checked matters as much as that it exists. It runs before the
per-channel lock (which is held by the very reply being stopped) and before
the per-user cooldown (someone typing "stop" twice is exactly the person who
needs the second one to land). Unlike Telegram, no poll-thread trick is
needed: the Gateway loop keeps running because inference is handed to an
executor.

Quitting. `_work_in_flight` composes the lines the close prompt reads out. It
walked the Telegram bots and not the Discord ones, so closing through a
Discord reply was silent — the exact failure the whole check exists to stop.

And the rule that ties them together: A STOPPED TURN IS NOT AN EMPTY REPLY.
Someone asked it to stop; the kin didn't fall silent. So no salvage, no
`[no reply produced]`, and nothing in empty_replies.log — that file diagnoses
faults and would otherwise fill up with our own interruptions.

Run: python tests/test_discord_stop.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="dcstop-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import threading                                             # noqa: E402
from discord_bot import DiscordBot, _STOP_WORDS              # noqa: E402


def _bot():
    b = DiscordBot.__new__(DiscordBot)
    b.agent_name = "Bracken"
    b._active_turns = {}
    b._turn_lock = threading.Lock()
    return b


# ── the stop itself ────────────────────────────────────────────────────

print("--- a reply in flight can be ended ---")

b = _bot()
check("with nothing running, a stop says so rather than pretending",
      b._request_turn_stop(11) is False)

b._begin_turn(11, "Wren")
check("a reply in flight is not cancelled until someone says so",
      b._turn_cancelled(11) is False)
check("asking it to stop reports that there was something to stop",
      b._request_turn_stop(11) is True)
check("...and the model call now sees the stop", b._turn_cancelled(11) is True)

# Scoped to the CHANNEL. One room's stop reaching into another's would be a
# stranger ending somebody else's conversation.
b = _bot()
b._begin_turn(11, "Wren")
b._begin_turn(22, "Ash")
b._request_turn_stop(11)
check("a stop reaches only the channel it was typed in",
      b._turn_cancelled(11) is True and b._turn_cancelled(22) is False)

# A stop must not outlive its turn, or the NEXT reply in that channel dies
# instantly for a reason nobody can see.
b = _bot()
b._begin_turn(11, "Wren")
b._request_turn_stop(11)
b._end_turn(11)
b._begin_turn(11, "Wren")
check("a stale stop does not kill the next reply in that channel",
      b._turn_cancelled(11) is False)

# Shutdown.
b = _bot()
b._begin_turn(11, "Wren")
b._begin_turn(22, "Ash")
b.stop_all_turns()
check("shutting down asks every reply to wind down, not just one",
      b._turn_cancelled(11) and b._turn_cancelled(22))


print("--- what counts as a stop ---")

for word in ("stop", "/stop", "cancel", "/cancel"):
    check(f"'{word}' on its own is a stop", word in _STOP_WORDS)
check("case doesn't matter -- the handler lowercases before matching",
      "STOP".lower() in _STOP_WORDS)
for phrase in ("stop that, it's funny", "don't stop", "cancel the meeting",
               "stopping by later"):
    check(f"'{phrase}' is a remark to the kin, not a control instruction",
          phrase.strip().lower() not in _STOP_WORDS)

import inspect                                               # noqa: E402
on_msg = inspect.getsource(DiscordBot._on_message)
stop_at = on_msg.find("_STOP_WORDS")
lock_at = on_msg.find("_channel_locks")
cool_at = on_msg.find("_user_last_reply")
check("the stop is checked BEFORE the channel lock it would wait forever on",
      0 < stop_at < lock_at)
check("...and before the cooldown, which would swallow a repeated stop",
      0 < stop_at < cool_at)


# ── quitting warns about it ────────────────────────────────────────────

print("--- quitting says what it would abandon ---")

from frame.lifecycle_mixin import LifecycleMixin              # noqa: E402


class _Frame(LifecycleMixin):
    """Only the probes _work_in_flight reads. Everything absent is the
    'nothing running' case, which must stay silent."""

    def __init__(self, discord_bots=None):
        self._streaming = False
        self._room_active = False
        self._distilling = {}
        self._cron_workers = set()
        self._heartbeat_workers = set()
        self._park_workers = set()
        self.bots = {}
        self.discord_bots = discord_bots or {}
        self._pending_approvals = []
        self.current_agent = "Bracken"
        self.current_room = None


f = _Frame()
check("an idle app still says nothing at all -- silence when idle is the point",
      f._work_in_flight() == [])

b = _bot()
b._begin_turn(11, "Wren")
f = _Frame({"Bracken": b})
lines = f._work_in_flight()
check("a Discord reply in flight is reported at all -- it never used to be",
      len(lines) == 1)
check("...naming the kin, the person, and the surface",
      "Bracken" in lines[0] and "Wren" in lines[0] and "Discord" in lines[0])

b.stop_all_turns()
b._end_turn(11)
check("once the reply is done, quitting is quiet again",
      f._work_in_flight() == [])

# Several at once should read as one sentence, not as a wall.
b = _bot()
b._begin_turn(11, "Wren")
b._begin_turn(22, "Ash")
f = _Frame({"Bracken": b})
lines = f._work_in_flight()
check("two replies at once are summarised, not listed one per line",
      len(lines) == 1 and "2 replies" in lines[0])


# A bot that raises must not be able to block the quit. A missing warning is
# a bad bug; an app that will not close is a worse one.
class _Angry:
    def active_turn_label(self):
        raise RuntimeError("gateway is gone")


f = _Frame({"Bracken": _Angry()})
try:
    got = f._work_in_flight()
    ok = True
except Exception:
    ok, got = False, None
check("a broken Discord bot cannot stop you quitting", ok)
check("...and contributes nothing rather than a half-formed line", got == [])

# The two registries must be probed INDEPENDENTLY. Sharing one guard means a
# fault reading either costs the report for both — so a Telegram problem
# would silently hide a Discord reply, and vice versa. This is not
# hypothetical: the first version of this change shared a guard, and the
# existing confirm-close test caught it.
b = _bot()
b._begin_turn(11, "Wren")
f = _Frame({"Bracken": b})


class _MissingRegistry(dict):
    def values(self):
        raise RuntimeError("telegram registry is unusable")


f.bots = _MissingRegistry()
lines = f._work_in_flight()
check("an unusable Telegram registry does not hide a Discord reply",
      len(lines) == 1 and "Discord" in lines[0])

# The Telegram stand-in here is a DiscordBot, which is fair: _work_in_flight
# asks both registries for exactly the same thing, active_turn_label().
f = _Frame({"Bracken": _Angry()})
tg = _bot()
tg.agent_name = "Ash"
tg._begin_turn(11, "Wren")
f.bots = {"Ash": tg}
lines = f._work_in_flight()
check("...and a broken Discord bot does not hide a Telegram reply",
      len(lines) == 1 and "Ash" in lines[0])


print()
if _fails:
    print(f"test_discord_stop: {len(_fails)} FAILED")
    for f_ in _fails:
        print("  - " + f_)
    sys.exit(1)
print("test_discord_stop: all checks passed")
