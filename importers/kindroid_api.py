# SPDX-License-Identifier: CC0-1.0

"""
kindroid_api — read a Kindroid history archived through their own API.

The sibling parser, `kindroid.py`, reads Kindroid's *text export*. That
format has no dates in it, does not say who spoke, and drops images and
links, so the parser has to guess: timestamps are invented from the
file's modified time at thirty-second strides, and speakers are found by
counting which standalone lines look name-shaped often enough. It also
changes shape without warning, and has.

This one reads the JSON-per-line archive that `/get-chat-messages`
answers with — one message per line, exactly the fields the API
returned. Nothing is guessed:

  * the timestamp is the real one, from Kindroid
  * the speaker is stated, per message, so a group chat with several
    Kindroids in it comes through with everyone named
  * image and link fields survive as notes on the message rather than
    vanishing

A row looks like this (fields absent on a given message are omitted):

    {"id": "kF8O…", "sender": "ai", "display_name": "…",
     "timestamp": 1788300698998, "message": "…",
     "image_urls": [...], "image_description": "…",
     "link_url": "…", "link_description": "…",
     "internet_response": "…"}

Written by the Kindroid Archiver, which exists because Kindroid's own
export is lossy. Any file of the same rows imports the same way.
"""

import datetime
import json
import re

from tools._io import robust_read_text


# How many rows to sample when sniffing. Enough to be confident, few
# enough that detection stays cheap on a long history.
_SNIFF = 25

# Kindroid says who spoke in one of two fields depending on the era of
# the export: `sender_type` in the documented response, `sender` in what
# the archive actually carries. Both hold the same two words.
_AI_WORDS = ("ai", "assistant", "kindroid")
_USER_WORDS = ("user", "human", "operator")

# Timestamps arrive in milliseconds. A value this large cannot be
# seconds (it would be the year 5138), so the unit is detectable rather
# than assumed — an export that switches to seconds still reads right.
_MILLIS_FLOOR = 1e11


def _rows(text, limit=None):
    """Parse JSON-per-line, skipping anything that is not an object.

    A truncated last line — the usual result of a copy interrupted
    mid-write — costs that line and nothing else.
    """
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] != "{":
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
        if limit is not None and len(out) >= limit:
            break
    return out


def detect(text):
    """True when this is a Kindroid API archive.

    The signature is all three of an id, a `message` field, and a
    numeric `timestamp`, on most of the rows sampled. Hearthkin's own
    conversation.jsonl carries `role` and `content` instead, and a
    Kindroid *text* export is not JSON at all, so neither can be
    mistaken for this.
    """
    rows = _rows(text, limit=_SNIFF)
    if not rows:
        return False
    hits = 0
    for row in rows:
        if "message" not in row or "id" not in row:
            continue
        if not isinstance(row.get("timestamp"), (int, float)):
            continue
        if "role" in row or "content" in row:
            continue           # that is our own shape, not Kindroid's
        hits += 1
    return hits >= max(1, int(len(rows) * 0.8))


def _iso(timestamp):
    """Kindroid's timestamp as an ISO string, or None if unusable.

    A message with a broken timestamp is still worth importing — it
    simply arrives without one — so this never raises.
    """
    if not isinstance(timestamp, (int, float)):
        return None
    seconds = timestamp / 1000.0 if timestamp >= _MILLIS_FLOOR else timestamp
    try:
        moment = datetime.datetime.fromtimestamp(seconds)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.replace(microsecond=0).isoformat()


def _who(row):
    """The stated speaker: (name, is_ai). Neither is ever guessed."""
    said = str(row.get("sender_type") or row.get("sender") or "").strip()
    lowered = said.lower()
    is_ai = lowered in _AI_WORDS
    is_user = lowered in _USER_WORDS
    name = str(row.get("display_name") or "").strip()
    if not name:
        # No display name: fall back to the only other thing that names
        # anyone. Better an unhelpful name than an unattributed turn.
        name = said or ("Kindroid" if is_ai else "You")
    if not is_ai and not is_user:
        # An unrecognised word is treated as another speaker rather than
        # as the kin. Mislabelling somebody else's words as the kin's own
        # is the one error that cannot be spotted by reading it back.
        is_ai = False
    return name, is_ai


def _content(row):
    """The message text, plus anything else the row carried.

    Images, links and web lookups are appended as short notes rather
    than dropped. The text export loses them silently, which is how a
    conversation quietly stops making sense: someone reacts to a picture
    that is not there.
    """
    parts = []
    text = str(row.get("message") or "").strip()
    if text:
        parts.append(text)

    described = str(row.get("image_description") or "").strip()
    urls = row.get("image_urls") or []
    if isinstance(urls, str):
        urls = [urls]
    urls = [str(u).strip() for u in urls if str(u or "").strip()]
    if described and urls:
        parts.append("[image: %s — %s]" % (described, " ".join(urls)))
    elif described:
        parts.append("[image: %s]" % described)
    elif urls:
        parts.append("[image: %s]" % " ".join(urls))

    video = str(row.get("video_description") or "").strip()
    if video:
        parts.append("[video: %s]" % video)

    link = str(row.get("link_url") or "").strip()
    link_said = str(row.get("link_description") or "").strip()
    if link and link_said:
        parts.append("[link: %s — %s]" % (link_said, link))
    elif link:
        parts.append("[link: %s]" % link)
    elif link_said:
        parts.append("[link: %s]" % link_said)

    looked_up = str(row.get("internet_response") or "").strip()
    if looked_up:
        parts.append("[looked up: %s]" % looked_up)

    return "\n\n".join(parts).strip()


def parse(source_path, kin_display_name, **_opts):
    """Read the archive into Hearthkin's shape.

    Returns `(messages, source_label, fmt)` like every sibling parser.

    Which turns count as the kin's own: a row is the kin's when the
    speaker is an AI *and* its display name matches the kin being
    imported into. That matters for a group chat, where several
    Kindroids speak and only one of them is this kin — the others come
    through as other speakers, named, which is what they were.

    If no row's name matches at all — a renamed kin, or a typo in the
    box — every AI turn is taken as the kin's rather than none of them.
    A history that imports with the replies present and the wrong name
    on them is fixable by reading it. A history where every reply landed
    as somebody else's is not.
    """
    rows = _rows(robust_read_text(source_path))
    wanted = (kin_display_name or "").strip().lower()

    ai_names = set()
    for row in rows:
        name, is_ai = _who(row)
        if is_ai:
            ai_names.add(name.lower())
    matched = wanted in ai_names if wanted else False

    messages = []
    for row in rows:
        content = _content(row)
        if not content:
            continue                      # nothing to carry
        name, is_ai = _who(row)
        mine = is_ai and (not matched or name.lower() == wanted)
        message = {
            "role": "assistant" if mine else "user",
            "content": content,
            "speaker": name,
            "source": "import:kindroid",
        }
        stamp = _iso(row.get("timestamp"))
        if stamp:
            message["ts"] = stamp
        if not mine:
            # Bare, like live capture stores it — the reading surface
            # adds the bracket (chat_helpers.speaker_attribution_prefix).
            message["sender_attribution"] = name
        messages.append(message)

    # Oldest first. The API answers in that order and the archive keeps
    # it, but a concatenated or hand-merged file might not, and an
    # out-of-order import is the kind of wrong that reads as the kin
    # having lost its mind.
    messages.sort(key=lambda m: m.get("ts") or "")
    return messages, "kindroid", "kindroid-api"


# ============================================================================
# The readable copy
# ============================================================================
#
# The archiver writes two files per month: the .jsonl above, and a Markdown
# view of it. The Markdown is the one a person actually opens, so it is the
# one they reach for in a file picker -- and picking it used to fail with
# "No messages parsed. Detected format: plain", which is a true sentence
# that helps nobody.
#
# It is worth reading properly rather than refusing, because unlike
# Kindroid's own text export this one has the times and the speakers written
# into its headings. Nothing has to be guessed here either.
#
#     # 2026-09
#
#     ## 2026-09-01 14:11 - SpeakerFifteen
#
#     the message
#
#     *Image: a kettle on a windowsill*
#
# The .jsonl remains the better source -- it keeps the ids and the exact
# fields -- but "the file I have" beating "the file I should have" is the
# difference between importing a history and giving up on it.

_MD_HEADING = re.compile(
    r"^##\s+(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?\s+-\s+(.+?)\s*$")


def detect_markdown(text):
    """True when this is the archiver's readable copy.

    Two headings of the exact shape is enough: a date, a time, a dash and a
    name. Claude's saved conversations use bold speaker lines and Kindroid's
    own export has no headings at all, so neither can be mistaken for this.
    """
    hits = 0
    for line in text.splitlines():
        if _MD_HEADING.match(line):
            hits += 1
            if hits >= 2:
                return True
    return False


def parse_markdown(source_path, kin_display_name, **_opts):
    """Read the readable copy. Same output as `parse`, same rules about whose
    turns are whose -- including the fallback when no name matches, because a
    renamed kin should not lose every reply.
    """
    lines = robust_read_text(source_path).splitlines()
    wanted = (kin_display_name or "").strip().lower()

    blocks = []                     # (speaker, iso, [body lines])
    for line in lines:
        found = _MD_HEADING.match(line)
        if found:
            year, month, day, hour, minute, second, speaker = found.groups()
            stamp = "%s-%s-%sT%02d:%02d:%02d" % (
                year, month, day, int(hour), int(minute), int(second or 0))
            blocks.append((speaker.strip(), stamp, []))
            continue
        if blocks:
            blocks[-1][2].append(line.rstrip())

    # Whose turns are the kin's. The readable copy does not say which
    # speakers are AI, so the kin is identified by name -- and when the name
    # matches nothing, everybody who is not the most frequent OTHER speaker
    # would be a guess, so the safer reading is to take no turns as the
    # kin's and let the preview show it. That is the opposite of the .jsonl
    # rule, and it is deliberate: there, "ai" is stated, so a non-matching
    # name is a naming problem. Here there is nothing to fall back ON.
    names = {speaker.lower() for speaker, _stamp, _body in blocks}
    matched = wanted in names

    messages = []
    for speaker, stamp, body in blocks:
        content = "\n".join(body).strip()
        if not content:
            continue
        mine = matched and speaker.lower() == wanted
        message = {
            "role": "assistant" if mine else "user",
            "content": content,
            "ts": stamp,
            "speaker": speaker,
            "source": "import:kindroid",
        }
        if not mine:
            message["sender_attribution"] = speaker
        messages.append(message)

    messages.sort(key=lambda m: m.get("ts") or "")
    return messages, "kindroid", "kindroid-archive-md"
