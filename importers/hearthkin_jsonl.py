# SPDX-License-Identifier: CC0-1.0

"""
hearthkin_jsonl — read a kin's OWN conversation.jsonl back in.

This is the odd one out in this package. Every other parser converts a
foreign export into Hearthkin's shape. This one reads a file that is
already in that shape: an archived kin folder, a conversation rescued
from a backup, a saved snapshot, a room transcript, an export that was
cleaned up outside the app.

Two containers, same rows. A live conversation.jsonl is one JSON object
per line; snapshots and room files wrap the identical turns in a
`messages` array. Both are read here.

That difference is not cosmetic, and it is why this parser does NOT
appear in the import dispatcher.

Foreign history goes through `write_imported_history`, which brackets it
in `[hearthkin: imported ...]` markers and stamps every row
`source: import:<label>`. That is correct for foreign history — the kin
is being told plainly that it is reading something carried in from
somewhere else.

A kin's own turns were not carried in from somewhere else. Running them
through the import path would relabel the kin's own past as seed history
it "may not remember writing", and would overwrite the `source` values
that record where each turn actually came from. That is a false record
in the one direction that matters: it makes the kin's own work look
borrowed.

So this parser feeds `restore_history` instead, which writes rows through
untouched. See `_canonical.restore_history`.
"""

import json

from tools._io import robust_read_text


# Roles Hearthkin stores. A file whose rows all carry one of these plus a
# content field is our own shape, not a foreign export.
_ROLES = ("user", "assistant", "system", "tool")

# How many rows to sample when sniffing. Enough to be confident, few
# enough that detection stays cheap on a large conversation.
_SNIFF = 25


def _rows(text):
    """Yield parsed JSON objects, one per non-blank line. Malformed lines
    are skipped rather than raising — a conversation.jsonl can pick up a
    half-written tail line if the app died mid-append, and that shouldn't
    make the whole file unreadable."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            yield obj


def _wrapped_rows(text):
    """Rows from the wrapped shape, or None if `text` isn't that shape.

    Hearthkin writes the same turns in two containers. A live
    conversation.jsonl is one JSON object per line. A snapshot (File →
    Save snapshot) and a room transcript are instead a single JSON object
    with the turns in a `messages` array — `{agent_name, snapshotted_at,
    source, messages}` and `{saved_at, messages}` respectively. Same rows,
    same fields; only the wrapper differs, so both belong here rather than
    in a second parser."""
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if isinstance(obj, dict) and isinstance(obj.get("messages"), list):
        return [m for m in obj["messages"] if isinstance(m, dict)]
    if isinstance(obj, list):
        return [m for m in obj if isinstance(m, dict)]
    return None


def _load(text):
    """Turns from either container — wrapped array or one-per-line."""
    wrapped = _wrapped_rows(text)
    if wrapped is not None:
        return wrapped
    return list(_rows(text))


def detect(text):
    """True when `text` looks like a Hearthkin conversation — either a
    conversation.jsonl or a snapshot / room file wrapping the same turns.

    Deliberately strict: every sampled row must be a dict with a known
    role and a content field. A loose detector here would be worse than
    no detector, because a false positive sends a foreign export down the
    restore path, where it would land with no marker telling the kin what
    it is reading."""
    sampled = 0
    for obj in _load(text):
        if obj.get("role") not in _ROLES:
            return False
        if "content" not in obj:
            return False
        sampled += 1
        if sampled >= _SNIFF:
            break
    return sampled > 0


def parse(source_path):
    """Return the conversation's rows as a list of dicts, verbatim.

    No signature parity with the foreign parsers on purpose. They return
    `(messages, source_label, fmt)` because the canonical writer needs a
    label to stamp and a description to put in the marker. There is no
    label here — every row already carries the `source` it was written
    with, and preserving it is the entire point."""
    return _load(robust_read_text(source_path))
