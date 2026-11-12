# SPDX-License-Identifier: CC0-1.0

"""What a kin has actually seen of a file, during one turn.

The file tools used to trust the kin's word that it knew what was in a file
before replacing it. `write_file` replaces a file whole, so a kin that
*believes* it knows a long file -- from an earlier day, from its notes, from
nothing -- can erase it in one call with no error. That happened: a scheduled
wake-up meant to add one entry to a months-long journal called `write_file`
with just the new entry, and every earlier entry was gone. The kin's own notes
already said "never write_file on an accumulating file". It knew the rule. The
rule did nothing, because a rule is something a model has to remember.

A refusal is something it can't forget. So the tools now keep a ledger:

  - `read_file` records each file it shows the kin: how big it was and when it
    last changed, and whether the kin saw ALL of it or only a slice.
  - `write_file` refuses to replace an existing file the kin has not read IN
    FULL this turn, or that has changed on disk since it read it.
  - `write_file` / `edit_file` record their own results, so a kin that wrote a
    file can write it again without being made to re-read its own words.

Why "this turn" and not "ever": a ledger lives as long as one `load_tools()`
call, which is one chat turn or one scheduled run. That is deliberately the
same span in which the kin can still SEE what it read. Older tool results are
compacted to one-line summaries on later turns, so a read from yesterday is a
memory of a file, not the file. Re-reading is cheap; a lost journal is not.

Only kin go through `load_tools()`, so only kin are guarded. Code that calls
the tool functions directly (scripts, tests, the app itself) passes no ledger
and behaves exactly as before.
"""

import os
from pathlib import Path


def _key(p):
    """One spelling per file: resolved, and case-folded on Windows (where
    `Notes.md` and `notes.md` are the same file)."""
    try:
        s = str(Path(p).resolve())
    except Exception:
        s = str(p)
    return os.path.normcase(s)


def _stamp(p):
    """(modified time in ns, size in bytes), or None if the file is gone."""
    try:
        st = os.stat(p)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


class ReadLedger:
    """Per-turn record of which files the kin has seen, and how much of each."""

    def __init__(self):
        self._seen = {}  # key -> {"stamp": (mtime_ns, size), "whole": bool}

    def record_read(self, p, whole):
        """The kin was just shown `p` -- all of it (`whole=True`) or a part.
        A whole read is never downgraded by a later partial one: once the kin
        has seen everything, a second look at a slice doesn't un-see it."""
        k = _key(p)
        prev = self._seen.get(k)
        stamp = _stamp(p)
        keep_whole = bool(whole) or (
            prev is not None and prev["whole"] and prev["stamp"] == stamp)
        self._seen[k] = {"stamp": stamp, "whole": keep_whole}

    def record_write(self, p, whole=True):
        """The kin just wrote `p` itself. After `write_file` it knows the
        whole file (it supplied every byte). After `edit_file` it knows only
        what it knew before, so `whole` carries the earlier state."""
        k = _key(p)
        if not whole:
            prev = self._seen.get(k)
            whole = bool(prev and prev["whole"])
        self._seen[k] = {"stamp": _stamp(p), "whole": bool(whole)}

    def entry(self, p):
        return self._seen.get(_key(p))

    def changed_since_seen(self, p):
        """True when the kin saw `p` this turn and it is different on disk
        now (another surface, a scheduled run, the person, an exec command)."""
        e = self.entry(p)
        return e is not None and e["stamp"] != _stamp(p)


def overwrite_refusal(ledger, p, tool="write_file", again=None):
    """The model-facing refusal for replacing `p` whole, or "" to allow.

    Allowed without a read: a file that doesn't exist yet, or one that is
    empty -- there is nothing in it to lose. Everything else needs a full,
    current read this turn."""
    if ledger is None:
        return ""
    again = again or f"call {tool} again"
    try:
        if not p.exists() or not p.is_file():
            return ""
        size = p.stat().st_size
    except OSError:
        return ""
    if size == 0:
        return ""
    e = ledger.entry(p)
    add_instead = (
        "If you meant to ADD to it, use `note` (appends a dated entry) or "
        "`edit_file` (changes just the part you name) -- neither can erase "
        "the rest."
    )
    if e is None:
        return (
            f"{tool}: refused -- {p} already has {size:,} bytes in it, and you "
            f"haven't read it this turn. {tool} replaces the WHOLE file, so "
            f"everything in it now would be lost. {add_instead} If you really "
            f"mean to replace it, read it first with read_file (all of it), "
            f"then {again}. Nothing was changed."
        )
    if ledger.changed_since_seen(p):
        return (
            f"{tool}: refused -- {p} has changed since you read it this turn "
            f"(something else wrote to it). Writing now would erase that "
            f"change without you ever seeing it. Read it again, then decide. "
            f"Nothing was changed."
        )
    if not e["whole"]:
        return (
            f"{tool}: refused -- you've only read PART of {p} ({size:,} bytes "
            f"in all) this turn. {tool} replaces the whole file, so the parts "
            f"you haven't seen would be lost. {add_instead} To replace it on "
            f"purpose, read it all first (read_file with start_line=1 and no "
            f"line_count reads to the end). Nothing was changed."
        )
    return ""


def edit_refusal(ledger, p, tool="edit_file"):
    """`edit_file` doesn't need a read first -- its exact-once match already
    proves the kin knows the text it is changing. But if the kin DID read the
    file this turn and it changed underneath since, refuse: the kin is working
    from a picture of the file that is no longer true."""
    if ledger is None:
        return ""
    if ledger.changed_since_seen(p):
        return (
            f"{tool}: refused -- {p} has changed since you read it this turn "
            f"(something else wrote to it). Read it again so you're editing "
            f"what's really there. Nothing was changed."
        )
    return ""
