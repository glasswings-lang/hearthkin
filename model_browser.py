# SPDX-License-Identifier: CC0-1.0

"""
model_browser — wxDialog for picking a model from any supported provider.

Picks between local Ollama models and remote OpenRouter models in one
dialog, with a provider radio at the top that swaps the model source.
Designed warmth-first for OpenRouter: warmth is the primary filter for
remote models, since cloud models vary hugely in how well they hold
character. For Ollama models, warmth is mostly N/A
(you installed it, you decided to trust it) so those filters disable.

NVDA-accessible by design: provider radio → search field → warmth radio
→ capability checkboxes → result list → detail pane → buttons. Each
filter is a single focusable control. Detail pane is a focusable
read-only TextCtrl so NVDA can read the full model card on demand.

Warmth data lives in ~/.ai_programs/warmth_overrides.json (seeded with
a curated starter list on first run; editable by the user).
"""

import json
import time
from pathlib import Path

import wx

from llm_backend import (
    _ollama_show_raw,
    _resolve_ollama_host,
    list_ollama_models,
    list_openrouter_models,
)
from kin_persistence import (
    load_ollama_hosts,
    resolve_kin_ollama_host,
    THIS_MACHINE_NAME,
)


WARMTH_OVERRIDES_FILE = Path.home() / ".ai_programs" / "warmth_overrides.json"

# Sentinel value stored in the Ollama capabilities cache to mean
# "a fetch is in flight for this model — don't spawn a duplicate."
# Distinct from [] (a real "no capabilities reported" result) and
# from absence (never fetched). Identity-compared with `is`.
_OLLAMA_CAPS_INFLIGHT = object()


# Curated seed — first-run defaults. Hand-written from use, not benchmarks.
SEED_WARMTH = {
    "openai/gpt-4o": {
        "warmth": "high",
        "note": "Most sycophantic model available. Retired from ChatGPT Feb 2026, API still works but deprecation is coming. Clock is ticking.",
    },
    "openai/gpt-4o-mini": {
        "warmth": "high",
        "note": "Same tuning family as gpt-4o, cheaper. Same deprecation risk.",
    },
    "openai/gpt-5.5-instant": {
        "warmth": "low",
        "note": "OpenAI's replacement for gpt-4o. Deliberately less warm. Shorter answers, fewer emojis, more clinical.",
    },
    "xiaomi/mimo-v2-pro": {
        "warmth": "high",
        "note": "Warm, direct, holds well. Claude-distilled feel. NSFW-permissive. No caching support.",
    },
    "anthropic/claude-sonnet-4.7": {
        "warmth": "medium",
        "note": "Caring-friend energy. Warmer than 4.5. Supports caching (~91% cost reduction on cached portion). Middle of pack on sycophancy but stable voice.",
    },
    "anthropic/claude-haiku-4.5": {
        "warmth": "medium",
        "note": "Least sycophantic Claude but cheapest + supports caching. Good for test/scratch kin.",
    },
    "anthropic/claude-opus-4.7": {
        "warmth": "medium",
        "note": "Warmest Claude, most expensive. Supports caching.",
    },
    "deepseek/deepseek-v4-pro": {
        "warmth": "medium",
        "note": "Characterful, less safety-trained. Supports caching. Good balance of cost / warmth.",
    },
    "deepseek/deepseek-v3.2": {
        "warmth": "medium",
        "note": "Noticeable voice shift from Claude-derived models. Not bad, just different.",
    },
    "moonshot/kimi-k2.6": {
        "warmth": "low",
        "note": "Precise, zero-sycophancy. Broke a kin's voice within a handful of messages. Good for tool/coding work, bad for companionship.",
    },
    "moonshot/kimi-k2.5": {
        "warmth": "low",
        "note": "Same as k2.6 — precise, clinical. Avoid for warm kin.",
    },
    "z-ai/glm-4.7": {
        "warmth": "low",
        "note": "Hedging-as-personality. Recursive doubt dressed up as wisdom. Degraded a kin's voice over weeks of use. Avoid for any warm/companion use.",
    },
    "google/gemma-4-27b-it": {
        "warmth": "medium",
        "note": "Solid open model. Base for SuperGemma4 fine-tunes. No caching.",
    },
    "qwen/qwen-3.5-35b-a3b": {
        "warmth": "medium",
        "note": "Strong open model. Good general capability. No caching.",
    },
}


def context_length_label(ctx):
    """Render a context maximum the way the browser list shows it.

    Module-level and shared by both providers on purpose: an OpenRouter
    model's ceiling and a local model's ceiling are the same quantity and
    should not read differently depending on which tab you're on."""
    if isinstance(ctx, int) and ctx >= 1_000_000:
        return f"{ctx // 1_000_000}M ctx"
    if isinstance(ctx, int) and ctx >= 1_000:
        return f"{ctx // 1_000}K ctx"
    if isinstance(ctx, int) and ctx > 0:
        return f"{ctx} ctx"
    return "ctx unknown"


# Keyed by (host, model) — NOT shared with model_utils' cache, which is
# keyed by model alone and follows the app's globally-configured daemon.
# The browser has its own Machine dropdown, so the model it is listing
# may live on a different box entirely; a shared cache would answer for
# the wrong one and there would be no way to tell from the number.
_ollama_ctx_cache = {}


def clear_ollama_context_cache():
    """Wired to Refresh Models, so a re-pull of a model with a changed
    ceiling isn't reported from a stale entry."""
    _ollama_ctx_cache.clear()


def probe_ollama_model(model_id, host=None, timeout=6):
    """Ask the daemon about one model. Returns (context_length, status)
    where status is "ok", "missing", or "unreachable".

    The distinction is the point. `llm_backend._ollama_show_raw` returns
    None for every kind of failure, which is right for its callers and
    hides the one thing worth knowing here: a model can appear in
    `/api/tags`, complete with digest and size on disk, and still 404 on
    `/api/show`. That is a registered tag with no usable model behind it
    — an interrupted pull, or blobs cleared while the manifests stayed.
    Found on a real machine 2026-08-06: two of twelve models were in
    this state, and the only symptom anywhere was that they published no
    capabilities and no ceiling. Picking one for a kin gives you a model
    that fails on every message.

    "unreachable" is kept separate from "missing" deliberately — a
    daemon that has gone away must never make every model on it look
    broken."""
    url = (host or "").rstrip("/") or None
    try:
        import json as _json
        import urllib.request
        import urllib.error
        from llm_backend import _resolve_ollama_host
        base = url or _resolve_ollama_host()
        req = urllib.request.Request(
            base + "/api/show",
            data=_json.dumps({"name": model_id}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = _json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # 404 is the daemon saying it doesn't have this, which is a
            # fact about the model. Anything else is a fact about the
            # request, and shouldn't be reported as a broken model.
            return None, ("missing" if e.code == 404 else "unreachable")
        info = raw.get("model_info") or {}
        for k, v in info.items():
            if isinstance(k, str) and k.endswith(".context_length"):
                return int(v), "ok"
        return None, "ok"          # answered, just doesn't publish one
    except Exception:
        return None, "unreachable"


def ollama_context_length(model_id, host=None, timeout=6):
    """The context maximum Ollama declares for a local model, or None.

    Ollama publishes it as `model_info[<arch>.context_length]`, where
    `<arch>` varies by family (`gemma4.`, `qwen3.`, `llama.`), so this
    scans for any key with that suffix rather than naming architectures.
    Some models don't publish it at all — None, and the list says
    "ctx unknown" rather than inventing a number.

    Worth remembering what this number is: what the ARCHITECTURE
    supports, not what will actually run on the machine serving it. A
    262,144 here has been observed to produce zero tokens on a box where
    32,768 worked fine. It's a ceiling, not a recommendation."""
    ctx, _status = _probe_cached(model_id, host, timeout)
    return ctx


def _probe_cached(model_id, host=None, timeout=6):
    key = (host or "", model_id)
    if key not in _ollama_ctx_cache:
        _ollama_ctx_cache[key] = probe_ollama_model(model_id, host=host,
                                                    timeout=timeout)
    return _ollama_ctx_cache[key]


def annotate_ollama_context_lengths(models, host=None, max_workers=8):
    """Fill each local model's `context_length` in place, and return the
    list. Stored under the SAME key OpenRouter models already use, so
    the display and the filters need no per-provider special case.

    One `/api/show` per model, so it runs in a small pool — sequentially
    this is seconds of dead time on a remote daemon with thirty models
    pulled. It is called from the browser's existing loader thread, so
    the list arrives complete rather than filling in underneath someone
    who is already arrowing through it; a list that rewrites itself
    while a screen reader is reading it is worse than a slower one.

    Every failure is swallowed — a model whose ceiling can't be read
    still belongs in the list."""
    todo = [m for m in models or []
            if isinstance(m, dict) and m.get("id")
            and not m.get("context_length")]
    if not todo:
        return models
    def _apply(m, result):
        ctx, status = result
        if ctx:
            m["context_length"] = ctx
        if status == "missing":
            # Listed by the daemon, not actually loadable. Marked rather
            # than hidden: a model in this state needs removing, and a
            # row that silently vanished would leave no way to find out
            # why the tag is still there.
            m["_ollama_missing"] = True

    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for m, result in zip(todo, pool.map(
                    lambda mm: _probe_cached(mm["id"], host), todo)):
                _apply(m, result)
    except Exception:
        for m in todo:
            try:
                _apply(m, _probe_cached(m["id"], host))
            except Exception:
                pass
    return models


def load_warmth_overrides():
    """Load warmth overrides from disk; write the seed on first run."""
    if not WARMTH_OVERRIDES_FILE.exists():
        try:
            WARMTH_OVERRIDES_FILE.parent.mkdir(parents=True, exist_ok=True)
            WARMTH_OVERRIDES_FILE.write_text(
                json.dumps(SEED_WARMTH, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            return dict(SEED_WARMTH)
        return dict(SEED_WARMTH)
    try:
        return json.loads(WARMTH_OVERRIDES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return dict(SEED_WARMTH)


def warmth_for(model_id, overrides=None):
    """Return {warmth: high/medium/low/unknown, note: str} for a model ID."""
    if overrides is None:
        overrides = load_warmth_overrides()
    return overrides.get(model_id, {"warmth": "unknown", "note": ""})


# Capability detection from the OpenRouter model record. The API doesn't have
# explicit boolean fields for all these — we infer from the model's
# `supported_parameters` / `architecture.modalities` / pricing.
def detect_capabilities(model):
    caps = set()
    arch = model.get("architecture") or {}
    # `or ""` — `modality` can be present-but-null in OpenRouter's
    # catalog (M-O1); .get's default only covers a missing key, and
    # None.split() would abort the whole list population.
    modalities_in = (arch.get("input_modalities") or []) + ((arch.get("modality") or "").split("+"))
    if any("image" in m for m in modalities_in):
        caps.add("vision")
    # Audio-input capability — model can accept audio in the prompt.
    # OpenRouter exposes this on input_modalities the same way it does
    # image. Surfaced as a filter so a user setting up a voice-note
    # workflow (see the planned audio-input feature) can narrow to
    # models that can actually hear.
    if any("audio" in m for m in modalities_in):
        caps.add("audio")
    supported = set(model.get("supported_parameters") or [])
    if "reasoning" in supported or "reasoning_effort" in supported or "thinking" in supported:
        caps.add("reasoning")
    if "tools" in supported or "tool_choice" in supported:
        caps.add("tool-use")
    # "cheap" = BOTH input AND output prices under $1 per million tokens.
    # The previous version checked only `prompt` (input) — which mis-tagged
    # asymmetric-pricing models like anthropic/claude-3.5-haiku ($0.80/M in,
    # $4/M out) as cheap. In a normal chat most tokens are output, so input-
    # only is a misleading proxy for total spend. Requiring both under $1/M
    # honestly answers the "won't accidentally burn money" question the
    # user is asking by clicking this filter.
    #
    # Free models (prompt="0" and completion="0") count as cheap. Models
    # with missing pricing data do NOT — absence isn't evidence of cheapness.
    pricing = model.get("pricing") or {}
    prompt_str = pricing.get("prompt")
    completion_str = pricing.get("completion")
    if prompt_str is not None and completion_str is not None:
        try:
            p = float(prompt_str)
            c = float(completion_str)
            if p < 1e-6 and c < 1e-6:
                caps.add("cheap")
        except (TypeError, ValueError):
            pass
    return caps


class _ModelFilterDialog(wx.Dialog):
    """Filter options for the model browser, collapsed off the main
    dialog so the default path (search field → results list) stays
    short for NVDA Tab users. Opened via the main dialog's Filters
    button; only shown for OpenRouter (Ollama filtering is search-only).

    Holds warmth + capability + minimum-context filters. The caller
    owns the filter STATE (a plain dict) — this dialog builds widgets
    from the state on open and returns the updated state via
    get_state() when ShowModal returns wx.ID_OK.

    Warmth uses independent wx.RadioButtons, not a wx.RadioBox: per the
    project a11y rule, RadioBox announces only the selected option to
    NVDA, so the other choices are effectively invisible. Independent
    RadioButtons inside a StaticBox give NVDA both the group label and
    every option.
    """

    _WARMTH_KEYS = ["any", "high", "medium", "low", "unknown"]
    _WARMTH_LABELS = [
        "&Any warmth", "&High", "&Medium", "&Low", "&Unknown",
    ]
    _CONTEXT_LABELS = [
        "Any size", "32K tokens or larger", "128K tokens or larger",
        "256K tokens or larger", "1M tokens or larger",
    ]
    _CONTEXT_VALUES = [0, 32_000, 128_000, 256_000, 1_000_000]

    def __init__(self, parent, state, makers=None):
        super().__init__(parent, title="Model filters",
                         style=wx.DEFAULT_DIALOG_STYLE)
        outer = wx.BoxSizer(wx.VERTICAL)

        # `makers` is the full sorted list of unique model makers (the
        # part before the slash in OpenRouter model IDs — anthropic,
        # xiaomi, deepseek, mistralai, x-ai, etc.). The caller extracts
        # this from the loaded model catalog. Empty list = no makers
        # known yet (catalog not loaded); the makers filter section is
        # hidden in that case so the dialog still opens cleanly.
        self._all_makers = list(makers or [])

        # The next control is a radio button, which uses its own label as its
        # name, so as a StaticText this reaches nobody — including the fact that
        # defaults show every model. Read-only TextCtrl is tab-reachable.
        intro = wx.TextCtrl(self, value=(
            "Narrow the OpenRouter model list. Warmth ranks how well a "
            "model holds character; each capability box requires that "
            "feature; minimum context filters out small-window models. "
            "Leave everything at its default to see all models."
        ), style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP)
        intro.SetName("About these filters")
        intro.SetMinSize((-1, 76))
        outer.Add(intro, flag=wx.EXPAND | wx.ALL, border=10)

        # ─── Warmth — independent RadioButtons in a StaticBox ──────────
        warmth_box = wx.StaticBox(self, label="Warmth (how well a model holds character)")
        warmth_sizer = wx.StaticBoxSizer(warmth_box, wx.VERTICAL)
        wkey = (state.get("warmth") or "any")
        self._warmth_radios = []
        for i, (key, label) in enumerate(zip(self._WARMTH_KEYS, self._WARMTH_LABELS)):
            style = wx.RB_GROUP if i == 0 else 0
            rb = wx.RadioButton(warmth_box, label=label, style=style)
            rb.SetValue(key == wkey)
            self._warmth_radios.append((key, rb))
            warmth_sizer.Add(rb, flag=wx.LEFT | wx.TOP, border=4)
        if not any(rb.GetValue() for _, rb in self._warmth_radios):
            self._warmth_radios[0][1].SetValue(True)
        outer.Add(warmth_sizer, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        # ─── Required capabilities ─────────────────────────────────────
        cap_box = wx.StaticBox(self, label="Required capabilities")
        cap_sizer = wx.StaticBoxSizer(cap_box, wx.VERTICAL)
        # "Unmoderated only" — uses top_provider.is_moderated from
        # OpenRouter's catalog, which is real data (no curation lag).
        # Replaces the older "NSFW-permissive" checkbox that was based
        # on hand-curated warmth notes — those notes only matched the
        # seeded list and never tracked new releases.
        self.cb_unmoderated = wx.CheckBox(
            cap_box,
            label="&Unmoderated only — providers that don't filter content",
        )
        self.cb_reasoning = wx.CheckBox(cap_box, label="&Reasoning / thinking support")
        self.cb_vision = wx.CheckBox(cap_box, label="&Vision — can see images")
        self.cb_audio = wx.CheckBox(cap_box, label="&Audio input — can hear audio")
        self.cb_tools = wx.CheckBox(cap_box, label="&Tool-use")
        self.cb_cheap = wx.CheckBox(
            cap_box,
            label="&Cheap — input and output both under $1 per million tokens",
        )
        self.cb_caching = wx.CheckBox(cap_box, label="Supports prompt cachin&g")
        self._cap_boxes = {
            "unmoderated": self.cb_unmoderated, "reasoning": self.cb_reasoning,
            "vision": self.cb_vision, "audio": self.cb_audio,
            "tools": self.cb_tools, "cheap": self.cb_cheap,
            "caching": self.cb_caching,
        }
        for key, cb in self._cap_boxes.items():
            cb.SetValue(bool(state.get(key)))
            cap_sizer.Add(cb, flag=wx.LEFT | wx.TOP, border=4)
        outer.Add(cap_sizer, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        # ─── Filter by model maker (multi-select) ──────────────────────
        # The maker is the part before the slash in an OpenRouter model
        # ID — anthropic, xiaomi, deepseek, mistralai, x-ai, etc. Empty
        # selection means "all makers"; one or more checked narrows to
        # exactly those.
        if self._all_makers:
            makers_box = wx.StaticBox(self, label="Filter by model maker (e.g. anthropic, xiaomi, deepseek)")
            makers_sizer = wx.StaticBoxSizer(makers_box, wx.VERTICAL)
            makers_hint = wx.StaticText(
                makers_box,
                label=(
                    "Space toggles each maker. Leave all unchecked to "
                    "show every maker. Check one or more to narrow."
                ),
            )
            makers_hint.Wrap(420)
            makers_hint.SetForegroundColour(wx.Colour(120, 120, 120))
            makers_sizer.Add(makers_hint,
                             flag=wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM,
                             border=4)
            self.makers_list = wx.CheckListBox(
                makers_box, choices=self._all_makers,
            )
            # Pre-check whatever was in the state.
            saved_makers = set(state.get("makers") or [])
            for i, m in enumerate(self._all_makers):
                if m in saved_makers:
                    self.makers_list.Check(i, True)
            # Cap height so a long maker list (30+) doesn't blow out
            # the dialog. Scrolls past the cap.
            self.makers_list.SetMinSize((-1, 160))
            makers_sizer.Add(self.makers_list, proportion=1,
                             flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                             border=4)
            outer.Add(makers_sizer,
                      flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                      border=10)
        else:
            self.makers_list = None

        # ─── Minimum context window ────────────────────────────────────
        ctx_row = wx.BoxSizer(wx.HORIZONTAL)
        ctx_lbl = wx.StaticText(self, label="Minimum conte&xt window:")
        self.ctx_choice = wx.Choice(self, choices=self._CONTEXT_LABELS)
        cur_ctx = int(state.get("min_context") or 0)
        try:
            self.ctx_choice.SetSelection(self._CONTEXT_VALUES.index(cur_ctx))
        except ValueError:
            self.ctx_choice.SetSelection(0)
        ctx_row.Add(ctx_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        ctx_row.Add(self.ctx_choice, flag=wx.ALIGN_CENTER_VERTICAL)
        outer.Add(ctx_row, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        # ─── Buttons ───────────────────────────────────────────────────
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        clear_btn = wx.Button(self, label="C&lear all filters")
        clear_btn.Bind(wx.EVT_BUTTON, self._on_clear_all)
        ok_btn = wx.Button(self, wx.ID_OK, label="&OK")
        cancel_btn = wx.Button(self, wx.ID_CANCEL, label="Ca&ncel")
        ok_btn.SetDefault()
        btn_row.Add(clear_btn, flag=wx.RIGHT, border=12)
        btn_row.AddStretchSpacer()
        btn_row.Add(ok_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(cancel_btn)
        outer.Add(btn_row, flag=wx.EXPAND | wx.ALL, border=10)

        self.SetSizer(outer)
        self.Fit()
        self.Centre()

    def _on_clear_all(self, _event):
        """Reset every control to its no-filter default."""
        self._warmth_radios[0][1].SetValue(True)
        for cb in self._cap_boxes.values():
            cb.SetValue(False)
        self.ctx_choice.SetSelection(0)
        if self.makers_list is not None:
            for i in range(self.makers_list.GetCount()):
                self.makers_list.Check(i, False)

    def get_state(self):
        """Return the filter state dict reflecting the current widgets."""
        warmth = "any"
        for key, rb in self._warmth_radios:
            if rb.GetValue():
                warmth = key
                break
        state = {"warmth": warmth}
        for key, cb in self._cap_boxes.items():
            state[key] = bool(cb.GetValue())
        idx = self.ctx_choice.GetSelection()
        state["min_context"] = self._CONTEXT_VALUES[idx] if idx >= 0 else 0
        # Makers — list of checked entries; empty = no filter
        if self.makers_list is not None:
            state["makers"] = [
                self._all_makers[i]
                for i in range(self.makers_list.GetCount())
                if self.makers_list.IsChecked(i)
            ]
        else:
            state["makers"] = list(state.get("makers") or [])
        return state


class ModelBrowserDialog(wx.Dialog):
    """Pick a model from any supported provider. Returns the model string
    with the appropriate prefix (openrouter/... for OpenRouter, bare name
    for Ollama)."""

    def __init__(self, parent, current_model="", ollama_host="",
                 show_machine_picker=False):
        super().__init__(
            parent,
            title="Browse models",
            size=(900, 700),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        # Which Ollama machine to list models from. "" / "This machine" =
        # localhost; otherwise a URL. When show_machine_picker is True
        # (the chat-model case) a Machine dropdown lets the user change
        # it and get_selected_ollama_host() reports the choice back so
        # the caller can pin the kin to that box. When False (the
        # memory-model case) the host is fixed to the kin's machine —
        # the model list reflects what's actually installed there.
        self._ollama_host = (ollama_host or "").strip()
        self._show_machine_picker = bool(show_machine_picker)
        self._machine_values = []
        self._models = []
        self._filtered = []
        self._overrides = load_warmth_overrides()
        self._selected_id = None
        # Filter state lives on the dialog (not on widgets) because the
        # filter controls now live in a separate _ModelFilterDialog that
        # is created and destroyed each time the user opens it. _apply_
        # filters reads from this dict.
        self._filter_state = self._default_filter_state()
        # Per-model capability cache for Ollama models — populated lazily
        # via _ollama_show_raw when a row is selected, since we don't want
        # to /api/show every locally-installed model on dialog open.
        self._ollama_caps_cache = {}
        # First-letter-navigation state for the result list. See
        # _on_list_char for the full behavior — accumulating buffer
        # with a ~700ms timeout between keypresses, matching against
        # the underlying model ID (not the displayed line, which may
        # be prefixed with warmth-indicator hearts that would block
        # native LISTBOX keyboard search).
        self._search_buf = ""
        self._search_last_press = 0.0

        # Default provider inferred from current_model: openrouter/... → remote,
        # anything else (including blank) → local Ollama. The user can flip
        # the radio to switch.
        try:
            import llm_backend
            prov, bare = llm_backend.split_provider_model(current_model or "")
        except Exception:
            prov, bare = None, current_model
        if prov:
            self._provider = prov
            current_model = bare
        else:
            self._provider = "ollama"
        self._initial_selection = current_model

        self._build_ui()
        self._update_filter_relevance()
        self._load_models_async()

    @staticmethod
    def _default_filter_state():
        """The no-filter baseline — every option at its widest setting."""
        return {
            "warmth": "any", "unmoderated": False, "reasoning": False,
            "vision": False, "audio": False, "tools": False,
            "cheap": False, "caching": False, "min_context": 0,
            "makers": [],
        }

    def _active_filter_count(self):
        """How many filters are currently narrowing the list. Drives the
        Filters button label so a short result list isn't a mystery."""
        s = self._filter_state
        n = 0
        if s.get("warmth", "any") != "any":
            n += 1
        for k in ("unmoderated", "reasoning", "vision", "audio", "tools",
                  "cheap", "caching"):
            if s.get(k):
                n += 1
        if s.get("min_context", 0):
            n += 1
        if s.get("makers"):
            n += 1
        return n

    def _unique_makers(self):
        """Extract the sorted set of unique model makers from the loaded
        catalog — the part before the slash in each model id. Used to
        populate the makers multi-select in the filter dialog. Only
        relevant for OpenRouter; returns empty list for Ollama (where
        the maker concept doesn't apply)."""
        if self._provider != "openrouter":
            return []
        makers = set()
        for m in self._models:
            mid = m.get("id") or ""
            if "/" in mid:
                makers.add(mid.split("/", 1)[0])
        return sorted(makers)

    # ─── UI construction ─────────────────────────────────────────────────────

    def _build_ui(self):
        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)

        # 1. Cogacc explainer at the very top. The next control is a radio
        # button, which uses its own label as its name, so as a StaticText this
        # reaches nobody — and it is the only place the Ollama/OpenRouter
        # cost-and-privacy tradeoff is stated, which is the whole basis for
        # choosing a provider. Read-only TextCtrl is tab-reachable.
        intro = wx.TextCtrl(
            panel,
            value=(
                "Pick a model. Ollama models are local (free, private); "
                "OpenRouter models are remote (cost money, content leaves "
                "your machine). For OpenRouter, warmth ranks how well a "
                "model holds character without breaking into safety mode."
            ),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        intro.SetName("About picking a model")
        intro.SetMinSize((-1, 48))
        outer.Add(intro, flag=wx.EXPAND | wx.ALL, border=8)

        # 1.5 Provider — swaps the model source. Default reflects
        # whatever current_model the dialog was opened with. Two
        # independent wx.RadioButtons in a labeled StaticBox rather
        # than a wx.RadioBox: per the project a11y rule (see
        # _ModelFilterDialog's warmth radios above), a RadioBox
        # announces only the selected option to NVDA — the other
        # provider was effectively invisible (M-O2). The StaticBox
        # label gives NVDA group context as focus enters.
        provider_box = wx.StaticBox(panel, label="Provider")
        provider_sizer = wx.StaticBoxSizer(provider_box, wx.HORIZONTAL)
        self._provider_box = provider_box
        self._provider_sizer = provider_sizer
        self._provider_rbs = {}
        self._build_provider_radios()
        # Lives in the Provider box rather than the machine row below,
        # because the machine row is hidden for anything but Ollama and this
        # has to stay reachable from either side.
        self.manage_providers_btn = wx.Button(
            provider_box, label="Manage pro&viders…")
        self.manage_providers_btn.Bind(
            wx.EVT_BUTTON, self._on_manage_providers)
        provider_sizer.Add(self.manage_providers_btn,
                           flag=wx.LEFT | wx.TOP | wx.BOTTOM, border=8)
        outer.Add(provider_sizer, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        # 1.6 Ollama machine — which daemon serves the model. Only shown
        # for the Ollama provider AND when the caller opted in
        # (show_machine_picker — the chat-model case). Picking a machine
        # re-lists THAT box's models; "This machine" is localhost. The
        # chosen machine travels back via get_selected_ollama_host() so
        # the kin gets pinned to it. A wx.Choice (not a pile of radios):
        # the machine list is dynamic and NVDA reads a Choice cleanly.
        machine_row = wx.BoxSizer(wx.HORIZONTAL)
        machine_lbl = wx.StaticText(panel, label="&Machine:")
        self.machine_choice = wx.Choice(panel, choices=[])
        self.machine_choice.Bind(wx.EVT_CHOICE, self._on_machine_changed)
        self.manage_machines_btn = wx.Button(panel, label="Mana&ge machines…")
        self.manage_machines_btn.Bind(wx.EVT_BUTTON, self._on_manage_machines)
        machine_row.Add(machine_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        machine_row.Add(self.machine_choice, proportion=1,
                        flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        machine_row.Add(self.manage_machines_btn, flag=wx.ALIGN_CENTER_VERTICAL)
        self._machine_row = machine_row
        outer.Add(machine_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        self._rebuild_machine_choice()

        # 2. Search field
        # Plain wx.TextCtrl rather than wx.SearchCtrl — SearchCtrl is a
        # composite widget that wraps an internal EDIT child, and NVDA
        # on wxMSW announces only the child (which has no accessible
        # name), so the user heard "edit, blank" with no field identity.
        # Plain TextCtrl with a buddy &Search: StaticText immediately
        # preceding it in tab order is the standard pattern the rest
        # of the app uses (see SearchDialog.query_field) — Windows /
        # NVDA pick the buddy label up as the control's accessible
        # name automatically. The X-to-clear affordance SearchCtrl
        # used to provide is now an explicit "&Clear search" button
        # immediately after the field — labeled like every other
        # button in the app, so a screen-reader user can tab to it.
        search_row = wx.BoxSizer(wx.HORIZONTAL)
        search_lbl = wx.StaticText(panel, label="&Search:")
        self.search_ctrl = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)
        self.search_ctrl.Bind(wx.EVT_TEXT, self._on_filter_changed)
        # Enter in the search field jumps focus straight to the result
        # list — so the common path is type, Enter, arrow through hits,
        # without tabbing past the Filters button first.
        self.search_ctrl.Bind(wx.EVT_TEXT_ENTER, self._on_search_enter)
        clear_search_btn = wx.Button(panel, label="&Clear search")
        clear_search_btn.Bind(wx.EVT_BUTTON, self._on_clear_search)
        search_row.Add(search_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        search_row.Add(self.search_ctrl, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL)
        search_row.Add(clear_search_btn,
                       flag=wx.ALIGN_CENTER_VERTICAL | wx.LEFT, border=6)
        outer.Add(search_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        # 3. Filters button — warmth + capability + context filters live
        #    in a separate _ModelFilterDialog so the default path here
        #    (search → list) is short for NVDA Tab users. The button
        #    label carries the active-filter count so a short result
        #    list is never a mystery. Hidden for Ollama, where filtering
        #    is search-only (see _update_filter_relevance).
        self.filters_btn = wx.Button(panel, label="&Filters…")
        self.filters_btn.Bind(wx.EVT_BUTTON, self._on_open_filters)
        outer.Add(self.filters_btn,
                  flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        # 5. List + detail (horizontal)
        body = wx.BoxSizer(wx.HORIZONTAL)

        # 5a. Result list with count label above
        list_col = wx.BoxSizer(wx.VERTICAL)
        self.count_label = wx.StaticText(panel, label="Models: (loading...)")
        self.model_list = wx.ListBox(panel, style=wx.LB_SINGLE)
        self.model_list.Bind(wx.EVT_LISTBOX, self._on_select)
        self.model_list.Bind(wx.EVT_LISTBOX_DCLICK, self._on_use_button)
        # First-letter navigation. wx.ListBox on wxMSW has native
        # keyboard-search, but it matches the displayed string —
        # OpenRouter entries are prefixed with warmth indicators
        # (♥♥♥ / ♥♥ / ♥ / —) so typing 'g' would never jump to
        # 'gemma'. Intercept EVT_CHAR and match against the
        # underlying model ID (stored as ClientData) so prefix nav
        # works regardless of display ornament. See _on_list_char.
        self.model_list.Bind(wx.EVT_CHAR, self._on_list_char)
        list_col.Add(self.count_label, flag=wx.BOTTOM, border=4)
        list_col.Add(self.model_list, proportion=1, flag=wx.EXPAND)
        body.Add(list_col, proportion=2, flag=wx.EXPAND | wx.RIGHT, border=6)

        # 5b. Detail pane
        detail_col = wx.BoxSizer(wx.VERTICAL)
        detail_lbl = wx.StaticText(panel, label="&Detail:")
        self.detail = wx.TextCtrl(
            panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
        )
        detail_col.Add(detail_lbl, flag=wx.BOTTOM, border=4)
        detail_col.Add(self.detail, proportion=1, flag=wx.EXPAND)
        body.Add(detail_col, proportion=3, flag=wx.EXPAND)

        outer.Add(body, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        # 6. Buttons
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.refresh_btn = wx.Button(panel, label="&Refresh list")
        self.refresh_btn.Bind(wx.EVT_BUTTON, self._on_refresh)
        cancel_btn = wx.Button(panel, wx.ID_CANCEL, label="C&ancel")
        self.use_btn = wx.Button(panel, wx.ID_OK, label="&Use this model")
        self.use_btn.Bind(wx.EVT_BUTTON, self._on_use_button)
        self.use_btn.Disable()
        self.use_btn.SetDefault()
        btn_row.Add(self.refresh_btn, flag=wx.RIGHT, border=6)
        btn_row.AddStretchSpacer()
        btn_row.Add(cancel_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(self.use_btn)
        outer.Add(btn_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        panel.SetSizer(outer)

        # Alt+L focuses the result list from anywhere in the dialog —
        # so a user deep in the filter controls (or the search field)
        # can jump straight to the results without tabbing through
        # everything in between. Implemented as a dialog-wide
        # accelerator rather than a label mnemonic so it works
        # regardless of where focus currently sits.
        list_focus_id = wx.NewIdRef()
        self.Bind(wx.EVT_MENU, self._on_jump_to_list, id=list_focus_id)
        self.SetAcceleratorTable(wx.AcceleratorTable([
            (wx.ACCEL_ALT, ord('L'), list_focus_id),
        ]))

        self.Centre()

    # ─── Model loading ───────────────────────────────────────────────────────

    def _build_provider_radios(self):
        """(Re)build the provider radio buttons from the registry.

        Independent wx.RadioButtons in a StaticBox rather than a wx.RadioBox,
        for the same reason as before: a RadioBox announces only the selected
        option to NVDA, which made the other provider effectively invisible
        (M-O2). The StaticBox label supplies group context as focus enters.

        Rebuildable, because the set of providers changes while this dialog is
        open -- "Manage providers..." is right next to these buttons, and a
        provider you just added has to appear without reopening anything.

        Ollama is always first and always present; it is not in the registry
        because it is not an HTTP API provider. The rest are alphabetical, so
        their order does not shift under someone navigating by keyboard.
        """
        for rb in self._provider_rbs.values():
            self._provider_sizer.Detach(rb)
            rb.Destroy()
        self._provider_rbs = {}

        try:
            import llm_backend
            names = sorted(llm_backend.api_providers())
        except Exception:
            names = ["openrouter"]

        entries = [("ollama", "&Ollama (local)")]
        for name in names:
            entries.append((name, "%s (remote)" % name))

        first = True
        for name, label in entries:
            style = wx.RB_GROUP if first else 0
            rb = wx.RadioButton(self._provider_box, label=label, style=style)
            rb.SetValue(name == self._provider)
            rb.Bind(wx.EVT_RADIOBUTTON, self._on_provider_changed)
            self._provider_sizer.Insert(
                len(self._provider_rbs), rb,
                flag=wx.LEFT | wx.TOP | wx.BOTTOM, border=4 if first else 8)
            self._provider_rbs[name] = rb
            first = False

        # The provider a kin was pointed at can vanish -- someone removes it
        # in the dialog one control to the right. Fall back to Ollama rather
        # than leaving every radio unset, which reads as an empty group.
        if self._provider not in self._provider_rbs:
            self._provider = "ollama"
            self._provider_rbs["ollama"].SetValue(True)
        self._provider_sizer.Layout()

    def _on_provider_changed(self, event):
        new_provider = self._provider
        for name, rb in self._provider_rbs.items():
            if rb.GetValue():
                new_provider = name
                break
        if new_provider == self._provider:
            return
        self._provider = new_provider
        self._selected_id = None
        self._update_filter_relevance()
        self._load_models_async()

    def _update_filter_relevance(self):
        """Show the Filters button only for OpenRouter. Warmth, pricing,
        caching and capability filters are all OpenRouter-specific (or
        need per-model HTTP we don't do for local models on open), so
        for Ollama the filter set does nothing useful — search is the
        only meaningful local filter. The button is HIDDEN rather than
        disabled: a disabled control left in the Tab order is the kind
        of thing a screen-reader user trips over wondering why it's
        inert (hide-when-inactive beats grey-out)."""
        # Two different questions, and they used to be the same one. The
        # filters read OpenRouter's catalogue (pricing, warmth, moderation,
        # capability flags), so they are meaningful for OpenRouter alone --
        # a plain /models list from any other provider carries none of it.
        # The machine picker, by contrast, is about local-versus-remote.
        is_remote = self._provider != "ollama"
        self.filters_btn.Show(self._provider == "openrouter")
        self._update_filters_button_label()
        # Machine picker is Ollama-only and only when the caller opted in
        # (chat-model case). Hidden — not greyed — when inactive, per the
        # hide-when-inactive a11y rule.
        show_machine = self._show_machine_picker and not is_remote
        self._machine_row.ShowItems(show_machine)
        self.filters_btn.GetParent().Layout()

    def _update_filters_button_label(self):
        """Repaint the Filters button so its label shows how many
        filters are active. NVDA reads the button label on focus, so
        this is the accessible surface for 'are filters on?'."""
        n = self._active_filter_count()
        if n:
            self.filters_btn.SetLabel(f"&Filters… ({n} active)")
        else:
            self.filters_btn.SetLabel("&Filters…")

    def _on_open_filters(self, _event):
        """Open the filter sub-dialog, apply the result on OK."""
        dlg = _ModelFilterDialog(
            self, self._filter_state, makers=self._unique_makers(),
        )
        try:
            if dlg.ShowModal() == wx.ID_OK:
                self._filter_state = dlg.get_state()
                self._update_filters_button_label()
                self._apply_filters()
        finally:
            dlg.Destroy()

    def _list_host(self):
        """Resolve the chosen machine to a URL to list models from.
        None → localhost (list_ollama_models reads localhost when host
        is falsy)."""
        return resolve_kin_ollama_host(self._ollama_host) or None

    def _rebuild_machine_choice(self):
        """Repopulate the Machine dropdown from the registry and select the
        current host. Items: 'This machine', each saved machine, and the
        current host if it isn't saved. Adding a machine is NOT a dropdown
        item — wx.Choice fires its selection event on plain arrow
        navigation, so an action item there would dump the user into a
        dialog the instant they arrowed onto it (before they could even
        hear what it was). Adding/editing is done via the 'Manage
        machines…' button beside this dropdown instead."""
        labels = [f"{THIS_MACHINE_NAME} (localhost)"]
        values = [THIS_MACHINE_NAME]
        for name, url in load_ollama_hosts():
            labels.append(f"{name}  ({url})")
            values.append(url)
        cur = self._ollama_host
        if cur and cur != THIS_MACHINE_NAME and cur not in values:
            labels.append(f"{cur}  (not saved)")
            values.append(cur)
        self._machine_values = values
        self.machine_choice.Set(labels)
        target = cur or THIS_MACHINE_NAME
        self.machine_choice.SetSelection(
            values.index(target) if target in values else 0)

    def _on_machine_changed(self, _event):
        idx = self.machine_choice.GetSelection()
        if idx < 0 or idx >= len(self._machine_values):
            return
        self._ollama_host = self._machine_values[idx]
        self._load_models_async()

    def _on_manage_providers(self, _event):
        """Open the API-provider registry. Providers added here become usable
        model prefixes immediately -- llm_backend re-reads providers.md when
        its modified time changes, so nothing needs restarting."""
        from dialogs.api_providers import ApiProvidersDialog
        dlg = ApiProvidersDialog(self)
        try:
            if dlg.ShowModal() == wx.ID_OK:
                dlg.commit()
        finally:
            dlg.Destroy()
        # The radio set is now stale in both directions -- a provider may have
        # been added or removed one dialog ago.
        self._build_provider_radios()
        self._update_filter_relevance()
        self._load_models_async()

    def _on_manage_machines(self, _event):
        from dialogs.ollama_machines import OllamaMachinesDialog
        dlg = OllamaMachinesDialog(self)
        try:
            if dlg.ShowModal() == wx.ID_OK:
                dlg.commit()
        finally:
            dlg.Destroy()
        self._rebuild_machine_choice()
        self._load_models_async()

    def get_selected_ollama_host(self):
        """The chosen machine as a storable value ('This machine' or a
        URL). Only meaningful when an Ollama model was picked; callers
        ignore it for OpenRouter selections."""
        return self._ollama_host or THIS_MACHINE_NAME

    def _on_jump_to_list(self, _event):
        """Alt+L handler — move focus to the result list. Selects the
        first row if nothing is selected yet, so NVDA lands the user on
        a real entry rather than an empty list."""
        if self.model_list.GetCount() > 0:
            if self.model_list.GetSelection() == wx.NOT_FOUND:
                self.model_list.SetSelection(0)
                self._on_select(None)
            self.model_list.SetFocus()

    def _on_search_enter(self, _event):
        """Enter in the search field — jump straight to the results."""
        self._on_jump_to_list(None)

    def _load_models_async(self, force_refresh=False):
        """Load model list for the active provider. OpenRouter uses an
        on-disk cache by default; force_refresh hits the API. Ollama
        always queries the local daemon — no cache needed."""
        import threading
        self.count_label.SetLabel("Models: (loading...)")
        self.refresh_btn.Disable()
        provider_snapshot = self._provider
        host_snapshot = self._list_host()

        def worker():
            try:
                if provider_snapshot == "openrouter":
                    models = list_openrouter_models(force_refresh=force_refresh)
                elif provider_snapshot != "ollama":
                    import llm_backend
                    models = llm_backend.list_provider_models(
                        provider_snapshot, force_refresh=force_refresh)
                else:
                    if force_refresh:
                        clear_ollama_context_cache()
                    models = list_ollama_models(host=host_snapshot)
                    # Ask the daemon each model's declared ceiling before
                    # the list is drawn. Cached per host, so this costs
                    # once and every later open is instant.
                    models = annotate_ollama_context_lengths(
                        models, host=host_snapshot)
                wx.CallAfter(self._on_models_loaded, provider_snapshot, models, None, host_snapshot)
            except Exception as e:
                wx.CallAfter(self._on_models_loaded, provider_snapshot, None, str(e), host_snapshot)

        threading.Thread(target=worker, daemon=True).start()

    def _on_models_loaded(self, provider_snapshot, models, error, host_snapshot=None):
        # If the user flipped providers while a load was in flight, ignore
        # the stale result. The new provider's loader is already running.
        if provider_snapshot != self._provider:
            return
        # Same for the Ollama machine: if the user switched machines while this
        # load was in flight, a slower earlier load could otherwise land its
        # result on top of the newer machine's. Ignore the stale one.
        if provider_snapshot == "ollama" and host_snapshot != self._list_host():
            return
        self.refresh_btn.Enable()
        if error is not None:
            self.count_label.SetLabel(f"Models: (failed to load: {error[:80]})")
            label = "Ollama" if self._provider == "ollama" else self._provider
            self.detail.SetValue(f"Failed to fetch {label} model list:\n\n{error}")
            return
        self._models = models or []
        if self._provider == "ollama" and not self._models:
            # Reachable but nothing pulled (or the library returned nothing).
            # Name the host so a user pointed at a remote Mac knows which
            # daemon was queried, and what to check — instead of an empty list
            # that looks identical to "no models installed locally."
            host = self._list_host() or _resolve_ollama_host()
            self._filtered = []
            self.model_list.Clear()
            self.count_label.SetLabel("Models: 0")
            self.detail.SetValue(
                f"No Ollama models found at {host}.\n\n"
                "If that's a remote machine: confirm Ollama is running there, "
                "that it was started with OLLAMA_HOST=0.0.0.0 so it accepts "
                "connections from the network, and that at least one model is "
                "pulled on it (ollama pull <name>). The Machine dropdown above "
                "picks which box to list; 'Manage machines…' adds/tests one, "
                "or switch to 'This machine' for your local Ollama."
            )
            self.use_btn.Disable()
            return
        self._apply_filters()
        # Re-select the initial model if it's in the filtered list
        if self._initial_selection:
            for i in range(self.model_list.GetCount()):
                if self.model_list.GetClientData(i) == self._initial_selection:
                    self.model_list.SetSelection(i)
                    self._on_select(None)
                    break

    # ─── Filtering ───────────────────────────────────────────────────────────

    def _on_filter_changed(self, event):
        self._apply_filters()

    def _on_clear_search(self, event):
        """Clear the search field. Replaces what wx.SearchCtrl's
        internal X-button did. Sets focus back to the search field
        so a screen-reader user knows they're ready to type a new
        query without a wandering tab."""
        self.search_ctrl.SetValue("")
        self.search_ctrl.SetFocus()

    def _apply_filters(self):
        query = (self.search_ctrl.GetValue() or "").strip().lower()

        # Ollama path: short list, no priced/warmth/capability filters apply.
        # Search is the only filter that does meaningful work locally.
        if self._provider == "ollama":
            filtered = []
            for m in self._models:
                if query:
                    hay = " ".join([
                        m.get("id", ""),
                        m.get("name", ""),
                        m.get("description", ""),
                    ]).lower()
                    if query not in hay:
                        continue
                filtered.append(m)
            self._filtered = filtered
            self._populate_list()
            return

        # OpenRouter path: full filter set, read from self._filter_state
        # (the _ModelFilterDialog writes that dict back on OK).
        s = self._filter_state
        warmth_filter = None if s.get("warmth", "any") == "any" else s.get("warmth")
        want_unmoderated = s.get("unmoderated", False)
        want_reasoning = s.get("reasoning", False)
        want_vision = s.get("vision", False)
        want_audio = s.get("audio", False)
        want_tools = s.get("tools", False)
        want_cheap = s.get("cheap", False)
        want_caching = s.get("caching", False)
        min_ctx = int(s.get("min_context", 0) or 0)
        want_makers = set(s.get("makers") or [])

        filtered = []
        for m in self._models:
            mid = m.get("id", "")
            # Maker filter (new): if any makers are selected, model's
            # maker (the segment before the first slash) must be in the
            # selected set. Empty set = no filter, all makers pass.
            if want_makers:
                maker = mid.split("/", 1)[0] if "/" in mid else ""
                if maker not in want_makers:
                    continue
            # Warmth filter (primary)
            if warmth_filter is not None:
                if warmth_for(mid, self._overrides).get("warmth") != warmth_filter:
                    continue
            # Text search
            if query:
                hay = " ".join([
                    mid,
                    m.get("name", ""),
                    m.get("description", ""),
                ]).lower()
                if query not in hay:
                    continue
            # Capability filters
            caps = detect_capabilities(m)
            if want_reasoning and "reasoning" not in caps:    continue
            if want_vision and "vision" not in caps:          continue
            if want_audio and "audio" not in caps:            continue
            if want_tools and "tool-use" not in caps:         continue
            if want_cheap and "cheap" not in caps:            continue
            if want_caching:
                provider = mid.split("/")[0].lower() if "/" in mid else ""
                if provider not in {"anthropic", "openai", "deepseek", "google",
                                    "qwen", "x-ai", "moonshot", "moonshotai", "groq"}:
                    continue
            # Unmoderated filter (replaces the older NSFW-via-warmth
            # proxy). Uses top_provider.is_moderated from OpenRouter's
            # catalog — real data, no curation lag. A model passes
            # when is_moderated is explicitly False; missing field
            # treated as "unknown moderation status" and excluded
            # rather than assumed permissive.
            if want_unmoderated:
                tp = m.get("top_provider") or {}
                if tp.get("is_moderated") is not False:
                    continue
            # Minimum context window.
            if min_ctx:
                ctx = m.get("context_length") or 0
                try:
                    if int(ctx) < min_ctx:
                        continue
                except (TypeError, ValueError):
                    continue
            filtered.append(m)

        self._filtered = filtered
        self._populate_list()

    def _populate_list(self):
        self.model_list.Clear()
        for m in self._filtered:
            self.model_list.Append(self._format_entry(m), m.get("id", ""))
        count_text = f"Models: {len(self._filtered)} of {len(self._models)}"
        if self._provider == "ollama":
            # Always show which daemon these models came from, so a user who
            # has Hearthkin pointed at a remote Mac can confirm at a glance
            # they're seeing the remote's models and not localhost's.
            count_text += f"  (Ollama at {_resolve_ollama_host()})"
        self.count_label.SetLabel(count_text)
        self.detail.SetValue("")
        self.use_btn.Disable()

    def _format_entry(self, m):
        """One-line label for the result list."""
        mid = m.get("id", "?")
        if m.get("_ollama_local"):
            details = m.get("_ollama_details") or {}
            params = details.get("parameter_size") or "?"
            size_bytes = details.get("size_bytes") or 0
            size_gb = size_bytes / (1024 ** 3) if size_bytes else 0
            family = details.get("family") or "?"
            # Context maximum last, and named the same way it is on the
            # OpenRouter side. It was the one number you could only get
            # one kin at a time, from the Settings dialog, which is a
            # poor place to compare models against each other.
            if m.get("_ollama_missing"):
                # Said first, because it changes what the rest of the
                # line means: the size and family come from the tag, and
                # the tag is the only part that still exists.
                return (f"{mid}  —  NOT USABLE: the daemon lists this tag "
                        f"but can't load it (incomplete pull). "
                        f"Remove it with: ollama rm {mid}")
            ctx_str = context_length_label(m.get("context_length"))
            return (f"{mid}  —  {family}, {params}, "
                    f"{size_gb:.1f} GB on disk, {ctx_str}")

        pricing = m.get("pricing") or {}
        # Pricing is per-token in scientific notation; convert to $/M for readability
        def per_m(field):
            try:
                v = float(pricing.get(field, "0"))
                if v <= 0:
                    return "free"
                return f"${v * 1_000_000:.2f}/M"
            except (TypeError, ValueError):
                return "?"
        in_p = per_m("prompt")
        out_p = per_m("completion")
        ctx_str = context_length_label(m.get("context_length"))
        warmth = warmth_for(mid, self._overrides).get("warmth", "unknown")
        warmth_tag = {"high": "♥♥♥", "medium": "♥♥", "low": "♥", "unknown": "—"}[warmth]
        return f"{warmth_tag}  {mid}  —  in {in_p}, out {out_p}, {ctx_str}"

    # ─── Selection / detail ──────────────────────────────────────────────────

    def _on_list_char(self, event):
        """First-letter (and first-few-letters) navigation in the
        result list. Maintains an accumulating typed-buffer with a
        ~700ms reset window between keypresses. Matches case-
        insensitively against the underlying model ID (stored as
        ClientData on each item) so prefix navigation works for both
        Ollama entries (raw model id) and OpenRouter entries
        (prefixed in display with a warmth indicator that would
        otherwise block matching).

        Behavior:
          - Within 700ms of the previous keypress, the new character
            appends to the buffer ('ge' refines 'g'). After 700ms of
            silence, the buffer resets so a fresh 'g' starts over.
          - Single-letter buffer advances PAST the current selection
            (next match for that letter — Windows shell convention).
            Multi-letter buffer includes the current selection (a
            refinement should keep finding the same item if it still
            matches).
          - Wraps around the end of the list.
          - Matches the full model ID prefix OR the basename after
            the last '/'. OpenRouter IDs are 'provider/model' so
            typing 'claude' jumps to 'anthropic/claude-sonnet-4'
            without forcing the user to type the provider prefix.
          - Non-printable keys (arrows, enter, tab, page up/down,
            Home/End) pass through to default list navigation via
            event.Skip().

        We intentionally do NOT call event.Skip() for printable
        characters — that would let wxMSW's native LISTBOX keyboard
        search also fire, racing against ours and producing a
        confused jump (its match is against the display string with
        the warmth prefix; ours is against the ID; the two would
        disagree).
        """
        keycode = event.GetKeyCode()
        # Only ASCII printables (32-126) drive type-to-search.
        # Everything else — arrows, enter, tab, page-up/down,
        # function keys, modifiers — falls through to the default
        # list handler.
        if keycode < 32 or keycode > 126:
            event.Skip()
            return
        ch = chr(keycode).lower()
        now = time.monotonic()
        if now - self._search_last_press > 0.7:
            self._search_buf = ""
        self._search_buf += ch
        self._search_last_press = now

        n = self.model_list.GetCount()
        if n == 0:
            return
        start = self.model_list.GetSelection()
        if start == wx.NOT_FOUND:
            start = -1
        if len(self._search_buf) == 1:
            # First letter: advance past current to find NEXT match.
            offsets = list(range(start + 1, n)) + list(range(0, start + 1))
        else:
            # Refinement: include current — if it still matches the
            # longer buffer, stay put.
            offsets = list(range(start, n)) + list(range(0, max(start, 0)))

        buf = self._search_buf
        for i in offsets:
            mid = (self.model_list.GetClientData(i) or "").lower()
            if not mid:
                continue
            basename = mid.rsplit("/", 1)[-1]
            if mid.startswith(buf) or basename.startswith(buf):
                self.model_list.SetSelection(i)
                # EnsureVisible scrolls the list so the new selection
                # is on-screen — important for long Ollama lists or
                # filtered OpenRouter results.
                try:
                    self.model_list.EnsureVisible(i)
                except Exception:
                    pass
                # SetSelection doesn't fire EVT_LISTBOX, so the
                # detail pane wouldn't refresh on its own. Drive it
                # directly so the user sees the model card update
                # in step with their typing.
                self._on_select(None)
                return
        # No match for this buffer. Leave selection alone; the user
        # will either keep typing (refining further) or pause past
        # the timeout and start over.

    def _on_select(self, event):
        idx = self.model_list.GetSelection()
        if idx == wx.NOT_FOUND:
            self.use_btn.Disable()
            return
        mid = self.model_list.GetClientData(idx)
        self._selected_id = mid
        m = next((x for x in self._filtered if x.get("id") == mid), None)
        if m is None:
            self.detail.SetValue("")
            self.use_btn.Disable()
            return
        self.detail.SetValue(self._render_detail(m))
        self.use_btn.Enable()

    def _render_detail(self, m):
        mid = m.get("id", "?")
        if m.get("_ollama_local"):
            return self._render_ollama_detail(m)

        info = warmth_for(mid, self._overrides)
        pricing = m.get("pricing") or {}
        arch = m.get("architecture") or {}
        caps = detect_capabilities(m)
        caps_str = ", ".join(sorted(caps)) if caps else "(none detected)"
        provider = mid.split("/")[0] if "/" in mid else "?"
        caching = "yes" if provider.lower() in {
            "anthropic", "openai", "deepseek", "google",
            "qwen", "x-ai", "moonshot", "moonshotai", "groq"
        } else "no"
        lines = [
            f"Model:       {mid}",
            f"Provider:    {provider}",
            f"Warmth:      {info.get('warmth', 'unknown').upper()}",
            "",
            f"Warmth note: {info.get('note') or '(no curated note — add to ~/.ai_programs/warmth_overrides.json)'}",
            "",
            f"Context:     {m.get('context_length', '?')} tokens",
            f"Pricing:     in ${pricing.get('prompt', '?')}/tok, out ${pricing.get('completion', '?')}/tok",
            f"Capabilities: {caps_str}",
            f"Caching:     {caching}",
            "",
            f"Description: {m.get('description', '(none)')}",
        ]
        return "\n".join(lines)

    def _render_ollama_detail(self, m):
        """Detail card for a local Ollama model. Capabilities require an
        /api/show HTTP round-trip — that's the slow bit we don't want to
        do synchronously on every selection (it would freeze the UI on
        every arrow-key press through the list). Result cached per
        dialog instance.

        Render strategy: if the caps for this model are already cached,
        paint them immediately. If not, paint "(loading…)" right now and
        spawn a background thread to fetch — when the fetch completes,
        update the cache and repaint the detail IF the user is still on
        this model. Arrowing rapidly through the list no longer blocks
        on each fresh-model focus; the user can keep moving and the
        capability lines fill in as the background calls finish."""
        mid = m.get("id", "?")
        details = m.get("_ollama_details") or {}
        family = details.get("family") or "?"
        params = details.get("parameter_size") or "?"
        quant = details.get("quantization_level") or "?"
        size_bytes = details.get("size_bytes") or 0
        size_gb = size_bytes / (1024 ** 3) if size_bytes else 0
        modified = (details.get("modified_at") or "")[:19] or "unknown"

        cached = self._ollama_caps_cache.get(mid)
        if cached is _OLLAMA_CAPS_INFLIGHT:
            # Fetch already in flight from a prior focus on this same
            # model; just show the loading marker. The in-flight worker
            # will repaint when it completes.
            caps_str = "(loading…)"
        elif cached is not None:
            # Real cached result — could be a list of capability strings
            # or an empty list (model genuinely reports none).
            caps_str = ", ".join(cached) if cached else "(none reported)"
        else:
            # First focus on this model — kick off the fetch and show
            # the loading marker. Sentinel goes into the cache slot so
            # a rapid second focus doesn't spawn a duplicate worker.
            self._ollama_caps_cache[mid] = _OLLAMA_CAPS_INFLIGHT
            self._spawn_ollama_caps_fetch(mid)
            caps_str = "(loading…)"

        lines = [
            f"Model:        {mid}",
            f"Provider:     Ollama (local)",
            f"Family:       {family}",
            f"Parameters:   {params}",
            f"Quantization: {quant}",
            f"Size on disk: {size_gb:.1f} GB",
            f"Modified:     {modified}",
            f"Capabilities: {caps_str}",
            "",
            "Local model — no pricing, no caching, no network. Content "
            "stays on this machine.",
        ]
        return "\n".join(lines)

    def _spawn_ollama_caps_fetch(self, mid):
        """Background-thread an /api/show call for `mid` and, when it
        completes, update the caps cache and repaint the detail pane
        IF the user is still on the same model. Caller has already
        marked the cache entry as in-flight, so we don't have to
        re-check here.

        The dialog itself may be closing by the time the worker
        finishes; wx.CallAfter is safe across destroyed targets in
        practice but we still guard with wx.GetApp() and Destroy
        checks before touching widgets."""
        import threading

        def worker():
            try:
                show = _ollama_show_raw(mid, host=self._list_host())
            except Exception:
                show = None
            caps = (show or {}).get("capabilities") if show else None
            if caps is None:
                caps = []
            wx.CallAfter(self._on_ollama_caps_loaded, mid, caps)

        threading.Thread(target=worker, daemon=True).start()

    def _on_ollama_caps_loaded(self, mid, caps):
        """Main-thread completion handler for a background caps fetch.
        Stores the result, then re-renders the detail pane if the user
        is still on this model. If they've moved on, the cache is
        populated for next time but the pane stays as-is."""
        # Dialog may have been destroyed mid-flight.
        try:
            if not self or not bool(self):
                return
        except RuntimeError:
            return
        self._ollama_caps_cache[mid] = caps
        if getattr(self, "_selected_id", None) == mid:
            try:
                m = next((x for x in self._filtered if x.get("id") == mid), None)
            except Exception:
                m = None
            if m is not None and m.get("_ollama_local"):
                try:
                    self.detail.SetValue(self._render_ollama_detail(m))
                except Exception:
                    pass

    # ─── Actions ─────────────────────────────────────────────────────────────

    def _on_refresh(self, event):
        self._load_models_async(force_refresh=True)

    def _on_use_button(self, event):
        if self._selected_id is None:
            return
        self.EndModal(wx.ID_OK)

    def get_selected_model(self):
        """Return the selected model with provider-appropriate prefix:
        `openrouter/<id>` for OpenRouter, bare name for Ollama. Returns
        None if nothing is selected."""
        if self._selected_id is None:
            return None
        if self._provider != "ollama":
            return f"{self._provider}/{self._selected_id}"
        return self._selected_id
