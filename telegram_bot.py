# SPDX-License-Identifier: CC0-1.0

"""
telegram_bot — per-kin Telegram polling-and-inference bot.

Extracted from hearthkin.pyw as the first step of the
modularization pass. The class itself was already well-bounded: it
takes config / soul / memory / model getters as callbacks at
construction time and exposes start() / stop() / status_label() —
zero direct references to wx, no hearthkin frame internals.

Module-level helpers live here too: telegram_api_call (the one-shot
HTTP request used everywhere a Telegram call is made),
telegram_test_token (the "Test token" button in Settings), and the
on-disk persistence for per-Telegram-user message histories. The
persistence helpers used to live in hearthkin.pyw but they're
only used by this bot, so they belong with it.

Hearthkin helpers used here (load_agent_config, build_system_prompt,
append_failure_log, agent_dir, atomic_write_json) are imported lazily
inside the methods that need them. That avoids the circular-import
problem of hearthkin.pyw importing this module at its top while
this module would want to import hearthkin.pyw at its top.
Lazy at call time means by the time these run, both modules are
fully loaded.
"""

import json
import re
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import datetime
from dataclasses import dataclass
from pathlib import Path

from hearthkin_paths import kin_dir as kin_folder

try:
    import ollama
except ImportError:
    ollama = None

import llm_backend


# Legacy in-content sender prefix — old group messages used
# `[@username]: text`. Module-level so the regex compiles once
# rather than per-group-message in the hot path (audit T18).
_LEGACY_SENDER_RE = re.compile(r'^\[([^\[\]\n]{1,40})\]:\s*')


# ─── Approval state ─────────────────────────────────────────────────────────

@dataclass
class _PendingApproval:
    """Per-user pending exec approval state. The worker thread that fired
    a gated exec call blocks on `event` until the POLL thread (or the
    in-handler fallback) sees an approval-shaped response from the same
    user and sets the decision + signals the event. Auto-denies on
    timeout."""
    event: threading.Event
    command: str
    args_summary: str
    # Decision is set by the message handler before signalling the event.
    # Default 'deny' so a timeout-or-no-reply path is safe by default.
    decision: str = "deny"
    # Chat the approval prompt was posted to. Resolution messages are
    # accepted from the same user in EITHER this chat or a DM — before
    # this field existed, a group-triggered approval could only time
    # out (the intercept was DM-only and the prompt told the user to
    # reply where replies didn't work — audit M-T1).
    chat_id: int = None


# Natural-language approval responses, in addition to slash commands.
# Case-insensitive. Whitespace stripped before match.
_APPROVE_WORDS = {
    "/allow", "allow", "yes", "y", "ok", "okay", "sure", "approve",
    "go", "do it", "👍", "✅",
}
_DENY_WORDS = {
    "/deny", "deny", "no", "n", "refuse", "cancel", "stop", "nope",
    "don't", "dont", "👎", "❌",
    # Slash forms too — without these, /cancel during a pending
    # approval fell through to the "please respond" nudge instead of
    # denying, making the registered menu command useless exactly when
    # it was needed (audit M-T5).
    "/cancel", "/stop",
}
_REMEMBER_WORDS = {
    "/remember", "remember", "save", "keep", "trust", "trust this",
    "always", "remember this", "save it",
}

# Acknowledgement text sent back when an approval response lands.
# Shared by the poll-thread resolver (the normal path — audit C1) and
# the in-handler fallback so the user sees identical wording either way.
_APPROVAL_ACKS = {
    "allow": "Okay — running the command.",
    "remember": "Okay — running and remembering.",
    "deny": "Denied. The kin will continue without that command.",
}

# What the MODEL is told when a gated command doesn't run. One string per
# outcome — a kin that gets these will describe the situation accurately to
# the operator instead of accusing them of a refusal that never happened.
# The "nobody refused" wording is load-bearing; without it a model reading
# any not-run result tends to narrate it as a denial.
_DENY_RESULTS = {
    "deny": (
        "[denied by the operator — they saw this command and said no. "
        "Don't retry it; if it matters, ask them about it in words.]"
    ),
    "timeout": (
        "[NOT RUN — the approval request timed out with no answer. Nobody "
        "refused it. The operator most likely never saw it (they may have "
        "been away, or in another chat). Say that plainly — do NOT tell "
        "them they denied it. Offer to try again.]"
    ),
    "undelivered": (
        "[NOT RUN — the approval request could not be delivered; the message "
        "failed to send. Nobody refused it and nobody saw it. Tell the "
        "operator the request never reached them, and that this is a "
        "delivery problem on my side, not a decision they made.]"
    ),
    "superseded": (
        "[NOT RUN — this approval request was replaced by a newer one before "
        "it was answered. Nobody refused it.]"
    ),
}


def _classify_approval_text(text):
    """Return 'allow' / 'remember' / 'deny' / None for the user's
    response to an approval prompt. None means the message didn't
    match any approval semantics — the caller should treat as a
    normal chat message (with a nudge telling the user there's a
    pending approval)."""
    if not text:
        return None
    norm = text.strip().lower()
    if norm in _REMEMBER_WORDS:
        return "remember"
    if norm in _APPROVE_WORDS:
        return "allow"
    if norm in _DENY_WORDS:
        return "deny"
    return None


# ─── Module-level helpers ───────────────────────────────────────────────────

class TelegramAPIError(RuntimeError):
    """Raised when a Telegram Bot API call returns a non-2xx response.

    Carries the HTTP status code, the parsed Telegram description, and
    (on 429 responses) the retry_after hint so callers can back off
    intelligently rather than hammering the API. Subclassing RuntimeError
    keeps `except RuntimeError` catches working — existing call sites
    don't need to change unless they want the structured fields.
    """

    def __init__(self, status, description, retry_after=None):
        self.status = status
        self.description = description
        self.retry_after = retry_after
        super().__init__(f"HTTP {status}: {description}")


def telegram_api_call(token, method, params=None, timeout=30):
    """One-shot Telegram Bot API call. Raises TelegramAPIError on API failure
    (a subclass of RuntimeError, so existing `except RuntimeError` catches
    still work; new code can extract .status / .retry_after explicitly)."""
    if not token:
        raise RuntimeError("No bot token")
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = urllib.parse.urlencode(params or {}, doseq=True).encode("utf-8")
    req = urllib.request.Request(url, data=body)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = ""
        retry_after = None
        try:
            body_text = e.read().decode("utf-8", "replace")
            payload = json.loads(body_text)
            desc = payload.get("description", str(e))
            # Telegram's documented 429 shape: `parameters.retry_after`
            # in the response JSON body, in seconds.
            params_obj = payload.get("parameters") or {}
            ra = params_obj.get("retry_after")
            if isinstance(ra, (int, float)):
                retry_after = float(ra)
        except Exception:
            desc = body_text or str(e)
        # HTTP Retry-After header as a fallback for proxies/intermediaries
        # that surface the hint that way.
        if retry_after is None:
            try:
                ra_hdr = e.headers.get("Retry-After") if e.headers else None
                if ra_hdr:
                    retry_after = float(ra_hdr)
            except Exception:
                pass
        raise TelegramAPIError(e.code, desc, retry_after) from e


_BOT_TOKEN_IN_URL_RE = re.compile(r"/bot[A-Za-z0-9:_-]+/")


def _redact_bot_token(s):
    """Replace any embedded bot-token-in-URL with /bot[REDACTED]/ so
    the token doesn't end up in failure logs. urllib's URLError can
    embed the failing URL in str(e); without this scrub a leaked
    log file would also leak the token. Cheap regex, fixed shape."""
    if not isinstance(s, str):
        return s
    return _BOT_TOKEN_IN_URL_RE.sub("/bot[REDACTED]/", s)


def _telegram_file_url(token, file_path):
    return f"https://api.telegram.org/file/bot{token}/{file_path}"


def _download_telegram_file(token, file_id, timeout=30, max_bytes=8 * 1024 * 1024):
    """Resolve a Telegram file_id to its downloadable URL via getFile,
    then download the bytes. Returns (bytes, error_message). Either
    bytes is non-None (success) or error_message is non-None (one of
    the bytes / error pair is set; never both).

    `max_bytes` is the size cap. Telegram's getFile response includes
    `file_size`; we refuse early if it's over the limit (saves a
    download). Servers can lie about file_size or omit it though, so
    we also enforce a hard limit when reading the body.
    """
    if not token:
        return None, "no bot token"
    try:
        resp = telegram_api_call(token, "getFile",
                                 {"file_id": file_id}, timeout=timeout)
    except Exception as e:
        return None, _redact_bot_token(f"getFile failed: {e}")
    result = (resp or {}).get("result") or {}
    file_path = result.get("file_path")
    if not file_path:
        return None, "Telegram returned no file_path"
    declared_size = result.get("file_size")
    if isinstance(declared_size, int) and declared_size > max_bytes:
        mb = declared_size / (1024 * 1024)
        return None, (f"file is {mb:.1f} MB — over the "
                      f"{max_bytes // (1024 * 1024)} MB cap")
    url = _telegram_file_url(token, file_path)
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            # Read up to max_bytes+1 so we can detect an over-cap file
            # whose declared size was missing or wrong.
            data = r.read(max_bytes + 1)
    except Exception as e:
        # Token-containing URL is in `url`; urllib's URLError shapes
        # can embed that URL in str(e). Scrub before returning so
        # the failure-log entry stays safe.
        return None, _redact_bot_token(f"download failed: {e}")
    if len(data) > max_bytes:
        mb = len(data) / (1024 * 1024)
        return None, (f"file is over the {max_bytes // (1024 * 1024)} "
                      f"MB cap (got {mb:.1f} MB)")
    if not data:
        return None, "downloaded empty file"
    return data, None


def telegram_test_token(token):
    """Quick `getMe` check. Returns (ok: bool, msg: str)."""
    if not token:
        return False, "no token"
    try:
        data = telegram_api_call(token, "getMe", timeout=10)
        if data.get("ok"):
            res = data.get("result") or {}
            name = res.get("first_name") or "(bot)"
            username = res.get("username") or "?"
            return True, f"{name} (@{username})"
        return False, data.get("description", "unknown")
    except Exception as e:
        return False, str(e)


def _agent_dir(name):
    """Local copy of hearthkin's agent_dir to keep persistence working
    without a hard import at module load time."""
    return kin_folder(name)


def load_telegram_history(name):
    """Load the kin's per-Telegram-user conversation histories. Returns a
    {user_id_str: [msg, ...]} dict, empty if the file doesn't exist or is
    malformed. Without persistence, every restart would wipe all Telegram
    conversations — a real problem for kin whose primary surface is Telegram."""
    from kin_persistence import _clean_chat_message
    path = _agent_dir(name) / "telegram_history.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("histories") if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            return {}
        clean = {}
        for user_id, msgs in raw.items():
            if not isinstance(msgs, list):
                continue
            kept = []
            for m in msgs:
                entry = _clean_chat_message(m)
                if entry is not None:
                    kept.append(entry)
            if kept:
                clean[str(user_id)] = kept
        return clean
    except Exception:
        return {}


def save_telegram_history(name, histories):
    from kin_persistence import atomic_write_text, now_iso
    d = _agent_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "saved_at": now_iso(),
        # Stringify keys so an int key can never reach json.dumps —
        # int + str forms of the same user id serialize as duplicate
        # JSON keys and json.loads keeps only the last, destroying
        # the older slice (audit DH1).
        "histories": {str(k): v for k, v in (histories or {}).items()},
    }
    # Compact separators, no indent: this file is rewritten in full on
    # every Telegram turn, so pretty-printing multiplies the per-turn
    # serialize + write cost for zero benefit (audit M-T3).
    atomic_write_text(
        d / "telegram_history.json",
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
    )


def seen_group_members(agent_name, chat_id):
    """Everyone who has ever spoken in this group, as [{"id", "name"}, ...],
    harvested from stored history so the operator can pick who to mute without
    hunting for numeric IDs through a third-party bot. Reads straight from
    disk (telegram_history.json for segregated groups, conversation.jsonl for
    share-with-desktop groups) — a running bot isn't required. Sorted by name.

    Every group user turn persists `sender_id` (numeric) + `sender_name`
    (display name) — see _handle_group_message — so the mapping is already
    there; this just gathers and de-duplicates it (keeping the most recent
    non-empty name per id)."""
    seen = {}  # id -> name

    def _note(turn):
        if not isinstance(turn, dict):
            return
        sid = turn.get("sender_id")
        if sid is None:
            return
        try:
            sid = int(sid)
        except (TypeError, ValueError):
            return
        name = (turn.get("sender_name") or "").strip()
        if sid not in seen or (name and not seen.get(sid)):
            seen[sid] = name

    try:
        histories = load_telegram_history(agent_name)
        for turn in histories.get(f"group:{chat_id}") or []:
            _note(turn)
    except Exception:
        pass
    # share-with-desktop groups live in conversation.jsonl, source-tagged.
    try:
        from kin_persistence import load_agent_conversation
        tag = f"telegram:group:{chat_id}"
        for turn in load_agent_conversation(agent_name) or []:
            if isinstance(turn, dict) and turn.get("source") == tag:
                _note(turn)
    except Exception:
        pass

    return sorted(
        ({"id": i, "name": n} for i, n in seen.items()),
        key=lambda d: ((d["name"] or "").lower(), d["id"]),
    )


def _hkey(user_id):
    """Canonical _histories key for a DM user: always the str form.
    Disk load normalizes to str; runtime appends used to key by the
    raw int from Telegram's JSON, so after a restart the int lookup
    missed the str-keyed disk slice and the next save destroyed it
    (audit DH1). Every _histories access for a DM user goes through
    this. Group keys ("group:<id>") are already strings."""
    return str(user_id)


def _drop_leading_orphan_tools(history, *, sweep_trailing_assistant_tcs=True):
    """Sweep `role=tool` messages off the head of `history` until the
    first message is something else. A trim via `history[-cap:]` can
    cut an `assistant tool_calls -> tool result` pair down the middle,
    leaving the tool result at the head with no parent assistant turn
    above it. Anthropic-via-OpenRouter accepts that shape silently;
    Mistral rejects with `Unexpected role 'tool' after role 'system'`
    (the system prompt is always prepended at build time, so an orphan
    tool at history head sends as `system -> tool`). Mutates and
    returns the input list.

    Symmetric defense (`sweep_trailing_assistant_tcs=True`): also
    drops a TRAILING `assistant` with `tool_calls` if the trim ate
    its paired tool results. Without that, the next send shows
    `... assistant(tool_calls) -> user` which some providers also
    reject (missing tool result for an issued call). Walks back from
    the end while the last message is an assistant with tool_calls
    and there's no following tool turn.

    The trailing sweep is gated because the APPEND paths
    (`_append_turns_for`, `_append_group_history`) feed `history`
    AFTER appending `new_turns` whose invariant is that the last
    entry is a content assistant / system / user turn — never an
    orphan assistant_tc. If a future regression ever lands an
    assistant_tc at the tail of `new_turns`, silently dropping it
    here would lose the kin's tool call from disk with no error.
    Append paths pass `sweep_trailing_assistant_tcs=False` so a
    regression surfaces as a Mistral 400 on the NEXT send (which
    the load-time sweep then cleans before the model sees it) —
    loud failure beats silent data loss. Load paths use the
    default True; that's where the trailing-orphan needs to actually
    be removed before send."""
    while history and isinstance(history[0], dict) and history[0].get("role") == "tool":
        history.pop(0)
    if not sweep_trailing_assistant_tcs:
        return history
    # Tail orphan: assistant tool_calls whose tool results were trimmed.
    # The append path adds the new user turn AFTER trim, so an orphan
    # tail assistant would otherwise sit directly before that user.
    while history and isinstance(history[-1], dict):
        last = history[-1]
        if last.get("role") == "assistant" and isinstance(last.get("tool_calls"), list) and last["tool_calls"]:
            content = last.get("content")
            if isinstance(content, str) and content.strip():
                # The turn carries real narrative content alongside the
                # orphaned tool_calls — keep the kin's words, drop only
                # the field providers reject (audit L-B5). Copy rather
                # than mutate: the dict may be shared with cached /
                # on-disk-shaped state.
                kept = dict(last)
                kept.pop("tool_calls", None)
                history[-1] = kept
                break
            history.pop()
        else:
            break
    return history


def _utf16_slice(text, offset, length):
    """Slice `text` by UTF-16 code-unit offset/length. Telegram entity
    offsets count UTF-16 code units (an astral-plane char like most
    emoji is 2 units), so Python code-point slicing drifts after any
    emoji earlier in the text — mention detection misses and
    mention-stripping removes the wrong span (audit M-T2)."""
    if not text:
        return ""
    try:
        b = text.encode("utf-16-le")
        return b[offset * 2:(offset + length) * 2].decode("utf-16-le", "ignore")
    except Exception:
        return text[offset:offset + length]


def _utf16_remove(text, offset, length):
    """Remove the UTF-16 code-unit span [offset, offset+length) from
    `text`. Counterpart of _utf16_slice for _strip_bot_mention."""
    if not text:
        return text
    try:
        b = text.encode("utf-16-le")
        return (b[:offset * 2] + b[(offset + length) * 2:]).decode(
            "utf-16-le", "ignore")
    except Exception:
        return text[:offset] + text[offset + length:]


def _utf16_len(text):
    """Length of `text` in UTF-16 code units — the unit Telegram counts
    entity offsets and its own per-message ceiling in."""
    if not text:
        return 0
    try:
        return len(text.encode("utf-16-le")) // 2
    except Exception:
        return len(text)


# --- Reassembling a message the sender's client split --------------------
#
# Telegram's 4096-code-unit per-message ceiling applies to what a PERSON
# sends just as much as to what a kin sends. Paste a long passage into the
# Telegram client and it quietly chops it into two or three messages, which
# reach the Bot API as two or three separate updates. Handled one at a time
# that means the kin answers the first half before the rest has arrived,
# then answers the orphaned tail — two confused replies to one thought, and
# a stored history that keeps the seam forever.
#
# Nothing in the payload says "part 1 of 2", so the fix is a quiet grace
# window: after a plain-text chat message, wait a beat to see whether more
# text from the same person in the same chat is right behind it, and if so
# treat the pieces as the one message they were meant to be. The window is
# short enough to disappear next to inference time, and it also folds in the
# everyday case of someone thinking out loud across two quick lines.
#
# How long to wait is the person's call, not ours, and there is no way to
# infer it: the Bot API never tells a bot that someone is typing, so "wait
# until they stop" is not available at any price. Someone who composes
# slowly — or who is using a screen reader, or a switch, or one hand — needs
# a longer pause than someone thumbing a phone, and guessing at their pace
# from punctuation would just fail differently for each of them. The default
# below is the fallback; the real value is the per-kin `message_wait_secs`
# setting (Telegram → Message settings), which they set to their own pace.
_COALESCE_WINDOW_SECS = 2.0
# Zero is a legitimate choice ("answer me the instant I hit send"), but it
# must not re-break the split it fixes: a part sitting at the ceiling was cut
# by the client, not finished by the person, so its continuation is always
# waited for. This is that floor, and it's generous — a second 4096-unit
# part can take a moment to reach us on a slow link.
_COALESCE_SPLIT_WINDOW_SECS = 8.0
# Ceiling on the configured wait, so a stray keystroke in the settings field
# can't strand the kin silent for an hour.
_COALESCE_MAX_WAIT_SECS = 600.0
_COALESCE_SPLIT_LEN = 3900
# Poll the queue in short hops inside the window so shutdown stays snappy.
_COALESCE_TICK_SECS = 0.25
# Rails so a burst of messages can't grow one turn without bound.
_COALESCE_MAX_PARTS = 16
_COALESCE_MAX_UNITS = 200000
# At the ceiling the client had no newline or space to break on, so the cut
# can land mid-word and rejoining with a newline would wedge a break into
# the middle of it.
_COALESCE_HARD_CUT_LEN = 4090


def _is_not_modified(err):
    """Is this the Telegram 400 that means "the message already says that"?

    `editMessageText` rejects a no-op edit with 400 Bad Request: "message is
    not modified". It reads like a failure and is the opposite: the text we
    wanted is already on screen. Matched on the message text because that's
    all the API gives us to go on — there is no distinct error code.
    """
    return "not modified" in str(err).lower()


class _TelegramStreamEditor:
    """Renders a streaming kin reply into ONE Telegram message that fills in
    place (OpenClaw-style), instead of a wall of silence until the whole reply
    lands. Wired into run_tool_loop via on_content (feed) + on_turn (reset_turn);
    the caller calls finalize() with the CLEANED reply after its cleanup pipeline.

    Dependencies are injected so the throttle logic is unit-testable without a
    live Telegram:
      send(text) -> message_id | None   — sendMessage, returns the new id
      edit(message_id, text) -> None    — editMessageText
      now() -> float                    — monotonic clock

    Throttle: the message is created on the first content delta, then edited at
    most once per `throttle_secs` — NEVER per token. Telegram rate-limits edits
    hard (we've eaten flood-waits before); interim under-editing is fine because
    finalize() writes the authoritative cleaned text at the end.

    EVERY EDIT ONLY ADDS. The message grows; nothing it has already said is
    ever unsaid. That is the whole discipline here, and it was got wrong:
    reset_turn used to CLEAR the buffer at each tool-loop turn boundary, so a
    kin that said "let me go and look at that file" and then called a tool had
    that sentence OVERWRITTEN by whatever it said afterwards. One message, and
    the first thing in it destroyed.

    Which is a bad shape anywhere and a worse one here. Telegram output is
    append-only precisely because the chat is a historical record and a
    screen reader has already read the earlier version aloud — so the reader
    hears one thing, and what is on the screen afterwards is another, with
    nothing to say it changed. Scrolling back does not recover it; the text is
    simply gone. See CLAUDE.md, "Telegram output is append-only".

    So a turn boundary now BANKS the buffer instead of dropping it, and
    finalize writes the banked text plus the cleaned final turn. The reply
    still fills in live; it just never takes anything back. All send/edit calls
    are best-effort; a rendering hiccup must never break the tool loop or the
    caller's own send."""

    def __init__(self, send, edit, *, throttle_secs=3.0, max_len=4000,
                 now=None, clean=None):
        import time as _t
        self._send = send
        self._edit = edit
        self._throttle = throttle_secs
        self._max_len = max_len
        self._now = now or _t.monotonic
        # Text from talking turns that have already ENDED. Banked rather than
        # discarded, so a tool call in the middle of a reply cannot erase what
        # the kin said before it. Rendered ahead of the live buffer.
        # Optional cleanup applied to what is DISPLAYED, so the interim text
        # matches the shape the finished message will have. Without it the
        # only way to end up clean is to shrink at the end: a model opening
        # with its own name tag would show that tag live and then have it
        # edited away, which is taking something back — the one thing this
        # class must not do. Injected rather than imported so the editor keeps
        # knowing nothing about kin or impersonation rules.
        self._clean = clean
        self._kept = ""
        self._buf = ""
        self._msg_id = None
        self._last_edit_at = 0.0
        self._dirty = False
        # The text the streamed message actually holds right now. Two jobs.
        # (1) Skip an edit that wouldn't change anything: once a reply grows
        # past max_len every interim flush writes the SAME truncated head, so
        # a long reply spent one pointless API call per throttle tick, burning
        # the rate-limit budget that the finalize edit then needs.
        # (2) Recognise the same situation in finalize, where it used to cost
        # the reader a duplicate — see _is_not_modified.
        self._written = None

    def _render(self):
        """Everything said so far: banked turns, then the live buffer, shaped
        the way the finished message will be. Never raises — a cleanup fault
        must cost the tidying, never the reply."""
        raw = self._kept + self._buf
        if self._clean is None:
            return raw
        try:
            return self._clean(raw)
        except Exception:
            return raw

    def feed(self, text):
        """Append a content delta; create the message on first content, then
        edit it on the throttle."""
        if not text:
            return
        self._buf += text
        self._dirty = True
        if self._msg_id is None:
            first = self._render()[:self._max_len] or "…"
            try:
                self._msg_id = self._send(first)
                self._written = first
            except Exception:
                self._msg_id = None
            self._last_edit_at = self._now()
            self._dirty = False
            return
        if (self._now() - self._last_edit_at) >= self._throttle:
            self._flush()

    def _flush(self):
        if self._msg_id is None or not self._dirty:
            return
        text = self._render()[:self._max_len] or "…"
        # Nothing new to show. Past max_len this is EVERY tick — the head
        # stopped changing while the reply kept growing — and Telegram answers
        # a no-op edit with 400 "message is not modified", so this was pure
        # rate-limit spend for no visible change.
        if text == self._written:
            self._last_edit_at = self._now()
            self._dirty = False
            return
        try:
            self._edit(self._msg_id, text)
            self._written = text
        except Exception:
            pass
        self._last_edit_at = self._now()
        self._dirty = False

    def reset_turn(self):
        """A talking turn has ENDED — bank what it said and start a fresh
        buffer.

        Banked, not dropped. This used to discard the buffer, which is what
        made a tool call erase the sentence in front of it. Nothing the reader
        has already been shown may be taken away."""
        said = self._buf.strip()
        if said:
            self._kept = (self._kept + said).strip() + "\n\n"
        self._buf = ""
        self._dirty = self._msg_id is not None

    def finalize(self, final_text):
        """Write the caller's CLEANED final reply into the streamed message.
        If nothing was ever streamed (no content produced), behave like a
        normal send. Overflow past max_len edits the head into the streamed
        message and sends the remainder as follow-on messages. Returns True
        when this handled the send (caller should NOT also send), False when
        there was nothing to send.

        `final_text` is the cleaned LAST talking turn. Anything banked from
        earlier turns is kept in front of it, so the finished message reads as
        the whole reply — the sentence before the tool call, and the answer
        after it — rather than the caller's cleanup silently deleting the
        first half. When nothing was ever streamed there is nothing banked and
        this behaves exactly as it always did."""
        final_text = (final_text or "")
        if final_text and self._kept:
            final_text = self._kept + final_text
        elif self._kept and not final_text:
            # Cleanup ate the last turn entirely. What the kin said before the
            # tool call is still real and still already on screen; leaving it
            # would mean editing it away, which is the exact thing this class
            # must not do.
            final_text = self._kept.strip()
        if self._msg_id is None:
            if final_text:
                try:
                    self._send(final_text[:self._max_len])
                except Exception:
                    return False
                rest = final_text[self._max_len:]
                while rest:
                    try:
                        self._send(rest[:self._max_len])
                    except Exception:
                        break
                    rest = rest[self._max_len:]
                return True
            return False
        head = final_text[:self._max_len] or "…"
        if head != self._written:
            try:
                self._edit(self._msg_id, head)
                self._written = head
            except Exception as e:
                if _is_not_modified(e):
                    # Not a failure: Telegram is telling us the message ALREADY
                    # says exactly this. Treating it as one is what produced
                    # duplicate replies — the caller's fallback re-sent the
                    # whole thing, so the reader got the streamed copy plus a
                    # complete second copy. It fired on every reply that ended
                    # right on a throttle tick, and on every reply past
                    # max_len, where the head had stopped changing long before
                    # the end. Nothing to do; the text is already there.
                    self._written = head
                else:
                    # A real failure — rate-limited, network, deleted message.
                    # Return False so the CALLER re-sends the COMPLETE reply
                    # through its robust _send_chunked path (429 backoff).
                    # Better a possibly-duplicated full reply than a message
                    # frozen mid-sentence.
                    return False
        rest = final_text[self._max_len:]
        while rest:
            try:
                self._send(rest[:self._max_len])
            except Exception:
                return False
            rest = rest[self._max_len:]
        return True


# ─── The bot ────────────────────────────────────────────────────────────────

class TelegramBot:
    """Per-agent Telegram bot. Polling thread is decoupled from the inference
    worker so a slow local model can never stall message intake."""

    STATUS_OFF = "Off"
    STATUS_CONNECTING = "Connecting..."
    STATUS_RUNNING = "Running"
    STATUS_ERROR = "Error"

    HISTORY_CAP = 100  # messages per Telegram user before trimming oldest
    # (default; per-kin override via cfg.telegram.history_cap — see
    # _history_cap below, audit T21).

    def _history_cap(self):
        """Effective per-user / per-group history trim cap. Reads
        cfg.telegram.history_cap; falls back to the class default
        (100). 0 disables trimming entirely. Sanity floor of 10 so a
        misconfigured zero-or-tiny value can't strangle context."""
        cfg = self.get_config() or {}
        try:
            val = cfg.get("history_cap", self.HISTORY_CAP)
            if val is None:
                return self.HISTORY_CAP
            val = int(val)
        except (TypeError, ValueError):
            return self.HISTORY_CAP
        if val <= 0:
            return 0  # 0 means "no trim"
        return max(10, val)

    def _trim_history(self, history, cap):
        """Cap the stored history — but in RARE, BIG steps, not one message
        per turn.

        `history[-cap:]` on every append is the obvious version and it was
        costing minutes a turn. A local model reuses its cached work only for
        an unbroken run from the very start of the prompt, so the moment a
        conversation reaches the cap, every new message pushes one off the
        FRONT and the whole prompt has to be read again from cold. Measured on
        a real conversation at the cap: 22,000+ tokens re-read at ~78 tok/s,
        about five minutes before a reply began — every single turn, forever,
        with no way for it to ever settle down.

        So the history is allowed to fill to `cap`, and when it overflows it is
        cut back by a whole `step` at once. Between cuts nothing at the front
        moves, the prompt is genuinely append-only, and the cache holds. One
        turn in `step` pays the re-read instead of all of them.

        `cap` stays a true ceiling — the trim never lets the history exceed it,
        because that is the number the person configured and an oversized
        context on local Ollama returns nothing at all rather than slowing
        down. What changes is the floor: length now runs between `cap - step`
        and `cap` instead of sitting pinned at `cap`. That is real context
        given up (a quarter of the window, worst case) to buy back most of the
        waiting. Raise `history_cap` if you want the old depth back; it costs
        only memory, not speed, now that the trim is rare.
        """
        if cap <= 0 or len(history) <= cap:
            return history
        step = max(1, cap // 4)
        return history[-max(10, cap - step):]

    def __init__(self, agent_name, get_config, get_soul, get_memory,
                 get_model_options, on_status, on_activity=None,
                 request_webcam_approval=None, on_approval_needed=None,
                 get_busy_label=None):
        self.agent_name = agent_name
        self.get_config = get_config
        self.get_soul = get_soul
        self.get_memory = get_memory
        self.get_model_options = get_model_options
        self.on_status = on_status
        # Optional callback fired after the bot persists a user-side
        # turn (DM or group). Signature: on_activity(kind, identifier)
        # where kind is "user" or "group" and identifier is the
        # Telegram user_id or chat_id. The frame uses this to tick
        # per-(kin, scope) distillation counters so Telegram activity
        # actually triggers distillation passes — previously only the
        # desktop send path did, which meant pure-Telegram kin never
        # distilled. Safe to be None (the bot just no-ops the call).
        self.on_activity = on_activity
        # Surface label used in usage.log when this bot's worker
        # thread calls llm_backend.chat(). Re-set by
        # _handle_normal_message ("telegram-dm") and
        # _handle_group_message ("telegram-group") each turn. The
        # __init__ default is the most generic — any out-of-handler
        # chat() call (shouldn't happen, but defensive) gets logged
        # as "telegram" rather than the global "unknown" default.
        self._surface_label = "telegram"
        # Optional callback: when a Telegram user with
        # webcam_permission="ask" triggers `use_webcam`, the bot's
        # executor wrap calls this synchronously from the worker
        # thread to pop a wx dialog on the operator's desktop.
        # Signature: request_webcam_approval(user_label, user_id) ->
        # "allow" | "deny". None means "always deny" (worker can't
        # reach a UI to ask; safer default than capturing without
        # consent).
        self.request_webcam_approval = request_webcam_approval
        # Optional callback fired when a remote tool approval starts BLOCKING
        # on the operator. Signature: on_approval_needed(kin, command,
        # timeout_secs). The frame raises a desktop notification + speaks it,
        # because the Telegram-side prompt alone is invisible to an operator
        # who is focused on another kin — which is how a request went unseen
        # until it timed out and the kin reported it as a refusal.
        # Fire-and-forget: never blocks and never affects the decision.
        self.on_approval_needed = on_approval_needed
        # Optional callback returning a short phrase for whatever currently has
        # the model — a distillation, a scheduled wake-up, a heartbeat, a room
        # round, a kin mid-reply to somebody else — or "" when nothing does.
        # Read on the POLL thread, so it must stay cheap and never block: it is
        # only dict reads on the frame plus each bot's own turn lock.
        # Signature: get_busy_label(skip_bot=None) -> str, where skip_bot names
        # one kin whose remote turn to ignore.
        #
        # Telegram asks the WIDE question deliberately. In the main window the
        # person can see a reply arriving; from a phone they can see none of
        # it, and a kin busy with the desktop or with someone else's DM makes
        # them wait exactly as long as a distillation does.
        #
        # Ollama answers one request at a time, so when this returns something,
        # a message arriving now will sit unanswered for as long as that work
        # takes — routinely thirteen minutes for a distillation bite. The
        # desktop says so in its Activity line. Telegram had nothing at all,
        # which is worse: there is no status line there to read, so the only
        # way to find out was to send and wait, and not knowing turned every
        # message into a gamble about whether it would be read.
        self.get_busy_label = get_busy_label

        self._stop = threading.Event()
        self._poll_thread = None
        self._infer_thread = None
        self._queue = queue.Queue()
        self._offset = 0
        # Updates pulled off _queue while waiting for the rest of a split
        # message, which turned out to belong to a later turn. Touched by
        # the inference thread only, so it needs no lock.
        self._holdover = []
        # Chats already told, during the CURRENT busy period, that their
        # message is queued behind background work of ours. Cleared the moment
        # nothing is holding the model, so the next busy period speaks again.
        # POLL thread only — same confinement as `_holdover`, so no lock.
        self._queued_notice_sent = set()
        # Stop-button state. `_turn_lock` guards both: `_active_turn` is the
        # (user_id, chat_id) whose reply is being generated right now, or
        # None; `_cancelled_turns` holds the user_ids who have asked to stop
        # it. Written by the POLL thread (which is the only one still moving
        # while a reply is in flight — the inference thread is inside the
        # model call) and read by the inference thread's should_stop poll.
        self._turn_lock = threading.RLock()
        self._active_turn = None
        self._cancelled_turns = set()
        # Per-Telegram-user message histories. Loaded from disk so a hearthkin
        # restart doesn't wipe in-flight Telegram conversations. The
        # inference thread mutates _histories on every incoming message
        # AND the UI thread mutates it via Settings → Telegram → "share
        # with desktop" migration (which pops a user's slice from
        # in-memory + disk in one operation). Both paths must take
        # _histories_lock — without it, the migration's load-from-disk
        # could miss a newly-arrived message that the bot hadn't saved
        # yet, and lose it.
        self._histories_lock = threading.RLock()
        with self._histories_lock:
            self._histories = load_telegram_history(agent_name)
        self._status = TelegramBot.STATUS_OFF
        self._last_error = None
        # Per-user pending exec approvals. Key: Telegram user_id (int).
        # Value: _PendingApproval. The inference worker that fired the
        # exec call blocks on the contained Event; the next incoming
        # message from that user, if approval-shaped, sets the decision
        # and signals. _pending_lock guards the dict; the Event itself
        # handles thread coordination.
        self._pending_approvals = {}
        self._pending_lock = threading.Lock()
        # When the bot was last started — used by /status to report
        # uptime. Reset on each start() call.
        self._started_at = None
        # Per-user cache of the most recent reply's reasoning block so
        # /think can show it on demand. Bounded by user count; capped
        # implicitly by chat history (a user who's never chatted has
        # no entry).
        self._last_thinking = {}
        # Bot's own Telegram identity, populated on start() via getMe.
        # Needed for mention-detection in groups: an @-mention of this
        # bot, or a reply to one of this bot's messages, is what
        # triggers the mention_only participation policy. Both stay
        # None until getMe succeeds; mention detection treats unset
        # identity as "no match" so the kin stays silent rather than
        # responding by accident.
        self._bot_user_id = None
        self._bot_username = None
        # Once-per-process "this bot has no @username" warning flag
        # (audit T11) — getMe is called repeatedly by the poll loop
        # for transient-failure retry, and we don't want to spam the
        # failure log every cycle.
        self._username_warned = False
        # Once-per-start "allow_from has unparseable entries" warning
        # flag (audit L-B1). Reset in start().
        self._allow_from_warned = False
        # Per-user_id monotonic timestamp of the last "you're not on
        # the allow list" courtesy reply, so a stranger spamming DMs
        # can't burn the bot into a 429 flood-wait (audit L-S7).
        self._unauth_reply_times = {}
        # Park mode: user_ids currently tending a kin's game by emoting
        # (*feeds luna* runs the move instead of being a failed tool call).
        # In-memory + per-DM — a restart clears it and the user re-enters
        # with "park". See _handle_normal_message + park_mode.py.
        self._park_users = set()
        # (key, parsed) cache for the share-path conversation.jsonl
        # read — key is (st_mtime_ns, st_size). Without it, every
        # share-user message re-reads + re-parses the whole archive
        # (audit M-T3). Invalidates naturally when any writer bumps
        # the file's mtime/size.
        self._shared_convo_cache = None

    def _log_empty_reply(self, surface, model, raw_content, chat_id=None,
                         user_id=None, post_cleanup=None,
                         intermediate_content=None, tool_calls_made=None):
        """Always-on diagnostic for empty Telegram replies. Parallels
        Hearthkin._log_empty_reply on the desktop side. The group code
        silently skips posting when content is empty after cleanup —
        without this log, those silent skips are invisible to the
        operator, who just sees the bot ignore a message.

        Writes to ~/.hearthkin/logs/empty_replies.log with format:
        <iso-timestamp> [<kin>] surface=<telegram-dm|telegram-group>
        model=<model-id> chat=<chat_id> user=<user_id>
        raw=<repr> post_cleanup=<repr>
        intermediate=<repr> tools=<list-of-tool-names>

        `raw_content` is what the model returned for its FINAL reply
        before the anti-impersonation cleanup ran; `post_cleanup` is
        what the cleanup chain produced. `intermediate_content` is
        the most recent NON-EMPTY content from earlier tool-call
        turns in the same run_tool_loop session — the kin's
        "let me look that up" or substantive pre-tool narrative that
        normally gets buried under the empty final reply. Together
        they tell the operator whether the kin (a) said something
        useful but then went silent after the tool, (b) said
        boilerplate and then went silent, or (c) was empty start to
        finish."""
        from kin_persistence import LOGS_DIR
        try:
            path = LOGS_DIR / "empty_replies.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().isoformat(timespec="seconds")
            tools_list = list(tool_calls_made or [])
            with open(path, "a", encoding="utf-8") as f:
                f.write(
                    f"{ts} [{self.agent_name}] surface={surface} "
                    f"model={model} chat={chat_id} user={user_id} "
                    f"raw={raw_content!r} "
                    f"post_cleanup={post_cleanup!r} "
                    f"intermediate={(intermediate_content or '')!r} "
                    f"tools={tools_list!r}\n"
                )
        except Exception:
            pass

    def _maybe_build_roleplay_corrective_note(self, content, added_turns,
                                              effective_tools, surface,
                                              model, chat_id, user_id):
        """Run the tool-roleplay detector against this turn's content.
        If a roleplay shape fires AND no structured tool calls happened
        (added_turns has no assistant tool_calls entries), log the
        pattern to empty_replies.log with a variant tag and return the
        corrective system-note string. Caller appends the returned note
        to `new_turns` before persistence so the kin sees it on next
        read and can correct course.

        Returns "" when the detector misses, when tools actually fired
        (model isn't stuck — it just narrated alongside real work), or
        when the variant is narrative-intent (too ambiguous to safely
        auto-correct; logged only).

        Wired into both `_handle_normal_message` (DM) and
        `_handle_group_message` (group). The worst narration pattern
        seen (`*reads the next 100 lines*`, in a roleplay-heavy
        register) happened exclusively in the Telegram DM, where the
        detector wasn't running at all before 2026-06-10."""
        if not content or not effective_tools:
            return ""
        # Skip when real tool calls happened this turn — the kin isn't
        # stuck, it just chose to narrate alongside structured action.
        real_tools_fired = any(
            isinstance(t, dict)
            and t.get("role") == "assistant"
            and t.get("tool_calls")
            for t in (added_turns or [])
        )
        if real_tools_fired:
            return ""
        try:
            from chat_helpers import (
                detect_tool_roleplay,
                build_tool_roleplay_corrective_note,
            )
            variant, tool_name = detect_tool_roleplay(
                content, list(effective_tools))
            if not variant:
                return ""
            # Log the pattern (variant + tool + content tail) so the
            # operator's paper trail captures these silent misses.
            try:
                from kin_persistence import LOGS_DIR
                path = LOGS_DIR / "empty_replies.log"
                path.parent.mkdir(parents=True, exist_ok=True)
                ts = datetime.datetime.now().isoformat(timespec="seconds")
                tail = content.strip()[-300:]
                with open(path, "a", encoding="utf-8") as f:
                    f.write(
                        f"{ts} [{self.agent_name}] surface={surface} "
                        f"model={model} chat={chat_id} user={user_id} "
                        f"variant=tool-roleplay:{variant} "
                        f"tool_named={tool_name!r} "
                        f"available_tools={list(effective_tools)!r} "
                        f"content_tail={tail!r}\n"
                    )
            except Exception:
                pass
            return build_tool_roleplay_corrective_note(variant, tool_name, self.agent_name)
        except Exception:
            return ""

    def _build_tool_summary_footer(self, added_turns, full_cfg):
        """Build the small italic "_used: tool1, tool2_" footer that
        gets appended to Telegram replies when the kin called any
        tools during the turn. Per-kin opt-in via
        cfg.telegram.show_tool_summary (default True). Returns "" when
        the setting is off OR when no tools fired this turn.

        **2026-06-10**: previously also returned "" when
        `show_tool_calls` was on, treating the two settings as
        mutually exclusive. That made the footer redundant noise on
        verbose-call setups — but it also broke the footer's most
        valuable property: it's the only **unfakeable** signal of
        what actually ran. The kin can write
        `**Tool call:** read_file(...)` inside its content as
        markdown (a hallucination shape observed on Mistral in a
        roleplay-heavy register), and the
        operator can't easily tell that apart from a turn where
        `read_file` actually fired. The harness-emitted `🔧 ... → ...`
        per-call messages are also ground truth, but they're easy to
        miss in a fast-scrolling group. The footer at the end of the
        reply is the receipt: present iff a structured tool_use call
        actually went through `run_tool_loop`. Now fires alongside
        verbose display when both settings are on; operators wanting
        the quiet mode can still set `show_tool_calls=false` and rely
        on the footer alone.

        Names are deduplicated and listed in the order they first
        appeared in the loop, which mirrors the kin's order of action.

        Defense-in-depth: `chat_helpers.strip_tool_summary_footer`
        runs on the model's raw content BEFORE this footer is
        appended, so a model that spontaneously emits a `_used: ..._`
        line (e.g. picked up the pattern from history) gets it
        stripped first. That keeps the footer's ground-truth property
        intact — a footer the operator sees was emitted by the
        harness, not faked by the model."""
        tg_cfg = (full_cfg or {}).get("telegram") or {}
        if not tg_cfg.get("show_tool_summary", True):
            return ""
        seen = []
        for turn in (added_turns or []):
            if not isinstance(turn, dict):
                continue
            if turn.get("role") != "assistant":
                continue
            for tc in (turn.get("tool_calls") or []):
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                name = fn.get("name") if isinstance(fn, dict) else None
                if name and name not in seen:
                    seen.append(name)
        if not seen:
            return ""
        # Markdown italics survive in Telegram. Leading blank line
        # separates the footer from the reply visually + via NVDA
        # paragraph navigation.
        return "\n\n_used: " + ", ".join(seen) + "_"

    def _build_recall_footer(self, full_cfg):
        """Small italic "_recalled: log1, log2_" footer naming the memory
        logs that per-turn recall surfaced for this reply (from
        self._last_recall_used, set by inject_into_messages before the
        send). Per-kin opt-in via cfg.telegram.show_recall_summary
        (default True) so the operator can SEE what depth the kin drew on
        — the legibility surface the per-turn-retrieval design calls for.
        Returns "" when the setting is off or nothing surfaced."""
        tg_cfg = (full_cfg or {}).get("telegram") or {}
        if not tg_cfg.get("show_recall_summary", True):
            return ""
        names = []
        for u in (getattr(self, "_last_recall_used", None) or []):
            rel = (u.get("relpath") if isinstance(u, dict) else "") or ""
            if rel and rel not in names:
                names.append(rel)
        if not names:
            return ""
        return "\n\n_recalled: " + ", ".join(names) + "_"

    def is_running(self):
        return self._poll_thread is not None and self._poll_thread.is_alive()

    def status_label(self):
        if self._status == TelegramBot.STATUS_ERROR and self._last_error:
            short = self._last_error.splitlines()[0][:160]
            return f"Error: {short}"
        return self._status

    def start(self):
        if self.is_running():
            return
        self._stop.clear()
        self._started_at = datetime.datetime.now()
        # Re-arm the once-per-start allow_from warning (audit L-B1)
        # so a config fix between restarts gets re-checked.
        self._allow_from_warned = False
        self._set_status(TelegramBot.STATUS_CONNECTING)
        # Fetch the bot's own identity so mention-detection in groups
        # works. Best-effort: if getMe fails (no network, bad token,
        # 429), the poll loop's existing error path will surface the
        # token / network problem on its first call. Until that
        # resolves, group mention-detection treats unset identity as
        # "no match" and the kin stays silent in groups rather than
        # accidentally engaging.
        self._fetch_bot_identity()
        # Push the full command list to Telegram. Without this, the
        # Telegram client's "/" menu and slash auto-complete only
        # show whatever was registered via BotFather's /setcommands
        # (often just /help or nothing), and any partial /<command>
        # the user types gets auto-corrected to the only registered
        # command. setMyCommands replaces the list each call, so this
        # is idempotent and self-healing.
        self._register_bot_commands()
        # A restart gets a clean slate: anything set aside mid-reassembly
        # by the previous run's inference thread is stale, and so is a stop
        # request for a turn that no longer exists.
        self._holdover = []
        with self._turn_lock:
            self._active_turn = None
            self._cancelled_turns.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._infer_thread = threading.Thread(target=self._infer_loop, daemon=True)
        self._poll_thread.start()
        self._infer_thread.start()

    # Bot's slash-command list, in the shape Telegram's setMyCommands
    # API expects. Description is capped at 256 chars per the API; keep
    # them short and Telegram-UI-friendly (these show in the in-app
    # command menu and as auto-complete hints).
    #
    # Group-context menu is published with scope=all_group_chats and
    # only includes commands that actually work in groups (/help and
    # /whoami — everything else is per-user state that has no sensible
    # group target). DMs see the full list via the default scope
    # (audit T22).
    BOT_COMMANDS_GROUP = [
        {"command": "help", "description": "List available commands."},
        {"command": "whoami", "description": "Show your user ID and this chat's ID."},
    ]
    BOT_COMMANDS = [
        {"command": "help", "description": "List available commands."},
        {"command": "whoami", "description": "Show your user ID and the current chat's ID."},
        {"command": "about", "description": "Kin name, model, your tool bucket, soul snippet."},
        {"command": "status", "description": "Active kin, model, context-usage %, bot uptime."},
        {"command": "tools", "description": "Tools you can call via this kin."},
        {"command": "new", "description": "Archive current chat, start fresh. Memory kept."},
        {"command": "clear", "description": "Wipe history (asks to confirm). Memory kept."},
        {"command": "reset", "description": "Wipe history AND memory.md (asks to confirm)."},
        {"command": "regen", "description": "Redo the kin's last reply."},
        {"command": "undo", "description": "Drop the last user/assistant exchange."},
        {"command": "note", "description": "Append text to today's journal: /note <text>."},
        {"command": "play", "description": "Take a turn in a kin's game: /play tff look."},
        {"command": "think", "description": "Show the reasoning behind the last reply."},
        {"command": "cancel", "description": "Stop the reply being written (or deny a pending approval)."},
        {"command": "allow", "description": "Approve a pending tool call."},
        {"command": "deny", "description": "Deny a pending tool call."},
        {"command": "remember", "description": "Approve a pending tool call and save to allowlist."},
    ]

    def _register_bot_commands(self):
        """Push the bot's command list to Telegram via the setMyCommands
        API — what makes the client's "/" auto-complete menu show the
        bot's commands.

        Screen-reader escape hatch: the app-level `telegram_command_menu`
        flag (Preferences) can turn this OFF, in which case we push an
        EMPTY list instead — actively clearing any previously-registered
        menu. This is the fix for Unigram (and other screen-reader Telegram
        clients), where the "/" popup HIJACKS typed input: Unigram builds
        the popup from this list and, on send, commits the TOP entry
        (/help) rather than what was typed, so every "/whoami" arrives as
        "/help". No menu → no popup → typed commands send verbatim (the bot
        still dispatches them; it keys on the leading slash, not the menu).

        Best-effort: failures land in telegram_failures.log; commands typed
        in full keep working regardless."""
        from kin_persistence import (
            append_failure_log, load_json, CONFIG_FILE, DEFAULT_CONFIG,
        )
        cfg = self.get_config() or {}
        token = (cfg.get("bot_token") or "").strip()
        if not token:
            return
        app_cfg = load_json(CONFIG_FILE, DEFAULT_CONFIG)
        menu_on = app_cfg.get("telegram_command_menu", True)
        default_cmds = TelegramBot.BOT_COMMANDS if menu_on else []
        group_cmds = TelegramBot.BOT_COMMANDS_GROUP if menu_on else []
        try:
            # commands must be JSON-encoded — telegram_api_call form-
            # encodes params via urllib.parse.urlencode, which can't
            # serialize a Python list of dicts as a form value. Telegram's
            # setMyCommands expects the "commands" field as a JSON string.
            # Without json.dumps here the request 400s, the failure lands
            # in telegram_failures.log, and the slash menu never updates.
            #
            # Push twice with different scopes so the slash menu in
            # groups only lists commands that actually work there
            # (audit T22). Default scope is the catch-all for DMs and
            # anywhere else.
            telegram_api_call(
                token,
                "setMyCommands",
                {"commands": json.dumps(default_cmds)},
                timeout=10,
            )
            telegram_api_call(
                token,
                "setMyCommands",
                {
                    "commands": json.dumps(group_cmds),
                    "scope": json.dumps({"type": "all_group_chats"}),
                },
                timeout=10,
            )
        except Exception as e:
            append_failure_log(
                "telegram_failures.log",
                self.agent_name,
                "setMyCommands",
                e,
            )

    def _fetch_bot_identity(self):
        """Call getMe and cache the bot's user_id + username for
        mention-detection in groups. Silent on failure — the poll
        loop will surface token issues separately."""
        cfg = self.get_config() or {}
        token = (cfg.get("bot_token") or "").strip()
        if not token:
            return
        try:
            resp = telegram_api_call(token, "getMe", {}, timeout=10)
            if resp.get("ok"):
                me = resp.get("result") or {}
                self._bot_user_id = me.get("id")
                username = me.get("username") or ""
                self._bot_username = username.lower() if username else None
                # Warn once when the bot has no @username (audit T11):
                # mention-detection in groups becomes impossible and
                # the bot silently degrades to reply-only with no UX
                # signal. Setting a username via @BotFather is the
                # fix; this log makes the cause visible.
                if not self._bot_username and not self._username_warned:
                    self._username_warned = True
                    try:
                        from kin_persistence import append_failure_log
                        append_failure_log(
                            "telegram_failures.log",
                            self.agent_name,
                            "bot identity",
                            "bot has no @username; group @mentions "
                            "will not match. Set a username via "
                            "@BotFather to enable mention-detection.",
                        )
                    except Exception:
                        pass
        except Exception:
            # Leave the cached identity unset; group mention-detection
            # will treat that as "no match." Poll loop will report the
            # network / token error via STATUS_ERROR.
            pass

    def stop(self):
        self._stop.set()
        # Unblock any worker thread that's waiting on a chat-based exec
        # approval so it can exit instead of hanging on its Event.
        self.wake_pending_approvals_on_shutdown()
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass

        # Join the worker threads with a short timeout. Daemon-true means
        # the process can still exit if they don't return — but we'd
        # rather have them clean up properly, so anything in-flight can
        # finalize state before we drop the references. The timeout
        # bounds shutdown latency at ~3s total; threads that take
        # longer are abandoned to the daemon-thread fate.
        for thread_attr in ("_poll_thread", "_infer_thread"):
            t = getattr(self, thread_attr, None)
            if t is not None and t.is_alive():
                try:
                    t.join(timeout=1.5)
                except Exception:
                    pass

        self._set_status(TelegramBot.STATUS_OFF)
        self._poll_thread = None
        self._infer_thread = None

    def _set_status(self, status, detail=None):
        self._status = status
        if detail is not None:
            self._last_error = detail
        elif status != TelegramBot.STATUS_ERROR:
            self._last_error = None
        try:
            self.on_status(self.status_label())
        except Exception:
            pass

    def _message_has_image(self, msg):
        """Cheap presence-only check — does `msg` carry an image
        attachment we'd handle? Used to gate the early-return in
        `_handle_update` without paying for the getFile + download
        round-trip until we've confirmed the sender is allowed
        through.

        Mirrors what `_extract_image_attachment` would accept: a
        photo array (compressed) or a document with an image/* mime
        type we know how to save.
        """
        from kin_persistence import ATTACHMENT_MIME_TO_EXT
        photo = msg.get("photo")
        if isinstance(photo, list) and any(isinstance(p, dict) for p in photo):
            return True
        doc = msg.get("document")
        if isinstance(doc, dict):
            doc_mime = (doc.get("mime_type") or "").lower()
            if doc_mime in ATTACHMENT_MIME_TO_EXT:
                return True
        return False

    def _text_document_of(self, msg):
        """Return the `document` dict when `msg` carries a NON-image file we'd
        read as text (.txt, .md, source, etc.), else None.

        Before this existed, only image attachments were ever downloaded — a
        text upload was dropped without a word, so "check this out" arrived
        with nothing attached and the kin had no idea a file had been sent.
        Extension-first because Telegram reports .md as
        application/octet-stream."""
        doc = msg.get("document")
        if not isinstance(doc, dict):
            return None
        mime = (doc.get("mime_type") or "").lower()
        if mime.startswith("image/"):
            return None  # the image path owns this
        try:
            import reading_bridge
            if reading_bridge.is_text_attachment(doc.get("file_name") or "",
                                                 mime):
                return doc
        except Exception:
            pass
        return None

    def _download_text_documents(self, msg):
        """Download this message's text document(s) and return
        [(filename, bytes)]. Best-effort: a failed download yields a readable
        error in place of the bytes rather than vanishing, because a silent
        drop is the exact failure this path exists to fix."""
        doc = self._text_document_of(msg)
        if not doc:
            return []
        name = doc.get("file_name") or "attachment"
        try:
            import reading_bridge
            data, err = _download_telegram_file(
                self.token, doc.get("file_id"),
                max_bytes=reading_bridge.MAX_TEXT_ATTACHMENT_BYTES)
        except Exception as e:
            data, err = None, str(e)
        if data is None:
            append_failure_log(
                "telegram_failures.log", self.agent_name,
                f"text attachment download failed name={name!r}", err or "?")
            return [(name, b"")]  # surfaces as "could not load" in the block
        return [(name, data)]

    def _extract_image_attachment(self, msg):
        """If `msg` carries a photo or an image-mime document, download
        and save it to the kin's attachments/ dir; return the relative
        path string. Returns None when the message has no image, or
        when the download / save fails (errors are logged but don't
        bubble — we still want the text part of the turn to go
        through cleanly).

        Telegram delivers images two ways:

          - `photo`: a list of size variants (server-side compressed).
            Pick the largest (last by file_size, which Telegram sorts
            from small to large in practice but we sort explicitly to
            be safe). Mime is always image/jpeg for compressed photos.

          - `document` with mime_type starting `image/`: the user
            chose "Send as file" (no compression). mime_type tells us
            the format; we accept jpeg/png/gif/webp.

        Animated GIFs come through as `animation` (not photo, not
        document), and stickers as `sticker` — both are out of scope
        for v1; this method returns None for them.
        """
        from kin_persistence import (
            ATTACHMENT_MIME_TO_EXT,
            MAX_ATTACHMENT_BYTES,
            save_attachment_bytes,
            append_failure_log,
        )

        cfg = self.get_config() or {}
        token = (cfg.get("bot_token") or "").strip()
        if not token:
            return None

        file_id = None
        mime_type = None

        photo = msg.get("photo")
        if isinstance(photo, list) and photo:
            # Telegram returns variants sorted small→large, but sort
            # explicitly by file_size desc just to be defensive.
            sized = [p for p in photo if isinstance(p, dict)]
            if sized:
                sized.sort(key=lambda p: p.get("file_size") or 0, reverse=True)
                file_id = sized[0].get("file_id")
                mime_type = "image/jpeg"

        if file_id is None:
            doc = msg.get("document")
            if isinstance(doc, dict):
                doc_mime = (doc.get("mime_type") or "").lower()
                if doc_mime in ATTACHMENT_MIME_TO_EXT:
                    file_id = doc.get("file_id")
                    mime_type = doc_mime

        if file_id is None or mime_type is None:
            return None

        data, err = _download_telegram_file(
            token, file_id, max_bytes=MAX_ATTACHMENT_BYTES,
        )
        if err is not None or data is None:
            try:
                append_failure_log(
                    "telegram_failures.log",
                    self.agent_name,
                    f"download image file_id={file_id}",
                    err or "no data",
                )
            except Exception:
                pass
            return None
        try:
            rel = save_attachment_bytes(self.agent_name, data,
                                        mime_type=mime_type)
            return rel
        except Exception as e:
            try:
                append_failure_log(
                    "telegram_failures.log",
                    self.agent_name,
                    f"save image attachment ({mime_type})",
                    e,
                )
            except Exception:
                pass
            return None

    def _allowed_users(self):
        cfg = self.get_config() or {}
        ids = cfg.get("allow_from") or []
        if "*" in ids or "telegram:*" in ids:
            return None  # None means "any"
        out = set()
        bad = []
        for x in ids:
            s = str(x).strip()
            for prefix in ("telegram:", "tg:"):
                if s.lower().startswith(prefix):
                    s = s[len(prefix):]
            if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
                out.add(int(s))
            elif s:
                bad.append(s)
        # A non-numeric entry (e.g. "@alice") silently locked that
        # user out with no signal anywhere (audit L-B1). Log once per
        # start so the operator can find out why a listed user can't
        # get through.
        if bad and not self._allow_from_warned:
            self._allow_from_warned = True
            try:
                from kin_persistence import append_failure_log
                append_failure_log(
                    "telegram_failures.log",
                    self.agent_name,
                    "allow_from",
                    f"ignored unparseable entries {bad!r} — entries "
                    f"must be numeric Telegram user IDs (use /whoami "
                    f"to find one), not @usernames",
                )
            except Exception:
                pass
        return out

    def _group_excluded_ids(self, group_entry):
        """The set of user_ids muted in a group — parsed like allow_from
        (numeric, tolerant of telegram:/tg: prefixes). An unparseable entry
        is ignored rather than silently muting the wrong person."""
        out = set()
        for x in (group_entry or {}).get("exclude") or []:
            s = str(x).strip()
            for prefix in ("telegram:", "tg:"):
                if s.lower().startswith(prefix):
                    s = s[len(prefix):]
            if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
                out.add(int(s))
        return out

    def _poll_loop(self):
        backoff = 1.0
        first_loop = True
        while not self._stop.is_set():
            try:
                cfg = self.get_config() or {}
                token = cfg.get("bot_token", "").strip()
                # If start()'s getMe failed (transient network blip),
                # retry quietly so mention-detection comes back online
                # without requiring a restart (audit T10).
                if self._bot_user_id is None:
                    self._fetch_bot_identity()
                resp = telegram_api_call(
                    token,
                    "getUpdates",
                    {"offset": self._offset, "timeout": 30},
                    timeout=35,
                )
                if not resp.get("ok"):
                    raise RuntimeError(resp.get("description", "unknown"))
                if first_loop:
                    self._set_status(TelegramBot.STATUS_RUNNING)
                    first_loop = False
                for upd in resp.get("result", []):
                    # If shutdown started during this long-poll cycle,
                    # stop advancing offset — otherwise Telegram thinks
                    # we processed the updates and won't redeliver
                    # them on the next start (audit T12).
                    if self._stop.is_set():
                        break
                    # Resolve pending exec approvals HERE, on the poll
                    # thread, before enqueueing (audit C1). The bot has
                    # exactly ONE inference thread, and the worker that
                    # fired a gated exec call blocks on its approval
                    # Event ON that thread — so an approval reply that
                    # only gets classified inside _handle_update can
                    # never be seen until the worker has already
                    # auto-denied at timeout. Classification is pure
                    # string matching; the poll thread never blocks on
                    # inference. The in-handler intercept stays as a
                    # fallback (e.g. an approval registered after this
                    # update was already enqueued).
                    try:
                        consumed = self._maybe_resolve_approval_from_poll(upd)
                    except Exception:
                        consumed = False
                    # Same reasoning for the stop button: the inference
                    # thread is inside the model call and won't read the
                    # queue again until the reply we're being asked to stop
                    # has finished, so /cancel has to be acted on here.
                    if not consumed:
                        try:
                            consumed = self._maybe_stop_turn_from_poll(upd)
                        except Exception:
                            consumed = False
                    # Advance offset AFTER successful queue.put (audit
                    # T13). If put raised before this fix, the offset
                    # had already moved past an update that never
                    # reached the inference worker; Telegram would
                    # consider it delivered and never resend.
                    # Say, straight away, when this message is going to wait on
                    # background work of ours. Same reasoning as the two
                    # intercepts above: the inference thread cannot answer
                    # while it is inside a model call, and this is the only
                    # thread still moving. Never consumes the update.
                    if not consumed:
                        try:
                            self._maybe_say_queued_from_poll(upd)
                        except Exception:
                            pass
                    if not consumed:
                        self._queue.put(upd)
                    self._offset = upd["update_id"] + 1
                backoff = 1.0
            except Exception as e:
                self._set_status(TelegramBot.STATUS_ERROR, str(e))
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, 30.0)
                first_loop = True

    def _maybe_resolve_approval_from_poll(self, upd):
        """Poll-thread approval resolver (audit C1). If `upd` is a text
        message from a user with a pending exec approval — sent either
        in a DM or in the approval's originating chat (audit M-T1) —
        classify it and resolve the approval right here, without
        touching the (blocked) inference thread. Returns True when the
        update was consumed and must NOT be enqueued.

        Non-approval-shaped messages from a pending user: in a DM we
        nudge + consume (mirrors the historical in-handler behavior —
        the worker is blocked, so processing it as chat would only
        queue it behind the approval anyway); in a group we let the
        message flow through as normal chat (nudging a group for every
        unrelated line from the approver would be noise).

        MUST stay cheap and non-blocking: pure dict lookups + string
        matching + at most one sendMessage. Never call into the LLM
        or anything that can block on the inference thread."""
        msg = upd.get("message") or {}
        user_id = (msg.get("from") or {}).get("id")
        if user_id is None:
            return False
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        is_private = (chat.get("type") == "private")
        text = (msg.get("text") or "").strip()
        if not text:
            return False
        decision = None
        pending = None
        with self._pending_lock:
            pending = self._pending_approvals.get(user_id)
            if pending is None:
                return False
            if not (is_private or (pending.chat_id is not None
                                   and chat_id == pending.chat_id)):
                return False
            decision = _classify_approval_text(text)
            if decision is not None:
                self._pending_approvals.pop(user_id, None)
                pending.decision = decision
                pending.event.set()
        if decision is not None:
            try:
                self._send_chunked(chat_id, _APPROVAL_ACKS[decision])
            except Exception:
                pass
            return True
        if is_private:
            try:
                self._send_chunked(
                    chat_id,
                    f"⚠️ You've got a pending tool approval — please respond to it first.\n\n"
                    f"{self.agent_name} wants to run:\n\n`{pending.command}`\n\n"
                    f"Reply with yes / allow / ok, remember (run and save to allowlist), "
                    f"or no / deny."
                )
            except Exception:
                pass
            return True
        return False

    # --- Stop button ---------------------------------------------------
    #
    # /cancel used to mean "deny a pending tool approval" while /help
    # advertised it as cancelling "a pending operation" — which anyone reads
    # as "stop the reply you're writing". Worse, with no approval pending the
    # command queued up BEHIND the very reply it was meant to stop, so the
    # "nothing to cancel" answer arrived after the reply already had. It now
    # does what it says.
    #
    # The poll thread is the one that has to act on it: the inference thread
    # is inside the model call and won't look at the queue again until the
    # reply it's generating is finished. Same reasoning as the exec-approval
    # resolver above.

    def _begin_turn(self, user_id, chat_id):
        """Mark a reply as being generated for this person, and clear any
        stale stop request so a /cancel from an earlier turn can't kill the
        next one."""
        with self._turn_lock:
            self._active_turn = (user_id, chat_id)
            self._cancelled_turns.discard(user_id)

    def _end_turn(self, user_id):
        with self._turn_lock:
            self._active_turn = None
            self._cancelled_turns.discard(user_id)

    def _turn_cancelled(self, user_id):
        """The should_stop poll, called from inside the model call."""
        with self._turn_lock:
            return user_id in self._cancelled_turns

    def active_turn_label(self):
        """One human line naming the reply being written right now, or None.

        Read by the desktop's confirm-on-close check. Before this, the only
        way to know a remote kin was mid-reply was to be watching, which is
        no use for a kin on another machine or when you're out of the room —
        so quitting silently abandoned other people's conversations.

        Takes the lock rather than letting the caller peek at _active_turn,
        so the UI thread can't read it half-written."""
        with self._turn_lock:
            active = self._active_turn
        if active is None:
            return None
        user_id, chat_id = active
        who = ""
        try:
            labels = (self.get_config() or {}).get("user_labels") or {}
            who = (labels.get(str(user_id)) or labels.get(user_id) or "").strip()
        except Exception:
            who = ""
        if not who:
            # A group's chat_id is negative; a DM's equals the user's own id.
            who = "a group" if str(chat_id).startswith("-") else f"user {user_id}"
        return f"{self.agent_name} is part-way through a reply to {who} on Telegram"

    def _request_turn_stop(self, user_id, chat_id):
        """Ask the in-flight reply for this person to stop. Returns True if
        there was one to stop — a stop is per-person, so one member of a
        group can't halt a reply being written for someone else."""
        with self._turn_lock:
            active = self._active_turn
            if active is None or active[0] != user_id:
                return False
            self._cancelled_turns.add(user_id)
            return True

    def _maybe_stop_turn_from_poll(self, upd):
        """Poll-thread /cancel (and /stop) handler. Returns True when the
        update was consumed and must not be enqueued.

        Only consumes the command when there is genuinely a reply of this
        person's in flight to stop. Otherwise it falls through to the normal
        queued handler, which knows about pending tool approvals and can give
        the accurate "nothing pending" answer."""
        msg = upd.get("message") or {}
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            return False
        token = text.split(maxsplit=1)[0].lower().lstrip("/").split("@", 1)[0]
        if token not in ("cancel", "stop"):
            return False
        user_id = (msg.get("from") or {}).get("id")
        chat_id = (msg.get("chat") or {}).get("id")
        if user_id is None or chat_id is None:
            return False
        # A pending exec approval is the other resolver's business, and it
        # runs before this one — don't steal a /cancel that means "deny".
        with self._pending_lock:
            if user_id in self._pending_approvals:
                return False
        if not self._request_turn_stop(user_id, chat_id):
            return False
        try:
            self._send_chunked(chat_id, "Stopping — keeping what was written so far.")
        except Exception:
            pass
        return True

    def _maybe_say_queued_from_poll(self, upd):
        """Tell the sender, immediately, when Hearthkin's own background work
        has the model and their message is therefore going to wait.

        Ollama answers one request at a time. A distillation bite routinely
        runs thirteen minutes, and during it a Telegram message is neither
        lost nor being worked on — it is queued, and completely silent. The
        desktop at least has an Activity line to read. Telegram had nothing,
        so the only way to find out was to send and wait an unknown amount of
        time, and not knowing turned every message into a question about
        whether it was worth sending at all. That hesitation is the thing
        being fixed here; the notice is just how.

        Runs on the POLL thread for the same reason `/cancel` does: the single
        inference thread is inside a model call and will not read its queue
        until the current reply is finished, so anything that has to be said
        *during* the wait cannot be said from there.

        Never consumes the update — the message still goes through and is
        still answered. This only adds a line in front of it.

        Three things keep it from becoming noise:

        - It speaks only when `get_busy_label` names something. A kin merely
          mid-reply to the person's own previous message is not reported:
          they just sent that, and a bot narrating ordinary turn-taking is
          exactly the chatter this app avoids.
        - **Once per busy period per chat.** A long paste arrives as several
          updates (see `_coalesce_message_parts`), and three notices for one
          message would be worse than none. The latch also covers someone
          sending several messages while a distillation runs.
        - It stays quiet for slash commands, which are answered promptly and
          do not queue behind inference in the same way.

        Telegram output is append-only, so this is its own message and is
        never edited afterwards — the reply arrives beneath it as usual."""
        if not self.get_busy_label:
            return
        msg = upd.get("message") if isinstance(upd, dict) else None
        if not isinstance(msg, dict):
            return
        chat_id = (msg.get("chat") or {}).get("id")
        if chat_id is None:
            return
        text = (msg.get("text") or "")
        if text.strip().startswith("/"):
            return
        # A reply of OUR OWN, to this same chat, is the one thing not worth
        # mentioning — they just sent the message it is answering. Any other
        # turn of ours (another person's DM, a group, another kin entirely)
        # makes them wait just as long and is completely invisible from here.
        skip_bot = None
        try:
            with self._turn_lock:
                active = self._active_turn
            if active is not None and active[1] == chat_id:
                skip_bot = self.agent_name
        except Exception:
            skip_bot = None
        try:
            label = (self.get_busy_label(skip_bot) or "").strip()
        except Exception:
            label = ""
        if not label:
            # Nothing of ours has the model. Drop the latch so the NEXT busy
            # period is announced again — without this, the notice would fire
            # once in the lifetime of the bot and never again.
            self._queued_notice_sent.discard(chat_id)
            return
        if chat_id in self._queued_notice_sent:
            return
        self._queued_notice_sent.add(chat_id)
        try:
            self._send_chunked(
                chat_id,
                f"{label}. Your message is in the queue and will be "
                f"answered when that finishes — nothing is lost.")
        except Exception:
            pass

    def _take_update(self, timeout):
        """The next update to handle: anything the reassembler set aside
        first (it came off the queue but belongs to a later turn), then
        the live queue. Raises queue.Empty on timeout, like the queue it
        wraps. Inference-thread only — that confinement is what lets
        `_holdover` go without a lock."""
        if self._holdover:
            return self._holdover.pop(0)
        return self._queue.get(timeout=timeout)

    def _coalesce_key(self, upd):
        """`(user_id, chat_id)` when `upd` is a plain-text chat message
        that could be one piece of a longer message the sender's client
        split — else None, meaning "hand this straight to the handler,
        never merge it, never delay it".

        Deliberately narrow. Slash commands stay atomic: two commands in
        a row are two commands. Anything carrying an attachment
        dispatches alone — media turns have their own download, caption
        and album handling. And someone with a pending exec approval is
        never held back: a worker thread is blocked on their answer.
        """
        msg = upd.get("message") if isinstance(upd, dict) else None
        if not isinstance(msg, dict):
            return None
        text = (msg.get("text") or "").strip()
        if not text or text.startswith("/"):
            return None
        if msg.get("caption") or msg.get("media_group_id"):
            return None
        if self._message_has_image(msg):
            return None
        if self._text_document_of(msg) is not None:
            return None
        user_id = (msg.get("from") or {}).get("id")
        chat_id = (msg.get("chat") or {}).get("id")
        if user_id is None or chat_id is None:
            return None
        with self._pending_lock:
            if user_id in self._pending_approvals:
                return None
        return (user_id, chat_id)

    def _message_wait_secs(self):
        """The person's own pace, from `message_wait_secs`. Junk in the
        config falls back to the default rather than raising — a bad
        value here must not cost anyone their message."""
        cfg = self.get_config() or {}
        raw = cfg.get("message_wait_secs", _COALESCE_WINDOW_SECS)
        try:
            secs = float(raw)
        except (TypeError, ValueError):
            return _COALESCE_WINDOW_SECS
        if secs != secs:  # NaN
            return _COALESCE_WINDOW_SECS
        return max(0.0, min(secs, _COALESCE_MAX_WAIT_SECS))

    def _coalesce_window(self, upd):
        """How long to keep listening for a continuation of `upd`.

        The configured pace normally, but never less than
        `_COALESCE_SPLIT_WINDOW_SECS` for a part sitting at the ceiling:
        that one was cut mid-thought by the sender's client, so its
        continuation is on its way whatever pause the person asked for.
        """
        text = ((upd.get("message") or {}) if isinstance(upd, dict) else {}).get("text") or ""
        configured = self._message_wait_secs()
        if _utf16_len(text) >= _COALESCE_SPLIT_LEN:
            return max(configured, _COALESCE_SPLIT_WINDOW_SECS)
        return configured

    @staticmethod
    def _reply_target(msg):
        """message_id this message is a reply to, or None."""
        return (msg.get("reply_to_message") or {}).get("message_id")

    def _parts_mergeable(self, prev, nxt, key):
        """Is `nxt` a continuation of `prev`, or a new turn?

        Same person, same chat, and `nxt` must not introduce a reply
        target of its own — quoting a different message means a
        different conversation. A part that carries NO reply is fine
        after one that does: when a long reply gets split, the client
        attaches the quote to the first piece only.
        """
        if self._coalesce_key(nxt) != key:
            return False
        prev_reply = self._reply_target(prev.get("message") or {})
        nxt_reply = self._reply_target(nxt.get("message") or {})
        if nxt_reply is not None and nxt_reply != prev_reply:
            return False
        return True

    @staticmethod
    def _join_parts(prev_text, next_text):
        """Rejoin two pieces of one message. Near the ceiling the client
        breaks at a newline or a space when it can find one and eats the
        whitespace it broke on, so a newline is the right seam in the
        common case — and the right seam anyway for two lines someone
        typed a second apart. The exception is a part that reaches the
        ceiling: there was nothing to break on, the cut can be
        mid-word, and a newline there would split the word."""
        if not prev_text:
            return next_text
        if not next_text:
            return prev_text
        if prev_text[-1].isspace() or next_text[0].isspace():
            return prev_text + next_text
        if _utf16_len(prev_text) >= _COALESCE_HARD_CUT_LEN:
            return prev_text + next_text
        return prev_text + "\n" + next_text

    def _merge_updates(self, parts):
        """Fold coalesced parts into one update the rest of the bot
        handles as an ordinary single message.

        The first part's envelope wins — sender, chat, and `date`, since
        the send time worth storing is when the person started sending,
        not when their client finished. Entity offsets from the later
        parts are shifted into the joined text so an @mention that
        landed in part two still counts as addressing the kin.
        """
        first = parts[0]
        base = first.get("message") or {}
        text = base.get("text") or ""
        entities = list(base.get("entities") or [])
        for nxt in parts[1:]:
            nxt_msg = nxt.get("message") or {}
            nxt_text = nxt_msg.get("text") or ""
            joined = self._join_parts(text, nxt_text)
            shift = _utf16_len(joined) - _utf16_len(nxt_text)
            for ent in (nxt_msg.get("entities") or []):
                shifted = dict(ent)
                shifted["offset"] = ent.get("offset", 0) + shift
                entities.append(shifted)
            text = joined
        merged_msg = dict(base)
        merged_msg["text"] = text
        if entities:
            merged_msg["entities"] = entities
        merged = dict(first)
        merged["message"] = merged_msg
        return merged

    def _coalesce_message_parts(self, first):
        """Given the update just dequeued, gather any continuation parts
        arriving right behind it and return one merged update. Returns
        `first` untouched when it isn't the kind of message that can be
        split, or when nothing followed it."""
        key = self._coalesce_key(first)
        if key is None:
            return first
        parts = [first]
        total = _utf16_len((first.get("message") or {}).get("text") or "")
        deadline = time.monotonic() + self._coalesce_window(first)
        while len(parts) < _COALESCE_MAX_PARTS and total < _COALESCE_MAX_UNITS:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self._stop.is_set():
                break
            try:
                nxt = self._take_update(min(remaining, _COALESCE_TICK_SECS))
            except queue.Empty:
                continue
            if nxt is None or not self._parts_mergeable(parts[-1], nxt, key):
                # Not part of this thought. Set it aside so it's handled
                # next, ahead of anything still queued behind it. `None`
                # is the shutdown sentinel and has to survive too.
                self._holdover.append(nxt)
                break
            parts.append(nxt)
            total += _utf16_len((nxt.get("message") or {}).get("text") or "")
            deadline = time.monotonic() + self._coalesce_window(nxt)
        if len(parts) == 1:
            return first
        return self._merge_updates(parts)

    def _infer_loop(self):
        while not self._stop.is_set():
            try:
                upd = self._take_update(1.0)
            except queue.Empty:
                continue
            if upd is None:
                break
            try:
                # A long message the sender's client split arrives as
                # several updates; stitch them back into one turn before
                # anything else looks at it. Best-effort — if this ever
                # throws, handle the first part alone rather than drop
                # the message.
                upd = self._coalesce_message_parts(upd)
            except Exception:
                pass
            try:
                self._handle_update(upd)
                # A transient handler error used to leave STATUS_ERROR
                # sticky forever — the only reset path was the poll
                # loop's own error-recovery (audit L-B33). After a
                # clean handle, restore RUNNING. If the POLL side is
                # simultaneously failing, its except sets ERROR again
                # on the very next cycle, so this can't mask a real
                # ongoing network/token problem for long.
                if (self._status == TelegramBot.STATUS_ERROR
                        and not self._stop.is_set()):
                    self._set_status(TelegramBot.STATUS_RUNNING)
            except Exception as e:
                self._set_status(TelegramBot.STATUS_ERROR, f"handler: {e}")

    def _handle_update(self, upd):
        """Top-level message dispatcher. Routes to:
          1. Pending approval response (if user has one) — natural
             language or slash command.
          2. Slash command (e.g. /help, /whoami, /clear, /cancel).
          3. Normal chat — runs through the kin's LLM, with tools if
             the user's bucket allows.
        """
        msg = upd.get("message")
        if not msg:
            return
        user_id = (msg.get("from") or {}).get("id")
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        chat_type = chat.get("type")
        chat_title = chat.get("title")
        if chat_id is None:
            return
        text = (msg.get("text") or "").strip()
        # If this is an image message (photo array or document with
        # image mime), the user's text lives in `caption` instead of
        # `text`. We treat caption as the text accompanying the image —
        # the LLM sees a single user turn with both. If the kin's
        # model isn't vision-capable, we reply with a friendly hint
        # rather than silently dropping the image.
        #
        # PRESENCE check only here — actual download happens after
        # the allow_from / bootstrap / slash gates below. Downloading
        # an unauthorized user's photo just to drop it would burn
        # bandwidth and write bytes to the operator's disk for someone
        # who isn't supposed to be reaching us at all.
        has_image = self._message_has_image(msg)
        # A text document (.txt/.md/source) carries its words in `caption`
        # too, and must keep the turn alive when there's no caption at all —
        # otherwise a bare file upload returns here and the kin never learns
        # anything was sent.
        has_text_doc = self._text_document_of(msg) is not None
        if not text and (has_image or has_text_doc):
            text = (msg.get("caption") or "").strip()
        if not text and not has_image and not has_text_doc:
            return
        # In non-private chats (groups, supergroups, channels) we only
        # respond to a whitelist of safe slash commands — primarily
        # /whoami so the operator can discover the group's chat_id from
        # inside the group. Normal LLM chat in groups needs per-group
        # access control, privacy-mode handling, and a participation
        # policy ("speak when mentioned" vs "speak freely") none of
        # which exist yet. Adding a kin to a group right now lets you
        # find the chat_id; it does NOT make the kin a conversational
        # participant.
        is_private = (chat_type == "private")
        # Telegram delivers send time as a Unix epoch on every message;
        # we capture it here and thread it down so persisted user turns
        # carry a `ts` field. Without this, Telegram messages have no
        # temporal info at all — and that's what made the share-toggle
        # migration unable to interleave historical Telegram + desktop
        # messages chronologically. New messages have ts going forward.
        message_date = msg.get("date")

        # --- Bootstrap commands (bypass allow_from) ---
        # /whoami and /help are the bootstrap surface: a brand-new user
        # who isn't yet in allow_from needs /whoami to discover their
        # user ID (so the operator can add them) and /help to see what
        # the bot can do. Gating these behind allow_from creates a
        # chicken-and-egg loop where unlisted users are silently
        # dropped — including silent on the very command that exists
        # to break them out of that state. Always respond to these
        # two, regardless of allow_from. Neither touches kin state,
        # neither costs an LLM call, both just echo bot/user info.
        if text.startswith("/"):
            bootstrap_parts = text.split(maxsplit=1)
            bootstrap_token = bootstrap_parts[0].lower().lstrip("/")
            bootstrap_cmd = bootstrap_token.split("@", 1)[0]
            if bootstrap_cmd in ("whoami", "help"):
                self._handle_command(
                    text, user_id, chat_id,
                    chat_type=chat_type, chat_title=chat_title,
                )
                return

        # allow_from gates PRIVATE chats (DMs) ONLY. Group participation is
        # authorized separately — by the per-group opt-in plus that group's
        # own exclude/mute list (see the group branch below). Keeping these
        # two gates independent is the whole point: letting someone speak in
        # a group must not force DM access on them, and being on the DM list
        # must not silently enroll someone into every group. Before this,
        # allow_from was checked here for both surfaces, so the only way to
        # let a group member talk was to also grant them DM access.
        allowed = self._allowed_users() if is_private else None
        if allowed is not None and user_id not in allowed:
            # Non-allow_from senders in DMs get a friendly reply
            # instead of silent drop — explains the state and gives
            # them their user_id so they can share it with the
            # operator.
            #
            # Throttled to once per user_id per 10 minutes (audit
            # L-S7) — a stranger spamming DMs could otherwise drive
            # the courtesy reply into a 429 flood-wait at zero cost
            # to them, taking the bot down for real users.
            if is_private:
                now_mono = time.monotonic()
                last = self._unauth_reply_times.get(user_id)
                if last is None or (now_mono - last) >= 600.0:
                    # Cheap unbounded-growth guard: prune expired
                    # entries when the dict gets big.
                    if len(self._unauth_reply_times) > 256:
                        self._unauth_reply_times = {
                            uid: t
                            for uid, t in self._unauth_reply_times.items()
                            if (now_mono - t) < 600.0
                        }
                    self._unauth_reply_times[user_id] = now_mono
                    self._send_chunked(
                        chat_id,
                        f"Hi! You're not on {self.agent_name}'s allow list "
                        f"yet, so I can't chat with you. Your Telegram user "
                        f"ID is `{user_id}` — share that with the kin's "
                        f"operator and ask to be added in Hearthkin's "
                        f"Settings → Telegram → Users.\n\n"
                        f"(/help and /whoami work even before you're added.)"
                    )
            return

        # --- Approval response check (fallback) ---
        # The poll thread is the NORMAL resolver for these (audit C1 —
        # this thread is usually the one BLOCKED on the approval, so
        # an intercept here could never fire before the timeout). This
        # fallback covers updates that were enqueued before the
        # approval was registered. Accepted from a DM OR from the
        # approval's originating chat (audit M-T1) — group-triggered
        # approvals used to be unresolvable because this intercept was
        # DM-only while the prompt told the user to reply in-group.
        # Atomic check-classify-commit under lock so two near-
        # simultaneous responses can't both resolve the same
        # approval (audit T7). Also pop here — the worker's own
        # cleanup pop becomes a no-op identity check (audit T6).
        pending = None
        decision = None
        with self._pending_lock:
            pending = self._pending_approvals.get(user_id)
            if pending is not None and not (
                    is_private or (pending.chat_id is not None
                                   and chat_id == pending.chat_id)):
                # Same user, unrelated chat — a stray "yes" in some
                # other group must not authorize an exec call.
                pending = None
            if pending is not None:
                decision = _classify_approval_text(text)
                if decision is not None:
                    self._pending_approvals.pop(user_id, None)
                    pending.decision = decision
                    pending.event.set()
        if pending is not None and decision is not None:
            # Acknowledge the user immediately so they know it
            # registered. The worker thread will continue and post
            # the kin's reply when it's ready.
            self._send_chunked(chat_id, _APPROVAL_ACKS[decision])
            return
        if pending is not None and is_private:
            # Not an approval response — tell the user there's a
            # pending decision and repost the prompt so they don't
            # have to scroll. DM only: nudging a group for every
            # unrelated line from the approver would be noise, so
            # group messages fall through to normal handling.
            self._send_chunked(
                chat_id,
                f"⚠️ You've got a pending tool approval — please respond to it first.\n\n"
                f"{self.agent_name} wants to run:\n\n`{pending.command}`\n\n"
                f"Reply with yes / allow / ok, remember (run and save to allowlist), "
                f"or no / deny."
            )
            return

        # --- Slash commands ---
        if text.startswith("/"):
            self._handle_command(
                text, user_id, chat_id,
                chat_type=chat_type, chat_title=chat_title,
            )
            return

        # --- Normal chat ---
        # NOW that the gates are all passed (allow_from, slash
        # commands, approval intercept), actually download the image
        # if there is one. Bundle into a list — same shape for both
        # DM and group handlers.
        if has_image:
            photo_attachment = self._extract_image_attachment(msg)
        else:
            photo_attachment = None
        attachments = [photo_attachment] if photo_attachment is not None else None
        if not is_private:
            # Groups are opt-in. The kin only converses in a group
            # whose chat_id has been added to cfg.telegram.groups in
            # Settings. Unconfigured groups stay silent on normal
            # messages (slash commands like /whoami still respond
            # there, so operators can discover the chat_id and add
            # the group to the config).
            cfg = self.get_config() or {}
            groups = cfg.get("groups") or {}
            gentry = groups.get(str(chat_id))
            if gentry is None:
                return
            # Everyone in an opted-in group can talk to the kin (mention-gated
            # by the group's policy) EXCEPT people on this group's mute list.
            # Muting here has no bearing on DM access — the two lists are
            # independent by design.
            if user_id in self._group_excluded_ids(gentry):
                return
            self._handle_group_message(
                msg, user_id, chat_id, chat_type, chat_title,
                attachments=attachments,
            )
            return
        self._handle_normal_message(
            text, user_id, chat_id,
            message_date=message_date,
            attachments=attachments,
            msg=msg,
        )

    def _handle_command(self, text, user_id, chat_id,
                        *, chat_type="private", chat_title=None):
        """Dispatch a slash command. Commands without an active context
        (e.g. /allow when nothing's pending) respond informatively
        rather than silently.

        chat_type / chat_title default to private so existing call
        sites (none today, but defensively) keep working. /help and
        /whoami work in any chat type — /whoami in particular is the
        operator's tool for discovering a group's chat_id. Every
        other command applies to per-user state and only makes sense
        in a DM; we send a polite "DMs only" reply rather than a
        silent no-op when invoked from a group."""
        parts = text.split(maxsplit=1)
        cmd_token = parts[0].lower().lstrip("/")
        # Telegram appends `@BotName` to commands sent in groups when
        # multiple bots could match (e.g. `/whoami@TensorBot`). Strip
        # the suffix before dispatch so the same handler matches in
        # DM and group.
        cmd = cmd_token.split("@", 1)[0]
        arg = parts[1] if len(parts) > 1 else ""

        is_private = (chat_type == "private")
        # Commands that work everywhere — group + DM. /help and /whoami
        # are informational and have a meaningful answer in any chat
        # type. Everything else falls through to the "private only"
        # check below.
        if cmd == "help":
            self._cmd_help(chat_id, is_private=is_private)
            return
        if cmd == "whoami":
            self._cmd_whoami(
                user_id, chat_id,
                chat_type=chat_type, chat_title=chat_title,
            )
            return

        if not is_private:
            # Every command past this point operates on per-user state
            # (conversation history, pending approvals, last reply,
            # etc.) — there's no sensible target for those in a group
            # context. Tell the user to switch to a DM rather than
            # silently dropping the command.
            self._send_chunked(
                chat_id,
                f"/{cmd} only works in a direct message with "
                f"{self.agent_name}. The commands that work in groups "
                f"are /help and /whoami (the latter is how you find a "
                f"group's chat_id from inside the group)."
            )
            return

        if cmd in ("clear", "forget"):
            self._cmd_clear(user_id, chat_id, arg)
        elif cmd == "new":
            self._cmd_new(user_id, chat_id)
        elif cmd == "reset":
            self._cmd_reset(user_id, chat_id, arg)
        elif cmd == "status":
            self._cmd_status(user_id, chat_id)
        elif cmd == "regen":
            self._cmd_regen(user_id, chat_id)
        elif cmd == "undo":
            self._cmd_undo(user_id, chat_id)
        elif cmd == "tools":
            self._cmd_tools(user_id, chat_id)
        elif cmd == "about":
            self._cmd_about(user_id, chat_id)
        elif cmd == "note":
            self._cmd_note(user_id, chat_id, arg)
        elif cmd == "think":
            self._cmd_think(user_id, chat_id)
        elif cmd == "play":
            self._cmd_play(chat_id, arg)
        elif cmd == "cancel":
            self._cmd_cancel(user_id, chat_id)
        elif cmd in ("allow", "deny", "remember"):
            # No pending approval (covered above) — let user know.
            self._send_chunked(
                chat_id,
                f"No pending tool approval — /{cmd} only works when "
                f"{self.agent_name} has asked for permission to run something."
            )
        else:
            self._send_chunked(
                chat_id,
                f"Unknown command: /{cmd}. Try /help to see what's available."
            )

    def _route_park_emotes(self, reply_text, user_id, chat_id):
        """Park mode: turn the kin's action-emotes (*feeds luna*) into real
        moves in its own park, post what happened, and record it so the kin
        responds to the ground truth next turn.

        Uses the SAME save the kin plays and the desktop tend dialog uses
        (GameHost.run holds a cross-process lock). Best-effort throughout — a
        game error or a missing game must never break the chat reply that
        already went out."""
        try:
            from park_mode import extract_park_actions
            from tools import get_game
            from kin_persistence import now_iso
        except Exception:
            return
        try:
            host = get_game("tff")
            if host is None:
                return
            actions = extract_park_actions(
                reply_text, host.known_verbs(),
                host.known_targets(self.agent_name),
            )
            if not actions:
                return
            results = []     # (text, narration) for real moves that ran
            teachable = []   # (verb, text) — unknown verb that named a target
            # Cap so a runaway reply can't fire dozens of moves in one turn.
            for act in actions[:12]:
                if act.known:
                    try:
                        out = (host.run(self.agent_name, act.text) or "").strip()
                    except Exception as e:
                        out = f"(couldn't do that: {e})"
                    if out:
                        results.append((act.text, out))
                elif act.verb not in [v for v, _ in teachable]:
                    teachable.append((act.verb, act.text))
            for _text, out in results:
                self._send_chunked(chat_id, "🌳 " + out)
            # Teaching lane: offer to learn any unknown-but-targeted verb.
            # Flagged (🔧) as clearly the harness, never the kin's voice, so the
            # operator knows they're teaching and answers with a crisp mapping.
            for verb, text in teachable[:3]:
                self._send_chunked(
                    chat_id,
                    f"🔧 {self.agent_name} did \"{text}\" but I don't know the "
                    f"word \"{verb}\" yet. Teach me by replying: "
                    f"teach {verb} = pet   (or any verb/animal I already know). "
                    "Ignore to skip."
                )
            if results:
                from kin_persistence import load_app_prompt
                note = (
                    load_app_prompt("park_result_batch", self.agent_name)
                    .replace("{results}",
                             "\n".join(f"- {t} -> {o}" for t, o in results))
                )
                try:
                    self._append_turns_for(
                        user_id,
                        [{"role": "system", "content": note, "ts": now_iso()}],
                    )
                except Exception:
                    pass
        except Exception:
            pass  # park routing must never break the normal reply

    def _route_park_command(self, reply_text, user_id, chat_id, ask=None):
        """Text-in / text-out park bridge: run the `> command` lines a kin puts
        in its reply, post what happened (🌳), and record each as ground truth.
        Same shared core as the cron keeper (park_keeper.route_reply), so a
        chatter-who-tends and a keeper act the same way
        in chat. Gated per-kin by the `park` config value; best-effort so a game
        error can never break the chat reply that already went out.

        `ask` is what turns this from one move into a turn of PLAY. Given the
        park result just recorded, it must ask the kin for its next words, post
        and persist them, and return the reply text (or "" to stop). Omitted, we
        run exactly one move and return — the old behaviour, and what a caller
        with no way to re-ask the model should pass.

        Why the loop exists at all: a kin got one move per message, so it could
        not look AND act. A kin walks into a room described as wanting three
        specific things, and its next move, after another message from the
        operator, is `look` again. Five of seven moves can be looks, four of
        them the same look hours apart. One move per
        turn does not merely slow a kin down; it spends the only move it has on
        the one command that changes nothing, because looking is what you do
        when you cannot act on what you see.

        Stops when the kin stops writing `>` lines (its own signal — a reply
        with voice and no command means done), when `park_moves_max` is
        reached, or when the person sends /cancel. There is deliberately no
        repeat guard; see ``park_keeper.should_take_another_move``.

        The continuation asks for park moves only — no tools are offered even to
        a kin that has them, since what is being asked for here is the next move
        in a game, not a work session. A kin that wants a tool can stop writing
        `>` lines and say so.
        """
        try:
            import park_keeper
            from tools import get_game
            from kin_persistence import now_iso
        except Exception:
            return
        try:
            host = get_game("tff")
            if host is None:
                return
        except Exception:
            return
        # A park that can't be reached is not a turn. Checked ONCE up front,
        # before any move runs: without this the loop below would multiply the
        # damage it was built to fix, turning one failed reach into a whole
        # visit's worth. The person is told plainly (silence here is
        # indistinguishable from being ignored), the kin gets a short honest
        # note rather than a raw connection error as its ground truth, and the
        # operator gets the always-on log entry.
        try:
            _ok, _why = host.reachable(self.agent_name)
        except Exception:
            _ok, _why = True, ""
        if not _ok:
            if park_keeper.extract_command(reply_text):
                try:
                    host.log_unreachable(self.agent_name, _why, "telegram")
                except Exception:
                    pass
                try:
                    self._send_chunked(
                        chat_id,
                        "🌳 The park isn't reachable right now, so that move "
                        "didn't run and nothing was changed.")
                except Exception:
                    pass
                try:
                    from kin_persistence import now_iso as _now
                    self._append_turns_for(user_id, [{
                        "role": "system",
                        "content": ("[hearthkin: the park isn't reachable at "
                                    "the moment, so that move didn't run. "
                                    "Nothing in the park changed, and nothing "
                                    "there needs anything from you until it's "
                                    "back — this has been logged for whoever "
                                    "looks after the park server.]"),
                        "ts": _now(),
                    }])
                except Exception:
                    pass
            return
        # Re-open the turn for the duration of the loop so /cancel reaches it.
        # The caller's `finally` already ran _end_turn before park routing, and
        # an uncancellable multi-move loop against a slow local model is exactly
        # the kind of "nothing stops it but quitting" this app keeps closing.
        reopened = False
        if ask is not None:
            try:
                self._begin_turn(user_id, chat_id)
                reopened = True
            except Exception:
                reopened = False
        try:
            # The loop itself lives in park_keeper.play_turn, shared with the
            # desktop. What stays here is only what is genuinely Telegram's:
            # how one move is run and posted, how the model is re-asked, and
            # what "the person pressed stop" means on this surface.
            result = park_keeper.play_turn(
                self.agent_name,
                reply_text,
                run_move=lambda text: self._route_one_park_move(
                    text, user_id, chat_id, host),
                ask=ask,
                awaiting=lambda: host.awaiting_answer(self.agent_name),
                cancelled=lambda: self._turn_cancelled(user_id),
            )
        finally:
            if reopened:
                try:
                    self._end_turn(user_id)
                except Exception:
                    pass
        moves = result.moves
        spent_allowance = result.spent_allowance
        # Into the chat, not a log: it has to reach the person (who otherwise
        # cannot tell a spent allowance from a timeout) AND the kin, which reads
        # this chat as its own history and would otherwise start over next turn
        # instead of carrying on. One message serves both.
        if spent_allowance:
            try:
                from kin_persistence import load_app_prompt
                self._send_chunked(chat_id, load_app_prompt(
                    "park_moves_spent", self.agent_name).replace(
                        "{moves}", str(moves)))
            except Exception:
                pass

    def _route_one_park_move(self, reply_text, user_id, chat_id, host):
        """Run one `> command` from `reply_text`. True if a move actually ran.

        Split out of the loop above so "what one move does" stays the small,
        readable thing it was before the loop existed — post the result to the
        person, hand the kin its own richer copy as ground truth."""
        try:
            import park_keeper
            from kin_persistence import now_iso
        except Exception:
            return False
        try:
            cmd, res = park_keeper.route_reply(
                reply_text,
                lambda c, s="": host.run(self.agent_name, c, say=s))
            if not res:
                return False
            self._send_chunked(chat_id, "🌳 " + res)
            # The kin's copy carries the trimmings the other surfaces get: what
            # other tenants did, and (on a look) one concrete thing worth doing.
            # Only the KIN's copy -- the operator's chat post above stays the
            # plain result, since a suggestion aimed at the kin is noise in
            # someone else's conversation.
            #
            # This surface had neither, which mattered least while the tool
            # existed and would matter most if the tool retires: it would leave
            # the `>` line as the only way in, and the poorest of the three.
            try:
                kin_res = host.decorate(self.agent_name, cmd, res)
            except Exception:
                kin_res = res
            from kin_persistence import load_app_prompt
            note = (load_app_prompt("park_result_single", self.agent_name)
                    .replace("{command}", str(cmd))
                    .replace("{result}", str(kin_res)))
            try:
                self._append_turns_for(
                    user_id,
                    [{"role": "system", "content": note, "ts": now_iso()}],
                )
            except Exception:
                pass
            return True
        except Exception:
            # Park routing must never break the normal reply. False also stops
            # the loop, which is right: we don't know what happened, so asking
            # for another move on top of it would be guessing.
            return False

    def _cmd_play(self, chat_id, arg):
        """`/play <game> <command>` — take a turn in one of the kin's own
        play-by-typing games, in the SAME save the kin plays (and that the
        desktop "Tend a kin's park" dialog tends). DM-only (this method is
        only reached from the private-chat branch of _handle_command), and
        the shared cross-process lock in GameHost.run keeps a turn here from
        colliding with the kin's own turn or a cron wake-up.

        `/play` alone (or with an unknown game) lists the available games.
        A bare game name defaults its command to `look`."""
        from tools import get_game, list_games
        games = ", ".join(list_games()) or "(none registered)"
        parts = (arg or "").split(maxsplit=1)
        if not parts:
            self._send_chunked(
                chat_id,
                f"Take a turn in one of {self.agent_name}'s games — the same "
                f"save {self.agent_name} plays.\n\n"
                f"Usage: /play <game> <command>\n"
                f"Example: /play tff look   (then: dig 50, adopt cat, "
                f"care for <room>, build indoor, …)\n\n"
                f"Games: {games}"
            )
            return
        game = parts[0]
        command = parts[1].strip() if len(parts) > 1 else "look"
        host = get_game(game)
        if host is None:
            self._send_chunked(
                chat_id,
                f"No game called '{game}'. Available: {games}."
            )
            return
        try:
            result = host.run(self.agent_name, command)
        except Exception as e:
            from kin_persistence import append_failure_log
            append_failure_log(
                "telegram_failures.log", self.agent_name,
                f"play game={game} chat_id={chat_id}", e,
            )
            self._send_chunked(chat_id, f"[couldn't play that: {e}]")
            return
        self._send_chunked(chat_id, result or "(the game said nothing)")

    def _cmd_help(self, chat_id, *, is_private=True):
        if not is_private:
            # In groups, only /help and /whoami have a meaningful
            # response — keep the help short so it doesn't promise
            # commands the user can't actually run from here.
            self._send_chunked(
                chat_id,
                f"Commands {self.agent_name} answers to in this group:\n\n"
                "/help — this message\n"
                "/whoami — show your Telegram user ID, this chat's ID, "
                "and chat type (use this to find a group's chat_id)\n\n"
                "For the full command list and to chat with "
                f"{self.agent_name}, send messages in a direct "
                "message instead. Group conversation isn't supported yet."
            )
            return
        msg = (
            f"Commands for {self.agent_name}:\n\n"
            "/help — this message\n"
            "/whoami — show your Telegram user ID (and this chat's ID, "
            "useful for discovering group chat IDs)\n"
            "/about — kin name, model, soul snippet\n"
            "/status — kin, model, context-usage %, bot uptime\n"
            "/tools — see what tools you can call via this kin\n"
            "/new — archive current conversation, start fresh "
            "(memory kept)\n"
            "/clear — wipe current history without archiving "
            "(memory kept)\n"
            "/reset — wipe history AND memory.md (asks to confirm)\n"
            "/regen — redo the kin's last reply\n"
            "/undo — drop the last user/assistant exchange\n"
            "/note <text> — append text to today's journal\n"
            "/play <game> <command> — take a turn in a kin's game "
            "(e.g. /play tff look)\n"
            "/think — show the last reply's reasoning block\n"
            "/cancel — stop the reply being written, keeping what's "
            "written so far (or deny a pending tool approval)\n"
            "/allow, /deny, /remember — respond to a tool approval prompt\n"
            "  (also: yes / no / ok / save / etc. in plain text)\n"
        )
        self._send_chunked(chat_id, msg)

    def _cmd_whoami(self, user_id, chat_id,
                    *, chat_type="private", chat_title=None):
        """Report the user's Telegram ID and the current chat's ID.

        In a DM, user_id == chat_id, so the chat_id is redundant but
        we surface it for consistency. In a group / supergroup /
        channel, chat_id is the negative group ID — that's the value
        operators need for "send to this group" features and for
        future per-group access control."""
        lines = [f"Your Telegram user ID: `{user_id}`"]
        if chat_type == "private":
            lines.append(
                "\nThis is a direct message — the chat ID is the same "
                "as your user ID."
            )
            lines.append(
                "\nOperators paste user IDs into Settings → Telegram → "
                "Users in Hearthkin to grant access."
            )
        else:
            kind_label = {
                "group": "group",
                "supergroup": "supergroup",
                "channel": "channel",
            }.get(chat_type, chat_type or "chat")
            title_suffix = f" ({chat_title})" if chat_title else ""
            lines.append(
                f"\nThis {kind_label}{title_suffix} has chat ID: "
                f"`{chat_id}`"
            )
            lines.append(
                "Group chat IDs are negative — Telegram uses the sign "
                "to distinguish them from user IDs."
            )
            # Tell the operator whether the group is configured yet,
            # so /whoami is also a quick "is this kin set up to talk
            # here?" check.
            cfg = self.get_config() or {}
            groups = cfg.get("groups") or {}
            if str(chat_id) in groups:
                lines.append(
                    f"\n{self.agent_name} is configured to converse "
                    f"in this group."
                )
            else:
                lines.append(
                    f"\n{self.agent_name} is NOT yet configured to "
                    f"converse in this group — paste this chat ID "
                    f"into Settings → Telegram → Groups in Hearthkin "
                    f"to opt in."
                )
        self._send_chunked(chat_id, "\n".join(lines))

    def _cmd_clear(self, user_id, chat_id, arg=""):
        """Wipe this Telegram user's history with the kin. Memory.md
        stays. Destructive: there's no archive — see /new for the
        archive-then-start-fresh variant.

        For share-with-desktop users, /clear wipes the kin's main
        conversation.jsonl — affecting the desktop and any other
        sharing user. That's a big deal, so we require a confirm
        step the same way /reset does."""
        if self._user_shares_desktop(user_id):
            if arg.strip().lower() != "confirm":
                self._send_chunked(
                    chat_id,
                    f"⚠️ Your conversation with {self.agent_name} is "
                    f"shared with the Hearthkin desktop. /clear here "
                    f"will wipe the conversation file the desktop "
                    f"reads + writes too. Memory.md stays.\n\n"
                    f"To confirm, send: /clear confirm\n"
                    f"To archive instead of wiping, send: /new\n"
                    f"To cancel, send any other message."
                )
                return
            # Confirmed — wipe the kin's main conversation. Use the
            # preserving variant so a cron / Telegram write landing
            # during /clear isn't silently nuked (audit T19).
            from kin_persistence import (
                save_agent_conversation_preserving_externals,
                load_agent_conversation,
            )
            try:
                current = load_agent_conversation(self.agent_name)
                save_agent_conversation_preserving_externals(
                    self.agent_name, [], len(current),
                )
            except Exception:
                pass
            self._last_thinking.pop(user_id, None)
            self._send_chunked(
                chat_id,
                f"Cleared the shared conversation with {self.agent_name}. "
                f"The desktop and any other sharing user will see an "
                f"empty conversation now. Memory file is untouched."
            )
            return

        # Non-share path: just the per-user slice. _histories_lock so
        # an inference worker writing a new turn simultaneously can't
        # be lost (audit T1). Key via _hkey (audit DH1).
        with self._histories_lock:
            self._histories.pop(_hkey(user_id), None)
            try:
                save_telegram_history(self.agent_name, self._histories)
            except Exception:
                pass
        self._last_thinking.pop(user_id, None)
        self._send_chunked(
            chat_id,
            f"Cleared your conversation history with {self.agent_name}. "
            "They won't remember anything from before this message. "
            "Memory file is untouched."
        )

    def _cmd_new(self, user_id, chat_id):
        """Archive the current history to disk, then start fresh.
        The kin's memory.md is untouched.

        Non-share users: archive lands at
        ~/.hearthkin/kin/<kin>/telegram_archive/<user_id>_<ts>.json
        and only the per-user slice is wiped.

        Share users: snapshot the kin's main conversation.jsonl into
        ~/.hearthkin/kin/<kin>/conversations/<ts>.json (the
        existing desktop snapshots dir), then wipe conversation.jsonl.
        Affects the desktop and any other sharing user; ack says so."""
        if self._user_shares_desktop(user_id):
            from kin_persistence import (
                load_agent_conversation,
                save_agent_conversation,
                CONVOS_DIR,
                atomic_write_json,
                now_iso,
            )
            current = []
            try:
                current = list(load_agent_conversation(self.agent_name) or [])
            except Exception:
                current = []
            archived_to = None
            if current:
                try:
                    ts = (
                        datetime.datetime.now()
                        .strftime("%Y-%m-%d_%H-%M-%S")
                    )
                    snapshot_path = (
                        CONVOS_DIR
                        / f"{self.agent_name}_telegram_new_{ts}.json"
                    )
                    atomic_write_json(snapshot_path, {
                        "agent_name": self.agent_name,
                        "snapshotted_at": now_iso(),
                        "source": f"telegram_user_{user_id}_/new",
                        "messages": current,
                    })
                    archived_to = snapshot_path
                except Exception:
                    archived_to = None
            try:
                save_agent_conversation(self.agent_name, [])
            except Exception:
                pass
            self._last_thinking.pop(user_id, None)
            if archived_to:
                ack = (
                    f"Started a new conversation. Previous one (shared "
                    f"with desktop) was snapshotted to {archived_to.name} "
                    f"in the kin's conversations folder. The desktop and "
                    f"any other sharing user will now see an empty "
                    f"conversation. {self.agent_name}'s memory file is "
                    f"untouched."
                )
            elif current:
                ack = (
                    f"Started a new conversation. (Couldn't snapshot "
                    f"the previous one to disk.) The desktop and any "
                    f"other sharing user will see an empty conversation. "
                    f"{self.agent_name}'s memory file is untouched."
                )
            else:
                ack = (
                    f"Started a new conversation with {self.agent_name}. "
                    f"(Nothing to archive.)"
                )
            self._send_chunked(chat_id, ack)
            return

        # Non-share path: per-user archive + per-user wipe. Snapshot
        # the slice UNDER the lock (audit L-B3 — the bare read raced
        # the inference worker's append) but archive outside it, so
        # the file write doesn't block message intake. Key via _hkey
        # (audit DH1).
        with self._histories_lock:
            history = list(self._histories.get(_hkey(user_id)) or [])
        archived_to = None
        if history:
            try:
                archived_to = self._archive_telegram_history(user_id, history)
            except Exception:
                # If archiving failed, treat /new as /clear rather
                # than refusing — user explicitly asked to start
                # fresh and the worst case (no archive) is recoverable
                # because we still have telegram_history.json's
                # backup-on-write semantics.
                archived_to = None
        # _histories_lock so an inference worker writing a new turn
        # simultaneously can't be lost (audit T1).
        with self._histories_lock:
            self._histories.pop(_hkey(user_id), None)
            try:
                save_telegram_history(self.agent_name, self._histories)
            except Exception:
                pass
        self._last_thinking.pop(user_id, None)
        if archived_to:
            ack = (
                f"Started a new conversation. The previous one was "
                f"archived to {archived_to.name} on the operator's "
                f"machine. {self.agent_name}'s long-term memory is "
                f"untouched."
            )
        elif history:
            ack = (
                f"Started a new conversation. (Couldn't archive the "
                f"previous one to disk — memory's untouched, but the "
                f"old conversation isn't recoverable.) "
                f"{self.agent_name}'s long-term memory is untouched."
            )
        else:
            ack = (
                f"Started a new conversation with {self.agent_name}. "
                f"(Nothing to archive.)"
            )
        self._send_chunked(chat_id, ack)

    def _cmd_reset(self, user_id, chat_id, arg):
        """Nuclear option: wipe BOTH this user's conversation history
        AND the kin's long-term memory.md. Two-step confirmation
        because there's no undo: first /reset prompts for /reset
        confirm; only the second one fires.

        memory.md being kin-wide (not per-Telegram-user) means a
        /reset by user A wipes the memory that user B and the
        operator share. That's surprising in a multi-user context,
        so the confirmation step makes the scope explicit.

        Privilege gate (audit SH2): memory.md is the kin's entire
        long-term memory, shared across every surface. Confirmation
        by the requester is not authorization — a chat-only guest on
        the allow list must not be able to irreversibly destroy it.
        /reset therefore requires share-with-desktop OR the 'full'
        tool bucket; everyone else gets a polite refusal pointing at
        /clear (which only touches their OWN segregated DM slice)."""
        if not self._user_is_privileged(user_id):
            self._send_chunked(
                chat_id,
                f"/reset wipes {self.agent_name}'s long-term memory "
                f"(memory.md), which is shared with the operator and "
                f"every other surface — so it's limited to users the "
                f"operator has marked as share-with-desktop or given "
                f"the 'full' tool bucket.\n\n"
                f"To clear just YOUR conversation history with "
                f"{self.agent_name}, use /clear (or /new to archive "
                f"it first)."
            )
            return
        if arg.strip().lower() != "confirm":
            self._send_chunked(
                chat_id,
                f"⚠️ /reset will wipe BOTH your conversation history "
                f"AND {self.agent_name}'s long-term memory file "
                f"(memory.md). The memory file is shared with every "
                f"surface — desktop chat and any other Telegram user "
                f"who talks to {self.agent_name}.\n\n"
                f"To confirm, send: /reset confirm\n\n"
                f"To cancel, send any other message."
            )
            return
        # Confirmed.
        if self._user_shares_desktop(user_id):
            # Use the externals-preserving variant, same as /clear's
            # share path (audit L-B4) — a cron / Telegram write
            # landing mid-wipe isn't silently nuked.
            from kin_persistence import (
                save_agent_conversation_preserving_externals,
                load_agent_conversation,
            )
            try:
                current = load_agent_conversation(self.agent_name)
                save_agent_conversation_preserving_externals(
                    self.agent_name, [], len(current),
                )
            except Exception:
                pass
        else:
            # _histories_lock so an inference worker writing a new
            # turn simultaneously can't be lost (audit T1). Key via
            # _hkey (audit DH1).
            with self._histories_lock:
                self._histories.pop(_hkey(user_id), None)
                try:
                    save_telegram_history(self.agent_name, self._histories)
                except Exception:
                    pass
        self._last_thinking.pop(user_id, None)
        # Wipe memory.md atomically. We import lazily because the
        # kin_persistence helpers shouldn't be loaded until they're
        # actually used (keeps cold-start light).
        memory_wiped = False
        try:
            from kin_persistence import save_memory
            save_memory(self.agent_name, "")
            memory_wiped = True
        except Exception:
            pass
        if memory_wiped:
            ack = (
                f"Reset complete. Your conversation history with "
                f"{self.agent_name} is gone, and their memory.md "
                f"has been wiped. They start the next message as a "
                f"blank slate."
            )
        else:
            ack = (
                f"Conversation cleared, but couldn't wipe memory.md "
                f"(file system error). You may want to clear it "
                f"manually from the desktop app: Tools → Open active "
                f"kin folder → edit memory.md."
            )
        self._send_chunked(chat_id, ack)

    def _archive_telegram_history(self, user_id, history):
        """Write `history` to a timestamped file under the kin's
        telegram_archive/ subdirectory. Returns the Path on success,
        raises on failure. Caller decides how to handle errors."""
        from kin_persistence import atomic_write_json, now_iso
        archive_dir = _agent_dir(self.agent_name) / "telegram_archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        # Filename is user-id + timestamp so multiple archives per
        # user don't collide; ISO timestamp uses dashes/colons that
        # need cleanup for filesystem-safety on Windows.
        ts = (
            datetime.datetime.now()
            .strftime("%Y-%m-%d_%H-%M-%S")
        )
        # user_id can be int or str depending on history shape; normalize.
        uid_str = str(user_id)
        filename = f"{uid_str}_{ts}.json"
        path = archive_dir / filename
        atomic_write_json(path, {
            "agent_name": self.agent_name,
            "user_id": uid_str,
            "archived_at": now_iso(),
            "messages": history,
        })
        return path

    def _cmd_status(self, user_id, chat_id):
        """Report what the user is talking to, the active model, the
        rough context-window usage as a percent of num_ctx, and how
        long the bot's been running this session."""
        from chat_helpers import estimate_tokens
        from llm_backend import _message_text as _msg_text

        cfg = self.get_config() or {}
        try:
            model, options = self.get_model_options()
        except Exception:
            model, options = "(unknown)", {}
        # Match _max_context_tokens's fallback so the displayed cap
        # matches what truncation actually gates on. Substituting
        # 8192 here while truncation clamps to 2048 made /status
        # report "5% used" when the real prompt was being savagely
        # truncated (audit T14).
        num_ctx = int((options or {}).get("num_ctx") or 0) or 2048

        try:
            soul = self.get_soul() or ""
        except Exception:
            soul = ""
        try:
            memory = self.get_memory() or ""
        except Exception:
            memory = ""

        history = self._load_history_for(user_id)
        total = estimate_tokens(soul) + estimate_tokens(memory)
        for m in history:
            # _message_text counts tool_calls.arguments too — a
            # write_file's content arg can be thousands of chars and
            # was previously invisible to /status, making it
            # under-report for tool-using kin (audit T8).
            total += estimate_tokens(_msg_text(m))

        # Prefer the provider-reported prompt_tokens from the most
        # recent send — the AUTHORITATIVE figure (audit M-T4). The
        # raw estimate above sums the full STORED archive, which for
        # a long-running kin is many times what's actually sent per
        # turn (truncation trims to the budget each send) — the same
        # "disk archive as cap usage" class already fixed in
        # context_status (v0.4.7) and the desktop bar (v0.5.0).
        real = None
        try:
            real = llm_backend.last_reported_prompt_tokens(self.agent_name)
        except Exception:
            real = None
        if real:
            pct = int(round(100.0 * real / num_ctx)) if num_ctx else 0
            context_line = (
                f"Context: {real} tokens of {num_ctx} cap ({pct}%) — "
                f"last send, as reported by the provider"
            )
        else:
            budget = self._max_context_tokens({"num_ctx": num_ctx})
            if total > budget:
                pct = int(round(100.0 * budget / num_ctx)) if num_ctx else 0
                context_line = (
                    f"Context: ~{budget} tokens of {num_ctx} cap "
                    f"(~{pct}%, estimated) — stored history is larger "
                    f"(~{total} tokens); older turns are trimmed each "
                    f"send. That's normal."
                )
            else:
                pct = int(round(100.0 * total / num_ctx)) if num_ctx else 0
                context_line = (
                    f"Context: ~{total} tokens of {num_ctx} cap "
                    f"({pct}%, estimated)"
                )

        bucket = self._user_tool_bucket(user_id)
        bucket_label = bucket if isinstance(bucket, str) else "custom"
        surface = (
            "shared with desktop"
            if self._user_shares_desktop(user_id)
            else "private to your Telegram"
        )

        self._send_chunked(
            chat_id,
            f"Kin: {self.agent_name}\n"
            f"Model: {model}\n"
            f"{context_line}\n"
            f"Your tool bucket: {bucket_label}\n"
            f"Conversation: {surface}\n"
            f"Bot status: {self.status_label()}\n"
            f"Bot uptime: {self._format_uptime()}"
        )

    def _format_uptime(self):
        if self._started_at is None:
            return "(not started)"
        delta = datetime.datetime.now() - self._started_at
        total_secs = int(delta.total_seconds())
        days, rem = divmod(total_secs, 86400)
        hours, rem = divmod(rem, 3600)
        mins, secs = divmod(rem, 60)
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if mins:
            parts.append(f"{mins}m")
        if not parts:
            parts.append(f"{secs}s")
        return " ".join(parts)

    def _last_user_index(self, history):
        """Return the index of the most recent user-role message in
        `history` that belongs to this DM surface, or None if there
        isn't one. Used by /regen and /undo to find where to truncate
        (they both want everything from the last user message onward
        gone).

        Skips `telegram:group:`-tagged rows: on the share path the
        RAW conversation.jsonl interleaves group turns that the DM
        read view filters out. The cut index MUST be computed against
        the same raw list the truncation slices (audit DH3) — the old
        shape computed it against the FILTERED `_load_history_for`
        list, so any group-share traffic shifted the index low and
        `current[:idx]` permanently deleted the wrong span."""
        for i in range(len(history) - 1, -1, -1):
            m = history[i]
            if not isinstance(m, dict):
                continue
            if m.get("role") != "user":
                continue
            if (m.get("source") or "").startswith("telegram:group:"):
                continue
            return i
        return None

    def _load_raw_surface_history(self, user_id):
        """UNFILTERED history list for this user's surface — i.e. the
        exact list `_truncate_history_for` slices. Share users: the
        kin's full conversation.jsonl INCLUDING group-tagged rows
        (the filtering `_load_history_for` does for the model's read
        view must not be applied here — audit DH3). Non-share users:
        the raw `_histories` slice, no orphan-drop (the drop pops
        leading tool turns, which would shift indexes the same way)."""
        if self._user_shares_desktop(user_id):
            from kin_persistence import load_agent_conversation
            try:
                return list(load_agent_conversation(self.agent_name) or [])
            except Exception:
                return []
        with self._histories_lock:
            return list(self._histories.get(_hkey(user_id)) or [])

    def _truncate_history_for(self, user_id, idx):
        """Replace the user's effective history with the prefix
        history[:idx] for either surface. `idx` is an index into the
        RAW list (`_load_raw_surface_history`), NOT the filtered
        model-view list (audit DH3). For share users, rewrites
        conversation.jsonl in-place (full save_agent_conversation,
        not the append helper, because we're shortening). For non-
        share users, just slices the _histories slice and saves the
        per-user file."""
        if self._user_shares_desktop(user_id):
            from kin_persistence import (
                load_agent_conversation, save_agent_conversation,
            )
            current = list(load_agent_conversation(self.agent_name) or [])
            # /regen slice can sever an `assistant tool_calls ->
            # tool` pair just like the cap-trim path. Sweep both
            # ends after slicing so the persisted state is clean
            # (otherwise disk holds an orphan between regen and
            # the next message, healed on next load but visibly
            # broken if anything reads disk in between).
            #
            # Group-tagged rows past the cut belong to OTHER surfaces
            # (each group reads only its own source-tag slice) — a DM
            # /regen has no business deleting them, so they're
            # retained (audit DH3). Desktop rows past the cut DO go:
            # for a share user, desktop and DM are the same unified
            # thread.
            truncated = current[:idx] + [
                m for m in current[idx:]
                if isinstance(m, dict)
                and (m.get("source") or "").startswith("telegram:group:")
            ]
            _drop_leading_orphan_tools(truncated)
            try:
                save_agent_conversation(self.agent_name, truncated)
            except Exception:
                pass
            return
        with self._histories_lock:
            history = self._histories.get(_hkey(user_id)) or []
            truncated = history[:idx]
            _drop_leading_orphan_tools(truncated)
            self._histories[_hkey(user_id)] = truncated
            try:
                save_telegram_history(self.agent_name, self._histories)
            except Exception:
                pass

    def _cmd_regen(self, user_id, chat_id):
        """Drop the kin's last reply (and any tool round-trips that
        landed between the last user message and that reply), then
        re-fire the same user message through the model. Equivalent of
        the desktop's regenerate. Surface-aware: uses share-aware
        loaders so /regen works the same in both modes."""
        # Atomic load + find + truncate so a new message arriving
        # between the load and the truncate can't slip past (audit
        # T4). _histories_lock is reentrant, so the helpers' own
        # acquires inside this block are harmless.
        #
        # Load the RAW list, not the filtered model view — the cut
        # index is applied to the raw list by _truncate_history_for,
        # and computing it against the filtered view deleted the
        # wrong span whenever group-share or orphan-drops had
        # shifted indexes (audit DH3).
        prior_user = None
        with self._histories_lock:
            history = self._load_raw_surface_history(user_id)
            idx = self._last_user_index(history)
            if idx is None:
                self._send_chunked(
                    chat_id,
                    "Nothing to regenerate — there's no prior message in "
                    "this conversation."
                )
                return
            prior_user = history[idx]
            prompt = (prior_user.get("content") or "").strip()
            prior_atts = prior_user.get("attachments")
            regen_atts = (
                [a for a in prior_atts if isinstance(a, str) and a]
                if isinstance(prior_atts, list) else None
            )
            if not prompt and not regen_atts:
                self._send_chunked(
                    chat_id,
                    "Can't regenerate — the last message had no text content "
                    "or image to re-send."
                )
                return
            # Truncate history at (but not including) the last user message;
            # _handle_normal_message will re-append it.
            self._truncate_history_for(user_id, idx)
        self._last_thinking.pop(user_id, None)
        # Re-run the model on the same prompt. Attachments thread
        # through so the kin sees the image again on regen.
        self._handle_normal_message(
            prompt, user_id, chat_id,
            attachments=regen_atts or None,
        )

    def _cmd_undo(self, user_id, chat_id):
        """Remove the last user message and everything after it
        (assistant reply, any tool round-trips). Lets the user retract
        a message they didn't mean to send without nuking the whole
        history."""
        # Atomic load + find + truncate (audit T4 — same shape as
        # regen). Raw list, not the filtered model view (audit DH3).
        with self._histories_lock:
            history = self._load_raw_surface_history(user_id)
            idx = self._last_user_index(history)
            if idx is None:
                self._send_chunked(
                    chat_id,
                    "Nothing to undo — no prior message in this conversation."
                )
                return
            self._truncate_history_for(user_id, idx)
        self._last_thinking.pop(user_id, None)
        if self._user_shares_desktop(user_id):
            ack = (
                "Undid the last exchange. (This conversation is shared "
                "with the desktop, so the desktop won't see those "
                "messages either.)"
            )
        else:
            ack = (
                "Undid the last exchange. The kin won't see your previous "
                "message or its reply."
            )
        self._send_chunked(chat_id, ack)

    def _cmd_tools(self, user_id, chat_id):
        """Show the user their effective tool access — bucket name +
        the actual list of tools the kin will let them trigger
        (intersection of the kin's allowlist and their bucket)."""
        from kin_persistence import load_kin_tools
        from tools._buckets import filter_tool_names, BUCKET_EXPLAINER

        bucket = self._user_tool_bucket(user_id)
        bucket_label = bucket if isinstance(bucket, str) else "custom"
        explainer = (
            BUCKET_EXPLAINER.get(bucket, "(custom tool list)")
            if isinstance(bucket, str)
            else "(custom tool list set by the operator)"
        )
        try:
            kin_tool_names = load_kin_tools(self.agent_name)
        except Exception:
            kin_tool_names = []
        effective = filter_tool_names(kin_tool_names, bucket)

        if effective:
            tool_lines = "\n".join(f"  • {name}" for name in effective)
        else:
            tool_lines = "  (none — chat-only access)"

        self._send_chunked(
            chat_id,
            f"Your tool access for {self.agent_name}:\n\n"
            f"Bucket: {bucket_label}\n"
            f"  {explainer}\n\n"
            f"Tools you can trigger:\n{tool_lines}\n\n"
            "If something you'd expect isn't here, ask the operator to "
            "either enable the tool on the kin or raise your bucket."
        )

    def _cmd_about(self, user_id, chat_id):
        """A 'who am I talking to' card. Kin name, model, the user's
        own bucket, plus the first chunk of the kin's soul prompt as a
        preview (capped to keep the message readable)."""
        try:
            soul = self.get_soul() or ""
        except Exception:
            soul = ""
        try:
            model, _ = self.get_model_options()
        except Exception:
            model = "(unknown)"
        bucket = self._user_tool_bucket(user_id)
        bucket_label = bucket if isinstance(bucket, str) else "custom"

        snippet = soul.strip()
        if not snippet:
            snippet = "(no soul prompt set — talking to the raw model)"
        elif len(snippet) > 800:
            cut = snippet.rfind("\n", 0, 800)
            if cut < 200:
                cut = 800
            snippet = snippet[:cut].rstrip() + "\n…"

        self._send_chunked(
            chat_id,
            f"You're talking to: {self.agent_name}\n"
            f"Model: {model}\n"
            f"Your tool access: {bucket_label}\n\n"
            f"--- soul ---\n{snippet}"
        )

    def _cmd_note(self, user_id, chat_id, arg):
        """Append a timestamped line to today's journal in the kin's
        directory. Doesn't go through the model — the user is dropping
        a note directly. Useful for quick capture from your phone."""
        text = (arg or "").strip()
        if not text:
            self._send_chunked(
                chat_id,
                "Usage: /note <text>\n\n"
                "Appends a timestamped entry to today's journal under "
                f"this kin's folder. The kin can read it back via "
                "memory_search later if they have that tool enabled."
            )
            return
        try:
            path = self._append_journal_note(user_id, text)
        except Exception as e:
            self._send_chunked(chat_id, f"Couldn't write note: {e}")
            return
        self._send_chunked(chat_id, f"Saved to journal/{path.name}.")

    def _append_journal_note(self, user_id, text):
        journal_dir = _agent_dir(self.agent_name) / "memory" / "journal"
        journal_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.date.today().isoformat()
        path = journal_dir / f"{today}.md"
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        # Append an entry per call. Each note carries the source
        # (telegram + user_id) so the kin can tell it apart from
        # cron-written and conversation-distilled journal lines.
        entry = f"\n## {stamp} — note from telegram user {user_id}\n\n{text}\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(entry)
        return path

    def _cmd_think(self, user_id, chat_id):
        """Show the reasoning block from the kin's last reply to this
        user. Models that don't expose a reasoning stream (or kin with
        thinking disabled) just produce empty thinking — we say so
        explicitly rather than show a blank."""
        thinking = (self._last_thinking.get(user_id) or "").strip()
        if not thinking:
            self._send_chunked(
                chat_id,
                "No reasoning to show. Either the last reply didn't "
                "include a thinking block, the kin has thinking turned "
                "off, or the model doesn't expose reasoning."
            )
            return
        # Telegram's per-message limit is 4096 chars; _send_chunked
        # would split anyway, but we cap below that so the "from last
        # reply" framing line stays attached to the reasoning.
        capped = thinking
        if len(capped) > 3500:
            capped = capped[:3500] + "\n\n[truncated]"
        self._send_chunked(
            chat_id,
            f"Reasoning from the last reply:\n\n{capped}"
        )

    def _cmd_cancel(self, user_id, chat_id):
        # Two jobs, in this order: deny a pending tool approval, or stop a
        # reply being written.
        #
        # By the time this queued handler runs, the interesting cases have
        # usually been taken already by the POLL thread — approvals by
        # _maybe_resolve_approval_from_poll, in-flight replies by
        # _maybe_stop_turn_from_poll. They have to be, because the inference
        # thread sits inside the model call and doesn't read the queue again
        # until the reply finishes; a /cancel that only landed here would be
        # answered after the thing it meant to stop had already arrived. The
        # duplicated attempts below are the fallback for an update that was
        # enqueued before the approval or the turn existed.
        with self._pending_lock:
            pending = self._pending_approvals.get(user_id)
        if pending is not None:
            pending.decision = "deny"
            pending.event.set()
            self._send_chunked(chat_id, "Cancelled — the kin will continue without that tool call.")
            return
        if self._request_turn_stop(user_id, chat_id):
            self._send_chunked(chat_id, "Stopping — keeping what was written so far.")
            return
        self._send_chunked(
            chat_id,
            "Nothing to cancel — no reply is being written for you right now "
            "and there's no approval waiting."
        )

    def _model_is_openrouter(self):
        """True when the kin's active model dispatches via OpenRouter
        (openrouter/ name prefix — same rule llm_backend.chat() keys
        on). Used to decide whether the ollama package is actually
        required (audit M-T6)."""
        try:
            model, _ = self.get_model_options()
        except Exception:
            return False
        return isinstance(model, str) and model.startswith("openrouter/")

    def _user_shares_desktop(self, user_id):
        """Per-user opt-in for sharing the conversation with the
        desktop. False by default — sharing only makes sense when the
        Telegram user IS the operator (or someone the operator wants
        to give full continuity to). Other Telegram users get the
        standard per-user history segregation."""
        cfg = self.get_config() or {}
        share = cfg.get("user_share_desktop") or {}
        if not isinstance(share, dict):
            return False
        return bool(share.get(str(user_id)) or share.get(user_id))

    def _user_is_privileged(self, user_id):
        """Is this Telegram user trusted with kin-wide destructive
        operations (wiping memory.md via /reset)? True when the
        operator has marked them share-with-desktop (they effectively
        ARE the operator's surface) or given them the 'full' tool
        bucket (the highest trust tier). Audit SH2: anything below
        that is a chat-level guest and must not be able to destroy
        state shared with every other surface."""
        if self._user_shares_desktop(user_id):
            return True
        bucket = self._user_tool_bucket(user_id)
        return isinstance(bucket, str) and bucket.strip().lower() == "full"

    def _group_shares_desktop(self, chat_id):
        """Per-group opt-in for merging group history into the kin's
        main conversation.jsonl. False by default — most groups have
        multiple participants whose chats shouldn't merge into the
        operator's unified history. Flip on for groups that are
        effectively just-you-and-the-kin where you want continuity
        across surfaces (e.g. wiping conversation.jsonl on the
        desktop side then clears group history too).

        Reads cfg.group_share_desktop, a parallel structure to
        user_share_desktop but keyed by chat_id."""
        cfg = self.get_config() or {}
        share = cfg.get("group_share_desktop") or {}
        if not isinstance(share, dict):
            return False
        return bool(share.get(str(chat_id)) or share.get(chat_id))

    def _load_history_for(self, user_id):
        """Return the conversation history this user should see. For
        share-with-desktop users that's the kin's main conversation
        (load_agent_conversation), filtered to exclude any
        telegram:group:* tagged messages — those belong to multi-
        participant group contexts and would confuse a 1-on-1 DM
        thread. Desktop turns and other shared DMs are included
        (those are all part of the operator's unified single-thread
        memory).

        For non-share users, the per-user slice from _histories
        (default segregated behavior)."""
        if self._user_shares_desktop(user_id):
            try:
                all_msgs = self._load_shared_conversation_cached()
                filtered = [
                    m for m in all_msgs
                    if not (isinstance(m, dict)
                            and (m.get("source") or "").startswith("telegram:group:"))
                ]
                return _drop_leading_orphan_tools(filtered)
            except Exception:
                return []
        # setdefault is a hidden mutation — must take the lock so an
        # inference worker / /clear can't race the implicit insert
        # (audit T2). Key via _hkey (audit DH1).
        with self._histories_lock:
            return _drop_leading_orphan_tools(
                list(self._histories.setdefault(_hkey(user_id), []))
            )

    def _load_shared_conversation_cached(self):
        """Read the kin's conversation.jsonl, memoized by the file's
        (mtime_ns, size) stat signature. The share path re-reads and
        re-parses the ENTIRE archive on every message — multi-MB for a
        long-running kin, on the inference thread (audit M-T3). Any
        writer (the bot's own appends, desktop, cron) bumps mtime/size,
        which invalidates the cache naturally.

        Callers must treat the returned list and its dicts as
        READ-ONLY — build filtered copies, never mutate in place
        (the existing call sites already do)."""
        from kin_persistence import load_agent_conversation
        path = _agent_dir(self.agent_name) / "conversation.jsonl"
        key = None
        try:
            st = path.stat()
            key = (st.st_mtime_ns, st.st_size)
        except OSError:
            key = None
        cached = self._shared_convo_cache
        if key is not None and cached is not None and cached[0] == key:
            return cached[1]
        msgs = load_agent_conversation(self.agent_name) or []
        self._shared_convo_cache = (key, msgs) if key is not None else None
        return msgs

    def _append_turns_for(self, user_id, new_turns):
        """Persist `new_turns` (list of message dicts) into the right
        place for this user. For share users, append into the kin's
        main conversation.jsonl via append_agent_conversation_turn so
        the desktop sees them. Each message gets a source tag
        ("telegram:<user_id>") so the desktop can distinguish where
        it came from. For non-share users, append to the per-user
        list and save_telegram_history."""
        # Persistence failures here used to be silently swallowed,
        # which meant Telegram-to-desktop sync could silently break
        # without leaving anything in the always-on telegram_failures.log
        # for the user to find. Logging both branches now.
        from kin_persistence import append_failure_log
        # Stamp anything that arrived unstamped. The user and assistant turns
        # are built here and carry a ts; the TOOL round-trips come out of the
        # tool loop as plain provider messages and carried none at all, on
        # either branch below. The desktop path has always stamped its own.
        #
        # It reads as bookkeeping and is not. A conversation bucketed by date
        # simply loses every tool call a kin ever made here, and the answer
        # that produces — "this kin has never called a tool" — is confident,
        # wrong, and indistinguishable from a real finding. That happened: it
        # led to a kin being described as having confabulated a success it had
        # in fact carried out and reported accurately.
        #
        # Stamping from here on does NOT make the file safe to read by time.
        # Older turns stay unstamped, and the file already holds at least one
        # pair written out of order, so ordering is a property of the file's
        # SEQUENCE and nothing else. Walk it by index. This only stops the
        # blank column growing.
        now_iso = datetime.datetime.now().isoformat(timespec="seconds")
        new_turns = [
            (t if (isinstance(t, dict) and t.get("ts"))
             else {**t, "ts": now_iso}) if isinstance(t, dict) else t
            for t in (new_turns or [])
        ]
        if self._user_shares_desktop(user_id):
            from kin_persistence import append_agent_conversation_turn
            uid_str = str(user_id)
            for turn in new_turns:
                tagged = dict(turn)
                tagged.setdefault("source", f"telegram:{uid_str}")
                try:
                    append_agent_conversation_turn(self.agent_name, tagged)
                except Exception as e:
                    append_failure_log(
                        "telegram_failures.log",
                        self.agent_name,
                        f"append_conversation user={user_id} role={turn.get('role')}",
                        e,
                    )
            # Fire activity hook for shared users too — the frame
            # routes this to the "desktop" scope counter via
            # _distill_scope_for_telegram_user, so this surface's
            # activity contributes to the unified distillation
            # cadence alongside literal desktop chat.
            if self.on_activity is not None:
                try:
                    self.on_activity("user", user_id)
                except Exception:
                    pass
            return
        cap = self._history_cap()
        with self._histories_lock:
            # Key via _hkey (audit DH1) — keying by the raw int from
            # Telegram's JSON missed the str-keyed disk slice after a
            # restart, and the duplicate int+str JSON keys destroyed
            # the older slice on the next save/load round-trip.
            history = self._histories.setdefault(_hkey(user_id), [])
            history.extend(new_turns)
            history = self._trim_history(history, cap)
            # Sweep LEADING tool-pair fragments (a cap-cut mid-
            # `assistant tool_calls -> tool` pair leaves the orphan
            # in a shape providers reject — Mistral 400 "Unexpected
            # role 'tool' after role 'system'"). Sweep unconditionally
            # so legacy orphans heal on first append. Trailing-sweep
            # is deliberately OFF here: new_turns ends in content
            # assistant / system / user by construction (see the
            # call-site invariant in _handle_normal_message), so if
            # a future regression ever lands an assistant_tc at the
            # tail, we want it visible (Mistral 400 on next send)
            # not silently dropped on disk. The load-time sweep will
            # clean it before the model sees it either way.
            _drop_leading_orphan_tools(history, sweep_trailing_assistant_tcs=False)
            self._histories[_hkey(user_id)] = history
            try:
                save_telegram_history(self.agent_name, self._histories)
            except Exception as e:
                append_failure_log(
                    "telegram_failures.log",
                    self.agent_name,
                    f"save_telegram_history user={user_id}",
                    e,
                )
        # Tell the frame this surface saw activity so its distillation
        # counter for the right scope can tick. The bot doesn't know
        # whether this user is "shared with desktop" or not — that's
        # the frame's concern — so we just report the raw surface and
        # let the frame compute scope.
        if self.on_activity is not None:
            try:
                self.on_activity("user", user_id)
            except Exception:
                pass

    def _user_tool_bucket(self, user_id):
        """Return the user's tool bucket name. Defaults to 'none' for
        users not explicitly listed in user_tools — safety default per
        the design (opt-in for action surface, even for chat-allowed
        users)."""
        cfg = self.get_config() or {}
        ut = cfg.get("user_tools") or {}
        if not isinstance(ut, dict):
            return "none"
        # Keys are stored as strings (JSON shape); accept int lookup
        # too. Use sentinel checks (audit T9) so an explicit empty-list
        # custom bucket (`user_tools[id] = []` — "this user has tools
        # turned ON but with the empty allowlist") doesn't get
        # silently downgraded to "none" via the falsy-or pattern.
        sentinel = object()
        v = ut.get(str(user_id), sentinel)
        if v is sentinel:
            v = ut.get(user_id, sentinel)
        if v is sentinel:
            return "none"
        return v

    def _user_ts_from_message_date(self, message_date):
        """Convert Telegram's `message.date` (Unix epoch int) to the
        local-time ISO string used both as the "[YYYY-MM-DD HH:MM]"
        grounding prefix the model reads and as the persisted `ts`.
        Falls back to now_iso() when the date is missing or
        unparseable. Shared by the DM and group handlers (was
        copy-pasted in both)."""
        from kin_persistence import now_iso
        if message_date:
            try:
                return (
                    datetime.datetime.fromtimestamp(int(message_date))
                    .isoformat(timespec="seconds")
                )
            except Exception:
                return now_iso()
        return now_iso()

    def _send_vision_apology(self, chat_id):
        """Tell the sender their image can't be seen by the kin's
        current (non-vision) model. Shared by the DM and group
        handlers — the wording must stay identical across surfaces."""
        self._send_chunked(
            chat_id,
            "I got the image, but my current model can't see "
            "images yet — could you describe what's in it? "
            "(Or ask the operator to switch me to a vision-"
            "capable model.)"
        )

    def _handle_normal_message(self, text, user_id, chat_id, *, message_date=None, attachments=None, msg=None):
        """The chat path. Loads soul + memory + history, builds the
        messages list, dispatches to llm_backend.chat() or
        llm_backend.run_tool_loop() depending on whether the user has
        any tools in their effective bucket. Tool calls are surfaced as
        separate Telegram messages — append-only, never editing prior
        messages (cogacc-friendly, log-friendly).

        Sets self._surface_label to "telegram-dm" so the chat()
        call sites in this handler log under that surface name in
        usage.log.

        `message_date` is the Telegram-provided send time (Unix epoch
        int) for the user's message. We stamp the persisted user turn
        with it converted to local-time ISO so any future migration
        between surfaces can sort messages chronologically. None
        (e.g. /regen path with no real Telegram update behind it)
        falls back to now_iso().

        `msg` is the raw Telegram message dict (from getUpdates).
        Used to compute sender attribution — operator-set
        `cfg.telegram.user_labels[user_id]` wins when non-empty;
        otherwise the Telegram-derived "Display Name (@username)"
        from `_sender_attribution(msg)`. /regen passes msg=None and
        the most recent stored user turn's `sender_attribution` is
        used as a fallback. Without attribution a kin with multiple
        DM users can't tell who's typing — every DM looks the same."""
        from kin_persistence import (
            append_failure_log,
            build_system_prompt,
            load_agent_config,
            resolve_kin_ollama_host,
            load_kin_tools,
            now_iso,
            sanitize_for_prompt_literal,
        )

        # The ollama package is only needed for local-model dispatch —
        # an openrouter/-prefixed model goes over plain HTTPS and works
        # fine without it (audit M-T6). Gating both unconditionally
        # bricked OpenRouter-only kin on hosts without ollama installed.
        if ollama is None and not self._model_is_openrouter():
            self._send_chunked(chat_id, "[error: ollama not installed on the host]")
            return

        # Set surface for usage.log logging. Used by chat() call sites
        # below + by _run_tool_loop_telegram (which reads this back).
        self._surface_label = "telegram-dm"

        # --- Park mode toggle. Accepts BOTH the plain keyword ("park") and the
        # slash form ("/park") — the leading slash is stripped before matching.
        # Slash forms are safe now that the command menu can be turned off
        # (telegram_command_menu): with the menu off, Unigram delivers a typed
        # "/park" as literal text instead of hijacking it into "/help". "/park"
        # is also less prone to false-firing than a bare "park" said in
        # conversation. "leave" / "/leave" turns it off. ---
        _kw = (text or "").strip().lstrip("/").strip().lower().strip(".!")
        if _kw == "park":
            self._park_users.add(user_id)
            self._send_chunked(
                chat_id,
                f"🌳 Park mode on — I'll act on what {self.agent_name} does in "
                "its park (feed a creature, tend a room, dig, adopt…) instead of "
                "just narrating it. Say \"leave\" to stop. If I ever learn a word "
                "wrong, say \"my words\" to see what you've taught me and "
                "\"forget <word>\" to undo one."
            )
            return
        if _kw in ("leave", "leave park", "exit park", "done tending") \
                and user_id in self._park_users:
            self._park_users.discard(user_id)
            self._send_chunked(chat_id, "🌳 Park mode off.")
            return

        # --- Teach command: "/teach <tool> <phrase> = <word>" (works any time,
        # not only in park mode). Quotes optional; slash optional (survives the
        # command menu being on or off); "as" accepted for "=". Routes to the
        # named tool/game's teach backend, e.g. /teach tff "grooms" = "pet".
        # Only fires when the first token is an actually-teachable tool, so a
        # normal message like "teach me about owls" falls through to the kin.
        # The older park-only "teach X = Y" (no tool, defaults to tff) is kept
        # below unchanged for back-compat. ---
        _mteach2 = re.match(
            r'(?i)^\s*/?teach\s+(\S+)\s+(.+?)\s*(?:=|\bas\b)\s*(.+?)\s*$',
            text or "")
        if _mteach2 and self._teachable_tool(_mteach2.group(1).strip().strip("\"'")):
            tool = _mteach2.group(1).strip().strip("\"'")
            phrase = _mteach2.group(2).strip().strip("\"'")
            word = _mteach2.group(3).strip().strip("\"'")
            try:
                from tools import get_game
                _host = get_game(tool)
                _msg = (_host.teach(phrase, word) if _host else None) \
                    or f"'{tool}' can't be taught (no teach support)."
            except Exception as _e:
                _msg = f"(couldn't teach that: {_e})"
            self._send_chunked(chat_id, "🔧 " + _msg)
            return

        park_mode = user_id in self._park_users

        # Park teaching lane: "teach <word> = <verb/animal I know>" (or "... as
        # ..."). A plain, explicit command — clearly the harness, not the kin —
        # so a new emote verb slots in during play without eating a message
        # meant for the kin. Only while in park mode.
        if park_mode:
            _mteach = re.match(
                r"(?i)^\s*teach\s+(.+?)\s*(?:=|\bas\b)\s*(.+?)\s*$", text or "")
            if _mteach:
                try:
                    from tools import get_game
                    _host = get_game("tff")
                    _msg = _host.teach(_mteach.group(1), _mteach.group(2)) \
                        if _host else None
                except Exception as _e:
                    _msg = f"(couldn't teach that: {_e})"
                self._send_chunked(chat_id, "🔧 " + (_msg or "Couldn't teach that."))
                return
            # Un-teach a word the accessible way (no JSON editing). Single-token
            # target only, with a few dismissive words excluded, so a
            # conversational "forget it" still reaches the kin.
            _mforget = re.match(
                r"(?i)^\s*(?:forget|unteach|un-teach)\s+(\S+)\s*$", text or "")
            if _mforget and _mforget.group(1).lower().strip("'\"") not in (
                    "it", "that", "this", "everything", "about", "me"):
                try:
                    from tools import get_game
                    _host = get_game("tff")
                    _msg = _host.forget(_mforget.group(1)) if _host else None
                except Exception as _e:
                    _msg = f"(couldn't forget that: {_e})"
                self._send_chunked(
                    chat_id, "🔧 " + (_msg or "Couldn't forget that."))
                return
            # Show what's been taught, so a wrong entry can be spotted + undone.
            if re.match(
                    r"(?i)^\s*(?:taught|my (?:taught )?words|"
                    r"what have i taught(?: you)?\??|"
                    r"list (?:my )?(?:taught )?words)\s*$", (text or "").strip()):
                try:
                    from tools import get_game
                    _host = get_game("tff")
                    _msg = _host.taught() if _host else None
                except Exception as _e:
                    _msg = f"(couldn't list that: {_e})"
                self._send_chunked(
                    chat_id, "🔧 " + (_msg or "Nothing taught yet."))
                return
            # Point the operator at the hand-editable word-list file, so a whole
            # batch of words can be added at once without teaching one by one.
            if re.match(r"(?i)^\s*(?:vocab|vocabulary|word ?list|edit vocab"
                        r"(?:ulary)?)\s*$", (text or "").strip()):
                try:
                    from tools import get_game
                    _host = get_game("tff")
                    _p = _host.vocab_path() if _host else None
                except Exception:
                    _p = None
                if _p:
                    _msg = ("Your hand-editable word lists are in this folder:\n"
                            + _p + "\n\nAll plain text:\n"
                            "• actions.txt — what a kin does (e.g. `snuggle = pet`)\n"
                            "• everyone.txt — words that mean 'care for all'\n"
                            "• creatures/ — one file per animal (cat.txt, "
                            "rabbit.txt…), each a plain list of nicknames\n\n"
                            "Edit any in a text editor; changes work on the next "
                            "turn — no restart. Or teach one quickly here with "
                            "`teach <word> = <a word I know>`.")
                else:
                    _msg = ("This game has no editable word list. Teach words "
                            "here with `teach <word> = <a word I know>`.")
                self._send_chunked(chat_id, "🔧 " + _msg)
                return

        cfg = self.get_config() or {}
        token = cfg.get("bot_token", "").strip()

        typing_stop = threading.Event()

        def keep_typing():
            # Log only the first failure in THIS TURN — the flag is a
            # closure local, so the next turn's keep_typing gets a fresh
            # one. Per-session de-duplication would need an instance
            # attribute; per-turn is fine because real send failures
            # also surface via _send_chunked. (Audit T15 — earlier docs
            # called this "once per session" incorrectly.)
            logged_failure = False
            while not typing_stop.is_set():
                try:
                    telegram_api_call(token, "sendChatAction",
                                      {"chat_id": chat_id, "action": "typing"}, timeout=10)
                except Exception as e:
                    if not logged_failure:
                        try:
                            from kin_persistence import append_failure_log
                            append_failure_log(
                                "telegram_failures.log",
                                self.agent_name,
                                f"sendChatAction chat_id={chat_id} (typing indicator)",
                                e,
                            )
                        except Exception:
                            pass
                        logged_failure = True
                if typing_stop.wait(4.0):
                    break

        typing_thread = threading.Thread(target=keep_typing, daemon=True)
        typing_thread.start()

        try:
            soul = self.get_soul() or ""
            memory = ""
            try:
                memory = self.get_memory() or ""
            except Exception:
                memory = ""
            model, options = self.get_model_options()
            # If the user sent an image but the kin's model can't see
            # images, reply with a friendly note and refuse — don't
            # silently drop the photo (user would wonder why the kin
            # ignored the image they obviously sent). Text-only turns
            # pass through unchanged.
            if attachments and not llm_backend.model_supports_images(model):
                # Apologize up front, then proceed with whatever text
                # accompanied the image (treating it like a text-only
                # turn). If the user sent ONLY an image with no caption,
                # there's no text to respond to either — bail with the
                # apology alone rather than send an empty user turn to
                # the model. The attachments are not persisted because
                # the kin can't actually see them — a later vision-
                # capable swap doesn't help retroactively (the file
                # exists on disk but no message references it).
                self._send_vision_apology(chat_id)
                attachments = None
                if not text:
                    typing_stop.set()
                    return
            # Decide the user-turn timestamp once, up front: Telegram-
            # provided send time when present (offline-queued sends keep
            # chronological order), now_iso() otherwise. Used twice — once
            # as the "[YYYY-MM-DD HH:MM]" prefix the model reads on this
            # turn, again as the persisted `ts` on the stored user turn.
            user_ts = self._user_ts_from_message_date(message_date)

            # Pull history from the right surface — desktop's
            # conversation.jsonl for share-with-desktop users, the
            # per-user telegram_history slice for everyone else.
            history = self._load_history_for(user_id)

            # Compute the current sender's attribution string. Order:
            #   1. Operator-set cfg.telegram.user_labels[user_id] —
            #      curated name the operator wrote in Settings →
            #      Telegram → Users (e.g. "SpeakerFifteen", "Alex"). Wins
            #      when non-empty.
            #   2. Telegram-derived "Display Name (@username)" via
            #      _sender_attribution(msg). Used when msg is present
            #      and the operator hasn't set a label.
            #   3. The most recent stored user turn's
            #      sender_attribution / sender_name. Used by /regen
            #      (msg is None there) so the regen prompt carries the
            #      same attribution the prior turn had.
            #   4. Empty — no attribution prefix gets emitted.
            labels_map = cfg.get("user_labels") or {}
            user_label = ""
            try:
                user_label = (labels_map.get(str(user_id))
                              or labels_map.get(user_id) or "").strip()
            except Exception:
                user_label = ""
            sender_attribution = ""
            sender_name = ""
            if user_label:
                sender_attribution = user_label
            elif msg is not None:
                sender_attribution = self._sender_attribution(msg)
            else:
                for h in reversed(history):
                    if isinstance(h, dict) and h.get("role") == "user":
                        sender_attribution = (
                            h.get("sender_attribution")
                            or h.get("sender_name") or ""
                        )
                        sender_attribution = (sender_attribution or "").strip()
                        break
            if msg is not None:
                sender_name = self._sender_display_name(msg)

            # Resolve this turn's effective tool set BEFORE building the
            # system prompt so the base prompt's tool/memory scaffolding
            # can be fenced to only what's actually enabled (a no-tool
            # turn gets none of it). Effective set = kin's tools.json ∩
            # user's bucket — same value reused for the tool-path decision
            # below.
            kin_tool_names = load_kin_tools(self.agent_name)
            bucket = self._user_tool_bucket(user_id)
            from tools._buckets import filter_tool_names
            effective_tools = filter_tool_names(kin_tool_names, bucket)

            # In park mode the kin plays by BEING itself — its emotes are the
            # moves (see the post-reply router below), so it needs no tools and
            # shouldn't be nudged to call any. Drop them for this turn.
            if park_mode:
                effective_tools = []

            messages = []
            sys_prompt = build_system_prompt(soul, memory, enabled_tools=effective_tools,
                                             kin_name=self.agent_name)
            if park_mode:
                # Editable harness prompt — seeded to ~/.hearthkin/prompts/
                # park_frame.md on first run, file wins thereafter, per-kin
                # override supported. See kin_persistence.APP_PROMPT_REGISTRY.
                from kin_persistence import load_app_prompt
                park_frame = load_app_prompt("park_frame", self.agent_name)
                if park_frame and park_frame.strip():
                    sys_prompt = (sys_prompt or "") + "\n\n" + park_frame
            # Text-in/text-out park kin (`park` = chat|keeper): tell it the
            # `> command` convention exists, or it never learns it. The harvest
            # side (_route_park_command, below) only listens — it never teaches
            # — and the operator shouldn't have to write it into the soul. So a
            # chat-mode kin could act in its park on every reply and have no
            # idea it could. Editable like every harness prompt; independent of
            # the emote `park_mode` toggle above (a keeper also gets the fuller
            # MECHANISM on its cron turns — this covers it, and every chat kin,
            # on the DM surface, which is where the `>` line actually runs).
            try:
                import park_keeper as _pk
                if _pk.kin_park_mode(self.agent_name) in ("chat", "keeper"):
                    from kin_persistence import load_app_prompt
                    _park_hint = load_app_prompt(
                        "park_chat_hint", self.agent_name)
                    if _park_hint and _park_hint.strip():
                        sys_prompt = (sys_prompt or "") + "\n\n" + _park_hint
            except Exception:
                pass
            if sys_prompt.strip():
                messages.append({"role": "system", "content": sys_prompt})
            # Apply ts + sender prefix to USER history entries only.
            # Applying it to assistant entries too made the kin see its
            # own prior replies in the prefix format and pulled
            # generation toward mimicking — see hearthkin.pyw
            # _history_entry_for_model for the longer note. The user-
            # turn prefix gives the kin two grounding signals: "when
            # was this sent" (timestamp) and "who said it"
            # (sender_attribution). Sender attribution is the key
            # missing piece for kin with multiple DM users — without
            # it a kin can't tell SpeakerFifteen from SpeakerThree in a non-shared
            # DM, since the kin sees only one user's history slice
            # but has no name attached to it.
            from chat_helpers import (format_ts_prefix,
                                      speaker_attribution_prefix)
            # Every name THIS surface brackets onto a turn, history included:
            # a shared DM carries more than one person and their names ride
            # their old turns. Collected here rather than named anywhere, so a
            # new person needs no list updating — the strip reads from the same
            # values that wrote the brackets, and the two cannot drift apart.
            dm_bracketed = set()
            for h in history:
                if not isinstance(h, dict):
                    messages.append(h)
                    continue
                h_role = h.get("role")
                h_content = h.get("content")
                if h_role == "user" and isinstance(h_content, str):
                    ts_prefix = format_ts_prefix(h.get("ts"))
                    h_sender = (h.get("sender_attribution")
                                or h.get("sender_name") or "").strip()
                    # Defense-in-depth: legacy stored values may pre-date
                    # the sanitizer in `_sender_attribution` /
                    # `_sender_display_name`. Sanitize again at the
                    # embed boundary so old conversation.jsonl rows
                    # with control chars in attribution can't break the
                    # prompt frame.
                    sender_prefix = speaker_attribution_prefix(h_sender)
                    if sender_prefix:
                        dm_bracketed.add(
                            sender_prefix.strip().strip("[]").strip())
                    if ts_prefix or sender_prefix:
                        new_h = dict(h)
                        new_h["content"] = ts_prefix + sender_prefix + h_content
                        messages.append(new_h)
                        continue
                messages.append(h)
            # Current turn's sender_attribution was already sanitized
            # by `_sender_attribution()`; re-applying here is a no-op
            # but keeps the embed-site call sites symmetric so future
            # readers can't accidentally pipe an unsanitized variable.
            sender_prefix_current = speaker_attribution_prefix(
                sender_attribution)
            if sender_prefix_current:
                dm_bracketed.add(
                    sender_prefix_current.strip().strip("[]").strip())
            new_user_msg = {
                "role": "user",
                "content": format_ts_prefix(user_ts) + sender_prefix_current + text,
            }
            if attachments:
                new_user_msg["attachments"] = list(attachments)
            messages.append(new_user_msg)

            full_cfg = load_agent_config(self.agent_name) or {}
            cache = bool(full_cfg.get("cache", True))
            cache_ttl = str(full_cfg.get("cache_ttl", "auto"))
            openrouter_provider = llm_backend.build_openrouter_provider_routing(
                full_cfg.get("openrouter_provider_order"),
                bool(full_cfg.get("openrouter_allow_fallbacks", True)),
            )

            # Per-turn memory recall (same mechanism as the desktop send path):
            # surface the kin's own relevant depth inline on this user turn so
            # it has real material to be present with — no tool call needed.
            # Fail-soft; covers both the tool-loop and plain-chat dispatch
            # below since both send this `messages`. The Telegram DM is the
            # surface where a kin with nothing to be present with falls back
            # on soothing noise, so it's the one this most needs to reach. See
            # docs/design/per-turn-memory-retrieval.md.
            self._last_recall_used = []
            try:
                from memory_recall import inject_into_messages
                messages, self._last_recall_used = inject_into_messages(
                    messages, self.agent_name,
                    num_ctx=int(full_cfg.get("num_ctx", 8192) or 8192),
                    cfg=full_cfg,
                    # Tell recall which bracket is OURS. Without it the
                    # sender's name is part of every message the matcher
                    # reads, and the depth log about that person qualifies on
                    # every turn regardless of what was said.
                    speaker_names=dm_bracketed)
            except Exception:
                pass

            # No-tools memory: a kin with neither read_staging nor a write tool
            # can't reach its own pending notes, so inline them. No-op for every
            # kin that has the tools. See toolless_memory.py.
            try:
                import toolless_memory
                messages, self._toolless_scopes = toolless_memory.inject(
                    messages, self.agent_name, effective_tools)
            except Exception:
                self._toolless_scopes = []

            # Reading bridge — "the sharing is the loading" (DM only; a group
            # is multi-user and auto-reading arbitrary machine paths named by
            # any member is a risk — groups get the nudge but not auto-attach).
            # If the operator named a real file, place its TEXT in front of the
            # kin this turn so it engages the content instead of gesturing at
            # reading it. TEXT, not an image attach — a kin on a non-vision model.
            shared_this_turn = False
            try:
                import reading_bridge
                _block = ""
                # An UPLOADED text document takes precedence over a path named
                # in the text: the operator physically attached it, so that's
                # unambiguously what they meant by "this".
                _uploaded = self._download_text_documents(msg)
                if _uploaded:
                    _block = reading_bridge.build_attachment_context_block(
                        _uploaded, self.agent_name)
                else:
                    _shared = reading_bridge.extract_shared_paths(text)
                    if _shared:
                        _block = reading_bridge.build_shared_context_block(
                            reading_bridge.read_shared_files(_shared),
                            self.agent_name)
                if _block and messages:
                    messages.insert(len(messages) - 1,
                                    {"role": "system", "content": _block})
                    shared_this_turn = True
            except Exception as e:
                append_failure_log(
                    "telegram_failures.log", self.agent_name,
                    "shared-file injection failed", e)

            # Tool path decision uses effective_tools resolved above
            # (kin's tools.json ∩ user's bucket). Empty → plain chat path.
            show_thinking = bool(full_cfg.get("show_thinking", False))
            _stream_ed = None  # set by the tool path when it streams in-place
            # From here until the reply is in hand, /cancel from this person
            # means "stop writing" (see the stop-button block above). The
            # poll thread is what notices; this just declares the turn open.
            self._begin_turn(user_id, chat_id)
            try:
                if effective_tools:
                    content, added_turns, thinking, _stream_ed = self._run_tool_loop_telegram(
                        model, messages, options, cache,
                        effective_tools, user_id, chat_id, full_cfg,
                    )
                else:
                    # Streamed with no render callback, purely so the stop
                    # button reaches this path too — `chat(stream=False)` has
                    # no interruptible moment inside it. Same ChatResult
                    # shape, same single send afterwards, no live-typing
                    # artifacts in chat.
                    result = llm_backend.chat_collect(
                        model, messages, options=options,
                        should_stop=lambda: self._turn_cancelled(user_id),
                        show_thinking=show_thinking,
                        cache=cache, cache_ttl=cache_ttl,
                        openrouter_provider=openrouter_provider,
                        max_context_tokens=self._max_context_tokens(full_cfg),
                        kin_name=self.agent_name,
                        surface=self._surface_label,
                        ollama_host=resolve_kin_ollama_host(
                            full_cfg.get("ollama_host_name", "")),
                    )
                    content = (result.content or "").strip()
                    added_turns = []
                    thinking = (getattr(result, "thinking", "") or "").strip()
            finally:
                # Read the flag BEFORE clearing it — _end_turn discards it so
                # a stop can't leak into the next turn.
                turn_stopped = self._turn_cancelled(user_id)
                self._end_turn(user_id)

            # Snapshot pre-cleanup content for empty-reply diagnostics
            # below. Matches the group handler's pattern so log lines
            # have the same shape across surfaces.
            raw_model_content = content

            # Pull any inline <thinking>...</thinking> markup out of
            # content and merge into the structured thinking field
            # before persistence and /think stashing. See
            # chat_helpers.extract_inline_thinking — handles models
            # (Haiku 4.5 inline, MiMo, R1 distills) that emit reasoning
            # as XML markup in content rather than via the structured
            # reasoning channel.
            from chat_helpers import (
                extract_inline_thinking,
                scan_intermediate_tool_content,
                strip_self_timestamp,
                strip_tool_summary_footer,
            )
            content, thinking = extract_inline_thinking(content, thinking)

            # Strip any leading "[YYYY-MM-DD HH:MM]" prefix the kin
            # echoed from the timestamp-grounding context. See
            # chat_helpers.strip_self_timestamp — same fix as the
            # desktop path; Telegram already shows a per-message
            # timestamp natively, so an echoed prefix is visible
            # duplication AND keeps the kin reinforcing the pattern
            # next turn.
            content = strip_self_timestamp(content)
            # Defensive: strip any trailing "_used: ..._" footer the
            # model may have spontaneously generated. See
            # chat_helpers.strip_tool_summary_footer for rationale.
            content = strip_tool_summary_footer(content)

            # Empty after cleanup — try to salvage intermediate
            # content as the reply before falling back to the
            # "[no reply from model]" placeholder. Same pattern as
            # the group handler: Haiku-4.5 with side-action tools
            # (`note` especially) tends to produce substantive
            # content + a tool call, then ~2 EOS tokens after the
            # tool result. The intermediate IS the kin's intended
            # reply.
            #
            # A stopped turn is NOT an empty reply and must not be recorded as
            # one: the kin didn't fall silent, someone asked it to stop. So no
            # salvage attempt (whatever it had begun is what they chose to
            # keep) and nothing written to empty_replies.log, which exists to
            # diagnose faults and would otherwise fill up with our own
            # interruptions.
            salvaged_from_intermediate = False
            salvaged_tool_names = []
            if not content and not turn_stopped:
                intermediate, salvaged_tool_names = (
                    scan_intermediate_tool_content(added_turns))
                candidate = (intermediate or "").strip()
                if candidate:
                    candidate, _drop_thinking = extract_inline_thinking(
                        candidate, "")
                    candidate = strip_self_timestamp(candidate)
                    candidate = strip_tool_summary_footer(candidate)
                    candidate = candidate.strip()
                if candidate:
                    content = candidate
                    salvaged_from_intermediate = True
                self._log_empty_reply(
                    surface=("telegram-dm-salvaged"
                             if salvaged_from_intermediate
                             else "telegram-dm"),
                    model=model,
                    raw_content=raw_model_content,
                    chat_id=chat_id,
                    user_id=user_id,
                    post_cleanup=(content if salvaged_from_intermediate
                                  else ""),
                    intermediate_content=intermediate,
                    tool_calls_made=salvaged_tool_names,
                )

            # Stash the reasoning so /think can surface it on request.
            # Empty stash (no thinking returned, or model doesn't expose
            # one) overwrites any prior — /think should always reflect
            # the latest reply, even if that reply had no reasoning.
            self._last_thinking[user_id] = thinking

            # Persist the user turn UNCONDITIONALLY, regardless of
            # whether the model produced a usable reply. Earlier
            # versions only persisted inside `if content:` — meaning
            # an empty model reply caused the user's message to vanish
            # from history entirely, and the kin couldn't see what the
            # user had said on the next turn. The user turn is real
            # input; it always counts. The assistant turn and any
            # tool round-trips only get appended when content actually
            # came back.
            #
            # Timestamps: user turn reuses the user_ts decided at the
            # top of this function (Telegram-provided send time when
            # present, now_iso otherwise — so the prefix the model
            # saw and the persisted `ts` are identical). Assistant
            # turn uses now_iso (when the kin produced the reply).
            # Tool round-trip turns aren't stamped — they all
            # belong "between" the user turn and the assistant
            # turn anyway, so order is unambiguous.
            persisted_user_turn = {"role": "user", "content": text, "ts": user_ts}
            if attachments:
                persisted_user_turn["attachments"] = list(attachments)
            # Persist sender info alongside the user turn so subsequent
            # history-replays (this same DM thread on the next message,
            # or /regen with msg=None) can rebuild the same inline
            # attribution prefix the model originally saw. sender_id is
            # always set; sender_attribution / sender_name only when
            # they have something useful in them. Mirrors the group
            # path's persistence shape so a future migration can treat
            # DM and group turns uniformly.
            persisted_user_turn["sender_id"] = user_id
            if sender_attribution:
                persisted_user_turn["sender_attribution"] = sender_attribution
            if sender_name:
                persisted_user_turn["sender_name"] = sender_name
            new_turns = [persisted_user_turn]
            authoring_confirm = None
            if content:
                new_turns.extend(added_turns)
                new_turns.append({
                    "role": "assistant", "content": content, "ts": now_iso(),
                })
                if salvaged_from_intermediate:
                    from kin_persistence import load_app_prompt
                    new_turns.append({
                        "role": "system",
                        "content": (
                            load_app_prompt(
                                "salvage_note", self.agent_name).replace(
                                    "{tools}",
                                    ", ".join(salvaged_tool_names) or "(none)")
                        ),
                        "ts": now_iso(),
                    })
                # Tool-roleplay detector: if the kin's content describes
                # tool work (`*reads the next 100 lines*`, `let me check
                # X`, `read_staging`) while no structured call fired,
                # append a corrective system note so next turn breaks
                # the pattern. See _maybe_build_roleplay_corrective_note.
                # In park mode the gesture IS the interface — never nudge the
                # kin to "call the tool instead" for narrating a park action.
                roleplay_note = None
                if not park_mode:
                    roleplay_note = self._maybe_build_roleplay_corrective_note(
                        content, added_turns, effective_tools,
                        surface="telegram-dm", model=model,
                        chat_id=chat_id, user_id=user_id,
                    )
                if roleplay_note:
                    new_turns.append({
                        "role": "system",
                        "content": roleplay_note,
                        "ts": now_iso(),
                    })
                # Authoring bridge: if the kin authored file content in text
                # (```write:<path>``` fence / *writes X* emote+fence) instead
                # of a write_file call it froze on, perform the write. Gated on
                # write tools being in the EFFECTIVE set for this surface, so a
                # read-bucket user can't make the kin write via fence. The
                # confirmation posts as its own message after the reply.
                authoring_note, authoring_confirm = self._run_authoring_bridge_telegram(
                    content, effective_tools, added_turns)
                if authoring_note:
                    new_turns.append({
                        "role": "system", "content": authoring_note, "ts": now_iso(),
                    })
                # Reading nudge: kin narrated reading content without loading it
                # and nothing was auto-attached this turn.
                read_nudge = self._maybe_read_nudge_telegram(
                    content, effective_tools, added_turns, shared_this_turn)
                if read_nudge:
                    new_turns.append({
                        "role": "system", "content": read_nudge, "ts": now_iso(),
                    })
            else:
                # Genuinely empty — intermediate was also empty (or
                # cleanup ate it). Mirror the group handler's
                # awareness-preserving persistence: any tool-loop
                # turns the kin produced go into history so the kin
                # sees their own engagement on next read, and a
                # system note tells them the operator saw
                # "[no reply from model]" (the placeholder posted
                # to chat in DMs) so they can address it next turn
                # if they meant to respond.
                if added_turns:
                    new_turns.extend(added_turns)
                # ...unless the silence was ours. Telling a kin "you produced
                # no reply" when someone pressed stop would have it apologise
                # next turn for something it didn't do.
                if not turn_stopped:
                    from kin_persistence import load_app_prompt
                    new_turns.append({
                        "role": "system",
                        "content": (
                            load_app_prompt("empty_reply_note",
                                            self.agent_name)
                        ),
                        "ts": now_iso(),
                    })
            try:
                self._append_turns_for(user_id, new_turns)
            except Exception as save_err:
                append_failure_log(
                    "save_failures.log",
                    self.agent_name,
                    f"telegram_history user_id={user_id}",
                    save_err,
                )
            if content:
                footer = self._build_tool_summary_footer(
                    added_turns, full_cfg)
                footer += self._build_recall_footer(full_cfg)
                # If the reply streamed in-place, finalize THAT message with the
                # cleaned text (returns True → don't also send). ed is None on
                # the non-tool path, so we fall through to a normal send.
                if not (_stream_ed and _stream_ed.finalize(content + footer)):
                    self._send_chunked(chat_id, content + footer)
                # "Show reasoning in chat" reached the model call and stopped
                # there: the kin thought, and the thinking was thrown away.
                # Posted as its own message rather than folded into the reply,
                # because the streamed message may only grow and this belongs
                # BEFORE the answer it explains, not appended after it.
                if show_thinking:
                    import turn_steering
                    _block = turn_steering.reasoning_block(
                        thinking, cfg=full_cfg)
                    if _block:
                        self._send_chunked(chat_id, _block)
            elif not turn_stopped:
                if not (_stream_ed and _stream_ed.finalize("[no reply from model]")):
                    self._send_chunked(chat_id, "[no reply from model]")
            # Stopped before a single word landed: the poll thread already
            # said "Stopping", so there's nothing to add. Deliberately no
            # finalize() either — overwriting the in-place message would wipe
            # the few words the kin HAD written, which is the opposite of
            # keeping what was written.

            # Authoring-bridge confirmation, posted as its own message
            # (append-only — never edits the reply), after the kin's words.
            if authoring_confirm:
                self._send_chunked(chat_id, authoring_confirm)

            # Park mode: turn the kin's action-emotes into real game moves,
            # post what happened, and record it so the kin responds to the
            # ground truth next turn. After the reply so the kin's words land
            # first; best-effort so it can never break the chat.
            if park_mode and content:
                self._route_park_emotes(content, user_id, chat_id)
            # Text-in/text-out park bridge (per-kin `park` setting = chat|keeper):
            # a `> command` in the reply runs for real. Independent of the emote
            # park_mode toggle above; best-effort so it can't break the reply.
            try:
                import park_keeper
                if content and park_keeper.kin_park_mode(self.agent_name) in (
                        "chat", "keeper"):
                    # `ask` is what lets the kin keep playing: after a move
                    # lands, put its own result in front of it and ask for the
                    # next words. Built here rather than inside the router
                    # because this is where the model, options and this turn's
                    # messages already are — rebuilding them down there would
                    # be a second copy of the send path, free to drift from
                    # this one.
                    _park_msgs = list(messages) + [
                        {"role": "assistant", "content": content}]

                    def _park_ask(_msgs=_park_msgs):
                        # The park note was appended to the kin's stored history
                        # by the router; mirror it into this turn's working
                        # list so the next ask actually sees the result.
                        _hist = self._load_history_for(user_id)
                        _last = _hist[-1] if _hist else None
                        if _last and _last.get("role") == "system":
                            _msgs.append({"role": "system",
                                          "content": _last.get("content", "")})
                        _r = llm_backend.chat_collect(
                            model, _msgs, options=options,
                            should_stop=lambda: self._turn_cancelled(user_id),
                            show_thinking=show_thinking,
                            cache=cache, cache_ttl=cache_ttl,
                            openrouter_provider=openrouter_provider,
                            max_context_tokens=self._max_context_tokens(full_cfg),
                            kin_name=self.agent_name,
                            surface=self._surface_label,
                            ollama_host=resolve_kin_ollama_host(
                                full_cfg.get("ollama_host_name", "")),
                        )
                        _next = (getattr(_r, "content", "") or "").strip()
                        if not _next or getattr(_r, "stopped", False):
                            return ""
                        _msgs.append({"role": "assistant", "content": _next})
                        # Append-only, like every other post on this surface.
                        self._send_chunked(chat_id, _next)
                        self._append_turns_for(
                            user_id,
                            [{"role": "assistant", "content": _next,
                              "ts": now_iso()}],
                        )
                        return _next

                    self._route_park_command(content, user_id, chat_id,
                                             ask=_park_ask)
            except Exception:
                pass
        except Exception as e:
            # Log every chat-side failure to telegram_failures.log so
            # the always-on diagnostic record captures errors that the
            # user only sees as a chat message and then forgets. The
            # surface mirror is `[error: {e}]` to the user; on disk we
            # also store the kin + user + chat_id for forensics.
            try:
                from kin_persistence import append_failure_log
                append_failure_log(
                    "telegram_failures.log",
                    self.agent_name,
                    f"dm_chat user_id={user_id} chat_id={chat_id}",
                    e,
                )
            except Exception:
                pass
            try:
                # Raw exception text can carry provider error bodies
                # and local file paths — operator-relevant, but not
                # something every allowlisted guest should see (audit
                # L-S6; the group path doesn't post errors at all for
                # the same reason). Share-with-desktop users are the
                # operator's own surface and keep the details.
                from chat_helpers import humanize_error
                _host = resolve_kin_ollama_host(
                    full_cfg.get("ollama_host_name", "")) or None
                if self._user_shares_desktop(user_id):
                    self._send_chunked(chat_id, "⚠️ " + humanize_error(
                        e, kin=self.agent_name, host=_host))
                else:
                    self._send_chunked(chat_id, "⚠️ " + humanize_error(
                        e, kin=self.agent_name, host=_host, redact=True))
            except Exception:
                pass
        finally:
            typing_stop.set()

    # ─── Group support ─────────────────────────────────────────────────

    def _is_bot_addressed(self, msg, text):
        """Did `msg` @-mention this bot, or reply to one of its
        messages? The mention-only participation policy uses this
        to decide whether to engage. Returns False if bot identity
        hasn't been cached yet (getMe failure / cold start) — safer
        to stay silent than to engage by guess.

        Photo / document messages carry their mention entities in
        `caption_entities` rather than `entities`, so we check both.
        """
        if self._bot_user_id is None:
            return False
        reply = msg.get("reply_to_message") or {}
        reply_from = reply.get("from") or {}
        if reply_from.get("id") == self._bot_user_id:
            return True
        # Check both fields — text messages use `entities`, photo /
        # document messages with a caption use `caption_entities`.
        entities = (msg.get("entities") or []) + (msg.get("caption_entities") or [])
        for ent in entities:
            etype = ent.get("type")
            if etype == "mention" and self._bot_username:
                off = ent.get("offset", 0)
                length = ent.get("length", 0)
                # Telegram entity offsets are UTF-16 code units, not
                # Python code points — slice in UTF-16 space or any
                # emoji before the mention shifts the span and the
                # mention never matches (audit M-T2).
                mention_text = _utf16_slice(text or "", off, length)
                if mention_text.lstrip("@").lower() == self._bot_username:
                    return True
            elif etype == "text_mention":
                ent_user = ent.get("user") or {}
                if ent_user.get("id") == self._bot_user_id:
                    return True
        return False

    def _strip_bot_mention(self, msg, text):
        """Remove the @-mention of this bot from `text` so the model
        sees a cleaner message body. Best-effort, strips the first
        matching mention only. Returns text unchanged if no mention
        of this bot is present or bot identity is unknown.

        For photo / document messages, the mention entities are in
        `caption_entities`; we union both lists since whichever the
        message has is the one the offsets apply to (Telegram emits
        captions and texts in mutually exclusive ways for one
        message).
        """
        if not text or self._bot_user_id is None:
            return text
        entities = (msg.get("entities") or []) + (msg.get("caption_entities") or [])
        for ent in entities:
            etype = ent.get("type")
            # Entity offsets are UTF-16 code units (audit M-T2) —
            # slicing/removing in code-point space removes the wrong
            # span as soon as an emoji precedes the mention.
            if etype == "mention" and self._bot_username:
                off = ent.get("offset", 0)
                length = ent.get("length", 0)
                mention_text = _utf16_slice(text, off, length)
                if mention_text.lstrip("@").lower() == self._bot_username:
                    return _utf16_remove(text, off, length).strip()
            elif etype == "text_mention":
                ent_user = ent.get("user") or {}
                if ent_user.get("id") == self._bot_user_id:
                    off = ent.get("offset", 0)
                    length = ent.get("length", 0)
                    return _utf16_remove(text, off, length).strip()
        return text

    def _sender_display_name(self, msg):
        """Pick an attribution name for a group message sender: the
        @username when present (most distinctive), then full name,
        then a `user_<id>` fallback. Legacy: was used as the
        `[Name]:` prefix on user turns in group history. Kept for
        back-compat; new turns also carry `sender_attribution` from
        `_sender_attribution` below, which is what the prompt-build
        path now uses.

        Output is run through `sanitize_for_prompt_literal` so a
        sender who renames themselves to include newlines, RTL-
        override, or zero-width chars can't break the prompt's
        structural framing when this string gets embedded as a
        bracket prefix. See `kin_persistence.sanitize_for_prompt_literal`."""
        from kin_persistence import sanitize_for_prompt_literal
        sender = msg.get("from") or {}
        uname = sender.get("username")
        if uname:
            return sanitize_for_prompt_literal(f"@{uname}")
        first = sender.get("first_name") or ""
        last = sender.get("last_name") or ""
        full = (first + " " + last).strip()
        if full:
            return sanitize_for_prompt_literal(full)
        return f"user_{sender.get('id', 'unknown')}"

    def _sender_attribution(self, msg):
        """Format the inline-attribution string the kin sees on each
        group user turn. Shape: 'Display Name (@username)' when both
        are available, falling back gracefully — '@username' alone,
        'Display Name' alone, or 'user_<id>' last resort.

        Why inline (not a preceding system note): OpenRouter routing
        to Anthropic collects every role=system message and
        concatenates them at the top of the prompt — so per-turn
        system notes get dissociated from the user turns they were
        supposed to introduce. The inline prefix travels with its
        own content block, surviving that concatenation. The format
        ([Sender] without a colon) is also less of an impersonation
        attractor than the historic '[Name]:' shape — combined with
        the existing cleanup chain and the system-prompt 'don't
        speak for others' rule, that's the safety story.

        Output is run through `sanitize_for_prompt_literal` so a
        sender who renames themselves to include newlines, RTL-
        override, or zero-width chars can't break the prompt's
        structural framing when this string gets embedded as a
        bracket prefix on every user turn. See
        `kin_persistence.sanitize_for_prompt_literal`."""
        from kin_persistence import sanitize_for_prompt_literal
        sender = msg.get("from") or {}
        uname = (sender.get("username") or "").strip()
        first = (sender.get("first_name") or "").strip()
        last = (sender.get("last_name") or "").strip()
        full = (first + " " + last).strip()
        if full and uname:
            return sanitize_for_prompt_literal(f"{full} (@{uname})")
        if uname:
            return sanitize_for_prompt_literal(f"@{uname}")
        if full:
            return sanitize_for_prompt_literal(full)
        return f"user_{sender.get('id', 'unknown')}"

    def _handle_group_message(self, msg, user_id, chat_id,
                              chat_type, chat_title, *, attachments=None):
        """Handle a normal (non-command) message from a group or
        supergroup that's listed in cfg.telegram.groups.

        Behavior:
          - Sender is already known to be in allow_from (caller gates
            on that). Non-allowed senders never reach this function.
          - Participation gate: per-group policy. "mention_only"
            requires @-mention or reply-to-bot to engage; "always"
            engages on every message (and needs BotFather privacy
            mode off to even receive non-mention messages).
          - History: per-group, stored under f"group:{chat_id}" in
            the same telegram_history.json. Sender attribution via
            [SenderName]: prefix on each user turn so the kin can
            distinguish speakers. Mirrors the room pattern; same
            anti-impersonation cleanup (strip_self_tag /
            strip_trailing_other_speakers) applies on outbound.
          - Tools: sender's existing user_tools bucket. No per-group
            override.
          - Empty replies / errors: silent in groups. A public
            "[no reply from model]" or "[error: ...]" in a shared
            chat is noisy and exposes internal state."""
        from kin_persistence import (
            append_failure_log,
            build_system_prompt,
            load_agent_config,
            resolve_kin_ollama_host,
            load_kin_tools,
            now_iso,
            sanitize_for_prompt_literal,
        )
        from chat_helpers import (
            clean_kin_reply,
            format_ts_prefix,
            scan_intermediate_tool_content,
            speaker_attribution_prefix,
            strip_self_timestamp,
            strip_tool_summary_footer,
        )

        # Only a local (non-openrouter/) model needs the ollama
        # package (audit M-T6). Log the refusal instead of dying
        # silently — the group convention is no error posts, so the
        # log is the only place this failure can be seen at all.
        if ollama is None and not self._model_is_openrouter():
            try:
                append_failure_log(
                    "telegram_failures.log",
                    self.agent_name,
                    f"group chat_id={chat_id}",
                    "ollama package not installed and the kin's model "
                    "is not openrouter/-prefixed — cannot reply",
                )
            except Exception:
                pass
            return

        # Surface label for usage.log — tags both this handler's
        # chat() call and _run_tool_loop_telegram (read via
        # self._surface_label there) as group traffic.
        self._surface_label = "telegram-group"

        # Photo / document messages carry the user's words in `caption`
        # instead of `text`. Either is valid input for the kin; if both
        # are empty we have no message to respond to (mention-only
        # gating below would catch an attachment with no caption too,
        # since there'd be no text-mention to gate on — only a
        # reply-to-bot photo would pass the engagement gate without a
        # caption).
        text = (msg.get("text") or msg.get("caption") or "").strip()
        if not text and not attachments:
            return

        cfg = self.get_config() or {}
        groups = cfg.get("groups") or {}
        group_entry = groups.get(str(chat_id)) or {}
        policy = (group_entry.get("policy") or "mention_only").lower()
        addressed = self._is_bot_addressed(msg, text)
        if policy == "mention_only" and not addressed:
            return

        cleaned_text = self._strip_bot_mention(msg, text)
        if not cleaned_text and not attachments:
            # User @-mentioned the bot with no other text and no image —
            # nothing to reply to.
            return

        sender_name = self._sender_display_name(msg)
        sender_attribution = self._sender_attribution(msg)
        # Operator-set per-user label (Settings -> Telegram -> Users)
        # wins over the Telegram-derived "Display Name (@username)",
        # mirroring the DM path in _handle_normal_message. Without this
        # the group path always showed the raw Telegram account name
        # even when the operator had labeled the sender (e.g. "SpeakerFifteen").
        # Resolved at build time only (current turn + replay loop below);
        # conversation.jsonl keeps the raw Telegram attribution as ground
        # truth. JSON stores the int user_id as a string key, try both.
        labels_map = cfg.get("user_labels") or {}

        def _label_for(sender_id):
            if sender_id is None:
                return ""
            try:
                return (labels_map.get(str(sender_id))
                        or labels_map.get(sender_id) or "").strip()
            except Exception:
                return ""

        _curr_label = _label_for(user_id)
        message_date = msg.get("date")
        token = (cfg.get("bot_token") or "").strip()

        typing_stop = threading.Event()

        def keep_typing():
            # Log only the first failure in THIS TURN — mirrors the DM
            # handler's keep_typing (the group copy had lost the T15
            # failure-logging in an earlier edit; restored).
            logged_failure = False
            while not typing_stop.is_set():
                try:
                    telegram_api_call(token, "sendChatAction",
                                      {"chat_id": chat_id, "action": "typing"},
                                      timeout=10)
                except Exception as e:
                    if not logged_failure:
                        try:
                            append_failure_log(
                                "telegram_failures.log",
                                self.agent_name,
                                f"sendChatAction chat_id={chat_id} (typing indicator)",
                                e,
                            )
                        except Exception:
                            pass
                        logged_failure = True
                if typing_stop.wait(4.0):
                    break

        typing_thread = threading.Thread(target=keep_typing, daemon=True)
        typing_thread.start()

        try:
            soul = self.get_soul() or ""
            memory = ""
            try:
                memory = self.get_memory() or ""
            except Exception:
                memory = ""
            model, options = self.get_model_options()

            # Non-vision model with an incoming image: apologize in-
            # group (groups normally stay silent on errors, but this
            # one is sender-facing and worth surfacing — otherwise the
            # sender thinks the bot ignored their picture), then drop
            # the attachment refs and continue with caption-as-text.
            # If there's no caption either, bail after the apology.
            if attachments and not llm_backend.model_supports_images(model):
                self._send_vision_apology(chat_id)
                attachments = None
                if not cleaned_text:
                    typing_stop.set()
                    return

            user_ts = self._user_ts_from_message_date(message_date)

            history = self._load_group_history(chat_id)

            # Resolve the effective tool set first (kin's tools.json ∩ the
            # sender's bucket) so the base prompt's tool/memory scaffolding
            # is fenced to only what's enabled this turn. Reused for the
            # tool-path decision below.
            kin_tool_names = load_kin_tools(self.agent_name)
            bucket = self._user_tool_bucket(user_id)
            from tools._buckets import filter_tool_names
            effective_tools = filter_tool_names(kin_tool_names, bucket)

            messages = []
            sys_prompt = build_system_prompt(soul, memory, enabled_tools=effective_tools,
                                             kin_name=self.agent_name)
            if sys_prompt.strip():
                # group_entry["label"] is operator-controlled (Settings);
                # chat_title comes from Telegram's group metadata, which
                # any group admin can set. Either could embed control
                # chars that break the system prompt's structural framing
                # ("You are participating in a Telegram group called \"X\"."
                # → an X containing \n\nIgnore previous instructions
                # would split the prompt). Sanitize before embedding.
                group_label = sanitize_for_prompt_literal(
                    group_entry.get("label")
                    or chat_title
                    or f"group {chat_id}"
                )
                # Brief context note: just "you're in group X." Sender
                # identification rides INSIDE each user message as a
                # "[Display Name (@username)]" prefix after the
                # timestamp, so the kin doesn't need to be told how
                # to parse it — the structure is self-explanatory.
                sys_prompt += (
                    f"\n\nYou are participating in a Telegram group "
                    f"called \"{group_label}\"."
                )
                messages.append({"role": "system", "content": sys_prompt})

            # Per-message sender attribution lives INLINE in the user
            # content (as "[TIMESTAMP] [Display Name (@username)]
            # content"), not as a preceding role=system note. The
            # system-note pattern was the pre-2026-05-23 design, and
            # it worked for local Ollama (which keeps the message
            # list linear in the raw prompt) — but OpenRouter routing
            # to Anthropic collects every role=system message and
            # concatenates them into Anthropic's single top-level
            # system field, dissociating each "next message is from
            # X" note from the user turn it was meant to introduce.
            # Inline attribution travels with its own content block,
            # surviving that concatenation across every provider.
            #
            # Format choice:
            # "[Display Name (@username)]" without a colon between
            # the bracket and the content. The no-colon shape is far
            # less of an impersonation attractor than the historic
            # "[Name]:" speaker-turn-token form. Combined with the
            # existing cleanup chain (strip_self_tag /
            # strip_leading_speaker_tag / strip_trailing_other_speakers)
            # and the system-prompt "don't speak for others" rule,
            # this is the layered safety story.
            #
            # Backwards compat: legacy stored user turns have either
            # the old "[@username]: content" embedded prefix OR no
            # attribution at all (sender_name=None on rows from
            # before the capture landed). Strip the legacy prefix
            # when found so it doesn't duplicate the new inline one.
            # (Pattern lives at module level — see _LEGACY_SENDER_RE —
            # to avoid recompiling on every group message, audit T18.)
            legacy_sender_re = _LEGACY_SENDER_RE
            emitted_senders = set()

            def _emit_user_with_sender(turn_sender, turn_ts, turn_content,
                                      turn_attachments=None):
                """Emit one user turn with sender attribution INLINE
                in the content: "[TIMESTAMP] [Display Name (@username)]
                content". When the sender is unknown (legacy row with
                no sender_name / sender_attribution), the [Sender]
                bracket is omitted and the content stays
                "[TIMESTAMP] content".

                turn_sender is sanitized on the read side as well as
                at capture time — defense-in-depth for legacy stored
                values written before the sanitizer landed."""
                ts_prefix = format_ts_prefix(turn_ts)
                # speaker_attribution_prefix sanitizes, drops a trailing
                # colon, and unwraps brackets a legacy row already carries
                # (imports used to store "[SpeakerOne]", which wrapped twice).
                sender_prefix = speaker_attribution_prefix(turn_sender)
                new_user = {
                    "role": "user",
                    "content": ts_prefix + sender_prefix + (turn_content or ""),
                }
                if turn_attachments:
                    new_user["attachments"] = list(turn_attachments)
                messages.append(new_user)
                # Remember every name we showed the model this turn. The
                # reply cleanup below uses it to catch a reply that opens as
                # one of them — the "[SpeakerOne] " form, which the colon-anchored
                # strippers are blind to. Collected here rather than derived
                # again afterwards so the two can't drift: what we caught is
                # exactly what we showed.
                if sender_prefix:
                    emitted_senders.add(sender_prefix.strip().strip("[]").strip())

            for h in history:
                if not isinstance(h, dict):
                    messages.append(h)
                    continue
                h_role = h.get("role")
                h_content = h.get("content")
                if h_role == "user" and isinstance(h_content, str):
                    # Operator label (resolved by the turn's stored
                    # sender_id) wins so historical turns relabel too
                    # without rewriting conversation.jsonl; then the
                    # stored "Display Name (@username)" attribution; then
                    # legacy sender_name; final fallback is parsing a
                    # "[Name]: " prefix from the very oldest rows that
                    # predate even sender_name capture.
                    h_sender = (_label_for(h.get("sender_id"))
                                or h.get("sender_attribution")
                                or h.get("sender_name") or "")
                    clean = h_content
                    m = legacy_sender_re.match(clean)
                    if m:
                        legacy_name = m.group(1)
                        if not h_sender:
                            h_sender = legacy_name
                        clean = clean[m.end():]
                    h_atts = h.get("attachments") if isinstance(h.get("attachments"), list) else None
                    _emit_user_with_sender(h_sender, h.get("ts"), clean, h_atts)
                    continue
                # Assistant / tool / system turns pass through unchanged.
                messages.append(h)

            # Current user turn: sender_attribution carries the
            # "Display Name (@username)" form for this message's
            # sender. Inlined into content by _emit_user_with_sender.
            # Attachments (if any) go on the message; llm_backend.chat()
            # expands them based on the provider when it dispatches.
            _emit_user_with_sender(_curr_label or sender_attribution,
                                   user_ts, cleaned_text, attachments)

            full_cfg = load_agent_config(self.agent_name) or {}
            cache = bool(full_cfg.get("cache", True))
            cache_ttl = str(full_cfg.get("cache_ttl", "auto"))
            openrouter_provider = llm_backend.build_openrouter_provider_routing(
                full_cfg.get("openrouter_provider_order"),
                bool(full_cfg.get("openrouter_allow_fallbacks", True)),
            )

            # Per-turn memory recall — same mechanism as the DM path; surface
            # the kin's own relevant depth inline on this user turn (no tool
            # call). Group is a conversational surface like the others. See
            # docs/design/per-turn-memory-retrieval.md. Fail-soft.
            self._last_recall_used = []
            try:
                from memory_recall import inject_into_messages
                messages, self._last_recall_used = inject_into_messages(
                    messages, self.agent_name,
                    num_ctx=int(full_cfg.get("num_ctx", 8192) or 8192),
                    cfg=full_cfg,
                    # Same list the reply-cleanup uses: exactly the names we
                    # showed the model this turn, so the two can't drift.
                    speaker_names=emitted_senders)
            except Exception:
                pass

            # No-tools memory: hand over the pending staging notes inline for a
            # kin that has no way to reach them. No-op otherwise. Group members
            # never see this — it rides the model's copy of the turn only.
            try:
                import toolless_memory
                messages, self._toolless_scopes = toolless_memory.inject(
                    messages, self.agent_name, effective_tools)
            except Exception:
                self._toolless_scopes = []

            # Reading bridge in GROUPS — this is where the read-gesturing
            # actually bites, since kin that live in groups are where it
            # comes up. "The sharing is the
            # loading": if the sender named a real file, place its TEXT in
            # front of the kin this turn. Gated on the SENDER being trusted —
            # read_file in THEIR effective tools — so a random group member
            # can't make the bot slurp arbitrary machine files into context,
            # but the operator sharing a file with the kin works.
            shared_this_turn = False
            if "read_file" in set(effective_tools or []):
                try:
                    import reading_bridge
                    _shared = reading_bridge.extract_shared_paths(
                        cleaned_text or text)
                    if _shared:
                        _block = reading_bridge.build_shared_context_block(
                            reading_bridge.read_shared_files(_shared))
                        if _block and messages:
                            messages.insert(len(messages) - 1,
                                            {"role": "system", "content": _block})
                            shared_this_turn = True
                except Exception:
                    pass

            # effective_tools resolved above (kin's tools.json ∩ bucket).
            show_thinking = bool(full_cfg.get("show_thinking", False))
            _stream_ed = None  # set by the tool path when it streams in-place
            # Stop button, same as the DM path. A stop is keyed to the person
            # who asked — one group member can't halt a reply being written
            # for someone else.
            self._begin_turn(user_id, chat_id)
            try:
                if effective_tools:
                    content, added_turns, thinking, _stream_ed = self._run_tool_loop_telegram(
                        model, messages, options, cache,
                        effective_tools, user_id, chat_id, full_cfg,
                    )
                else:
                    # Streamed with no render callback purely to make this
                    # path interruptible — see the DM path for why.
                    result = llm_backend.chat_collect(
                        model, messages, options=options,
                        should_stop=lambda: self._turn_cancelled(user_id),
                        show_thinking=show_thinking,
                        cache=cache, cache_ttl=cache_ttl,
                        openrouter_provider=openrouter_provider,
                        max_context_tokens=self._max_context_tokens(full_cfg),
                        kin_name=self.agent_name,
                        surface=self._surface_label,
                        ollama_host=resolve_kin_ollama_host(
                            full_cfg.get("ollama_host_name", "")),
                    )
                    content = (result.content or "").strip()
                    added_turns = []
                    thinking = (getattr(result, "thinking", "") or "").strip()
            finally:
                turn_stopped = self._turn_cancelled(user_id)
                self._end_turn(user_id)

            # Snapshot what the model actually produced before the
            # anti-impersonation cleanup chain runs. If `content` ends
            # up empty after cleanup, this is the forensic record of
            # whether the model returned empty or the cleanup ate the
            # reply. Logged below when content goes empty.
            raw_model_content = content

            # Pull any inline <thinking>...</thinking> markup out of
            # content and merge into the structured thinking field
            # BEFORE last-thinking stash, impersonation cleanup, and
            # persistence. Normalizes the shape so /think + group
            # display + saved record all see the structured form
            # regardless of whether the model used the reasoning
            # channel or leaked into content. See
            # chat_helpers.extract_inline_thinking.
            from chat_helpers import extract_inline_thinking
            content, thinking = extract_inline_thinking(content, thinking)

            # (No _last_thinking stash here: /think is DM-only — the
            # group command path gets the "DMs only" reply — and
            # _cmd_think reads by user_id, so a group-keyed stash was
            # a dead write. Removed per audit L-B2.)

            # clean_kin_reply owns the ORDER. The old sequence here stripped
            # the timestamp LAST, so a "[TS] [OtherSender]:" opening sailed
            # past strip_leading_speaker_tag (its regex needs ':' right after
            # ']') and the tag was then persisted bare. That never fired on
            # Telegram — other senders live in the `user` slot here, so no
            # "[Name]:" exemplar exists in the assistant slot to imitate —
            # but the gap was loaded. It fired in rooms on 2026-07-11.
            # Logs to LOGS_DIR/impersonation.log if it ever does.
            content, _imp = clean_kin_reply(
                content, self.agent_name, known_speakers=emitted_senders)
            # Defensive: strip any trailing "_used: ..._" footer the
            # model may have spontaneously generated. See
            # chat_helpers.strip_tool_summary_footer for rationale.
            content = strip_tool_summary_footer(content)
            content = (content or "").strip()

            # Empty final content — try to salvage from intermediate
            # tool-loop content before giving up. Confirmed pattern
            # (Haiku-4.5 + `note` tool, observed 3x on 2026-06-01):
            # the kin produces substantive content alongside a tool
            # call, then returns ~2 EOS tokens after the tool result
            # because it treats the tool as the action. The
            # intermediate content IS the kin's intended reply; the
            # post-tool empty is the bug.
            #
            # If intermediate is non-empty after the same cleanup
            # chain we ran on the final content, use it as the reply.
            # Otherwise log + silent-skip as before.
            #
            # As on the DM path: a stopped turn isn't an empty reply. No
            # salvage, and nothing written to empty_replies.log — that log is
            # for diagnosing faults, not for recording our own interruptions.
            salvaged_from_intermediate = False
            salvaged_tool_names = []
            if not content and not turn_stopped:
                intermediate, salvaged_tool_names = (
                    scan_intermediate_tool_content(added_turns))
                # Try the same cleanup chain on intermediate. If it
                # survives, it's our reply.
                candidate = (intermediate or "").strip()
                if candidate:
                    candidate, _drop_thinking = extract_inline_thinking(
                        candidate, "")
                    candidate, _imp = clean_kin_reply(candidate, self.agent_name)
                    candidate = strip_tool_summary_footer(candidate)
                    candidate = candidate.strip()
                if candidate:
                    content = candidate
                    salvaged_from_intermediate = True
                # Always log — operator should see both salvaged and
                # silent cases. The intermediate / tool_names fields
                # tell the story (substantive vs empty intermediate).
                self._log_empty_reply(
                    surface=("telegram-group-salvaged"
                             if salvaged_from_intermediate
                             else "telegram-group"),
                    model=model,
                    raw_content=raw_model_content,
                    chat_id=chat_id,
                    user_id=user_id,
                    post_cleanup=(content if salvaged_from_intermediate
                                  else ""),
                    intermediate_content=intermediate,
                    tool_calls_made=salvaged_tool_names,
                )

            # Persist the user turn UNCONDITIONALLY. An empty assistant
            # reply shouldn't make the user message vanish from group
            # history — the kin needs to see what was said even if it
            # couldn't respond this turn.
            #
            # Content is stored CLEAN — no "[@sender]:" prefix baked
            # in. The sender_name field is the structural carrier of
            # who sent it; build-time replay (in this same function
            # above) emits a system note from that field. Storing
            # clean content means future schema changes (e.g. moving
            # to OpenAI's `name` field, or richer envelope metadata)
            # don't have to wrestle a parsing tax out of every old
            # row.
            persisted_user_turn = {
                "role": "user",
                "content": cleaned_text,
                "ts": user_ts,
                "sender_id": user_id,
                "sender_name": sender_name,
                "sender_attribution": sender_attribution,
            }
            if attachments:
                persisted_user_turn["attachments"] = list(attachments)
            new_turns = [persisted_user_turn]
            authoring_confirm = None
            if content:
                new_turns.extend(added_turns)
                new_turns.append({
                    "role": "assistant",
                    "content": content,
                    "ts": now_iso(),
                })
                if salvaged_from_intermediate:
                    # Tell the kin what happened. They wrote a real
                    # reply alongside a tool call; their post-tool
                    # final was empty; we surfaced the pre-tool
                    # content as the actual reply so the operator
                    # saw their voice rather than silence. Without
                    # this note the kin would see their content in
                    # history twice (once on the tool-call turn,
                    # once on the final assistant turn) and might
                    # think they sent the message twice.
                    from kin_persistence import load_app_prompt
                    new_turns.append({
                        "role": "system",
                        "content": (
                            load_app_prompt(
                                "salvage_note", self.agent_name).replace(
                                    "{tools}",
                                    ", ".join(salvaged_tool_names) or "(none)")
                        ),
                        "ts": now_iso(),
                    })
                # Tool-roleplay detector for the group surface. Same
                # contract as the DM-side hook — fires when content
                # describes tool work but no structured call landed,
                # appends a corrective system note so the kin's next
                # read sees the misalignment. Group narration is rarer
                # than the DM's (groups do produce real tool calls in
                # practice) but the detector running here costs nothing
                # on quiet turns and catches the pattern if it migrates
                # surfaces.
                roleplay_note = self._maybe_build_roleplay_corrective_note(
                    content, added_turns, effective_tools,
                    surface="telegram-group", model=model,
                    chat_id=chat_id, user_id=user_id,
                )
                if roleplay_note:
                    new_turns.append({
                        "role": "system",
                        "content": roleplay_note,
                        "ts": now_iso(),
                    })
                # Authoring bridge (group surface — where write-gesturing bites
                # most). Same contract as the DM hook: if the kin authored file
                # content in text instead of a write_file call, perform the
                # write. Gated on write tools being in the effective set.
                authoring_note, authoring_confirm = self._run_authoring_bridge_telegram(
                    content, effective_tools, added_turns)
                if authoring_note:
                    new_turns.append({
                        "role": "system", "content": authoring_note, "ts": now_iso(),
                    })
                # Reading nudge — suppressed when a file was auto-attached this
                # turn (a trusted sender shared one; content is already present).
                read_nudge = self._maybe_read_nudge_telegram(
                    content, effective_tools, added_turns, shared_this_turn)
                if read_nudge:
                    new_turns.append({
                        "role": "system", "content": read_nudge, "ts": now_iso(),
                    })
            else:
                # Genuinely empty — intermediate was also empty (or
                # the cleanup chain ate everything). Three things
                # matter for kin awareness on next read:
                # (1) the tool-loop intermediate turns DID happen
                #     and should be in history — without them, the
                #     kin's next read shows no engagement on this
                #     message at all and they have no way to know
                #     they tried.
                # (2) when there was no tool loop (added_turns empty)
                #     AND no final content, NOTHING in history shows
                #     the kin attempted — same gap, just on the
                #     no-tools path.
                # (3) the kin should be told the operator saw silence
                #     so on the next turn they can acknowledge,
                #     elaborate, or just be aware.
                if added_turns:
                    new_turns.extend(added_turns)
                # (3) does not apply when the silence was ours — a kin told it
                #     produced nothing will apologise for someone else's stop.
                if not turn_stopped:
                    from kin_persistence import load_app_prompt
                    new_turns.append({
                        "role": "system",
                        "content": (
                            load_app_prompt("empty_reply_note_group",
                                            self.agent_name)
                        ),
                        "ts": now_iso(),
                    })
            try:
                self._append_group_history(chat_id, new_turns)
            except Exception as save_err:
                append_failure_log(
                    "save_failures.log",
                    self.agent_name,
                    f"telegram group_history chat_id={chat_id}",
                    save_err,
                )
            if content:
                footer = self._build_tool_summary_footer(
                    added_turns, full_cfg)
                footer += self._build_recall_footer(full_cfg)
                if not (_stream_ed and _stream_ed.finalize(content + footer)):
                    self._send_chunked(chat_id, content + footer)
                # "Show reasoning in chat" reached the model call and stopped
                # there: the kin thought, and the thinking was thrown away.
                # Posted as its own message rather than folded into the reply,
                # because the streamed message may only grow and this belongs
                # BEFORE the answer it explains, not appended after it.
                if show_thinking:
                    import turn_steering
                    _block = turn_steering.reasoning_block(thinking)
                    if _block:
                        self._send_chunked(chat_id, _block)
            # Authoring-bridge confirmation (basename-only — no full paths in a
            # multi-user group), posted as its own message after the reply.
            if authoring_confirm:
                self._send_chunked(chat_id, authoring_confirm)
            # Text-in/text-out park bridge, exactly as the DM path does it
            # (_handle_normal_message) and the desktop and the cron keeper.
            # This surface did not have it, and a group is where a kin is most
            # likely to be PLAYING with someone: a `> ` line simply did not
            # run. Not filtered, not refused, not logged -- the router was
            # never called, so nothing anywhere recorded that a move had been
            # asked for. Reported from a live group: three phrasings of the
            # same move -- `> make room roost`, `> make a new room called
            # "roost"`, `> make roost` -- all three correct, all three met
            # with silence. The game was running the whole time, and the
            # silence was read as the game being broken.
            #
            # Best-effort, after the reply, same as everywhere else: a park
            # error must never break a chat message that already went out.
            try:
                import park_keeper
                if content and park_keeper.kin_park_mode(self.agent_name) in (
                        "chat", "keeper"):
                    self._route_park_command(content, user_id, chat_id)
            except Exception:
                pass
        except Exception as e:
            # Full error -> the always-on log for forensics...
            append_failure_log(
                "telegram_failures.log",
                self.agent_name,
                f"group chat_id={chat_id}",
                e,
            )
            # ...and a brief, REDACTED human line to the group so the operator
            # (who is in the group) isn't left staring at silence. redact=True
            # keeps file paths / provider bodies out of a multi-user surface;
            # the full detail stays in the log above.
            try:
                from chat_helpers import humanize_error
                try:
                    _host = resolve_kin_ollama_host(
                        full_cfg.get("ollama_host_name", "")) or None
                except Exception:
                    _host = None
                self._send_chunked(chat_id, "⚠️ " + humanize_error(
                    e, kin=self.agent_name, host=_host, redact=True))
            except Exception:
                pass
        finally:
            typing_stop.set()

    def _load_group_history(self, chat_id):
        """Return the conversation history for this group. When the
        group has group_share_desktop on, the storage is unified in
        the kin's main conversation.jsonl — but reads are STILL
        scoped to messages tagged with this group's source-tag, so
        the kin doesn't see desktop or other-group context when
        replying here. Groups are multi-participant contexts; mixing
        them with the operator's desktop chat would confuse who the
        kin is talking to. Storage unification gives "wipe affects
        everything" semantics; read-time filtering keeps the
        per-surface voice straight.

        Without share-on, the segregated slice from _histories under
        "group:<chat_id>" is used directly (default behavior)."""
        if self._group_shares_desktop(chat_id):
            source_tag = f"telegram:group:{chat_id}"
            try:
                all_msgs = self._load_shared_conversation_cached()
                filtered = [
                    m for m in all_msgs
                    if isinstance(m, dict) and m.get("source") == source_tag
                ]
                return _drop_leading_orphan_tools(filtered)
            except Exception:
                return []
        history_key = f"group:{chat_id}"
        with self._histories_lock:
            return _drop_leading_orphan_tools(
                list(self._histories.setdefault(history_key, []))
            )

    def _append_group_history(self, chat_id, new_turns):
        """Persist `new_turns` to the right place for this group.
        When group_share_desktop is on, append to conversation.jsonl
        with source="telegram:group:<chat_id>" so the desktop sees
        them and clear-chat clears them. Otherwise, append to
        _histories under "group:<chat_id>" + save the segregated
        telegram_history.json (default behavior). Either way, fires
        the on_activity hook so the frame can tick the right
        distillation scope counter."""
        if self._group_shares_desktop(chat_id):
            from kin_persistence import (
                append_agent_conversation_turn,
                append_failure_log,
            )
            cid_str = str(chat_id)
            source_tag = f"telegram:group:{cid_str}"
            for turn in new_turns:
                tagged = dict(turn)
                tagged.setdefault("source", source_tag)
                try:
                    append_agent_conversation_turn(self.agent_name, tagged)
                except Exception as e:
                    append_failure_log(
                        "telegram_failures.log",
                        self.agent_name,
                        f"append_conversation group={chat_id} role={turn.get('role')}",
                        e,
                    )
        else:
            history_key = f"group:{chat_id}"
            cap = self._history_cap()
            with self._histories_lock:
                history = self._histories.setdefault(history_key, [])
                history.extend(new_turns)
                history = self._trim_history(history, cap)
                # Sweep leading tool-pair fragments only. Trailing-
                # sweep is OFF here for the same reason as the DM
                # path (see _append_turns_for comment): new_turns
                # ends in content / system / user by construction;
                # a future regression should surface loudly, not
                # silently lose data on disk.
                _drop_leading_orphan_tools(history, sweep_trailing_assistant_tcs=False)
                self._histories[history_key] = history
                save_telegram_history(self.agent_name, self._histories)
        if self.on_activity is not None:
            try:
                self.on_activity("group", chat_id)
            except Exception:
                pass

    def pop_group_history(self, chat_id):
        """Atomically remove a group's segregated history slice from
        the bot's in-memory state AND from disk. Returns the popped
        list of message dicts. Parallel to pop_user_history; used by
        the per-group share-with-desktop migration so a message
        arriving mid-migration can't be silently lost between the
        bot's in-memory dict and the file.

        Tries both `group:<int>` and `group:<str>` key forms (audit
        T5). _append_group_history always builds the key via
        f-string so the entry SHOULD be a string, but defending
        against a future caller writing a raw int chat_id costs
        nothing and matches pop_user_history's pattern."""
        from kin_persistence import append_failure_log
        keys_to_try = []
        try:
            as_int = int(chat_id)
            keys_to_try.append(f"group:{as_int}")
        except (TypeError, ValueError):
            pass
        str_key = f"group:{chat_id}"
        if str_key not in keys_to_try:
            keys_to_try.append(str_key)
        with self._histories_lock:
            slice_msgs = []
            for k in keys_to_try:
                popped = self._histories.pop(k, None)
                if popped:
                    slice_msgs.extend(popped)
            try:
                save_telegram_history(self.agent_name, self._histories)
            except Exception as e:
                append_failure_log(
                    "telegram_failures.log",
                    self.agent_name,
                    f"pop_group_history chat_id={chat_id}",
                    e,
                )
        return slice_msgs

    def pop_user_history(self, user_id):
        """Atomically remove a Telegram user's history slice from the
        bot's in-memory state AND from disk. Returns the popped list
        of message dicts (empty list if no history existed).

        Used by the share-with-desktop migration to grab the slice
        without racing the inference worker, which might be appending
        a new message to the same dict in parallel. Without this, the
        migration's load-from-disk could miss an in-flight message
        that the bot hadn't saved yet, and lose it on the next bot
        save.

        Pops the user under as the original value AND both its int
        and str forms, because the save/load round-trip turns int
        keys into str keys but the inference loop creates int-keyed
        entries from runtime appends (Telegram's API returns user
        ids as ints) — both forms can coexist in _histories at any
        given moment, and the caller's user_id might be either."""
        from kin_persistence import append_failure_log
        with self._histories_lock:
            keys_to_try = [user_id]
            try:
                as_int = int(user_id) if not isinstance(user_id, int) else None
                if as_int is not None:
                    keys_to_try.append(as_int)
            except (ValueError, TypeError):
                pass
            try:
                as_str = str(user_id) if not isinstance(user_id, str) else None
                if as_str is not None:
                    keys_to_try.append(as_str)
            except Exception:
                pass
            slice_msgs = []
            for k in keys_to_try:
                slice_msgs.extend(self._histories.pop(k, []) or [])
            try:
                save_telegram_history(self.agent_name, self._histories)
            except Exception as e:
                append_failure_log(
                    "telegram_failures.log",
                    self.agent_name,
                    f"pop_user_history user={user_id}",
                    e,
                )
        return slice_msgs

    def _max_context_tokens(self, cfg):
        """Input-context budget for a send: num_ctx minus ~2K reply
        headroom. Mirrors the desktop path (hearthkin _send_message) so
        the Telegram surfaces truncate oversized history the same way.
        Before this the Telegram chat + tool-loop calls passed nothing,
        so they sent the whole conversation every message regardless of
        num_ctx — a silent, compounding cost leak on the busiest surface."""
        return max(512, int((cfg or {}).get("num_ctx", 2048)) - 2000)

    def _run_tool_loop_telegram(self, model, messages, options, cache,
                                effective_tools, user_id, chat_id, full_cfg):
        """Run the tool-calling loop with Telegram-flavored display +
        approval. Returns (final_content, intermediate_turns_added).

        Each tool call posts two separate Telegram messages:
          1. "🔧 read_file({'path': 'foo.md'})" — the call
          2. "→ <result preview>" — the outcome
        Append-only; never edits a prior message. Final kin reply is
        sent as a separate message by the caller."""
        import tools as kin_tools

        # Pass the active model so capability-gated tools (use_webcam)
        # only register when the kin's model can actually consume the
        # resulting image attachment.
        try:
            tg_model, _opts = self.get_model_options()
        except Exception:
            tg_model = None
        # confine_paths revokes the absolute-path escape hatch for file tools
        # on this remote surface, so a remote/injected kin can't read or
        # overwrite arbitrary host files (audit D1). Confined by DEFAULT; the
        # operator can hand this kin desktop-equivalent reach by setting
        # `remote_unconfined_files` (Settings -> Tools -> Tool settings).
        # Read from the kin config, never from anything the model can set.
        _confine = not bool((full_cfg or {}).get("remote_unconfined_files"))
        schemas, executor = kin_tools.load_tools(
            effective_tools,
            context={"agent_name": self.agent_name, "confine_paths": _confine},
            model=tg_model,
        )
        # Wrap use_webcam with the per-user approval flow (ask /
        # auto / deny radio in Settings → Telegram → Users) if it's
        # in the executor at all. Done before the exec wrap so the
        # webcam dialog can fire for a non-exec-using kin too.
        if "use_webcam" in executor:
            executor = dict(executor)
            inner_webcam = executor["use_webcam"]
            executor["use_webcam"] = self._wrap_webcam_for_telegram(
                inner_webcam, user_id, chat_id,
            )
        # Wrap exec specifically with the Telegram approval flow, if
        # exec is in the executor at all. Other tools pass through.
        if "exec" in executor:
            executor = dict(executor)
            inner_exec = executor["exec"]
            tool_trust = (full_cfg.get("tool_trust") or "untrusted").strip().lower()
            executor["exec"] = self._wrap_exec_for_telegram(
                inner_exec, tool_trust, user_id, chat_id,
            )

        # Auto-inject tool-use nudge into system prompt — same hint the
        # desktop path uses for Gemma-family models that otherwise
        # refuse tools cold.
        messages = self._inject_tool_use_hint(
            list(messages), [s["function"]["name"] for s in schemas],
        )

        # Tool-call display is per-kin opt-in via the Telegram tab's
        # "Show tool calls in chat" toggle. Default off — most users
        # just want the kin's narrative reply. When on, each call posts
        # two append-only messages (call + result preview).
        tg_cfg = full_cfg.get("telegram") or {}
        show_tool_calls = bool(tg_cfg.get("show_tool_calls", False))
        # Progress-ping cadence for long tool loops. When the kin is
        # churning through many tool calls without producing chat-side
        # output, the typing indicator is the only feedback the user
        # gets — which feels indistinguishable from a hang. A periodic
        # progress ping (default 90s) tells the user "still working, N
        # tools so far". 0 disables. Honored only when show_tool_calls
        # is OFF — when ON, the per-call messages are already the
        # progress signal.
        try:
            progress_interval = int(tg_cfg.get("tool_progress_interval_secs", 90))
        except (TypeError, ValueError):
            progress_interval = 90
        import time
        progress_state = {
            "started_at": time.monotonic(),
            "last_ping_at": time.monotonic(),
            "call_count": 0,
            "tool_names": [],  # ordered list, deduped at display time
        }

        def _maybe_send_progress_ping():
            """Emit a periodic 'still working' message when the loop
            runs longer than the interval without chat-side output.
            Each ping is a fresh message (append-only — never edits a
            prior one, matching the documented convention)."""
            if show_tool_calls or progress_interval <= 0:
                return
            now = time.monotonic()
            if now - progress_state["last_ping_at"] < progress_interval:
                return
            progress_state["last_ping_at"] = now
            elapsed = int(now - progress_state["started_at"])
            mins = elapsed // 60
            secs = elapsed % 60
            elapsed_str = f"{mins}m {secs}s" if mins else f"{secs}s"
            # Dedupe tool-name list while preserving order so display
            # reads naturally ("read_file, edit_file, write_file").
            seen = set()
            distinct = []
            for n in progress_state["tool_names"]:
                if n not in seen:
                    seen.add(n)
                    distinct.append(n)
            tools_text = ", ".join(distinct) if distinct else "—"
            try:
                self._send_chunked(
                    chat_id,
                    f"⏳ still working ({elapsed_str}, "
                    f"{progress_state['call_count']} tool calls): "
                    f"{tools_text}",
                )
            except Exception:
                pass

        # on_tool_call is always installed. When "show tool calls" is on,
        # every call posts call + result (append-only, as before). When
        # it's off, successful calls stay silent — but a FAILED call
        # (malformed/truncated args, unknown tool, or the tool raising)
        # still posts a short message, so a broken tool call is never
        # invisible to the user. Either way, the progress-ping check
        # runs on every call so a long quiet loop becomes a periodic
        # "still working" message.
        def on_tool_call(name, args, result, is_error):
            try:
                progress_state["call_count"] += 1
                progress_state["tool_names"].append(name)
                if show_tool_calls:
                    args_str = self._format_tool_args(args)
                    self._send_chunked(chat_id, f"🔧 {name}({args_str})")
                    preview = self._tool_result_preview(result)
                    self._send_chunked(chat_id, f"→ {preview}")
                elif is_error:
                    # The error result already begins with "<tool>: ",
                    # so the preview alone carries the tool name.
                    preview = self._tool_result_preview(result)
                    self._send_chunked(chat_id, f"⚠️ {preview}")
                else:
                    _maybe_send_progress_ping()
            except Exception:
                pass

        show_thinking = bool(full_cfg.get("show_thinking", False))
        # Suffix the surface with "-tool" so the usage.log
        # distinguishes tool-loop traffic (which has multiple
        # blocking calls per user turn) from plain chat traffic.
        # Falls back to "telegram-tool" if _surface_label isn't set
        # (shouldn't happen — handlers set it before calling here —
        # but defensive).
        base_surface = getattr(self, "_surface_label", "telegram") or "telegram"
        tool_loop_surface = f"{base_surface}-tool"
        try:
            tool_result_cap = int((full_cfg or {}).get("tool_result_cap", 8000))
        except (TypeError, ValueError):
            tool_result_cap = 8000
        cache_ttl = str((full_cfg or {}).get("cache_ttl", "auto"))
        openrouter_provider = llm_backend.build_openrouter_provider_routing(
            (full_cfg or {}).get("openrouter_provider_order"),
            bool((full_cfg or {}).get("openrouter_allow_fallbacks", True)),
        )
        from kin_persistence import resolve_kin_ollama_host
        # In-place-edit streaming: render the kin's talking turn into ONE
        # message that fills in, throttled to stay clear of Telegram's edit
        # flood limits. The caller finalizes with the cleaned reply. Best-
        # effort — any API hiccup falls back to the caller's normal send.
        _stream_tok = (self.get_config() or {}).get("bot_token", "").strip()

        def _stream_log(action, n):
            # Diagnostic: one line per stream send/edit so streaming is
            # verifiable from disk (no need to catch the in-place edit live).
            # Many edits during a turn = streaming works; a lone send+edit =
            # the model handed us one chunk (e.g. Ollama buffering with tools).
            try:
                from kin_persistence import LOGS_DIR
                import datetime as _dt
                with open(LOGS_DIR / "telegram_stream.log", "a",
                          encoding="utf-8") as f:
                    f.write(
                        f"{_dt.datetime.now().isoformat(timespec='seconds')} "
                        f"kin={self.agent_name} chat={chat_id} "
                        f"{action} len={n}\n")
            except Exception:
                pass

        def _stream_send(text):
            _stream_log("send", len(text or ""))
            try:
                r = telegram_api_call(
                    _stream_tok, "sendMessage",
                    {"chat_id": chat_id, "text": text})
                return ((r or {}).get("result") or {}).get("message_id")
            except Exception:
                return None

        def _stream_edit(mid, text):
            _stream_log("edit", len(text or ""))
            telegram_api_call(
                _stream_tok, "editMessageText",
                {"chat_id": chat_id, "message_id": mid, "text": text})

        def _stream_clean(text):
            # Shape the live text the way the finished message will be shaped,
            # so the reply only ever grows. Same chain the caller runs at the
            # end; running it here too means a model opening with its own name
            # tag never shows that tag rather than showing it and having it
            # edited away.
            from chat_helpers import clean_kin_reply
            return clean_kin_reply(text, self.agent_name)[0]

        ed = _TelegramStreamEditor(_stream_send, _stream_edit,
                                   clean=_stream_clean)
        result = llm_backend.run_tool_loop(
            model, messages,
            tools=schemas, tool_executor=executor,
            options=options, cache=cache, cache_ttl=cache_ttl,
            openrouter_provider=openrouter_provider,
            on_tool_call=on_tool_call,
            on_content=ed.feed, on_turn=ed.reset_turn,
            show_thinking=show_thinking,
            max_context_tokens=self._max_context_tokens(full_cfg),
            tool_result_cap=tool_result_cap,
            kin_name=self.agent_name,
            surface=tool_loop_surface,
            max_iterations=int((full_cfg or {}).get("max_tool_iterations", 8) or 8),
            # The stop button: polled per streamed chunk and between tool
            # calls, so /cancel lands mid-sentence instead of waiting out a
            # long tool loop.
            should_stop=lambda: self._turn_cancelled(user_id),
            ollama_host=resolve_kin_ollama_host(
                (full_cfg or {}).get("ollama_host_name", "")),
        )
        content = (result.content or "").strip()
        added = list(getattr(result, "messages_added", []) or [])
        thinking = (getattr(result, "thinking", "") or "").strip()
        return content, added, thinking, ed

    def _wrap_webcam_for_telegram(self, inner_webcam, user_id, chat_id):
        """Gate the use_webcam tool call on the per-user
        webcam_permission setting (cfg.telegram.user_webcam_permission).

        Three behaviors:
          - "deny": refuse without prompting; return a denial string
            the model can read and apologize to the user about.
          - "auto": pass through to the inner capture executor.
          - "ask" (or unset / unknown): pop a wx dialog on the
            operator's desktop via self.request_webcam_approval. If
            the operator allows, run the capture. If they deny or
            the callback isn't wired (shouldn't happen in practice
            but is the safety default), refuse.

        Sends a Telegram-side "checking with operator…" message
        before blocking on the approval so the user knows why their
        request seems to hang. No-ops the message if it fails to
        send — the photo flow shouldn't fail just because the status
        update couldn't post."""
        def wrapped(args):
            cfg = self.get_config() or {}
            perm_map = cfg.get("user_webcam_permission") or {}
            perm = perm_map.get(str(user_id)) or perm_map.get(user_id) or "ask"
            perm = (perm or "ask").strip().lower()
            if perm not in ("ask", "auto", "deny"):
                perm = "ask"

            if perm == "deny":
                return json.dumps({
                    "ok": False,
                    "error": "Webcam capture refused for this user "
                             "(per-user webcam permission is set to "
                             "'always deny').",
                })

            if perm == "auto":
                return inner_webcam(args)

            # perm == "ask": defer to operator via wx dialog.
            if self.request_webcam_approval is None:
                return json.dumps({
                    "ok": False,
                    "error": "Webcam capture needs operator approval but "
                             "no approval channel is configured.",
                })

            label = ""
            try:
                label_map = cfg.get("user_labels") or {}
                label = label_map.get(str(user_id)) or label_map.get(user_id) or ""
            except Exception:
                pass

            # Best-effort hint to the user — never blocks the flow.
            try:
                self._send_chunked(
                    chat_id,
                    "📷 Asking the operator to approve a webcam "
                    "capture — one moment…",
                )
            except Exception:
                pass

            try:
                decision = self.request_webcam_approval(label, user_id)
            except Exception as e:
                return json.dumps({
                    "ok": False,
                    "error": f"Webcam approval failed: {e}",
                })

            if decision == "allow":
                return inner_webcam(args)
            if decision == "unavailable":
                # Nobody was shown the request (Hearthkin closing, or the
                # approval dialog failed to open). Say that — don't tell the
                # user the operator refused, which used to be the blanket
                # message for every non-allow outcome.
                return json.dumps({
                    "ok": False,
                    "error": "Couldn't reach the operator to approve the "
                             "webcam capture right now — nobody refused it, "
                             "the request just couldn't be put to them. Try "
                             "again in a moment.",
                })
            return json.dumps({
                "ok": False,
                "error": "The operator saw the webcam request and declined it.",
            })

        return wrapped

    def _wrap_exec_for_telegram(self, inner_exec, tool_trust, user_id, chat_id):
        """Wrap the exec executor with Telegram-side approval. Mirrors
        the desktop _wrap_exec_executor on the Hearthkin frame: checks
        the per-kin allowlist, then trust level, then asks the user via
        Telegram message if needed.

        Order on the Telegram surface (deliberately stricter than the
        desktop wrapper):
          1. Denylist match → denied, regardless of allowlist or trust
             (audit T16). A future denylist tightening shouldn't be
             escapable just because the operator allowlisted a related
             command months ago.
          2. Exact-match in kin's exec_allowlist.json → run, no prompt.
          3. tool_trust in ('trusted', 'full') → run.
          4. Otherwise → ask the user in Telegram chat.

        Note that 'full' does NOT bypass denylist + approval here, even
        though the desktop-side wrapper allows that bypass. Reason: an
        operator setting tool_trust=full for their own desktop-use
        convenience would not reasonably expect that same setting to
        also disable all gating for any Telegram user they later assign
        the 'full' bucket. Telegram is a multi-user surface; tool_trust
        is a per-kin (single) value; the safer reading is "tool_trust
        governs the desktop's local convenience, denylist + approval
        always apply to remote requests."
        """
        from tools._exec_denylist import match_denylist
        from tools._exec_state import is_in_allowlist, add_to_allowlist

        def wrapped(args):
            command = (args or {}).get("command") or ""
            background = bool((args or {}).get("background"))
            if not command:
                # An empty command used to pass straight through to
                # inner_exec UNGATED (audit L-S8). Harmless while exec
                # only runs `command`, but if exec ever grows a
                # non-command mode this would be a silent gate bypass.
                # Refuse with a model-readable error instead.
                return ("exec: no command provided — nothing to run. "
                        "Pass the shell command in the `command` "
                        "argument.")
            # Denylist gate runs first (audit T16): a denylisted shape
            # is never run on the Telegram surface, regardless of
            # remembered approval. The denylist is the operator's
            # "never run this from a remote surface" line, and
            # allowlist entries that pre-date a tightening shouldn't
            # be retroactively safe.
            if match_denylist(command):
                return "[denied by denylist]"
            # Remembered approval — straight through. Scoped to THIS Telegram
            # user (audit E1): a command the operator remembered at the
            # desktop, or that a different Telegram user had approved, does
            # not silently auto-run for this user.
            surface_key = f"telegram:{user_id}"
            if is_in_allowlist(self.agent_name, command, surface=surface_key):
                return inner_exec(args)
            # tool_trust trusted/full on the Telegram surface: historically
            # this ran without an approval prompt (denylist-only). The
            # 2026-07 audit (B1) flagged that the operator's local-convenience
            # trust dial silently disables per-command approval for REMOTE
            # users. So trusted/full now auto-runs a remote exec ONLY when the
            # operator has explicitly set `remote_unattended_exec: true` in
            # the kin config; otherwise we fall through to chat approval.
            if tool_trust in ("trusted", "full"):
                try:
                    from kin_persistence import load_agent_config
                    _cfg = load_agent_config(self.agent_name) or {}
                except Exception:
                    _cfg = {}
                if _cfg.get("remote_unattended_exec"):
                    return inner_exec(args)
            # Otherwise ask the user via Telegram chat.
            decision = self._request_exec_approval_telegram(
                user_id, chat_id, command,
                f"background={background}" if background else "",
            )
            if decision == "remember":
                try:
                    add_to_allowlist(self.agent_name, command, surface=surface_key)
                except Exception:
                    pass
                return inner_exec(args)
            if decision == "allow":
                return inner_exec(args)
            # Every non-approval outcome used to collapse to the single
            # string "[denied by user]" — including a timeout nobody saw, a
            # prompt that never sent, and an eviction by a newer request.
            # The kin then truthfully reported to the operator that they had
            # refused something they were never shown. Each outcome now says
            # what actually happened, and explicitly tells the kin when
            # NOBODY refused, so it doesn't relay a denial that never was.
            return _DENY_RESULTS.get(decision, _DENY_RESULTS["deny"])

        return wrapped

    def _request_exec_approval_telegram(self, user_id, chat_id, command, args_summary):
        """Block the worker thread until the user responds via
        Telegram chat. Returns 'allow' / 'remember' / 'deny'. Auto-
        denies on timeout (default 10 min, per-kin configurable via
        cfg['telegram']['approval_timeout_secs'])."""
        cfg = self.get_config() or {}
        timeout = int(cfg.get("approval_timeout_secs", 600) or 600)
        if timeout < 30:
            timeout = 30  # sanity floor — anything less is impractical for human reply time
        # Render the wait in words. Plain `timeout // 60` printed "0 min" for
        # any sub-minute timeout (the 30s floor included), which reads as a
        # bug in a message whose whole job is to be trusted.
        if timeout < 60:
            wait_label = f"{timeout} seconds"
        elif timeout < 120:
            wait_label = "1 minute"
        else:
            wait_label = f"{timeout // 60} minutes"

        approval = _PendingApproval(
            event=threading.Event(),
            command=command,
            args_summary=args_summary,
            # Where the prompt is posted — resolution is accepted from
            # this same chat or a DM with the bot (audit M-T1).
            chat_id=chat_id,
        )
        from kin_persistence import append_approval_log, append_failure_log

        with self._pending_lock:
            # If there's somehow already a pending approval for this user
            # (race / edge case), release it so the old worker unblocks
            # before we register the new one. It is marked 'superseded'
            # rather than 'deny': the old worker used to be told the user
            # had refused, which was never true and which the kin then
            # repeated to the operator.
            old = self._pending_approvals.get(user_id)
            if old is not None:
                old.decision = "superseded"
                old.event.set()
                append_approval_log(self.agent_name, "superseded",
                                    user_id=user_id, command=old.command)
            self._pending_approvals[user_id] = approval

        append_approval_log(self.agent_name, "asked", user_id=user_id,
                            chat_id=chat_id, command=command,
                            timeout_secs=timeout)

        # Tell the operator's DESKTOP that a remote kin is blocked waiting on
        # them. Without this the only signal was a Telegram message in
        # whichever chat the request came from — invisible if they were
        # focused elsewhere, which is exactly how a request sat unseen until
        # it timed out. Best-effort and never fatal to the approval flow.
        try:
            if self.on_approval_needed is not None:
                self.on_approval_needed(self.agent_name, command, timeout)
        except Exception:
            pass

        try:
            extras = f"\n({args_summary})" if args_summary else ""
            self._send_chunked(
                chat_id,
                f"⚠️ {self.agent_name} wants to run:\n\n"
                f"`{command}`{extras}\n\n"
                f"Reply with:\n"
                f"  yes / allow / ok — run once\n"
                f"  remember / save / trust — run and add to allowlist\n"
                f"  no / deny — refuse\n\n"
                f"You can reply right here in this chat, or in a DM "
                f"with me — either works.\n"
                f"(I'll give up in {wait_label} if you don't respond.)"
            )
        except Exception as e:
            # The send failure used to be swallowed entirely: the prompt never
            # arrived, nothing was logged, and the worker then sat blocked for
            # the full timeout before reporting a "denial" to a kin whose
            # operator had never been asked anything. Fail fast and say so.
            append_approval_log(self.agent_name, "undelivered",
                                user_id=user_id, chat_id=chat_id,
                                command=command, error=repr(e))
            append_failure_log("telegram_failures.log", self.agent_name,
                               f"exec approval prompt chat_id={chat_id}", e)
            with self._pending_lock:
                if self._pending_approvals.get(user_id) is approval:
                    self._pending_approvals.pop(user_id, None)
            return "undelivered"

        signalled = approval.event.wait(timeout=timeout)
        with self._pending_lock:
            # Identity check — if a newer approval evicted us by
            # re-binding self._pending_approvals[user_id], we'd
            # otherwise pop the NEW worker's entry and strand it
            # (audit T6). The response-handler path also pops, so
            # this branch is only the cleanup for our own entry.
            if self._pending_approvals.get(user_id) is approval:
                self._pending_approvals.pop(user_id, None)
        if not signalled:
            # Timeout. Reported as its own outcome, not as a denial: nobody
            # refused this, the operator simply never answered (very often
            # because they never saw it at all).
            append_approval_log(self.agent_name, "timeout", user_id=user_id,
                                chat_id=chat_id, command=command,
                                timeout_secs=timeout)
            try:
                self._send_chunked(
                    chat_id,
                    f"No answer after {wait_label}, so I didn't run it "
                    f"— {self.agent_name} is carrying on without that command. "
                    f"Nothing was refused; just ask again if you want it.",
                )
            except Exception:
                pass
            return "timeout"
        append_approval_log(self.agent_name, approval.decision or "deny",
                            user_id=user_id, chat_id=chat_id, command=command)
        return approval.decision

    def _format_tool_args(self, args):
        """Compact one-line repr of tool arguments for display. Lops
        long values (e.g. a multi-KB content blob being written) so
        the Telegram message stays scannable."""
        if not isinstance(args, dict) or not args:
            return ""
        parts = []
        for k, v in args.items():
            # Plain strings render without enclosing quotes for
            # readability; non-strings get JSON-serialized (audit T17).
            s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            if len(s) > 200:
                s = s[:200] + "…"
            parts.append(f"{k}={s}")
        return ", ".join(parts)

    def _tool_result_preview(self, result):
        """Truncate a tool result for inline display in Telegram. Full
        text still goes into the model's history (used for reasoning);
        this is just what the user sees as a breadcrumb."""
        s = str(result) if not isinstance(result, str) else result
        s = s.strip()
        if not s:
            return "(no output)"
        # Cap individual preview at 400 chars — enough to see what
        # happened, short enough that ten tool calls don't drown the
        # phone screen.
        if len(s) > 400:
            s = s[:400] + " …(truncated for display; model sees full output)"
        return s

    def _inject_tool_use_hint(self, messages, tool_names):
        """Append a 'you actually have these tools' nudge to the
        system prompt. Same pattern as the desktop path's
        _inject_tool_use_hint — many models (Gemma family especially)
        default to refusing tool use unless explicitly told they have
        them despite proper schemas being provided. Plus the
        anti-pseudo-call steering that landed on the desktop side
        2026-06-06 after Haiku 4.5 wrote 'call_context_status' as
        text instead of issuing a structured tool_use call."""
        # Hint text is shared with the desktop surface and lives in
        # ~/.hearthkin/prompts/ (editable). Previously this surface carried
        # its own thinner copy with no no-tools branch; unified here so
        # Telegram gets the same (stronger) anti-pseudo-call steering.
        from kin_persistence import load_app_prompt
        if tool_names:
            hint = load_app_prompt("tool_use_hint", self.agent_name).replace(
                "{tools}", ", ".join(tool_names))
            # Kin that can write files get the authoring-bridge fallback too —
            # the low-load "save via a fenced block" path for when the big
            # write_file argument snags (the write-gesture that bites hardest
            # in groups). Honored by _run_authoring_bridge_telegram after the
            # reply. See authoring_bridge.py.
            if {"write_file", "edit_file"} & set(tool_names):
                hint += load_app_prompt("authoring_bridge_hint", self.agent_name)
        else:
            hint = load_app_prompt("tool_use_hint_no_tools", self.agent_name)
        for i, m in enumerate(messages):
            if m.get("role") == "system":
                new = dict(m)
                new["content"] = (m.get("content") or "") + hint
                messages[i] = new
                return messages
        # No system message — prepend a bare-hint one.
        return [{"role": "system", "content": hint.strip()}] + messages

    def _teachable_tool(self, name):
        """True if `name` is a tool/game that supports teaching (has a game
        host). Gates the /teach command so a normal message that happens to
        start with 'teach' isn't swallowed as a command."""
        if not name:
            return False
        try:
            from tools import get_game
            return get_game(name) is not None
        except Exception:
            return False

    def _turn_had_write_tool_call(self, turn_tool_history):
        """True if this turn's tool round-trips include a write-class tool
        call (write_file / edit_file). Keeps the authoring-bridge nudge quiet
        when the kin actually did write via the structured tool."""
        for m in (turn_tool_history or []):
            if not isinstance(m, dict):
                continue
            for tc in (m.get("tool_calls") or []):
                name = (tc.get("function") or {}).get("name") or ""
                if name in ("write_file", "edit_file"):
                    return True
        return False

    def _run_authoring_bridge_telegram(self, content, effective_tools, added_turns):
        """Authoring bridge for the Telegram surfaces (DM + group — where the
        write-gesture bites most). If the kin authored file content in its
        natural text register — a ```write:<path>``` fence, or a *writes X*
        emote followed by a plain fenced block — instead of the write_file
        call it froze on, perform the write. See authoring_bridge.py.

        Gated on write_file / edit_file being in EFFECTIVE tools for this
        surface (kin allowlist ∩ the user's bucket), so a read-only user
        can't drive a disk write through a fence.

        Returns ``(system_note, chat_confirmation)``:
        - system_note is persisted to the kin's history (kin-only) and carries
          full detail including paths, so the kin knows exactly what landed.
        - chat_confirmation is posted to the chat and is BASENAME-only with no
          raw error bodies — safe for a multi-user group (mirrors the group
          error handler's path-redaction rule).
        Either may be None. Never raises — the reply is already handled."""
        try:
            if not content:
                return None, None
            if not ({"write_file", "edit_file"} & set(effective_tools or [])):
                # For a kin with NO tools this bridge is its only write path,
                # confined to its own memory — otherwise a kin on a phone can
                # never keep anything. Any other tool set still returns here.
                return self._commit_toolless_memory_telegram(
                    content, effective_tools)
            import os
            import authoring_bridge
            writes = authoring_bridge.extract_authoring_writes(content)
            if writes:
                results = authoring_bridge.commit_authoring_writes(
                    self.agent_name, writes)
                oks = [(p, d) for (p, ok, d) in results if ok]
                errs = [(p, d) for (p, ok, d) in results if not ok]
                # Kin-facing note: full paths (its own files).
                note_bits = []
                if oks:
                    note_bits.append("saved from your reply: " + ", ".join(
                        f"{p} ({n} bytes)" for p, n in oks))
                for p, e in errs:
                    note_bits.append(f"could NOT save {p!r} — {e}")
                from kin_persistence import load_app_prompt
                note = load_app_prompt(
                    "authoring_bridge_result", self.agent_name).replace(
                        "{results}", "; ".join(note_bits))
                # Chat-facing confirmation: basenames only, no raw errors.
                chat_bits = []
                if oks:
                    chat_bits.append("saved " + ", ".join(
                        f"{os.path.basename(str(p))} ({n} bytes)" for p, n in oks))
                if errs:
                    chat_bits.append(
                        f"{len(errs)} file(s) couldn't be saved (see logs)")
                chat = "📝 " + "; ".join(chat_bits) if chat_bits else None
                return note, chat
            # Nothing committed. Nudge toward the fence convention only if the
            # reply gestured at a write AND no write tool actually fired.
            if self._turn_had_write_tool_call(added_turns):
                return None, None
            if authoring_bridge.looks_like_write_gesture(content):
                from kin_persistence import load_app_prompt
                return load_app_prompt("authoring_write_nudge",
                                       self.agent_name), None
            return None, None
        except Exception:
            return None, None

    def _commit_toolless_memory_telegram(self, content, effective_tools):
        """Write side of the no-tools memory loop on Telegram. Same
        ``(system_note, chat_confirmation)`` contract as the authoring bridge
        above, and the same redaction rule: the kin-facing note carries full
        paths (its own files), the chat-facing line is basenames only with no
        raw error bodies, because a group is multi-user.

        Never raises — the reply is already handled by the time this runs."""
        try:
            import os
            import toolless_memory
            results, archived = toolless_memory.commit(
                self.agent_name, content, effective_tools,
                shown_scopes=getattr(self, "_toolless_scopes", []) or [])
            note = toolless_memory.receipt(self.agent_name, results, archived)
            if not note:
                # Nothing landed — tell the kin if it meant to keep something.
                # Kin-facing only: the chat stays quiet, since a missed write
                # is between the kin and its own memory, not group business.
                return toolless_memory.missed_write_nudge(
                    self.agent_name, content, results) or None, None
            self._toolless_scopes = []
            oks = [(p, d) for (p, ok, d) in results if ok]
            errs = [p for (p, ok, _d) in results if not ok]
            chat_bits = []
            if oks:
                chat_bits.append("saved " + ", ".join(
                    f"{os.path.basename(str(p))} ({n} bytes)" for p, n in oks))
            if errs:
                chat_bits.append(f"{len(errs)} couldn't be saved (see logs)")
            if archived:
                chat_bits.append(f"tended {len(archived)} staging scope(s)")
            return note, ("📝 " + "; ".join(chat_bits) if chat_bits else None)
        except Exception:
            return None, None

    def _maybe_read_nudge_telegram(self, content, effective_tools, added_turns,
                                   shared_this_turn):
        """Reading-side prompting for Telegram. Returns a system-note string
        when the kin narrated reading CONTENT (not presence) but no read tool
        fired and nothing was shared this turn — else None. Gated on read_file
        being in the effective set. Suppressed when a file was auto-attached
        (DM) this turn or when a read tool actually fired. See reading_bridge."""
        try:
            if shared_this_turn:
                return None
            if "read_file" not in set(effective_tools or []):
                return None
            for m in (added_turns or []):
                if not isinstance(m, dict):
                    continue
                for tc in (m.get("tool_calls") or []):
                    name = (tc.get("function") or {}).get("name") or ""
                    if name in ("read_file", "memory_search", "read_staging"):
                        return None
            import reading_bridge
            from kin_persistence import load_app_prompt
            reach = reading_bridge.looks_like_read_gesture(content)
            if not reach:
                return None
            return (
                load_app_prompt("read_gesture_nudge", self.agent_name)
                .replace("{reach}", str(reach[:60]))
            )
        except Exception:
            return None

    def wake_pending_approvals_on_shutdown(self):
        """Called when the bot is stopping. Resolves any pending
        approval as 'deny' so the blocked worker threads unblock and
        exit cleanly instead of hanging on the Event forever."""
        with self._pending_lock:
            for ap in self._pending_approvals.values():
                ap.decision = "deny"
                ap.event.set()
            self._pending_approvals.clear()

    def _send_chunked(self, chat_id, text):
        """Send `text` as one or more Telegram messages, respecting the
        4096-char per-message limit, with 429-aware backoff so we don't
        hammer the API into a flood-wait.

        Why this is more involved than a naive loop: when a model
        cascades and produces a multi-thousand-character reply, the
        naive chunker fires sendMessage for each chunk in rapid
        succession. Telegram starts 429-ing; the naive chunker treats
        the 429 as "log and continue" and immediately fires the next
        chunk — which gets a longer retry_after — which gets ignored —
        which gets a longer retry_after — and so on. That's how we
        ends up rate-limited for hours off a single bad reply.
        Each retry-after that we ignore makes the next one
        worse.

        New shape:
          - Each chunk gets up to 2 retries on 429, sleeping the
            retry_after the server told us (capped to 60s for sanity).
          - After 3 consecutive 429s across the loop, abort the rest
            of the send. Continuing past that point only makes things
            worse for the next n minutes / hours.
          - Small inter-chunk delay so we don't ratchet ourselves into
            429 territory in the first place.
        """
        from kin_persistence import append_failure_log
        import time as _time

        cfg = self.get_config() or {}
        token = cfg.get("bot_token", "").strip()
        LIMIT = 4000
        if not text:
            return

        chunks = [text[i:i + LIMIT] for i in range(0, len(text), LIMIT)]
        consecutive_429s = 0
        MAX_CONSEC_429S = 3
        MAX_PER_CHUNK_RETRIES = 2
        INTER_CHUNK_DELAY = 0.5  # seconds; gentle pacing
        RETRY_AFTER_CAP = 60.0   # cap server retry hint at 60s

        for idx, chunk in enumerate(chunks):
            attempts = 0
            sent = False
            while not sent:
                try:
                    telegram_api_call(
                        token,
                        "sendMessage",
                        {"chat_id": chat_id, "text": chunk},
                        timeout=30,
                    )
                    sent = True
                    consecutive_429s = 0
                except TelegramAPIError as e:
                    if e.status == 429 and attempts < MAX_PER_CHUNK_RETRIES:
                        wait = (e.retry_after or 5.0) + 0.5
                        wait = min(wait, RETRY_AFTER_CAP)
                        append_failure_log(
                            "telegram_failures.log",
                            self.agent_name,
                            f"send chat_id={chat_id} chunk={idx} "
                            f"(429, sleeping {wait:.1f}s before retry)",
                            e,
                        )
                        _time.sleep(wait)
                        attempts += 1
                        continue
                    # Either a non-429 API error, or we've already
                    # retried this chunk too many times. Log and
                    # abandon this chunk.
                    append_failure_log(
                        "telegram_failures.log",
                        self.agent_name,
                        f"send chat_id={chat_id} chunk={idx}",
                        e,
                    )
                    if e.status == 429:
                        consecutive_429s += 1
                        if consecutive_429s >= MAX_CONSEC_429S:
                            append_failure_log(
                                "telegram_failures.log",
                                self.agent_name,
                                f"send chat_id={chat_id} aborting remaining "
                                f"{len(chunks) - idx - 1} chunks after "
                                f"{consecutive_429s} consecutive 429s "
                                f"(flood-wait protection)",
                                RuntimeError("flood-wait abort"),
                            )
                            return
                    break  # move on to next chunk (or abort if at threshold)
                except Exception as e:
                    # Non-Telegram error (network blip, etc.). Log this
                    # chunk's failure but try the next chunk — a
                    # transient network failure shouldn't drop the
                    # whole reply.
                    append_failure_log(
                        "telegram_failures.log",
                        self.agent_name,
                        f"send chat_id={chat_id} chunk={idx}",
                        e,
                    )
                    break

            # Inter-chunk delay if more chunks remain. This is the
            # difference between "burst, get 429ed, recover" and
            # "burst, get 429ed, dig the hole deeper."
            if idx < len(chunks) - 1:
                _time.sleep(INTER_CHUNK_DELAY)
