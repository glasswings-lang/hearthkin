# SPDX-License-Identifier: CC0-1.0
"""Guard test: the claude.ai conversations.json importer.

Companion services close, change, or become unreachable, and the people
using them lose years of conversation with something that mattered. That
is what the importers are for — kindroid and openclaw are already here
for exactly that reason. This is the same job for Anthropic's own export.

Everything below uses synthetic fixtures. The real export this was built
against is one person's private history and no part of it belongs in a
test file.

What this pins:
  1. detection survives a LONG per-conversation `summary`. The first
     draft looked at the first 4,000 characters and demanded `"sender"`,
     and missed a real 74 MB export outright because one summary pushed
     the first `"sender"` to character 4,577. A sniffer that fails on
     ordinary data is worse than no sniffer, because it fails by handing
     the file to some other parser rather than by saying so;
  2. it does NOT claim a Skype export or an OpenClaw stream;
  3. role comes from `sender`, which the file states — never from
     name-matching `kin_display_name`, which is right for a two-party
     text log and wrong here;
  4. **both message shapes are read.** Older messages carry a top-level
     `text`; newer ones carry a `content` block list. On the real export
     3,813 messages had an empty `content` while 13,742 had a non-empty
     `text` — reading only one shape drops thousands of messages and
     looks exactly like nothing went wrong;
  5. machinery (`thinking`, `tool_use`, `tool_result`) is excluded, and
     a message that was ONLY an attachment still leaves a trace instead
     of vanishing;
  6. threads are separated by a header and the whole lot comes back in
     timestamp order;
  7. the contract: `(messages, source_label, fmt)`.

Run: python tests/test_import_claude_json.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault(
    "HEARTHKIN_HOME", tempfile.mkdtemp(prefix="hearthkin-claudeimport-"))

from importers import claude_json  # noqa: E402
from importers import openclaw, skype_json  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def _write(obj):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    return path


def main():
    # A long summary, exactly the shape that defeated the first sniffer.
    long_summary = "This conversation covers a great many things. " * 140
    export = [
        {
            "uuid": "conv-1",
            "name": "First thread",
            "summary": long_summary,
            "created_at": "2026-01-01T10:00:00.000000Z",
            "chat_messages": [
                {"uuid": "m1", "sender": "human",
                 "created_at": "2026-01-01T10:00:00.000000Z",
                 "content": [{"type": "text", "text": "hello there"}],
                 "text": "", "attachments": [], "files": []},
                {"uuid": "m2", "sender": "assistant",
                 "created_at": "2026-01-01T10:00:05.000000Z",
                 "content": [
                     {"type": "thinking", "thinking": "hmm"},
                     {"type": "text", "text": "hello yourself"},
                     {"type": "tool_use", "name": "x"},
                     {"type": "tool_result", "content": "y"},
                 ],
                 "text": "", "attachments": [], "files": []},
            ],
        },
        {
            "uuid": "conv-2",
            "name": "Older thread",
            "summary": "",
            "created_at": "2025-06-01T09:00:00.000000Z",
            "chat_messages": [
                # The OLD shape: empty content, real top-level text.
                {"uuid": "m3", "sender": "human",
                 "created_at": "2025-06-01T09:00:00.000000Z",
                 "content": [], "text": "an older message",
                 "attachments": [], "files": []},
                # Only an attachment — must not vanish silently.
                {"uuid": "m4", "sender": "human",
                 "created_at": "2025-06-01T09:00:01.000000Z",
                 "content": [], "text": "",
                 "attachments": [{"file_name": "notes.txt"}], "files": []},
                # Genuinely empty — nothing anywhere. Correctly dropped.
                {"uuid": "m5", "sender": "assistant",
                 "created_at": "2025-06-01T09:00:02.000000Z",
                 "content": [], "text": "", "attachments": [], "files": []},
            ],
        },
    ]
    path = _write(export)
    raw = open(path, encoding="utf-8").read()

    # 1 + 2. Detection.
    check("detects a Claude export despite a long summary", claude_json.detect(raw))
    check("...and by path", claude_json.detect_path(path))
    check("does not claim a Skype export",
          not skype_json.detect('{"userId": "8:x", "conversations": []}'))
    check("openclaw does not claim this file", not openclaw.detect(raw))
    check("positive control: the sniffer CAN say no",
          not claude_json.detect('{"userId": "8:x", "conversations": []}'))

    msgs, label, fmt = claude_json.parse(path, "Claude")
    check("returns the (messages, source_label, fmt) contract",
          label == "claude_ai" and fmt == "claude_json")

    bodies = [m["content"] for m in msgs]
    roles = [m["role"] for m in msgs]

    # 3. Role from the file.
    said = [(m["role"], m["content"]) for m in msgs if m["role"] != "system"]
    check("the human's turn is role=user",
          ("user", "hello there") in said)
    check("Claude's turn is role=assistant",
          ("assistant", "hello yourself") in said)
    check("role is unaffected by kin_display_name",
          claude_json.parse(path, "Somebody Else")[0] == msgs)

    # 4. Both shapes.
    check("the OLD shape (top-level text) is read",
          any("an older message" == b for b in bodies))

    # 5. Machinery out, attachments kept.
    joined = "\n".join(bodies)
    check("thinking blocks are excluded", "hmm" not in joined)
    check("tool_use / tool_result are excluded",
          "tool_use" not in joined and '"y"' not in joined)
    check("an attachment-only message leaves a trace",
          any("attachment" in b for b in bodies))
    check("a wholly empty message is dropped", len(said) == 4)

    # 6. Thread headers and time order.
    check("each thread gets a header", roles.count("system") == 2)
    check("the header names the thread",
          any("Older thread" in m["content"]
              for m in msgs if m["role"] == "system"))
    check("everything comes back in timestamp order",
          all(msgs[i]["ts"] <= msgs[i + 1]["ts"] for i in range(len(msgs) - 1)))
    check("the older thread sorts first",
          bodies[0].find("Older thread") >= 0)

    # 7. Listing + single-thread selection.
    listing = claude_json.list_conversations(path)
    check("both threads are listed", len(listing) == 2)
    check("listing is newest first", listing[0]["id"] == "conv-1")
    one, _l, _f = claude_json.parse(path, "Claude", conversation_id="conv-2")
    check("a single thread can be picked",
          all("hello" not in m["content"] for m in one))

    try:
        claude_json.parse(path, "Claude", conversation_id="nope")
        check("an unknown conversation id is refused, not silently empty", False)
    except ValueError:
        check("an unknown conversation id is refused, not silently empty", True)

    os.unlink(path)
    print()
    if _fails:
        print("FAILED: %d check(s)" % len(_fails))
        for f in _fails:
            print("  -", f)
        return 1
    print("OK - Claude exports import with roles taken from the file, both "
          "message shapes read, machinery excluded and threads kept apart.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
