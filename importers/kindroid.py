# SPDX-License-Identifier: CC0-1.0

"""
Kindroid chat-log parser.

Kindroid's text exports use a "speaker on their own line, content
on following lines" shape, no in-body timestamps:

    SpeakerFifteen
    Good morning. The kettle has just boiled if you want some.
    Priya
    Perfect timing — I was about to ask.

Two variations supported:

  1. Standard chat export — the shape above. The kin's name often
     has a `play` line right under it (Kindroid voice-button UI
     artifact) and sometimes a `show recalled memories` line
     (recall-button artifact). Both are filtered out.

  2. Voice call transcript — same shape with a three-line header:

         Voice Call Transcript
         12 minutes • 1/15/2024, 10:00:00 AM
         Close transcript

     The header's date/time anchors the first message's timestamp;
     subsequent messages stride 30s from there.

For standard exports (no header), the anchor is the source file's
mtime — Kindroid doesn't record the conversation date inside the
.txt itself, so the file's last-modified time is the closest honest
signal we have. Strides of 30s within the file keep messages sorted
and don't fake a precision Kindroid never provided.

Speaker detection: the caller passes the kin's display name. The
parser scans for standalone "name-shaped" lines (short, no quote
or asterisk leader, no terminal sentence punctuation) and identifies
the operator by frequency — the most-frequent non-kin name-shaped
line that appears at least three times is the operator. The
three-times floor keeps short interjections in roleplay content
("Yeah", "Okay") from being misidentified as a third speaker.
"""

import datetime
import os
import re
from collections import Counter

from tools._io import robust_read_text


# Kindroid UI artifacts that occasionally land as standalone lines
# in the .txt export and are not part of any speaker's actual content.
# Skipped during the message-walk.
#
#   `play`                    — voice-button label, between speaker
#                               name and message body
#   `show recalled memories`  — recall-button label, same position
#   `Edit message`            — edit-button label, trails certain
#                               assistant messages (the editable ones
#                               in the source UI). Present in most but
#                               not all exports; missing this caused
#                               the literal string to land as a
#                               trailing line on those messages.
_ARTIFACT_LINES = {"play", "show recalled memories", "Edit message"}

# Voice Call Transcript header — three-line preamble whose last
# line is exactly "Close transcript".
_VC_HEADER_FIRST_LINE = "Voice Call Transcript"
_VC_HEADER_LAST_LINE = "Close transcript"

# The duration/timestamp line in the VC header looks like
#   "12 minutes • 1/15/2024, 10:00:00 AM"
# US-style M/D/YYYY date plus 12-hour clock. We pull it out
# best-effort; on any parse failure we fall back to file mtime.
_VC_DATETIME_RE = re.compile(
    r"(\d{1,2})/(\d{1,2})/(\d{4}),\s+(\d{1,2}):(\d{2}):(\d{2})\s+([AP]M)"
)

# A line that LOOKS like a speaker name. Kindroid speaker lines take a
# wide range of name shapes — an all-lowercase handle, a capitalised
# first name, a two-word first-and-last, or a generic role word like
# "Assistant" — so the test is deliberately loose: single word or
# two-word, mixed case allowed, no terminal punctuation, no quote /
# asterisk / bracket leader.
_NAME_RE = re.compile(r"^[A-Za-z][\w.'\-]*(?:\s[A-Za-z][\w.'\-]*)?$")

# Hand-authored `Name: text` shape — used in the detection function
# to disqualify files that are hand-authored (where the first line
# might pass the name-shape test but the second line carries the
# `Name: ` prefix that text_log's hand-authored parser owns).
_HAND_AUTHORED_LINE = re.compile(r"^[A-Za-z][\w.'\-]{0,40}?:\s+")


def _looks_like_speaker_name(s):
    """A line is a speaker-name candidate if it's short, name-shaped,
    and not one of the known UI artifact strings."""
    if not (1 <= len(s) <= 30):
        return False
    if s in _ARTIFACT_LINES:
        return False
    return bool(_NAME_RE.match(s))


def detect(text):
    """Return True if `text` looks like a Kindroid chat-log export.

    Three signals — any one fires:
      1. File starts with the Voice Call Transcript header.
      2. File contains at least one standalone `play` line in the
         first ~400 lines (Kindroid voice-button UI artifact —
         doesn't appear in Telegram or hand-authored exports).
      3. The first non-empty line is a bare speaker-shaped name
         AND the second non-empty line is NOT a `Name: text` shape
         (which would be the hand-authored format that text_log owns).
    """
    lines = text.splitlines()
    if lines and lines[0].strip() == _VC_HEADER_FIRST_LINE:
        return True
    # Cap the scan so a multi-MB file doesn't pay full traversal cost
    # just to detect.
    for ln in lines[:400]:
        if ln.strip() == "play":
            return True
    nonempty = [ln.strip() for ln in lines if ln.strip()]
    if len(nonempty) >= 2:
        first, second = nonempty[0], nonempty[1]
        if _looks_like_speaker_name(first):
            if not _HAND_AUTHORED_LINE.match(second):
                return True
    return False


def parse(source_path, kin_display_name):
    """Parse a Kindroid chat-log file. Returns
    (canonical_messages, source_label, fmt). Matches the contract
    used by importers.text_log.parse so the dialog can dispatch
    uniformly."""
    raw = robust_read_text(source_path)
    lines = raw.splitlines()

    # ─── Voice Call Transcript header: skip + anchor time ─────────── #
    anchor = None
    if lines and lines[0].strip() == _VC_HEADER_FIRST_LINE:
        if len(lines) >= 2:
            m = _VC_DATETIME_RE.search(lines[1])
            if m:
                mo, da, yr, h12, mn, sec, ampm = m.groups()
                h12 = int(h12)
                if ampm == "PM" and h12 != 12:
                    h12 += 12
                elif ampm == "AM" and h12 == 12:
                    h12 = 0
                anchor = _safe_dt(
                    int(yr), int(mo), int(da), h12, int(mn), int(sec),
                )
        # Walk past the header (everything up to and including
        # "Close transcript").
        idx = 0
        while idx < len(lines):
            if lines[idx].strip() == _VC_HEADER_LAST_LINE:
                idx += 1
                break
            idx += 1
        lines = lines[idx:]

    if anchor is None:
        try:
            mtime = os.path.getmtime(source_path)
            anchor = datetime.datetime.fromtimestamp(mtime).replace(
                microsecond=0,
            )
        except (OSError, ValueError):
            anchor = datetime.datetime.now().replace(microsecond=0)

    # ─── Identify the speakers ────────────────────────────────────── #
    speaker_set = _identify_speakers(lines, kin_display_name)

    # ─── Walk lines, building messages ────────────────────────────── #
    msgs = []
    current = None
    for line in lines:
        stripped = line.strip()
        if not stripped:
            # Blank line — preserve as a paragraph break inside the
            # current message, but don't end it. Kindroid roleplay
            # messages often span paragraphs; keeping the breaks
            # makes them readable on re-display.
            if current is not None:
                current["lines"].append("")
            continue
        if stripped in _ARTIFACT_LINES:
            continue
        if stripped in speaker_set:
            if current is not None:
                finalized = _finalize(current)
                if finalized["content"]:
                    msgs.append(finalized)
            current = {"speaker": stripped, "lines": []}
            continue
        if current is None:
            # Stray content before any recognized speaker — skip.
            continue
        current["lines"].append(line.rstrip())

    if current is not None:
        finalized = _finalize(current)
        if finalized["content"]:
            msgs.append(finalized)

    # ─── Assign synthetic timestamps ──────────────────────────────── #
    for i, m in enumerate(msgs):
        ts_dt = anchor + datetime.timedelta(seconds=30 * i)
        m["ts"] = ts_dt.replace(microsecond=0).isoformat()

    canonical = [_to_canonical(m, kin_display_name) for m in msgs]
    return canonical, "kindroid", "kindroid"


# ─── Helpers ──────────────────────────────────────────────────────── #

def _identify_speakers(lines, kin_display_name):
    """Frequency-rank standalone name-shaped lines; return a set
    containing the kin (always) plus the most-frequent non-kin
    candidate that appears at least three times.

    The three-times floor is the guard against short content-line
    interjections — a single "Yeah" or "Okay" as a one-off content
    line technically matches the name-shape regex, but a real
    speaker name in Kindroid's alternating-turn format will appear
    many times across the file.

    If the operator typo'd the kin name, the kin name's count is 0
    and the returned set contains both the (incorrect) kin name and
    the actual second speaker — the import preview will then show
    "0 kin turns, N from others" and the operator can correct."""
    counts = Counter()
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if _looks_like_speaker_name(s):
            counts[s] += 1

    speakers = {kin_display_name}
    other_threshold = 3
    for name, count in counts.most_common():
        if name == kin_display_name:
            continue
        if count >= other_threshold:
            speakers.add(name)
            break
    return speakers


def _finalize(raw):
    """Convert the working dict (speaker, lines) into a flat
    {speaker, content} dict by joining the line list and trimming
    leading/trailing blank lines from the content."""
    content = "\n".join(raw["lines"]).strip()
    return {"speaker": raw["speaker"], "content": content}


def _to_canonical(msg, kin_display_name):
    """Convert a parsed message into Hearthkin's on-disk shape."""
    speaker = msg["speaker"]
    role = "assistant" if speaker == kin_display_name else "user"
    out = {
        "role": role,
        "content": msg["content"],
        "ts": msg["ts"],
        "speaker": speaker,
        "source": "import:kindroid",
    }
    if role == "user":
        # Bare, like live capture stores it — the reading surface adds the
        # bracket (chat_helpers.speaker_attribution_prefix).
        out["sender_attribution"] = speaker
    return out


def _safe_dt(year, month, day, hour, minute, second):
    """Datetime constructor that returns None on invalid input rather
    than raising. Caller falls back to file mtime."""
    try:
        return datetime.datetime(year, month, day, hour, minute, second)
    except (ValueError, TypeError):
        return None
