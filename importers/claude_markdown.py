# SPDX-License-Identifier: CC0-1.0

"""
Claude conversations already extracted to Markdown.

A claude.ai export is JSON, and `claude_json` reads it. But people extract
those conversations to readable Markdown — to read them, to keep them, to
survive the export being lossy — and once that is done the JSON is often
gone. What is left is a folder of `.md` files that hold the whole
conversation and that nothing could import.

Two shapes are in the wild and both are handled, because a long-lived
archive has both in it:

    # Chat ID: 3a3f5986-65c5-40ee-9148-984b91e15f30      <- older extractor

    **human:**

    what I said

    **assistant:**

    what came back

and:

    # Analyzing therapy records                          <- claude-to-text

    - Date: 2025-12-08
    - ID: 013fc723-47e4-41cb-8984-0f5e45763970

    ---

    **You:**

    what I said

    **Claude:**

    what came back

**The header block is metadata, not conversation, and it is dropped.**
Title, `Chat ID:`, `- Date:`, `- ID:`, the `---` rule, and any leading
blockquote note an extractor added about where the file came from —
everything above the first speaker line goes. Imported as text it would
arrive as a kin's first words, and on an archive of two hundred files that
is two hundred openings of machine scaffolding in a kin's own history. The
title is not thrown away: it becomes the one-line thread header, the same
one `claude_json` writes, so a folder of conversations doesn't arrive as
one undifferentiated wall.

**Role comes from the speaker line, never from a name you type.** Both
shapes state who is speaking on every turn, so this parser ignores
`kin_display_name` exactly as `claude_json` does. Guessing a role a file
already states is how an import ends with zero assistant turns — which is
precisely what happened when these files were handed to the plain-text
parser: a whole conversation arrived as two `user` messages and every reply
was lost, silently.

**Dates come from the file, or from its name, or not at all.** A `- Date:`
line is used if present, then a `Date:` inside a leading blockquote note,
then a `YYYY-MM-DD` prefix on the filename (which is how `claude_to_text`
names its output). Failing all three the turns are still imported, ordered,
just without a real anchor — losing a conversation because nothing stamped
it would be a poor trade.
"""

import datetime
import os
import re

from tools._io import robust_read_text

# A speaker line: bold, alone on its line, one of the four labels the two
# extractors use. Deliberately a CLOSED set rather than "any bold line
# ending in a colon" -- a conversation about **Something:** would otherwise
# split a message in half and hand the remainder to the wrong speaker.
_SPEAKER_RE = re.compile(
    r"^\s*\*\*\s*(You|Claude|human|assistant|user|Human|Assistant)\s*:?\s*\*\*\s*:?\s*$"
)

_ROLE_BY_LABEL = {
    "you": "user",
    "human": "user",
    "user": "user",
    "claude": "assistant",
    "assistant": "assistant",
}

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_DATE_LINE_RE = re.compile(r"^\s*[-*>]?\s*Date:\s*(\d{4}-\d{2}-\d{2})", re.I)
_TITLE_RE = re.compile(r"^\s*#\s+(.*\S)\s*$")

# Markers only an extractor writes. Used as the second signal that lets a
# genuine ONE-turn conversation through detection -- see detect().
_CHAT_ID_RE = re.compile(r"^\s*#*\s*Chat ID:\s*[0-9a-fA-F-]{8,}", re.I)
_ID_LINE_RE = re.compile(r"^\s*[-*>]?\s*ID:\s*[0-9a-fA-F-]{8,}\s*$", re.I)


# ─── Detection ────────────────────────────────────────────────────── #

def detect(text):
    """True when this looks like an extracted Claude conversation.

    Two speaker lines is the ordinary signal. One alone is too weak on its
    own: a piece of prose that happens to contain `**You:**` on a line by
    itself is not a conversation, and claiming it here would take the file
    away from the plain-text parser that should have had it.

    **But a conversation can genuinely be one turn long**, and requiring two
    threw one away — a real archived exchange of a single message failed to
    import at all, having fallen through to a parser that could not read it.
    So one speaker line is accepted when the file ALSO carries an
    extractor's header (`# Chat ID:` or an `- ID: <uuid>` line), which is
    not something ordinary prose has. A second signal, rather than a lower
    bar.
    """
    if not text:
        return False
    hits = 0
    for line in text.splitlines():
        if _SPEAKER_RE.match(line):
            hits += 1
            if hits >= 2:
                return True
    return hits == 1 and _has_extractor_header(text)


def _has_extractor_header(text):
    """True when the header block carries a marker only an extractor writes."""
    for line in text.splitlines()[:40]:
        if _CHAT_ID_RE.match(line) or _ID_LINE_RE.match(line):
            return True
    return False


def detect_path(source_path):
    if not source_path.lower().endswith((".md", ".markdown", ".txt")):
        return False
    try:
        return detect(robust_read_text(source_path))
    except Exception:
        return False


# ─── Parse ────────────────────────────────────────────────────────── #

def _title_and_date(lines, first_speaker_line, source_path):
    """The conversation's title and its date, read out of the header block
    above the first speaker line (and the filename as a last resort)."""
    title = ""
    date = ""
    for raw in lines[:first_speaker_line]:
        if not date:
            m = _DATE_LINE_RE.match(raw)
            if m:
                date = m.group(1)
        if not title:
            m = _TITLE_RE.match(raw)
            if m:
                candidate = m.group(1)
                # "# Chat ID: <uuid>" is an identifier, not a title.
                if not candidate.lower().startswith("chat id"):
                    title = candidate
    if not date:
        m = _DATE_RE.search(os.path.basename(source_path))
        if m:
            date = m.group(1)
    if not title:
        stem = os.path.splitext(os.path.basename(source_path))[0]
        # Strip a leading date and a trailing "(id)" from the filename.
        stem = _DATE_RE.sub("", stem, count=1).strip(" -_")
        stem = re.sub(r"\s*\([0-9a-fA-F]{6,}\)\s*$", "", stem).strip()
        title = stem or "(untitled)"
    return title, date


def parse(source_path, kin_display_name=None, **_opts):
    """Canonical messages from an extracted-to-Markdown Claude conversation.

    `kin_display_name` is accepted for signature compatibility and
    deliberately unused — every turn states its own speaker.

    Returns `(messages, source_label, fmt)`, the contract every sibling
    parser uses.
    """
    text = robust_read_text(source_path)
    lines = text.splitlines()

    first = None
    for i, line in enumerate(lines):
        if _SPEAKER_RE.match(line):
            first = i
            break
    if first is None:
        raise ValueError(
            f"{os.path.basename(source_path)} has no **speaker:** lines — "
            f"it isn't an extracted Claude conversation.")

    title, date = _title_and_date(lines, first, source_path)

    # Walk the speaker lines, collecting what falls between them.
    turns = []
    role = None
    buf = []
    for line in lines[first:]:
        m = _SPEAKER_RE.match(line)
        if m:
            if role and "\n".join(buf).strip():
                turns.append((role, "\n".join(buf).strip()))
            role = _ROLE_BY_LABEL.get(m.group(1).strip().lower())
            buf = []
            continue
        buf.append(line)
    if role and "\n".join(buf).strip():
        turns.append((role, "\n".join(buf).strip()))

    if not turns:
        raise ValueError(
            f"{os.path.basename(source_path)} has speaker lines but nothing "
            f"under them — no conversation to import.")

    try:
        baseline = datetime.datetime.strptime(date, "%Y-%m-%d") if date else None
    except ValueError:
        baseline = None

    out = []
    if title:
        out.append({
            "role": "system",
            "content": f"[hearthkin: imported Claude conversation — {title}]",
            "ts": (baseline.isoformat() if baseline else ""),
        })
    for n, (r, body) in enumerate(turns):
        ts = ""
        if baseline is not None:
            ts = (baseline + datetime.timedelta(seconds=30 * (n + 1))
                  ).replace(microsecond=0).isoformat()
        out.append({"role": r, "content": body, "ts": ts})
    return out, "claude_ai", "claude_markdown"
