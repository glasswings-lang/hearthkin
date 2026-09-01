"""Remote tool-approval outcomes must not lie about who refused.

Origin (2026-07-20): a kin on Telegram reported to its operator that they had
denied a command. They hadn't — they were in another window and never saw the
request at all. Every non-approval path in the exec gate returned the single
string "[denied by user]", so a timeout, an undeliverable prompt and an
eviction were all indistinguishable from an actual refusal, and the kin
faithfully relayed the only thing it was told.

Worse, the approval path logged nothing anywhere, and approval replies are
consumed on the poll thread so they never reach conversation.jsonl either —
so the incident left no record to diagnose. These checks pin the fix.
"""
import os
import re
import sys
import threading
import time
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import telegram_bot as tb  # noqa: E402

_failures = []


def check(label, ok):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        _failures.append(label)


def _bot(timeout_secs=600, send=None):
    """A TelegramBot with only the fields the approval path touches."""
    b = tb.TelegramBot.__new__(tb.TelegramBot)
    b.agent_name = "TestKin"
    b._pending_lock = threading.Lock()
    b._pending_approvals = {}
    b.on_approval_needed = None
    b.get_config = lambda: {"approval_timeout_secs": timeout_secs}
    b._send_chunked = send if send is not None else (lambda c, m: None)
    return b


# ─── The strings themselves ────────────────────────────────────────────────────
check("every outcome has its own result string",
      len(set(tb._DENY_RESULTS.values())) == len(tb._DENY_RESULTS))

check("all four outcomes are present",
      set(tb._DENY_RESULTS) == {"deny", "timeout", "undelivered", "superseded"})

# The load-bearing bit: a model reading a not-run result will narrate it as a
# refusal unless told otherwise. Only a real deny may imply the operator said no.
for _k in ("timeout", "undelivered", "superseded"):
    check(f"'{_k}' tells the kin nobody refused",
          "obody refused" in tb._DENY_RESULTS[_k])

check("only a real deny claims the operator said no",
      "said no" in tb._DENY_RESULTS["deny"]
      and not any("said no" in tb._DENY_RESULTS[k]
                  for k in ("timeout", "undelivered", "superseded")))


# ─── Undeliverable prompt: fail fast, don't block then cry denial ──────────────
def _boom(*a, **k):
    raise RuntimeError("simulated send failure")


_b = _bot(timeout_secs=600, send=_boom)
_t0 = time.time()
_d = _b._request_exec_approval_telegram(111, 222, "ls -la", "")
_elapsed = time.time() - _t0

check("undeliverable prompt returns 'undelivered'", _d == "undelivered")
# Previously this swallowed the exception and blocked for the full timeout
# before reporting a denial. 600s of silence, then a lie.
check("undeliverable prompt fails fast instead of waiting out the timeout",
      _elapsed < 2)
check("undeliverable prompt leaves no pending entry behind",
      _b._pending_approvals == {})


# ─── Timeout is its own outcome, and says so to BOTH sides ────────────────────
_sent = []
_b2 = _bot(timeout_secs=600, send=lambda c, m: _sent.append(m))
with mock.patch.object(tb.threading.Event, "wait",
                       lambda self, timeout=None: False):
    _d2 = _b2._request_exec_approval_telegram(111, 222, "rm x", "")

check("timeout returns 'timeout', not 'deny'", _d2 == "timeout")
check("timeout tells the human nothing was refused",
      any("refused" in m for m in _sent))
check("timeout does not use the word auto-denied to the human",
      not any("denied" in m.lower() for m in _sent))


# ─── Wait is rendered in words, never '0 min' ─────────────────────────────────
# The 30s floor integer-divided to "0 min", which reads as a bug in the one
# message whose entire job is to be believed.
for _secs, _label in ((30, "30 seconds"), (60, "1 minute"), (600, "10 minutes")):
    _s = []
    _b3 = _bot(timeout_secs=_secs, send=lambda c, m: _s.append(m))
    with mock.patch.object(tb.threading.Event, "wait",
                           lambda self, timeout=None: False):
        _b3._request_exec_approval_telegram(1, 2, "ls", "")
    check(f"wait of {_secs}s reads as '{_label}' in both messages",
          all(_label in m for m in (_s[0], _s[-1])))
    # Word-boundary, not substring — "10 minutes" legitimately contains
    # "0 min", which is what a naive check trips over.
    check(f"wait of {_secs}s never renders as a bare zero",
          not any(re.search(r"\b0\s+(min|second)", m) for m in _s))


# ─── Eviction is 'superseded', not a fabricated refusal ───────────────────────
_b4 = _bot(timeout_secs=600)
_first = tb._PendingApproval(event=threading.Event(), command="first",
                             args_summary="", chat_id=222)
_b4._pending_approvals[111] = _first
with mock.patch.object(tb.threading.Event, "wait",
                       lambda self, timeout=None: False):
    _b4._request_exec_approval_telegram(111, 222, "second", "")

check("an evicted approval is released so its worker unblocks",
      _first.event.is_set())
check("an evicted approval is 'superseded', not 'deny'",
      _first.decision == "superseded")


# ─── Desktop / Discord gate: same class, smaller blast radius ─────────────────
# The desktop wrapper returned "[denied by user]" when it could not ASK at all
# (shutdown in progress, or the dialog failed to build). Same lie as the
# Telegram one, just rarer.
#
# Fixing it surfaced a real fail-open: both wrappers refused only on an exact
# DENY match and let every other value fall through to raw_exec. Harmless while
# "deny" was the only alternative; a silent gate bypass the instant a new
# outcome existed. These pin BOTH properties.
import frame.cron_exec_mixin as cem  # noqa: E402
from dialogs.exec_approval import ExecApprovalDialog as _D  # noqa: E402


class _FrameClosing(cem.CronExecMixin):
    _closing = True
    _pending_approvals = []


_f = _FrameClosing()

check("shutdown yields 'unavailable', not 'deny'",
      _f._request_exec_approval("K", "ls", "r") == "unavailable")

check("only a real desktop deny is reported as a refusal",
      cem._EXEC_REFUSALS["deny"] == "[denied by user]"
      and "obody refused" in cem._EXEC_REFUSALS["unavailable"])

_WRAPPERS = (("desktop", "_wrap_exec_executor", ("K",)),
             ("remote", "_wrap_exec_for_remote", ("K", "discord:1")))
# Guessing a method name and skipping when it's absent is how a test quietly
# covers nothing. Assert both exist before exercising them.
for _n, _w, _a in _WRAPPERS:
    check(f"{_n} wrapper {_w} exists to be tested", hasattr(_f, _w))

for _label, _wrap, _args in _WRAPPERS:
    _ran = []
    _ex = {"exec": lambda a: _ran.append(a) or "RAN"}
    _w = getattr(_f, _wrap)(_ex, *_args)
    _res = _w["exec"]({"command": "echo hi"})
    check(f"{_label} gate fails CLOSED when approval is unavailable",
          not _ran)
    check(f"{_label} gate tells the kin nobody refused",
          "obody refused" in _res)

# Webcam now distinguishes "couldn't ask" from "refused" too — the operator
# asked for this to be surfaced to the kin, since the Telegram webcam wrap
# used to tell the user the operator had declined a capture nobody ever saw.
check("webcam approval yields 'unavailable' on shutdown, not 'deny'",
      _f._request_webcam_approval("K", "someone", 1) == "unavailable")


# ─── Approval alert: audible, gated, and defaults ON ──────────────────────────
# NVDA speech gets cut off by the operator's own typing and a toast is easy
# to miss, so a kin could sit blocked until timeout with no signal that reached
# the operator. The audible cue is the fix; these pin that it fires by default
# and only goes quiet when explicitly turned off or muted.
import frame.bot_integration_mixin as bim  # noqa: E402


class _AlertProbe(bim.BotIntegrationMixin):
    def __init__(self, cfg):
        self.config = cfg


_calls = []
with mock.patch.object(bim, "play_alert", lambda **k: _calls.append(k)):
    # default config: no key set -> alert should fire (default ON)
    _calls.clear()
    _AlertProbe({})._play_approval_alert()
    check("approval alert fires when unconfigured (defaults on)", len(_calls) == 1)

    # explicitly off -> silent
    _calls.clear()
    _AlertProbe({"approval_alert": False})._play_approval_alert()
    check("approval alert stays silent when turned off", _calls == [])

    # volume 0 -> silent even when enabled
    _calls.clear()
    _AlertProbe({"approval_alert": True, "chime_volume": 0})._play_approval_alert()
    check("approval alert respects a muted volume", _calls == [])

    # independent of reply_chime: chimes off, alert still fires
    _calls.clear()
    _AlertProbe({"reply_chime": False})._play_approval_alert()
    check("approval alert is independent of the reply chime", len(_calls) == 1)

# The default config carries the key, so an existing install gets the cue.
import kin_persistence as _kp  # noqa: E402
check("approval_alert defaults ON in the shipped config",
      _kp.DEFAULT_CONFIG.get("approval_alert") is True)


# ─── Summary ──────────────────────────────────────────────────────────────────
print()
if _failures:
    print(f"{len(_failures)} FAILURE(S): " + ", ".join(_failures))
    sys.exit(1)
print("All approval-outcome checks passed.")
