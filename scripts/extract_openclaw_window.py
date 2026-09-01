# SPDX-License-Identifier: CC0-1.0
"""Pull a slice of an OpenClaw history out as a clean, importable transcript.

    python scripts/extract_openclaw_window.py <sessions-folder> <KinName> \
        [--before-telegram] [--before ISO] [--after ISO] [-o out.txt]

READ-ONLY on the source. Writes one text file.

Why this exists: an OpenClaw session stream is mostly machinery. Every
turn a person sent through a channel is wrapped in an injected metadata
preamble, a bracketed local-time stamp, sometimes a `System:` prefix;
tool results come back through the *user* role; cron and heartbeat runs
look like conversation. Reading it raw, the actual talking is maybe a
tenth of the bytes.

`importers/openclaw.py` already knows how to strip all of that — it has
to, to import at all — so this borrows it rather than guessing again.
What this adds is the time window and a human-readable output, for the
case where you want a specific stretch (say, everything before a kin
moved onto Telegram) as something you can read, keep, or re-import.

`--before-telegram` finds the first turn that carries Telegram sender
metadata and cuts there. That boundary is worth having as a flag: it is
not the same as the start of the file, and eyeballing it is how you get
it wrong. OpenClaw records the channel in each turn's metadata `label`
(e.g. `openclaw-control-ui`), NOT as a separate field, so a scan that
treats "has metadata" as "came from Telegram" reports the first day of a
kin's life as Telegram traffic. It did.

Output is the `[DD-MM-YYYY HH:MM:SS] Speaker: text` shape that
importers/text_log already reads, so the result drops straight into
File → Import history. Multi-line messages are preserved; a body line
that would itself look like a new speaker line is indented one space so
it can't be misread as one.
"""

import argparse
import datetime
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from importers import openclaw  # noqa: E402

# The metadata block lives INSIDE the message content, so in the raw JSONL
# line its quotes are escaped (\"username\"). Matching the raw line with a
# normal-quoted pattern finds nothing and reports a clean "no Telegram here"
# — the exact false-clear this whole file exists to avoid. Parse the event
# and read the decoded text instead.
_META_BLOCK = re.compile(
    r"(?:Conversation info|Sender)\s*\(untrusted metadata\):\s*```(?:json)?\s*(.*?)```",
    re.DOTALL)
_TG_KEYS = ("username", "message_id", "sender_id")
_SPEAKER_LINE = re.compile(r"^\[\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2}\] [^:]+: ")


def _ts_key(msg):
    ts = (msg.get("ts") or "").strip()
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _first_telegram_ts(folder):
    """Timestamp of the first turn carrying Telegram sender metadata.

    Read from the RAW stream rather than the parsed messages, because
    parsing is what removes the metadata this looks for.
    """
    import json
    best = None
    for path in openclaw._session_files(folder):
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh as f:
            for line in f:
                line = line.strip()
                if not line or "untrusted metadata" not in line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                m = ev.get("message") or {}
                if not isinstance(m, dict) or m.get("role") != "user":
                    continue
                c = m.get("content")
                text = (" ".join(b.get("text", "") for b in c
                                 if isinstance(b, dict))
                        if isinstance(c, list)
                        else (c if isinstance(c, str) else ""))
                is_tg = False
                for blob in _META_BLOCK.findall(text):
                    try:
                        d = json.loads(blob)
                    except Exception:
                        continue
                    if isinstance(d, dict) and any(k in d for k in _TG_KEYS):
                        is_tg = True
                        break
                if not is_tg:
                    continue
                when = _event_utc(ev, m)
                if when and (best is None or when < best):
                    best = when
    return best


def _utc_offset():
    """Seconds to add to a UTC stamp to get local wall-clock time.

    The transcript is meant to be read, and the messages themselves carry
    local stamps in their own text ("[Sat 2026-03-21 16:05 UTC]"). A file
    whose header says 00:05 next day while the sentence under it says
    16:05 invites exactly the kind of "which of these is wrong" that this
    whole afternoon has been made of. The CUT stays in UTC, where the
    comparison is correct; only the printed stamps move.
    """
    now = datetime.datetime.now()
    utc = datetime.datetime.utcfromtimestamp(
        datetime.datetime.now().timestamp())
    return round((now - utc).total_seconds())


def _event_utc(ev, m):
    """UTC datetime for one event.

    UTC, not local — importers/openclaw normalises to the UTC ISO stamp,
    and mixing the two silently shifts the window by the local offset. On
    the machine this was written for that is eight hours, which is enough
    to put a whole first evening on the wrong side of the cut.
    """
    raw = m.get("timestamp")
    if raw is None:
        raw = ev.get("timestamp")
    if isinstance(raw, (int, float)):
        t = raw / 1000.0 if raw > 1e11 else float(raw)
        return datetime.datetime.utcfromtimestamp(t)
    if isinstance(raw, str):
        try:
            return datetime.datetime.fromisoformat(
                raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return None
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("kin")
    ap.add_argument("--before-telegram", action="store_true",
                    help="cut at the first turn carrying Telegram metadata")
    ap.add_argument("--before", help="ISO datetime; keep turns strictly before")
    ap.add_argument("--after", help="ISO datetime; keep turns at or after")
    ap.add_argument("-o", "--out", help="output file (default: alongside cwd)")
    ap.add_argument("--user-name", default=None,
                    help="name for turns OpenClaw left unattributed "
                         "(default: keep 'User')")
    ap.add_argument("--utc", action="store_true",
                    help="write UTC stamps instead of local time")
    args = ap.parse_args(argv)

    folder = args.folder
    if not os.path.isdir(folder):
        folder = os.path.dirname(folder)

    cut = None
    if args.before_telegram:
        cut = _first_telegram_ts(folder)
        if cut is None:
            print("No Telegram-tagged turn found — keeping everything.")
        else:
            print(f"First Telegram turn: {cut:%Y-%m-%d %H:%M}")
    if args.before:
        b = datetime.datetime.fromisoformat(args.before)
        cut = min(cut, b) if cut else b
    after = datetime.datetime.fromisoformat(args.after) if args.after else None

    msgs, source_label, _fmt = openclaw.parse(folder, args.kin)
    print(f"{len(msgs):,} messages survived OpenClaw's noise "
          f"(source: {source_label})")

    kept = []
    undated = 0
    for m in msgs:
        t = _ts_key(m)
        if t is None:
            undated += 1
            continue
        if cut and t >= cut:
            continue
        if after and t < after:
            continue
        kept.append((t, m))
    kept.sort(key=lambda p: p[0])

    if undated:
        # Say so rather than silently dropping them — a window that
        # quietly loses turns is worse than one that reports its edges.
        print(f"{undated:,} messages had no usable timestamp and were left out.")
    if not kept:
        print("Nothing in that window.")
        return 1

    shift = datetime.timedelta(seconds=0 if args.utc else _utc_offset())
    lines = []
    for t, m in kept:
        t = t + shift
        who = (m.get("speaker") or "").strip() or (
            args.kin if m.get("role") == "assistant" else "User")
        if args.user_name and m.get("role") != "assistant" and who == "User":
            who = args.user_name
        body = (m.get("content") or "").rstrip()
        parts = body.split("\n")
        head = parts[0] if parts else ""
        lines.append(f"[{t:%d-%m-%Y %H:%M:%S}] {who}: {head}")
        for extra in parts[1:]:
            # Guard: a body line shaped like a speaker line would start a
            # phantom message on re-import.
            lines.append(" " + extra if _SPEAKER_LINE.match(extra) else extra)

    out = args.out or f"{args.kin}_pre-telegram.txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    first, last = kept[0][0] + shift, kept[-1][0] + shift
    speakers = {}
    for _t, m in kept:
        nm = (m.get("speaker") or "?").strip() or "?"
        if args.user_name and m.get("role") != "assistant" and nm == "User":
            nm = args.user_name
        speakers[nm] = speakers.get(nm, 0) + 1
    print(f"\nWrote {len(kept):,} messages to {out}")
    print(f"  span: {first:%Y-%m-%d %H:%M} -> {last:%Y-%m-%d %H:%M}")
    for name, n in sorted(speakers.items(), key=lambda p: -p[1]):
        print(f"  {name}: {n:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
