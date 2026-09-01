# SPDX-License-Identifier: CC0-1.0

"""
importers — bring foreign chat history into a kin's conversation.jsonl.

One package, N parsers (one file per source format), one canonical
writer. Each parser reads a source file and returns a list of plain
dicts shaped like Hearthkin's on-disk conversation.jsonl entries.
The canonical writer takes that list plus a target kin name and
appends it to the kin's conversation, bracketed by system markers
that tell the kin what's being read.

Public entry points:

    from importers import write_imported_history, parse_history
    from importers.text_log import parse as parse_text_log
    from importers.kindroid import parse as parse_kindroid

`parse_history(path, kin_display_name)` is the auto-dispatcher: it
runs each parser's `detect` function against the file content and
hands the parse off to the first match, falling back to text_log
(which has its own three-way internal detection for Telegram /
hand-authored / plain-sequential shapes). New parsers added under
this package only need a `parse(path, kin_name)` and a `detect(text)`
function; register in the dispatcher's tuple below.

Design doc: docs/design/history-import.md.
"""

import os

from ._canonical import (  # noqa: F401
    ImportError,
    restore_history,
    write_imported_history,
)
from tools._io import robust_read_text

from . import (claude_json, claude_markdown, hearthkin_jsonl, kindroid,
               openclaw, skype_json, skype_txt, text_log)


# Dispatch order: most specific format first, text_log last (it owns
# Telegram + hand-authored + plain-sequential and has its own internal
# detection — anything that doesn't trigger an earlier parser's
# detector goes here).
#
# Each entry is (detector, parser). The detector takes the file's
# raw text and returns True if its parser should handle this file.
# The parser takes (source_path, kin_display_name, **opts) and returns
# (messages, source_label, fmt). `opts` carries format-specific extras
# like `conversation_id` for Skype JSON (one file = many conversations).
_PARSERS = (
    (openclaw.detect, openclaw.parse),
    # Before skype_json: both are .json and both hold many
    # conversations, so the more specific sniff has to go first.
    (claude_json.detect, claude_json.parse),
    (skype_json.detect, skype_json.parse),
    (skype_txt.detect, skype_txt.parse),
    # Before kindroid and text_log: an extracted Claude conversation is a
    # .md whose speakers are bold lines, and neither of those parsers can
    # see that. Handed to text_log it did not fail -- it merged a whole
    # conversation into two `user` turns and dropped every reply, which is
    # the silent kind of wrong.
    (claude_markdown.detect, claude_markdown.parse),
    (kindroid.detect, kindroid.parse),
)


def parse_history(source_path, kin_display_name, **opts):
    """Auto-detect format and dispatch to the right parser. Returns
    `(canonical_messages, source_label, fmt)` matching the contract
    that text_log.parse and every sibling parser uses.

    `opts` is forwarded to the chosen parser. Used for format-specific
    selection like `conversation_id` (Skype JSON) where one source
    file holds many conversations and the dialog picks one."""
    # A directory is only ever an OpenClaw session folder (its whole-life
    # reconstruction reads the folder, not a single file). Nothing else
    # accepts a directory, so dispatch straight there.
    if os.path.isdir(source_path):
        return openclaw.parse(source_path, kin_display_name, **opts)
    # For .tar (Skype's official bundle), detection happens on the
    # extracted messages.json — skip the raw-text read and just hand
    # off to skype_json directly. Same for .json files (Skype export or
    # an OpenClaw sessions.json index sitting beside the session files).
    lower = source_path.lower()
    if lower.endswith(".tar") or lower.endswith(".json"):
        if skype_json.detect_path(source_path):
            return skype_json.parse(source_path, kin_display_name, **opts)
        if openclaw.detect_path(source_path):
            return openclaw.parse(source_path, kin_display_name, **opts)
    raw = robust_read_text(source_path)
    for detector, parser in _PARSERS:
        try:
            if detector(raw):
                return parser(source_path, kin_display_name, **opts)
        except Exception:
            # A parser-specific detector should never throw on plain
            # text input, but if one does we want fall-through to the
            # next parser rather than a hard failure of the whole
            # import flow.
            continue
    # Our own shape reaches here (no foreign detector claims it). Refuse
    # rather than fall through to text_log: importing a kin's own
    # conversation would bracket its turns in "history you may not
    # remember writing" markers and overwrite every `source` with
    # `import:<label>` — relabelling the kin's own past as borrowed.
    # That's what restore_from_file is for.
    if hearthkin_jsonl.detect(raw):
        raise ImportError(
            "This is a Hearthkin conversation.jsonl — a kin's own turns, not "
            "foreign history. Importing it would relabel them as carried-in "
            "seed history and overwrite where each turn came from. Use "
            "restore_from_file() to bring it back as the kin's own."
        )
    return text_log.parse(source_path, kin_display_name)


def restore_from_file(source_path, kin_name, *, mode="merge",
                      create_kin_if_missing=False):
    """Read a Hearthkin conversation.jsonl and write it back into `kin_name`
    as that kin's own turns — no markers, no relabelling, every row keeping
    the `source` it was written with.

    For archived kin folders, rescued backups, and conversations cleaned up
    outside the app. Foreign history goes through `parse_history` +
    `write_imported_history` instead; the two paths are separate on purpose
    (see importers/hearthkin_jsonl.py)."""
    rows = hearthkin_jsonl.parse(source_path)
    if not rows:
        raise ImportError(f"No readable turns in {source_path!r}.")
    return restore_history(kin_name, rows, mode=mode,
                           create_kin_if_missing=create_kin_if_missing)


def restore_from_files(paths, kin_name, *, mode="merge", report=None,
                       create_kin_if_missing=False):
    """Restore several archived conversation files into one kin, in a
    single pass.

    Same contract as `restore_from_file`, which it replaces for the
    multi-file case: no markers, no relabelling, every row keeping the
    `source` it was written with.

    ORDERING. Files are played oldest-archive-first — sorted by each
    file's own earliest timestamp, not by filename, because an archive
    called `conversation (3).jsonl` says nothing about when it happened.
    Within a file, rows keep their recorded order. In the default merge
    mode this barely matters, since `restore_rows` then weaves everything
    against the kin's existing turns by timestamp; it decides the result
    only for "add it to the end", where the files land in that order.

    DEDUPLICATION IS THE REASON THIS IS ONE CALL rather than a loop over
    `restore_from_file`. `restore_rows` builds its seen-set from the
    kin's existing turns and then extends it as it walks the incoming
    rows, so handing it every file at once dedupes ACROSS the files as
    well as against what's already there. Restoring them one at a time
    would still catch overlap with the kin, but two archives that overlap
    EACH OTHER would both land — and overlapping archives is the normal
    case, since that is what having several backups means.

    `report`, if given, is a list that receives `(path, problem)` for
    every file that couldn't be read or held nothing. Collected rather
    than raised: one unreadable file out of twenty shouldn't cost you the
    other nineteen, and a silent skip is how an archive quietly arrives
    incomplete.
    """
    if isinstance(paths, str):
        paths = [paths]
    batches = []
    for path in paths:
        try:
            rows = hearthkin_jsonl.parse(path)
        except Exception as e:  # noqa: BLE001
            if report is not None:
                report.append((path, str(e)))
            continue
        if not rows:
            if report is not None:
                report.append((path, "no readable turns"))
            continue
        stamps = [r.get("ts") for r in rows if r.get("ts")]
        batches.append((min(stamps) if stamps else "", rows))
    if not batches:
        raise ImportError("No readable turns in any of those files.")
    batches.sort(key=lambda b: b[0])
    merged = []
    for _first_ts, rows in batches:
        merged.extend(rows)
    return restore_history(kin_name, merged, mode=mode,
                           create_kin_if_missing=create_kin_if_missing)


def parse_many(paths, kin_display_name, *, weave=False, report=None, **opts):
    """Parse several sources and combine them into one canonical stream.

    Returns the same `(messages, source_label, fmt)` triple as
    `parse_history`, so everything downstream — preview, dedup, merge-by-date,
    the import markers — works unchanged.

    Written because importing an archive one file at a time does not scale: a
    Skype export is 50 threads, and the same shape turns up for every other
    dead platform someone wants to carry in. Doing it fifty times by hand is
    not a workflow, it is an endurance test.

    TWO ORDERINGS, and the choice is not cosmetic:

    `weave=False` (default) keeps each conversation whole and plays them in
    order of when each began. Every exchange stays adjacent to its own reply,
    which is what you want if the point is how someone actually converses —
    a prompt and its answer next to each other.

    `weave=True` interleaves everything by timestamp into a single chronology,
    so a year reads as a year across all the people in it. Right when the
    point is a life rather than a relationship, and wrong when it would
    scatter a conversation across a dozen unrelated turns.

    Rows carry their own `source`, so a mixed batch stays honest about which
    row came from where.

    `report`, if given, is a list that receives `(path, problem)` for every
    source that failed or held nothing. Failures are collected rather than
    raised, because one unreadable file out of fifty should not cost you the
    other forty-nine — but they are handed back rather than swallowed, so the
    dialog can say so.
    """
    if report is None:
        report = []
    parsed = []
    for path in paths:
        try:
            msgs, label, fmt = parse_history(path, kin_display_name, **opts)
        except Exception as e:
            report.append((path, f"{type(e).__name__}: {e}"))
            continue
        msgs = [m for m in (msgs or []) if m]
        if not msgs:
            report.append((path, "no readable turns"))
            continue
        parsed.append({"path": path, "msgs": msgs, "fmt": fmt,
                       "first_ts": _first_ts(msgs)})
    if not parsed:
        raise ValueError(
            "None of the selected files held any readable history."
            + (f" ({len(report)} skipped)" if report else "")
        )

    parsed.sort(key=lambda p: (p["first_ts"] or "", p["path"]))
    combined = []
    for idx, p in enumerate(parsed):
        # Carry the last seen timestamp forward so a row without one sorts
        # beside its neighbours instead of leaping to the front of the whole
        # archive. `idx` and position keep the order stable and reproducible.
        last = p["first_ts"] or ""
        for pos, m in enumerate(p["msgs"]):
            ts = m.get("ts") or last
            last = ts or last
            combined.append(((ts or ""), idx, pos, m))

    if weave:
        combined.sort(key=lambda t: (t[0], t[1], t[2]))
    # weave=False needs no sort: files are already in first-message order and
    # each file's rows are already in their own order.

    messages = [t[3] for t in combined]
    fmts = {p["fmt"] for p in parsed}
    fmt = fmts.pop() if len(fmts) == 1 else "mixed"
    n = len(parsed)
    label = (f"{n} files" if n > 1
             else os.path.basename(parsed[0]["path"]))
    return messages, label, fmt


def _first_ts(msgs):
    for m in msgs:
        ts = m.get("ts")
        if ts:
            return ts
    return None
