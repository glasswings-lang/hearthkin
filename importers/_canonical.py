# SPDX-License-Identifier: CC0-1.0

"""
Canonical writer for imported history.

Takes a list of canonical message dicts plus a target kin name,
optionally creates the kin, and appends the imported block to the
kin's conversation.jsonl bracketed by system markers.

The actual disk-write path is `kin_persistence.append_agent_conversation_turn`
(lock-safe, constant-cost-per-turn, journals through the same
save_failures.log path the live chat writers use). We don't reach
into the file directly here — the existing append helper handles
locking, atomic-line semantics, and per-message validation through
`_clean_chat_message`.

Four write modes:

  * "append" (default) — imported block lands at end of existing
    conversation. If a conversation already exists, the leading
    marker makes it clear where the imported content begins.

  * "merge" — imported turns are woven into the existing conversation
    *by timestamp*, so history carried in from another platform lands
    where it belongs chronologically (older history threads in ahead of
    what's already here) rather than getting bolted onto the end. The
    existing conversation is backed up first, then rewritten. Crucially,
    the existing turns keep their exact stored order — only the imported
    turns are placed by time — because on-disk turns have inconsistent
    timestamps (many lack one; some disagree with stored order), so a
    blanket time-sort would scramble the live conversation. See
    _weave_by_timestamp. This is the mode for un-scattering a kin whose
    life spilled across platforms.

  * "replace" — existing conversation.jsonl is backed up to
    <kin dir>/backups/conversation-<timestamp>.jsonl and the imported
    block becomes the new sole content.

  * "create_only" — fails with FileExistsError if the target kin
    already exists. For when the operator explicitly wants to create
    a new kin from this import and not accidentally clobber.

Returns a dict with counts and the kin folder path so the dialog
can surface a one-line summary.
"""

import datetime
import shutil

from kin_persistence import (
    agent_dir,
    append_agent_conversation_turn,
    create_agent,
    load_agent_conversation,
    save_agent_conversation,
    sanitize_for_prompt_literal,
    _clean_chat_message,
    _conversation_jsonl_path,
)

from ._marker import leading_marker, trailing_marker


class ImportError(Exception):
    """Raised when an import can't proceed (target exists in create_only
    mode, malformed source, etc.). The dialog catches and surfaces."""


def _backup_conversation(kin_name):
    """Copy a kin's conversation aside before a rewrite. Returns the backup
    path, or None when there was nothing to back up.

    Backups go in `<kin dir>/backups/`, NOT alongside conversation.jsonl.

    They used to land beside it as conversation.jsonl.bak.<stamp>, which
    put every undo copy in the same folder — and therefore the same file
    picker — as the real conversation. A restore duly offered one as a
    source file. Restoring a backup of the very file you're restoring into
    re-adds every turn that can't be matched (see _restore_key: anything
    without a timestamp is never treated as a duplicate), so it silently
    doubles a chunk of the kin's history while reporting a healthy-looking
    number of turns "coming back".

    A subfolder keeps the undo history without leaving it underfoot."""
    path = _conversation_jsonl_path(kin_name)
    if not path.exists() or path.stat().st_size == 0:
        return None
    backups = agent_dir(kin_name) / "backups"
    backups.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backups / f"conversation-{stamp}.jsonl"
    shutil.copy2(path, dest)
    return dest


def write_imported_history(
    kin_name,
    messages,
    *,
    source_label,
    source_description,
    mode="append",
    create_kin_if_missing=True,
    blank_soul_for_new_kin=True,
):
    """Write a list of canonical message dicts into the kin's
    conversation.jsonl, bracketed by import markers.

    `messages` is a list of dicts shaped like conversation.jsonl rows:
    {"role": "user"|"assistant", "content": str, "ts": iso, ...}.
    Each is validated through `_clean_chat_message` before write; any
    that fail validation are dropped from the count (no exception —
    the rest of the block still lands).

    `source_label` is a short tag used in the marker's source field
    ('telegram_dm', 'telegram_group', 'hand_authored', etc.).
    `source_description` is a sentence-fragment the kin reads in the
    leading marker ('a Telegram DM', 'a hand-authored seed history').

    Returns a dict:
      {
        "kin": <name>,
        "kin_dir": <path>,
        "written": <int — non-marker turns successfully appended>,
        "dropped": <int — messages rejected by _clean_chat_message>,
        "first_ts": <str or None>,
        "last_ts": <str or None>,
        "mode": <"append" | "replace" | "create_only">,
        "created_kin": <bool — True if we created the kin folder here>,
        "backup_path": <str or None — set when mode=replace>,
      }
    """
    if not messages:
        raise ImportError("No messages to import.")

    # Speaker names come from third-party export files (Skype, Kindroid,
    # arbitrary text logs) — sanitize them centrally before they land in
    # the `speaker` / `sender_attribution` fields, both of which get
    # embedded into prompts as framework-controlled literals (the
    # inline-attribution bracket, the import marker's operator name). A
    # speaker name with embedded newlines / control chars would otherwise
    # carry structural injection into the prompt framing. Doing it here
    # covers every importer in one place.
    messages = [_sanitize_speaker_fields(m) for m in messages]

    d = agent_dir(kin_name)
    created_kin = False
    if not d.exists():
        if not create_kin_if_missing:
            raise ImportError(
                f"Kin {kin_name!r} does not exist and create_kin_if_missing=False."
            )
        ok = create_agent(kin_name, blank_soul=blank_soul_for_new_kin)
        if not ok:
            # create_agent returns False only when the dir already
            # exists; we just checked it didn't, so this is a race
            # (extremely unlikely) or a permission error.
            raise ImportError(
                f"Could not create kin folder for {kin_name!r}."
            )
        created_kin = True
    else:
        if mode == "create_only":
            raise ImportError(
                f"Kin {kin_name!r} already exists; create_only mode "
                f"refuses to write into an existing kin."
            )

    # Merge weaves the imported turns into the existing conversation by
    # timestamp (see _write_merged) — a full rewrite, so it takes its own
    # path rather than the append-a-block loop below.
    if mode == "merge":
        return _write_merged(
            kin_name, messages,
            source_label=source_label,
            source_description=source_description,
            created_kin=created_kin,
        )

    backup_path = None
    if mode == "replace":
        backup_path = _backup_conversation(kin_name)
        path = _conversation_jsonl_path(kin_name)
        if path.exists():
            # Truncate the live file. We use a plain open() rather
            # than removing the file so the existing conversation
            # lock (which guards reads/writes via the path) keeps
            # working unchanged.
            with path.open("w", encoding="utf-8") as f:
                f.write("")

    first_ts = _first_ts(messages)
    last_ts = _last_ts(messages)
    operator_name = _infer_operator_name(messages)

    # Leading marker.
    head = leading_marker(
        count=len(messages),
        source_label=source_label,
        source_description=source_description,
        first_ts=first_ts,
        last_ts=last_ts,
        operator_name=operator_name,
        kin_name=kin_name,
    )
    _safe_append(kin_name, head)

    written = 0
    dropped = 0
    for msg in messages:
        cleaned = _clean_chat_message(msg)
        if cleaned is None:
            dropped += 1
            continue
        _safe_append(kin_name, cleaned)
        written += 1

    tail = trailing_marker(source_label=source_label, last_ts=last_ts,
                           kin_name=kin_name)
    _safe_append(kin_name, tail)

    return {
        "kin": kin_name,
        "kin_dir": str(d),
        "written": written,
        "dropped": dropped,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "mode": mode,
        "created_kin": created_kin,
        "backup_path": str(backup_path) if backup_path else None,
    }


def _forward_filled_keys(messages):
    """Sort keys for `messages`, where a turn with no `ts` inherits the
    last one seen before it.

    Foreign parsers stamp every turn they emit, so the import path never
    needs this. A restore reads Hearthkin's own conversation.jsonl, where
    plenty of turns legitimately carry no `ts` at all — and sorting those
    on `ts or ""` drops every one of them to the very front of the file,
    because "" sorts before every real timestamp. That silently rebuilds a
    kin's history opening with a scrambled block.

    Inheriting the preceding turn's timestamp keeps an unstamped turn next
    to the turn it followed. The key is used for ordering only; the row is
    written unchanged and stays unstamped on disk."""
    keys = []
    last = ""
    for m in messages:
        ts = m.get("ts")
        if isinstance(ts, str) and ts:
            last = ts
        keys.append(last)
    return keys


def _weave_by_timestamp(existing, imported, imported_keys=None):
    """Merge `imported` turns into `existing` by timestamp WITHOUT
    reordering the existing turns.

    Existing on-disk turns are emitted in their exact stored order — that
    order is authoritative, because many carry no `ts` and some `ts` values
    disagree with stored order, so the sequence can't be re-sorted safely.
    Each imported turn (which always carries a real `ts` from its parser) is
    slotted in just before the first existing turn whose timestamp is at or
    after it. Imported turns later than everything — or all of them, when no
    existing turn has a usable ts — fall to the end.

    The common case, importing an older era that predates everything here,
    puts every imported turn ahead of the first timestamped existing turn:
    a clean chronological prepend."""
    if imported_keys is None:
        pairs = [(m.get("ts") or "", m) for m in imported]
    else:
        pairs = list(zip(imported_keys, imported))
    # Stable sort: turns sharing a key (an unstamped run inheriting the
    # same inherited timestamp) keep their original order relative to
    # each other.
    pairs.sort(key=lambda p: p[0])

    result = []
    i = 0
    n = len(pairs)
    for turn in existing:
        ets = turn.get("ts")
        if ets:
            while i < n and pairs[i][0] <= ets:
                result.append(pairs[i][1])
                i += 1
        result.append(turn)
    while i < n:
        result.append(pairs[i][1])
        i += 1
    return result


def _write_merged(kin_name, messages, *, source_label, source_description,
                  created_kin):
    """mode="merge": back up the existing conversation, weave the imported
    turns in by timestamp (existing order preserved — see
    _weave_by_timestamp), and rewrite the file. A single leading marker goes
    at the very front naming the carried-over history; there's no trailing
    marker, because merged turns interleave rather than form one contiguous
    block."""
    existing = load_agent_conversation(kin_name)

    cleaned = []
    dropped = 0
    for msg in messages:
        c = _clean_chat_message(msg)
        if c is None:
            dropped += 1
            continue
        cleaned.append(c)
    if not cleaned:
        raise ImportError("No importable messages after cleanup.")

    first_ts = _first_ts(cleaned)
    last_ts = _last_ts(cleaned)
    operator_name = _infer_operator_name(cleaned)
    head = leading_marker(
        count=len(cleaned),
        source_label=source_label,
        source_description=source_description,
        first_ts=first_ts,
        last_ts=last_ts,
        operator_name=operator_name,
        kin_name=kin_name,
    )

    # Back up the existing conversation before the rewrite (same shape as
    # replace mode's backup). Missing / empty file → nothing to back up.
    backup_path = _backup_conversation(kin_name)

    woven = _weave_by_timestamp(existing, cleaned) if existing else cleaned
    save_agent_conversation(kin_name, [head] + woven)

    return {
        "kin": kin_name,
        "kin_dir": str(agent_dir(kin_name)),
        "written": len(cleaned),
        "dropped": dropped,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "mode": "merge",
        "created_kin": created_kin,
        "backup_path": str(backup_path) if backup_path else None,
    }


def _restore_key(msg):
    """Identity of a turn for duplicate detection during a restore, or
    None when the turn can't be identified confidently.

    Role + content + ts identifies a turn: two that agree on all three are
    the same turn seen twice (an archive overlapping the live file), not
    two things that happened. Content alone would be far too aggressive —
    a kin says "yeah" many times, legitimately.

    A turn with NO timestamp returns None and is never matched. That case
    isn't hypothetical padding: `_clean_chat_message` normalises
    `content: None` to `""` on assistant tool-call turns, so every
    untimestamped one collapses to the identical ("assistant", "", None).
    Treating those as duplicates silently deletes real turns — and
    deleting an assistant tool-call turn orphans the `tool` result that
    answered it, which is the message shape providers reject outright.
    A missed duplicate is a visible, harmless repeat; a wrongly-matched
    one is invisible data loss. Prefer the repeat.

    Tool-call ids join the key when present, so two genuine tool turns
    that share a timestamp stay distinct."""
    ts = msg.get("ts")
    if not (isinstance(ts, str) and ts):
        return None

    tool_ids = ()
    calls = msg.get("tool_calls")
    if isinstance(calls, list):
        tool_ids = tuple(tc.get("id") for tc in calls
                         if isinstance(tc, dict) and tc.get("id"))
    return (msg.get("role"), msg.get("content"), ts,
            tool_ids, msg.get("tool_call_id"))


def restore_rows(existing, messages, mode="merge"):
    """Pure half of `restore_history`: work out the final row list.

    Returns `(rows, stats)` where stats counts what happened. Split out
    from the disk write so the behaviour that matters — provenance
    survives, duplicates don't — is testable without a kin folder.

    Rows are passed through `_clean_chat_message`, which preserves
    `source`, `speaker`, `sender_*` and `attachments` as well as the
    core fields. Nothing is relabelled and no marker is added; a kin's
    own turns are not an import and must not be dressed as one."""
    cleaned = []
    dropped = 0
    for msg in messages:
        c = _clean_chat_message(msg)
        if c is None:
            dropped += 1
            continue
        cleaned.append(c)

    seen = set()
    if mode != "replace":
        seen = {k for k in (_restore_key(m) for m in existing) if k is not None}
    fresh = []
    skipped = 0
    for m in cleaned:
        k = _restore_key(m)
        # k is None for a turn we can't identify — always keep it. See
        # _restore_key: a wrongly-matched turn is invisible data loss.
        if k is not None and k in seen:
            skipped += 1
            continue
        if k is not None:
            seen.add(k)
        fresh.append(m)

    if mode == "replace":
        rows = fresh
    elif mode == "append":
        rows = list(existing) + fresh
    else:  # merge
        # Forward-filled keys, not raw ts: a restored conversation carries
        # genuinely unstamped turns, and sorting those on "" would pile
        # them all at the front (see _forward_filled_keys).
        rows = (_weave_by_timestamp(existing, fresh,
                                    imported_keys=_forward_filled_keys(fresh))
                if existing else fresh)

    return rows, {
        "restored": len(fresh),
        "dropped": dropped,
        "skipped_duplicates": skipped,
    }


def restore_history(kin_name, messages, *, mode="merge",
                    create_kin_if_missing=False):
    """Write a kin's OWN turns back into its conversation.jsonl.

    The counterpart to `write_imported_history`, and deliberately not the
    same function. An import announces itself: markers around the block,
    `source: import:<label>` on every row, so the kin can tell carried-in
    history from turns it took here. A restore announces nothing, because
    there is nothing to announce — these turns are already the kin's, and
    each one keeps the `source` it was originally written with.

    Using the import path for this would relabel a kin's own past as seed
    history and overwrite its provenance. That is why this exists.

    Modes mirror the import writer: "merge" (default) weaves by timestamp
    without reordering what's already on disk, "append" adds at the end,
    "replace" swaps the conversation out. Merge and replace back the
    existing file up first.

    Turns already present (same role + content + ts) are skipped, so
    restoring a file that overlaps the live conversation doesn't double
    it up. Returns counts plus the backup path."""
    if not messages:
        raise ImportError("No turns to restore.")

    d = agent_dir(kin_name)
    created_kin = False
    if not d.exists():
        if not create_kin_if_missing:
            raise ImportError(
                f"Kin {kin_name!r} does not exist and "
                f"create_kin_if_missing=False."
            )
        if not create_agent(kin_name, blank_soul=True):
            raise ImportError(f"Could not create kin folder for {kin_name!r}.")
        created_kin = True

    existing = load_agent_conversation(kin_name) if not created_kin else []
    rows, stats = restore_rows(existing, messages, mode=mode)
    if not rows:
        raise ImportError("Nothing to restore after cleanup.")

    backup_path = _backup_conversation(kin_name)

    save_agent_conversation(kin_name, rows)

    return {
        "kin": kin_name,
        "kin_dir": str(d),
        "written": stats["restored"],
        "dropped": stats["dropped"],
        "skipped_duplicates": stats["skipped_duplicates"],
        "total_rows": len(rows),
        "first_ts": _first_ts(rows),
        "last_ts": _last_ts(rows),
        "mode": mode,
        "created_kin": created_kin,
        "backup_path": str(backup_path) if backup_path else None,
    }


def _sanitize_speaker_fields(msg):
    """Return `msg` with `speaker` / `sender_attribution` run through
    `sanitize_for_prompt_literal` (control / format chars stripped).
    Messages without those fields pass through by reference."""
    sp = msg.get("speaker")
    sa = msg.get("sender_attribution")
    if not isinstance(sp, str) and not isinstance(sa, str):
        return msg
    out = dict(msg)
    if isinstance(sp, str):
        out["speaker"] = sanitize_for_prompt_literal(sp)
    if isinstance(sa, str):
        out["sender_attribution"] = sanitize_for_prompt_literal(sa)
    return out


def _safe_append(kin_name, msg):
    """append_agent_conversation_turn already wraps in try/except and
    logs to save_failures.log on failure. We don't add a second layer;
    if the disk is unwritable the import will be incomplete and the
    failure log is the place to look."""
    append_agent_conversation_turn(kin_name, msg)


def _first_ts(messages):
    for m in messages:
        ts = m.get("ts")
        if isinstance(ts, str) and ts:
            return ts
    return None


def _last_ts(messages):
    for m in reversed(messages):
        ts = m.get("ts")
        if isinstance(ts, str) and ts:
            return ts
    return None


def _infer_operator_name(messages):
    """Pick the operator's name from the imported messages so the leading
    marker can name them explicitly. Returns the unique non-kin speaker
    name if there's exactly one across all role=user turns; otherwise
    None (multi-party group imports — each turn keeps its own inline
    attribution and no single 'operator' label applies).

    Without this, the distillation summarizer grabs 'hearthkin' from
    the marker prefix and misattributes the operator's past actions
    to the harness. See history-import.md for the full diagnosis."""
    speakers = set()
    for m in messages:
        if m.get("role") != "user":
            continue
        sp = m.get("speaker")
        if isinstance(sp, str) and sp.strip():
            speakers.add(sp.strip())
            if len(speakers) > 1:
                return None
    if len(speakers) == 1:
        return next(iter(speakers))
    return None
