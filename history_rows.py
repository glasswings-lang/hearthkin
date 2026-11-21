"""Keeping a distill bookmark true when a history loses rows off its front.

Telegram's and Discord's own histories (the ones not kept with the desktop
conversation) are capped: once full, old rows come off the FRONT. A
/clear or a move into the desktop conversation takes rows away too. The
distill bookmark is a position in the list ("read up to row 120"), so
when 40 rows come off the front, the row that was 120th is now 80th.
Before this the bookmark stayed at 120, and the 40 rows that slid under
it were never distilled: they never reached the kin's memory (audit, 20
Nov 2026).

The fix keeps ONE number per history: how many rows have ever been
added to it. Rows-added minus the rows still there is how many have come
off the front, whatever took them (the cap, a /clear, a sweep of broken
tool rows, a move). Only the two things that are NOT a front cut adjust
the count by hand: adding rows (count goes up), and cutting the newest
rows off the END (undo, regenerate), which takes those rows back off the
count. The count is saved in the same file as the history, so they
can't disagree.

When a bookmark is written, the number that had come off the front at
that moment is written beside it (`distill_front_seen`). When it's read,
anything that has come off the front since is taken off the bookmark.
The bot writes the history file and the app writes the config; neither
writes the other's, so there's nothing to race.

Every surface and the Memory tab go through `scope_bookmark` and
`set_scope_bookmark`, so the rule lives here once.
"""


ROWS_ADDED_KEY = "_rows_added"   # Discord: reserved key in discord_history.json


class ScopeRows(list):
    """A history list that also knows how many rows had come off its front
    when it was read (None: this history is never cut from the front)."""
    front_dropped = None


def scope_rows(rows, front_dropped=None):
    out = ScopeRows(rows or [])
    out.front_dropped = front_dropped
    return out


def front_dropped(rows_added, current_len):
    """How many rows have come off the front: everything ever added minus
    what's still there. None when there's no count yet (an old file)."""
    if rows_added is None:
        return None
    try:
        return max(0, int(rows_added) - int(current_len))
    except (TypeError, ValueError):
        return None


def note_added(counts, key, existing_len, n):
    """`n` rows were just added to the history under `key`, which had
    `existing_len` rows before. A history with no count yet starts from
    what it holds, so an old file begins tracking without a jump."""
    key = str(key)
    try:
        base = int(counts.get(key, existing_len))
    except (TypeError, ValueError):
        base = int(existing_len)
    counts[key] = base + int(n)


def note_end_cut(counts, key, existing_len, n):
    """The newest `n` rows were cut off the END (undo, regenerate). They
    come back off the count, so they aren't mistaken for a front cut."""
    key = str(key)
    try:
        base = int(counts.get(key, existing_len))
    except (TypeError, ValueError):
        base = int(existing_len)
    counts[key] = max(0, base - int(n))


def clean_counts(raw):
    """A {key: int} dict from whatever was on disk."""
    out = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                out[str(k)] = max(0, int(v))
            except (TypeError, ValueError):
                continue
    return out


def scope_bookmark(cfg, scope_key, convo):
    """Where distillation has read up to in `convo`, as a position in it
    now. Takes off whatever came off the front since the bookmark was
    written, then applies the restart rule in live_distill_bookmark."""
    from kin_persistence import live_distill_bookmark
    cfg = cfg or {}
    offsets = cfg.get("distill_offsets") or {}
    try:
        stored = int(offsets.get(scope_key, 0) or 0)
    except (TypeError, ValueError):
        stored = 0
    now = getattr(convo, "front_dropped", None)
    seen = (cfg.get("distill_front_seen") or {}).get(scope_key)
    if now is not None and seen is not None:
        try:
            seen = int(seen)
            if now < seen:
                # The count went backwards: the history was replaced by
                # one this bookmark wasn't measured on. Read it again.
                return 0
            stored = max(0, stored - (now - seen))
        except (TypeError, ValueError):
            pass
    return live_distill_bookmark(stored, len(convo or []))


def set_scope_bookmark(cfg, scope_key, value, front=None):
    """Write the bookmark `value` (a position in a list that had `front`
    rows off its front when it was read) into `cfg`. Doesn't save."""
    offsets = dict(cfg.get("distill_offsets") or {})
    offsets[scope_key] = int(value)
    cfg["distill_offsets"] = offsets
    seen = dict(cfg.get("distill_front_seen") or {})
    if front is None:
        seen.pop(scope_key, None)
    else:
        seen[scope_key] = int(front)
    if seen:
        cfg["distill_front_seen"] = seen
    else:
        cfg.pop("distill_front_seen", None)


HISTORY_CAP_DEFAULT = 100


def history_cap(section, default=HISTORY_CAP_DEFAULT):
    """The "History cap (messages)" setting of a Telegram or Discord settings
    section: a whole number, 0 for no cap, else the default."""
    try:
        v = int((section or {}).get("history_cap", default))
    except (TypeError, ValueError, AttributeError):
        return default
    return v if v >= 0 else default


def trim_front(history, cap):
    """A history kept apart from the desktop's, capped at `cap` messages,
    trimmed in RARE, BIG steps rather than one message per turn. The one
    rule for Telegram's and Discord's own histories (21 Nov 2026; Discord
    used to keep 200 and send the newest 40).

    Trimming one message per turn costs minutes a turn on a local model: it
    reuses its cached work only for an unbroken run from the very start of
    the prompt, so every message pushed off the FRONT makes it read the whole
    prompt again from cold (measured: 22,000+ tokens re-read, about five
    minutes before a reply began, every turn). So the history fills to
    `cap` and, when it overflows, is cut back by a quarter at once. `cap`
    stays a true ceiling, because an oversized context on local Ollama
    returns nothing at all. The compaction note, if any, is kept.
    `cap` of 0 means no cap."""
    if cap <= 0 or len(history) <= cap:
        return history
    step = max(1, cap // 4)
    from chat_helpers import keep_compaction_note
    return keep_compaction_note(history, history[-max(10, cap - step):])
