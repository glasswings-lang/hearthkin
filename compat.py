# SPDX-License-Identifier: CC0-1.0

"""
compat — pre-flight compatibility checks between a kin's current state
and a target model.

The motivating case: a kin migrates from Anthropic Haiku to Mistral
Large. Their history is full of long Anthropic-format tool_call_ids
that Mistral truncates to 9 characters, collapsing many calls onto the
same prefix and producing a confusing "Duplicate tool call id" 400.
Without a pre-flight check, the operator finds out only when the next
send fails. With one, the dialog at swap time can say "this kin's
history has 471 tool calls in a format the target model needs to
rewrite — Hearthkin will do that automatically, no action needed."

Each potential incompatibility is one CompatNote. analyze() returns
the list (empty when everything looks fine). The dialog layer
(model_browser / edit_kin) renders the notes for the operator.

The checks are pattern-based, not provider-specific — adding a new
provider quirk means populating a profile and possibly adding a check,
not writing new UI.

What's checked today (severity in parentheses):

  - Context cap headroom — num_ctx near or above model max (warning/blocker)
  - Output token cap — num_predict exceeds model's output ceiling (warning)
  - Tool support — kin uses tools, target can't call them (blocker)
  - Image input — recent images in history, target is text-only (info)
  - Audio input — recent audio in history, target can't accept (warning)
       [forward-looking; fires when an audio-to-kin path lands]
  - Caching support — kin has cache=True, target provider doesn't cache (warning)
  - Reasoning channel — thinking enabled, target has no reasoning channel (info)
  - Tool-call ID format — long IDs need rewriting on send (info; auto-handled)
  - Provider quirk notes — free-form heads-ups per family (info)

What we do NOT yet check (the blind spots — worth knowing about so a
future regression or new provider quirk doesn't go silent):

  - Image OUTPUT support (some models generate images; we don't yet
    have a use case but Gemini Imagen and others could matter)
  - Audio OUTPUT support (text-to-speech via provider; Hearthkin uses
    ElevenLabs separately so this is low-priority but would matter if
    a kin wanted to ride provider-native TTS)
  - Video input (Gemini accepts video; no current Hearthkin path)
  - Structured output / JSON mode constraints (rare for chat use)
  - Provider-specific stop-sequence support (Anthropic ignores some
    stop sequences others honor; relevant for the room anti-
    impersonation `\\n[` stop)
  - System message handling (Anthropic concatenates all role=system
    into one top-level field, dissociating mid-conversation system
    notes from their nearest message — see the Telegram group
    attribution work that moved attribution inline because of this)
  - Per-provider content-policy strictness (NSFW kin behavior differs
    between providers; routing pins via openrouter_provider_order
    handle this today but a check could surface the mismatch)
  - Per-provider rate limits (each tier has different caps; a busy
    kin moved to a strict-tier provider could 429 unexpectedly)
  - Pricing-tier delta (silent cost jump when switching to a more
    expensive provider — usage.log catches it after the fact but a
    proactive estimate would help)
  - Streaming SSE format quirks (mostly homogenized by OpenRouter
    but heartbeat patterns differ — affects the watchdog)

The two principles behind keeping this list visible: (1) when a kin
breaks in a way none of the existing checks would have caught,
SEARCH this list before assuming a new category. (2) When you add a
new modality / capability anywhere in Hearthkin (audio-to-kin being
the canonical example), add the corresponding profile field AND a
check in the same patch — even if the check just declares the
dimension and stays dormant until data exists. Forward-declaring the
slot is cheap; back-fitting after the dimension has shipped without
checks is what produces the kind of bug we spent today's session
diagnosing.
"""

from dataclasses import dataclass, field
from typing import List, Optional


# ─── Data shapes ─────────────────────────────────────────────────────

SEVERITY_INFO = "info"          # heads-up, no action needed
SEVERITY_WARNING = "warning"    # something will be silently dropped / rewritten
SEVERITY_BLOCKER = "blocker"    # the send will fail; user must act


@dataclass
class CompatNote:
    """One compatibility finding. Severity drives display order and
    icon; title is the headline; detail is the plain-English
    explanation. action_hint is optional ("Recommended: ...") when
    the operator has a concrete next step."""
    severity: str
    title: str
    detail: str
    action_hint: str = ""

    def is_blocker(self):
        return self.severity == SEVERITY_BLOCKER


@dataclass
class ModelProfile:
    """What we know about a model's API surface. Used to compare
    against a kin's stored state. Default values are the permissive
    ones (we want unknown-model checks to NOT cry wolf).

    None means "we don't know" — distinct from False ("we know it
    doesn't"). Checks treat None as "skip this check" to avoid false
    alarms on unrecognized models.

    Modality fields beyond text+images are pre-declared even where
    Hearthkin doesn't use them today (audio_input, audio_output,
    video_input) so that when those modalities land — audio-to-kin
    being the next likely one — the profile already has the slot, the
    provider data is already populated, and only the check function
    needs to be added. No retroactive ModelProfile schema change to
    coordinate across files."""
    family: str = "unknown"          # "anthropic" / "openai" / "mistralai" / "google" / "ollama" / "openrouter-generic"
    max_context: int = 0             # 0 = unknown (skip context-cap check)
    max_output_tokens: int = 0       # 0 = unknown; non-zero is the hard ceiling
    # Modality / capability fields:
    supports_tools: Optional[bool] = None
    supports_images: Optional[bool] = None       # vision input
    supports_audio_input: Optional[bool] = None  # speech input (forward-looking)
    supports_audio_output: Optional[bool] = None # speech output (forward-looking)
    supports_video_input: Optional[bool] = None  # video input (forward-looking)
    supports_caching: bool = False               # prompt caching (cost-relevant)
    supports_thinking: Optional[bool] = None     # reasoning channel
    # Strict-format quirks (each maps to a corresponding _check_* function):
    tool_id_strict_9char: bool = False           # Mistral
    strict_field_validation: bool = False        # Mistral
    strict_role_alternation: bool = False        # Mistral (less tolerant than Anthropic)
    notes: List[str] = field(default_factory=list)  # free-form heads-ups specific to this family


# ─── Provider profile lookup ─────────────────────────────────────────

def _profile_for_model(model_id):
    """Return the ModelProfile for a given model id. Looks up by
    provider-family prefix on OpenRouter, falls back to "ollama" for
    bare names. Calls into model_utils / llm_backend for live
    capability lookups where possible so the profile reflects the
    actual model's current shape, not a stale hardcoded list."""
    if not model_id:
        return ModelProfile()

    if model_id.startswith("openrouter/"):
        return _openrouter_profile(model_id)
    return _ollama_profile(model_id)


def _openrouter_profile(model_id):
    """Build a profile for an OpenRouter model. Family-specific
    quirks live here; live capability lookups (context length,
    image support) go through the cached OpenRouter catalogue."""
    family = _family_from_model_id(model_id)
    profile = ModelProfile(family=family)

    # Live lookups against the OpenRouter catalogue.
    try:
        from llm_backend import list_openrouter_models, model_supports_images
        catalogue = list_openrouter_models() or []
        bare = model_id[len("openrouter/"):] if model_id.startswith("openrouter/") else model_id
        entry = next((m for m in catalogue if m.get("id") == bare), None)
        if entry:
            profile.max_context = int(entry.get("context_length") or 0)
            # Output cap, when reported. OpenRouter exposes this under
            # top_provider.max_completion_tokens for hosted models.
            tp = entry.get("top_provider") or {}
            mct = tp.get("max_completion_tokens")
            if isinstance(mct, (int, float)) and mct > 0:
                profile.max_output_tokens = int(mct)
            # Modality fields from OR's `architecture.input_modalities`
            # list (when present). Defensive lookup — older catalogue
            # entries may not have it. When the catalogue EXPLICITLY
            # lists input modalities, their absence means False ("we
            # know it doesn't"), not None ("unknown") — the old
            # `... or None` coercion meant these could never be False
            # and the audio/video checks were permanently dormant.
            # None only when the list itself is missing/empty.
            arch = entry.get("architecture") or {}
            input_modalities = arch.get("input_modalities") or []
            if isinstance(input_modalities, list) and input_modalities:
                profile.supports_audio_input = "audio" in input_modalities
                profile.supports_video_input = "video" in input_modalities
        profile.supports_images = model_supports_images(model_id)
    except Exception:
        pass

    # Caching support: the single source of truth is
    # llm_backend._CACHE_SUPPORTED_PROVIDERS (a hand-copied tuple here
    # had already drifted — it was missing "moonshot").
    try:
        from llm_backend import _CACHE_SUPPORTED_PROVIDERS
        if family in _CACHE_SUPPORTED_PROVIDERS:
            profile.supports_caching = True
    except Exception:
        pass

    # Family-specific quirks.
    if family == "mistralai":
        profile.tool_id_strict_9char = True
        profile.strict_field_validation = True
        profile.strict_role_alternation = True
        profile.notes.append(
            "Mistral has a hard context cap (rejects overflow with 400). "
            "Anthropic / OpenAI silently absorb overflows; Mistral does not."
        )

    if family == "anthropic":
        # The null-content-on-tool-calls quirk is auto-handled at send
        # time via _coerce_tool_call_assistant_content. No operator-
        # facing note needed.
        profile.supports_thinking = True  # most Anthropic models support reasoning channel
    elif family == "openai":
        profile.supports_thinking = True  # o-series; non-reasoning models accept exclude no-op
    elif family == "google":
        profile.supports_thinking = True
    elif family == "deepseek":
        profile.supports_thinking = True

    # Tool support is broadly true on OpenRouter-routed providers, but
    # individual models opt out. Leave as None when unsure rather than
    # claim a wrong answer.
    return profile


def _family_from_model_id(model_id):
    """Pull the family name from `openrouter/<family>/<name>`. Returns
    'openrouter-generic' when the shape isn't recognized."""
    if not isinstance(model_id, str) or not model_id.startswith("openrouter/"):
        return "openrouter-generic"
    rest = model_id[len("openrouter/"):]
    parts = rest.split("/", 1)
    if len(parts) < 2:
        return "openrouter-generic"
    return parts[0].lower()


def _ollama_profile(model_id):
    """Build a profile for a local Ollama model. Capability lookups
    go through model_utils which calls Ollama's /api/show."""
    profile = ModelProfile(family="ollama")
    try:
        from model_utils import (
            _model_context_length, _model_supports_tools, _model_supports_vision,
        )
        profile.max_context = int(_model_context_length(model_id) or 0)
        # These detectors return True / False / None ("unknown" — e.g.
        # Ollama unreachable or an older daemon without a capabilities
        # field). Preserve the None: the ModelProfile contract is that
        # checks SKIP on None, and bool(None) → False used to turn
        # every detection failure into a false "doesn't support tools"
        # BLOCKER on Ollama model swaps.
        profile.supports_tools = _model_supports_tools(model_id)
        profile.supports_images = _model_supports_vision(model_id)
    except Exception:
        pass
    # Ollama doesn't do prompt caching, doesn't have an exclude-reasoning
    # flag, doesn't have format constraints on tool_call_ids.
    return profile


# ─── The actual checks ───────────────────────────────────────────────

def analyze_kin_for_target(kin_name, target_model):
    """Compare a kin's current configuration + history against a target
    model. Returns a list of CompatNote in display order (blockers
    first, then warnings, then info). Empty list means everything
    looks fine.

    Safe to call on any kin/target combo — missing data, unreadable
    files, unknown models all result in fewer findings rather than
    raised exceptions. The dialog layer should be resilient to an
    empty list (don't pop a dialog at all in that case)."""
    from kin_persistence import load_agent_config, load_agent_conversation

    notes = []
    try:
        cfg = load_agent_config(kin_name) or {}
    except Exception:
        cfg = {}
    try:
        conversation = load_agent_conversation(kin_name) or []
    except Exception:
        conversation = []

    profile = _profile_for_model(target_model)

    # Run each check; each appends 0 or more notes.
    _check_context_headroom(cfg, profile, notes)
    _check_output_token_cap(cfg, profile, notes, kin_name=kin_name)
    _check_window_fits_a_conversation(cfg, profile, notes, kin_name=kin_name)
    _check_tool_support(cfg, conversation, profile, notes, kin_name=kin_name,
                        target_model=target_model)
    _check_image_support(conversation, profile, notes)
    _check_audio_support(conversation, profile, notes)
    _check_caching_support(cfg, profile, notes)
    _check_tool_id_format(conversation, profile, notes)
    _check_thinking_support(cfg, profile, notes)

    # Free-form provider notes appended last (informational).
    for n in profile.notes:
        notes.append(CompatNote(SEVERITY_INFO, "Provider note", n))

    # Sort: blockers, warnings, info.
    severity_order = {SEVERITY_BLOCKER: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}
    notes.sort(key=lambda n: severity_order.get(n.severity, 99))
    return notes


def _check_context_headroom(cfg, profile, notes):
    """Flag when num_ctx is at or very near the model's hard max.
    The slop between Hearthkin's pre-send estimate and the provider's
    real token count can be a few percent on a normal turn (and more
    on image-bearing turns); leaving zero headroom turns that slop
    into a 400."""
    if not profile.max_context:
        return
    try:
        num_ctx = int(cfg.get("num_ctx") or 0)
    except (TypeError, ValueError):
        return
    if num_ctx <= 0:
        return
    if num_ctx > profile.max_context:
        notes.append(CompatNote(
            SEVERITY_BLOCKER,
            "Context window above model's hard limit",
            f"This kin's num_ctx is {num_ctx:,} but the target model only "
            f"supports {profile.max_context:,}. Every message will be rejected "
            f"with a context-overflow error.",
            action_hint=f"Recommended: set num_ctx to {int(profile.max_context * 0.92):,} or lower.",
        ))
    elif num_ctx >= int(profile.max_context * 0.95):
        notes.append(CompatNote(
            SEVERITY_WARNING,
            "Context window very close to model's hard limit",
            f"This kin's num_ctx ({num_ctx:,}) is at or near the target model's "
            f"max ({profile.max_context:,}). The small mismatch between Hearthkin's "
            f"size estimate and the provider's real count can tip you over the cap "
            f"and cause a 400 error — especially on image-bearing turns.",
            action_hint=f"Recommended: set num_ctx to about {int(profile.max_context * 0.92):,} for a safer buffer.",
        ))


def _check_window_fits_a_conversation(cfg, profile, notes, kin_name=""):
    """Flag a window too small to hold this kin's own soul + memory plus
    a tool reply reserve — the shape where the kin answers fluently and
    remembers nothing.

    This is not a provider quirk; it's arithmetic, and it bites hardest
    on the kin most likely to have a small window: a brand-new one, made
    to try something out, with tools switched on. Found live 2026-08-06
    on a kin at num_ctx 8192. The tool loop reserves 8,000 output tokens,
    which took the entire window, so every turn of conversation was
    trimmed away and the model was sent its soul prompt alone. It kept
    introducing itself, ignored a file it had just read, and addressed
    its own name — and from a chat window that reads as the model being
    stupid, not as a setting being wrong.

    llm_backend now clamps the reserve and always restores the newest
    question, so the failure is survivable and logged. This exists so
    it doesn't have to be survived: the person is told while they're
    still in Settings, with the number to type."""
    try:
        num_ctx = int(cfg.get("num_ctx") or 0)
    except (TypeError, ValueError):
        return
    if num_ctx <= 0:
        return
    has_tools = False
    if kin_name:
        try:
            from kin_persistence import load_kin_tools
            has_tools = bool(load_kin_tools(kin_name))
        except Exception:
            has_tools = False
    if not has_tools:
        return
    _ROOM_FOR_A_CONVERSATION = 4000   # a few exchanges, not one
    # Measure the system prompt this kin ACTUALLY sends, by building it
    # the way every surface does. Measuring soul + memory alone was the
    # first version of this check and it stayed silent on the kin that
    # prompted it: that kin's soul is under a thousand characters, while
    # the block that goes out — base prompt, tool scaffolding, memory
    # frame — is around eleven thousand. The part you wrote is the small
    # part. Guessing here produces a check that only fires on cases you
    # would have spotted anyway.
    persona_chars = 0
    try:
        from kin_persistence import (
            load_soul, load_memory, load_kin_tools, build_system_prompt,
        )
        tool_names = load_kin_tools(kin_name)
        persona_chars = len(build_system_prompt(
            load_soul(kin_name) or "",
            load_memory(kin_name) or "",
            enabled_tools=tool_names,
            kin_name=kin_name,
        ) or "")
        # The surfaces append the tool-use hint and the authoring hint on
        # top of that, in the same system block. On the kin this was found
        # on they were most of it — the built prompt measured about 6,300
        # characters while the block that actually went out was around
        # 11,000. Leaving them out made this check stay quiet at exactly
        # the window sizes that were failing, which is worse than not
        # having the check.
        from kin_persistence import load_app_prompt
        for slug in ("tool_use_hint", "authoring_bridge_hint"):
            try:
                persona_chars += len(load_app_prompt(slug, kin_name) or "")
            except Exception:
                pass
    except Exception:
        return
    if not persona_chars:
        return
    persona_tokens = persona_chars / 4.0     # llm_backend._CHARS_PER_TOKEN
    # Solve llm_backend's own budget arithmetic for the window size that
    # leaves room for the persona plus a few exchanges, rather than
    # picking a threshold and a suggestion separately — two numbers that
    # disagree is how a check ends up warning you and then advising a
    # setting that doesn't fix it.
    #
    #   budget = [(num_ctx - 2000) * 0.5 + 2000] / ratio      (reply
    #   reserve capped at half the window, then the standard 2,000-token
    #   response reserve added back)
    #
    # want: budget >= persona + _ROOM_FOR_A_CONVERSATION
    #   =>  num_ctx >= 3 * persona + 10,000        (at ratio 1.5)
    #
    # Ratio 1.5 is llm_backend's first-call default and the least
    # generous value in play; a kin that has been talking a while
    # measures lower and gets more room than this. Using the pessimistic
    # one means the advice is safe on a brand-new kin, which is exactly
    # the kin this happens to.
    needed = 3 * persona_tokens + 10000
    if num_ctx >= needed:
        return
    suggested = int((needed + 8191) // 8192) * 8192
    notes.append(CompatNote(
        SEVERITY_WARNING,
        "Context window too small to hold a conversation",
        f"With tools enabled, this kin's window ({num_ctx:,}) has to cover its "
        f"soul and memory (about {int(persona_tokens):,} tokens), room to reply, "
        f"and the conversation itself. There is little or nothing left for the "
        f"conversation. The kin will still answer — fluently — but it may see "
        f"none of what you said, which looks like the model being vacant rather "
        f"than a setting being wrong. Watch for it re-introducing itself, or "
        f"ignoring a file it just read.",
        action_hint=(
            f"Recommended: set num_ctx to {suggested:,} or higher in "
            f"Settings → Model && generation. Turning tools off for this kin "
            f"also frees the reserve."
        ),
    ))


def _check_output_token_cap(cfg, profile, notes, kin_name=""):
    """Flag when the kin's `num_predict` exceeds the target model's
    hard output ceiling. Most providers either reject the request or
    silently clip the response; either way the operator should know.
    Common gotcha: Mistral Large's 16k output cap when migrating from
    Anthropic Sonnet (64k). Tool-loop calls also floor num_predict to
    8000 internally — check against that too."""
    if not profile.max_output_tokens:
        return
    try:
        np = int(cfg.get("num_predict") or 0)
    except (TypeError, ValueError):
        np = 0
    # Tool-loop floor applies whenever the kin actually has tools
    # enabled (non-empty tools.json). NOT gated on tool_trust — every
    # kin has a tool_trust value ("untrusted" by default, which is
    # truthy), so that gate applied the 8000 floor to tool-less kin
    # and warned on every one of them.
    from llm_backend import TOOL_LOOP_MIN_OUTPUT_TOKENS
    has_tools = False
    if kin_name:
        try:
            from kin_persistence import load_kin_tools
            has_tools = bool(load_kin_tools(kin_name))
        except Exception:
            has_tools = False
    effective = max(np, TOOL_LOOP_MIN_OUTPUT_TOKENS) if has_tools else np
    if effective > profile.max_output_tokens:
        notes.append(CompatNote(
            SEVERITY_WARNING,
            "Output cap below this kin's request size",
            f"This kin asks for up to {effective:,} output tokens per turn "
            f"(num_predict, plus the tool-loop minimum when tools are in use), "
            f"but the target model caps replies at {profile.max_output_tokens:,}. "
            f"Long replies may be clipped or rejected. For normal chat this "
            f"rarely matters; for tool-using kin that emit large arguments "
            f"(write_file content, big web_search results) it can truncate "
            f"mid-argument and break the tool call.",
            action_hint=(
                f"Consider setting num_predict to {profile.max_output_tokens:,} "
                f"or lower in Settings → Model && generation."
            ),
        ))


def _check_audio_support(conversation, profile, notes):
    """Heads-up when the kin's history has audio attachments and the
    target doesn't accept audio input. Forward-looking — Hearthkin
    doesn't ship an audio-to-kin path yet (2026-06-09), but the design
    is in motion. When that lands, audio attachments will appear in
    history the same way image attachments do today (a separate field
    on user turns); this check will fire automatically without further
    code changes. Until then it's dormant."""
    if profile.supports_audio_input is True or profile.supports_audio_input is None:
        return
    has_recent_audio = False
    for m in conversation[-20:]:
        if isinstance(m, dict):
            audio = m.get("audio_attachments")
            if isinstance(audio, list) and audio:
                has_recent_audio = True
                break
    if has_recent_audio:
        notes.append(CompatNote(
            SEVERITY_WARNING,
            "Target model doesn't accept audio input",
            "This kin's recent history includes audio attachments. The "
            "target model is text/image-only — audio input won't reach it. "
            "Past replies where the kin transcribed or described an audio "
            "clip still carry that text. Future audio messages would need "
            "to be transcribed manually before the kin could engage with "
            "their content.",
        ))


def _check_tool_support(cfg, conversation, profile, notes, kin_name="", target_model=""):
    """Flag when the kin uses tools but the target model can't call
    them. The kin's tools.json is the runtime allowlist; recent tool
    round-trips in conversation are a signal the kin is actively
    tool-using."""
    if profile.supports_tools is False:
        claims_but_cannot = False
    else:
        # Declares tools, or unknown. A DECLARED capability is not a
        # promise: Ollama derives that flag from the model's template
        # (or its compiled renderer), which says the format can express
        # a tool call — not that the weights will ever emit one. A
        # roleplay finetune of a tool-trained base declares `tools`
        # truthfully and then narrates the call in prose forever.
        #
        # So consult the behavioural probe, and only the CACHED verdict:
        # this runs on the UI thread during a model swap, and an
        # inference call here is a multi-second freeze with the screen
        # reader silent. Never probed → nothing to say, same as before.
        claims_but_cannot = False
        try:
            from model_utils import probed_tool_calling
            claims_but_cannot = (probed_tool_calling(target_model) is False)
        except Exception:
            claims_but_cannot = False
        if not claims_but_cannot:
            return  # supported or unknown — don't cry wolf
    # Detect tool use: either tools.json is non-empty, or recent
    # conversation has tool round-trips.
    try:
        from kin_persistence import load_kin_tools
        enabled_tools = load_kin_tools(kin_name) if kin_name else []
    except Exception:
        enabled_tools = []
    has_recent_tool_calls = False
    for m in conversation[-40:]:
        if isinstance(m, dict) and (m.get("tool_calls") or m.get("role") == "tool"):
            has_recent_tool_calls = True
            break
    if not (enabled_tools or has_recent_tool_calls):
        return
    if claims_but_cannot:
        notes.append(CompatNote(
            SEVERITY_BLOCKER,
            "Target model says it can call tools, but doesn't",
            "This model reports tool support, and when it was actually asked "
            "for a tool call it answered in words instead and called nothing. "
            "That is not a formatting slip a retry can fix — it writes a "
            "description of using the tool, which reads like it worked. This "
            "kin has tools enabled, so those would quietly stop happening.",
            action_hint="Switch to a model that passed the tool-calling test, "
                        "or disable tools for this kin first.",
        ))
        return
    notes.append(CompatNote(
        SEVERITY_BLOCKER,
        "Target model doesn't support tools",
        "This kin has tools enabled and the conversation shows recent tool "
        "use. The target model can't call tools — tool requests in chat "
        "will fail and the kin will lose access to its file / memory / "
        "search abilities.",
        action_hint="Switch to a tool-supporting model, or disable tools for this kin first.",
    ))


def _check_image_support(conversation, profile, notes):
    """Heads-up when the kin's recent history includes image
    attachments but the target model is text-only. Hearthkin
    automatically drops image bytes for text-only models (the
    captions stay), so this is a heads-up, not a blocker."""
    if profile.supports_images is True or profile.supports_images is None:
        return
    has_recent_image = False
    for m in conversation[-20:]:
        if isinstance(m, dict) and isinstance(m.get("attachments"), list) and m["attachments"]:
            has_recent_image = True
            break
    if has_recent_image:
        notes.append(CompatNote(
            SEVERITY_INFO,
            "Target model is text-only; recent images won't be visible",
            "This kin's recent conversation includes image attachments. The "
            "target model doesn't accept image input — Hearthkin will drop the "
            "image bytes from each send (the text content stays). Past replies "
            "where the kin described an image still carry that description in "
            "the conversation. Only matters if you specifically wanted the kin "
            "to look at one of those images again on this model.",
        ))


def _check_caching_support(cfg, profile, notes):
    """Heads-up when the kin has cache=True but the target provider
    isn't on Hearthkin's known-caches list. Honest about uncertainty:
    OpenRouter's catalogue advertises cache-read prices for several
    providers we haven't verified actually fire under Hearthkin's
    chat-append usage pattern. The cost difference between "this
    provider caches and we save 10x" and "this provider doesn't cache
    and we pay full price every turn" is large — but in either
    direction the operator can detect the actual outcome by watching
    usage.log for a few sends after the switch. Severity info,
    pending real evidence."""
    if not cfg.get("cache", True):
        return  # cache off; nothing to note
    if profile.supports_caching:
        return  # all good
    notes.append(CompatNote(
        SEVERITY_INFO,
        "Target provider may not honor prompt caching",
        "This kin has caching enabled. The target model's provider isn't "
        "on Hearthkin's known-caches list. It may genuinely not support "
        "prompt caching the way Anthropic / OpenAI / Google do, OR it may "
        "cache under conditions we haven't verified yet. The cost difference "
        "is meaningful either way: on a caching provider, a steady "
        "conversation pays about 10% of normal cost per turn for the cached "
        "prefix; without caching, every turn pays the full prompt cost. So "
        "this could be roughly free OR a 5-10x per-turn cost increase — we "
        "don't know in advance for this provider.",
        action_hint=(
            "Watch ~/.hearthkin/logs/usage.log for the first several sends "
            "after switching. If cached=0 on every line, caching isn't "
            "firing for this provider and per-turn costs will be higher. "
            "If cached>0 starts appearing on follow-up turns, the provider "
            "does cache and you're fine."
        ),
    ))


def _check_tool_id_format(conversation, profile, notes):
    """Heads-up when the kin's history has tool_call_ids that don't
    match the target provider's required format. As of the 2026-06-09
    fix, Hearthkin auto-rewrites long IDs to Mistral's 9-char shape on
    send — so this is informational only. Without the fix it'd be a
    blocker."""
    if not profile.tool_id_strict_9char:
        return
    # Single source of truth for the 9-char format — a locally
    # compiled duplicate had no tie to the remap logic it describes.
    from llm_backend import _MISTRAL_TOOL_CALL_ID_RE as nine_char
    nonconforming = 0
    for m in conversation:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "assistant" and isinstance(m.get("tool_calls"), list):
            for tc in m["tool_calls"]:
                tid = (tc or {}).get("id")
                if isinstance(tid, str) and tid and not nine_char.match(tid):
                    nonconforming += 1
    if nonconforming:
        notes.append(CompatNote(
            SEVERITY_INFO,
            "Tool-call IDs will be rewritten on send",
            f"This kin's history has {nonconforming} tool call(s) with IDs in a "
            f"format the target model doesn't accept directly (typically "
            f"Anthropic-style 36-char IDs being sent to Mistral, which requires "
            f"9 characters). Hearthkin handles this automatically — IDs get "
            f"rewritten to the target's required shape on each send, with "
            f"assistant/tool pairings preserved. Nothing for you to do.",
        ))


def _check_thinking_support(cfg, profile, notes):
    """Heads-up when the kin has reasoning enabled but the target
    model doesn't support a reasoning channel. The send still works —
    the reasoning request becomes a no-op on the target — but the kin
    loses its 'think before responding' lever.

    Gates on `think_effort` — the toggle that actually requests
    reasoning from the provider — not the show/feed DISPLAY flags. A
    kin with think_effort=high but display off was previously
    unchecked; a kin with display on but reasoning off got a
    spurious note."""
    try:
        from kin_persistence import think_effort_of
        effort = think_effort_of(cfg)
    except Exception:
        effort = cfg.get("think_effort") or (
            "medium" if cfg.get("think") else "off")
    if not effort or effort == "off":
        return  # reasoning isn't requested at all
    if profile.supports_thinking is True or profile.supports_thinking is None:
        return
    notes.append(CompatNote(
        SEVERITY_INFO,
        "Target model doesn't expose a reasoning channel",
        "This kin has thinking display or feedback enabled, but the target "
        "model doesn't support a reasoning channel separate from content. "
        "Sends still work; the reasoning setting becomes a no-op for this "
        "model. The kin will still produce normal replies.",
    ))
