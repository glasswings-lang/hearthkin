"""Standalone test for _TelegramStreamEditor (telegram_bot).

Verifies the throttled in-place-edit logic that will render a streaming kin
reply into one Telegram message — WITHOUT a live Telegram — using a fake clock
and recording send/edit stubs. The throttle is the flood-ban-risky part, so it's
pinned here before the editor is wired into the handlers.

Run: python tests/test_telegram_stream_editor.py   (or via tests/run_all.py)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram_bot import _TelegramStreamEditor, _is_not_modified  # noqa: E402

_failures = []


def check(cond, label):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _failures.append(label)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make():
    clock = Clock()
    sends = []   # list of texts sent (each returns a fresh id)
    edits = []   # list of (message_id, text)

    def send(text):
        sends.append(text)
        return f"m{len(sends)}"

    def edit(mid, text):
        edits.append((mid, text))

    ed = _TelegramStreamEditor(send, edit, throttle_secs=1.0, max_len=20,
                               now=clock)
    return ed, clock, sends, edits


# 1. First content creates the message; no edit yet.
ed, clock, sends, edits = make()
ed.feed("Hello")
check(sends == ["Hello"], "first content sends the placeholder message")
check(edits == [], "no edit on the very first content delta")

# 2. Rapid feeds within the throttle window do NOT edit.
ed.feed(", ")
ed.feed("there")
check(edits == [], "feeds within throttle window are NOT flushed (rate-limit safe)")

# 3. Advancing past the throttle then feeding flushes exactly one edit.
clock.t = 1.5
ed.feed("!")
check(len(edits) == 1 and edits[0][0] == "m1", "one edit after throttle elapses")
check(edits[0][1] == "Hello, there!", "edit carries the accumulated buffer")

# 4. reset_turn BANKS the finished turn. It used to drop it, and this check
#    used to assert that dropping — which is how a rule violation stayed green.
#
#    What the old behaviour did: a kin said "let me check", that went out as a
#    real message a screen reader read aloud, the tool ran, and the reply that
#    came back OVERWROTE the sentence. One message, first half destroyed, with
#    nothing to say it had changed. Telegram output is append-only exactly
#    because the chat is a historical record and the earlier text has already
#    been read to someone. See CLAUDE.md.
ed, clock, sends, edits = make()
ed.feed("let me check")          # a talking turn before a tool call
ed.reset_turn()                  # that turn ends; the tool runs
clock.t = 2.0
ed.feed("the answer is 42")      # the turn after the tool
check(sends == ["let me check"], "placeholder created on the first turn")
# Asserted as a PREFIX, not by looking for both strings: this fixture caps
# the message at 20 characters, so the second turn is legitimately cut off.
# The invariant is not "both are visible" — it is that the message never
# stops starting with what it already said.
check(edits and edits[-1][1].startswith("let me check"),
      "the sentence said before the tool call still opens the message")
check(edits and len(edits[-1][1]) > len("let me check"),
      "...and the message grew rather than being replaced")

# 5. finalize writes the cleaned reply into the streamed message, returns True.
ed, clock, sends, edits = make()
ed.feed("draft")
handled = ed.finalize("final clean")
check(handled is True, "finalize returns True when it handled the send")
check(edits[-1] == ("m1", "final clean"), "finalize edits the streamed message with cleaned text")

# 6. finalize with NO prior stream sends fresh, returns True.
ed, clock, sends, edits = make()
handled = ed.finalize("just this")
check(handled is True and sends == ["just this"], "finalize sends fresh when nothing streamed")

# 7. finalize empty with no stream returns False (caller sends nothing).
ed, clock, sends, edits = make()
handled = ed.finalize("")
check(handled is False and sends == [], "finalize of empty with no stream returns False")

# 8. Overflow past max_len: head edited in, remainder sent as follow-ons.
ed, clock, sends, edits = make()
ed.feed("x")
ed.finalize("A" * 45)   # max_len=20 → head 20, then 20, then 5
check(edits[-1][1] == "A" * 20, "finalize edits head up to max_len")
check(sends[1:] == ["A" * 20, "A" * 5], "finalize sends the overflow remainder in chunks")

# 9. The duplicate-reply bug: a no-op edit is not a failure.
#
# Telegram answers an edit that changes nothing with 400 "message is not
# modified". finalize() used to report that as a failed edit, and the caller's
# fallback then re-sent the COMPLETE reply — so the reader got the streamed
# message plus a full second copy of the same words. It fired whenever the
# last interim flush had already written the final text: every reply that
# ended on a throttle tick, and EVERY reply past max_len, where the truncated
# head stops changing long before the reply ends.
def make_strict():
    """Like make(), but the fake Telegram refuses a no-op edit the way the
    real one does, and records every call so a wasted edit is visible."""
    clock = Clock()
    sends = []
    edits = []
    state = {"text": None}

    def send(text):
        sends.append(text)
        state["text"] = text
        return f"m{len(sends)}"

    def edit(mid, text):
        edits.append((mid, text))
        if text == state["text"]:
            raise RuntimeError(
                "HTTP 400: Bad Request: message is not modified")
        state["text"] = text

    ed = _TelegramStreamEditor(send, edit, throttle_secs=1.0, max_len=20,
                               now=clock)
    return ed, clock, sends, edits


check(_is_not_modified(RuntimeError(
          "HTTP 400: Bad Request: message is not modified: the text...")),
      "the not-modified 400 is recognised")
check(not _is_not_modified(RuntimeError("HTTP 429: Too Many Requests")),
      "a rate-limit is NOT mistaken for not-modified")
check(not _is_not_modified(RuntimeError("HTTP 400: message to edit not found")),
      "a deleted message is NOT mistaken for not-modified")

# Reply ends exactly on a throttle tick, so the interim flush already wrote
# the final words.
ed, clock, sends, edits = make_strict()
ed.feed("all done")
clock.t = 5.0
ed.feed("")            # no-op
handled = ed.finalize("all done")
check(handled is True,
      "finalize reports success when the message already says the final text")
check(len(sends) == 1,
      "...so the caller does not re-send, and there is no duplicate reply")

# Past max_len: the head stopped changing many ticks ago.
ed, clock, sends, edits = make_strict()
ed.feed("B" * 30)      # buffer 30, message holds the first 20
clock.t = 5.0
ed.feed("C" * 10)      # buffer 40, head STILL the same 20 B's
n_edits_before = len(edits)
check(n_edits_before == 0,
      "an interim flush that would change nothing makes no API call at all")
handled = ed.finalize("B" * 30 + "C" * 10)
check(handled is True, "finalize past max_len succeeds instead of duplicating")
check(sends[1:] == ["B" * 10 + "C" * 10],
      "...and only the part not already shown is sent as the remainder")

# A REAL edit failure must still fall back to the caller's robust re-send.
ed, clock, sends, edits = make()


def _explode(mid, text):
    raise RuntimeError("HTTP 429: Too Many Requests: retry after 12")


ed._edit = _explode
ed.feed("hello")
check(ed.finalize("hello there") is False,
      "a genuine edit failure still returns False so the caller re-sends")

if _failures:
    print(f"\n{len(_failures)} FAILURE(S)")
    sys.exit(1)
print("\nAll Telegram stream-editor checks passed.")
