# SPDX-License-Identifier: CC0-1.0
"""The DESKTOP park turn, driven end to end with a fake park and a fake model.

The structural test next door (test_park_turn_loop.py) asks whether this
surface calls the shared loop and hands it an `ask`. It cannot tell whether
the wiring around that call actually works — whether the result of move one
reaches the model before it is asked for move two, whether Stop is honoured,
whether anything is painted, whether the kin's history ends up in a shape it
can read next turn. Those are what broke the desktop in the first place, so
they are checked here by running the real method.

No wx window is built and no model is called: `wx.CallAfter` is made to run
inline, the worker thread is made to run inline, and the park and the model
are stand-ins that record what they were asked.

Run: python tests/test_park_desktop_turn.py
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="hk_dtpark_"))
os.environ.setdefault("HEARTHKIN_SILENT", "1")

import frame.chat_send_mixin as CSM
import park_keeper as PK

_fails = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _fails.append(label)


# ── stand-ins ────────────────────────────────────────────────────────────────

class FakeWx(object):
    """CallAfter runs inline, so the test is deterministic and ordering bugs
    show up as ordering bugs rather than as flakiness."""

    @staticmethod
    def CallAfter(fn, *a, **kw):
        fn(*a, **kw)


class InlineThread(object):
    """threading.Thread that runs on start(), so the turn is over by the time
    _start_park_turn returns."""

    def __init__(self, target=None, daemon=None, **kw):
        self._target = target

    def start(self):
        self._target()


class FakeThreading(object):
    Thread = InlineThread


class FakeHost(object):
    """A park that answers every move and records what it was asked."""

    def __init__(self, awaiting=False):
        self.ran = []
        self._awaiting = awaiting
        self.decorated = 0

    def run(self, kin, cmd, say=""):
        self.ran.append(cmd)
        return "the park did: %s" % cmd

    def decorate(self, kin, cmd, res):
        self.decorated += 1
        return res + " [+co-op news]"

    def awaiting_answer(self, kin):
        return self._awaiting

    def reachable(self, kin):
        return True, ""


class FakeResult(object):
    def __init__(self, content, stopped=False):
        self.content = content
        self.stopped = stopped


class FakeFrame(CSM.ChatSendMixin):
    """The smallest object the park turn actually touches."""

    def __init__(self):
        self.current_agent = "Keeper"
        self.agent_cfg = {}
        self.conversation = []
        self.painted = []
        self.status = []
        self._closing = False
        self._stream_id = 7
        self._park_workers = set()
        self._park_continuation = None
        self.stop_btn = self
        self._enabled = None

    # widgets, reduced to what is asked of them
    def Enable(self):
        self._enabled = True

    def Disable(self):
        self._enabled = False

    def _append_block(self, speaker, text, when=None):
        self.painted.append((speaker, text))

    def _set_status(self, s):
        self.status.append(s)

    def _persist_current_conversation(self):
        pass

    def _log(self, s):
        pass

    def _other_speakers_in_history(self):
        return []


def build(replies, awaiting=False, stopped_after=None, gen_bump_after=None):
    """A frame wired to a fake park and a scripted model. `replies` is what the
    kin says each time it is asked again."""
    f = FakeFrame()
    host = FakeHost(awaiting=awaiting)
    f._park_continuation = {
        "gen": f._stream_id,
        "model": "fake-model",
        "messages": [{"role": "system", "content": "soul"},
                     {"role": "user", "content": "go tend the park"}],
        "options": {},
        "cache": False,
        "cache_ttl": "auto",
        "openrouter_provider": None,
        "max_ctx_tokens": 8000,
    }
    seen = {"n": 0, "msgs": []}

    def fake_chat_collect(model, messages, **kw):
        seen["n"] += 1
        # Snapshot what the model was shown, so the test can assert the
        # previous move's RESULT was in front of it.
        seen["msgs"].append(list(messages))
        if gen_bump_after is not None and seen["n"] == gen_bump_after:
            f._stream_id += 1          # someone sent a new message / pressed Stop
        if stopped_after is not None and seen["n"] >= stopped_after:
            return FakeResult("", stopped=True)
        nxt = replies[seen["n"] - 1] if seen["n"] <= len(replies) else ""
        return FakeResult(nxt)

    return f, host, seen, fake_chat_collect


def run_turn(f, host, fake_chat_collect, first_reply):
    old_wx, old_thr, old_be = CSM.wx, CSM.threading, CSM.llm_backend
    CSM.wx = FakeWx()
    CSM.threading = FakeThreading()

    class _BE(object):
        chat_collect = staticmethod(fake_chat_collect)
    CSM.llm_backend = _BE()
    try:
        f._start_park_turn("Keeper", host, first_reply, "2026-08-14T21:00:00")
    finally:
        CSM.wx, CSM.threading, CSM.llm_backend = old_wx, old_thr, old_be


# ── the whole point: look AND act, in one turn ───────────────────────────────

print("\n-- a kin looks, then acts, without another message from anyone --")

f, host, seen, cc = build(["Two need tending.\n> care for Glade",
                           "And a gift.\n> give acorn to Otter",
                           "They're settled now."])
run_turn(f, host, cc, "Let me see.\n> look")

check(host.ran == ["look", "care for Glade", "give acorn to Otter"],
      "three real moves ran from one desktop reply")
check(seen["n"] == 3, "the kin was asked again after each move")
check(f._park_workers == set(), "the worker de-registers when the turn ends")

print("\n-- the kin is shown what its own move did, before being asked again --")

# The desktop appends the park note on the UI thread. If `ask` read that back
# off self.conversation instead of holding its own list, the second request
# could go out without the result of the move it is meant to react to.
_second = seen["msgs"][1]
check(any(m.get("role") == "system" and "the park did: look" in str(m.get("content"))
          for m in _second),
      "the result of move one is in front of the model for move two")
check(any("co-op news" in str(m.get("content")) for m in _second),
      "...and it is the DECORATED copy, never bare run() output")
check(host.decorated == 3, "every move a reader sees went through decorate()")

print("\n-- what the person sees, and what the kin can read next turn --")

check([p[0] for p in f.painted] == [
        "park: look", "Keeper", "park: care for Glade",
        "Keeper", "park: give acorn to Otter", "Keeper"],
      "moves and the kin's words alternate in the window, in order")
_roles = [m["role"] for m in f.conversation]
check(_roles == ["system", "assistant", "system", "assistant",
                 "system", "assistant"],
      "the kin's history alternates result / voice, so it reads as one turn")
check(all(m.get("ts") for m in f.conversation),
      "every saved turn carries a timestamp")

print("\n-- the ceiling reaches the person AND the kin --")

f, host, seen, cc = build(["> a", "> b", "> c", "> d", "> e", "> f", "> g"])
run_turn(f, host, cc, "> look")
check(len(host.ran) == PK.DEFAULT_PARK_MOVES_MAX,
      "the default per-kin ceiling stops the turn")
_last = f.conversation[-1]
_spent_expected = CSM.load_app_prompt("park_moves_spent", "Keeper").replace(
    "{moves}", str(PK.DEFAULT_PARK_MOVES_MAX))
check(_last["role"] == "system" and _last["content"] == _spent_expected,
      "a spent allowance is written into the kin's own history")
check(str(PK.DEFAULT_PARK_MOVES_MAX) in _last["content"],
      "...naming how many moves were taken, not a bare 'that's enough'")
check(f.painted[-1] == ("park", _spent_expected),
      "...and shown to the person in exactly the same words")

print("\n-- a kin that simply finishes is not narrated --")

f, host, seen, cc = build(["that's everyone, they're all content."])
run_turn(f, host, cc, "> care for Glade")
check(host.ran == ["care for Glade"], "one move, then the kin stopped itself")
check(len(f.painted) == 2 and f.painted[0][0] == "park: care for Glade",
      "the move and the kin's closing words are painted")
check(not any("park" == p[0] for p in f.painted),
      "no allowance line under an ordinary ending")

print("\n-- stopping --")

# Bumping _stream_id is what both Stop and a new message do.
f, host, seen, cc = build(["> a", "> b", "> c"], gen_bump_after=1)
run_turn(f, host, cc, "> look")
check(len(host.ran) == 1,
      "a new generation ends the turn instead of playing on")
check(f._park_workers == set(),
      "...and the worker still de-registers, so quitting stops warning")
check(not any(p[0].startswith("park:") and "> a" in p[1] for p in f.painted),
      "nothing from the abandoned turn is painted into the later one")

# should_stop firing inside the model call.
f, host, seen, cc = build(["> a", "> b"], stopped_after=1)
run_turn(f, host, cc, "> look")
check(host.ran == ["look"], "a stopped model call ends the turn after the move")

print("\n-- no continuation context: one move, honestly --")

f, host, seen, cc = build(["> a", "> b"])
f._park_continuation = {"gen": 999, "model": "fake-model", "messages": []}
run_turn(f, host, cc, "> look")
check(host.ran == ["look"], "a stale continuation degrades to exactly one move")
check(seen["n"] == 0, "...and the model is never asked, rather than guessed at")

f, host, seen, cc = build(["> a"])
f._park_continuation = None
run_turn(f, host, cc, "> look")
check(host.ran == ["look"], "no continuation at all is the same one move")

print("\n-- a park that falls over doesn't take the window with it --")


class Exploding(FakeHost):
    def run(self, kin, cmd, say=""):
        raise RuntimeError("park server fell over")


f, host, seen, cc = build(["> a"])
_boom = Exploding()
run_turn(f, _boom, cc, "> look")
check(f._park_workers == set(),
      "a failing park still ends the turn and de-registers")

if _fails:
    print(f"\nFAILED: {len(_fails)} check(s): {_fails}")
    sys.exit(1)
print("\nALL CHECKS PASSED -- the desktop plays a whole park turn.")
