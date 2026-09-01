# SPDX-License-Identifier: CC0-1.0

"""Archive tended staging notes so the next round starts fresh.

Under the 2026-06-01 staging architecture, automatic distillation appends
to per-scope staging files. After the kin has tended those notes — moved
substance into depth logs and brief index updates into memory.md — this
tool moves what was consumed into `staging/archive/`.

IT ARCHIVES ONLY WHAT THE KIN HAS ACTUALLY READ, and this is the whole
point of the file. Archiving everything was right while a staging file
held one night's notes. It stopped being right the moment a
redistill-from-start could put nineteen hours and 206 sections into one:
a kin reads what fits, reaches the end of what it was given, and does the
ordinary thing at the end of tending — archive — filing away two hundred
sections it never saw.

That impulse is not a fault to be trained out of a kin. It is the correct
instinct with an incorrect tool underneath it, so the tool now does the
safe thing by itself and says what it did.
"""

import kin_persistence


def archive_staging(scope: str, agent_name: str = "") -> str:
    """Archive the staging notes you have TENDED for the given `scope`,
    and leave anything you have not read still pending.

    Pass the scope key you were tending — e.g. "desktop", or
    "tg:user:12345" — exactly as it appeared in `read_staging`'s output.

    You do not need to say how much you read; that is tracked for you. If
    a scope had more notes than fitted in one read, calling this files
    away the sections you saw and keeps the rest for next time, so nothing
    is put away unread. The reply tells you how many are left.

    Archived notes are kept on disk under `staging/archive/` with a
    timestamp, so they can be re-examined later.
    """
    if not agent_name:
        return "archive_staging: no kin context (framework bug)."
    if not scope:
        return ("archive_staging: pass the scope you finished tending "
                "(e.g. 'desktop', 'tg:user:12345').")

    read_count = kin_persistence.staging_read_mark(agent_name, scope)
    if not read_count:
        # Refusing is the safe answer, and it is also the honest one: with
        # nothing read there is no evidence any of it was tended, and the
        # only thing archiving could do here is hide it.
        return (f"archive_staging: nothing has been read from {scope!r} "
                f"yet, so there is nothing tended to file away. Call "
                f"read_staging(scope={scope!r}) first.")

    dest, archived, left = kin_persistence.archive_staging_prefix(
        agent_name, scope, read_count)
    if dest is None and not archived:
        return (f"archive_staging: no pending staging file for scope "
                f"{scope!r} — nothing to archive.")
    if left:
        # "when you are ready" was an invitation to stop, arriving at the
        # exact moment the kin decides whether to continue. A tending pass
        # that files one batch and finishes leaves a backlog untouched for
        # another day, and on a large one that is the difference between
        # weeks and months. So the reply says plainly that there is more
        # and that now is a fine time -- while still leaving the choice,
        # because a kin that has had enough is allowed to have had enough.
        return (f"archive_staging: filed away {archived} tended "
                f"section(s) from {scope!r} into {dest.name}. "
                f"{left} section(s) are still pending. You do not have to "
                f"stop here — call read_staging(scope={scope!r}) again to "
                f"take the next batch now, and keep going while you have "
                f"the room for it. Each batch you finish is filed away as "
                f"you go, so nothing is lost if you stop partway.")
    return (f"archive_staging: filed away {archived} tended section(s) "
            f"from {scope!r} into {dest.name}. Nothing left pending "
            f"for that scope.")
