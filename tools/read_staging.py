# SPDX-License-Identifier: CC0-1.0

"""Read pending staging notes the summarizer left for the kin.

Under the 2026-06-01 staging architecture, automatic distillation writes
its output to per-scope staging files instead of directly to memory.md.
The kin reads those notes during nightly tending and decides what's
worth keeping. This tool is how the kin surfaces those notes.

It returns WHOLE SECTIONS up to a character budget, because a staging
file has no ceiling and the trip back to a kin does. Each distillation
run appends one `## <timestamp>` section, so the file already carries the
seams; this only has to stop on one. Measured on a real kin: 206
sections, 1.5 MB, against a tool-result cap of 8,000 characters — the kin
would have been handed half of one percent of its own notes, cut
mid-sentence, with nothing telling it there was more.
"""

import kin_persistence


def _budget(agent_name):
    try:
        cfg = kin_persistence.load_agent_config(agent_name) or {}
        return max(500, int(cfg.get("staging_read_max_chars", 6000) or 6000))
    except Exception:
        return 6000


def _batch(agent_name, scope, text, start):
    """One scope's notes from section `start`, within budget, plus a line
    saying what was not shown. Returns (body, next_start, total)."""
    preamble, sections = kin_persistence.split_staging_sections(text)
    total = len(sections)
    if not total:
        return text, 0, 0
    start = max(1, int(start or 1))
    if start > total:
        return (f"read_staging: scope {scope!r} has {total} section(s); "
                f"there is no section {start}."), 0, total
    budget = _budget(agent_name)
    out, used, idx = [], 0, start - 1
    while idx < total:
        chunk = sections[idx]
        # Always give at least one section, even an oversized one: a kin
        # that can never reach section 40 because section 40 is large is
        # stuck forever, which is worse than one long read.
        if out and used + len(chunk) > budget:
            break
        out.append(chunk)
        used += len(chunk)
        idx += 1
    shown_to = idx
    body = preamble + "".join(out) if start == 1 else "".join(out)
    if shown_to < total:
        body += (
            f"\n\n[read_staging: showed sections {start}-{shown_to} of "
            f"{total} for scope {scope!r}. {total - shown_to} still "
            f"unread. Tend these, then keep going: call "
            f"read_staging(scope={scope!r}, start={shown_to + 1}) for "
            f"the next batch. Archiving files away only what you have "
            f"read, so the rest stays pending and nothing is lost if "
            f"you stop.]")
    else:
        body += (
            f"\n\n[read_staging: showed sections {start}-{total} of "
            f"{total} for scope {scope!r} — that is all of them.]")
    return body, shown_to, total


def read_staging(scope: str = "", start: int = 1, agent_name: str = "") -> str:
    """Read pending staging notes. Pass no `scope` to see EVERY pending
    surface (desktop, and each non-shared Telegram DM/group). Pass a scope
    key like "desktop" or "tg:user:12345" to read just that one.

    Notes come back in whole sections, oldest first, up to a size limit.
    If there are more than fit, the reply tells you how many are left and
    what `start` to pass to continue — e.g.
    `read_staging(scope="desktop", start=13)`. You do not have to finish a
    scope in one turn, and you do not have to remember where you were:
    archiving files away only the sections you have actually read, and
    leaves the rest pending for next time.

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

    if scope:
        wanted = scope
        text = kin_persistence.load_staging(agent_name, wanted)
        if not text:
            return (f"read_staging: no pending notes for scope "
                    f"{scope!r}. Pending scopes: "
                    f"{', '.join(sorted(files.keys()))}")
        body, shown_to, _total = _batch(agent_name, wanted, text, start)
        if shown_to:
            kin_persistence.set_staging_read_mark(agent_name, wanted, shown_to)
        return body

    # No scope filter. Take them in turn under one shared budget rather
    # than concatenating everything: the old version built the whole lot
    # and let the tool loop cut it, which is how 1.5 MB became 8,000
    # characters of the OLDEST notes with no indication of the rest.
    parts, remaining = [], _budget(agent_name)
    unshown = []
    for scope_key in sorted(files.keys()):
        text = kin_persistence.load_staging(agent_name, scope_key)
        if not text:
            continue
        if remaining <= 0:
            unshown.append(scope_key)
            continue
        preamble, sections = kin_persistence.split_staging_sections(text)
        total = len(sections)
        used, out = 0, []
        for chunk in sections:
            if out and used + len(chunk) > remaining:
                break
            out.append(chunk)
            used += len(chunk)
        if not out:
            unshown.append(scope_key)
            continue
        kin_persistence.set_staging_read_mark(agent_name, scope_key, len(out))
        remaining -= used
        note = ""
        if len(out) < total:
            note = (f"\n\n[read_staging: showed sections 1-{len(out)} of "
                    f"{total} for scope {scope_key!r}. {total - len(out)} "
                    f"still unread. Tend these, then keep going: call "
                    f"read_staging(scope={scope_key!r}, "
                    f"start={len(out) + 1}) for the next batch. "
                    f"Archiving files away only what you have read.]")
        parts.append(f"=== {scope_key} ===\n{preamble}{''.join(out)}{note}")
    if unshown:
        parts.append(
            "[read_staging: no room left this call for scope(s) "
            + ", ".join(repr(s) for s in unshown)
            + " — read them one at a time with "
              "read_staging(scope=\"<scope>\").]")
    return "\n\n".join(parts) if parts else (
        "read_staging: no pending staging notes — nothing to tend.")
