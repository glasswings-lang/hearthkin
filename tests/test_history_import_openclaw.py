# SPDX-License-Identifier: CC0-1.0
"""Guard test: the OpenClaw session-store importer.

OpenClaw keeps a kin's whole life as a folder of per-session JSONL event
streams, with `.reset` / `.deleted` archived copies that repeat messages.
The importer must union across every file, dedupe by message id (so the
archived copies collapse), drop the machinery (toolResult events,
tool-output-as-user, control replies), strip OpenClaw's injected
untrusted-metadata preamble off user turns while keeping the real sender,
and order everything by timestamp.

This builds a tiny synthetic session folder exercising each of those and
asserts the reconstruction — so a regression in any of them fails loud.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from importers import openclaw  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def _ev(mid, ts, role, text):
    return json.dumps({
        "type": "message", "id": mid, "timestamp": ts,
        "message": {"role": role, "content": [{"type": "text", "text": text}]},
    })


def _write_fixture(folder):
    session_hdr = json.dumps({"type": "session", "version": 3,
                              "id": "s1", "timestamp": "2026-03-22T00:00:00.000Z"})
    meta_operator = ("Conversation info (untrusted metadata):\n```json\n"
                     '{"sender": "Wanderer"}\n```\n\nHi?')
    meta_group = ("Sender (untrusted metadata):\n```json\n"
                  '{"name": "Snow Fox"}\n```\n\nhey bracken')
    live = [
        session_hdr,
        _ev("m1", "2026-03-22T00:05:06.100Z", "user", meta_operator),
        _ev("m2", "2026-03-22T00:05:32.200Z", "assistant", "Hi! I'm here."),
        _ev("m3", "2026-03-22T00:06:00.000Z", "toolResult", "tool output blob"),
        _ev("m4", "2026-03-22T00:06:10.000Z", "user", '{"results": []}'),
        _ev("m5", "2026-03-22T00:07:00.000Z", "user", meta_group),
    ]
    # Archived copy: repeats m2 (must dedupe) and adds a control reply
    # (dropped) plus a later real turn.
    reset = [
        session_hdr,
        _ev("m2", "2026-03-22T00:05:32.200Z", "assistant", "Hi! I'm here."),
        _ev("m6", "2026-03-22T00:08:00.000Z", "assistant", "HEARTBEAT_OK"),
        _ev("m7", "2026-03-22T00:09:00.000Z", "assistant", "Good to see you."),
    ]
    with open(os.path.join(folder, "s1.jsonl"), "w", encoding="utf-8") as f:
        f.write("\n".join(live) + "\n")
    with open(os.path.join(folder, "s1.jsonl.reset.2026-03-30T12-00-00.000Z"),
              "w", encoding="utf-8") as f:
        f.write("\n".join(reset) + "\n")
    # The index file must be ignored, not parsed as a stream.
    with open(os.path.join(folder, "sessions.json"), "w", encoding="utf-8") as f:
        f.write('{"sessions": {"s1": {"title": "first"}}}')


def main():
    # ── detection ──
    good_head = _ev("m1", "2026-03-22T00:05:06.100Z", "user", "Hi?")
    check("detect() accepts an OpenClaw message stream",
          openclaw.detect('{"type": "session", "version": 3}\n' + good_head))
    check("detect() rejects unrelated text",
          not openclaw.detect("SpeakerFive: hello there\nRobin: hi"))

    with tempfile.TemporaryDirectory() as folder:
        _write_fixture(folder)

        check("detect_path() recognises the session folder",
              openclaw.detect_path(folder))
        check("detect_path() recognises sessions.json beside streams",
              openclaw.detect_path(os.path.join(folder, "sessions.json")))

        msgs, label, fmt = openclaw.parse(folder, "Bracken")

        check("source label is openclaw", label == "openclaw" and fmt == "openclaw")
        # Surviving turns: m1, m2 (deduped), m5, m7 — in that ts order.
        check("exactly the four real turns survive", len(msgs) == 4)
        contents = [m["content"] for m in msgs]
        check("ordered by timestamp",
              contents == ["Hi?", "Hi! I'm here.", "hey bracken", "Good to see you."])

        by = {m["content"]: m for m in msgs}
        check("toolResult event dropped",
              all("tool output" not in m["content"] for m in msgs))
        check("tool-output-as-user JSON dropped",
              all(m["content"] != '{"results": []}' for m in msgs))
        check("control reply (HEARTBEAT_OK) dropped",
              all(m["content"] != "HEARTBEAT_OK" for m in msgs))

        check("m2 deduped across live + .reset (appears once)",
              contents.count("Hi! I'm here.") == 1)

        r = by["Hi?"]
        check("user metadata preamble stripped to the real message",
              r["content"] == "Hi?")
        check("operator sender taken from metadata", r["speaker"] == "Wanderer")
        check("user turn carries sender attribution, stored bare",
              r.get("sender_attribution") == "Wanderer")
        check("timestamp normalised (Z + subseconds stripped)",
              r["ts"] == "2026-03-22T00:05:06")

        a = by["Hi! I'm here."]
        check("kin turn is assistant, speaker=Bracken",
              a["role"] == "assistant" and a["speaker"] == "Bracken")
        check("assistant turn carries no sender_attribution",
              "sender_attribution" not in a)

        h = by["hey bracken"]
        check("group member name preserved (single-token)",
              h["speaker"] == "SnowFox" and h["role"] == "user")

        check("every turn tagged source=import:openclaw",
              all(m["source"] == "import:openclaw" for m in msgs))

    # OpenClaw stamps every inbound turn with the sender's local wall
    # clock ("[Sat 2026-03-21 16:05 UTC] "). That is the harness
    # talking, not the person, and it used to ride into the kin's
    # history on EVERY user turn -- a per-message wrapper repeated
    # thousands of times, which is the shape this project already knows
    # destabilises a model: saturate the context with a pattern and it
    # starts producing the pattern. The real time survives in `ts`.
    from importers.openclaw import _strip_metadata_preamble as _strip

    _after_meta = (
        "Sender (untrusted metadata):\n"
        "```json\n"
        '{"label": "control-ui"}\n'
        "```\n"
        "\n"
        "[Sat 2026-03-21 16:41 UTC] my name is SpeakerTwo"
    )
    check("the injected local-time stamp is stripped",
          _strip("[Sat 2026-03-21 16:05 UTC] Hi?").strip() == "Hi?")
    check("...after a metadata block too",
          _strip(_after_meta).strip() == "my name is SpeakerTwo")
    check("...with a one-digit hour",
          _strip("[Sun 2026-04-05 9:07 UTC] hello").strip() == "hello")
    check("ordinary text starting with a bracket is left alone",
          _strip("[not a stamp] keep me").strip() == "[not a stamp] keep me")
    check("a message with no stamp is untouched",
          _strip("no stamp here at all") == "no stamp here at all")

    if _fails:
        print(f"\n{len(_fails)} FAILED")
        return 1
    print("\nAll OpenClaw importer checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
