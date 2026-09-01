# SPDX-License-Identifier: CC0-1.0

"""
OpenClaw session-store importer.

OpenClaw (the agent runtime several kin lived on before migrating to
Hearthkin) keeps its history as a *folder* of per-session JSONL files
under `<agent>/sessions/`, not a single export. One kin's whole life
there is spread across many session files, each an append-only event
stream, plus `.reset.<ts>Z` and `.deleted.<ts>Z` archived copies of
sessions that were reset or deleted — so the same message often appears
in several files.

This parser takes any one file inside that folder (or the folder
itself) and reconstructs the whole life from it:

  * enumerate every `*.jsonl*` session file in the folder,
  * read every `type == "message"` event,
  * **dedupe by the event's `id`** (the reset/deleted copies collapse
    into their live originals — a message id is unique per message),
  * sort by timestamp into one continuous conversation,
  * convert to Hearthkin's canonical shape.

Event-stream shape (one JSON object per line):

    {"type": "session", "version": 3, "id": "...", "timestamp": "...Z"}
    {"type": "model_change", ...}
    {"type": "message", "id": "e71043f1", "parentId": "...",
     "timestamp": "2026-03-31T19:06:25.839Z",
     "message": {"role": "user"|"assistant"|"toolResult",
                 "content": [{"type": "text", "text": "..."}, ...]}}

Only `role: user` / `role: assistant` message events become turns.
`toolResult` events, tool_use/tool_result content blocks, and OpenClaw's
injected system/status/tool-output-as-user turns are dropped — the
import is the human-and-kin conversation, not the machinery around it.

OpenClaw ran kin over Telegram, so user turns carry an embedded
"Conversation info (untrusted metadata)" / "Sender (untrusted
metadata)" fenced JSON preamble. We pull the real sender name out of
it for `speaker` / `sender_attribution` (group context is preserved
that way) and strip the preamble from the message body.

A human sender can also be the kin. Every message event already carries
an authoritative `role` from the moment it actually happened — OpenClaw
tagged `assistant` on the agent's own generated replies and `user` on
everything a human typed, live, at the time. That's reliable in a way
Skype's structural guesses never were, so `kin_display_name` was never
checked against it at all — the folder's own original agent turns
always became the imported kin, unconditionally.

That default is right for the overwhelmingly common case (this folder
IS one specific kin's whole life). It breaks the moment `kin_display_name`
names a HUMAN sender who appears inside this same session-store — the
identical situation the Skype fixes exist for: an account that is itself
a kin's own historical voice, not a separate person talking to one.
Confirmed against a real archive: a recurring human sender's own turns
stayed `role=user` no matter what was picked, while the folder's actual
agent got silently relabeled with that same person's name — producing
one name claiming two contradictory roles in the same conversation.

Fix: if `kin_display_name` matches a human sender who genuinely appears
in this session-store's `user`-role turns, THAT sender's own turns
become the kin (`role=assistant`), and the folder's original agent turns
demote to `role=user` under a plain, honest placeholder — no specific
name for "whichever agent originally ran this session" is ever recorded
in OpenClaw's own data, so nothing tries to invent one. If
`kin_display_name` doesn't match any human sender present, the original,
unconditional default is unchanged.

Design doc: docs/design/history-import.md.
"""

import glob
import json
import os
import re

from tools._io import robust_read_text


# ─── Detection ────────────────────────────────────────────────────── #

def detect(text):
    """Return True if `text` is the start of an OpenClaw session file.

    Light-touch: the very first events of every OpenClaw session are a
    `{"type": "session", ...}` line, and message events are
    `{"type": "message", ..., "message": {"role": ...}}`. Either shape
    in the first ~4 KB is a confident match."""
    head = text[:4096]
    if '"type": "session"' in head and '"version"' in head:
        return True
    # A raw session .jsonl that was sliced (starts mid-stream) still
    # shows the nested message envelope, which nothing else produces.
    if '"type": "message"' in head and '"message"' in head and '"role"' in head:
        return True
    return False


def detect_path(source_path):
    """Path-based detection for the folder / index cases the raw-text
    detector can't see. True when:
      * source_path is a directory containing OpenClaw session files, or
      * source_path is `sessions.json` sitting beside session files, or
      * source_path is a `.jsonl` whose head passes `detect`.
    """
    try:
        if os.path.isdir(source_path):
            return bool(_session_files(source_path))
        base = os.path.basename(source_path).lower()
        if base in ("sessions.json", "sessions.json.bak"):
            folder = os.path.dirname(source_path)
            return bool(_session_files(folder))
        if ".jsonl" in base:
            with open(source_path, "r", encoding="utf-8", errors="replace") as f:
                return detect(f.read(4096))
    except OSError:
        return False
    return False


# ─── Parse ────────────────────────────────────────────────────────── #

def parse(source_path, kin_display_name, **_opts):
    """Reconstruct a kin's whole OpenClaw life from any file inside its
    sessions folder (or the folder itself). Returns
    (canonical_messages, source_label, fmt).

    `kin_display_name` routes role: the kin's own turns are already
    role=assistant in the source, so the name only matters for labelling
    the assistant `speaker`; every non-kin speaker keeps its own name.
    """
    folder = source_path if os.path.isdir(source_path) else os.path.dirname(source_path)
    files = _session_files(folder)
    if not files:
        folder, files = _descend_to_sessions(folder)

    # Union every message event across every session file (live +
    # .reset + .deleted), deduped by the event id. A message id is
    # unique per message, so the archived copies collapse into their
    # originals and nothing is counted twice. Events with no id (should
    # not happen for real messages) fall back to a content+ts key.
    seen = {}
    for fp in files:
        for ev in _iter_message_events(fp):
            key = ev.get("id") or _fallback_key(ev)
            if key in seen:
                continue
            seen[key] = ev

    events = list(seen.values())
    # Sort by timestamp; events without one sink to the front in stable
    # order so they don't jump the timeline.
    events.sort(key=lambda e: e.get("timestamp") or "")

    # Does kin_display_name pick out a specific HUMAN sender who actually
    # appears in this session-store, rather than referring to the
    # folder's own original agent? One pass, computed before conversion,
    # because the answer changes how BOTH roles get handled below — see
    # "A human sender can also be the kin" in the module docstring.
    kin_lower = (kin_display_name or "").strip().lower()
    user_sender_is_kin = False
    if kin_lower:
        for ev in events:
            msg = ev.get("message") or {}
            if msg.get("role") != "user":
                continue
            sender = _sender_from_metadata(_text_from_blocks(msg.get("content")))
            if sender and sender.strip().lower() == kin_lower:
                user_sender_is_kin = True
                break

    canonical = []
    for ev in events:
        c = _message_to_canonical(
            ev, kin_display_name, user_sender_is_kin=user_sender_is_kin)
        if c is not None:
            canonical.append(c)

    if not canonical:
        raise ValueError(
            f"No conversational turns survived cleanup across "
            f"{len(files)} OpenClaw session file(s) in {folder!r} "
            f"(all were tool/system/empty events)."
        )

    return canonical, "openclaw", "openclaw"


def _descend_to_sessions(folder):
    """Handle being pointed at an OpenClaw ROOT or an agent folder.

    Sessions live at ``<root>/agents/<Name>/sessions/*.jsonl``, three levels
    down. The obvious thing to hand this importer is the ``.openclaw`` folder
    itself -- it is what the module docstring calls "the folder" -- and the
    old behaviour was to report that there was nothing there at all. That
    reads as a broken importer rather than "you are two levels too high",
    which is exactly how it was read.

    Returns ``(folder, files)``. Raises with something useful otherwise.

    Deliberately NOT a recursive glob. A root holds several agents, and
    sweeping them all together would merge different companions' histories
    into one kin -- silently, and worse than the error it replaced. When
    there is a choice to make, this makes the caller make it.
    """
    # An agent folder: <agent>/sessions/
    direct = os.path.join(folder, "sessions")
    if os.path.isdir(direct):
        files = _session_files(direct)
        if files:
            return direct, files

    # A root: <root>/agents/<Name>/sessions/
    agents_dir = os.path.join(folder, "agents")
    if os.path.isdir(agents_dir):
        found = []
        for name in sorted(os.listdir(agents_dir)):
            sess = os.path.join(agents_dir, name, "sessions")
            if os.path.isdir(sess) and _session_files(sess):
                found.append((name, sess))
        if len(found) == 1:
            return found[0][1], _session_files(found[0][1])
        if len(found) > 1:
            names = ", ".join(n for n, _ in found)
            raise ValueError(
                f"{folder!r} holds {len(found)} OpenClaw agents: {names}. "
                f"Point at one of them (or at its sessions folder) -- "
                f"importing them together would merge different histories "
                f"into a single kin."
            )

    raise ValueError(
        f"No OpenClaw session files found in {folder!r}. Sessions live in "
        f"<agent>/sessions/*.jsonl; point at an agent folder, its sessions "
        f"folder, or the OpenClaw root that contains agents/."
    )

# ─── Session-file enumeration ─────────────────────────────────────── #

def _session_files(folder):
    """Every OpenClaw session stream in `folder`: the live `*.jsonl`
    plus its `.reset.*` / `.deleted.*` archived copies. Excludes the
    `sessions.json` index (not a message stream)."""
    out = []
    for p in glob.glob(os.path.join(folder, "*.jsonl*")):
        base = os.path.basename(p).lower()
        if base.startswith("sessions.json"):
            continue
        if os.path.isfile(p):
            out.append(p)
    return sorted(out)


def _iter_message_events(path):
    """Yield every `type == "message"` event dict from one session
    file. Tolerant of blank lines and any non-JSON garbage (a partially
    written last line, say) — those are skipped, not fatal."""
    try:
        text = robust_read_text(path)
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or '"type"' not in line:
            continue
        try:
            ev = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(ev, dict) and ev.get("type") == "message":
            yield ev


# ─── Per-message conversion ───────────────────────────────────────── #

def _message_to_canonical(ev, kin_display_name, *, user_sender_is_kin=False):
    """Convert one OpenClaw message event to canonical shape, or None
    if it should be dropped (tool/system/empty).

    `user_sender_is_kin` — computed once per parse() call, not here —
    is True when `kin_display_name` matched a real human sender found
    somewhere in this session-store's user-role turns. When it's True,
    the roles below are handled the opposite of the ordinary default:
    that specific sender's turns become the kin, and the folder's
    original agent turns demote to role=user (a human sender can also
    be the kin — see the module docstring)."""
    msg = ev.get("message") or {}
    role = msg.get("role")
    if role not in ("user", "assistant"):
        return None  # toolResult / system / anything else — drop

    text = _text_from_blocks(msg.get("content"))
    ts = _normalize_ts(ev.get("timestamp"))

    if role == "user":
        # Strip OpenClaw's injected metadata preamble, pull the sender
        # out of it, and drop anything that's pure machinery.
        speaker = _sender_from_metadata(text) or _DEFAULT_USER_SPEAKER
        body = _strip_metadata_preamble(text).strip()
        # Both run BEFORE the noise check: the queue wrapper hides real
        # words behind chrome, and a cron prompt is real history that
        # simply isn't the person's.
        body = _unwrap_queued(body)
        body, cron_name = _cron_prompt(body)
        if not body or _is_injected_noise(body):
            return None
        if cron_name:
            return {
                "role": "user",
                "content": body,
                "ts": ts,
                "speaker": CRON_SPEAKER,
                "sender_attribution": "%s: %s" % (CRON_SPEAKER, cron_name),
                "source": "import:openclaw",
            }
        kin_lower = (kin_display_name or "").strip().lower()
        if kin_lower and speaker.strip().lower() == kin_lower:
            # This human sender IS the kin being imported, not somebody
            # talking to it — their own words, filed in their own slot.
            return {
                "role": "assistant",
                "content": body,
                "ts": ts,
                "speaker": kin_display_name,
                "source": "import:openclaw",
            }
        return {
            "role": "user",
            "content": body,
            "ts": ts,
            "speaker": speaker,
            # Bare, like live capture stores it — the reading surface adds the
            # bracket (chat_helpers.speaker_attribution_prefix).
            "sender_attribution": speaker,
            "source": "import:openclaw",
        }

    # role == "assistant": the folder's own original agent reply.
    body = text.strip()
    if not body or _is_control_reply(body):
        return None
    if user_sender_is_kin:
        # kin_display_name pointed at a specific human sender found
        # elsewhere in this same session-store, so these are a
        # DIFFERENT voice, not the kin being imported — keep them,
        # correctly attributed, instead of silently merging two
        # identities under one contradictory name. No specific name for
        # "whichever agent originally ran this session" is ever
        # recorded in OpenClaw's own data, so this doesn't invent one.
        return {
            "role": "user",
            "content": body,
            "ts": ts,
            "speaker": _ORIGINAL_AGENT_LABEL,
            "sender_attribution": _ORIGINAL_AGENT_LABEL,
            "source": "import:openclaw",
        }
    return {
        "role": "assistant",
        "content": body,
        "ts": ts,
        "speaker": kin_display_name,
        "source": "import:openclaw",
    }


# ─── Content-block flattening ─────────────────────────────────────── #

def _text_from_blocks(content):
    """Flatten a message's `content` to plain text. String content is
    returned as-is; a block list keeps only `text` blocks (tool_use /
    tool_result blocks are the machinery and are dropped)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for b in content:
        if isinstance(b, dict):
            if b.get("type") == "text":
                parts.append(b.get("text", ""))
        elif isinstance(b, str):
            parts.append(b)
    return "\n".join(p for p in parts if p)


# ─── OpenClaw / Telegram metadata handling ────────────────────────── #

# The fenced metadata blocks OpenClaw prepends to Telegram-sourced user
# turns. Non-greedy so back-to-back blocks each match rather than one
# swallowing everything between the first and last fence.
_META_BLOCK = re.compile(
    r"(?:Conversation info|Sender)\s*\(untrusted metadata\):\s*```(?:json)?\s*.*?```",
    re.DOTALL,
)
_SENDER_IN_META = re.compile(r'"sender"\s*:\s*"([^"]+)"')
_NAME_IN_META = re.compile(r'"name"\s*:\s*"([^"]+)"')
_LEADING_SYSTEM = re.compile(r"^\s*System:\s?", re.MULTILINE)
# OpenClaw stamps every inbound turn with the sender's local wall clock:
# "[Sat 2026-03-21 16:05 UTC] ". Dropped, because it is the harness
# talking, not the person — and because a wrapper repeated on every
# single user turn is the shape this project already knows destabilises
# a model: saturate the context with a pattern and it starts producing
# the pattern. The turn's real time is preserved in `ts`, which is where
# the rest of Hearthkin looks for it.
_LEADING_LOCAL_STAMP = re.compile(
    r"^\s*\[(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\d{4}-\d{2}-\d{2}\s+"
    r"\d{1,2}:\d{2}(?::\d{2})?\s*[A-Z]{2,5}\]\s*")

# Fallback speaker for a human turn that carries no sender metadata (a
# plain DM turn OpenClaw didn't tag). Generic on purpose — no person is
# named in this general-purpose importer.
_DEFAULT_USER_SPEAKER = "User"

# Placeholder for the folder's original agent turns when a human sender
# has taken over the kin identity for this import (see
# "A human sender can also be the kin" in the module docstring).
# Deliberately generic — OpenClaw's own data never records a name for
# "whichever agent ran this session," so nothing here invents one.
_ORIGINAL_AGENT_LABEL = "the agent this session originally ran"


def _sender_from_metadata(text):
    """Pull the human sender's display name out of the untrusted-metadata
    preamble, as a single-token-safe name for the attribution bracket.
    Returns None when there's no metadata (a plain DM turn with no
    injected block).

    No person-specific mapping lives here: the name is taken as the
    source recorded it. Consolidating one sender who appears under several
    handles is a choice for whoever runs the import, not something this
    parser should hard-code."""
    m = _SENDER_IN_META.search(text) or _NAME_IN_META.search(text)
    if not m:
        return None
    # Drop any "(parenthetical)" and collapse spaces so the name is a
    # single token the attribution bracket / hand-authored round-trip can
    # carry.
    raw = re.sub(r"\s*\(.*?\)\s*", "", m.group(1)).strip()
    return re.sub(r"\s+", "", raw) or None


def _strip_metadata_preamble(text):
    """Remove the fenced metadata blocks and any leading `System:`
    prefixes, leaving the actual message the human sent."""
    out = _META_BLOCK.sub("", text)
    out = _LEADING_SYSTEM.sub("", out)
    out = _LEADING_LOCAL_STAMP.sub("", out.lstrip())
    return out


# ─── Noise filters ────────────────────────────────────────────────── #

# Assistant control replies that are protocol chatter, not conversation.
_CONTROL_REPLIES = {"HEARTBEAT_OK", "OK", "ACK"}


def _is_control_reply(body):
    return body.strip() in _CONTROL_REPLIES


_NOISE_PREFIXES = (
    "OpenClaw status",
    "Conversation info (untrusted metadata)",  # a preamble with no body after it
)


def _is_injected_noise(body):
    """True for user turns that are actually machinery OpenClaw fed back
    in as user role: tool-result JSON blobs, status dumps, bare command
    echoes. Conservative — only drops clear machine output, never real
    text."""
    s = body.strip()
    if not s:
        return True
    for pre in _NOISE_PREFIXES:
        if s.startswith(pre):
            return True
    # A body that is entirely a JSON object/array is a tool result, not
    # something a person typed.
    if s[0] in "{[":
        try:
            json.loads(s)
            return True
        except (ValueError, TypeError):
            pass
    return False


# ─── User-turn chrome: queue wrappers and scheduler prompts ───────── #

# `[Queued messages ...]` and `[Queued announce messages ...]` are the same
# chrome; matching only the first spelling left the variant importing with a
# machine label on the front, which is the bug this whole helper exists for.
_QUEUED_WRAPPER_RE = re.compile(
    r"^\s*\[Queued(?:\s+\w+)?\s+messages while agent was busy\]\s*\n+", re.I)
# Inside the wrapper each queued item is introduced by a rule and a
# "Message #N" header. Both are chrome, not something anyone typed.
_QUEUED_CHROME_RE = re.compile(r"^(?:-{3,}|\w+ #\d+)\s*$", re.M)

_CRON_PREFIX_RE = re.compile(r"^\s*\[cron:[0-9a-fA-F-]{8,}\s+([^\]]+)\]\s*")

# What a scheduler-generated turn is attributed to, instead of the person.
CRON_SPEAKER = "scheduled wake-up"


def _unwrap_queued(body):
    """Strip OpenClaw's "queued while busy" wrapper, keeping what was queued.

    When a message arrives while the agent is working, OpenClaw wraps it: a
    `[Queued messages while agent was busy]` line, a rule, a `Message #N`
    header, then the text. The text is genuinely the person's -- only the
    wrapper is machinery -- so this removes the chrome and keeps the words
    rather than importing the turn with a machine label on the front.

    A wrapper can hold more than one queued message. They stay in a single
    turn: they share one timestamp, so splitting them would invent an
    ordering the source does not record.
    """
    if not _QUEUED_WRAPPER_RE.match(body or ""):
        return body
    rest = _QUEUED_WRAPPER_RE.sub("", body, count=1)
    rest = _QUEUED_CHROME_RE.sub("", rest)
    return re.sub(r"\n{3,}", "\n\n", rest).strip()


def _cron_prompt(body):
    """`(body_without_prefix, schedule_name)` for a scheduled wake-up.

    A cron turn is stored in the user role, but nobody typed it -- it is the
    scheduler talking on a timer. Importing it as the person's own words
    puts sentences in their mouth; dropping it leaves the kin's reply
    hanging with nothing before it. So it is kept and attributed to the
    scheduler instead, and the opaque `[cron:<uuid> ...]` prefix comes off,
    because the attribution now says the same thing legibly.

    Returns `(body, None)` when this is not a cron turn.
    """
    m = _CRON_PREFIX_RE.match(body or "")
    if not m:
        return body, None
    return _CRON_PREFIX_RE.sub("", body, count=1).strip(), m.group(1).strip()


# ─── Timestamp ────────────────────────────────────────────────────── #

def _normalize_ts(ts_raw):
    """OpenClaw timestamps are ISO-8601 UTC with a trailing Z and
    subsecond fraction (2026-03-31T19:06:25.839Z). Strip both to match
    the plain 'YYYY-MM-DDTHH:MM:SS' shape used everywhere on disk.
    Returns None on anything unparseable (the writer stamps a fallback)."""
    if not isinstance(ts_raw, str) or not ts_raw:
        return None
    s = ts_raw.strip()
    if s.endswith("Z"):
        s = s[:-1]
    if "." in s:
        s = s.split(".", 1)[0]
    if "+" in s:  # a +HH:MM offset — drop it, keep the wall-clock stamp
        s = s.split("+", 1)[0]
    return s or None


def _fallback_key(ev):
    """Dedup key for the rare message event with no id: role + timestamp
    + a content prefix. Good enough to collapse identical archived copies
    without conflating genuinely distinct turns."""
    msg = ev.get("message") or {}
    body = _text_from_blocks(msg.get("content"))[:200]
    return f"{msg.get('role')}|{ev.get('timestamp')}|{body}"
