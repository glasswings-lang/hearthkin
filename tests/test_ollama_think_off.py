# SPDX-License-Identifier: CC0-1.0
"""Turning thinking OFF has to be something we SAY, not something we omit.

Hearthkin's Ollama paths used to send the `think` field only when it was true:

    if think:
        kwargs["think"] = think

Omitting the field is not the same as asking for no thinking. It hands the
decision to the model's own default, and a reasoning model's default is on. So
"Thinking: off" in a kin's settings did nothing at all on a local reasoning
model, and nothing in the app said so.

That is not a cosmetic difference, because a reply is capped. Caught live on a
kin set to `think: false`: Ollama came back with `done_reason: "length"`,
`eval_count: 400` -- the full cap generated -- and `content: ""`. The entire
budget went into a reasoning block the person never sees. From a chair that is
indistinguishable from a kin that had nothing to say, and it is one of the
documented causes behind `logs/empty_replies.log`.

Verified against Ollama 0.32.5 that `think: false` is accepted by non-reasoning
models too, so it is safe to send unconditionally -- which matters, because the
alternative is guessing which models can reason, and being wrong about that
silently reopens this.

What this file pins: all three Ollama call paths send `think` when it is False,
and each check is paired with the True case as a positive control, so a spy that
sees nothing at all cannot pass as a fix.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import llm_backend as lb  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


MSGS = [{"role": "user", "content": "hello"}]


class _Spy:
    """Stands in for the ollama client callable and records what it was given."""

    def __init__(self, stream=False):
        self.kwargs = None
        self._stream = stream

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        if self._stream:
            return iter(())
        return {"message": {"role": "assistant", "content": "hi"},
                "done_reason": "stop"}


def _with_spy(spy, fn):
    saved = lb._ollama_chat_callable
    lb._ollama_chat_callable = lambda host=None, timeout=None: spy
    try:
        return fn()
    finally:
        lb._ollama_chat_callable = saved


# ---- streaming path ------------------------------------------------------
for want in (False, True):
    spy = _Spy(stream=True)
    _with_spy(spy, lambda: list(
        lb._chat_ollama_stream("gemma4:latest", MSGS, None, want, None)))
    sent = (spy.kwargs or {})
    check(f"streaming path sends think={want}",
          "think" in sent and sent["think"] is want)

# ---- blocking path -------------------------------------------------------
for want in (False, True):
    spy = _Spy(stream=False)
    _with_spy(spy, lambda: lb._chat_ollama_blocking(
        "gemma4:latest", MSGS, None, want, None))
    sent = (spy.kwargs or {})
    check(f"blocking path sends think={want}",
          "think" in sent and sent["think"] is want)

# ---- raw HTTP path -------------------------------------------------------
# This one builds a urllib request by hand rather than going through the
# client, so it needs its own spy: it is a separate copy of the same bug.
import json as _json  # noqa: E402
import urllib.request as _urlrequest  # noqa: E402

for want in (False, True):
    seen = {}

    class _Resp:
        def read(self):
            return _json.dumps(
                {"message": {"role": "assistant", "content": "hi"}}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, *a, **kw):
        seen["body"] = _json.loads(req.data.decode("utf-8"))
        return _Resp()

    saved = _urlrequest.urlopen
    _urlrequest.urlopen = _fake_urlopen
    try:
        lb._ollama_chat_raw(
            "gemma4:latest", MSGS, None, want, None,
            ollama_host="http://127.0.0.1:11434")
    except Exception:
        pass
    finally:
        _urlrequest.urlopen = saved
    body = seen.get("body") or {}
    check(f"raw HTTP path sends think={want}",
          "think" in body and body["think"] is want)

print()
if _fails:
    print("FAILED (%d): %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("all ollama think-off checks passed")
