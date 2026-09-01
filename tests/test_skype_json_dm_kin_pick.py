# SPDX-License-Identifier: CC0-1.0

"""Pins the fix to importers/skype_json.py's DM role assignment.

The DM branch assumed the exporting account is always "the human
talking to a kin" and the other side of every 1:1 is always "the kin"
-- true for an ordinary personal archive, false when the account that
did the exporting IS the kin's own historical voice (the same
situation importers/skype_txt.py was fixed for). kin_display_name had
NO effect on this branch whatsoever: every value produced an identical
result, because the code never once looked at it. Confirmed against a
real export before this fix landed.

Fix: kin_display_name is checked against both sides of the DM before
falling back to the old assumption. A match on the operator's own
handle (and not the partner's) flips the direction; a match on the
partner, or no match at all, leaves the original behavior untouched --
that fallback matters, since requiring an exact match for every
ordinary DM would silently zero out the kin's slot whenever a Skype
display name doesn't match what got typed into Hearthkin.

Names in this file are all invented. Run:
    python tests/test_skype_json_dm_kin_pick.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from importers import skype_json

_fails = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print(f"{status} {label}")
    if not cond:
        _fails.append(label)


def _write_export(user_id, conv_id, conv_display_name, messages):
    """Build a minimal Skype messages.json export and return its path."""
    data = {
        "userId": user_id,
        "exportDate": "2024-01-01T00:00:00Z",
        "conversations": [
            {
                "id": conv_id,
                "displayName": conv_display_name,
                "MessageList": messages,
            }
        ],
    }
    d = Path(tempfile.mkdtemp(prefix="skype-json-test-"))
    p = d / "messages.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def _msg(sender, display_name, text, ts):
    return {
        "id": f"{ts}",
        "from": sender,
        "displayName": display_name,
        "originalarrivaltime": ts,
        "messagetype": "Text",
        "content": text,
    }


def _roles(canonical):
    return {m["speaker"]: m["role"] for m in canonical}


# A DM: the operator's own account is "8:speakerthree.handle" (their bare handle
# is how their own messages show up, per the module docstring). The
# partner is "SpeakerTwo Kerr", handle "speakertwo.k".

OPERATOR_ID = "8:speakerthree.handle"
PARTNER_ID = "8:speakertwo.k"
CONV_ID = "8:speakertwo.k"

# Skype's own MessageList is NEWEST-first; parse() reverses it to get
# oldest-to-newest. Building this fixture newest-first, matching a real
# export, so the chronological order below reads "hey" -> "hey yourself"
# -> "how's it going" AFTER parse()'s reversal, not before it.
MESSAGES = [
    _msg(PARTNER_ID, "SpeakerTwo Kerr", "how's it going", "2024-01-01T10:02:00.000Z"),
    _msg(PARTNER_ID, "SpeakerTwo Kerr", "hey yourself", "2024-01-01T10:01:00.000Z"),
    _msg(OPERATOR_ID, "speakerthree.handle", "hey", "2024-01-01T10:00:00.000Z"),
]


# --- the ordinary case: no signal, ordinary DM assumption holds -----------

path = _write_export(OPERATOR_ID, CONV_ID, "SpeakerTwo Kerr", MESSAGES)

canonical, _, _ = skype_json.parse(path, "", conversation_id=CONV_ID)
roles = _roles(canonical)
check("with no kin name given, the partner is the kin (old default)",
      roles.get("SpeakerTwo Kerr") == "assistant")
check("...and the operator's own turns stay user",
      roles.get("speakerthree.handle") == "user")

canonical_nomatch, _, _ = skype_json.parse(
    path, "somebody-else-entirely", conversation_id=CONV_ID)
roles_nomatch = _roles(canonical_nomatch)
check("a kin name matching NEITHER side falls back to the same default",
      roles_nomatch == roles)


# --- the ordinary case, explicit: kin name matches the partner ------------

canonical2, _, _ = skype_json.parse(path, "SpeakerTwo Kerr", conversation_id=CONV_ID)
roles2 = _roles(canonical2)
check("kin name matching the partner is unaffected by the fix",
      roles2.get("SpeakerTwo Kerr") == "assistant"
      and roles2.get("speakerthree.handle") == "user")


# --- the actual bug: kin name matches the OPERATOR's own side -------------

canonical3, _, _ = skype_json.parse(path, "speakerthree.handle", conversation_id=CONV_ID)
roles3 = _roles(canonical3)
check("kin name matching the OPERATOR flips the direction",
      roles3.get("speakerthree.handle") == "assistant")
check("...and the partner becomes user under their own name",
      roles3.get("SpeakerTwo Kerr") == "user")

by_speaker3 = {m["speaker"]: m for m in canonical3}
check("the partner's turns carry sender_attribution once flipped to user",
      by_speaker3["SpeakerTwo Kerr"].get("sender_attribution") == "SpeakerTwo Kerr")
check("the (now-kin) operator's turns carry no sender_attribution",
      "sender_attribution" not in by_speaker3["speakerthree.handle"])
check("message content is untouched by the flip, in chronological order",
      [m["content"] for m in canonical3 if m["speaker"] == "SpeakerTwo Kerr"]
      == ["hey yourself", "how's it going"])


# --- degenerate case: kin name matches BOTH sides (rare) ------------------
# Falls back to the ordinary assumption rather than guessing a direction.
# Newest-first input again: "hi back" (partner) is newest, "hi" (operator)
# is oldest, so after parse()'s reversal "hi" comes first chronologically.

path_both = _write_export(OPERATOR_ID, CONV_ID, "speakerthree.handle",
                          [_msg(PARTNER_ID, "speakerthree.handle", "hi back",
                               "2024-01-01T10:01:00.000Z"),
                           _msg(OPERATOR_ID, "speakerthree.handle", "hi",
                               "2024-01-01T10:00:00.000Z")])
canonical4, _, _ = skype_json.parse(path_both, "speakerthree.handle", conversation_id=CONV_ID)
check("kin name matching both sides doesn't crash or duplicate",
      len(canonical4) == 2)
check("...and falls back to the ordinary default: the operator's own "
      "(chronologically first) turn stays user",
      canonical4[0]["content"] == "hi" and canonical4[0]["role"] == "user")
check("...and the partner's turn is correctly the kin, as in the ordinary case",
      canonical4[1]["content"] == "hi back" and canonical4[1]["role"] == "assistant")


# --- case-insensitive match, same as every other importer -----------------
#
# The speaker label preserves whatever casing was actually typed (same as
# the ordinary partner-is-kin branch already does) -- so the assertion
# looks up the AS-TYPED key, not a normalized one.

canonical5, _, _ = skype_json.parse(path, "SPEAKERTHREE.HANDLE", conversation_id=CONV_ID)
check("matching the operator's handle is case-insensitive",
      _roles(canonical5).get("SPEAKERTHREE.HANDLE") == "assistant")


# --- a group conversation is completely unaffected -------------------------
# The group branch already matched by name before this fix; this just
# confirms the new DM-only computation doesn't leak into it.

GROUP_MESSAGES = [
    _msg(OPERATOR_ID, "speakerthree.handle", "morning", "2024-02-01T09:00:00.000Z"),
    _msg(PARTNER_ID, "SpeakerTwo Kerr", "morning", "2024-02-01T09:01:00.000Z"),
    _msg("8:speakerfour.vale", "SpeakerFour Vale", "hey all", "2024-02-01T09:02:00.000Z"),
]
group_data = {
    "userId": OPERATOR_ID,
    "exportDate": "2024-01-01T00:00:00Z",
    "conversations": [
        {
            "id": "19:groupid@thread.skype",
            "threadProperties": {"membercount": 3},
            "MessageList": GROUP_MESSAGES,
        }
    ],
}
d = Path(tempfile.mkdtemp(prefix="skype-json-test-group-"))
group_path = d / "messages.json"
group_path.write_text(json.dumps(group_data), encoding="utf-8")

canonical_g, _, _ = skype_json.parse(
    str(group_path), "SpeakerTwo Kerr", conversation_id="19:groupid@thread.skype")
roles_g = _roles(canonical_g)
check("group role assignment is untouched by the DM fix",
      roles_g.get("SpeakerTwo Kerr") == "assistant"
      and roles_g.get("speakerthree.handle") == "user"
      and roles_g.get("SpeakerFour Vale") == "user")


if _fails:
    print(f"\n{len(_fails)} FAILED: " + ", ".join(_fails))
    sys.exit(1)
print("\nall skype_json DM kin-pick checks passed")
