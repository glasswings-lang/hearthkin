# SPDX-License-Identifier: CC0-1.0

"""Pins the fix to importers/openclaw.py's kin selection.

Every OpenClaw message event already carries an authoritative role from
when it actually happened -- assistant on the agent's own generated
replies, user on whatever a human typed live. That's reliable in a way
Skype's structural guesses never were, so kin_display_name was never
checked against it: the folder's own original agent turns ALWAYS became
the imported kin, unconditionally, no matter what was picked.

That default is right for the ordinary case (this folder IS one
specific kin's whole life). It breaks the moment kin_display_name names
a HUMAN sender who appears in this same session-store -- the same
situation the Skype fixes exist for: an account that is itself a kin's
own historical voice, not a separate person talking to one. Confirmed
against a real archive before this fix landed: a recurring human
sender's own turns stayed role=user no matter what was picked, while the
folder's actual agent got silently relabeled with that same person's
name -- one name claiming two contradictory roles in the same
conversation.

Names in this file are all invented. Run:
    python tests/test_openclaw_user_kin_pick.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from importers import openclaw

_fails = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print(f"{status} {label}")
    if not cond:
        _fails.append(label)


def _write_session(events):
    """One session .jsonl file in a fresh folder; returns the folder path."""
    d = Path(tempfile.mkdtemp(prefix="openclaw-test-"))
    p = d / "session1.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return str(d)


def _assistant_ev(eid, text, ts):
    return {"type": "message", "id": eid, "timestamp": ts,
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": text}]}}


def _user_ev(eid, sender, text, ts):
    body = f'Sender (untrusted metadata):\n```json\n{{"sender": "{sender}"}}\n```\n{text}'
    return {"type": "message", "id": eid, "timestamp": ts,
            "message": {"role": "user",
                        "content": [{"type": "text", "text": body}]}}


def _roles(canonical):
    return {m["speaker"]: m["role"] for m in canonical}


EVENTS = [
    _user_ev("e1", "SpeakerOne", "hey there",       "2024-01-01T10:00:00.000Z"),
    _assistant_ev("e2", "hi SpeakerOne",            "2024-01-01T10:00:05.000Z"),
    _user_ev("e3", "SpeakerFour", "hi from me too",  "2024-01-01T10:01:00.000Z"),
    _assistant_ev("e4", "hey SpeakerFour",           "2024-01-01T10:01:05.000Z"),
]


# --- the ordinary case: kin name matches nobody's sender, unaffected -----

folder = _write_session(EVENTS)
canonical, _, fmt = openclaw.parse(folder, "SpeakerTwo")
roles = _roles(canonical)
check("fmt is openclaw", fmt == "openclaw")
check("ordinary case: the folder's agent is the kin (old default)",
      roles.get("SpeakerTwo") == "assistant")
check("...and every human sender stays user under their own name",
      roles.get("SpeakerOne") == "user" and roles.get("SpeakerFour") == "user")


# --- the actual bug: kin name matches a HUMAN sender ----------------------

canonical2, _, _ = openclaw.parse(folder, "SpeakerOne")
roles2 = _roles(canonical2)
check("kin name matching a human sender promotes THAT sender to the kin",
      roles2.get("SpeakerOne") == "assistant")
check("...the OTHER human sender still stays user under their own name",
      roles2.get("SpeakerFour") == "user")
check("...and the folder's original agent demotes to user, not vanishing",
      roles2.get("the agent this session originally ran") == "user")
check("no name claims two contradictory roles at once",
      len({m["speaker"] for m in canonical2 if m["role"] == "assistant"}) == 1)

by_speaker2 = {m["speaker"]: m for m in canonical2}
check("the promoted sender's turn carries no sender_attribution",
      "sender_attribution" not in by_speaker2["SpeakerOne"])
check("the demoted original-agent turn DOES carry sender_attribution "
      "(it's role=user now, same as any other speaker)",
      by_speaker2["the agent this session originally ran"].get(
          "sender_attribution") == "the agent this session originally ran")


# --- content and other senders are untouched by the flip ------------------

check("message content survives the flip",
      by_speaker2["SpeakerOne"]["content"] == "hey there")
check("SpeakerFour's own content is untouched",
      by_speaker2["SpeakerFour"]["content"] == "hi from me too")


# --- case-insensitive match, same as every other importer -----------------
#
# The speaker label preserves whatever casing was actually typed, same
# precedent as skype_json.py's group branch (`speaker_label =
# kin_display_name` on a match, not the message's own recorded display
# text) -- so the assertion looks up the AS-TYPED key.

canonical3, _, _ = openclaw.parse(folder, "speakerone")
check("matching a human sender is case-insensitive",
      _roles(canonical3).get("speakerone") == "assistant")


# --- no kin name given: safe, unchanged default ---------------------------

canonical4, _, _ = openclaw.parse(folder, "")
roles4 = _roles(canonical4)
check("an empty kin name changes nothing about the old default",
      roles4.get("SpeakerOne") == "user" and roles4.get("SpeakerFour") == "user")


if _fails:
    print(f"\n{len(_fails)} FAILED: " + ", ".join(_fails))
    sys.exit(1)
print("\nall openclaw user-kin-pick checks passed")
