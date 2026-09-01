# SPDX-License-Identifier: CC0-1.0

"""
Skype text-export parser — handles the per-thread .txt files produced
by third-party Skype parser tools (Sharp Tool's SkypeParser and friends).

Format:

    (quietwatermark) 2024.01.15 10:00:00 UTC :
    Hi Marielle van Dijk, I'd like to add you as a contact.

    Marielle van Dijk (live:tealwing207) 2024.01.15 10:05:00 UTC :
    Thanks for the invite, adding you now.
    This second line has no header, so it folds into the message above.

Two header shapes:

  1. The OPERATOR's messages — header is `(skype_handle) DATE TIME UTC :`
     with NO display name preceding the parenthesized handle. Their
     display name in the .txt is just their bare Skype ID
     (e.g. "quietwatermark").

  2. The PARTNER's messages — header is
     `Display Name (skype:handle) DATE TIME UTC :`

Role mapping: a speaker becomes the kin (`role=assistant`) only when
their display name or handle matches `kin_display_name` — the same
rule importers/skype_json.py's group branch and every other importer
in this codebase already use. Everyone else is `role=user`, keeping
their own name, however many distinct people that turns out to be.

This replaced a two-party guess that used to decide role by which of
the TWO header shapes above a line matched — bare-paren (no display
name) meant "this is the exporting account's own line" -> role=user,
and EVERYTHING else, no matter how many distinct people, became
role=assistant. `kin_display_name` only ever excluded one candidate
from a "who talks the most" contest for the one `user` slot; it never
decided who became the kin. That degenerates exactly once a real file
has more than two speakers in it, which most Skype exports do — a
"normalized" .txt is very often a saved GROUP thread, not a private
DM, and this parser had no way to represent "several distinct people
here, none of them the kin." Confirmed against a real archive: a
five-person group thread filed three different real people as the
kin's own voice, alongside the kin's actual turns, with no signal that
anything had gone wrong — the operator only found it by chance in a
search result, months later. Root-caused by literally running the
parser with several different `kin_display_name` values against the
same file and watching TWO different people's roles flip depending on
which name excluded which contestant from the popularity contest —
not a coincidence, a designed-in failure mode.

A real two-person DM export (no bare-paren header at all, since the
exporting tool doesn't always use that shape) degrades safely under
name-matching: if `kin_display_name` is an exact pick from the
"who is the kin" list the import dialog shows (turn counts and all,
see dialogs/import_history.py), the one match becomes the kin and the
other party — the operator's own turns — becomes `user` under their
own name. If nothing matches (wrong name typed, or a file where the
kin genuinely never appears), NOBODY becomes the kin and the preview
shows zero assistant turns — visible and correctable before import,
rather than silently wrong after it.

System events appearing as `/ ... /` inline content (file transfers,
calls, etc.) are surfaced as `[skype system: ...]` markers so the
kin sees that something was there without us pulling in the URL noise.
"""

import datetime
import os
import re

from tools._io import robust_read_text


# Header regex. Two shapes captured by one pattern with alternation:
#   `(handle) DATE TIME UTC :`             — operator
#   `Display Name (handle) DATE TIME UTC :` — partner
#
# The leading display-name portion is optional but greedy to the open
# paren. We allow Skype handles with digits, dots, hyphens, colons
# (e.g. live:tealwing207, dana.whitlock2, renli.torvasque).
#
# Call entries from SkypeParser carry a duration-range trailer:
#   `... UTC - DATE TIME UTC :`
# That second timestamp is the call's end time (sometimes in local
# time, often nonsensical when SkypeParser's TZ guessing is wrong).
# We don't capture it — just consume it so the line is recognized
# as a header rather than being absorbed into the previous message's
# content.
_HEADER_RE = re.compile(
    r"^(?:(?P<display>[^()]+?)\s+)?"
    r"\((?P<handle>[\w.\-:]+)\)\s+"
    r"(?P<year>\d{4})\.(?P<month>\d{2})\.(?P<day>\d{2})\s+"
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})\s+UTC"
    r"(?:\s+-\s+\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2}\s+UTC)?"
    r"\s*:\s*$"
)

# Skype system events inside content are wrapped in slash-space:
#   / File: Foo.mp3 / To view this shared file, go to: ... /
#   / The group topic has been set to '...' by ... /
# These are noisy URL blobs that don't add relational value. Strip
# them to a short marker.
_SYSTEM_EVENT_RE = re.compile(r"^/(.+)/\s*$")


def detect(text):
    """Return True if `text` starts with a Skype .txt header line
    (handles BOM-prefixed files — SkypeParser writes UTF-8 BOM)."""
    lines = text.splitlines()
    for ln in lines[:10]:
        # Strip BOM if present on the first line.
        s = ln.lstrip("﻿").rstrip()
        if not s:
            continue
        if _HEADER_RE.match(s):
            return True
        # If we hit a non-empty line that's not a header in the first
        # 10 non-blank lines, this isn't a SkypeParser file.
        return False
    return False


def parse(source_path, kin_display_name, **_opts):
    """Parse one Skype .txt file. Returns (canonical_messages,
    source_label, fmt). A speaker becomes the kin (role=assistant)
    only when their display name or handle matches `kin_display_name`;
    everyone else is role=user, keeping their own name — see the
    module docstring for why this replaced a two-party guess.

    Raises ValueError if no recognizable messages are found."""
    text = robust_read_text(source_path)
    lines = text.splitlines()

    raw_msgs = []
    current = None

    for line in lines:
        s = line.lstrip("﻿").rstrip()
        m = _HEADER_RE.match(s)
        if m:
            if current is not None:
                raw_msgs.append(_finalize(current))
            display = (m.group("display") or "").strip()
            handle = m.group("handle").strip()
            ts = _safe_iso(
                int(m.group("year")), int(m.group("month")),
                int(m.group("day")), int(m.group("hour")),
                int(m.group("minute")), int(m.group("second")),
            )
            current = {
                "display": display,
                "handle": handle,
                "ts": ts,
                "lines": [],
            }
            continue
        if current is None:
            # Pre-amble before first header — skip.
            continue
        # Strip BOM defensively on any line (shouldn't happen past 1).
        current["lines"].append(line.lstrip("﻿"))

    if current is not None:
        raw_msgs.append(_finalize(current))

    if not raw_msgs:
        raise ValueError(
            f"No Skype messages parsed from {source_path}."
        )

    canonical = []
    for rm in raw_msgs:
        c = _to_canonical(rm, kin_display_name)
        if c is not None:
            canonical.append(c)

    if not canonical:
        raise ValueError(
            f"No content-bearing Skype messages survived cleanup in "
            f"{source_path}."
        )

    return canonical, "skype_dm", "skype_txt"


# ─── Helpers ──────────────────────────────────────────────────────── #

def _finalize(raw):
    """Drop blank trailing lines, collapse content. System-event
    inline markers get rewritten to short tags."""
    content_lines = []
    for ln in raw["lines"]:
        stripped = ln.strip()
        if not stripped and not content_lines:
            # Skip leading blanks; keep trailing/inner for paragraph shape.
            continue
        # Rewrite system events.
        sys_m = _SYSTEM_EVENT_RE.match(stripped)
        if sys_m:
            body = sys_m.group(1).strip()
            # Trim the boilerplate "To view this shared file, go to:
            # <url>" suffix that SkypeParser doubles up.
            body = re.split(r"\s+To view this shared file,", body, 1)[0]
            content_lines.append(f"[skype system: {body}]")
            continue
        content_lines.append(ln)
    content = "\n".join(content_lines).rstrip()
    return {
        "display": raw["display"],
        "handle": raw["handle"],
        "ts": raw["ts"],
        "content": content,
    }


def _to_canonical(rm, kin_display_name):
    """Convert a finalized message to Hearthkin's on-disk shape.

    THE ASSISTANT SLOT IS THE KIN'S ALONE, same policy as every other
    importer in this codebase: role=assistant iff this speaker's
    display name or handle matches `kin_display_name`
    (case-insensitive). Every other speaker is role=user with their
    own name kept — regardless of how many distinct people that is,
    and regardless of who happens to have the most turns. Turn count
    is not identity; the previous version of this function used it as
    one anyway (see the module docstring)."""
    if not rm.get("content"):
        return None
    handle = rm["handle"]
    display = rm["display"] or handle
    speaker_label = display

    kin_lower = (kin_display_name or "").strip().lower()
    is_kin = bool(kin_lower) and (
        display.strip().lower() == kin_lower
        or handle.strip().lower() == kin_lower
    )
    role = "assistant" if is_kin else "user"

    out = {
        "role": role,
        "content": rm["content"],
        "ts": rm["ts"],
        "speaker": speaker_label,
        "source": "import:skype",
    }
    if role == "user":
        # Bare, like live capture stores it — the reading surface adds the
        # bracket (chat_helpers.speaker_attribution_prefix).
        out["sender_attribution"] = speaker_label
    return out


def _safe_iso(year, month, day, hour, minute, second):
    try:
        return datetime.datetime(
            year, month, day, hour, minute, second,
        ).isoformat()
    except (ValueError, TypeError):
        return None
