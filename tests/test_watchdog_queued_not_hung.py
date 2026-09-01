# SPDX-License-Identifier: CC0-1.0
"""Guard test: a turn queued behind Hearthkin's own work is not a hang.

Ollama answers one request at a time. So when this app has a distillation, a
scheduled wake-up or a heartbeat out on the same daemon, a reply the person just
sent produces nothing at all until that finishes. A distillation bite routinely
runs thirteen minutes. The shortest streaming watchdog window here is five.

Observed on a real machine: a turn sent at 04:47:51 was declared hung at
04:52:51 -- to the second -- while the model was working steadily on something
the app itself had asked for. The reply was lost and the transcript said
"[no response -- possible hang]".

The machinery cost is the smaller half. What the person reported was that they
had started hesitating before sending at all: not knowing whether the model was
free turned every message into "should I even send this, will it get read". A
watchdog that fires on healthy work teaches exactly that, and an app has no
business imposing it.

So two things are pinned here:

  - the watchdog HOLDS OFF and re-arms while our own background work has the
    model, instead of declaring a hang;
  - it still fires normally when nothing of ours is running, because a
    watchdog that never fires is not a watchdog;
  - and the Activity line SAYS what is being waited on, both while a turn is
    in flight and while idle -- the idle case being the one that answers
    "is it safe to send right now" before anything is typed.

Run: python tests/test_watchdog_queued_not_hung.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="wdog-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


from frame.status_voice_mixin import StatusVoiceMixin  # noqa: E402


class _Frame(StatusVoiceMixin):
    """Bare host. Only the state the two methods under test read."""

    def __init__(self, **kw):
        self._distilling = {}
        self._cron_workers = set()
        self._heartbeat_workers = set()
        self._stream_chunks_seen = 0
        self._stream_started_at = None
        self._stream_watchdog_minutes = 5
        self.__dict__.update(kw)


# --- naming what has the model ------------------------------------------

f = _Frame()
check("nothing running reports nothing", f._own_background_on_the_model() == "")

f = _Frame(_distilling={"Bracken": object()})
check("a distillation is named, with the kin",
      f._own_background_on_the_model() == "Bracken is saving notes to its memory")

f = _Frame(_cron_workers={("Vesper", "07:00")})
check("a scheduled wake-up is named",
      "Vesper" in f._own_background_on_the_model()
      and "wake-up" in f._own_background_on_the_model())

f = _Frame(_heartbeat_workers={"Opal"})
check("a heartbeat is named",
      "Opal" in f._own_background_on_the_model())

# The foreground is deliberately NOT reported to the DESKTOP — the person is
# looking at it. That reasoning does not survive the trip to a phone, so the
# same probe answers a wider question when asked to.
f = _Frame(_streaming=True, current_agent="Tarn")
check("this turn's own streaming is not reported to the desktop",
      f._own_background_on_the_model() == "")
check("...but IS reported when the caller can't see the main window",
      "Tarn is part-way through a reply"
      in f._own_background_on_the_model(include_foreground=True))

f = _Frame(_room_active=True, current_room="park planning", current_agent=None)
check("a room round is invisible to the desktop line",
      f._own_background_on_the_model() == "")
check("...and named for a caller that can't see it",
      "park planning" in f._own_background_on_the_model(include_foreground=True))


class _Bot:
    def __init__(self, label):
        self._label = label

    def active_turn_label(self):
        return self._label


f = _Frame(bots={"Bracken": _Bot("Bracken is replying to Sam on Telegram")})
check("a kin mid-reply to someone else holds the model too",
      f._own_background_on_the_model(include_foreground=True)
      == "Bracken is replying to Sam on Telegram")
check("...and skip_bot drops the one turn the asker already knows about",
      f._own_background_on_the_model(include_foreground=True,
                                     skip_bot="Bracken") == "")
check("...while another kin's turn still counts",
      "Vesper" in _Frame(
          bots={"Bracken": _Bot("Bracken is replying to Sam"),
                "Vesper": _Bot("Vesper is replying to Ada")}
      )._own_background_on_the_model(include_foreground=True,
                                     skip_bot="Bracken"))

# The long invisible work outranks a live conversation when both are true.
f = _Frame(_distilling={"Bracken": object()},
           bots={"Vesper": _Bot("Vesper is replying to Ada")})
check("a distillation is named ahead of a conversation, being the longer wait",
      "saving notes" in f._own_background_on_the_model(include_foreground=True))

# A bot that raises must not take the status line with it.
class _AngryBot:
    def active_turn_label(self):
        raise RuntimeError("bot went away")


f = _Frame(bots={"Bracken": _AngryBot(), "Vesper": _Bot("Vesper is replying to Ada")})
check("a bot that raises is stepped over, not fatal",
      f._own_background_on_the_model(include_foreground=True)
      == "Vesper is replying to Ada")

# Must never raise into a status repaint, whatever state it finds. This runs
# on every Activity-line refresh, so an exception here would break the one
# surface a screen-reader user reads to know what the app is doing.
f = _Frame(_distilling=None, _cron_workers=None, _heartbeat_workers=12345)
try:
    _out = f._own_background_on_the_model()
    _raised = False
except Exception:
    _out, _raised = None, True
check("unusable state does not raise into a status repaint", not _raised)
check("...and reports nothing rather than something half-formed", _out == "")


# --- what the person is told, mid-turn -----------------------------------

f = _Frame(_distilling={"Bracken": object()})
line = f._compose_in_flight_status()
check("a queued turn says what it is waiting on",
      "Bracken is saving notes to its memory" in line)
check("...and says the message is not lost, which is the actual worry",
      "queued" in line and "nothing is lost" in line.lower())
check("...instead of the bare generic waiting line",
      "Cold starts and big prefills" not in line)

f = _Frame()
check("with nothing of ours running, the ordinary wait line is unchanged",
      "Cold starts and big prefills" in f._compose_in_flight_status())

f = _Frame(_distilling={"Bracken": object()}, _stream_chunks_seen=3)
check("once the reply is actually arriving, it says so and stops explaining",
      f._compose_in_flight_status().startswith("Receiving reply"))


# --- and BEFORE anything is sent -----------------------------------------
#
# The point of this one: it answers "is it safe to send" without the person
# having to send something to find out.

class _IdleFrame(_Frame):
    current_room = None
    current_agent = "Tarn"
    agent_cfg = {"model": "gemma4:31b", "num_ctx": 8192}
    _soul_cache = ""
    _memory_cache = ""

    def _conversation_token_estimate(self, model):
        return 100


f = _IdleFrame(_distilling={"Bracken": object()})
idle = f._compose_default_status()
check("the idle line warns before a message is even typed",
      "busy: Bracken is saving notes to its memory" in idle)
check("...and says plainly what sending now would mean",
      "queue behind it" in idle)

f = _IdleFrame()
check("a quiet app says nothing extra - silence when idle is the feature",
      "busy:" not in f._compose_default_status())


# --- the watchdog itself -------------------------------------------------
#
# Read from source rather than driving wx timers: the property is "our own
# background work suppresses the hang verdict and re-arms", and a test that
# stubbed CallLater would pass on a version that re-armed but forgot to skip
# the force-clear below it.

import inspect  # noqa: E402
from frame.chat_stream_mixin import ChatStreamMixin  # noqa: E402

src = inspect.getsource(ChatStreamMixin._on_stream_watchdog_fire)
_hold = src.index("_own_background_on_the_model")
_real = src.index("# Real hang")
check("the watchdog asks whether we are the ones holding the model",
      "_own_background_on_the_model" in src)
check("...before it declares a hang, not after",
      _hold < _real)
check("...and re-arms rather than giving up on the turn",
      "wx.CallLater" in src[_hold:_real])
check("...returning without force-clearing the stream",
      "return" in src[_hold:_real])
check("the re-arm reuses the window the turn was given, so the room path "
      "doesn't silently get a different budget",
      "_stream_watchdog_minutes" in src[_hold:_real])
check("a genuine hang is still reported",
      "streaming_hangs.log" in inspect.getsource(ChatStreamMixin))


print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_watchdog_queued_not_hung: all checks passed")
