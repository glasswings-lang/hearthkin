# SPDX-License-Identifier: CC0-1.0

"""
Text-log parser. Handles two shapes through one entry point:

  1. Telegram .txt exports — bracket timestamps, e.g.
       [15-01-2024 10:00:00] SpeakerFive: The parcel arrived this morning.
     with continuation lines (no bracket prefix) folded into the
     previous message.

  2. Hand-authored — date headers + Name: lines, e.g.
       # 2024-01-15
       SpeakerFive: The parcel arrived this morning.
       SpeakerFifteen: Good — was anything broken?
     with the same continuation-line rule.

Detection is automatic via `_detect_format`. The caller passes a
`kin_display_name` (e.g. "SpeakerFive") so the parser knows which speaker
to route to role=assistant; everyone else becomes role=user.

For group exports (more than two distinct non-kin speakers in the
file), non-kin lines still become role=user, but the speaker name
is preserved in the message's `speaker` field and copied into
`sender_attribution` in the inline-bracket shape Hearthkin uses
elsewhere (`[Display Name]`, no colon — see CLAUDE.md "Telegram
group attribution"). That's enough for the kin to tell speakers
apart on re-read without us needing real Telegram user IDs (which
the plain .txt format doesn't carry).
"""

import datetime
import re

from tools._io import robust_read_text


# ─── Detection ────────────────────────────────────────────────────── #

_TELEGRAM_PREFIX = re.compile(
    r"^\[(\d{2})-(\d{2})-(\d{4}) (\d{2}):(\d{2}):(\d{2})\] ([^:]+?): "
)
_DATE_HEADER = re.compile(
    r"^#\s+(\d{4})-(\d{2})-(\d{2})(?:\s+(.+))?\s*$"
)
_HAND_AUTHORED_SPEAKER = re.compile(
    # Speaker is a single-token name — no spaces allowed. Spaces in
    # the name class let ordinary body text get misparsed as a speaker
    # turn: any multi-word label followed by a colon ("Shipping Address
    # Line 1:", a pasted command with a drive letter, a form-fill
    # template) matches a spaces-allowed pattern, and long pasted
    # messages contain those constantly.
    # Word chars + dot + apostrophe + hyphen cover the speaker-name
    # shapes that do occur — SpeakerFifteen, Corvid.07, O'Reilly,
    # Smith-Jones.
    r"^([A-Za-z][\w.'\-]{0,40}?):\s+(.*)$"
)


def _detect_format(text):
    """Return one of: 'telegram', 'hand_authored', 'plain'."""
    sample_lines = [ln for ln in text.splitlines()[:50] if ln.strip()]
    if not sample_lines:
        return "plain"
    # Any line in the first ~50 with the Telegram bracket prefix and
    # the file's first content line matching → Telegram.
    if _TELEGRAM_PREFIX.match(sample_lines[0]):
        return "telegram"
    # Any date header in the first ~50 lines → hand_authored.
    for ln in sample_lines:
        if _DATE_HEADER.match(ln):
            return "hand_authored"
    # No date header, but Name: lines present → hand_authored without dates.
    for ln in sample_lines:
        if _HAND_AUTHORED_SPEAKER.match(ln):
            return "hand_authored"
    return "plain"


# ─── Time-hint parsing for hand-authored date headers ─────────────── #

_TIME_HINT_HHMM_24 = re.compile(r"^(\d{1,2}):(\d{2})$")
_TIME_HINT_HHMM_AMPM = re.compile(r"^(\d{1,2}):(\d{2})\s*([ap]m?\.?)$")
_TIME_HINT_WORDS = {
    "dawn": 6, "morning": 9, "midmorning": 10, "noon": 12,
    "midday": 12, "afternoon": 14, "evening": 19, "night": 21,
    "midnight": 0, "late": 23,
}


def _ampm_to_24h(hour_12, suffix):
    """Convert a 12-hour clock hour to 24-hour given an 'am'/'pm'
    suffix (already lowercased). 12am → 0, 12pm → 12."""
    is_pm = suffix.startswith("p")
    if hour_12 == 12:
        return 12 if is_pm else 0
    return hour_12 + 12 if is_pm else hour_12


def _time_hint_hour(hint):
    """Parse a time hint string ('morning', '14:30', '5:00 PM', 'late
    evening'). Returns (hour, minute) or None. Accepts both 24-hour
    HH:MM and 12-hour HH:MM AM/PM (Signal-style copy-paste uses 12-
    hour with AM/PM; rather than asking the operator to convert by
    hand during a manual annotation pass, we accept both shapes)."""
    if not hint:
        return None
    s = hint.strip().lower()
    # 12-hour with am/pm
    m = _TIME_HINT_HHMM_AMPM.match(s)
    if m:
        h12 = int(m.group(1))
        mn = int(m.group(2))
        suf = m.group(3)
        if 1 <= h12 <= 12 and 0 <= mn <= 59:
            return (_ampm_to_24h(h12, suf), mn)
    # 24-hour HH:MM
    m = _TIME_HINT_HHMM_24.match(s)
    if m:
        h = int(m.group(1))
        mn = int(m.group(2))
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return (h, mn)
    # Word-shaped time hint (morning, evening, etc.)
    for word in s.replace(",", " ").split():
        if word in _TIME_HINT_WORDS:
            return (_TIME_HINT_WORDS[word], 0)
    return None


# Optional per-message timestamp prefix. Operator writes
# "5:00 PM SpeakerFifteen: text" or "14:30 SpeakerFifteen: text" before the speaker
# name; if present, that's the anchor for this message. Following
# messages without an explicit prefix stride 30s from this anchor
# (same rule as the date-header baseline).
_MSG_TIMESTAMP_PREFIX = re.compile(
    r"^(\d{1,2}:\d{2}(?:\s*[apAP][mM]?\.?)?)\s+"
    r"([A-Za-z][\w.'\-]{0,40}?):\s+(.*)$"
)


# ─── Public entry point ───────────────────────────────────────────── #

def parse(source_path, kin_display_name):
    """Parse a text log file and return a list of canonical message
    dicts. The caller is responsible for passing those to
    `importers.write_imported_history`.

    `kin_display_name` is the name as it appears in the file
    (case-sensitive). All non-matching speakers become role=user.

    Raises ValueError on an empty file."""
    raw = robust_read_text(source_path)
    fmt = _detect_format(raw)

    if fmt == "telegram":
        msgs = _parse_telegram(raw, kin_display_name)
        source_label = "telegram_dm"
    elif fmt == "hand_authored":
        msgs = _parse_hand_authored(raw, kin_display_name)
        source_label = "hand_authored"
    else:
        msgs = _parse_plain(raw, kin_display_name)
        source_label = "plain_text"

    if not msgs:
        raise ValueError(
            f"No messages parsed from {source_path}. "
            f"Detected format: {fmt}."
        )
    return msgs, source_label, fmt


# ─── Telegram parser ──────────────────────────────────────────────── #

def _parse_telegram(text, kin_display_name):
    msgs = []
    current = None
    for line in text.splitlines():
        m = _TELEGRAM_PREFIX.match(line)
        if m:
            # Flush previous.
            if current is not None:
                msgs.append(_finalize(current))
            mo, da, yr, hh, mm, ss, speaker = m.groups()
            ts = _safe_iso(int(yr), int(mo), int(da),
                           int(hh), int(mm), int(ss))
            speaker = speaker.strip()
            # Strip the matched prefix to get the rest of the line.
            rest = line[m.end():]
            current = {
                "speaker": speaker,
                "ts": ts,
                "lines": [rest],
            }
        else:
            if current is None:
                # Pre-amble before first timestamped line — skip.
                continue
            current["lines"].append(line)
    if current is not None:
        msgs.append(_finalize(current))

    return [_to_canonical(m, kin_display_name, "telegram_dm")
            for m in msgs]


# ─── Hand-authored parser ─────────────────────────────────────────── #

def _parse_hand_authored(text, kin_display_name):
    """Walks lines. Date headers set the baseline; `Name:` lines start
    messages; lines with `HH:MM [AM/PM] Name:` set an explicit message
    timestamp anchor; blank lines end the current message; non-blank,
    non-header, non-`Name:` lines continue the current message.

    Three timestamp sources, in priority order:
      1. Explicit per-message prefix (`5:00 PM SpeakerFifteen: ...`) — strongest
         anchor. Resets the stride counter for following messages.
      2. Date-header time hint (`# 2024-01-15 5:00 PM` or `# ... morning`)
         — sets a baseline that following messages stride from.
      3. Default: header date at 09:00 if no hint given; 30-second
         stride between messages within a header block.

    Per-message timestamps are how Signal-style copy-paste (where each
    message-burst carries a real `5:00 PM` or `8:00 AM` from the source
    UI) survives into conversation.jsonl with real timing rather than
    being flattened to synthetic 30s strides."""
    msgs = []
    current = None
    # Default baseline if the file opens without a date header.
    baseline = _today_at(9, 0)
    msg_index_since_anchor = 0

    def flush():
        nonlocal current
        if current is not None and current.get("lines"):
            msgs.append(_finalize(current))
        current = None

    for line in text.splitlines():
        stripped = line.rstrip()
        dh = _DATE_HEADER.match(stripped)
        if dh:
            flush()
            yr, mo, da, hint = dh.groups()
            hh, mm = (9, 0)
            tm = _time_hint_hour(hint or "")
            if tm:
                hh, mm = tm
            baseline = _safe_dt(int(yr), int(mo), int(da), hh, mm, 0)
            msg_index_since_anchor = 0
            continue
        if not stripped.strip():
            flush()
            continue
        # Per-message timestamp prefix (strongest anchor) — try first.
        ts_match = _MSG_TIMESTAMP_PREFIX.match(stripped)
        if ts_match:
            flush()
            ts_str, speaker, rest = ts_match.groups()
            tm = _time_hint_hour(ts_str)
            if tm:
                hh, mm = tm
                # Preserve the current date from the baseline.
                anchor = baseline.replace(hour=hh, minute=mm,
                                          second=0, microsecond=0)
                baseline = anchor
                msg_index_since_anchor = 0
            else:
                # Malformed timestamp — fall back to stride from
                # current baseline rather than dropping the message.
                anchor = baseline + datetime.timedelta(
                    seconds=30 * msg_index_since_anchor
                )
            current = {
                "speaker": speaker.strip(),
                "ts": anchor.replace(microsecond=0).isoformat(),
                "lines": [rest],
            }
            msg_index_since_anchor += 1
            continue
        sp = _HAND_AUTHORED_SPEAKER.match(stripped)
        if sp:
            flush()
            speaker, rest = sp.groups()
            # Stride 30 seconds from the most recent anchor.
            ts_dt = baseline + datetime.timedelta(
                seconds=30 * msg_index_since_anchor
            )
            current = {
                "speaker": speaker.strip(),
                "ts": ts_dt.replace(microsecond=0).isoformat(),
                "lines": [rest],
            }
            msg_index_since_anchor += 1
        else:
            if current is None:
                # Stray line before any speaker — skip.
                continue
            current["lines"].append(stripped)
    flush()

    return [_to_canonical(m, kin_display_name, "hand_authored")
            for m in msgs]


# ─── Plain-sequential parser (fallback) ───────────────────────────── #

def _parse_plain(text, kin_display_name):
    """No date headers, no Telegram bracket timestamps — treat each
    Name: line as a message, anchor timestamps relative to now-minus-N
    so the imported block lands ordered just before "now."""
    msgs = []
    current = None
    for line in text.splitlines():
        stripped = line.rstrip()
        if not stripped.strip():
            if current is not None and current.get("lines"):
                msgs.append(_finalize(current))
                current = None
            continue
        sp = _HAND_AUTHORED_SPEAKER.match(stripped)
        if sp:
            if current is not None and current.get("lines"):
                msgs.append(_finalize(current))
            speaker, rest = sp.groups()
            current = {
                "speaker": speaker.strip(),
                "ts": None,  # filled in below
                "lines": [rest],
            }
        else:
            if current is None:
                continue
            current["lines"].append(stripped)
    if current is not None and current.get("lines"):
        msgs.append(_finalize(current))

    # Backfill timestamps: start at now - 30*N seconds and stride
    # forward, so messages land in order and end at "now."
    now = datetime.datetime.now().replace(microsecond=0)
    n = len(msgs)
    for i, m in enumerate(msgs):
        m["ts"] = (now - datetime.timedelta(seconds=30 * (n - i))).isoformat()

    return [_to_canonical(m, kin_display_name, "plain_text")
            for m in msgs]


# ─── Shared helpers ───────────────────────────────────────────────── #

def _finalize(raw):
    """Convert the working dict (speaker, ts, lines) into a flat
    {speaker, ts, content} dict by joining the line list."""
    content = "\n".join(raw["lines"]).strip()
    return {
        "speaker": raw["speaker"],
        "ts": raw["ts"],
        "content": content,
    }


def _to_canonical(msg, kin_display_name, source_label):
    """Convert a parsed message into Hearthkin's on-disk shape."""
    speaker = msg["speaker"]
    role = "assistant" if speaker == kin_display_name else "user"
    out = {
        "role": role,
        "content": msg["content"],
        "ts": msg["ts"],
        "speaker": speaker,
        "source": f"import:{source_label}",
    }
    # For non-kin speakers, also record inline attribution — lets a
    # re-read tell speakers apart on multi-party imports without
    # requiring real user IDs. Stored BARE, the same as live Telegram
    # capture stores it: the reading surface adds the bracket
    # (chat_helpers.speaker_attribution_prefix). Writing "[SpeakerOne]" here
    # got it wrapped a second time on the way to the model.
    if role == "user":
        out["sender_attribution"] = speaker
    return out


def _safe_iso(year, month, day, hour, minute, second):
    """Best-effort ISO timestamp. Returns None on invalid date."""
    try:
        return datetime.datetime(
            year, month, day, hour, minute, second
        ).isoformat()
    except (ValueError, TypeError):
        return None


def _safe_dt(year, month, day, hour, minute, second):
    """Datetime for safe_iso's callers that need to do arithmetic.
    Falls back to today-at-noon if the date is invalid (rare; only
    if the operator wrote an impossible date like Feb 30)."""
    try:
        return datetime.datetime(year, month, day, hour, minute, second)
    except (ValueError, TypeError):
        return _today_at(12, 0)


def _today_at(hour, minute):
    now = datetime.datetime.now()
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
