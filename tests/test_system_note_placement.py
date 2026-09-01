# SPDX-License-Identifier: CC0-1.0
"""Guard test: a note about the conversation must not rewrite the front of it.

A local model reuses its cached work only for an unbroken run from the very
start of the prompt, so what a change costs is set by how EARLY it lands, not
how big it is. Appending is free; touching message 0 re-reads everything.

Hearthkin writes `[hearthkin: ...]` notes into a kin's stored history as
`role=system` — park receipts, tool-history compaction markers, authoring-bridge
receipts, salvage notes. One or more per turn on a kin that plays its park. The
send path then folded every system message into one leading block, so each new
note landed at position 0 and the whole context was read again from cold.
Measured on a real kin: the system block grew by roughly 345 characters on six
consecutive turns while nothing on disk had changed. At ~78 tok/s that is about
five minutes of silence before a 22,000-token conversation says a word.

`_inline_mid_conversation_system_notes` leaves those notes where they happened,
re-roled to `user`, so they invalidate only from their own position — far back
and stable. The leading system run is untouched, which also protects the
rolling-window marker (deliberately `role=system`: as `user`, models answered it).

What this pins is the PROPERTY — replay a growing park-keeper conversation and
assert the front of the prompt holds — not the shape of any one helper. It also
runs the OLD pipeline as a positive control, because a stability test that would
pass on the broken code proves nothing at all.

Run: python tests/test_system_note_placement.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="sysnote-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import llm_backend as lb  # noqa: E402


# --- the shape of the send path, as chat() applies it ---------------------

def send_shape(messages):
    """What an Ollama call actually gets, for the normalizations at issue."""
    out = lb._inline_mid_conversation_system_notes(messages)
    out = lb._collapse_consecutive_user_turns(out)
    return lb._consolidate_system_messages(out)


def old_send_shape(messages):
    """The pipeline before the fix — the positive control."""
    out = lb._consolidate_system_messages(messages)
    return lb._collapse_consecutive_user_turns(out)


def fingerprint(messages):
    """Per-message parts in the same form _prefix_reuse compares."""
    import hashlib
    import json
    parts = []
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            continue
        text = m.get("content")
        if not isinstance(text, str):
            text = json.dumps(text, sort_keys=True, default=str)
        if m.get("tool_calls"):
            text += json.dumps(m["tool_calls"], sort_keys=True, default=str)
        h = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:8]
        parts.append(f"{i}:{m.get('role', '?')}:{len(text)}:{h}")
    return parts


def park_conversation(turns):
    """A keeper's history: every turn leaves a park receipt behind it, which is
    the accumulating `role=system` note that caused the bug."""
    convo = [{"role": "system", "content": "You are Tarn. " + ("soul " * 400)}]
    for i in range(turns):
        convo.append({"role": "user", "content": f"how is the village, turn {i}?"})
        convo.append({"role": "assistant", "content": f"reply {i} " + ("word " * 60)})
        convo.append({"role": "system",
                      "content": f"[hearthkin: park — you did `look` and it ran "
                                 f"for real:\nturn {i} " + ("detail " * 40) + "]"})
    return convo


# --- the property: the front of the prompt holds -------------------------

def replay(shape):
    """Normalize a growing conversation turn by turn; report where the first
    change landed each time, as a fraction of the messages sent."""
    firsts = []
    prev = None
    # Realistic lengths on purpose. On a 3-turn conversation a 4th turn is a
    # third of the whole thing, so even a perfect append reuses only ~75% and
    # a percentage threshold would measure the toy, not the fix.
    for turns in range(20, 33):
        parts = fingerprint(shape(park_conversation(turns)))
        reuse, first = lb._prefix_reuse(prev, parts)
        if reuse is not None:
            firsts.append((first, len(parts), reuse))
        prev = parts
    return firsts


_new = replay(send_shape)
_old = replay(old_send_shape)

check("every turn keeps message 0 (the system prompt) byte-identical",
      all(first > 0 for first, _n, _r in _new))
check("...and reuses nearly all of the previous prompt",
      all(reuse >= 90 for _f, _n, reuse in _new))
check("...with the first change in the last few messages, not the middle",
      all(first >= n - 4 for first, n, _r in _new))
print(f"       (new pipeline: first change at msg "
      f"{[f for f, _n, _r in _new][:4]}... of {_new[0][1]}+, "
      f"reuse {min(r for _f, _n, r in _new)}%+)")

# Positive control. If this passes, the checks above are measuring nothing.
check("the OLD pipeline really did rewrite message 0 every turn "
      "(control - proves the test can fail)",
      all(first == 0 for first, _n, _r in _old))
print(f"       (old pipeline: reuse {max(r for _f, _n, r in _old)}% at best)")


# --- the rules the transform has to keep ---------------------------------

_msgs = [
    {"role": "system", "content": "You are Tarn."},
    {"role": "system", "content": "[hearthkin: earlier messages truncated]"},
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "hello"},
    {"role": "system", "content": "[hearthkin: park — you did `look`]"},
    {"role": "user", "content": "and now?"},
]
_out = lb._inline_mid_conversation_system_notes(_msgs)
check("the leading system run stays system",
      [m["role"] for m in _out[:2]] == ["system", "system"])
check("the rolling-window marker (spliced right after it) is protected",
      _out[1]["content"] == "[hearthkin: earlier messages truncated]"
      and _out[1]["role"] == "system")
check("a mid-conversation note becomes a user turn",
      _out[4]["role"] == "user"
      and _out[4]["content"] == "[hearthkin: park — you did `look`]")
check("nothing is reordered",
      [m["content"] for m in _out] == [m["content"] for m in _msgs])
check("the note does not mutate the caller's message",
      _msgs[4]["role"] == "system")

check("a conversation with no mid-conversation note is returned unchanged "
      "(by reference)",
      lb._inline_mid_conversation_system_notes(_msgs[:1] + _msgs[2:4])
      is not None
      and lb._inline_mid_conversation_system_notes(
          [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
      == [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
_ref = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
check("...and really by reference, so the common case costs nothing",
      lb._inline_mid_conversation_system_notes(_ref) is _ref)
check("an empty list is fine", lb._inline_mid_conversation_system_notes([]) == [])

# A note between an assistant's tool_calls and its results would break the
# pairing and 400 the provider. It stays put.
_tool = [
    {"role": "system", "content": "You are Tarn."},
    {"role": "assistant", "content": "",
     "tool_calls": [{"id": "c1", "type": "function",
                     "function": {"name": "read_file", "arguments": "{}"}}]},
    {"role": "system", "content": "[hearthkin: note]"},
    {"role": "tool", "tool_call_id": "c1", "content": "contents"},
]
check("a note immediately before a tool result is left as system",
      lb._inline_mid_conversation_system_notes(_tool)[2]["role"] == "system")

check("a blank note is dropped rather than sent as an empty user turn",
      [m["role"] for m in lb._inline_mid_conversation_system_notes(
          [{"role": "system", "content": "s"},
           {"role": "user", "content": "u"},
           {"role": "system", "content": "   "}])] == ["system", "user"])


# --- truncation must not be able to put a note back ----------------------
#
# This is the second bug, found live after the first fix shipped.
# `_truncate_messages` hoists the leading contiguous system run to protect the
# system prompt, then drops the oldest of what's left. When what's left BEGINS
# with a `[hearthkin: ...]` note, that note becomes contiguous with the system
# block — and is then indistinguishable from the system prompt to everything
# downstream. The fold merges it into message 0 and the cache dies again.
#
# Measured on a real kin: the system block alternated between 14,002 and 14,301
# characters turn to turn, a park receipt joining and leaving the leading run as
# the trim moved. reuse=0% first-change=msg 0, with the fix above in place and
# working exactly as designed.
#
# The order of operations IS the fix, so this drives the real truncation.

NOTE = "[hearthkin: park — you did `look at the owl roost` and it ran for real]"


def with_a_note_at_the_cut(pad_turns):
    """A conversation sprinkled with our own notes, mid-history — never
    adjacent to the system prompt to begin with. Trimming at different points
    is what pushes one of them up against it, which is the whole scenario."""
    convo = [{"role": "system", "content": "You are Tarn. " + ("soul " * 300)}]
    for i in range(pad_turns):
        convo.append({"role": "user", "content": f"question {i} " + ("x" * 400)})
        convo.append({"role": "assistant", "content": f"reply {i} " + ("y" * 400)})
        if i % 3 == 0:
            convo.append({"role": "system", "content": NOTE})
    return convo


def through_the_send_path(messages, cap):
    """chat()'s order for the passes at issue: inline, truncate, inline, fold."""
    out = lb._inline_mid_conversation_system_notes(messages)
    out, _ = lb._truncate_messages(out, cap)
    out = lb._inline_mid_conversation_system_notes(out)
    return lb._consolidate_system_messages(out)


def old_order(messages, cap):
    """What shipped first: truncate, then inline. The positive control."""
    out, _ = lb._truncate_messages(messages, cap)
    out = lb._inline_mid_conversation_system_notes(out)
    return lb._consolidate_system_messages(out)


_leaked_new, _leaked_old, _sizes = [], [], set()
for cap in range(900, 3000, 50):
    convo = with_a_note_at_the_cut(30)
    new0 = through_the_send_path(convo, cap)[0].get("content") or ""
    old0 = old_order(convo, cap)[0].get("content") or ""
    _sizes.add(len(new0))
    if NOTE in new0:
        _leaked_new.append(cap)
    if NOTE in old0:
        _leaked_old.append(cap)

check("truncation can never push one of our notes into the system prompt",
      not _leaked_new)
check("...so the system block is byte-identical at every trim point, "
      "which is what the cache actually needs",
      len(_sizes) == 1)
check("the OLD order really did leak it (control — proves the test can fail)",
      bool(_leaked_old))
print(f"       (old order leaked the note at {len(_leaked_old)} of "
      f"{len(range(900, 3000, 50))} trim points; new order at 0)")


# --- the wiring: the transform has to actually be in the send path -------

_src = (ROOT / "llm_backend.py").read_text(encoding="utf-8")
_call = "messages = _inline_mid_conversation_system_notes(messages)"
_gate = "if not _is_openrouter_model(model):"
check("chat() calls the transform", _call in _src)
check("...BEFORE truncation, which is the ordering that IS the fix — "
      "truncation can otherwise shove a note into the system run",
      _src.index(_call) < _src.index("messages, _ = _truncate_messages("))
check("...for every provider, not only Ollama "
      "(OpenRouter concatenates system messages server-side too)",
      _call in _src and _gate in _src
      and _src.index(_call) < _src.index(_gate))


print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_system_note_placement: all checks passed")
