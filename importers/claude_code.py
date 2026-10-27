# SPDX-License-Identifier: CC0-1.0

"""
Claude Code session-transcript parser.

Claude Code keeps every session as a JSON-lines file under
`~/.claude/projects/<slugged-cwd>/<session-uuid>.jsonl`. One line per
event, written as it happens:

    {"type":"user","message":{"role":"user","content":"..."},
     "timestamp":"...","sessionId":"...","cwd":"...","gitBranch":"...",
     "isSidechain":false,"isMeta":false,"origin":{"kind":"human"}}
    {"type":"assistant","message":{"role":"assistant","content":[
       {"type":"thinking",...},{"type":"text","text":"..."},
       {"type":"tool_use",...}]}, ...}

Other line types appear and are not conversation: `system`, `attachment`,
`mode`, `queue-operation`, `ai-title`, `custom-title`, `last-prompt`.

**The `user` role is not the same thing as the person.** This is the one
trap in the format and it is a bad one. Claude Code writes tool results
back into the transcript wearing the user role, because that is how the
model is shown them. Across a 14-file sample of one real archive, 6,676
lines were `type: "user"` and **5,617 of them carried nothing but
`tool_result` blocks** — file contents, command output, error text. Only
1,029 were the person actually typing. A parser that trusts the role
alone imports a kin's history as 84% machine output, and it looks like
it worked.

So a line has to survive four checks to count as something the person
said:

  * `isSidechain` false. A sidechain is a subagent's own conversation.
    Its `user` turns are prompts the agent wrote, not the person.
  * `isMeta` false. Meta lines are harness bookkeeping.
  * `origin.kind` is `human`, when `origin` is present at all. Newer
    lines state it outright — `{"kind": "human"}` versus
    `{"kind": "task-notification"}`, which is a background event wearing
    the user role. Older lines have no `origin` field, so its absence
    cannot disqualify a line; only a stated non-human value can.
  * the content is prose, not a `tool_result` block and not one of the
    wrappers the harness injects into the user slot: `<system-reminder>`,
    `<command-…>`, `<local-command-…>`, and the `Caveat:` preamble.

Assistant lines get the same treatment from the other side: only `text`
blocks are speech. `thinking` is the model talking to itself and
`tool_use` is machinery, and on the same sample they outnumbered the
words.

**One file is one session**, unlike the claude.ai export where one file
is a whole account. A session opens with a header line naming the folder
it ran in and the branch, because a transcript read months later is
otherwise unplaceable — the work is the subject, and the subject is which
project it was.

Design doc: docs/design/history-import.md.
"""

import json
import os

from tools._io import robust_read_text

# Block types that are things somebody actually said. Everything else is
# machinery — see the module docstring.
_SPOKEN_BLOCK_TYPES = frozenset({"text"})

# Wrappers the harness injects into the user slot. None of them were
# typed by the person, and all of them are prose-shaped enough that a
# naive reader keeps them.
_INJECTED_PREFIXES = (
    "<system-reminder",
    "<command-",
    "<local-command",
    "Caveat: The messages below were generated",
)

_ROLE_BY_TYPE = {
    "user": "user",
    "assistant": "assistant",
}


# ─── Detection ────────────────────────────────────────────────────── #

def detect(text):
    """True when `text` looks like a Claude Code session transcript.

    `sessionId` alongside `parentUuid` is the signature: the pairing is
    unique to this format among everything this package reads. Checking
    for `"type":"user"` as well would be tempting and wrong — a
    transcript can open with any event type, and one that begins with a
    `mode` or `queue-operation` line is ordinary.

    Cannot collide with its line-oriented neighbours. OpenClaw wants
    `"type": "session"` or `"type": "message"`, which this format never
    writes, and Hearthkin's own jsonl carries `role` at the top level
    where this one nests it inside `message`.
    """
    head = (text or "")[:65536]
    return '"sessionId"' in head and '"parentUuid"' in head


def detect_path(source_path):
    """True for a `.jsonl` that turns out to be a session transcript."""
    if not source_path.lower().endswith(".jsonl"):
        return False
    try:
        return detect(robust_read_text(source_path))
    except Exception:
        return False


# ─── Parse ────────────────────────────────────────────────────────── #

def parse(source_path, kin_display_name, **_opts):
    """Canonical messages from one Claude Code session transcript.

    `kin_display_name` is accepted for signature compatibility and
    deliberately unused — every line states its own role, and guessing a
    role a file already states is how a kin ends up with zero assistant
    turns.

    Returns `(canonical_messages, source_label, fmt)`, the contract every
    sibling parser uses.
    """
    rows = _load(robust_read_text(source_path))
    msgs = []
    for row in rows:
        role = _ROLE_BY_TYPE.get(row.get("type"))
        if role is None:
            continue
        if row.get("isSidechain") or row.get("isMeta"):
            continue
        if role == "user" and not _is_person(row):
            continue
        body = _text_of(row, role)
        if not body:
            continue
        msgs.append({
            "role": role,
            "content": body,
            "ts": row.get("timestamp") or "",
        })

    if not msgs:
        raise ValueError(
            "Nothing importable in this Claude Code transcript — every "
            "line was machinery rather than conversation.")

    header = _header(rows, source_path)
    if header:
        msgs.insert(0, {
            "role": "system",
            "content": header,
            "ts": msgs[0].get("ts") or "",
        })
    return msgs, "claude_code", "claude_code"


# ─── Helpers ──────────────────────────────────────────────────────── #

def _load(text):
    """Every parseable object in the file, in file order.

    A transcript is appended to live, so the last line of a session that
    ended badly can be a half-written object. One unreadable line is not
    a reason to refuse the other several thousand.
    """
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _is_person(row):
    """True when a `user` line is the person typing.

    See the module docstring: the user role is also where tool results
    and harness notifications are written, and on real data those are
    the large majority of it.
    """
    origin = row.get("origin")
    if isinstance(origin, dict):
        kind = (origin.get("kind") or "").strip().lower()
        # Stated non-human origin disqualifies. A missing `origin` does
        # not — older transcripts have no such field at all.
        if kind and kind != "human":
            return False
    return True


def _text_of(row, role):
    """What was actually said on this line, or '' if nothing was.

    `content` is a plain string on most typed turns and a block list
    everywhere else. Only `text` blocks are speech.
    """
    content = (row.get("message") or {}).get("content")

    if isinstance(content, str):
        body = content.strip()
        if role == "user" and _is_injected(body):
            return ""
        return body

    if not isinstance(content, list):
        return ""

    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") not in _SPOKEN_BLOCK_TYPES:
            continue
        text = (block.get("text") or "").strip()
        if not text:
            continue
        if role == "user" and _is_injected(text):
            continue
        parts.append(text)
    return "\n\n".join(parts).strip()


def _is_injected(text):
    """True for the wrappers the harness writes into the user slot."""
    stripped = text.lstrip()
    return any(stripped.startswith(p) for p in _INJECTED_PREFIXES)


def _header(rows, source_path):
    """One line saying where this session ran.

    A transcript is named by a uuid and its subject is the project it
    was in, so without this a session read later is unplaceable.
    """
    cwd = branch = ""
    for row in rows:
        cwd = cwd or (row.get("cwd") or "")
        branch = branch or (row.get("gitBranch") or "")
        if cwd and branch:
            break
    if not cwd:
        return ""
    where = os.path.basename(cwd.rstrip("\\/")) or cwd
    if branch:
        where = f"{where} ({branch})"
    return f"[hearthkin: imported Claude Code session — {where}]"
