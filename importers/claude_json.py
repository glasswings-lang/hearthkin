# SPDX-License-Identifier: CC0-1.0

"""
Claude.ai conversation-export parser.

Anthropic's export (Settings → Privacy → Export data) arrives as a
`conversations.json` holding every chat as one JSON list:

    [
      { "uuid": "...",
        "name": "Reclaiming imagination without guilt",
        "created_at": "2026-02-09T19:14:50.125195Z",
        "updated_at": "...",
        "summary": "...",
        "account": {...},
        "chat_messages": [
          { "uuid": "...",
            "sender": "human",              # or "assistant"
            "created_at": "2026-02-09T19:14:50.125195Z",
            "text": "...",                  # older messages
            "content": [                    # newer messages
              {"type": "text", "text": "..."},
              {"type": "thinking", ...},
              {"type": "tool_use", ...},
              {"type": "tool_result", ...},
            ],
            "attachments": [...], "files": [...],
          }, ...
        ],
      }, ...
    ]

**Role comes from the data, not from a name.** `sender` is `human` or
`assistant` and is authoritative, so this parser does NOT name-match
against `kin_display_name` the way the two-party text importers must.
Same situation as the OpenClaw event stream, and for the same reason:
guessing a role a file already states is how a kin ends up with zero
assistant turns, or with two identities claiming one.

**Both message shapes are handled.** On a real 239-conversation export,
3,813 messages carried an EMPTY `content` list while 13,742 carried a
non-empty top-level `text` — the format changed part-way through the
account's life and an export spans both. Reading only `content` would
silently drop several thousand messages, which is the kind of loss that
looks like nothing at all went wrong.

Only `text` blocks become conversation. `thinking`, `tool_use`,
`tool_result`, `flag` and `token_budget` are deliberately left out: they
are machinery, not what either party said, and on the same export they
outnumbered the actual words. A message that was ONLY an attachment
would otherwise vanish without trace, so those get a short marker
instead of disappearing.

**One file, many conversations** — the same shape as the Skype JSON
export, and the same entry points. `conversation_id=None` imports ALL of
them woven in time order, which is the usual want here: a Claude export
is one person's history split into topic threads, not separate
correspondents, so the whole thing IS the history. Each thread keeps a
one-line header so a kin can still tell where one ended and the next
began.
"""

import json
import os
import zipfile

from tools._io import robust_read_text

# Block types that are things somebody actually said. Everything else in
# a `content` list is machinery — see the module docstring.
_SPOKEN_BLOCK_TYPES = frozenset({"text"})

_ROLE_BY_SENDER = {
    "human": "user",
    "user": "user",
    "assistant": "assistant",
}


# ─── Detection ────────────────────────────────────────────────────── #

def detect(text):
    """True when `text` looks like a claude.ai conversations.json.

    A top-level LIST whose entries carry `chat_messages` — distinctive
    enough on its own. The Skype export is an OBJECT at top level and
    OpenClaw's is line-oriented, so neither can collide with this.

    The window is deliberately generous. A first draft looked in the
    first 4,000 characters and required `"sender"` as well, and missed a
    real export outright: each conversation carries a prose `summary`
    before its messages, and one long enough pushed the first `"sender"`
    to character 4,577. A sniffer that fails on ordinary data is worse
    than none, because it fails by handing the file to the wrong parser
    rather than by saying so.
    """
    head = (text or "")[:65536].lstrip()
    if not head.startswith("["):
        return False
    return '"chat_messages"' in head


def detect_path(source_path):
    """True for a claude.ai export: the `.zip` you download, or the
    `conversations.json` inside it."""
    low = source_path.lower()
    if low.endswith(".zip"):
        try:
            raw = _zip_member(source_path, "conversations.json")
            return bool(raw) and detect(raw)
        except Exception:
            return False
    if not low.endswith(".json"):
        return False
    try:
        return detect(robust_read_text(source_path))
    except Exception:
        return False


# ─── Listing ──────────────────────────────────────────────────────── #

def list_conversations(source_path):
    """Every thread in the export, newest first, for a picker.

    Each entry:
        {"id": uuid, "display_name": str, "message_count": int,
         "created_at": str}
    """
    convs = _load(source_path)
    items = []
    for c in convs:
        if not isinstance(c, dict):
            continue
        items.append({
            "id": c.get("uuid") or "",
            "display_name": (c.get("name") or "").strip() or "(untitled)",
            "message_count": len(c.get("chat_messages") or []),
            "created_at": c.get("created_at") or "",
        })
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items


# ─── Parse ────────────────────────────────────────────────────────── #

def parse(source_path, kin_display_name, conversation_id=None, **_opts):
    """Canonical messages from a Claude export.

    `conversation_id` picks a single thread by uuid; None (the default)
    takes every thread, ordered by time. `kin_display_name` is accepted
    for signature compatibility and deliberately unused — the export
    states each message's role itself.

    Returns `(canonical_messages, source_label, fmt)`, the contract every
    sibling parser uses.
    """
    convs = _load(source_path)
    if conversation_id:
        convs = [c for c in convs
                 if isinstance(c, dict) and c.get("uuid") == conversation_id]
        if not convs:
            raise ValueError(
                f"No conversation with id {conversation_id!r} in this export.")
    out = []
    for conv in convs:
        if not isinstance(conv, dict):
            continue
        msgs = _messages_of(conv)
        if not msgs:
            continue
        # One line marking where this thread starts. Without it 239
        # separate threads arrive as a single undifferentiated stream and
        # nothing on re-read says where one subject ended.
        title = (conv.get("name") or "").strip() or "(untitled)"
        out.append({
            "role": "system",
            "content": f"[hearthkin: imported Claude conversation — {title}]",
            "ts": msgs[0].get("ts") or conv.get("created_at") or "",
        })
        out.extend(msgs)
    if not out:
        raise ValueError(
            "Nothing importable in this Claude export — every message was "
            "empty, or machinery rather than conversation.")
    out.sort(key=lambda m: m.get("ts") or "")
    return out, "claude_ai", "claude_json"


def _messages_of(conv):
    out = []
    for m in (conv.get("chat_messages") or []):
        if not isinstance(m, dict):
            continue
        role = _ROLE_BY_SENDER.get((m.get("sender") or "").strip().lower())
        if role is None:
            continue
        body = _text_of(m)
        if not body:
            continue
        out.append({
            "role": role,
            "content": body,
            "ts": m.get("created_at") or "",
        })
    return out


def _text_of(msg):
    """What was actually said, from either message shape.

    `content` blocks win where present; the top-level `text` is the older
    shape and the fallback. Reading only one of the two drops thousands
    of messages on a long-lived account — see the module docstring.
    """
    parts = []
    content = msg.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in _SPOKEN_BLOCK_TYPES:
                t = (block.get("text") or "").strip()
                if t:
                    parts.append(t)
    if not parts:
        t = (msg.get("text") or "").strip()
        if t:
            parts.append(t)
    body = "\n\n".join(parts).strip()
    # A message that carried only a file would otherwise vanish silently.
    n_files = len(msg.get("attachments") or []) + len(msg.get("files") or [])
    if n_files:
        note = f"[{n_files} attachment{'s' if n_files != 1 else ''}]"
        body = (body + "\n\n" + note).strip() if body else note
    return body


def _zip_member(source_path, filename):
    """Read `filename` out of an export .zip, or None if it isn't one.

    Matched on the BASENAME rather than a fixed path: the export puts
    everything at the top level today and has changed shape before, so a
    future `data/conversations.json` should still be found. Nothing is
    unpacked to disk — the member is read straight out of the archive, so
    pointing Hearthkin at a download leaves the download alone.
    """
    if not zipfile.is_zipfile(source_path):
        return None
    try:
        with zipfile.ZipFile(source_path) as zf:
            for name in zf.namelist():
                if name.rsplit("/", 1)[-1].lower() == filename:
                    return zf.read(name).decode("utf-8", errors="replace")
    except (OSError, zipfile.BadZipFile, KeyError):
        return None
    return None


def export_memory(source_path):
    """Claude's own accumulated memory from an export, or "".

    The download carries a `memories.json` alongside the conversations,
    holding one string of Markdown — what the assistant had come to know
    about the person across every chat. Nothing in Hearthkin had ever
    read it, so importing a history brought the conversations and left
    behind the one file that was already a summary of them.

    Returned as text for the caller to place. It is deliberately NOT
    written into `memory.md`: that sits in the system prompt, and this
    material is third-person prose about the person, written by another
    assistant. A kin's memory is its own first-person reflection, and
    seeding the loudest slot in the prompt with an analyst's register is
    the exact voice erosion the distillation work exists to undo. It
    belongs in a depth log the kin can open when it wants.
    """
    raw = _zip_member(source_path, "memories.json")
    if raw is None:
        # A loose memories.json sitting next to a loose conversations.json.
        sibling = os.path.join(os.path.dirname(source_path), "memories.json")
        if not os.path.isfile(sibling):
            return ""
        try:
            raw = robust_read_text(sibling)
        except OSError:
            return ""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return ""
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return ""
    text = data.get("conversations_memory")
    return text.strip() if isinstance(text, str) else ""


def _load(source_path):
    """The export as a list of conversation dicts. Tolerant of a file
    that is a dict wrapping the list under an obvious key, since exports
    have changed shape before and may again.

    Accepts the `.zip` the download actually is, as well as the
    `conversations.json` inside it. Requiring the unpacked file put a
    manual step in front of every single import that nothing announced —
    and the error it produced talked about JSON, never mentioning zips.
    """
    raw = _zip_member(source_path, "conversations.json")
    if raw is None:
        raw = robust_read_text(source_path)
    data = json.loads(raw)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("conversations", "chats", "data"):
            val = data.get(key)
            if isinstance(val, list):
                return val
    raise ValueError(
        f"{os.path.basename(source_path)} isn't a Claude conversations "
        f"export — expected a list of conversations.")
