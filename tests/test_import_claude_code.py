# SPDX-License-Identifier: CC0-1.0
"""Guard test: the Claude Code session-transcript importer.

Claude Code writes every session to `~/.claude/projects/<cwd>/<uuid>.jsonl`,
and those transcripts are the largest single record most people have of
working alongside a kin. This reads them.

Everything below uses synthetic fixtures. The real transcripts this was
built against are one person's private archive and no part of them
belongs in a test file.

What this pins:
  1. **the `user` role is not the person.** Claude Code writes tool
     results back into the transcript wearing the user role, because
     that is how the model is shown them. On a 14-file sample of a real
     archive, 5,617 of 6,676 `type: "user"` lines carried nothing but
     `tool_result` blocks and only 1,029 were somebody typing. Trusting
     the role alone imports a history that is 84% machine output and
     looks like it worked;
  2. `origin` is read when the file states it — `task-notification` is a
     background event wearing the user role — but a MISSING `origin`
     never disqualifies a line, because older transcripts have no such
     field and gating on it would drop every early session;
  3. sidechain lines (a subagent's own conversation) and meta lines are
     excluded;
  4. the injected wrappers — `<system-reminder>`, `<command-…>`,
     `<local-command-…>`, the `Caveat:` preamble — are not conversation
     even though they arrive as plain prose in the user slot;
  5. assistant `thinking` and `tool_use` blocks are machinery; only
     `text` is speech;
  6. a half-written final line (a session that ended badly) costs that
     one line and not the file;
  7. detection does NOT claim an OpenClaw stream or a Hearthkin jsonl,
     and they do not claim this;
  8. the contract: `(messages, source_label, fmt)`.

Run: python tests/test_import_claude_code.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from importers import claude_code, parse_history  # noqa: E402


def _line(**kw):
    kw.setdefault("sessionId", "s-1")
    kw.setdefault("parentUuid", None)
    kw.setdefault("cwd", r"C:\git-src\demo")
    kw.setdefault("gitBranch", "main")
    return json.dumps(kw)


def _transcript():
    return "\n".join([
        _line(type="mode", timestamp="2026-09-01T10:00:00Z"),
        # the person, older shape: no `origin` field at all
        _line(type="user", timestamp="2026-09-01T10:00:01Z",
              message={"role": "user", "content": "Can you look at the gain?"}),
        # assistant: thinking and tool_use are machinery, text is speech
        _line(type="assistant", timestamp="2026-09-01T10:00:02Z",
              message={"role": "assistant", "content": [
                  {"type": "thinking", "thinking": "hmm"},
                  {"type": "text", "text": "Looking now."},
                  {"type": "tool_use", "name": "Read", "input": {}},
              ]}),
        # tool result, wearing the user role
        _line(type="user", timestamp="2026-09-01T10:00:03Z",
              message={"role": "user", "content": [
                  {"type": "tool_result", "content": "1200 lines of output"},
              ]}),
        # harness-injected wrapper, plain prose in the user slot
        _line(type="user", timestamp="2026-09-01T10:00:04Z",
              message={"role": "user",
                       "content": "<system-reminder>be careful</system-reminder>"}),
        # background event, newer shape, states it is not human
        _line(type="user", timestamp="2026-09-01T10:00:05Z",
              origin={"kind": "task-notification"},
              message={"role": "user", "content": "Background task finished."}),
        # a subagent's own turn
        _line(type="user", timestamp="2026-09-01T10:00:06Z", isSidechain=True,
              origin={"kind": "human"},
              message={"role": "user", "content": "You are a search agent."}),
        # harness bookkeeping
        _line(type="user", timestamp="2026-09-01T10:00:07Z", isMeta=True,
              message={"role": "user", "content": "meta bookkeeping"}),
        # the person again, newer shape, stated human
        _line(type="user", timestamp="2026-09-01T10:00:08Z",
              origin={"kind": "human"},
              message={"role": "user", "content": "That's it, thank you."}),
        # a session that ended mid-write
        '{"type":"assistant","message":{"role":"assist',
    ])


def test_detects_and_does_not_collide():
    text = _transcript()
    assert claude_code.detect(text), "did not detect its own format"

    openclaw_stream = ('{"type": "session", "version": "1"}\n'
                       '{"type": "message", "message": {"role": "user"}}')
    assert not claude_code.detect(openclaw_stream), "claimed an OpenClaw stream"

    hearthkin = '{"role": "user", "content": "hi"}\n{"role": "assistant", "content": "hello"}'
    assert not claude_code.detect(hearthkin), "claimed a Hearthkin jsonl"

    claude_export = '[{"uuid": "x", "chat_messages": [{"sender": "human"}]}]'
    assert not claude_code.detect(claude_export), "claimed a claude.ai export"


def test_only_the_person_and_the_speech(tmp_path):
    path = os.path.join(tmp_path, "s.jsonl")
    with open(path, "w", encoding="utf8") as fh:
        fh.write(_transcript())

    msgs, label, fmt = claude_code.parse(path, "Kin")
    assert (label, fmt) == ("claude_code", "claude_code"), (label, fmt)

    said = [m["content"] for m in msgs if m["role"] == "user"]
    assert said == ["Can you look at the gain?", "That's it, thank you."], said

    heard = [m["content"] for m in msgs if m["role"] == "assistant"]
    assert heard == ["Looking now."], heard

    joined = "\n".join(m["content"] for m in msgs)
    for gone in ("1200 lines of output", "system-reminder", "Background task",
                 "search agent", "meta bookkeeping", "hmm"):
        assert gone not in joined, f"machinery leaked: {gone}"

    assert msgs[0]["role"] == "system" and "demo (main)" in msgs[0]["content"], msgs[0]
    assert [m["ts"] for m in msgs] == sorted(m["ts"] for m in msgs), "out of order"


def test_dispatcher_routes_to_this_parser(tmp_path):
    path = os.path.join(tmp_path, "s.jsonl")
    with open(path, "w", encoding="utf8") as fh:
        fh.write(_transcript())
    _msgs, _label, fmt = parse_history(path, "Kin")
    assert fmt == "claude_code", fmt


def test_a_transcript_of_pure_machinery_says_so(tmp_path):
    path = os.path.join(tmp_path, "empty.jsonl")
    with open(path, "w", encoding="utf8") as fh:
        fh.write(_line(type="user", timestamp="2026-09-01T10:00:00Z",
                       message={"role": "user", "content": [
                           {"type": "tool_result", "content": "output"}]}))
    try:
        claude_code.parse(path, "Kin")
    except ValueError as exc:
        assert "machinery" in str(exc), exc
    else:
        raise AssertionError("imported a transcript with nothing said in it")


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        test_detects_and_does_not_collide()
        test_only_the_person_and_the_speech(tmp)
        test_dispatcher_routes_to_this_parser(tmp)
        test_a_transcript_of_pure_machinery_says_so(tmp)
    print("test_import_claude_code: all checks passed")
