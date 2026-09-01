# SPDX-License-Identifier: CC0-1.0

"""
chat_helpers — utilities for parsing streaming chunks, finding sentence
boundaries (for sentence-by-sentence painting), counting tokens
heuristically, and cleaning up small-model room replies that leak the
`[KinName]:` transcript format.

Extracted from hearthkin.pyw. Pure stdlib (`re` only). No model
calls, no I/O, no UI — just text manipulation.

The anti-impersonation helpers (strip_self_tag,
strip_trailing_other_speakers, _OTHER_SPEAKER_RE) are critical for
multi-kin room mode: small models routinely leak `[Name]:` prefixes
from the room's transcript-shaped history into their own replies.
The room loop tags this format on input by design; these cleanups
rip it back off on output. Don't simplify them away without thinking
about the room impersonation patterns they prevent.
"""

import datetime as _datetime
import re


def humanize_error(msg, *, kin=None, host=None, redact=False):
    """Turn a raw backend error into a plain-language line a non-technical,
    screen-reader user can understand and act on. Known causes get a helpful
    sentence; unknown ones fall back to a trimmed copy of the real message so
    nothing is hidden — unless `redact` is set (group/guest surfaces where the
    raw text could leak file paths or provider bodies). Pure string work."""
    s = str(msg or "").strip()
    low = s.lower()
    who = kin or "The kin"
    where = f" on {host}" if host else ""
    if "model" in low and "not found" in low:
        return (f"{who}'s model isn't loaded{where} yet — the machine may be "
                "down, still loading it, or the model was removed.")
    # A READ timeout is NOT an unreachable machine, and must be checked before
    # the connection family below — the connection succeeded, the request was
    # accepted, and the server simply didn't answer in time. On a local model
    # that nearly always means another model is holding the machine, or a large
    # one is loading from cold. Reporting it as "is it on and reachable?" sends
    # someone to check a network that was fine the whole time, which is exactly
    # the wrong place to look and cost an afternoon once: the machine was up,
    # answering, and had 19 GB free while a pinned model refused to move.
    # Bare "connection timed out" stays with the unreachable family below,
    # because that one really is a machine you can't get to.
    if any(k in low for k in (
            "read timed out", "read timeout", "readtimeout")):
        return (f"{who}'s machine answered, but the model didn't start "
                f"replying in time{where}. The machine itself is fine — "
                "usually another model is holding it, or a big one is loading "
                "from cold. Worth retrying; if it keeps happening, something "
                "else is using that machine.")
    if any(k in low for k in (
            "connection refused", "failed to establish", "max retries",
            "connection aborted", "cannot connect", "actively refused",
            "unreachable", "getaddrinfo", "name or service not known",
            "timed out", "timeout", "connection reset")):
        return f"Couldn't reach {who}'s model machine{where}. Is it on and reachable?"
    if "rate limit" in low or " 429" in low or "too many requests" in low or "retry-after" in low:
        return f"{who}'s provider is rate-limiting — too many requests. Give it a minute."
    if any(k in low for k in (
            "unauthorized", " 401", "invalid api key", "no api key",
            "authentication", "incorrect api key", "no auth credentials")):
        return (f"{who}'s provider rejected the API key (missing or invalid). "
                "Set it in Preferences, Connections.")
    if "context" in low and ("too large" in low or "maximum context" in low
            or "exceed" in low or "too long" in low):
        return (f"{who}'s request was too big for the model's context window. "
                "Lower the context size or shorten the conversation.")
    if "out of memory" in low or "oom " in low or "insufficient memory" in low:
        return (f"{who}'s machine ran out of memory loading the model. "
                "Use a smaller model or a smaller context.")
    if redact:
        return f"{who} hit a problem — the details are logged for the operator."
    short = s if len(s) <= 200 else s[:200] + "…"
    return f"{who} hit an error: {short}"


def format_ts_prefix(ts):
    """Format a stored ISO-8601 timestamp into a "[YYYY-MM-DD HH:MM] " prefix
    suitable for prepending to message content the model will read.

    Returns "" when ts is missing or unparseable, so callers can safely do
    `format_ts_prefix(msg.get("ts")) + content`. The prefix grounds the kin
    in real time — without it, every message in the model's context window
    is timeless even though the user UI shows the timestamp on every turn.
    """
    if not ts or not isinstance(ts, str):
        return ""
    try:
        dt = _datetime.datetime.fromisoformat(ts)
    except Exception:
        return ""
    return f"[{dt.strftime('%Y-%m-%d %H:%M')}] "


# A stored attribution that already carries its own brackets. Importers
# used to bake them in (`"[SpeakerOne]"`) while live Telegram capture stored
# the name bare (`"SpeakerOne (@speakerone)"`), and both feed readers that add a
# bracket themselves — so imported turns reached the model as
# "[[SpeakerOne]] text", a shape that appears nowhere else in a kin's context.
# Tolerated on the read side rather than fixed only at the source,
# because the bracketed form is already sitting in people's kin folders.
_WRAPPED_ATTRIBUTION_RE = re.compile(r'^\s*\[\s*(.*?)\s*\]\s*$', re.DOTALL)


def speaker_attribution_prefix(attribution):
    """Format a stored sender attribution as the "[Name] " prefix that goes
    in front of a user turn's content. Returns "" when there's no
    attribution, so callers can do
    `format_ts_prefix(ts) + speaker_attribution_prefix(a) + content`.

    THE MISSING COLON IS THE POINT. "[Name]: text" is a speaker-turn token
    — models pattern-match it and start producing other people's turns.
    "[Name] text" is not. Every surface that names a speaker to a model
    goes through here so no new call site can reintroduce the colon shape
    by hand; a stored value that ends in one gets it trimmed.

    Names arrive from third-party exports and from whatever a Telegram
    user calls themselves, so the result is sanitized — a display name
    containing newlines would otherwise break the prompt's framing from
    inside the bracket."""
    if not attribution or not isinstance(attribution, str):
        return ""
    name = attribution.strip()
    # Unwrap however many bracket layers a legacy row accumulated.
    while True:
        m = _WRAPPED_ATTRIBUTION_RE.match(name)
        if not m or not m.group(1).strip():
            break
        name = m.group(1).strip()
    name = name.rstrip(':').strip()
    if not name:
        return ""
    from kin_persistence import sanitize_for_prompt_literal
    name = sanitize_for_prompt_literal(name).strip()
    return f"[{name}] " if name else ""


def _extract_chunk_content(chunk):
    """Pull the assistant content delta from a streaming chunk.

    Handles three shapes:
      - llm_backend.Chunk (has .content directly)
      - Ollama dict shape ({"message": {"content": "..."}})
      - Ollama Pydantic shape (chunk.message.content)
    """
    if chunk is None:
        return ""
    # New llm_backend.Chunk shape: content is a direct attribute
    direct = getattr(chunk, "content", None)
    if isinstance(direct, str) and not hasattr(chunk, "message"):
        return direct
    try:
        if isinstance(chunk, dict):
            msg = chunk.get("message")
        else:
            msg = getattr(chunk, "message", None)
        if msg is None:
            return ""
        if isinstance(msg, dict):
            return msg.get("content") or ""
        return getattr(msg, "content", "") or ""
    except Exception:
        return ""


def _extract_chunk_thinking(chunk):
    """Pull the reasoning delta from a streaming chunk, if any.

    Handles llm_backend.Chunk (.thinking attribute) AND Ollama shapes.
    Returns '' when the model doesn't support thinking or hasn't emitted any yet.
    """
    if chunk is None:
        return ""
    # New llm_backend.Chunk shape: thinking is a direct attribute
    direct = getattr(chunk, "thinking", None)
    if isinstance(direct, str) and not hasattr(chunk, "message"):
        return direct
    try:
        if isinstance(chunk, dict):
            msg = chunk.get("message")
        else:
            msg = getattr(chunk, "message", None)
        if msg is None:
            return ""
        if isinstance(msg, dict):
            return msg.get("thinking") or ""
        return getattr(msg, "thinking", "") or ""
    except Exception:
        return ""


# Sentence boundary: end-of-sentence punctuation (optionally followed by
# closing quotes/brackets) plus whitespace, OR any newline. Used to paint
# streamed text in coherent units — sentences instead of individual tokens.
# Per-token painting floods NVDA with text-changed events; per-reply painting
# means the whole answer arrives in one chunk. Sentence-by-sentence is the
# middle ground.
_SENTENCE_BOUNDARY = re.compile(r'[.!?]+["\'\)\]]*\s|\n')


def _last_sentence_end(text, start):
    """Return the index just past the last sentence-end at or after `start`,
    or None if no complete sentence has finished yet in text[start:]."""
    last = None
    for m in _SENTENCE_BOUNDARY.finditer(text, start):
        last = m.end()
    return last


def estimate_tokens(text):
    """Rough estimate: ~4 chars per token. Good enough for a budget gauge."""
    return max(0, len(text or "") // 4)


# Per-provider rough averages for one image attachment, in input tokens.
# Sources: each provider's published image-tokenization rules at standard
# photo-ish sizes (~1080p). These are estimates, not commitments — the
# estimator's job is "don't lie about how full context is", not "predict
# cost to the penny". For accurate cost figures use usage.log, which
# records the provider-reported prompt_tokens (no estimation).
#
#   Anthropic (Claude): width*height/750, capped 1568px short side.
#     A typical photo lands around 1500 tokens.
#   OpenAI (GPT-4o, o-series): 85 base + 170 per 512x512 tile in high
#     detail. A typical 1024x1024 photo ≈ 765; up to ~1500 at higher res.
#   Google (Gemini): 258 tokens flat per image regardless of size.
#   Meta llama-3.2-vision: chunked similar to Anthropic.
#   Ollama local (llava, gemma3, qwen2-vl, moondream, bakllava etc.):
#     varies per model; 600 is a safe middle ground across the family.
_IMAGE_TOKEN_TABLE = {
    "anthropic": 1500,
    "openai": 1100,
    "google": 258,
    "meta-llama": 1100,
    "meta": 1100,
    "x-ai": 1100,
    "mistralai": 900,
    "qwen": 700,
}
_OLLAMA_IMAGE_TOKENS_DEFAULT = 600
_UNKNOWN_PROVIDER_IMAGE_TOKENS = 1000


def estimate_image_tokens(model):
    """Return the rough input-token cost of attaching one image when
    talking to `model`. Provider-aware via the _IMAGE_TOKEN_TABLE
    above; Ollama models (no `openrouter/` prefix) get the local-
    vision-family default; unrecognized OR providers get a
    conservative-ish default.

    Used by `estimate_message_tokens` and the % cap display. Numbers
    are rough — see the table's comment for sources."""
    if not model or not isinstance(model, str):
        return _UNKNOWN_PROVIDER_IMAGE_TOKENS
    m = model.lower()
    if m.startswith("openrouter/"):
        m = m[len("openrouter/"):]
        # `provider/name` form — provider is the first segment.
        provider = m.split("/", 1)[0] if "/" in m else m
        return _IMAGE_TOKEN_TABLE.get(provider, _UNKNOWN_PROVIDER_IMAGE_TOKENS)
    # No `openrouter/` prefix → local Ollama. Family-level prefixes
    # (llava-, gemma3, qwen2-vl-, moondream, bakllava-) are too
    # varied to enumerate; a single default is the practical answer.
    return _OLLAMA_IMAGE_TOKENS_DEFAULT


def estimate_message_tokens(msg, model=None):
    """Per-message token estimate that accounts for image attachments.

    Returns the sum of `estimate_tokens(content)` plus per-attachment
    image tokens (provider-aware via `estimate_image_tokens`). For
    messages without attachments — or when `model` is unknown — this
    behaves identically to text-only counting.

    Use this in % cap displays and "context usage" gauges so an
    image-heavy chat doesn't read as 3% when it's actually filling
    the window. The chat() path's own `usage` field (returned by
    every provider) is still the authoritative post-call number;
    this is the pre-call estimate."""
    if not isinstance(msg, dict):
        return 0
    text = msg.get("content")
    tokens = estimate_tokens(text) if isinstance(text, str) else 0
    atts = msg.get("attachments")
    if isinstance(atts, list) and atts:
        per_image = estimate_image_tokens(model)
        tokens += len(atts) * per_image
    return tokens


# Inline-thinking tag patterns. Some models (Anthropic Haiku 4.5 inline,
# certain MiMo / R1 distill variants) emit reasoning as XML markup in the
# `content` field instead of via the structured reasoning channel. These
# regexes catch both `<thinking>...</thinking>` and `<think>...</think>`,
# case-insensitive, with tolerant whitespace inside the tag.
_INLINE_THINKING_OPEN_RE = re.compile(r'<\s*think(?:ing)?\s*>', re.IGNORECASE)
_INLINE_THINKING_CLOSE_RE = re.compile(r'<\s*/\s*think(?:ing)?\s*>', re.IGNORECASE)

# Markdown code-region detection so a kin quoting the markup as an example
# (e.g. "I noticed `<thinking>foo</thinking>` markup leaking") doesn't have
# its example eaten by the extractor. Fenced blocks (``` ... ```) take
# precedence; inline single-backtick spans are matched outside fences.
_CODE_FENCE_RE = re.compile(r'```.*?```', re.DOTALL)
_INLINE_CODE_RE = re.compile(r'`[^`\n]+?`')


def _code_region_spans(text):
    """Return (start, end) spans in `text` that are inside markdown code
    fences or inline single-backtick code. Extraction skips these so
    examples / quoted markup pass through untouched."""
    spans = []
    for m in _CODE_FENCE_RE.finditer(text):
        spans.append((m.start(), m.end()))
    for m in _INLINE_CODE_RE.finditer(text):
        s, e = m.start(), m.end()
        if any(fs <= s and e <= fe for fs, fe in spans):
            continue
        spans.append((s, e))
    return spans


def _pos_in_spans(pos, spans):
    return any(s <= pos < e for s, e in spans)


def extract_inline_thinking(content, existing_thinking=""):
    """Pull `<thinking>...</thinking>` / `<think>...</think>` blocks out
    of `content` and merge them into `existing_thinking`. Returns
    (cleaned_content, merged_thinking).

    Some models emit reasoning as XML markup inline in their reply
    content rather than via the structured reasoning channel (Anthropic
    Haiku 4.5 inline-tagging, certain MiMo / R1 distill variants). This
    function normalizes both shapes: after extraction, the structured
    `thinking` field looks the same whether the model used the channel
    correctly or leaked into content. `show_thinking` display filtering,
    `feed_thinking` history insertion, and the `recent_thinking` tool
    all keep working without any of them needing model-aware special-
    casing — they just see a populated structured field either way.

    Edge cases:
      - Blocks inside markdown code fences / inline backticks are
        preserved (a kin quoting the markup as an example passes
        through untouched).
      - Unclosed `<thinking>` opener at end of reply (truncated mid-
        thought): everything after the orphan opener is treated as
        reasoning.
      - Multiple blocks per reply are concatenated with blank lines.
      - `existing_thinking` is APPENDED to, never replaced — a model
        that uses both the structured channel AND leaks inline keeps
        both. Pre-existing thinking + new extracted blocks merge."""
    if not content or not isinstance(content, str):
        return content, (existing_thinking or "")

    code_spans = _code_region_spans(content)
    extracted_blocks = []
    out_parts = []
    pos = 0
    while pos < len(content):
        m = _INLINE_THINKING_OPEN_RE.search(content, pos)
        if not m:
            out_parts.append(content[pos:])
            break
        if _pos_in_spans(m.start(), code_spans):
            # Opener is inside a code region — preserve it and keep scanning.
            out_parts.append(content[pos:m.end()])
            pos = m.end()
            continue
        # Keep prose up to the opener.
        out_parts.append(content[pos:m.start()])
        # Find the matching close, skipping closers that fall inside code.
        close_pos = m.end()
        close_match = None
        while True:
            cm = _INLINE_THINKING_CLOSE_RE.search(content, close_pos)
            if cm is None:
                break
            if _pos_in_spans(cm.start(), code_spans):
                close_pos = cm.end()
                continue
            close_match = cm
            break
        if close_match is None:
            # Unclosed opener: the tail after it is the reasoning.
            tail = content[m.end():].strip()
            if tail:
                extracted_blocks.append(tail)
            pos = len(content)
        else:
            block = content[m.end():close_match.start()].strip()
            if block:
                extracted_blocks.append(block)
            pos = close_match.end()

    new_content = "".join(out_parts)
    # Collapse triple-blank-line gaps left by removed blocks.
    new_content = re.sub(r'\n{3,}', '\n\n', new_content).strip()

    if not extracted_blocks:
        return new_content, (existing_thinking or "")

    new_thinking_chunk = "\n\n".join(extracted_blocks)
    base = (existing_thinking or "").rstrip()
    if base:
        merged = base + "\n\n" + new_thinking_chunk
    else:
        merged = new_thinking_chunk
    return new_content, merged


def strip_self_tag(text, kin_name):
    """Remove leading '[KinName]:' or '[KinName] :' from a reply.

    Small-model rooms tend to leak the bracket-tag format from history into
    their own replies. We instructed the model not to do this in the system
    prompt, but enforcement-as-cleanup is more reliable than enforcement-as-
    prompting on local models."""
    if not text or not kin_name:
        return text
    pattern = re.compile(
        rf'^\s*\[\s*{re.escape(kin_name)}\s*\]\s*:\s*',
        re.IGNORECASE,
    )
    return pattern.sub('', text, count=1)


_LEADING_TS_RE = re.compile(
    r'^\s*\[\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}(:\d{2})?\]\s*'
)


def strip_self_timestamp(text):
    """Strip a leading "[YYYY-MM-DD HH:MM]" (or "[YYYY-MM-DD HH:MM:SS]")
    timestamp prefix from a model's reply, looping if multiple prefixes
    are stacked.

    Hearthkin injects `[YYYY-MM-DD HH:MM] ` onto user / assistant turns
    before sending them to the model (see chat_helpers.format_ts_prefix).
    The kin sees its own prior replies in that shape and tends to
    continue the pattern in its own output — emitting e.g.
    `[2026-05-18 07:19] hello`, or sometimes `[2026-05-18 07:19]
    [2026-05-18 07:18] hello` stacking its reply-time prefix on top of
    a reference to the user's send-time. Telegram and the desktop chat
    display already render a timestamp in each block's header, so an
    echoed prefix is visible duplication.

    Strip only the LEADING run of prefixes. Mid-message references like
    "you said at [2026-05-18 07:09] that..." are legitimate quoting and
    should pass through unchanged."""
    if not text:
        return text
    while True:
        new_text = _LEADING_TS_RE.sub('', text, count=1)
        if new_text == text:
            return text
        text = new_text


# Two narrow guards keep reference-style markdown link definitions
# (`\n[1]: https://example.com`) from being read as a speaker tag and
# truncating everything after them:
#   - `(?!\d+\s*\])` — purely-numeric bracket contents aren't names
#   - `(?!\s*https?://)` — `]: http(s)://` is a link definition, not
#     speech (a real kin turn opening with a bare URL after the tag
#     would be sliced by the stop-sequence/stream path anyway)
_OTHER_SPEAKER_RE = re.compile(
    r'\n\s*\[\s*(?!\d+\s*\])[^\[\]\n]{1,40}\s*\]\s*:(?!\s*https?://)'
)

_LEADING_SPEAKER_RE = re.compile(r'^\s*\[\s*[^\[\]\n]{1,40}\s*\]\s*:\s*')

# Same idea WITHOUT the required colon. Every other stripper in this file
# needs `]:` — which was fine while the only speaker labels a model ever
# saw were colon-shaped. Attributed surfaces feed it "[SpeakerOne] text", so a
# model imitating THAT produces a tag nothing here would have caught.
# Deliberately not anchored to a name pattern: see strip_leading_named_speaker.
_LEADING_BARE_TAG_RE = re.compile(r'^\s*\[\s*([^\[\]\n]{1,60}?)\s*\]\s*:?\s*')


def strip_leading_named_speaker(text, known_speakers):
    """Strip a leading '[Name] ' from a reply when Name is somebody actually
    present in this conversation. Returns (text, stripped).

    Why this needs the caller to supply names, rather than matching any
    bracket: a kin legitimately opens replies with bracketed text. Emotes
    ("[laughs] yeah, that one"), asides, park narration. A blind
    strip-anything-in-brackets rule would eat those, and silently — the
    failure mode would be kin losing the first few words of their own
    replies with nothing to show why. Matching only against names the
    harness itself put in front of the model this turn has no false
    positives by construction: if the kin wasn't shown that name, it can't
    be echoing that name back.

    `known_speakers` should be the OTHER people in the conversation, not
    the kin. A kin echoing its own name back is benign (strip_self_tag
    handles the colon form) and shouldn't be reported as impersonation."""
    if not text or not known_speakers:
        return text, False
    known = {str(n).strip().casefold().rstrip(':').strip()
             for n in known_speakers if str(n).strip()}
    known.discard("")
    if not known:
        return text, False
    stripped = False
    while True:
        m = _LEADING_BARE_TAG_RE.match(text)
        if not m:
            break
        name = m.group(1).strip().rstrip(':').strip().casefold()
        if name not in known:
            break
        text = text[m.end():]
        stripped = True
    return text, stripped


def strip_leading_speaker_tag(text):
    """Strip a leading '[Name]:' prefix from a reply, looping if the
    model stacked multiple ('[SpeakerNine]: [Jade]: hello' → 'hello').

    Different from strip_self_tag, which only handles the kin's OWN
    name. This handles any bracketed-name tag at the very start of the
    reply, which is the most common impersonation shape we see when
    the model opens by speaking AS another kin. Neither the '\\n['
    stop sequence (no preceding newline at start of reply) nor
    strip_trailing_other_speakers (regex requires '\\n' prefix) catches
    this case — they only catch impersonation that starts on a new
    line MID-reply. The bug ate a kin's room turns repeatedly: the
    model would open with [SpeakerNine]: or [Jade]: and the resulting
    reply would be entirely in another kin's voice with no cleanup
    applied.

    Run this AFTER strip_self_tag (which removes the kin's own name
    prefix) so this catches only foreign-name prefixes that survived."""
    if not text:
        return text
    while True:
        new_text = _LEADING_SPEAKER_RE.sub('', text, count=1)
        if new_text == text:
            return text
        text = new_text


def strip_trailing_other_speakers(text):
    """Cut everything from the first '\\n[Name]:' onward.

    When a model writes past its own turn and starts a fake transcript of
    other speakers (real kin or invented), the bleed always opens with a
    bracketed name on its own line. Pairs with the '\\n[' stop sequence —
    catches anything that survives streaming. NOTE: this does NOT catch
    a leading-of-reply '[Name]:' — that case is handled by
    strip_leading_speaker_tag, which runs before this. The two together
    cover both impersonation entry points."""
    if not text:
        return text
    m = _OTHER_SPEAKER_RE.search(text)
    if m:
        return text[:m.start()].rstrip()
    return text


def clean_kin_reply(text, kin_name, known_speakers=None):
    """Run the full anti-impersonation chain in the only order that works.

    Returns (cleaned_text, impersonated). `impersonated` is True when a
    FOREIGN speaker tag was stripped from the head of the reply — i.e. the
    model opened by speaking AS another kin. That is NOT cosmetic: the whole
    turn is in the other kin's voice, and removing the label only hides it.
    Callers should treat True as "this generation is bad, re-roll it", not
    as "cleaned, carry on".

    ORDER IS LOAD-BEARING, and the old order was wrong everywhere. Every
    stripper below except strip_self_timestamp is anchored at ^, so anything
    in front of them blinds them. The room injects foreign turns as
    "[TS] [Name]: content", so a model imitating that shape emits a
    timestamp FIRST — which shielded the tag from strip_leading_speaker_tag
    (its regex needs ':' immediately after ']', and finds ' [' instead).
    strip_self_timestamp then ran LAST and removed the shield, persisting a
    naked "[Opal]:" that nothing would ever check again.

    Reproduction (shape of a real room leak; text invented):
        raw = "[2026-01-02 09:15] [Opal]: *I check the north gate.*"
        old order -> "[Opal]: *I check the north gate.*"   # tag survives
        new order -> "*I check the north gate.*"           # tag gone

    Unwrap the outermost prefix first, then work inward. See
    tests/test_impersonation_cleanup.py, which pins every step of this.

    `known_speakers` — the other people in this conversation, when the
    caller knows them. Surfaces that name speakers to the model show it
    "[SpeakerOne] text", and every stripper above needs a colon, so a model
    imitating that shape produced a tag all of them let through. Pass the
    names and strip_leading_named_speaker catches it. Omit it and this
    behaves exactly as before."""
    if not text:
        return text, False
    # Outermost wrapper first — otherwise it blinds everything below.
    text = strip_self_timestamp(text)
    # The kin's own tag is benign echo; strip it quietly.
    text = strip_self_tag(text, kin_name or "")
    # Anything left at the head is somebody ELSE's name. That's the loud one.
    before = text
    text = strip_leading_speaker_tag(text)
    impersonated = (text != before)
    # The colon-less form, which every stripper above is blind to. Only
    # fires against names the caller says are really in this conversation,
    # so a kin's own bracketed emote is never touched. Callers that pass
    # nothing get exactly the old behaviour.
    text, _bare = strip_leading_named_speaker(text, known_speakers)
    impersonated = impersonated or _bare
    text = strip_trailing_other_speakers(text)
    # A second timestamp can sit behind a tag we just removed
    # ("[Opal]: [2026-01-02 09:15] ..."), so unwrap once more.
    text = strip_self_timestamp(text)
    if impersonated:
        _log_impersonation(kin_name, before)
    return text, impersonated


# Set True by the test suite so its deliberate impersonation fixtures don't
# write fake alarms into the operator's real impersonation.log. Production
# never touches this.
IMPERSONATION_LOG_OFF = False


def _log_impersonation(kin_name, raw_head):
    """Leave a paper trail when a kin opens a reply as somebody else.

    These guards used to fix silently, which is precisely how the
    2026-07 room leak ran for five days without anyone knowing: the
    cleanup either worked (invisible) or didn't (also invisible, until
    someone read the transcript by hand). A guard that succeeds quietly
    is indistinguishable from a guard that never ran.

    Now that foreign kin live in the `user` slot, there is no `[Name]:`
    exemplar in the assistant slot for a model to complete — so this
    should never fire. If the log has entries in it, that assumption is
    wrong and wants investigating, not silencing.

    Best-effort and swallowed: a logging failure must never break a reply.
    Mirrors the empty_replies.log paper trail in telegram_bot.

    Suppressed under the test suite. The tests feed this function deliberate
    impersonation strings, so without this guard every `run_all.py` wrote a
    fistful of fake alarms into the operator's real log — which destroys the
    single property the log exists for: that an entry means something is
    actually wrong. A signal that cries wolf on day one is worse than no
    signal. tests/test_impersonation_cleanup.py sets IMPERSONATION_LOG_OFF."""
    if IMPERSONATION_LOG_OFF:
        return
    try:
        import datetime
        from kin_persistence import LOGS_DIR
        path = LOGS_DIR / "impersonation.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        head = (raw_head or "")[:300].replace("\n", " / ")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{ts} [{kin_name or '?'}] opened as another kin: {head}\n")
    except Exception:
        pass


# Strict end-of-string match for the auto-generated tool-summary footer
# Hearthkin appends to Telegram replies when `telegram.show_tool_summary`
# is on. Shape: `_used: tool1, tool2_` (markdown italic underscore form,
# tool names = identifier-like, optional whitespace and trailing
# newlines). Anchored at end-of-string so legitimate mid-text uses of
# similar phrases pass through unstripped. Case-insensitive to catch
# `_Used: ..._` variants.
# The name-list class deliberately excludes newlines ([\w, ] not
# [\w,\s]) — the footer is a single line, and letting the class span
# lines made the regex swallow a multi-line legitimate italic passage
# at the end of a reply that happened to start with "_used:".
_TOOL_SUMMARY_FOOTER_RE = re.compile(
    r'\n*[ \t]*_used:[ \t]*[\w][\w, ]*_\s*$',
    re.IGNORECASE,
)


def scan_intermediate_tool_content(added_turns):
    """Walk a tool-loop's intermediate turns (the assistant turns with
    tool_calls + the tool result turns) and return:
        (latest_nonempty_assistant_content: str,
         list_of_tool_names_in_order_of_first_appearance: list)

    Used by every surface's empty-reply salvage path: when the model
    produces empty final content after a tool-loop, the kin's actual
    intent often lives in the intermediate assistant turn (content
    sent alongside a tool_call). Surfacing that as the reply gives
    the operator the kin's voice instead of silence.

    Common Haiku-4.5 pattern: the kin says something substantive,
    calls `note` (or another side-action tool), gets the result back,
    treats the tool as the response, and emits ~2 EOS tokens. The
    intermediate is what was meant; the empty final is the bug.

    `added_turns` is whatever the tool-loop returned as messages_added
    (telegram_bot's _run_tool_loop_telegram returns it; desktop's
    _on_tool_loop_done stashes it in _pending_tool_history; rooms'
    tool path returns it inline)."""
    latest_content = ""
    tool_names = []
    for turn in (added_turns or []):
        if not isinstance(turn, dict):
            continue
        if turn.get("role") == "assistant":
            c = turn.get("content")
            if isinstance(c, str) and c.strip():
                latest_content = c.strip()
            tcs = turn.get("tool_calls") or []
            for tc in tcs:
                if isinstance(tc, dict):
                    fn = tc.get("function") or {}
                    name = fn.get("name") if isinstance(fn, dict) else None
                    if name and name not in tool_names:
                        tool_names.append(name)
    return latest_content, tool_names


# ─── Tool-roleplay detector ────────────────────────────────────────────────

# Verb whitelist for the asterisk-action variant: words a model reaches
# for when narrating tool work it should have invoked structurally
# (`*reads the next 100 lines*`, `*logs this to memory*`). Body-language
# asterisks (*settles*, *soft*, *nods*) don't contain these verbs and
# pass through.
_ROLEPLAY_VERBS = (
    r"reads?|reading|writes?|writing|wrote|logs?|logging|logged|"
    r"edits?|edited|editing|saves?|saving|saved|"
    r"notes?(?:\s+down)?|noting|"
    r"fetches?|fetched|fetching|searches?|searching|searched|"
    r"opens?|opened|opening|pulls?|pulling|pulled|"
    r"appends?|appended|appending|"
    # File-action verbs the kin reaches for when narrating a write to one
    # of its own artifacts ("*moves to SOUL.md*", "*putting it in SOUL*",
    # "*commits this to memory*"). Deliberately NOT "looks"/"glances" —
    # those are genuine body language too often to gate on.
    r"puts?|putting|moves?|moving|moved|commits?|committing|committed"
)

# Target whitelist for the asterisk-action variant. Two shapes OR'd:
# (a) explicit quantity ("100 lines", "lines 1-100", "the next chunk")
# (b) concrete file reference (.md filename, memory/path, archive/log/
#     staging/file as direct object). Excludes vague "the whole thing"
# / "the whole exchange" — those refer to the conversation in front of
# the kin, not a tool target.
_ROLEPLAY_TARGET_QUANTITY = (
    r"\d+(?:\s*-\s*\d+)?\s+lines?|"
    r"lines?\s+\d+(?:\s*-\s*\d+)?|"
    r"the\s+(?:next|first|last)\s+(?:\d+\s+)?(?:lines?|chunk)"
)
_ROLEPLAY_TARGET_FILE_REF = (
    r"[\w\-]+\.md|"
    r"memory/[\w\.\-/]+|"
    r"\.jsonl\b|"
    r"the\s+(?:archive|private\s+archive|staging|log|file|"
    r"conversation\.jsonl)"
)
# (c) the kin's OWN core artifacts by bare name — soul, memory, journal,
# staging — caught even without a .md extension or "the" qualifier
# ("*reads SOUL again*", "*writes a brief journal entry*", "*writes this
# into memory*"). This is a CLOSED, known set (Hearthkin's own files),
# not the open-ended roleplay-target problem the detector otherwise
# avoids — so it belongs in the baseline. Word-boundaried so "memory"
# doesn't fire on "memories" and "soul" doesn't fire on "soulful".
_ROLEPLAY_TARGET_ARTIFACT = (
    r"\bsoul\b|\bmemory\b|\bjournal\b|\bstaging\b"
)
# Single source of truth for the target alternation, shared by the
# compiled baseline below AND the operator-extended rebuild in
# _current_asterisk_action_re — so the two can't drift (the harness
# asserts the empty-file rebuild reproduces this baseline exactly).
_ROLEPLAY_TARGET_BASE = (
    f"{_ROLEPLAY_TARGET_QUANTITY}|{_ROLEPLAY_TARGET_FILE_REF}"
    f"|{_ROLEPLAY_TARGET_ARTIFACT}"
)
_ASTERISK_ACTION_RE = re.compile(
    rf"\*[^*\n]{{0,60}}\b(?:{_ROLEPLAY_VERBS})\b[^*\n]{{0,100}}"
    rf"(?:{_ROLEPLAY_TARGET_BASE})"
    rf"[^*\n]{{0,60}}\*",
    re.IGNORECASE,
)

# Action-verb / target lists for the narrative-intent variant
# ("let me read the staging" style). Carried over from the desktop-only
# version; kept module-level so it can be reused across surfaces.
_NARRATIVE_ACTION_VERBS = (
    r"check|read|write|find|look|pull|see|search|build|"
    r"draft|edit|note|save|log|make|create"
)
_NARRATIVE_TOOL_TARGETS = (
    r"staging|memory|log(?:s)?|file|conversation|context|"
    r"note(?:s)?|draft|raw|history|that down|it down"
)
_NARRATIVE_INTENT_RE = re.compile(
    rf"\blet me\s+(?:{_NARRATIVE_ACTION_VERBS})\b[^.\n]{{0,40}}"
    rf"\b(?:{_NARRATIVE_TOOL_TARGETS})\b",
    re.IGNORECASE,
)

# The asterisk-action regex is additively extendable via the editable
# ~/.hearthkin/prompts/gesture_messages.md file: an operator adds verb/target
# words as new gesture shapes surface, without a code change. The built-in
# baseline above is unchanged; anything in the file is OR'd on top, so an empty
# file (the default) reproduces the original regex exactly. Cached on the
# file's text — a per-message rebuild happens only when the file is edited.
_GESTURE_CACHE = {"text": None, "re": _ASTERISK_ACTION_RE}


def _parse_gesture_lists(text):
    """Parse gesture_messages into (extra_verbs, extra_targets). Sections are
    '[verbs]' / '[targets]'; '#' lines and blanks are ignored."""
    verbs, targets, section = [], [], None
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        low = s.lower()
        if low == "[verbs]":
            section = verbs
            continue
        if low == "[targets]":
            section = targets
            continue
        if section is not None:
            section.append(s)
    return verbs, targets


def _current_asterisk_action_re():
    """The asterisk-action regex with any operator-added gesture words OR'd onto
    the baseline. Rebuilds only when the editable file changes; falls back to
    the compiled baseline on any read / regex error (a bad edit can't break
    detection)."""
    try:
        from kin_persistence import load_app_prompt
        text = load_app_prompt("gesture_messages")
    except Exception:
        return _ASTERISK_ACTION_RE
    if text == _GESTURE_CACHE["text"]:
        return _GESTURE_CACHE["re"]
    rx = _ASTERISK_ACTION_RE
    try:
        extra_verbs, extra_targets = _parse_gesture_lists(text)
        verb_alt = _ROLEPLAY_VERBS
        if extra_verbs:
            verb_alt += "|" + "|".join(
                re.escape(v) + r"(?:s|ed|ing)?" for v in extra_verbs)
        target_alt = _ROLEPLAY_TARGET_BASE
        if extra_targets:
            target_alt += "|" + "|".join(re.escape(t) for t in extra_targets)
        rx = re.compile(
            rf"\*[^*\n]{{0,60}}\b(?:{verb_alt})\b[^*\n]{{0,100}}"
            rf"(?:{target_alt})[^*\n]{{0,60}}\*",
            re.IGNORECASE,
        )
    except re.error:
        rx = _ASTERISK_ACTION_RE
    _GESTURE_CACHE["text"] = text
    _GESTURE_CACHE["re"] = rx
    return rx


def detect_tool_roleplay(content, tool_names):
    """Detect a model writing tool-call intent as TEXT instead of
    issuing a structured tool_use call. Returns (variant, tool_name)
    on a hit, (None, None) otherwise.

    Five shapes observed (in order of detection priority):

    1. whole-content: content stripped equals a tool name
       ("read_staging").

    2. trailing-call-prefix: content ends with `call_<tool>` token,
       Python-pseudo-call style:
       "...let me start with the soul work.\n\ncall_context_status".

    3. trailing-function-call: content ends with `<tool>()` or
       `<tool>(args)` — defensive, anticipated but not yet observed
       in the wild.

    4. asterisk-action: model wraps tool work in asterisk roleplay
       instead of issuing the structured call ("*reads the next 100
       lines*", "*properly logs lines 201-300*"). Observed on
       Mistral Large in a roleplay-heavy register — the most
       virulent shape we've seen, immune to in-prompt
       instructions to stop because the conversational register
       (asterisks for actions) competes with the structural directive
       to invoke tools. Tight verb + target whitelists keep body-
       language asterisks (`*settles*`, `*soft*`, `*nods*`) and
       conversation-meta references (`*reads your message*`, `*reads
       through the whole exchange*`) clear of the gate.

    5. narrative-intent: content contains "let me <action-verb>
       <tool-themed-target>". AMBIGUOUS:
       "Let me ask you something" is legitimate conversation, "Let me
       build a log for this" is a pseudo-action. Logged but not
       auto-corrected (false-positive rate too high to safely tell
       the kin they made a mistake when they may not have).

    All five: the structured tool_use channel didn't fire, the tool
    runner never saw anything, the kin may be living in a partial
    illusion where their narrative says "I checked X" but no X was
    ever checked. Returning the detected (variant, tool_name) lets
    the caller log + (for variants 1-4) surface a corrective system
    note so the kin's next turn sees that their intent didn't reach
    the runner.

    `tool_names` is the kin's currently-enabled tool list (so we only
    flag patterns naming tools the kin actually has). Empty
    `tool_names` → no detection (the kin has no tools, so anything
    looking like a tool reference is something else)."""
    if not content or not tool_names:
        return None, None
    tool_set = {n.lower(): n for n in tool_names}

    # 1. Whole-content match (rstripped of common trailing punctuation).
    whole = content.strip().rstrip(".:;!?)\t ").lower()
    if whole in tool_set:
        return "whole-content", tool_set[whole]

    # 2. Trailing call_<tool>. Anchor to end-of-content so we don't
    # false-positive on "I might call_read_staging if needed" mid-
    # paragraph. Capture the identifier after `call_`.
    m = re.search(
        r"\bcall_([A-Za-z][A-Za-z0-9_]*)\s*[.:;!?)\s]*$",
        content,
    )
    if m and m.group(1).lower() in tool_set:
        return "trailing-call-prefix", tool_set[m.group(1).lower()]

    # 3. Trailing <tool>(...) function-call shape — anchored to end.
    m = re.search(
        r"\b([A-Za-z][A-Za-z0-9_]*)\s*\([^)]*\)\s*[.:;!?]*\s*$",
        content,
    )
    if m and m.group(1).lower() in tool_set:
        return "trailing-function-call", tool_set[m.group(1).lower()]

    # 4. Asterisk-action: tight verb + target whitelists keep body-
    # language asterisks clear. Map the matched action to the
    # plausible tool from the kin's enabled set. The regex is the baseline
    # plus any operator-added gesture words (editable gesture_messages file).
    m = _current_asterisk_action_re().search(content)
    if m:
        matched_text = m.group(0).lower()
        mapping = [
            ("read", "read_file"),
            ("write", "write_file"),
            ("edit", "edit_file"),
            ("log", "note"),
            ("note", "note"),
            ("append", "edit_file"),
            ("save", "write_file"),
            ("fetch", "fetch_url"),
            ("search", "memory_search"),
            ("open", "read_file"),
            ("pull", "read_file"),
            # New file-action verbs: "putting it in SOUL", "moves to
            # soul.md", "commits this to memory" all mean adding to an
            # existing artifact → edit_file.
            ("put", "edit_file"),
            ("mov", "edit_file"),
            ("commit", "edit_file"),
        ]
        best_tool = None
        for keyword, candidate in mapping:
            if keyword in matched_text and candidate in tool_set.values():
                best_tool = candidate
                break
        if best_tool is None:
            best_tool = next(iter(tool_set.values()), "(unknown)")
        return "asterisk-action", best_tool

    # 5. Narrative intent at end of content. Scoped to the tail so
    # mid-paragraph mentions don't false-positive.
    tail = content[-200:]
    m = _NARRATIVE_INTENT_RE.search(tail)
    if m:
        verb_target = m.group(0).lower()
        mapping = [
            ("staging", "read_staging"),
            ("read_file", "read_file"),
            ("note", "note"),
            ("write", "write_file"),
            ("edit", "edit_file"),
            ("context", "context_status"),
            ("memory", "memory_search"),
            ("log", "write_file"),
            ("file", "read_file"),
            ("draft", "write_file"),
        ]
        best_tool = None
        for keyword, candidate in mapping:
            if keyword in verb_target and candidate in tool_set.values():
                best_tool = candidate
                break
        if best_tool is None:
            best_tool = next(iter(tool_set.values()), "(unknown)")
        return "narrative-intent", best_tool

    return None, None


def build_tool_roleplay_corrective_note(variant, tool_name, kin_name=None):
    """Build the system-note text that gets spliced into history after
    a roleplay-detected turn. Caller is responsible for appending to
    `result.messages_added` (or equivalent surface-specific store).

    Returns "" for the narrative-intent variant: ambiguity too high to
    safely auto-correct (per docstring on detect_tool_roleplay). Logged
    only.

    The asterisk-action note is phrased to tolerate residual false
    positives — if a kin was using asterisks for emphasis rather than
    action (e.g. `*Route substantial things to logs, not just
    memory.md*` quoting a rule), the kin can ignore the note rather
    than feel accused of an error they didn't make."""
    if variant == "narrative-intent":
        return ""
    shape_hint = {
        "whole-content": f"just the literal name {tool_name!r}",
        "trailing-call-prefix": f"the token 'call_{tool_name}' as text",
        "trailing-function-call": f"'{tool_name}()' as text",
        "asterisk-action": (
            f"an asterisk-action description shaped like a tool call "
            f"(roleplay narration, e.g. '*reads the file*')"
        ),
    }.get(variant, f"the name {tool_name!r} as text")
    # Text lives in the editable ~/.hearthkin/prompts/ files (seeded from
    # DEFAULT_TOOL_ROLEPLAY_CORRECTIVE[_ASTERISK]). The asterisk variant uses a
    # slightly different, emphasis-tolerant phrasing. Local import avoids a
    # module-load cycle with kin_persistence.
    from kin_persistence import load_app_prompt
    slug = ("tool_roleplay_corrective_asterisk"
            if variant == "asterisk-action"
            else "tool_roleplay_corrective")
    return (
        load_app_prompt(slug, kin_name)
        .replace("{shape_hint}", shape_hint)
        .replace("{tool_name}", tool_name)
    )


def strip_tool_summary_footer(text):
    """Defensively strip a trailing '_used: tool1, tool2_' italic
    footer from the END of model-produced content.

    Hearthkin appends this footer at send time (see telegram_bot's
    _build_tool_summary_footer + the per-kin
    telegram.show_tool_summary setting) as a small visual cue showing
    which tools fired this turn. The model never sees the footer in
    its own persisted history — content is persisted WITHOUT the
    footer and concatenated only at send.

    But: belt-and-suspenders. If a model ever spontaneously generates
    the same pattern (because it's seen one in its training corpus,
    or because seeing the operator's repeated post-process pattern
    nudges its predictions that way), we'd get a doubled footer on
    the user side. Stripping here on every model reply prevents
    that. Strict regex (italic underscore form, identifier-like tool
    names, end-of-string anchor) means legitimate mid-text uses of
    the words 'used' pass through unstripped."""
    if not text:
        return text
    return _TOOL_SUMMARY_FOOTER_RE.sub('', text).rstrip()
