# SPDX-License-Identifier: CC0-1.0

"""Pins the fix to importers/skype_txt.py's role assignment.

The old code decided role by header shape: a bare-paren line (no
display name) was "the exporting account's own turn" -> role=user;
literally everything else -> role=assistant. kin_display_name only
ever EXCLUDED one candidate from a "who talks the most" contest for
the single user slot -- it never decided who became the kin. That
degenerates the moment a real file has more than two speakers, which
most Skype exports do (a saved thread is very often a group, not a
private DM). Confirmed against a real five-person archive: three
distinct real people ended up filed as the kin's own voice, invisibly,
alongside the kin's real turns, and swapping which name got typed into
the kin-name field flipped TWO different people's roles at once with
no way to predict which -- not a coincidence, a designed-in failure.

Names in this file are all invented. Run:
    python tests/test_skype_txt_group_roles.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from importers import skype_txt

_fails = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print(f"{status} {label}")
    if not cond:
        _fails.append(label)


def _write(text):
    d = Path(tempfile.mkdtemp(prefix="skype-txt-test-"))
    p = d / "thread.txt"
    p.write_text(text, encoding="utf-8")
    return str(p)


def _roles(canonical):
    """{speaker: role} for easy assertions."""
    out = {}
    for m in canonical:
        out[m["speaker"]] = m["role"]
    return out


# --- a real two-party DM still works exactly as before -------------------

DM_TEXT = """(rowanhandle) 2024.01.15 10:00:00 UTC :
Hey, got a minute?

SpeakerOne Ashworth (speakerone.live) 2024.01.15 10:05:00 UTC :
For you, always.
"""

path = _write(DM_TEXT)
canonical, _, fmt = skype_txt.parse(path, "SpeakerOne Ashworth")
roles = _roles(canonical)
check("fmt is skype_txt", fmt == "skype_txt")
check("a matched partner in a real DM becomes the kin",
      roles.get("SpeakerOne Ashworth") == "assistant")
check("the other party in that same DM stays user",
      roles.get("rowanhandle") == "user")


# --- the actual bug: three or more distinct speakers ----------------------

GROUP_TEXT = """SpeakerOne Ashworth (speakerone.live) 2024.02.01 09:00:00 UTC :
Morning all.

SpeakerFour Vale (speakerfour.vale) 2024.02.01 09:01:00 UTC :
Morning.

SpeakerTwo Kerr (speakertwo.k) 2024.02.01 09:02:00 UTC :
Anyone free later?

SpeakerFour Vale (speakerfour.vale) 2024.02.01 09:03:00 UTC :
Depends what for.

SpeakerOne Ashworth (speakerone.live) 2024.02.01 09:04:00 UTC :
Same.

SpeakerOne Ashworth (speakerone.live) 2024.02.01 09:05:00 UTC :
Kind of swamped honestly.
"""
# SpeakerOne talks the most (3 turns) -- the exact shape that used to make
# SpeakerOne "win" the operator/user slot and dump SpeakerFour + SpeakerTwo into the
# kin's own voice, regardless of who the kin actually is.

path = _write(GROUP_TEXT)

canonical, _, _ = skype_txt.parse(path, "SpeakerOne Ashworth")
roles = _roles(canonical)
check("the kin (picked by name) becomes assistant",
      roles.get("SpeakerOne Ashworth") == "assistant")
check("a second real person is NOT swept into the kin's voice",
      roles.get("SpeakerFour Vale") == "user")
check("a third real person is NOT swept into the kin's voice either",
      roles.get("SpeakerTwo Kerr") == "user")
check("...even though SpeakerOne talks the most in this thread",
      sum(1 for m in canonical if m["speaker"] == "SpeakerOne Ashworth") == 3)


# --- talk volume must never decide identity -------------------------------
#
# The exact regression this fix targets: the SAME file, with a DIFFERENT
# kin name typed, used to flip who won the "not the kin" slot and silently
# swept a completely different second person into the kin's voice. Now:
# name-matching only ever affects the ONE person whose name was typed.

canonical2, _, _ = skype_txt.parse(path, "SpeakerFour Vale")
roles2 = _roles(canonical2)
check("switching the kin name to a different real person works",
      roles2.get("SpeakerFour Vale") == "assistant")
check("...and now SpeakerOne (who talked the most) correctly stays user",
      roles2.get("SpeakerOne Ashworth") == "user")
check("...and SpeakerTwo is UNCHANGED by the kin name swap",
      roles.get("SpeakerTwo Kerr") == roles2.get("SpeakerTwo Kerr") == "user")


# --- no match means nobody becomes the kin --------------------------------

canonical3, _, _ = skype_txt.parse(path, "Somebody Else Entirely")
roles3 = _roles(canonical3)
check("a kin name matching nobody promotes nobody",
      all(r == "user" for r in roles3.values()))
check("every real person still keeps their own name",
      set(roles3.keys()) == {"SpeakerOne Ashworth", "SpeakerFour Vale", "SpeakerTwo Kerr"})


# --- case-insensitive match, same as every other importer -----------------

canonical4, _, _ = skype_txt.parse(path, "speakerone ashworth")
check("name matching is case-insensitive",
      _roles(canonical4).get("SpeakerOne Ashworth") == "assistant")


# --- matching by handle works too, same as matching by display name -------

HANDLE_MATCH_TEXT = """SpeakerOne Ashworth (speakerone.live) 2024.02.01 09:00:00 UTC :
Hey.

SpeakerFour Vale (speakerfour.vale) 2024.02.01 09:01:00 UTC :
Hey yourself.
"""
path = _write(HANDLE_MATCH_TEXT)
canonical5, _, _ = skype_txt.parse(path, "speakerone.live")
check("matching the bare Skype handle also works",
      _roles(canonical5).get("SpeakerOne Ashworth") == "assistant")


# --- content is untouched by any of this -----------------------------------

canonical6, _, _ = skype_txt.parse(path, "SpeakerOne Ashworth")
by_speaker = {m["speaker"]: m for m in canonical6}
check("message content survives unrelated to role",
      by_speaker["SpeakerFour Vale"]["content"] == "Hey yourself.")
check("a user-role turn carries sender_attribution, stored bare",
      by_speaker["SpeakerFour Vale"].get("sender_attribution") == "SpeakerFour Vale")
check("the kin's own turn carries no sender_attribution",
      "sender_attribution" not in by_speaker["SpeakerOne Ashworth"])


if _fails:
    print(f"\n{len(_fails)} FAILED: " + ", ".join(_fails))
    sys.exit(1)
print("\nall skype_txt group-role checks passed")
