# SPDX-License-Identifier: CC0-1.0

"""Read pending staging notes the summarizer left for the kin.

Under the 2026-06-01 staging architecture, automatic distillation writes
its output to per-scope staging files instead of directly to memory.md.
The kin reads those notes during nightly tending and decides what's
worth keeping. This tool is how the kin surfaces those notes.

It returns WHOLE SECTIONS, because a staging file has no ceiling and the
trip back to a kin does. Each `## ...` heading starts a section (since the
bookmark distiller, one section is one bookmark), so the file already
carries the seams; this only has to stop on one. Measured on a real kin:
206 sections, 1.5 MB, against a tool-result cap of 8,000 characters — the
kin would have been handed half of one percent of its own notes, cut
mid-sentence, with nothing telling it there was more.

How much a kin may read is counted in SECTIONS PER REPLY (26 Nov 2026,
replacing a size in characters). A kin was asking for batch after batch in
one reply and filing each away unread to get the next; a character size
only decided how big each batch was, never how many. Now each reply gets
`staging_sections_per_reply` new sections in all, and then the kin is told
to stop, keep what matters and talk. The count lives on the turn's read
ledger, so it starts fresh with every reply and can't be worked round
within one. Re-reading sections already shown never counts.
"""

import kin_persistence

DEFAULT_SECTIONS_PER_REPLY = 5


def _settings(agent_name):
    """(sections allowed per reply, character safety limit or None)."""
    try:
        cfg = kin_persistence.load_agent_config(agent_name) or {}
    except Exception:
        cfg = {}
    try:
        per_reply = max(1, int(cfg.get("staging_sections_per_reply",
                                       DEFAULT_SECTIONS_PER_REPLY)))
    except (TypeError, ValueError):
        per_reply = DEFAULT_SECTIONS_PER_REPLY
    # Not a setting: just stay under the tool-result cap, so nothing
    # downstream cuts a batch mid-section.
    try:
        cap = kin_persistence.tool_result_cap_of(cfg)
    except Exception:
        cap = 8000
    safety = max(500, cap - 600) if cap else None
    return per_reply, safety


def _used(ledger):
    return int(getattr(ledger, "staging_new_sections", 0) or 0) if ledger else 0


def _spend(ledger, n):
    if ledger is not None and n > 0:
        ledger.staging_new_sections = _used(ledger) + n


def _seen_before(agent_name, scope):
    """How many leading sections of this scope the kin has already been
    shown. Re-reading those is free."""
    last = kin_persistence.staging_batch_given(agent_name, scope)
    return max(kin_persistence.staging_read_mark(agent_name, scope),
               last["to"] if last else 0)


def _stop_line(scope, per_reply, shown_from, shown_to, total):
    return (
        f"\n\n[read_staging: showed sections {shown_from}-{shown_to} of "
        f"{total} for scope {scope!r}. That's the {per_reply} new sections "
        f"for this reply. Stop reading here. Keep what matters from these: "
        f"write it to memory.md, a depth log in memory/, or today's journal. "
        f"If your person is here, tell them what you found. The next "
        f"sections come in your next reply: read_staging(scope={scope!r}, "
        f"start={shown_to + 1}). Archiving files away only what you have "
        f"read, so nothing is lost.]")


def spent_message(scope, per_reply, next_start):
    return (
        f"read_staging: not in this reply. You've read the {per_reply} new "
        f"sections this reply allows. Stop here: keep what matters from what "
        f"you read, and if your person is here, tell them what you found. "
        f"Ask again in your next reply, from "
        f"read_staging(scope={scope!r}, start={next_start}). Reading "
        f"sections you've already seen is still allowed.")


def _next_line(agent_name, scope, start, shown_to, total):
    """What the kin is told after a batch that isn't the last one, while
    this reply still has sections left. It says the next batch is held only
    when this kin's setting holds it."""
    if kin_persistence.staging_keep_between_batches(agent_name):
        return (
            f"\n\n[read_staging: showed sections {start}-{shown_to} of "
            f"{total} for scope {scope!r}. {total - shown_to} still unread. "
            f"Keep what matters from these first: write it to memory.md, a "
            f"depth log in memory/, or today's journal. Hearthkin gives the "
            f"next batch only after that, or after archive_staging if "
            f"nothing here needs keeping. Then keep going: call "
            f"read_staging(scope={scope!r}, start={shown_to + 1}). "
            f"Archiving files away only what you have read, so the rest "
            f"stays pending and nothing is lost if you stop.]")
    return (
        f"\n\n[read_staging: showed sections {start}-{shown_to} of "
        f"{total} for scope {scope!r}. {total - shown_to} still "
        f"unread. Tend these, then keep going: call "
        f"read_staging(scope={scope!r}, start={shown_to + 1}) for "
        f"the next batch. Archiving files away only what you have "
        f"read, so the rest stays pending and nothing is lost if "
        f"you stop.]")


def held_message(scope, held):
    a, b = held
    return (
        f"read_staging: not yet. Nothing has been written since you read "
        f"sections {a}-{b} of scope {scope!r}. Keep what matters from them "
        f"first: write it to memory.md, a depth log in memory/, or today's "
        f"journal. If nothing there needs keeping, call "
        f"archive_staging(scope={scope!r}) instead. Then ask for the next "
        f"batch. Reading those same sections again is always allowed.")


def _take(sections, start, seen_before, new_left, safety):
    """Pick sections from `start` (1-based): stop after `new_left` sections
    the kin hasn't seen, or at the safety size. Always gives at least one
    section unless that one would be new with none left, since a kin that
    can never get past one oversized section is stuck for good.
    Returns (chunks, last index shown, how many were new)."""
    out, used, new, idx = [], 0, 0, start - 1
    # Starting inside what it has already seen is a re-read: give back only
    # those, so a re-read never runs on into new sections.
    end = seen_before if start <= seen_before else len(sections)
    while idx < end:
        is_new = idx + 1 > seen_before
        if is_new and new >= new_left:
            break
        chunk = sections[idx]
        if out and safety and used + len(chunk) > safety:
            break
        out.append(chunk)
        used += len(chunk)
        new += 1 if is_new else 0
        idx += 1
    return out, idx, new


def _record(agent_name, scope, start, shown_to, seen_before):
    """Note the batch handed over (for the keep-between-batches hold) and
    move the read mark."""
    if shown_to > seen_before:
        kin_persistence.note_staging_batch_given(
            agent_name, scope, max(int(start or 1), seen_before + 1), shown_to)
    # Move the "read up to here" mark only when this batch carries on from
    # it: a kin that jumps ahead (start=13 first) would otherwise mark 1-12
    # as read, and archive_staging would file away sections it never saw
    # (accuracy audit, 24 Nov 2026).
    if int(start or 1) <= kin_persistence.staging_read_mark(agent_name, scope) + 1:
        kin_persistence.set_staging_read_mark(agent_name, scope, shown_to)


def read_staging(scope: str = "", start: int = 1, agent_name: str = "",
                 read_ledger=None) -> str:
    """Read pending staging notes. Pass no `scope` to see EVERY pending
    surface (desktop, and each non-shared Telegram DM/group). Pass a scope
    key like "desktop" or "tg:user:12345" to read just that one.

    Notes come back in whole sections, oldest first, a few at a time. The
    reply tells you how many are left and what `start` to pass to continue
    — e.g. `read_staging(scope="desktop", start=13)`. You do not have to
    finish a scope in one turn, and you do not have to remember where you
    were: archiving files away only the sections you have actually read,
    and leaves the rest pending for next time.

    Each reply allows a set number of new sections. When the reply says to
    stop, stop: keep what matters, tell your person what you found if
    they're here, and carry on in your next reply. When it says to keep what
    matters first, write it down before asking for more: Hearthkin holds the
    next batch until you have.

    These notes were produced by automatic distillation between the last
    tending pass and now. They have not been added to memory.md or your
    depth logs yet — that's your job. Read them, decide what's worth
    keeping, write substance into the appropriate `memory/<topic>.md` log,
    write brief index updates into `memory.md`, then call
    `archive_staging` for each scope you've tended.

    If something in the notes feels flattened or wrong, you can pull the
    raw conversation with `read_file` on `conversation.jsonl` to verify.
    """
    if not agent_name:
        return "read_staging: no kin context (framework bug)."

    files = kin_persistence.list_staging_files(agent_name)
    if not files:
        return ("read_staging: no pending staging notes — nothing to "
                "tend at the moment.")

    per_reply, safety = _settings(agent_name)
    new_left = per_reply - _used(read_ledger) if read_ledger is not None else per_reply

    if scope:
        wanted = scope
        text = kin_persistence.load_staging(agent_name, wanted)
        if not text:
            return (f"read_staging: no pending notes for scope "
                    f"{scope!r}. Pending scopes: "
                    f"{', '.join(sorted(files.keys()))}")
        preamble, sections = kin_persistence.split_staging_sections(text)
        total = len(sections)
        if not total:
            return text
        start = max(1, int(start or 1))
        if start > total:
            return (f"read_staging: scope {wanted!r} has {total} section(s); "
                    f"there is no section {start}.")
        seen = _seen_before(agent_name, wanted)
        out, shown_to, new = _take(sections, start, seen, max(0, new_left), safety)
        if not out:
            return spent_message(wanted, per_reply, start)
        # One batch at a time: hold sections it hasn't seen until it has
        # kept something from the last batch (25 Nov 2026).
        held = kin_persistence.staging_batch_held(agent_name, wanted, shown_to)
        if held:
            return held_message(wanted, held)
        _record(agent_name, wanted, start, shown_to, seen)
        _spend(read_ledger, new)
        body = (preamble if start == 1 else "") + "".join(out)
        if shown_to >= total:
            body += (f"\n\n[read_staging: showed sections {start}-{total} of "
                     f"{total} for scope {wanted!r} — that is all of them.]")
        elif read_ledger is not None and new and new >= new_left:
            body += _stop_line(wanted, per_reply, start, shown_to, total)
        else:
            body += _next_line(agent_name, wanted, start, shown_to, total)
        return body

    # No scope filter: take the scopes in turn, sharing this reply's
    # allowance, rather than concatenating everything (the old version let
    # the tool loop cut 1.5 MB down to 8,000 characters of the OLDEST notes
    # with no indication of the rest).
    parts, unshown = [], []
    for scope_key in sorted(files.keys()):
        text = kin_persistence.load_staging(agent_name, scope_key)
        if not text:
            continue
        preamble, sections = kin_persistence.split_staging_sections(text)
        total = len(sections)
        if not total:
            continue
        seen = _seen_before(agent_name, scope_key)
        out, shown_to, new = _take(sections, 1, seen, max(0, new_left), safety)
        if not out:
            unshown.append(scope_key)
            continue
        held = kin_persistence.staging_batch_held(agent_name, scope_key, shown_to)
        if held:
            parts.append(f"=== {scope_key} ===\n{held_message(scope_key, held)}")
            continue
        _record(agent_name, scope_key, 1, shown_to, seen)
        _spend(read_ledger, new)
        new_left -= new
        note = ""
        if shown_to < total:
            note = (_stop_line(scope_key, per_reply, 1, shown_to, total)
                    if read_ledger is not None and new_left <= 0 else
                    _next_line(agent_name, scope_key, 1, shown_to, total))
        parts.append(f"=== {scope_key} ===\n{preamble}{''.join(out)}{note}")
    if unshown:
        parts.append(
            "[read_staging: nothing more from scope(s) "
            + ", ".join(repr(s) for s in unshown)
            + " in this reply; this reply's new sections are used up. Read "
              "them in your next reply with read_staging(scope=\"<scope>\").]")
    return "\n\n".join(parts) if parts else (
        "read_staging: no pending staging notes — nothing to tend.")
