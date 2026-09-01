# SPDX-License-Identifier: CC0-1.0

"""
Skype official JSON export parser.

Microsoft's Skype export bundle is a .tar containing `messages.json`
plus `endpoints.json` and a `media/` folder. The .tar can be extracted
or pointed at directly (this parser handles both — for a .tar, the
embedded `messages.json` is opened in-memory via the stdlib tarfile
module; no extraction to disk required).

Shape of messages.json:
    {
      "userId": "8:quietwatermark",   # the operator's skype ID
      "exportDate": "2024-02-01T12:00",
      "conversations": [
        { "id": "8:live:tealwing207",       # a DM — id starts with 8:, no @
          "displayName": "Marielle van Dijk",
          "MessageList": [
            { "id": "...",
              "from": "8:live:tealwing207",
              "displayName": "Marielle van Dijk",
              "originalarrivaltime": "2024-01-15T10:00:00.000Z",
              "messagetype": "RichText",      # or Text, Event/Call, RichText/Media_*
              "content": "...",
            }, ...
          ],
        },
        { "id": "19:abc@thread.skype",         # a group (@thread.skype)
          "threadProperties": {"members": "[...]", "membercount": 3},
          ...
        },
      ],
    }

DM detection: `id` starts with `8:` and contains no `@`. Groups have
`@thread.skype`, `@cast.skype`, or `@encrypted.skype` in their id; the
picker still lists DMs only, but the role mapping below no longer
assumes one.

Speaker mapping. THE ASSISTANT SLOT IS THE KIN'S ALONE:

  * `from` matching `userId` (compared as handles, see below) is the
    operator -> role=user.
  * In a DM, everyone else is the kin -> role=assistant, UNLESS
    `kin_display_name` explicitly names the operator's own side and
    NOT the partner's — see "The exporting account can itself be the
    kin" below. The ordinary case still does not depend on display
    names matching what the operator typed.
  * In a group, only a sender whose display name or handle matches the
    kin is the kin. Everyone else is role=user with their own name kept
    in `speaker` and echoed inline via `sender_attribution`, the same
    policy importers/text_log documents for group exports.

This used to read "everyone who isn't the operator is the kin", which
is a two-party assumption. With a third person in the thread their
words went into the kin's slot AND their name was overwritten with the
conversation's display name, so nothing on disk recorded that anyone
else had spoken. `speaker` never reaches the model (it is not in
llm_backend._API_MESSAGE_FIELDS), so a third party in the assistant
slot simply *is* the kin having said it.

The exporting account can itself be the kin. The DM rule above assumes
the exporting account is always "the human talking to a kin" and the
other side of every 1:1 is always "the kin" — true for an ordinary
personal archive, false the moment the account that did the exporting
IS the kin's own historical voice (the same situation that motivated
the equivalent fix in importers/skype_txt.py: a Skype identity used AS
a kin, not as the human running Hearthkin). Confirmed against a real
export: picking the operator's own handle as `kin_display_name` had NO
effect whatsoever — every kin_display_name value produced an identical
result, because the DM branch never once looked at it. The partner's
messages landed as the kin's own words regardless of what was picked,
which is exactly the failure this whole file exists to prevent.

Fix: `kin_display_name` is checked against BOTH sides of the DM before
falling back to the old assumption. If it matches the operator's own
handle and NOT the partner, the direction flips — operator's turns
become the kin, partner's become role=user under their own name. If it
matches the partner (the ordinary case) or matches neither (no
explicit signal either way), the original behavior is unchanged —
that fallback is deliberately kept, since requiring an exact name
match for every ordinary DM would reintroduce the exact failure mode
this file's own history warns about: a kin's Skype display name that
simply doesn't match what got typed into Hearthkin would silently zero
out the kin's slot. An explicit pick should always be able to override
a structural default; a missing or ambiguous pick should never break
a case that worked before.

The operator comparison is on HANDLES, not raw ids: `userId` and a
message's `from` come from different places in the export and do not
reliably carry the same prefix form ("8:live:foo" vs "live:foo" vs
"foo" across export vintages). An exact-string comparison that misses
does not degrade gracefully — it puts every message, the operator's
included, into the kin's slot.

The operator's display name in their own messages is just their bare
Skype handle (e.g. "quietwatermark" or "quietwatermark");
the partner's display name lives at the conversation's top level.

Content cleanup: Skype's RichText embeds XML-style markup with
`raw_pre` / `raw_post` attributes that LITERALLY tell us what the
original markdown-like input was. E.g.
    <b raw_pre="*" raw_post="*">waves</b>
means the user typed `*waves*` and Skype rendered it bold. We
restore the original by wrapping the inner text with raw_pre/raw_post.
Emoticon `<ss type="smile">:)</ss>` becomes the inner text. Media /
call XML blobs collapse to a `[media: ...]` / `[call: ...]` marker.
"""

import io
import json
import re
import tarfile

from html.parser import HTMLParser

from tools._io import robust_read_text


# Hard cap on how big a messages.json inside a .tar we'll read fully
# into memory. Real Skype exports run a few MB; 512 MB is a sanity
# ceiling against corrupt or malicious archives, not a real limit.
_MAX_TAR_MEMBER_BYTES = 512 * 1024 * 1024


# ─── Detection ────────────────────────────────────────────────────── #

def detect(text):
    """Return True if `text` looks like the start of a Skype messages.json.

    Light-touch detection: looks for the export's distinctive
    top-level keys in the first ~2 KB so we don't pay the full-JSON
    parse cost for files that aren't us."""
    head = text[:2048]
    return (
        '"userId"' in head
        and '"conversations"' in head
        and ('"exportDate"' in head or '"MessageList"' in head)
    )


def detect_path(source_path):
    """Path-based detection for files that aren't sensibly read as
    text (Skype's .tar bundles). For .tar: check the tar member list
    for a messages.json entry. For .json: peek the first 2 KB and run
    text detection. Returns False on any other extension."""
    lower = source_path.lower()
    if lower.endswith(".tar"):
        try:
            with tarfile.open(source_path, "r") as tar:
                for m in tar.getmembers():
                    if m.name.endswith("messages.json"):
                        return True
        except (tarfile.TarError, OSError):
            return False
        return False
    if lower.endswith(".json"):
        try:
            with open(source_path, "r", encoding="utf-8", errors="replace") as f:
                head = f.read(2048)
        except OSError:
            return False
        return detect(head)
    return False


# ─── Conversation listing (for the dialog's picker) ───────────────── #

def list_conversations(source_path):
    """Return a list of dicts describing each conversation in the file,
    suitable for populating a dialog Choice widget. DMs first, sorted
    by message count desc; groups suppressed for v1.

    Each entry:
        {
            "id": str,           # skype conversation id
            "display_name": str, # human label (or fallback)
            "is_dm": bool,
            "message_count": int,
            "member_count": int,
        }
    """
    data = _load_messages_json(source_path)
    convs = data.get("conversations") or []

    items = []
    for c in convs:
        cid = c.get("id") or ""
        is_dm = cid.startswith("8:") and "@" not in cid
        # Skip groups for v1. (Caller can filter further.)
        if not is_dm:
            continue
        msgs = c.get("MessageList") or []
        dn = c.get("displayName") or _id_to_handle(cid) or "(unnamed)"
        items.append({
            "id": cid,
            "display_name": dn,
            "is_dm": True,
            "message_count": len(msgs),
            "member_count": 2,
        })

    items.sort(key=lambda x: x["message_count"], reverse=True)
    return items


# ─── Parse ────────────────────────────────────────────────────────── #

def parse(source_path, kin_display_name, conversation_id=None, **_opts):
    """Parse one conversation out of a Skype messages.json (or .tar
    containing messages.json). Returns (canonical_messages,
    source_label, fmt) matching importers.text_log.parse's contract.

    `conversation_id` picks which conversation to extract. If None,
    the parser picks the conversation whose `displayName` matches
    `kin_display_name` (case-insensitive), falling back to the
    largest DM. The dialog normally passes conversation_id explicitly
    after the operator picks from the listing.
    """
    data = _load_messages_json(source_path)
    operator_user_id = data.get("userId") or ""
    convs = data.get("conversations") or []

    # Without a userId there is no way to tell the operator's turns from
    # anyone else's, and the failure is not a partial one: every message
    # in the file would land in the kin's slot, the operator's own half
    # included, and the kin would afterwards read the whole conversation
    # as words it had said. Refuse and say why. An import that quietly
    # produces a corrupt history is worse than one that doesn't run —
    # the damage is only visible much later, in how a kin sounds.
    if not _id_to_handle(operator_user_id).strip():
        raise ValueError(
            "This Skype export has no `userId` at the top level, so "
            "there's no way to tell which messages are yours. Importing "
            "it would file the whole conversation as the kin's own "
            "words. Check the export is complete (messages.json should "
            "start with a \"userId\" field)."
        )

    target_conv = _pick_conversation(
        convs, conversation_id, kin_display_name,
    )
    if target_conv is None:
        raise ValueError(
            "No matching conversation found in this Skype export."
        )

    msgs = target_conv.get("MessageList") or []
    if not msgs:
        raise ValueError(
            f"Conversation {target_conv.get('id')!r} has no messages."
        )

    # Skype puts messages newest-first inside each MessageList.
    # Reverse so the canonical conversation reads oldest-to-newest,
    # matching every other surface in Hearthkin.
    msgs = list(reversed(msgs))

    partner_display_name = (
        target_conv.get("displayName")
        or _id_to_handle(target_conv.get("id") or "")
        or kin_display_name
    )

    # Read from the id, which is what actually distinguishes them: a DM
    # is "8:<handle>", a group carries "@thread.skype" / "@cast.skype" /
    # "@encrypted.skype". The per-message role mapping needs this — in a
    # DM the non-operator is the kin by definition, in a group they have
    # to be identified by name.
    target_id = target_conv.get("id") or ""
    is_dm = target_id.startswith("8:") and "@" not in target_id

    # Does kin_display_name pick out one side of this DM explicitly?
    # Computed once per conversation, not per message — it can't change
    # from one message to the next.
    #
    # Checked against the RAW conversation fields, not against
    # `partner_display_name` above — that variable falls back to
    # `kin_display_name` itself when the export has neither a
    # displayName nor a derivable handle, which would make
    # partner_is_kin trivially true and silently block a legitimate
    # operator-side override in that edge case.
    op_handle = _id_to_handle(operator_user_id).strip().lower()
    kin_lower = (kin_display_name or "").strip().lower()
    partner_is_kin = bool(kin_lower) and kin_lower in (
        (target_conv.get("displayName") or "").strip().lower(),
        _id_to_handle(target_id).strip().lower(),
    )
    operator_is_kin = bool(kin_lower) and kin_lower == op_handle
    # Both matching (same handle typed for both sides — a degenerate
    # export, or a coincidence) falls back to the ordinary assumption
    # rather than picking a direction arbitrarily.
    dm_operator_is_kin = is_dm and operator_is_kin and not partner_is_kin

    canonical = []
    for m in msgs:
        c = _message_to_canonical(
            m,
            operator_user_id=operator_user_id,
            kin_display_name=kin_display_name,
            partner_display_name=partner_display_name,
            is_dm=is_dm,
            dm_operator_is_kin=dm_operator_is_kin,
        )
        if c is not None:
            canonical.append(c)

    if not canonical:
        raise ValueError(
            f"No messages in conversation {target_conv.get('id')!r} survived "
            f"cleanup (all were system events or empty)."
        )

    return canonical, "skype_dm", "skype_json"


# ─── Load + .tar handling ─────────────────────────────────────────── #

def _load_messages_json(source_path):
    """Open source_path and return the parsed messages.json dict.
    Accepts:
      - a .json file (read directly)
      - a .tar file (extract messages.json from inside, in memory)
    """
    if source_path.lower().endswith(".tar"):
        with tarfile.open(source_path, "r") as tar:
            member = None
            for m in tar.getmembers():
                if m.name.endswith("messages.json"):
                    member = m
                    break
            if member is None:
                raise ValueError(
                    f"No messages.json found inside {source_path}."
                )
            # The member is read fully into memory below — cap its
            # declared size so a corrupt/malicious archive can't OOM
            # the app. 512 MB is far past any real Skype export.
            if member.size > _MAX_TAR_MEMBER_BYTES:
                raise ValueError(
                    f"messages.json inside {source_path} is "
                    f"{member.size:,} bytes — over the "
                    f"{_MAX_TAR_MEMBER_BYTES // (1024 * 1024)} MB import "
                    f"cap; refusing to load it into memory."
                )
            f = tar.extractfile(member)
            if f is None:
                raise ValueError(
                    f"Could not read messages.json from {source_path}."
                )
            raw = f.read().decode("utf-8", errors="replace")
        return json.loads(raw)
    # Plain .json file. Use robust_read_text for the Windows-edited
    # smart-character chain (cp1252 fallback). Skype exports are
    # UTF-8 so the strict path normally wins; this just keeps us
    # honest if a user ever hand-edits one.
    raw = robust_read_text(source_path)
    return json.loads(raw)


# ─── Pick the right conversation ──────────────────────────────────── #

def _pick_conversation(convs, conversation_id, kin_display_name):
    """Pick the conversation matching the dialog's selection.

    Priority:
      1. Exact match on `conversation_id` if provided.
      2. Case-insensitive match on displayName == kin_display_name.
      3. The DM (id starts with 8:, no @) with the most messages.
    """
    if conversation_id:
        for c in convs:
            if c.get("id") == conversation_id:
                return c
        return None
    # NOTE: an explicit conversation_id is honoured whatever kind of
    # conversation it names — it is the operator's stated choice, and
    # the role mapping below is group-safe. The two name-based
    # fallbacks stay DM-only on purpose: guessing which conversation
    # someone meant is a different matter from being told.

    kin_lower = (kin_display_name or "").strip().lower()
    if kin_lower:
        for c in convs:
            cid = c.get("id") or ""
            if not (cid.startswith("8:") and "@" not in cid):
                continue
            dn = (c.get("displayName") or "").strip().lower()
            if dn == kin_lower:
                return c

    dms = [
        c for c in convs
        if (c.get("id") or "").startswith("8:")
        and "@" not in (c.get("id") or "")
    ]
    if not dms:
        return None
    dms.sort(key=lambda c: len(c.get("MessageList") or []), reverse=True)
    return dms[0]


# ─── Per-message conversion ───────────────────────────────────────── #

# Skype messagetypes we know how to handle. Anything else falls
# through to a `[skype: <messagetype>]` marker on a best-effort basis.
_RICHTEXT_TYPES = {"RichText", "Text"}
_SKIP_TYPES = {
    # System / control events we drop entirely. The conversation reads
    # better without "X added Y" / "free relationship initialized" noise.
    "InviteFreeRelationshipChanged/Initialized",
    "ThreadActivity/AddMember",
    "ThreadActivity/DeleteMember",
    "ThreadActivity/TopicUpdate",
    "ThreadActivity/MemberConsumptionHorizonUpdate",
    "Notice",
    "Event/SkypeVideoMessage",
}
_MEDIA_TYPES = {
    "RichText/Media_AudioMsg": "audio",
    "RichText/Media_Video": "video",
    "RichText/Media_GenericFile": "file",
    "RichText/UriObject": "image",
    "RichText/Media_Album": "album",
    "RichText/Location": "location",
}


def _message_to_canonical(
    m, *, operator_user_id, kin_display_name, partner_display_name,
    is_dm=True, dm_operator_is_kin=False,
):
    """Convert one Skype message dict to the canonical
    conversation.jsonl shape. Returns None for messages that should
    be dropped (system events, empties)."""
    mt = m.get("messagetype") or "?"
    if mt in _SKIP_TYPES:
        return None

    raw_content = m.get("content") or ""
    sender = m.get("from") or ""
    sender_display = m.get("displayName") or _id_to_handle(sender) or sender
    ts = _normalize_ts(m.get("originalarrivaltime"))

    # Role mapping. THE ASSISTANT SLOT IS THE KIN'S ALONE — everyone
    # else goes in the user slot with their own name kept, the same
    # policy text_log documents for group exports and rooms enforce for
    # multi-kin turns.
    #
    # This used to read "operator's turns become role=user; everyone
    # else is the kin", which is a two-party assumption. It is fine in a
    # DM and wrong the moment a third person is present: their words
    # landed in the kin's slot AND their name was overwritten with the
    # conversation's display name, so nothing on disk recorded that
    # anyone else had spoken. `speaker` never reaches the model anyway
    # (it isn't in llm_backend._API_MESSAGE_FIELDS), so a third party in
    # the assistant slot is simply the kin appearing to have said it —
    # exactly the voice contamination the rest of this codebase is built
    # to prevent.
    #
    # Comparing handles rather than raw ids: `userId` and a message's
    # `from` come from different places in the export and don't reliably
    # carry the same prefix form ("8:live:foo" vs "live:foo" vs "foo"
    # across export vintages). An exact-string comparison that misses
    # doesn't degrade gracefully — it puts EVERY message, the operator's
    # included, into the kin's slot.
    # `is_dm` comes from the conversation id, not from guessing at
    # names, and that distinction matters: in a DM the non-operator IS
    # the kin whatever their display name happens to be, so name-
    # matching there would put the kin's own turns in the user slot
    # every time their Skype display name didn't equal what the operator
    # typed. Only a group has to work it out by name.
    op_handle = _id_to_handle(operator_user_id).strip().lower()
    sender_handle = _id_to_handle(sender).strip().lower()
    is_operator_turn = bool(op_handle) and bool(sender_handle) and sender_handle == op_handle

    if is_dm and dm_operator_is_kin:
        # Explicit override, computed once in parse(): kin_display_name
        # named the exporting account itself, not the partner. The
        # Skype identity that did this export IS the kin here — flip
        # the ordinary direction rather than assume the partner always
        # is the kin. See the module docstring, "The exporting account
        # can itself be the kin."
        if is_operator_turn:
            role = "assistant"
            speaker_label = kin_display_name or sender_display
        else:
            role = "user"
            speaker_label = partner_display_name or sender_display
    elif is_operator_turn:
        role = "user"
        speaker_label = sender_display or "operator"
    elif is_dm:
        role = "assistant"
        speaker_label = partner_display_name or kin_display_name or sender_display
    else:
        kin_lower = (kin_display_name or "").strip().lower()
        display_lower = (sender_display or "").strip().lower()
        handle_lower = sender_handle
        if kin_lower and kin_lower in (display_lower, handle_lower):
            role = "assistant"
            speaker_label = kin_display_name
        else:
            # Somebody else in the thread. Their words, under their name.
            role = "user"
            speaker_label = sender_display or _id_to_handle(sender) or "unknown"

    # Content depends on messagetype.
    if mt in _RICHTEXT_TYPES:
        content = _clean_richtext(raw_content)
    elif mt in _MEDIA_TYPES:
        label = _MEDIA_TYPES[mt]
        title = _extract_uriobject_title(raw_content) or label
        content = f"[skype {label}: {title} — not imported]"
    elif mt.startswith("Event/Call"):
        duration = _extract_call_duration(raw_content)
        if duration:
            content = f"[skype call: {duration}]"
        else:
            content = "[skype call]"
    else:
        # Fallback: just strip any XML tags and emit with a hint
        # marker. Better than dropping silently.
        stripped = _strip_xml_tags(raw_content).strip()
        if not stripped:
            return None
        content = f"[skype {mt}] {stripped}"

    content = content.strip()
    if not content:
        return None

    out = {
        "role": role,
        "content": content,
        "ts": ts,
        "speaker": speaker_label,
        "source": "import:skype",
    }
    if role == "user":
        # Bare, like live capture stores it — the reading surface adds the
        # bracket (chat_helpers.speaker_attribution_prefix).
        out["sender_attribution"] = speaker_label
    return out


# ─── Content helpers ──────────────────────────────────────────────── #

_RICHTEXT_TAG_RE = re.compile(
    r"<(?P<name>[a-zA-Z_][\w:]*)"
    r"(?P<attrs>[^>]*)"
    r">(?P<body>.*?)</(?P=name)>",
    re.DOTALL,
)
_RAW_PRE_RE = re.compile(r'raw_pre="([^"]*)"')
_RAW_POST_RE = re.compile(r'raw_post="([^"]*)"')
# Self-closing tags Skype sometimes emits (e.g. <quote ...>...</quote>
# we cover via the paired regex; <br/> and <e_m/> are bare). Just
# drop them; they're not message content.
_SELF_CLOSING_TAG_RE = re.compile(r"<[a-zA-Z_][\w:]*\s*/?>")
_HTML_ENTITY = re.compile(r"&(#?\w+);")
_ENTITY_MAP = {
    "amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'",
    "nbsp": " ",
}


def _clean_richtext(raw):
    """Replace Skype's RichText markup with plain-text approximation.

    `<b raw_pre="*" raw_post="*">x</b>` → `*x*`
    `<i raw_pre="_" raw_post="_">x</i>` → `_x_`
    `<ss type="smile">:)</ss>`          → `:)`
    `<at id="...">name</at>`            → `name`
    `<quote ...>x</quote>`              → `> x`
    other tags                          → inner text

    We iterate until no more tags remain (Skype's content can nest:
    `<b><ss>:)</ss></b>`).
    """
    s = raw
    # Strip <quote>...</quote> first since they often wrap other markup.
    for _ in range(20):  # bound the loop
        new = _RICHTEXT_TAG_RE.sub(_replace_tag, s)
        if new == s:
            break
        s = new
    s = _SELF_CLOSING_TAG_RE.sub("", s)
    s = _HTML_ENTITY.sub(_replace_entity, s)
    return s


def _replace_tag(match):
    name = match.group("name").lower()
    attrs = match.group("attrs") or ""
    body = match.group("body")
    if name == "quote":
        # Skype quote tag — render as a markdown-style block quote.
        inner = body.strip()
        return "> " + inner.replace("\n", "\n> ") if inner else ""
    pre_m = _RAW_PRE_RE.search(attrs)
    post_m = _RAW_POST_RE.search(attrs)
    if pre_m or post_m:
        pre = pre_m.group(1) if pre_m else ""
        post = post_m.group(1) if post_m else ""
        return f"{pre}{body}{post}"
    # Default: drop the tag, keep the body.
    return body


def _replace_entity(match):
    name = match.group(1)
    if name in _ENTITY_MAP:
        return _ENTITY_MAP[name]
    if name.startswith("#"):
        try:
            if name.startswith("#x"):
                return chr(int(name[2:], 16))
            return chr(int(name[1:]))
        except (ValueError, OverflowError):
            return match.group(0)
    return match.group(0)


class _TagStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _strip_xml_tags(raw):
    """Coarse XML/HTML tag strip — falls back when richtext cleanup
    doesn't match the expected shape (unknown messagetype etc.)."""
    p = _TagStripper()
    try:
        p.feed(raw)
        p.close()
    except Exception:
        return raw
    return "".join(p.parts)


_DURATION_RE = re.compile(r"<duration>([\d.]+)</duration>")
_URIOBJECT_TITLE_RE = re.compile(r"<Title>([^<]+)</Title>")


def _extract_call_duration(raw):
    """Pull a duration in seconds out of an Event/Call partlist XML
    and return it as a human-readable string."""
    m = _DURATION_RE.search(raw)
    if not m:
        return ""
    try:
        secs = float(m.group(1))
    except ValueError:
        return ""
    if secs < 60:
        return f"{int(secs)}s"
    mins = int(secs // 60)
    rem = int(secs % 60)
    return f"{mins}m{rem:02d}s"


def _extract_uriobject_title(raw):
    """Pull a media object's Title element if present."""
    m = _URIOBJECT_TITLE_RE.search(raw)
    return m.group(1) if m else ""


# ─── Timestamp + ID helpers ───────────────────────────────────────── #

def _normalize_ts(ts_raw):
    """Skype timestamps are ISO-8601 with a trailing Z. Strip the Z
    and return a plain ISO string. Best-effort; returns None on
    anything unparseable so the writer can stamp a fallback."""
    if not isinstance(ts_raw, str):
        return None
    s = ts_raw.strip()
    if s.endswith("Z"):
        s = s[:-1]
    # Cut off subsecond fraction if present so the saved string
    # matches the rest of the on-disk shape.
    if "." in s:
        s = s.split(".", 1)[0]
    return s


def _id_to_handle(skype_id):
    """Pull a human-readable handle out of a Skype ID. `8:live:foo`,
    `8:foo` and `live:foo` all become `foo`. Falls back to the input.

    The bare `live:foo` form matters because this is what the
    operator-identity comparison normalises through, and the two ids
    being compared (`userId` at the top of the export, and each
    message's `from`) don't reliably carry the same prefixes. Leaving
    `live:` on when the `8:` isn't there made those two spellings of
    one person fail to match — and a missed operator match doesn't
    degrade gracefully, it gives their whole half of the conversation
    to the kin."""
    if not skype_id:
        return ""
    rest = skype_id
    if rest.startswith("8:"):
        rest = rest[2:]
    if rest.startswith("live:"):
        rest = rest[5:]
    return rest
