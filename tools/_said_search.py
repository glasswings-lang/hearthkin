# SPDX-License-Identifier: CC0-1.0
"""Search what was actually said, not only the notes (18 Nov 2026).

memory_search used to look only at a kin's notes. The conversation itself was
on disk and readable by line number, but nothing could FIND anything in it, so
the notes stood in for the real thing. The person this app is for put it this
way in September: the original is the memory; every note is a map back to it.
This module is the finding half. It returns a few moments, each with when,
who, a piece of their words and where to read more.

Only things people said are searched: user and assistant turns with text.
Tool output (file listings would bury everything), Hearthkin's own bracketed
notes, and system rows are left out.

PRIVACY. `scope` decides which conversations may be searched:
  None    every conversation the kin has (its person, the desktop,
          wake-ups, rooms, and anyone the person opened it to)
  {"place": "telegram:<id>" | "telegram:group:<id>" | "discord:<channel>",
   "operators": [ids marked "This is me"]}
          only that one conversation, unless one of `operators` has spoken
          in it, in which case everything
  anything else reaches nothing.
The bots work the scope out from the person's settings (see
tools/_person_settings.resolve_said_scope); the model never sets it.
"""

import json
import re
from pathlib import Path

from ._io import robust_read_text

_MAX_ENTRY_CHARS = 4000     # what is scored per message; long pastes are cut
_SNIPPET_CHARS = 220
_SNIPPET_CHARS_NO_LINES = 400  # a JSON store can't be opened by line, so more


def _tokenize(text):
    return re.findall(r"\w+", (text or "").lower())


def _names(kin_dir):
    """The names the person gave people in Hearthkin: each person's label
    (Telegram and Discord person windows), and the app's own user name for
    desktop turns with no sender. A label beats a platform handle, so the
    kin reads "Morgan said", not "@someHandle said"."""
    labels, me = {}, ""
    try:
        cfg = json.loads(robust_read_text(Path(kin_dir) / "config.json"))
        for surface in ("telegram", "discord"):
            sec = cfg.get(surface) or {}
            for k, v in (sec.get("user_labels") or {}).items():
                if isinstance(v, str) and v.strip():
                    labels[str(k)] = v.strip()
    except Exception:
        pass
    try:
        app = json.loads(robust_read_text(
            Path(kin_dir).parent.parent / "config.json"))
        me = (app.get("user_name") or "").strip()
    except Exception:
        pass
    return labels, me


def _speaker(m, names=({}, "")):
    if m.get("role") == "assistant":
        return "you"
    labels, me = names
    sid = m.get("sender_id")
    if sid is not None and str(sid) in labels:
        return labels[str(sid)]
    name = (m.get("sender_name") or m.get("speaker") or "").strip()
    if name:
        return name
    return me or "they"


def _date(m):
    ts = str(m.get("ts") or "")
    mo = re.match(r"(\d{4})-(\d\d)-(\d\d)", ts)
    if not mo:
        return ""
    months = ("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec").split()
    try:
        return f"{int(mo.group(3))} {months[int(mo.group(2)) - 1]} {mo.group(1)}"
    except (ValueError, IndexError):
        return ""


def _said(m):
    """The text of a message somebody said, or None."""
    if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
        return None
    if m.get("compact") or m.get("tool_calls") and not m.get("content"):
        return None
    c = m.get("content")
    if not isinstance(c, str) or not c.strip():
        return None
    s = c.strip()
    if s.startswith("[hearthkin:"):
        return None
    return s


def _jsonl_entries(path, label):
    """(where, line number, message) for each said line of a .jsonl file."""
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                if _said(m) is not None:
                    out.append((label, i, m))
    except OSError:
        pass
    return out


def _json_store(path, top_key=None):
    try:
        data = json.loads(robust_read_text(path))
    except Exception:
        return {}
    if top_key:
        data = data.get(top_key) if isinstance(data, dict) else None
    return data if isinstance(data, dict) else {}


def _store_entries(kin_dir, surface, only_key=None):
    """(where, message number, message) from a Telegram or Discord store."""
    if surface == "telegram":
        store = _json_store(kin_dir / "telegram_history.json", "histories")
        name = "Telegram"
    else:
        store = _json_store(kin_dir / "discord_history.json")
        if isinstance(store.get("histories"), dict):
            store = store["histories"]
        name = "Discord"
    out = []
    for key, msgs in store.items():
        if only_key is not None and str(key) != str(only_key):
            continue
        if not isinstance(msgs, list):
            continue
        where = (f"{name} group chat" if str(key).startswith("group:")
                 else f"{name} chat")
        for i, m in enumerate(msgs, 1):
            if _said(m) is not None:
                out.append((f"{where} {key}", -i, m))
    return out


def _place_entries(kin_dir, place):
    """One conversation, wherever it's kept: its rows in conversation.jsonl
    (a conversation shared with the desktop is tagged with `place` as its
    source) and its slice of the Telegram or Discord store."""
    out = [e for e in _jsonl_entries(kin_dir / "conversation.jsonl",
                                     "conversation.jsonl")
           if (e[2].get("source") or "") == place]
    surface, _, key = place.partition(":")
    if surface in ("telegram", "discord") and key:
        out += _store_entries(kin_dir, surface, only_key=key)
    return out


def gather(kin_dir, scope=None):
    kin_dir = Path(kin_dir)
    if isinstance(scope, dict):
        place = str(scope.get("place") or "")
        if not place:
            return []
        entries = _place_entries(kin_dir, place)
        ops = {str(o) for o in (scope.get("operators") or ())}
        if ops and any(m.get("role") == "user"
                       and str(m.get("sender_id", "")) in ops
                       for _, _, m in entries):
            scope = None     # the operator has spoken here: theirs too
        else:
            return entries
    if scope is None:
        entries = _jsonl_entries(kin_dir / "conversation.jsonl",
                                 "conversation.jsonl")
        hist = kin_dir / "cron_history"
        if hist.is_dir():
            for p in sorted(hist.glob("*.jsonl")):
                entries += _jsonl_entries(p, f"cron_history/{p.name}")
        entries += _store_entries(kin_dir, "telegram")
        entries += _store_entries(kin_dir, "discord")
        return entries
    return []   # an unknown scope reaches nothing, never everything


def _best_piece(text, qset, limit):
    """The sentence-ish piece of `text` with the most query words, padded
    out to about `limit` characters."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    parts = re.split(r"(?<=[.!?])\s+", flat)
    best_i, best = 0, -1
    for i, p in enumerate(parts):
        hit = len(qset & set(_tokenize(p)))
        if hit > best:
            best_i, best = i, hit
    piece = parts[best_i]
    j = best_i
    while len(piece) < limit and j + 1 < len(parts):
        j += 1
        piece += " " + parts[j]
    if len(piece) > limit:
        piece = piece[:limit].rsplit(" ", 1)[0] + "..."
    return piece


def search(kin_dir, query, *, scope=None, cap=3, exact=False, bm25_cls=None):
    """Up to `cap` lines, each one moment from the conversation."""
    entries = gather(kin_dir, scope)
    if not entries:
        return []
    texts = [_said(m)[:_MAX_ENTRY_CHARS] for _, _, m in entries]
    qtok = _tokenize(query)
    if not qtok:
        return []
    if exact:
        words = [w for w in query.lower().split() if w]
        scored = [(1.0, i) for i, t in enumerate(texts)
                  if all(w in t.lower() for w in words)]
        scored.reverse()          # newest first among exact matches
    else:
        toks = [_tokenize(t) for t in texts]
        bm = bm25_cls(toks)
        scores = bm.get_scores(qtok)
        scored = sorted(((s, i) for i, s in enumerate(scores) if s > 0),
                        reverse=True)
    qset = set(qtok)
    names = _names(kin_dir)
    lines = []
    for _, i in scored[:cap]:
        where, num, m = entries[i]
        by_line = num > 0
        piece = _best_piece(texts[i], qset,
                            _SNIPPET_CHARS if by_line else _SNIPPET_CHARS_NO_LINES)
        place = (f"{where} line {num}" if by_line
                 else f"{where}, message {-num}")
        when = _date(m)
        lead = f"{place}, {when + ', ' if when else ''}{_speaker(m, names)} said"
        lines.append(f'{lead}: "{piece}"')
    return lines
