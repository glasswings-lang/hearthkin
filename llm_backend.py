# SPDX-License-Identifier: CC0-1.0

"""
llm_backend — Ollama + OpenRouter chat dispatch.

Single public function `chat(model, messages, ...)`:
  - When stream=True: returns an iterator of Chunk objects
  - When stream=False: returns a single ChatResult

Dispatches on model name prefix:
  openrouter/<provider>/<name>  → OpenRouter HTTPS (+ SSE for streaming)
  anything else                 → Ollama (current behavior preserved)

Designed as a drop-in for hearthkin / ollama_chat_thinking / ool_dialogue.
Each script swaps its `ollama.chat(...)` call for `llm_backend.chat(...)`
and the rest of the streaming loop continues to work.

OpenRouter key resolution (in order):
  1. OPENROUTER_API_KEY env var
  2. ~/.ai_programs/openrouter_key.json   (JSON: {"key": "sk-or-..."})
  3. None → OpenRouterAuthError on first OpenRouter call
"""

import hashlib
import http.client
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

_LOG = logging.getLogger("llm_backend")

try:
    import ollama
except ImportError:
    ollama = None


# Re-export the same version the frame uses, so any User-Agent headers
# built here match. Source of truth is app_version.py (build-stamped).
from app_version import __version__  # noqa: E402

# Markdown code-region detection shared with the inline-thinking
# extractor: fenced blocks + inline backtick spans. Used to keep
# content-extracted tool calls from executing markup the model merely
# QUOTED inside a code example. chat_helpers is pure stdlib — no
# import cycle.
from chat_helpers import _code_region_spans, _pos_in_spans  # noqa: E402


OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_MODELS_CACHE = Path.home() / ".ai_programs" / "openrouter_models_cache.json"
OPENROUTER_KEY_FILE = Path.home() / ".ai_programs" / "openrouter_key.json"

# Providers known to support prompt caching (per OpenRouter docs, verified 2026-05).
# Some are automatic, some need explicit cache_control. Top-level cache_control
# works for both modes — the server applies it where it can.
_CACHE_SUPPORTED_PROVIDERS = frozenset({
    "anthropic", "openai", "deepseek", "google",
    "qwen", "x-ai", "moonshot", "moonshotai", "groq",
})

# Where Ollama lives. Set by the frame at startup (and on Preferences
# changes) via set_ollama_host(); resolved at call time by
# _resolve_ollama_host() which falls through to the OLLAMA_HOST env var
# and then to localhost:11434. Module-level so every direct-HTTP path
# (chat raw fallback, capability/show, model listing) sees the same
# host as the chat path without each caller threading it through.
_OLLAMA_HOST_OVERRIDE = ""


def set_ollama_host(host):
    """Set the host all Ollama-side calls in this module should target.

    Pass an empty string to clear the override (falls back to the
    OLLAMA_HOST env var, then localhost:11434). The protocol prefix
    is added if absent; trailing slashes are stripped.

    The frame sets this to the ACTIVE kin's machine on every kin switch
    (_load_agent), so the shared probe/capability/preload/list machinery
    — which resolves the host via this override rather than an explicit
    per-call host — targets the kin whose model is being inspected. The
    chat() send paths do NOT rely on this; they pass ollama_host=
    explicitly (per-kin, concurrency-safe across surfaces).
    """
    global _OLLAMA_HOST_OVERRIDE
    host = (host or "").strip()
    if host and not host.startswith(("http://", "https://")):
        host = "http://" + host
    _OLLAMA_HOST_OVERRIDE = host.rstrip("/")


def _resolve_ollama_host():
    """Return the base URL to use for Ollama requests. Resolution
    order: explicit override (set_ollama_host) → OLLAMA_HOST env var →
    default http://localhost:11434. Always returns a URL with protocol
    prefix and no trailing slash."""
    host = _OLLAMA_HOST_OVERRIDE or os.environ.get("OLLAMA_HOST", "")
    host = (host or "").strip()
    if not host:
        return "http://localhost:11434"
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host.rstrip("/")


# --- Ollama request wall-clock guard -------------------------------------
# ollama-python's Client sets NO timeout by default, so a hung request — a
# dead-but-ESTABLISHED connection that never returns data, seen when a
# second big model loads on the same host and wedges the daemon — blocks
# the calling worker forever. The kin sits at "typing" indefinitely, and
# because kin on one host share a single Ollama, a hang on one kin's turn
# stalls every other kin's turn queued behind it. A generous read cap lets
# a legitimately slow load+generation finish while turning a true hang into
# a clean error the surface reports and logs. Every NON-streaming Ollama
# path — desktop tool-loop, rooms, Telegram, and every cron path — builds
# its client here, so this one guard covers chats and crons alike. (The
# streaming desktop path also has its own chunk-based UI watchdog.)
_OLLAMA_CONNECT_TIMEOUT = 15.0
_OLLAMA_READ_TIMEOUT = 900.0  # fallback read cap (15 min) when no per-kin
                              # watchdog setting is resolvable; normally the
                              # per-kin value governs (see _resolve_watchdog_timeout_secs)

# FALLBACK embedding-call timeout (SECONDS), used when a caller passes no
# timeout (or 0). The live value is the app-level `embed_timeout_secs`
# config (Preferences → Connections), resolved by the caller and passed
# into embed_texts — same shape as embed_host / embed_model. This constant
# only governs when that config is unreadable. Embeddings get their OWN
# short cap, separate from the long chat read cap: memory_recall calls
# embed_texts on every send, so an un-timeout'd embed against a slow /
# unreachable host is catastrophic — it froze the whole desktop UI on send
# (permanent, unkillable hang). A bounded timeout fails soft to BM25.
_EMBED_TIMEOUT_SECS = 12.0


def _ollama_client_timeout(read_secs=None):
    """Per-request timeout for the Ollama httpx client. httpx.Timeout keeps
    connect separate from read so a black-holed host fails CONNECT fast
    while a slow-but-live generation gets the full read window. `read_secs`
    is the resolved per-kin cap (see _resolve_watchdog_timeout_secs); None /
    0 falls back to _OLLAMA_READ_TIMEOUT. Falls back to a plain float if
    httpx isn't importable (it always is — it's an ollama-python dependency
    — but degrade safely rather than crash)."""
    read = float(read_secs) if read_secs and read_secs > 0 else _OLLAMA_READ_TIMEOUT
    try:
        import httpx
        return httpx.Timeout(
            connect=_OLLAMA_CONNECT_TIMEOUT,
            read=read,
            write=120.0,
            pool=_OLLAMA_CONNECT_TIMEOUT,
        )
    except Exception:
        return read


# Surfaces where nobody is waiting on the answer. The cost of cutting one
# of these off early is a whole chunk of work thrown away — and, for a
# redistill, the run stopping — while the cost of waiting longer is a
# background thread idling before it gives up. That asymmetry is nothing
# like an interactive chat's, where a person is sitting there.
_UNATTENDED_SURFACES = frozenset({"distill", "consolidate"})
# Floor for those surfaces. The size-derived formula below is tuned for a
# reply and turned out to sit barely above the real work on a big local
# model: measured on gemma4:31b at ~78 tok/s prefill and ~8 tok/s
# generation, one distillation chunk of a 20k-token bite runs 360-455
# seconds against a derived cap of 480. A margin that thin is a coin
# flip per chunk — anything else touching the machine pushes it over —
# and it was losing roughly four times in ten, each loss stopping the
# redistill it belonged to.
_UNATTENDED_MIN_TIMEOUT_SECS = 30 * 60


def _resolve_watchdog_timeout_secs(kin_name, model, surface=None):
    """Wall-clock cap (SECONDS) for a single Ollama request, so a hung
    request self-terminates instead of blocking a worker (and every
    same-host kin behind it) forever.

    Reads the SAME per-kin `watchdog_timeout_minutes` the streaming UI
    watchdog uses, so ONE setting governs every path — streaming chats,
    tool-loop chats, and crons. Deliberately kept in sync with
    Hearthkin._compute_watchdog_timeout_ms:
      1. per-kin `watchdog_timeout_minutes` if > 0 (floored at 5 min)
      2. else auto: 5 min + 1 min per 8k of num_ctx above 8k, capped 30 min

    UNATTENDED surfaces then get a floor applied on top — see
    _UNATTENDED_SURFACES. The floor can only RAISE the cap, never lower
    it: a per-kin override tuned for chat responsiveness shouldn't
    quietly become the budget for background work that nobody is
    waiting on.

    Falls back to _OLLAMA_READ_TIMEOUT when no kin config is available (e.g.
    a call with no kin_name), so the guard is never absent."""
    unattended = str(surface or "") in _UNATTENDED_SURFACES
    if not kin_name:
        return (max(_OLLAMA_READ_TIMEOUT, _UNATTENDED_MIN_TIMEOUT_SECS)
                if unattended else _OLLAMA_READ_TIMEOUT)
    try:
        cfg = _load_agent_config_cached(kin_name) or {}
    except Exception:
        return (max(_OLLAMA_READ_TIMEOUT, _UNATTENDED_MIN_TIMEOUT_SECS)
                if unattended else _OLLAMA_READ_TIMEOUT)
    BASE_MIN, CAP_MIN = 5, 30
    try:
        override = int(cfg.get("watchdog_timeout_minutes", 0) or 0)
    except (TypeError, ValueError):
        override = 0
    if override > 0:
        secs = max(BASE_MIN, override) * 60
    else:
        try:
            num_ctx = int(cfg.get("num_ctx", 8192) or 8192)
        except (TypeError, ValueError):
            num_ctx = 8192
        extra_min = max(0, (num_ctx - 8192)) // 8192
        secs = min(CAP_MIN, BASE_MIN + extra_min) * 60
    if unattended:
        secs = max(secs, _UNATTENDED_MIN_TIMEOUT_SECS)
    return secs


def _is_request_timeout(err):
    """True if `err` (or a cause/context in its chain) is a request
    timeout — httpx raises ReadTimeout/ConnectTimeout/etc., which
    ollama-python may pass through or wrap. Matched by type-name so we
    don't hard-import httpx here."""
    seen = set()
    e = err
    for _ in range(6):
        if e is None or id(e) in seen:
            break
        seen.add(id(e))
        if "timeout" in type(e).__name__.lower():
            return True
        e = getattr(e, "__cause__", None) or getattr(e, "__context__", None)
    return False


def _log_hang_watchdog(surface, kin_name, model, err, read_secs=None):
    """Append a caught-hang line to hang_watchdog.log (always on, bypasses
    the conversation-logging toggle — same pattern as openrouter_errors.log
    / empty_replies.log). Fires when the Ollama wall-clock guard trips. The
    `surface` label (desktop / desktop-tool / room* = chats; cron-* =
    scheduled wake-ups) makes it explicit which path the one guard caught,
    so it reads clearly that it covers both crons and chats. `read_secs` is
    the actual per-kin cap that tripped (from watchdog_timeout_minutes)."""
    try:
        from kin_persistence import LOGS_DIR
        import datetime
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        cap = int(read_secs) if read_secs else int(_OLLAMA_READ_TIMEOUT)
        line = (f"{ts} surface={surface or 'unknown'} kin={kin_name or '-'} "
                f"model={model} watchdog_timeout={cap}s "
                f"err={type(err).__name__}\n")
        with open(LOGS_DIR / "hang_watchdog.log", "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _ollama_chat_callable(host=None, request_timeout_secs=None):
    """Return the right `ollama.chat`-shaped callable for the currently
    resolved host. When no override is set, returns the module-level
    `ollama.chat` (which reads OLLAMA_HOST env var itself). When the
    override is set, returns an `ollama.Client(host=...).chat` bound
    method so streaming + blocking calls reach the configured host —
    `ollama.chat()` itself doesn't accept a host kwarg, you have to
    construct a Client.

    A fresh Client is created per call. ollama-python's Client is cheap
    to construct (just stores config; doesn't open a connection), so
    this avoids any caching/threadsafety question."""
    if ollama is None:
        return None
    # A per-call host (per-kin override) wins; else the app-level global
    # override; else the library default (OLLAMA_HOST env / localhost).
    # Always go through an explicit Client so we can attach the wall-clock
    # timeout (_ollama_client_timeout) — the library default is no timeout,
    # which lets a hung request block a worker forever. host=None keeps the
    # library's own OLLAMA_HOST/localhost resolution.
    target = host or _OLLAMA_HOST_OVERRIDE
    return ollama.Client(
        host=target,
        timeout=_ollama_client_timeout(request_timeout_secs),
    ).chat


def embed_texts(texts, model, host=None, timeout=None, keep_alive=None):
    """Embed a list of strings via Ollama on `host` (a URL; None falls
    back to _OLLAMA_HOST_OVERRIDE / OLLAMA_HOST / localhost). Returns a
    list of float vectors — one per input, order preserved — or None on
    ANY failure (library missing, host unreachable, model not pulled,
    malformed response). Callers (semantic memory search) treat None as
    "fall back to keyword search" rather than an error, so a
    misconfigured embed setup degrades gracefully instead of breaking.

    `keep_alive` (Ollama's own format: -1 = pin forever, "30m", seconds,
    …) controls how long the embed model stays resident after the call.
    None = don't send it (server default). Keeping it warm avoids a
    cold-reload of the embed model on every per-turn recall.

    The embed model (e.g. nomic-embed-text) must be pulled on that host
    first — Preferences → Connections → Download embedding model, or
    `ollama pull nomic-embed-text` on the box running Ollama.

    `timeout` (SECONDS) bounds the request so a slow / unreachable host
    returns None (→ keyword-search fallback) instead of hanging. None / 0
    uses the built-in fallback `_EMBED_TIMEOUT_SECS`; callers resolve it
    from the app-level `embed_timeout_secs` config."""
    if ollama is None or not texts:
        return None
    target = host or _OLLAMA_HOST_OVERRIDE
    to = timeout if (timeout and timeout > 0) else _EMBED_TIMEOUT_SECS
    _ka = {} if keep_alive is None else {"keep_alive": keep_alive}
    try:
        if target:
            resp = ollama.Client(host=target, timeout=to).embed(
                model=model, input=texts, **_ka)
        else:
            resp = ollama.Client(timeout=to).embed(
                model=model, input=texts, **_ka)
    except Exception:
        return None
    embs = (resp.get("embeddings") if isinstance(resp, dict)
            else getattr(resp, "embeddings", None))
    if not embs or len(embs) != len(texts):
        return None
    return embs


def pull_ollama_model(model, progress_cb=None, host=None):
    """Pull an Ollama model onto `host` (a URL; None falls back to
    _OLLAMA_HOST_OVERRIDE / OLLAMA_HOST / localhost). Streams progress;
    if `progress_cb` is given it is called with short human-readable
    status lines (e.g. "pulling 42%"). Returns (True, "") on success or
    (False, error_message). Lets the 'Download embedding model' button
    fetch the embed model without the user needing a terminal on the box
    that runs Ollama — a first slice of the in-app model management the
    model browser otherwise lacks."""
    if ollama is None:
        return (False, "ollama library not installed")
    target = host or _OLLAMA_HOST_OVERRIDE
    host = target or _resolve_ollama_host()
    try:
        client = ollama.Client(host=target) if target else ollama
        last = ""
        for prog in client.pull(model, stream=True):
            status = (prog.get("status") if isinstance(prog, dict)
                      else getattr(prog, "status", "")) or ""
            completed = (prog.get("completed") if isinstance(prog, dict)
                         else getattr(prog, "completed", None))
            total = (prog.get("total") if isinstance(prog, dict)
                     else getattr(prog, "total", None))
            if progress_cb:
                if total and completed:
                    line = f"{status} {int(completed * 100 / total)}%"
                else:
                    line = status
                if line and line != last:
                    progress_cb(line)
                    last = line
        return (True, "")
    except Exception as e:
        return (False, f"Could not pull {model!r} from {host}: {e}")


# Fudge factor for token estimation: ~4 chars per token across most tokenizers.
_CHARS_PER_TOKEN = 4

# Response reserve every conversational surface assumes when sizing
# `max_context_tokens`: callers pass num_ctx - 2000 so prompt + reply
# fit the window together. chat()'s budget math reclaims anything a
# caller's num_predict needs beyond this, and the calibration cap-hit
# heuristic inflates max_context_tokens back by the same amount to
# recover the true num_ctx. One named constant so the three sites
# can't drift.
_RESPONSE_RESERVE_TOKENS = 2000

# _est_tokens (~4 chars/token, message text only) runs low against the real
# provider token count: mixed prose+JSON tokenizes denser than 4 chars/tok,
# per-message JSON structure isn't counted, and tool schemas (billed on
# every tool-enabled call) aren't messages so they're missed too. Rather
# than guess a fixed correction, we CALIBRATE against reality: every
# blocking conversational call gets back the provider's real prompt-token
# count, and _update_token_calibration records real/estimate as a per-kin
# ratio. _truncate_messages' budget is divided by that measured ratio, so
# the real billed prompt lands near the caller's cap. _DEFAULT_TOKEN_RATIO
# (~1.3, the observed real/est gap) seeds a kin before its first measured
# call.
_DEFAULT_TOKEN_RATIO = 1.5  # Safer seed for unknown tokenizers. Was 1.3 — too
                            # optimistic for Mistral's Tekken tokenizer with
                            # heavy tool schemas, where the real ratio is
                            # closer to 2.0-2.3. Calibration learns the real
                            # ratio over a few calls; this just keeps the
                            # first call from overshooting num_ctx so badly
                            # that the model can't generate at all.
# Plausibility ceiling for a measured real/estimate ratio, and the
# ratchet applied when a call comes back having hit the context wall.
# See _update_token_calibration for why a cap-hit can only ever raise
# the ratio, never lower it.
_MAX_TOKEN_RATIO = 5.0
_CAP_HIT_RATIO_BUMP = 1.15

_token_calibration = {}    # kin_name -> measured real/estimate ratio
_last_prompt_tokens = {}   # kin_name -> real prompt_tokens, most recent call

# The learned ratio is persisted per kin so it survives process exit.
# Without this every scheduled wake-up relearns from the seed: cron and
# heartbeat runs are separate short-lived processes (Task Scheduler
# spawns hearthkin_cron.py per entry), so they never accumulate a
# measurement at all and always send their first — and only — call at
# the default ratio.
#
# Stored in its own small file per kin rather than in config.json: the
# GUI and a cron subprocess can be live at the same time, and a
# background process rewriting the shared config would race the GUI's
# own save and silently drop user settings. A single-purpose file is
# safe to clobber, because losing it costs one recalibration.
_calibration_loaded = set()   # kin names read from disk this process
_calibration_on_disk = {}     # kin_name -> ratio last written
# Rewrite only on a meaningful move — the ratio updates on every
# conversational call, and a write per turn is pointless disk churn.
_CALIBRATION_SAVE_DELTA = 0.02

# How far a fresh measurement must sit from the current ratio before it
# moves it at all. Without this the EMA never settles: every call nudges
# the ratio by a fraction of a percent, forever, because real prompts
# genuinely tokenize a little differently from each other. That looks
# harmless and is not — the ratio divides the truncation budget, so a
# ratio that never stops moving is a truncation point that never stops
# moving, and a trim point that moves is the whole conversation re-read
# from cold. See _stable_truncation_budget. A genuine change (a
# different model, a different tokenizer, a kin that starts using tools)
# clears 5% easily; noise doesn't.
_CALIBRATION_DEADBAND = 0.05

# --- Truncation-budget stability -------------------------------------
#
# The budget handed to _truncate_messages is `max_context_tokens /
# ratio`, and the trim is a pure function of it: same budget, same trim
# point, byte-identical prefix, warm cache. Measured by replaying a real
# kin's 1,871-turn history: with the ratio held still the window start
# did not move once across twelve turns; with the ratio drifting the way
# the EMA actually drifts it moved on EVERY turn, sometimes backwards,
# and a wobble of half a percent was enough to make it oscillate between
# two points forever. That is the difference between a reply starting in
# seconds and one spending minutes re-reading a conversation that barely
# changed, and nothing in the UI shows why.
#
# So the budget is quantized (so two processes with slightly different
# ratios still agree on one number) and then held still (so jitter around
# a quantum boundary can't flip it back and forth). It may drop
# immediately, because a budget that is too LARGE risks overrunning
# num_ctx, which on local Ollama returns nothing at all. It only rises on
# a big, unmistakable change — regaining a couple of old messages is
# worth very little and costs a full re-prefill.
_BUDGET_QUANTUM = 2048
_BUDGET_GROW_FACTOR = 1.15
_sticky_budgets = {}   # (kin, surface, cap) -> budget last actually used


def _stable_truncation_budget(key, raw_budget):
    """Round a computed truncation budget down to a quantum and hold it
    there, so an unchanged conversation keeps an unchanged trim point.

    `key` identifies one caller's steady state — (kin, surface, cap) —
    because different surfaces legitimately have different budgets (a
    tool-enabled turn reserves room for schemas) and must not fight over
    one number; two surfaces alternating between two budgets would
    reproduce the exact churn this exists to stop.

    Falls immediately, rises reluctantly. See _BUDGET_QUANTUM.
    """
    q = _BUDGET_QUANTUM
    try:
        want = max(q, (int(raw_budget) // q) * q)
    except (TypeError, ValueError):
        return raw_budget
    prev = _sticky_budgets.get(key)
    if prev is None or want < prev or want >= prev * _BUDGET_GROW_FACTOR:
        _sticky_budgets[key] = want
        return want
    return prev


def _calibration_path(kin_name):
    from kin_persistence import agent_dir
    return agent_dir(kin_name) / "calibration.json"


def _load_calibration(kin_name):
    """Populate this kin's in-memory ratio from disk, once per process.

    A value already measured in this process always wins — it is newer
    than anything stored. Out-of-range or unreadable values are ignored
    and simply cost one recalibration."""
    if not kin_name or kin_name in _calibration_loaded:
        return
    _calibration_loaded.add(kin_name)
    if kin_name in _token_calibration:
        return
    try:
        from kin_persistence import load_json
        path = _calibration_path(kin_name)
        if not path.exists():
            return
        stored = load_json(path, {}) or {}
        ratio = float(stored.get("token_ratio") or 0)
    except Exception:
        return
    if 0.8 <= ratio <= _MAX_TOKEN_RATIO:
        _token_calibration[kin_name] = ratio
        _calibration_on_disk[kin_name] = ratio


def _save_calibration(kin_name, ratio):
    """Persist this kin's ratio, skipping writes that wouldn't change it
    meaningfully. Failures are swallowed: an unwritable calibration file
    must never cost the kin their reply."""
    if not kin_name:
        return
    prior = _calibration_on_disk.get(kin_name)
    if prior is not None and abs(ratio - prior) < _CALIBRATION_SAVE_DELTA:
        return
    try:
        from kin_persistence import atomic_write_json
        path = _calibration_path(kin_name)
        if not path.parent.exists():
            return
        atomic_write_json(path, {"token_ratio": round(ratio, 4)})
        _calibration_on_disk[kin_name] = ratio
    except Exception:
        pass


def _log_context_overflow(kin_name, reported, num_ctx):
    """Append a context-wall hit to context_overflow.log (always on,
    bypasses the conversation-logging toggle — same pattern as
    hang_watchdog.log / empty_replies.log).

    Fires when the provider reports a prompt token count at or above the
    kin's configured window. The visible symptom is a reply with no text
    in it, which is indistinguishable in empty_replies.log from a model
    that simply had nothing to say. This log is what tells the two
    apart: an entry here at the same timestamp means the prompt filled
    the window and left no room to generate."""
    try:
        from kin_persistence import LOGS_DIR
        import datetime
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        line = (f"{ts} kin={kin_name or '-'} prompt_tokens={reported} "
                f"num_ctx={num_ctx} — prompt filled the context window; "
                f"little or no room left to generate\n")
        with open(LOGS_DIR / "context_overflow.log", "a",
                  encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _append_context_overflow_line(text):
    """Shared writer for context_overflow.log. Always on, bypasses the
    conversation-logging toggle, and swallows every failure — a
    diagnostic must never be the reason a send fails."""
    try:
        from kin_persistence import LOGS_DIR
        import datetime
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with open(LOGS_DIR / "context_overflow.log", "a",
                  encoding="utf-8") as f:
            f.write(f"{ts} {text}\n")
    except Exception:
        pass


def _log_reserve_clamped(kin_name, surface, asked, allowed, max_ctx):
    """The reply reserve was bigger than the window could afford and got
    cut down to fit. Logged rather than silent because it changes how
    long a reply the model may produce — a long write_file argument can
    still be cut off mid-JSON at the clamped size, and when that happens
    the person needs a way to connect it to a num_ctx that is simply too
    small for a tool-using kin."""
    _append_context_overflow_line(
        f"kin={kin_name or '-'} surface={surface or '-'} "
        f"reply_reserve={asked}->{allowed} window={max_ctx} — the reply "
        f"reserve was larger than the window; capped so the conversation "
        f"still fits. Raise num_ctx if replies or tool arguments get cut off")


def _log_context_starved(kin_name, surface, budget, sent_est, restored):
    """The trim left NO conversation at all. This is the quiet one: the
    kin still answers, fluently, from its soul prompt — with no idea that
    anyone is talking to it. From a chat window it reads as the kin
    having gone blank or stupid, and it persists turn after turn because
    nothing about it clears on its own.

    Written every time it happens, on purpose. A rate limit here would
    hide exactly the case that matters most — the one that repeats."""
    what = ("most recent question put back"
            if restored else "NOTHING could be put back")
    _append_context_overflow_line(
        f"kin={kin_name or '-'} surface={surface or '-'} budget={budget} "
        f"sent_est={sent_est} — the window left no room for ANY of the "
        f"conversation; {what}. The kin is answering from its soul prompt "
        f"alone. Raise num_ctx, or shorten the soul/memory block")


def _has_conversation(messages):
    """True when `messages` holds anything the model could answer — i.e.
    something that isn't part of the leading system block. A list of
    system messages alone is a persona with no conversation in it."""
    return any(isinstance(m, dict) and m.get("role") != "system"
               for m in messages)


def _last_user_turn(messages):
    """The most recent plain user turn, or None. Deliberately a USER turn
    rather than simply the last message: the last message can be a tool
    result or an assistant turn, neither of which gives the model
    anything to answer, and a lone tool result is a shape some providers
    reject outright."""
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            return m
    return None


def last_reported_prompt_tokens(kin_name):
    """Real prompt-token count the provider reported for this kin's most
    recent blocking conversational call, or None if there's been none
    this session. The Usage tab shows this as the authoritative
    'currently using' number instead of the _est_tokens estimate."""
    return _last_prompt_tokens.get(kin_name)


def invalidate_last_reported(kin_name):
    """Clear this kin's cached `last_reported_prompt_tokens` so the next
    consumer falls back to the estimate. Call when something has
    changed that makes the previous count meaningless against the
    current configuration: num_ctx changed (the cap shifted, so
    the % computed from the cached value is wrong against the new
    cap) or model changed (different tokenizer, so the cached count
    isn't comparable). Cheap; no-op if the kin has no entry."""
    _last_prompt_tokens.pop(kin_name, None)


def token_calibration_ratio(kin_name):
    """The measured real/estimate prompt-token ratio for this kin, or
    the seed default (_DEFAULT_TOKEN_RATIO) if no call has calibrated
    it yet. Multiply an _est_tokens-style estimate by this to
    approximate the real billed prompt size. See
    _update_token_calibration."""
    _load_calibration(kin_name)
    return _token_calibration.get(kin_name) or _DEFAULT_TOKEN_RATIO


# ─── Result types ────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    """One streamed delta from a model. Empty strings if absent."""
    content: str = ""
    thinking: str = ""
    # Tool-call deltas as raw OpenAI-shaped dicts. The caller accumulates by
    # index across chunks if needed (left to the caller for v1 simplicity).
    tool_calls: list = field(default_factory=list)
    done: bool = False
    # Populated only on the final chunk:
    usage: dict | None = None
    # Liveness signal that carries no content. Used for SSE-comment
    # "heartbeat" lines from OpenRouter (the SSE spec uses `:` lines
    # as ignorable comments; some providers send them periodically as
    # keepalives during slow inference). The streaming watchdog treats
    # heartbeats as proof-of-life so a stalled-but-connected provider
    # doesn't trip the no-chunks timer.
    heartbeat: bool = False


@dataclass
class ChatResult:
    """Full non-streamed result."""
    content: str = ""
    thinking: str = ""
    tool_calls: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    # Populated only by run_tool_loop: the intermediate assistant-with-tool_calls
    # and tool-result turns the loop appended to its internal history. Lets the
    # caller persist the full round-trip into the kin's conversation so the
    # model sees its own past tool calls on the next turn.
    messages_added: list = field(default_factory=list)
    # True when a `should_stop` callback asked the streaming path or the tool
    # loop to stop early. The content/tool_calls collected up to that moment
    # are still returned — a stop keeps what the kin had already said rather
    # than throwing the turn away. Callers that never pass `should_stop`
    # always see False.
    stopped: bool = False


@dataclass
class _CallContext:
    """Per-chat-call metadata bundle. Built once at the top of chat()
    and passed through to _log_call_usage / _stream_with_usage so each
    add-on signal (token estimate, context cap, future per-call data)
    shows up in one named field instead of growing those functions'
    parameter lists.

    Lives for one chat() invocation; not persisted, not exposed to
    callers of chat()."""
    kin_name: str | None = None
    model: str = ""
    surface: str = "unknown"
    est_sent: int | None = None
    max_context_tokens: int | None = None


class LLMBackendError(Exception):
    """Base for backend-level errors."""


class OpenRouterAuthError(LLMBackendError):
    """No API key set."""


class OpenRouterRateLimitError(LLMBackendError):
    """429 from OpenRouter. Carries .retry_after (seconds) if provided."""
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


# ─── Key resolution ──────────────────────────────────────────────────────────

# Standard layout: every provider's API key resolves the same way —
# env var `<PROVIDER>_API_KEY` first, then `~/.ai_programs/<provider>_key.json`
# (JSON: `{"key": "..."}`). UI in Preferences → Connections writes the JSON
# file; env vars still win for users who script them. Adding a new provider
# means just calling resolve_provider_key("name") — no new resolver needed.
PROVIDER_KEY_DIR = Path.home() / ".ai_programs"


# Whitelist of characters allowed in a provider name (audit L18). Keeps
# weird inputs (paths, env-var manipulation attempts, whitespace) from
# producing malformed env-var lookups or filesystem traversal in the
# key-file path. New providers should match this shape — short
# lowercase names with hyphens, e.g. "openrouter", "brave",
# "elevenlabs".
_PROVIDER_NAME_RE = re.compile(r"^[a-z0-9_-]+$")


def _validate_provider_name(provider_name):
    if not provider_name or not isinstance(provider_name, str):
        return False
    return bool(_PROVIDER_NAME_RE.match(provider_name))


def resolve_provider_key(provider_name):
    """Return the API key for `provider_name` ("openrouter", "brave", etc.)
    by checking the env var first, then the on-disk JSON file. Returns ""
    (empty string) if neither is set — callers should treat that as
    "not configured" and surface a clear error to whoever's asking.

    File shape: `{"key": "..."}` at `~/.ai_programs/<provider>_key.json`.
    Env var: `<PROVIDER>_API_KEY` (uppercase, underscores)."""
    if not _validate_provider_name(provider_name):
        return ""
    env_var = provider_name.upper().replace("-", "_") + "_API_KEY"
    key = os.environ.get(env_var, "").strip()
    if key:
        return key
    key_file = PROVIDER_KEY_DIR / f"{provider_name}_key.json"
    if key_file.exists():
        try:
            data = json.loads(key_file.read_text(encoding="utf-8"))
            return (data.get("key") or "").strip()
        except Exception as e:
            # Hand-edited JSON corruption used to silently look like
            # "no key set" with no hint that the file existed and was
            # unreadable (audit L14). Log so the user can spot why
            # their key isn't working.
            try:
                from kin_persistence import append_failure_log
                append_failure_log(
                    "save_failures.log", provider_name,
                    f"resolve_provider_key({key_file.name})", e,
                )
            except Exception:
                pass
            return ""
    return ""


def write_provider_key(provider_name, key):
    """Write `key` to `~/.ai_programs/<provider>_key.json` so it's picked up
    by resolve_provider_key on the next call. Empty `key` writes an empty
    file (effectively clearing the on-disk key — env var still wins). The
    parent directory is created if missing."""
    if not _validate_provider_name(provider_name):
        raise ValueError(
            f"provider_name must match {_PROVIDER_NAME_RE.pattern!r}")
    key_file = PROVIDER_KEY_DIR / f"{provider_name}_key.json"
    key_file.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"key": (key or "").strip()}, indent=2)
    # Write atomically via a temp file that is 0600 FROM CREATION, then
    # rename over the destination. The previous write_text()+chmod() left a
    # TOCTOU window where the key bytes were briefly world-readable (0644)
    # before the chmod tightened them (2026-07 security audit G2). mkstemp
    # creates the temp at 0600 on POSIX, and os.replace preserves that mode;
    # on Windows the ACLs already default to the writing user.
    import tempfile
    fd, tmp = tempfile.mkstemp(
        dir=str(key_file.parent), prefix=".key-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, key_file)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _resolve_openrouter_key():
    """Back-compat shim — internal callers still use this name. New code
    should call resolve_provider_key('openrouter') directly."""
    return resolve_provider_key("openrouter")


# ─── Context-window truncation ───────────────────────────────────────────────

# Per-image token cost estimate used by _est_tokens. Real cost varies
# 765-2000 by provider and image dimensions (OpenAI low-detail ~765,
# high-detail ~1530; Anthropic ~1500; Mistral ~1500-2000). 1500 is a
# conservative middle that won't undercount on the strict providers.
# Truncation runs BEFORE _expand_attachments_for_provider, so it sees
# `attachments: [paths]` not yet-expanded image_url blocks; _message_image_count
# handles both shapes so calibration after expansion is also accurate.
_IMAGE_TOKEN_ESTIMATE = 1500


def _est_tokens(messages):
    """Estimate prompt tokens for a messages list. Counts message text +
    tool_call argument text via _message_text, PLUS image input at a
    fixed ~1500-token-per-image estimate via _message_image_count.
    Without the image accounting, truncation budget under-shoots on
    image-bearing turns and the real prompt overruns num_ctx — Mistral
    via OpenRouter rejects with a 400 ("This endpoint's maximum context
    length is N tokens"), where Anthropic / OpenAI silently accept the
    overrun up to their hard cap."""
    text_tokens = sum(len(_message_text(m)) for m in messages) // _CHARS_PER_TOKEN
    image_tokens = sum(_message_image_count(m) for m in messages) * _IMAGE_TOKEN_ESTIMATE
    return text_tokens + image_tokens


def _message_image_count(m):
    """Count images carried by one message.

    Handles both shapes the truncation budget might see:
      - Pre-expansion: `attachments: ["path/to/img.jpg", ...]` field
        with file paths. The truncator runs before
        _expand_attachments_for_provider, so this is the common case.
      - Post-expansion: `content` list containing
        `{type: "image_url", image_url: {...}}` blocks. Seen by the
        post-send calibration step which runs after expansion.

    Older user turns whose images will be dropped by image_history_keep
    are still counted here — slight over-estimate, kept simple because
    duplicating the keep-window logic at truncation time is brittle
    and over-estimating is the safe direction for budget headroom."""
    if not isinstance(m, dict):
        return 0
    count = 0
    atts = m.get("attachments")
    if isinstance(atts, list):
        count += sum(1 for a in atts if isinstance(a, str) and a)
    c = m.get("content")
    if isinstance(c, list):
        for b in c:
            if isinstance(b, dict) and ("image_url" in b or b.get("type") == "image_url"):
                count += 1
    return count


def _message_text(m):
    """Stringify a message's content (handles plain string or content-block list).

    Also concatenates any `tool_calls[*].function.arguments` text on
    assistant messages. A `write_file`'s arguments JSON can be
    thousands of characters — without including them here, _est_tokens
    undercounts a tool-using kin's history substantially and
    truncation lets the real prompt run over num_ctx."""
    c = m.get("content", "")
    if isinstance(c, str):
        text = c
    elif isinstance(c, list):
        # OpenAI/Anthropic-style content blocks
        parts = []
        for b in c:
            if isinstance(b, dict):
                parts.append(b.get("text") or b.get("content") or "")
        text = "".join(parts)
    else:
        text = str(c)
    # feed_thinking kin carry a `thinking` field on assistant turns
    # that ships in the request (it's in _API_MESSAGE_FIELDS); count
    # it so the truncation budget doesn't undercount reasoning-heavy
    # history.
    th = m.get("thinking")
    if isinstance(th, str):
        text += th
    # Tool-call assistant turns carry their arguments in tool_calls;
    # count those too so tool-loop history doesn't undercount.
    tc = m.get("tool_calls")
    if isinstance(tc, list):
        for call in tc:
            if not isinstance(call, dict):
                continue
            fn = call.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                text += args
            elif isinstance(args, dict):
                try:
                    text += json.dumps(args)
                except Exception:
                    pass
    return text


def _estimate_tools_overhead(tools):
    """Rough est-token cost of the tools= parameter on a chat() call.

    Tool schemas are passed alongside messages but aren't IN the
    message list, so _est_tokens never sees them. On a tool-heavy
    kin the schemas total 10-25k characters easily — missing that
    overhead is the load-bearing cause of tool-loop calls
    overshooting num_ctx by 2-3x. Estimate by JSON-serializing
    each schema and adding a small per-tool wrapper allowance."""
    if not tools:
        return 0
    total_chars = 0
    for t in tools:
        try:
            total_chars += len(json.dumps(t))
        except Exception:
            total_chars += 200  # conservative fallback
    return (total_chars // _CHARS_PER_TOKEN) + 30 * len(tools)


def _truncate_messages(messages, max_tokens):
    """Drop oldest non-system messages until under the cap. Prepends a marker.

    Returns (truncated_messages, was_truncated).
    """
    if not max_tokens:
        return messages, False
    if _est_tokens(messages) <= max_tokens:
        return messages, False

    # Hoist only the LEADING contiguous run of system messages — the
    # soul / base-prompt block every surface prepends. Inline
    # role=system notes spliced mid-conversation (tool-history
    # compaction one-liners, empty-reply salvage notes, "your previous
    # reply contained..." correctives) are position-sensitive: they
    # refer to the turns around them, so they stay in `others` in
    # place and are droppable by the trim loop like any old message.
    # The old shape pulled EVERY system message to the top, reordering
    # those notes ahead of the conversation they annotate on every
    # send for a capped long-running kin.
    split = 0
    while (split < len(messages)
           and isinstance(messages[split], dict)
           and messages[split].get("role") == "system"):
        split += 1
    system = list(messages[:split])
    others = list(messages[split:])

    # Per-message estimates computed once, kept as a running total —
    # the old loop re-ran _est_tokens over the whole list (re-
    # serializing every tool-call args blob) per dropped message,
    # O(N²) on a large archive. Track raw char/image counts
    # (not per-message token counts) so the running total matches
    # _est_tokens' sum-then-divide arithmetic exactly.
    other_chars = [len(_message_text(m)) for m in others]
    other_images = [_message_image_count(m) for m in others]
    total_chars = sum(len(_message_text(m)) for m in system) + sum(other_chars)
    total_images = (sum(_message_image_count(m) for m in system)
                    + sum(other_images))

    i = 0  # index of the first surviving message in `others`
    n = len(others)

    def _drop_one():
        nonlocal i, total_chars, total_images
        m = others[i]
        total_chars -= other_chars[i]
        total_images -= other_images[i]
        i += 1
        return m

    def _running_est():
        return (total_chars // _CHARS_PER_TOKEN
                + total_images * _IMAGE_TOKEN_ESTIMATE)

    # Hysteresis for prefix-cache stability. Trimming to the TIGHTEST fit
    # (drop until just under cap) advances the kept window's start by ~one
    # turn every message — so the prompt prefix SHIFTS every turn, and the
    # backend's prefix KV-cache (Ollama's implicit reuse; Anthropic's
    # cache_control) can't match it, forcing a full re-prefill of the
    # whole context every single message. On a local model that's the
    # difference between ~2s and several MINUTES per turn (measured 235x
    # on qwen36-opus-q4: 376s cold vs 1.6s on a stable prefix).
    #
    # Instead, drop a QUANTIZED chunk: enough to get under cap PLUS round
    # the dropped amount up to a CHUNK multiple. Because that multiple is
    # derived from the current excess, small per-turn growth doesn't change
    # it — the window's start stays put (identical prefix → cache hit) for
    # many turns, and only jumps forward (one slow re-prefill turn) when the
    # conversation grows a full CHUNK past the cap. Kept size then oscillates
    # in (max_tokens - CHUNK, max_tokens), always under cap. This is a
    # SEND-TIME view only: conversation.jsonl and the distillation bookmark
    # (distill_offsets) are untouched — distillation reads the full stored
    # conversation, so no turn is lost to memory by this trimming.
    _excess = _running_est() - max_tokens
    _CHUNK = max(2000, max_tokens // 5)
    _drop_target = (_excess // _CHUNK + 1) * _CHUNK
    _start_est = _running_est()

    # Drop pairs from the front until we've dropped a full quantized chunk
    # (or only 2 messages left). The pairing matters: a lone user turn with
    # no assistant reply at the top reads as "the user just said this," and
    # a lone assistant tool_calls turn with no following tool result is
    # malformed history (Ollama 400s on it; OpenAI/Anthropic hallucinate).
    # So when we drop a user turn we
    # also drop its assistant reply; when we drop an assistant turn that
    # had tool_calls we also drop the role=tool messages that were its
    # responses, all the way until we hit a non-tool turn.
    while (_start_est - _running_est()) < _drop_target and n - i > 2:
        dropped = _drop_one()
        # User → drop the assistant reply with it, if present
        if dropped.get("role") == "user" and i < n and others[i].get("role") == "assistant":
            dropped = _drop_one()
        # If the most-recently-dropped turn was an assistant with tool_calls,
        # drop the tool result messages that followed it, otherwise they're
        # orphaned (tool result with no parent assistant turn). Stop at the
        # first non-tool message.
        if dropped.get("role") == "assistant" and dropped.get("tool_calls"):
            while i < n and others[i].get("role") == "tool":
                _drop_one()
        # Also: if the truncation already cut the parent assistant turn in
        # a previous iteration and left tool messages at the top, sweep
        # those orphan tool turns now. (Defensive — shouldn't usually happen
        # given the above, but `others[i]` could start as tool if some
        # earlier code path produced that shape.)
        while i < n and others[i].get("role") == "tool":
            _drop_one()
    others = others[i:]

    # Use role=system so the model reads this as framework annotation, not as
    # the user's most recent message. Previously this was role=user, which
    # caused models to respond *to* the marker — explaining "context limits"
    # to the user as if they'd asked about it. The `[hearthkin:` prefix makes
    # the source evident even after providers concatenate system messages.
    # Marker text lives in the editable ~/.hearthkin/prompts/
    # rolling_window_marker.md (seeded from DEFAULT_ROLLING_WINDOW_MARKER).
    # Local import avoids a module-load cycle with kin_persistence.
    from kin_persistence import load_app_prompt
    marker = {
        "role": "system",
        "content": load_app_prompt("rolling_window_marker"),
    }
    return system + [marker] + others, True


# ─── Dispatch ────────────────────────────────────────────────────────────────

def chat(
    model,
    messages,
    options=None,
    *,
    think=False,
    think_effort=None,
    show_thinking=True,
    stream=True,
    tools=None,
    tool_executor=None,
    cache=False,
    cache_ttl="auto",
    openrouter_provider=None,
    keep_alive=None,
    max_context_tokens=None,
    kin_name=None,
    surface=None,
    ollama_host=None,
):
    """Dispatch to Ollama or OpenRouter based on model name prefix.

    See module docstring for parameter semantics.

    `kin_name` is consumed by the attachment-expansion pass so
    relative `attachments` paths on user messages can be resolved
    inside the kin's directory. Optional — callers without
    attachments (cron, distillation, etc.) can leave it None.

    `surface` is a free-form short string identifying which code
    path called us — "desktop", "room", "telegram-dm",
    "telegram-group", "telegram-tool", "distill", "cron",
    "consolidate", etc. Only used for the usage.log line so the
    operator can see WHERE billable calls came from. Default
    "unknown" gets written when callers don't set it. Both blocking
    and streaming paths log usage + feed token calibration — the
    streaming path via _stream_with_usage, which catches the
    provider's final usage frame off the last Chunk.
    """
    # BEFORE truncation, deliberately — see the note on the function itself.
    # `_truncate_messages` hoists the leading contiguous run of system messages
    # to protect the system prompt, and drops the oldest of what's left. When
    # what's left happens to BEGIN with one of Hearthkin's own `[hearthkin: ...]`
    # notes, that note becomes contiguous with the system block and is then
    # indistinguishable from the system prompt to everything downstream —
    # including the pass below, which would leave it there, and the fold, which
    # would merge it into message 0.
    #
    # Measured on a real kin after the first version of this fix shipped: the
    # system block alternated between 14,002 and 14,301 characters turn to turn
    # — a park receipt joining and leaving the leading run as truncation moved —
    # for a steady reuse=0% first-change=msg 0. The fix worked; truncation was
    # putting the note back.
    #
    # Re-roling first means truncation sees these notes for what they are:
    # ordinary, droppable, mid-conversation turns. Nothing can promote one into
    # the system prompt by accident afterwards.
    messages = _inline_mid_conversation_system_notes(messages)

    _cal_est_sent = None
    if max_context_tokens:
        # _truncate_messages trims by _est_tokens (message text only),
        # which runs lower than the real provider token count. Divide
        # the caller's cap by this kin's measured real/estimate ratio
        # so the real billed prompt lands near the cap, not well over
        # it. The ratio is learned from prior calls' reported usage;
        # the default seeds the first call. See
        # _update_token_calibration.
        _load_calibration(kin_name)
        ratio = _token_calibration.get(kin_name) or _DEFAULT_TOKEN_RATIO
        est_budget = max_context_tokens / ratio
        if tools:
            # Tool schemas ride alongside messages in the request
            # but aren't IN the message list — _est_tokens misses
            # them. Subtract that overhead from the message budget.
            est_budget -= _estimate_tools_overhead(tools)
        # max_context_tokens is sized by the caller assuming a
        # ~2000-token response reserve. If num_predict is bigger
        # than that (tool-loop floors to TOOL_LOOP_MIN_OUTPUT_TOKENS,
        # but a caller could also set a large num_predict for a
        # non-tool path), reclaim the additional reserve from the
        # input budget so prompt + response together fit num_ctx.
        # Audit L24 — previously only tools triggered this reclaim,
        # leaving non-tool paths with large num_predict overbudget.
        effective_num_predict = 0
        if isinstance(options, dict):
            try:
                effective_num_predict = int(options.get("num_predict") or 0)
            except (TypeError, ValueError):
                effective_num_predict = 0
        # Only when something in this turn's tools can actually emit a long
        # argument. See _needs_large_output_reserve: applying it to every
        # tool-capable turn overrode the person's own configured reply cap and
        # took a fifth of the window away from the conversation to hold room
        # for a file write that was never going to happen.
        if tools and _needs_large_output_reserve(tools):
            effective_num_predict = max(
                effective_num_predict, TOOL_LOOP_MIN_OUTPUT_TOKENS)
        # A reply reserve must never be able to eat the window it sits in.
        # The tool loop floors num_predict at 8,000 so a write_file's
        # content argument can't be cut off mid-JSON — sound on a big
        # window, ruinous on a small one. On a kin at num_ctx 8192 the
        # caller's cap arrives here as ~6,192, the 8,000 floor reclaims
        # every token of it, and the budget collapses to the 2,048 floor
        # below — which is LESS than that kin's system prompt, so the
        # trim drops the entire conversation and the model answers from
        # its soul alone, remembering nothing. Observed live 2026-08-06:
        # 2,852 prompt tokens sent, of which 2,849 were the system block.
        # It looks exactly like a model being vacant, and nothing says
        # otherwise.
        #
        # So the reserve is capped at half the window, and — this is the
        # load-bearing half — num_predict itself is capped to MATCH.
        # Reserving less than we then let the model generate would push
        # prompt+reply past num_ctx, and an overrun on local Ollama
        # returns nothing at all rather than degrading.
        _reserve_ceiling = max(_RESPONSE_RESERVE_TOKENS,
                               int(max_context_tokens * 0.5))
        if effective_num_predict > _reserve_ceiling:
            _log_reserve_clamped(kin_name, surface, effective_num_predict,
                                 _reserve_ceiling, int(max_context_tokens))
            effective_num_predict = _reserve_ceiling
            options = dict(options or {})
            options["num_predict"] = _reserve_ceiling
        # How much room to hold back for the reply -- which is NOT the same
        # question as how long this turn's reply may be.
        #
        # It used to be the same number, and that was the whole bug. A kin
        # that can write files reserves 8,000 tokens on a tool turn and its
        # configured cap (1,024, say) on a plain one, so the room left for the
        # conversation changed between one turn and the next. The far end of
        # the history moved with it, and a history whose far end moves is
        # re-read from cold every time -- minutes of silence before a reply,
        # with nothing on screen saying why. Measured on a real kin: a fifth
        # of the window taken away and handed back, turn after turn, all day.
        #
        # So the reserve follows the KIN, not the call: if this kin can write
        # files at all, every one of its turns holds the same room, and the
        # trim point stops moving. `effective_num_predict` is untouched, so a
        # plain reply is still as short as the person asked for -- only the
        # bookkeeping is made consistent.
        reserve_tokens = effective_num_predict
        if _kin_may_emit_large_argument(kin_name):
            reserve_tokens = min(_reserve_ceiling,
                                 max(reserve_tokens, TOOL_LOOP_MIN_OUTPUT_TOKENS))
        extra_reserve_real = max(0, reserve_tokens - _RESPONSE_RESERVE_TOKENS)
        if extra_reserve_real:
            est_budget -= extra_reserve_real / ratio
        # Floor at 2048 (audit L11) — the prior 512 floor could trip
        # in pathological cases (small num_ctx, high calibration ratio,
        # heavy tool schemas) and drop every non-system turn except
        # the most recent. 2048 still survives an extreme combination
        # but leaves room for at least a few historical turns.
        #
        # Quantized and held still before it reaches the trim (see
        # _stable_truncation_budget): the calibration ratio moves a
        # little on every single call, and passing that straight through
        # moves the truncation point on every single call, which throws
        # the whole prompt cache away each turn.
        est_budget = _stable_truncation_budget(
            (kin_name or "", surface or "", int(max_context_tokens)),
            max(2048, int(est_budget)))
        _before_trim = messages
        messages, _ = _truncate_messages(messages, est_budget)
        # A kin must never be sent its soul with NO conversation at all.
        #
        # The trim drops oldest-first and stops with two messages left,
        # but it checks that guard only at the top of each pass — and one
        # pass can drop a user turn, its assistant reply, and a whole run
        # of tool results. So it can and does overshoot to zero. When it
        # does, the request that goes out is the system prompt and
        # nothing else: the model has no idea who is talking, what was
        # asked, or that a conversation is in progress. It answers from
        # its persona alone, which reads from the outside as the kin
        # having gone vacant — and every reply after it, too, because the
        # condition doesn't clear on its own.
        #
        # The most recent user turn is put back. It may push the request
        # slightly over the budget; that is the better failure by a wide
        # margin, because "slightly too long" degrades and "no question
        # at all" cannot possibly work. Loud, because the person cannot
        # see it from a chat window: this is exactly the shape that gets
        # blamed on the model for weeks.
        if _before_trim and not _has_conversation(messages):
            restored = _last_user_turn(_before_trim)
            if restored is not None:
                messages = list(messages) + [restored]
            _log_context_starved(kin_name, surface, est_budget,
                                 _est_tokens(messages),
                                 restored is not None)
        # Pre-send estimate, paired after the call with the real
        # prompt_tokens to refine this kin's ratio. Include the tool-
        # schema overhead: the provider's reported prompt_tokens
        # covers the schemas, so the estimate must too — otherwise
        # the learned ratio bakes the schemas in AND the budget math
        # above subtracts _estimate_tools_overhead again, double-
        # charging tool-heavy kin and over-truncating real history.
        _cal_est_sent = _est_tokens(messages)
        if tools:
            _cal_est_sent += _estimate_tools_overhead(tools)

    # Fill in any BLANK tool-call ids before sending to OpenRouter.
    # Ollama returns tool calls without an id, so a kin that used tools
    # locally has `id: ""` / `tool_call_id: ""` all through its stored
    # history. OpenAI (via OpenRouter, on both the OpenAI and Azure
    # routes) rejects that outright — "Invalid 'input[N].call_id':
    # empty string" — so the kin can't send its own past at all. Fills
    # only the blanks, pairs them by position, and leaves any id a
    # provider actually gave us alone. No-op on Ollama, where blank is
    # both normal and accepted.
    if _is_openrouter_model(model):
        # Structural repair first: an unpaired half of a tool round-trip
        # is a 400 on OpenAI whether its id is empty or filled in, so
        # completing the pairing has to happen before ids are minted —
        # otherwise a result whose call has gone just trades "empty
        # string" for "no tool call found for call_id ...".
        messages = _repair_tool_pairing(messages)
        messages = _fill_blank_tool_call_ids(messages)
    # Rewrite tool_call_ids to Mistral's 9-char format when sending to
    # any mistralai/* model. Anthropic Bedrock IDs (`toolu_bdrk_...`,
    # 36+ chars) and OpenAI's longer IDs are silently truncated by
    # Mistral's API to their 9-char prefix, collapsing many distinct
    # calls onto duplicates ("Duplicate tool call id in assistant
    # message", error 3230). Rewrites assistant→tool pairings together
    # so they stay matched. No-op on non-Mistral models — Anthropic,
    # OpenAI, Ollama all accept the longer formats unchanged.
    if _is_mistral_model(model):
        messages = _remap_tool_call_ids_for_mistral(messages)
    # Coerce any tool_calls in history to the active provider's required shape
    # for `function.arguments`. Ollama's server rejects string-args with 400:
    # "Value looks like object, but can't find closing '}' symbol". OpenRouter
    # rejects dict-args. Either shape can land on disk (cross-provider kin,
    # raw-HTTP fallback in older builds), so we always normalize here.
    messages = _normalize_history_tool_args(messages, model)
    # Coerce content=None to content="" on assistant turns that carry
    # tool_calls. The OpenAI streaming spec permits null content
    # there, but Anthropic-via-OpenRouter treats it as a structural
    # defect and the FOLLOWING turn's output degenerates — semantic
    # chain walks, repetition runs — the degenerate output seen
    # after every tool call. Any provider
    # (Anthropic, OpenAI, Ollama) tolerates "" as the content there,
    # so it's the safe universal shape. Old records on disk with
    # null content also get fixed by this pass before going out.
    messages = _coerce_tool_call_assistant_content(messages)
    # Expand `attachments` references to provider-shaped image inputs.
    # Ollama gets per-message `images: [base64]`; OpenRouter gets
    # content-block lists with `image_url` entries. Messages without
    # attachments pass through by reference. Done after the other
    # normalizations so they see clean text content first.
    #
    # Image-history cap: only the most recent N image-bearing user
    # turns send their image bytes; older image turns send their
    # caption text only. Looked up from the kin's config so callers
    # don't have to pass the value through every chat() call. Falls
    # back to a sensible default when the lookup fails.
    if model_supports_images(model):
        image_keep = _resolve_image_history_keep(kin_name)
        messages = _expand_attachments_for_provider(
            messages, model, kin_name, image_history_keep=image_keep,
        )
    else:
        # Model can't see images — strip the field rather than ship
        # a payload that the provider will reject (or worse, silently
        # ignore). The text still goes through.
        if any(isinstance(m, dict) and m.get("attachments") for m in messages):
            messages = [
                ({k: v for k, v in m.items() if k != "attachments"}
                 if isinstance(m, dict) and "attachments" in m else m)
                for m in messages
            ]

    # Drop any per-message bookkeeping (`ts`, `source`, `sender_id`,
    # `sender_name`, `sender_attribution`, `speaker`, `model`, etc.)
    # the caller carried over from storage. Mistral via OpenRouter
    # rejects unknown keys with a generic 400; other providers tolerate
    # them silently. Attribution + timestamps the kin reads are already
    # inlined into `content` at message-build time on every surface, so
    # this strip loses nothing the model sees. Runs LAST so prior
    # normalizations (tool-args coercion, null-content coercion,
    # attachment expansion) see the original storage fields if they
    # need to inspect them.
    messages = _strip_extra_message_fields(messages)

    # `thinking` stays whitelisted in _API_MESSAGE_FIELDS because
    # Ollama legitimately accepts it (feed_thinking round-trips the
    # reasoning back) and the OpenAI-shape providers ignore unknown
    # message keys. Mistral does NOT — its strict field validation
    # 400s on every send for a feed_thinking kin — so drop the field
    # for mistralai/* models specifically.
    if _is_mistral_model(model):
        messages = [
            ({k: v for k, v in m.items() if k != "thinking"}
             if isinstance(m, dict) and "thinking" in m else m)
            for m in messages
        ]

    # Some Ollama model chat templates — notably certain Qwen GGUF Jinja
    # templates (e.g. `qwen36-opus-q4`) — raise "System message must
    # be at the beginning." if a system message appears anywhere but first.
    # Hearthkin legitimately inserts mid-conversation `[hearthkin: ...]`
    # system notes: the truncation marker (`_truncate_messages`), cap-full
    # markers, salvage notes. Fold every system message into one leading
    # block so those strict templates accept the request. A single leading
    # system message is also the most universally-compatible shape, so it's
    # harmless for well-behaved models. Gated to the Ollama path because
    # OpenRouter concatenates system messages into the provider's single
    # top-level system field server-side already.
    # Keep Hearthkin's own mid-conversation `[hearthkin: ...]` notes where they
    # happened instead of letting them be hoisted to the front of the prompt,
    # which re-read the whole context every turn. Runs for EVERY provider: the
    # fold below is Ollama-only, but OpenRouter concatenates system messages
    # into the provider's single top-level system field server-side, so the
    # same prefix-invalidation happens there — just out of our sight.
    # See docs/design/prompt-cache-system-fold.md.
    messages = _inline_mid_conversation_system_notes(messages)
    # Collapse adjacent user turns — some local templates (qwen36-opus-q4)
    # return empty for consecutive user turns, which snowballs failed crons
    # into a dead loop. Now runs for every provider rather than Ollama-only,
    # because the re-roling above is what can put two user turns side by side
    # (a note appended after a reply, then the next real turn). No-op by
    # reference when no adjacent user turns exist, which is the OpenRouter
    # case whenever nothing was re-roled.
    messages = _collapse_consecutive_user_turns(messages)

    if not _is_openrouter_model(model):
        messages = _consolidate_system_messages(messages)
        # Ensure a user turn survives — the same qwen36-opus-q4 template
        # raises "No user query found in messages." (a 400 "Unable to
        # generate parser for this template") when the sent list has no
        # plain-string user turn, which a tool-loop continuation can produce
        # after truncation drops the query. Ollama-only; runs after truncation
        # so it catches a user turn dropped by the trim above.
        messages = _ensure_user_turn_present(messages)
        # Diagnostic (always-on, cheap): fingerprint the FINAL Ollama prompt
        # so we can see, turn to turn, whether the prefix is byte-stable
        # (→ Ollama reuses its KV cache, fast) or shifting (→ full re-prefill
        # on a warm model — the Opal slowness). Diff two consecutive lines
        # for a kin; the first message whose hash changed is what's busting
        # the cache. See _log_prompt_fingerprint.
        _log_prompt_fingerprint(kin_name, surface, model, messages)

    # Resolve thinking-effort tier. think_effort is the new four-state
    # field ("off" / "low" / "medium" / "high"); think is the legacy
    # boolean kept for backward compat with callers that haven't been
    # updated yet. If think_effort is None, derive from think.
    if think_effort is None:
        think_effort = "medium" if think else "off"
    if think_effort not in ("off", "low", "medium", "high"):
        think_effort = "off"
    # Ollama's API has no level concept — anything but "off" is just
    # think=True. The level distinction matters only for hosted models
    # via OpenRouter (Claude reasoning, OpenAI o-series, etc.).
    think_bool = (think_effort != "off")

    # Build the per-call metadata bundle once and reuse for both the
    # blocking-return and the streaming-iterator paths. Every add-on
    # call-accounting signal (token estimate, context cap, future
    # per-call data) goes on _CallContext rather than growing the
    # parameter list of _log_call_usage / _stream_with_usage.
    ctx = _CallContext(
        kin_name=kin_name,
        model=model,
        surface=surface or "unknown",
        est_sent=_cal_est_sent,
        max_context_tokens=max_context_tokens,
    )

    if _is_openrouter_model(model):
        if stream:
            # The streamed iterator carries the provider's usage frame on
            # its final Chunk; _stream_with_usage logs + calibrates off
            # it as the stream completes, matching the blocking path.
            return _stream_with_usage(
                _chat_openrouter_stream(model, messages, options, think_effort, tools, cache, show_thinking, cache_ttl, openrouter_provider),
                ctx)
        result = _chat_openrouter_blocking(model, messages, options, think_effort, tools, cache, show_thinking, cache_ttl, openrouter_provider)
        _log_call_usage(ctx, result.usage)
        return result

    # Ollama path: model is a plain name like "qwen2.5:7b-instruct"
    # When the caller didn't explicitly pass keep_alive, fall back to
    # the kin's configured value so per-kin Settings actually apply
    # without every call site having to look it up. Same pattern as
    # _resolve_image_history_keep for image_history_keep.
    effective_keep_alive = keep_alive
    if effective_keep_alive is None:
        effective_keep_alive = _resolve_keep_alive(kin_name)
    # Per-kin wall-clock cap for this request — reads the SAME
    # `watchdog_timeout_minutes` setting the streaming UI watchdog uses, so
    # one knob governs streaming chats, tool-loop chats, and crons alike.
    req_secs = _resolve_watchdog_timeout_secs(kin_name, model, ctx.surface)
    if stream:
        return _stream_with_usage(
            _chat_ollama_stream(model, messages, options, think_bool, tools, effective_keep_alive, ollama_host=ollama_host, request_timeout_secs=req_secs),
            ctx)
    try:
        result = _chat_ollama_blocking(model, messages, options, think_bool, tools, effective_keep_alive, ollama_host=ollama_host, request_timeout_secs=req_secs)
    except Exception as e:
        # Wall-clock guard tripped (or any other failure): if it was a
        # request timeout, record it to hang_watchdog.log with the surface
        # label so a wedged cron or chat is visible after the fact, then
        # let the surface handle the error normally (show [error], end the
        # turn, clear "typing"). Without the guard this call could block the
        # worker — and every same-host kin queued behind it — indefinitely.
        if _is_request_timeout(e):
            _log_hang_watchdog(ctx.surface, kin_name, model, e, req_secs)
        raise
    _log_call_usage(ctx, result.usage)
    return result


def _cached_tokens_from_usage(usage):
    """Pull the cache-read token count out of a provider usage object.
    OpenRouter / OpenAI nest it at prompt_tokens_details.cached_tokens
    (the OpenAI-standard shape); some shapes carry cached_tokens at the
    top level, and Anthropic-native passthrough uses
    cache_read_input_tokens. Check every known spot. Returns 0 when none
    are present — which also legitimately covers a cold cache (the first
    call after an idle gap reads nothing; the cache gets written, not
    read). Across a run of messages, a steady 0 means caching isn't
    working; 0-then-nonzero means it just warmed up."""
    if not isinstance(usage, dict):
        return 0
    candidates = []
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        candidates.append(details.get("cached_tokens"))
    candidates.append(usage.get("cached_tokens"))
    candidates.append(usage.get("cache_read_input_tokens"))
    for value in candidates:
        if value:
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
    return 0


def _merge_streaming_usage(existing, event):
    """Combine streaming usage frames so we keep the best info from
    each. OpenRouter sometimes sends usage on a non-terminal SSE
    event and again on the terminal frame; without merging, the later
    frame unconditionally overwrites the earlier and any field it
    lacks (e.g. cost) is lost (audit L6).

    Rule: a frame carrying `prompt_tokens > 0` is treated as
    authoritative for token counts. Earlier-frame cost is preserved
    if the new frame lacks one. Frames with no `usage` at all leave
    `existing` untouched."""
    if not event.get("usage"):
        return existing
    new_usage = _usage_with_cost(event)
    if not new_usage:
        return existing
    is_authoritative = False
    try:
        is_authoritative = int(new_usage.get("prompt_tokens") or 0) > 0
    except (TypeError, ValueError):
        pass
    if is_authoritative:
        merged = dict(new_usage)
        # Preserve cost if an earlier frame had it and this one doesn't.
        if (existing
                and existing.get("cost") is not None
                and merged.get("cost") is None):
            merged["cost"] = existing["cost"]
        # Same for the serving provider: OpenRouter names it on the FIRST
        # frame, while the authoritative token counts arrive on the last, so
        # taking the last frame wholesale loses it every time.
        if (existing
                and existing.get("provider")
                and not merged.get("provider")):
            merged["provider"] = existing["provider"]
        return merged
    # Non-authoritative frame: carry cost and provider forward.
    if new_usage.get("cost") is not None or new_usage.get("provider"):
        merged = dict(existing or {})
        if new_usage.get("cost") is not None:
            merged["cost"] = new_usage["cost"]
        if new_usage.get("provider"):
            merged["provider"] = new_usage["provider"]
        return merged
    return existing


def _usage_with_cost(data):
    """Return the response's usage dict with the real per-call cost
    normalised under `cost` (a float). OpenRouter reports the actual
    cost of every call, but it may sit inside the usage object or at
    the response top level — check both, plus a cost_details nest, so
    downstream code sees it regardless. `cost` is left absent when
    nothing usable is found (Ollama local calls, or a provider that
    didn't report it) — callers then fall back to the estimate.

    Works for a blocking response dict (data['usage']) and for a
    streaming SSE event (event['usage']) — same shape."""
    if not isinstance(data, dict):
        return {}
    usage = dict(data.get("usage") or {})
    raw = usage.get("cost")
    if raw is None:
        cd = usage.get("cost_details")
        if isinstance(cd, dict):
            raw = cd.get("total_cost", cd.get("cost"))
    if raw is None:
        raw = data.get("cost", data.get("total_cost"))
    if raw is not None:
        try:
            usage["cost"] = float(raw)
        except (TypeError, ValueError):
            pass
    # WHICH upstream actually served this call. OpenRouter fans one model name
    # out across several providers, and they do not serve it identically —
    # different chat templates, different sampler defaults, and some prepend
    # framing of their own. So the same model can come back in a noticeably
    # different register from one call to the next, and nothing recorded which
    # one you got: the field was in every response and thrown away. Without it
    # "this model has started hedging" cannot be told from "this provider has".
    # Top level on OpenRouter; absent on Ollama, which leaves it unset.
    prov = data.get("provider")
    if isinstance(prov, str) and prov.strip():
        usage["provider"] = prov.strip()
    return usage


# Last fingerprint per (kin, surface, model), so each line can say how much of
# the prompt was reusable rather than leaving that to be worked out by hand.
# In-memory and unbounded only by the number of kin × surfaces, which is tiny.
_LAST_FINGERPRINT = {}


def _prefix_reuse(prev_parts, parts):
    """How much of this prompt is byte-identical to the last one, from the start.

    Returns (percent_reusable, index_of_first_change) — or (None, None) with no
    previous prompt to compare against.

    This is the number that matters and the one nothing surfaced. A local model
    reuses its cached work only for an unbroken run from the very beginning, so
    "how much changed" is the wrong question; "how far in did the FIRST change
    happen" is the right one. A turn that appends one short message reuses ~99%
    and starts replying in seconds. A turn that alters message 1 reuses ~0% and
    re-reads twenty thousand tokens from cold — about five minutes — even though
    the conversation looks almost identical.

    Measured in characters rather than messages on purpose: the first change
    landing at message 89 of 92 sounds nearly free, and is, but the same
    position in a conversation whose bulk sits at the end would not be.
    """
    if not prev_parts or not parts:
        return None, None
    shared = 0
    for a, b in zip(prev_parts, parts):
        if a != b:
            break
        shared += 1

    def _chars(items):
        total = 0
        for p in items:
            bits = p.split(":")
            if len(bits) >= 3 and bits[2].isdigit():
                total += int(bits[2])
        return total

    total = _chars(parts)
    if total <= 0:
        return None, None
    pct = round(100.0 * _chars(parts[:shared]) / total)
    # 100% must mean "nothing changed", not "the change rounded away". A tiny
    # new message against a large context really is 99.6% reusable, but a
    # readout saying 100% next to a first-change position reads as a
    # contradiction, and the whole point of this line is to be read quickly.
    if pct >= 100 and shared < len(parts):
        pct = 99
    return pct, shared


def _keep_system_prompt_pair(kin_name, surface, messages):
    """Keep the last TWO DIFFERENT system prompts for this kin+surface, so the
    pair can be diffed to see exactly what changed.

    The fingerprint log records message 0's size and hash, which tells you THAT
    the system block changed but never WHAT changed — and reconstructing it from
    its parts (base prompt, soul, memory, harness prompts) left thousands of
    characters unaccounted for. Recording it removes the guesswork: the system
    prompt is a concatenation of files already sitting in the kin's own folder,
    so keeping a copy discloses nothing new.

    Only a CHANGE is written. An unchanged prompt costs one hash and no I/O, and
    the two files on disk are therefore always a before/after of a real change
    rather than two arbitrary turns:

        logs/system_prompts/<kin>--<surface>.txt        (current)
        logs/system_prompts/<kin>--<surface>.prev.txt   (the one before it)

    Two files per kin per surface, overwritten in place — this does not grow.
    Everything is swallowed; a diagnostic must never cost a reply."""
    try:
        import hashlib
        import re as _re
        import shutil as _shutil
        from kin_persistence import LOGS_DIR
        first = messages[0] if messages else None
        if not isinstance(first, dict) or first.get("role") != "system":
            return
        text = first.get("content")
        if not isinstance(text, str) or not text:
            return
        digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
        if _SYSTEM_PROMPT_SEEN.get((kin_name, surface)) == digest:
            return                      # unchanged — nothing to record
        _SYSTEM_PROMPT_SEEN[(kin_name, surface)] = digest

        # Separators can't survive this, so the name can never escape the
        # folder. Leading dots are stripped too — harmless as characters, but a
        # file called `..something` reads as a mistake and hides on POSIX.
        safe = _re.sub(r"[^A-Za-z0-9_.-]", "_",
                       f"{kin_name or 'kin'}--{surface or 'surface'}")
        safe = safe.lstrip(".") or "kin--surface"
        folder = LOGS_DIR / "system_prompts"
        folder.mkdir(parents=True, exist_ok=True)
        current = folder / f"{safe}.txt"
        if current.exists():
            _shutil.copyfile(current, folder / f"{safe}.prev.txt")
        current.write_text(text, encoding="utf-8")
    except Exception:
        pass


# Last system-prompt hash per (kin, surface) — so an unchanged prompt costs a
# hash and no disk write at all.
_SYSTEM_PROMPT_SEEN = {}


def _log_prompt_fingerprint(kin_name, surface, model, messages):
    """Always-on diagnostic: one line per Ollama call recording a per-message
    fingerprint (index:role:content-length:sha1[:8]) of the FINAL messages
    sent to the model.

    Diffing two consecutive lines for the same kin reveals the FIRST message
    whose hash changed turn-to-turn — i.e. exactly what is shifting the prompt
    prefix and defeating Ollama's KV-cache reuse (which forces a full
    re-prefill even on a warm model — the observed Opal slowness). If message
    0 (the system block) changes every turn, the culprit is something dynamic
    baked into the system prompt; if only tail messages change, the prefix is
    stable and the slowness is elsewhere. Cheap (sha1 of already-in-memory
    strings); all failures swallowed — diagnostics must never break a send."""
    try:
        import datetime as _dt
        import hashlib
        from kin_persistence import LOGS_DIR
        parts = []
        for i, m in enumerate(messages):
            if not isinstance(m, dict):
                continue
            role = m.get("role", "?")
            content = m.get("content")
            text = (content if isinstance(content, str)
                    else json.dumps(content, sort_keys=True, default=str))
            tcs = m.get("tool_calls")
            if tcs:
                text += json.dumps(tcs, sort_keys=True, default=str)
            h = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:8]
            parts.append(f"{i}:{role}:{len(text)}:{h}")
        ts = _dt.datetime.now().isoformat(timespec="seconds")
        # Compare against this kin+surface's previous prompt and put the answer
        # ON the line. The raw fingerprints have always held it; reading it
        # meant diffing two lines by hand, so in practice nobody ever did, and
        # a conversation re-reading itself from cold every turn looked exactly
        # like a slow model.
        key = (kin_name or "?", surface or "?", model or "?")
        reuse, first_change = _prefix_reuse(_LAST_FINGERPRINT.get(key), parts)
        _LAST_FINGERPRINT[key] = parts
        if reuse is None:
            summary = "reuse=? (first prompt seen this run)"
        elif first_change >= len(parts):
            summary = "reuse=100% (nothing changed)"
        else:
            what = parts[first_change].split(":")
            where = (f"msg {first_change}"
                     + (f" ({what[1]})" if len(what) > 1 else ""))
            summary = f"reuse={reuse}% first-change={where}"
        line = (f"{ts} kin={kin_name or '?'} surface={surface or '?'} "
                f"model={model or '?'} nmsg={len(parts)} {summary} | "
                + " | ".join(parts) + "\n")
        path = LOGS_DIR / "prompt_fingerprint.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    # Separate try: the fingerprint line is the more important of the two, and
    # a failure keeping the system-prompt copy must not cost us that line.
    _keep_system_prompt_pair(kin_name, surface, messages)


def _log_call_usage(ctx, usage):
    """Write one usage.log line for a completed chat() call. Pulls
    prompt_tokens / completion_tokens from the ChatResult.usage dict,
    estimates USD cost via _estimate_call_cost, hands off to
    kin_persistence.append_usage_log. Failures are swallowed —
    logging mustn't be load-bearing on the call path.

    `ctx` is a _CallContext bundle built at the top of chat(). When
    `ctx.est_sent` (the pre-send _est_tokens value) is non-None, the
    kin's token-calibration ratio is also refreshed by pairing it with
    the provider's real prompt_tokens. Distillation and other callers
    that don't truncate pass est_sent=None and skip calibration.

    `ctx.max_context_tokens` is the caller's pre-send budget cap. When
    the provider's reported prompt_tokens lands at or above that cap,
    the calibration update treats it as a hit-the-wall event and
    recalibrates more aggressively (see _update_token_calibration's
    `num_ctx` arg)."""
    if not isinstance(usage, dict):
        usage = {}
    try:
        from kin_persistence import append_usage_log
        prompt = usage.get("prompt_tokens", 0)
        completion = usage.get("completion_tokens", 0)
        cached = _cached_tokens_from_usage(usage)
        # Provider-reported real cost when present (see _usage_with_cost);
        # est_cost is the catalogue-price estimate, kept as the fallback
        # for Ollama / when the provider doesn't report a cost.
        real_cost = usage.get("cost")
        if not isinstance(real_cost, (int, float)):
            real_cost = None
        # allow_fetch=False: this runs inside the send (the streaming
        # path logs BEFORE yielding the done chunk) — a stale-cache
        # network catalogue refresh here would stall the user's reply.
        cost = _estimate_call_cost(
            ctx.model, prompt, completion, cached_tokens=cached,
            allow_fetch=False)
        # Ollama reports prefill / generation durations in NANOSECONDS
        # (prompt_eval_duration / eval_duration). Convert to seconds for
        # the log so "how long did prefill take" — the real cache-reuse
        # signal on local models — is readable. Absent on OpenRouter → None.
        def _ns_to_secs(v):
            try:
                v = float(v)
                return (v / 1e9) if v > 0 else None
            except (TypeError, ValueError):
                return None
        prefill_secs = _ns_to_secs(usage.get("prompt_eval_duration"))
        gen_secs = _ns_to_secs(usage.get("eval_duration"))
        append_usage_log(
            kin=ctx.kin_name,
            model=ctx.model,
            prompt_tokens=prompt,
            completion_tokens=completion,
            cached_tokens=cached,
            est_cost=cost,
            real_cost=real_cost,
            surface=ctx.surface or "unknown",
            prefill_secs=prefill_secs,
            gen_secs=gen_secs,
            provider=usage.get("provider"),
        )
    except Exception:
        pass
    try:
        # max_context_tokens stands in for num_ctx for the cap-hit
        # heuristic in _update_token_calibration. Inflate slightly
        # since max_context_tokens = num_ctx - _RESPONSE_RESERVE_TOKENS,
        # and the provider's real prompt_tokens can land in the reserve
        # window without being a true overshoot.
        effective_ctx = (
            (ctx.max_context_tokens + _RESPONSE_RESERVE_TOKENS)
            if ctx.max_context_tokens else None
        )
        _update_token_calibration(
            ctx.kin_name, ctx.est_sent, usage, num_ctx=effective_ctx)
    except Exception:
        pass


def _update_token_calibration(kin_name, est_sent, usage, *, num_ctx=None):
    """Refresh a kin's real/estimate token ratio (and last real
    prompt-token count) from a completed blocking call. `est_sent` is
    _est_tokens of the messages we sent; `usage` carries the provider's
    real prompt_tokens. No-op unless both are usable — so distillation
    calls (est_sent=None) never pollute the conversational ratio.

    `num_ctx` is the kin's configured context window. When the
    provider's reported prompt_tokens lands within 200 of num_ctx, we
    treat that as a "we hit the wall" signal — the prompt overshot the
    truncation budget so badly that the provider had to cap it. In
    that case the normal slow EMA update would leave the next several
    calls also overshooting before the ratio caught up. Apply a
    panic-recalibration: heavier sample weight (0.6 instead of 0.2)
    so one cap-hit lifts the ratio fast enough that the NEXT call
    actually fits."""
    if not kin_name or not est_sent or not isinstance(usage, dict):
        return
    reported = int(usage.get("prompt_tokens") or 0)
    if reported <= 0:
        return
    _last_prompt_tokens[kin_name] = reported
    measured = reported / est_sent
    # Blend against the stored ratio, not the seed, when this process
    # hasn't read it yet — otherwise a short-lived cron run would
    # discard everything earlier runs learned.
    _load_calibration(kin_name)
    prev = _token_calibration.get(kin_name)
    # Panic recalibration when reported tokens are at or above the
    # context cap minus a small headroom — the prompt overshot so
    # badly the provider capped it. Push the ratio up faster than the
    # normal EMA so the next call doesn't repeat the same overshoot.
    hit_cap = (
        num_ctx is not None
        and num_ctx > 0
        and reported >= num_ctx - 200
    )
    if hit_cap:
        _log_context_overflow(kin_name, reported, num_ctx)
    # Reject implausible ratios — a degenerate est_sent or odd usage
    # frame shouldn't poison truncation. Real ratios sit ~1.1-1.7 for
    # most provider+tokenizer combos, but heavily-tool'd kin on dense
    # tokenizers (Mistral's Tekken especially) can push past 3.0. Cap
    # raised to 5.0 (was 3.0 — a Mistral-routed kin overshot well
    # past estimate on a fresh Mistral Small calibration).
    if not (0.8 <= measured <= 5.0):
        # A cap-hit carries a usable signal even when the derived ratio
        # is out of range: we know the window overflowed. Drop the
        # unusable number but still run the ratchet below, rather than
        # discarding the one call that most needs to move the ratio.
        if not hit_cap:
            return
        measured = prev if prev is not None else _DEFAULT_TOKEN_RATIO
    # Noise floor. Two real prompts of the same size tokenize slightly
    # differently, so without this the EMA nudges the ratio a fraction of
    # a percent every single call and never settles. The ratio divides
    # the truncation budget, so "never settles" means the trim point
    # never settles either — and a trim point that moves costs the entire
    # prompt cache, every turn, invisibly. A real change (different
    # model, different tokenizer, a kin that starts using tools) clears
    # the deadband easily; noise doesn't. A cap-hit always gets through:
    # that one is a window overflow, and it must move the ratio now.
    if (not hit_cap and prev is not None
            and abs(measured - prev) <= _CALIBRATION_DEADBAND * prev):
        return
    new_weight = 0.6 if hit_cap else 0.2
    blended = (
        measured if prev is None
        else (1 - new_weight) * prev + new_weight * measured
    )
    # A cap-hit measurement is CENSORED, and blending toward it as if it
    # were a clean sample is what makes this failure repeat. When the
    # provider clamps an oversized prompt to the window, the reported
    # count is the window size — not the size of what we actually sent.
    # So `measured` is a floor on the true ratio, not an estimate of it,
    # and it can easily land BELOW the current ratio. Blending then
    # lowers the ratio, shrinking the truncation margin immediately
    # after the window overflowed, and the next call overflows again.
    # Ratchet instead: a cap-hit may only raise the ratio, never lower
    # it, and lifts it by a step so the next prompt actually fits.
    if hit_cap and prev is not None:
        blended = max(prev * _CAP_HIT_RATIO_BUMP, blended)
    settled = min(blended, _MAX_TOKEN_RATIO)
    _token_calibration[kin_name] = settled
    # Persist on a cap-hit regardless of how small the move was: a
    # window overflow is exactly the lesson a fresh process most needs
    # to inherit, and it may be the only call that process ever makes.
    if hit_cap:
        _calibration_on_disk.pop(kin_name, None)
    _save_calibration(kin_name, settled)


def _stream_with_usage(chunks, ctx):
    """Pass a streaming Chunk iterator straight through to the caller,
    but route the provider's final usage frame (carried on the done
    Chunk's `.usage`) through _log_call_usage — the same usage.log line
    and per-kin token calibration the blocking path gets. Without this,
    streamed calls (desktop 1-on-1, room streaming) were invisible to
    usage.log and never fed the calibration ratio.

    `ctx` is the _CallContext bundle built by chat().

    Logging happens the moment the usage-bearing chunk arrives, BEFORE
    it is yielded — consumers typically `break` on the done chunk, which
    would otherwise strand any post-loop code in this generator.
    _log_call_usage is wrapped so a logging failure can't strand the
    done chunk (audit L5) — without the guard, an exception in the
    log path would skip the yield and leave the consumer waiting for
    a `done=True` it never gets, with streaming state hung."""
    logged = False
    for chunk in chunks:
        if not logged:
            usage = getattr(chunk, "usage", None)
            if usage:
                try:
                    _log_call_usage(ctx, usage)
                except Exception:
                    pass
                logged = True
        yield chunk


# Fields the OpenAI / OpenRouter chat-completions spec recognizes on
# message objects, plus `thinking` (the reasoning-feedback field we set
# when feed_thinking is on). Anything else gets dropped before send.
#
# Hearthkin stores per-turn bookkeeping (`ts`, `source`, `sender_id`,
# `sender_name`, `sender_attribution`, `speaker`, `model`, etc.) on the
# same dicts that double as API request shapes. Most providers silently
# ignore unknown keys; Mistral via OpenRouter returns a generic 400
# "provider returned error" on them. Strip before send so every
# provider sees the same clean shape.
#
# Attribution-bearing data (the "[Display Name] " prefix on group / DM
# user turns and the "[YYYY-MM-DD HH:MM] " timestamp prefix) is already
# inlined into `content` at message-build time on every surface that
# captures it — see telegram_bot._emit_user_with_sender and the DM
# build path. Stripping the original top-level fields here loses
# nothing the kin reads; only the storage-side duplicate is gone.
_API_MESSAGE_FIELDS = frozenset({
    "role", "content", "name", "tool_calls", "tool_call_id",
    "refusal", "thinking",
})


def _strip_extra_message_fields(messages):
    """Drop any message-object fields not in _API_MESSAGE_FIELDS.
    Messages already clean pass through by reference; only messages
    carrying unknown keys get a fresh dict. Runs after the existing
    normalizations and after _expand_attachments_for_provider has
    consumed `attachments` — by the time this fires, the only fields
    left to filter are storage bookkeeping."""
    out = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        has_extras = False
        for k in m:
            if k not in _API_MESSAGE_FIELDS:
                has_extras = True
                break
        if not has_extras:
            out.append(m)
            continue
        out.append({k: v for k, v in m.items() if k in _API_MESSAGE_FIELDS})
    return out


def _inline_mid_conversation_system_notes(messages):
    """Re-role Hearthkin's mid-conversation `role=system` notes to `user`,
    IN PLACE, so nothing gets teleported to the front of the prompt.

    This is the fix for the slow-reply bug described in
    `docs/design/prompt-cache-system-fold.md`. A local model reuses its cached
    work only for an unbroken run from the very start of the prompt, so the
    cost of a change is set by how EARLY it lands, not how big it is.

    Hearthkin appends `[hearthkin: ...]` notes into a kin's stored history as
    `role=system` — park receipts, tool-history compaction markers, authoring
    -bridge receipts, salvage notes, shared-file blocks. One or more per turn
    on a park keeper. `_consolidate_system_messages` (below) then hoisted every
    one of them to position 0, so each new note rewrote the FRONT of the prompt
    and the entire context was re-read from cold. Measured on a real kin: the
    system block grew ~345 characters on six consecutive turns while nothing on
    disk had changed, and a 22,000-token prefill at ~78 tok/s is five minutes
    before the first word.

    Left where they are, those notes invalidate only from their own position,
    which is far back in the history and stable between turns. The prompt goes
    back to being append-only.

    Why `user` and not `assistant`: a note usually lands right after the kin's
    own reply, and two assistant turns in a row is exactly the shape Gemma's
    chat template rejects (empty completion). `user` also matches what these
    notes are — the world reporting back — and `_collapse_consecutive_user_turns`
    merges the pair cleanly.

    Two things deliberately stay `system`:

    - **The LEADING contiguous run.** That is the real system prompt, and it
      also catches `_truncate_messages`'s rolling-window marker, which is
      spliced directly after it. That marker was made `role=system` on purpose
      (as `user`, models answered it, explaining context limits to the person
      who never asked) — and being a fixed string at a fixed position, it costs
      the cache nothing.
    - **A note sitting immediately before a `role=tool` turn.** Breaking an
      assistant-tool_calls → tool pairing with a user turn is a provider 400.
      Shouldn't occur — notes are appended after a turn completes — but the
      guard is three lines and the failure is a dead reply.

    **Called TWICE in chat(), and the first one is the load-bearing one.** It
    has to run BEFORE `_truncate_messages`, because truncation drops the oldest
    of what follows the system block — and if what's left then begins with one
    of these notes, the note is contiguous with the system prompt and this
    function can no longer tell them apart. That shipped: a real kin's system
    block alternated between 14,002 and 14,301 characters, a park receipt
    joining and leaving the leading run as the trim moved, holding reuse at 0%.
    The second call is a cheap safety net for anything added in between (today,
    only truncation's own marker, which belongs in the leading run) and is a
    no-op by reference in practice.

    Fast no-op by reference when there are no mid-conversation system notes,
    which is the common case for a kin that hasn't used tools or a park."""
    if not messages:
        return messages
    split = 0
    while (split < len(messages)
           and isinstance(messages[split], dict)
           and messages[split].get("role") == "system"):
        split += 1
    if not any(isinstance(m, dict) and m.get("role") == "system"
               for m in messages[split:]):
        return messages
    out = list(messages[:split])
    for i in range(split, len(messages)):
        m = messages[i]
        if not (isinstance(m, dict) and m.get("role") == "system"):
            out.append(m)
            continue
        nxt = messages[i + 1] if i + 1 < len(messages) else None
        if isinstance(nxt, dict) and nxt.get("role") == "tool":
            out.append(m)             # don't split a tool_calls→tool pairing
            continue
        c = m.get("content")
        if isinstance(c, str) and not c.strip():
            continue                  # blank note: the fold used to drop these
        note = dict(m)
        note["role"] = "user"
        out.append(note)
    return out


def _consolidate_system_messages(messages):
    """Merge every system-role message into a single leading system message.

    Some model chat templates raise "System message must be at the
    beginning." (or otherwise mishandle a system message that isn't first) —
    certain Qwen GGUF Jinja templates do this, e.g. `qwen36-opus-q4`.
    Hearthkin inserts mid-conversation `[hearthkin: ...]` system notes (the
    truncation marker, cap-full markers, salvage notes) that don't depend on
    their exact position, so they fold cleanly into the one leading system
    block such templates allow.

    Fast no-op for the common case: a conversation whose only system message
    is already first is returned unchanged (by reference). Otherwise the
    system contents are concatenated in order onto a single leading message
    and every non-system message keeps its order."""
    sys_idx = [i for i, m in enumerate(messages)
               if isinstance(m, dict) and m.get("role") == "system"]
    if not sys_idx:
        return messages
    if len(sys_idx) == 1 and sys_idx[0] == 0:
        return messages
    parts = []
    rest = []
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "system":
            c = m.get("content")
            if isinstance(c, str):
                if c.strip():
                    parts.append(c)
            elif c:
                parts.append(str(c))
        else:
            rest.append(m)
    if not parts:
        return rest
    return [{"role": "system", "content": "\n\n".join(parts)}] + rest


def _collapse_consecutive_user_turns(messages):
    """Merge adjacent user-role messages into a single user turn.

    Some local-model chat templates (certain Qwen GGUF templates, e.g.
    qwen36-opus-q4) return an EMPTY completion when the conversation
    ends in two or more consecutive user turns with no assistant turn
    between them. That snowballs viciously: a scheduled wake-up (cron) whose
    reply fails to save leaves a dangling user turn, so the NEXT wake-up
    sends two-users-in-a-row and gets empty too, leaving two dangling
    turns, and so on — every subsequent cron fails the same way. OpenRouter
    providers (Anthropic, etc.) merge consecutive user content server-side,
    which is exactly why this never surfaced there and only appeared after a
    kin moved onto a local model.

    Joining the contents with a blank-line separator preserves every word
    and gives the template the single-user-turn shape it expects, which
    breaks the snowball at send time — no history surgery required.

    Only user turns merge. Assistant and tool turns are left untouched so
    tool_call/tool-result pairing is never disturbed. Image turns (content
    is a block list, not a string) are left separate rather than risk
    mangling the structured shape. Fast no-op when no adjacent user turns
    exist (returns the input unchanged, by reference)."""
    has_adjacent = any(
        isinstance(messages[i], dict) and messages[i].get("role") == "user"
        and isinstance(messages[i - 1], dict) and messages[i - 1].get("role") == "user"
        for i in range(1, len(messages))
    )
    if not has_adjacent:
        return messages
    out = []
    for m in messages:
        if (isinstance(m, dict) and m.get("role") == "user"
                and out and isinstance(out[-1], dict)
                and out[-1].get("role") == "user"):
            prev_c = out[-1].get("content")
            cur_c = m.get("content")
            if isinstance(prev_c, str) and isinstance(cur_c, str):
                merged = dict(out[-1])
                if prev_c and cur_c:
                    merged["content"] = prev_c + "\n\n" + cur_c
                else:
                    merged["content"] = prev_c or cur_c
                out[-1] = merged
                continue
        out.append(m)
    return out


def _ensure_user_turn_present(messages):
    """Guarantee at least one plain-string user turn for strict templates.

    Certain Qwen GGUF Jinja templates (e.g. qwen36-opus-q4) scan the message
    list for a user turn whose rendered content is a non-empty string that is
    NOT a `<tool_response>` wrapper, and `raise_exception('No user query found
    in messages.')` if none exists. Ollama surfaces that as a 400 "Unable to
    generate parser for this template. Automatic parser generation failed"
    (the raise fires during Ollama's parser-generation probe, before any
    inference runs) — line 79 of the qwen36-opus-q4 template.

    Hearthkin can legitimately produce such a list. The common trigger is a
    tool-loop continuation whose original user query was dropped by
    `_truncate_messages`: the trim floor keeps only the two most recent
    turns, which in a tool round-trip are `[assistant tool_calls, tool
    result]`, and the user query sat one slot earlier. On a memory-heavy kin
    with many tool schemas and a tight num_ctx that trim bites regularly, so
    the sent list ends up with zero user turns. (An edge-case cron or
    continuation with no user role at all hits the same wall.)

    Fix: splice a minimal user turn in right after the leading system block
    so the template's scan is satisfied. The assistant/tool turns still carry
    the context the model continues from — this only restores the structural
    "a human asked something" anchor the template requires; it does not try
    to reconstruct the lost query text.

    Ollama-only (OpenRouter/Anthropic never raise on this). Fast no-op — and
    returns the input unchanged by reference — when a qualifying user turn
    already exists, which is the overwhelming common case."""
    for m in messages:
        if not (isinstance(m, dict) and m.get("role") == "user"):
            continue
        c = m.get("content")
        # Non-string content (an image block list) still renders to a user
        # query in the template's scan, so it counts as present.
        if not isinstance(c, str):
            return messages
        t = c.strip()
        if t and not (t.startswith("<tool_response>")
                      and t.endswith("</tool_response>")):
            return messages
    # No qualifying user turn — insert one after the leading system run so
    # the strict template accepts the request.
    split = 0
    while (split < len(messages)
           and isinstance(messages[split], dict)
           and messages[split].get("role") == "system"):
        split += 1
    synthetic = {"role": "user", "content": "Please continue."}
    return list(messages[:split]) + [synthetic] + list(messages[split:])


def _repair_tool_pairing(messages):
    """Make every tool round-trip in `messages` structurally complete
    before the request goes to OpenRouter, by dropping calls nothing
    answered and re-roling answers whose call has gone.

    OpenAI's Responses API — which is what OpenRouter translates our
    history into for `openai/*` — requires an exact one-to-one pairing:

      - a `role=tool` result with no matching call is
        `No tool call found for function call output with call_id ...`
      - a call with no result is the mirror of it

    Ollama and Anthropic accept both halves of a broken pair silently,
    so the shape survives on disk and only bites when a kin moves to an
    OpenAI model. It is also easy to CREATE at send time rather than at
    write time: any window that cuts between a call and its result
    (a truncation, a per-surface slice, a cap) leaves one half behind.
    That is why this runs at the choke point on the FINAL list rather
    than at any one of the places a window gets chosen — the shape is
    what's wrong, and it does not matter which cut produced it.

    Two repairs, deliberately asymmetric:

      - A call with no result has its entry removed from `tool_calls`.
        Nothing is lost that the model can act on: the result never
        reached the window either. When that empties the list the field
        goes with it, and an assistant turn left with no words at all is
        dropped (an empty assistant turn is its own provider problem).
        This is the same trade `telegram_bot._drop_leading_orphan_tools`
        already makes for a trailing orphan — keep the kin's words, drop
        the field providers reject.
      - A result with no call is **kept**, re-roled to `user`, wrapped in
        the `orphan_tool_result` prompt. Dropping it is the tidier code
        and the worse behaviour: the result is usually the thing the
        kin's next words are about, and the window kept it on purpose.
        `user` rather than `assistant` for the reason every other
        re-roling here uses it — two assistant turns in a row is what
        Gemma answers with nothing.

    A `role=system` message BETWEEN a call and its result does not break
    the run. `_inline_mid_conversation_system_notes` deliberately leaves
    a note in that position as `system` precisely so the pairing holds,
    so treating one as a break here would manufacture the orphans this
    function exists to remove.

    Returns a new list only when something needed repair; otherwise the
    input, by reference. Structural only — ids are not consulted, which
    is what lets it run before `_fill_blank_tool_call_ids` on a history
    whose ids are all empty strings."""
    n = len(messages)
    has_any_tool_traffic = any(
        isinstance(m, dict)
        and ((m.get("role") == "assistant" and m.get("tool_calls"))
             or m.get("role") == "tool")
        for m in messages
    )
    if not has_any_tool_traffic:
        return messages

    # First pass: for each assistant tool_calls turn, count the result
    # turns that follow it (system notes don't break the run), and mark
    # every result turn that belongs to one. What stays unmarked is an
    # orphan result.
    answered = {}        # assistant index -> number of result turns following
    claimed = set()      # indices of result turns that have a call
    for i, m in enumerate(messages):
        if not (isinstance(m, dict) and m.get("role") == "assistant"
                and isinstance(m.get("tool_calls"), list) and m["tool_calls"]):
            continue
        count = 0
        j = i + 1
        while j < n and isinstance(messages[j], dict):
            role = messages[j].get("role")
            if role == "tool":
                if count >= len(m["tool_calls"]):
                    break          # more results than calls — the rest are orphans
                claimed.add(j)
                count += 1
                j += 1
                continue
            if role == "system":
                j += 1             # a note between call and result is fine
                continue
            break
        answered[i] = count

    if (all(c == len(messages[i]["tool_calls"]) for i, c in answered.items())
            and all(i in claimed for i, m in enumerate(messages)
                    if isinstance(m, dict) and m.get("role") == "tool")):
        return messages

    from kin_persistence import load_app_prompt
    try:
        wrapper = load_app_prompt("orphan_tool_result")
    except Exception:
        wrapper = "[hearthkin: the result of an earlier tool call — the call " \
                  "itself has scrolled out of view]\n{result}"

    out = []
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            out.append(m)
            continue
        role = m.get("role")
        if role == "tool" and i not in claimed:
            content = m.get("content")
            out.append({
                "role": "user",
                "content": wrapper.replace(
                    "{result}", content if isinstance(content, str) else ""),
            })
            continue
        if i in answered:
            keep = answered[i]
            if keep >= len(m["tool_calls"]):
                out.append(m)
                continue
            new_m = dict(m)
            if keep > 0:
                new_m["tool_calls"] = m["tool_calls"][:keep]
            else:
                new_m.pop("tool_calls", None)
                content = new_m.get("content")
                if not (isinstance(content, str) and content.strip()):
                    # No words and no surviving calls — nothing left to send.
                    continue
            out.append(new_m)
            continue
        out.append(m)
    return out


def _mint_tool_call_id(seed, used):
    """Deterministic `call_<16 hex>` id derived from `seed`, guaranteed
    not to collide with anything already in `used` (which is mutated).

    Same collision ladder as `_mint_short_id_9`, and deterministic for
    the same reason: these ids land IN the prompt, so an id that
    changed between turns would move the prompt and throw the cache
    away. See the append-only-prompt rule in CLAUDE.md."""
    def _h(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    base = "call_" + _h(str(seed))
    if base not in used:
        used.add(base)
        return base
    for i in range(1, 1000):
        candidate = "call_" + _h(f"{seed}:{i}")
        if candidate not in used:
            used.add(candidate)
            return candidate
    final = "call_" + _h(f"{seed}:fallback:{len(used)}")
    used.add(final)
    return final


def _fill_blank_tool_call_ids(messages):
    """Give every assistant tool_call — and its paired tool turn — a
    non-empty id before the request goes to OpenRouter.

    Ollama does not return an id with a tool call, so
    `_normalize_tool_call_for_history` stores `id: ""` and the matching
    `role=tool` turn stores `tool_call_id: ""`. That is fine forever on
    Ollama, and fine on Anthropic. It is NOT fine on OpenAI: OpenRouter
    translates the history into the Responses API, where an empty
    `call_id` is a hard 400 —

        Invalid 'input[9].call_id': empty string. Expected a string
        with minimum length 1 ... code: empty_string

    — so a kin that used tools locally and then moved to an OpenAI
    model could not send its own history at all. Observed 2026-08-06
    against openai/* via both OpenAI and Azure.

    Only BLANK ids are filled; anything the provider already gave us is
    passed through untouched, so an Anthropic/OpenAI history keeps its
    own pairing. Pairing of the filled ones is by POSITION: the fresh
    ids from an assistant turn queue up and the following `role=tool`
    turns consume them in order — the same strategy
    `_remap_tool_call_ids_for_mistral` uses, and for the same reason
    (a blank id cannot be matched by id). A tool turn that already has
    an id still consumes its queue slot, so a partially-blank run stays
    aligned. An orphan tool turn (no preceding call — the history trim
    sweeps these, but be defensive) gets its own fresh id.

    The seed is the call's own content (tool name + arguments), not its
    position, so trimming the front of the history doesn't renumber
    what's left. Two byte-identical calls in one prompt take the
    collision ladder's next rung, which is stable as long as both stay
    in the prompt.

    Returns a new list; the caller's input and on-disk history are
    untouched. Fast no-op — returns the input by reference — when there
    is no tool traffic, which is most calls."""
    has_any_tool_traffic = any(
        isinstance(m, dict)
        and ((m.get("role") == "assistant" and m.get("tool_calls"))
             or m.get("role") == "tool")
        for m in messages
    )
    if not has_any_tool_traffic:
        return messages

    used = set()
    for m in messages:
        if not isinstance(m, dict):
            continue
        if isinstance(m.get("tool_calls"), list):
            for tc in m["tool_calls"]:
                if isinstance(tc, dict) and isinstance(tc.get("id"), str) and tc["id"]:
                    used.add(tc["id"])

    out = []
    pending_queue = []
    changed = False
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            pending_queue = []
            continue
        role = m.get("role")
        if role == "assistant" and isinstance(m.get("tool_calls"), list) and m["tool_calls"]:
            new_tcs = []
            queue = []
            turn_changed = False
            for tc in m["tool_calls"]:
                if not isinstance(tc, dict):
                    new_tcs.append(tc)
                    continue
                tcid = tc.get("id")
                if isinstance(tcid, str) and tcid:
                    new_tcs.append(tc)
                    queue.append(tcid)
                    continue
                fn = tc.get("function") or {}
                seed = "{}|{}".format(
                    (fn.get("name") if isinstance(fn, dict) else "") or "",
                    (fn.get("arguments") if isinstance(fn, dict) else "") or "",
                )
                fresh = _mint_tool_call_id(seed, used)
                new_tc = dict(tc)
                new_tc["id"] = fresh
                new_tcs.append(new_tc)
                queue.append(fresh)
                turn_changed = True
            if turn_changed:
                new_m = dict(m)
                new_m["tool_calls"] = new_tcs
                out.append(new_m)
                changed = True
            else:
                out.append(m)
            pending_queue = queue
        elif role == "tool":
            tcid = m.get("tool_call_id")
            if isinstance(tcid, str) and tcid:
                # Already paired — keep it, but still consume the slot
                # so a later blank turn in the same run lines up.
                if pending_queue:
                    pending_queue.pop(0)
                out.append(m)
                continue
            if pending_queue:
                fresh = pending_queue.pop(0)
            else:
                content = m.get("content")
                fresh = _mint_tool_call_id(
                    "orphan|" + (content if isinstance(content, str) else ""), used)
            new_m = dict(m)
            new_m["tool_call_id"] = fresh
            out.append(new_m)
            changed = True
        else:
            out.append(m)
            # A `role=system` note between a call and its result does NOT
            # end the run — `_inline_mid_conversation_system_notes` leaves
            # a note in exactly that position as `system` on purpose, to
            # keep the pairing intact. Clearing here would send the result
            # down the orphan path and mint it an id that pairs with
            # nothing, which is a 400 on OpenAI rather than a near miss.
            if m.get("role") != "system":
                pending_queue = []
    return out if changed else messages


# Mistral's chat-completions API requires tool_call_id to be exactly
# 9 alphanumeric characters (`^[a-zA-Z0-9]{9}$`). Anthropic-Bedrock
# IDs are 36+ chars (`toolu_bdrk_01EXAMPLEEXAMPLEEXAMPLE`). When a
# cross-provider kin (one moved from Haiku to Mistral Large, say)
# sends its history to Mistral, Mistral truncates each ID to its
# 9-char form — collapsing many distinct calls onto the same prefix
# and surfacing as a generic 400 ("Duplicate tool call id in assistant
# message", error code 3230, type invalid_request_message_order).
# Other OpenRouter providers (Anthropic, OpenAI, etc) accept the
# longer IDs as-is, so the rewrite is Mistral-specific.
#
# Source of truth for the 9-char format. The remap below mints fresh
# ids unconditionally and doesn't consult this regex itself;
# compat._check_tool_id_format imports it to count nonconforming
# stored ids at model-swap time.
_MISTRAL_TOOL_CALL_ID_RE = re.compile(r"^[a-zA-Z0-9]{9}$")


def _is_mistral_model(model):
    """OpenRouter routes mistralai/* models through Mistral's API,
    which has the 9-char tool_call_id constraint. Other providers
    don't. Match on the OpenRouter model-id prefix."""
    return isinstance(model, str) and model.startswith("openrouter/mistralai/")


def _mint_short_id_9(seed, used):
    """Produce a 9-char hex id deterministically derived from `seed`,
    guaranteed not to collide with anything in the `used` set. Mutates
    `used` to add the chosen id.

    Determinism property: given the SAME seed and the SAME pre-state
    of `used`, the output is the SAME. That means a repeated remap
    pass over an unchanged message list produces identical ids —
    important for any future provider that pairs a strict id format
    with prompt-cache-prefix matching (the cache key would otherwise
    invalidate on every retry). For Mistral alone this doesn't
    matter; Mistral doesn't cache. But the cost of getting it right
    is small.

    Collision strategy: hash `seed` first; if that 9-char prefix is
    already used, hash `seed:1`, `seed:2`, etc. SHA256's avalanche
    means each suffix gives a fresh 9-char prefix; collision-on-
    collision is astronomically rare but capped at 999 retries before
    falling back to a hash incorporating the used-set size (final
    safety net — should be unreachable in practice)."""
    def _h(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:9]
    base = _h(str(seed))
    if base not in used:
        used.add(base)
        return base
    for i in range(1, 1000):
        candidate = _h(f"{seed}:{i}")
        if candidate not in used:
            used.add(candidate)
            return candidate
    # Unreachable in practice (SHA256 collision avalanche makes 1000
    # straight collisions cosmically unlikely), but defensive — keep
    # determinism dependent on observable state (used-set size) rather
    # than wall-clock time, so reruns still match.
    final = _h(f"{seed}:fallback:{len(used)}")
    used.add(final)
    return final


def _remap_tool_call_ids_for_mistral(messages):
    """Rewrite every assistant tool_call ID (and its paired tool turns)
    to a fresh unique 9-char hex id derived from SHA256 of the
    original. Two pairing strategies are used together:

      - Primary: by ORIGINAL id. The mapping `original_id -> fresh_id`
        rewrites tool turns whose `tool_call_id` matches a previously
        rewritten assistant id. Handles the common case where the
        provider's stored history pairs tool results with their
        assistant call by id.

      - Fallback: by POSITION. After each `role=assistant` turn with
        `tool_calls`, the following `role=tool` turns are consumed in
        order from a queue of (original_id, fresh_id) pairs for that
        turn. Catches three edge cases the id-only path misses:
          1. Two tool_calls in one assistant turn with the same
             original id (or both `id=""` from a defaulted-missing
             field) — the by-id mapping collapses, by-position keeps
             them distinct.
          2. Tool turn whose `tool_call_id` was stored empty / null —
             impossible to match by id; position pairs it.
          3. Latent provider quirks where the on-disk ID was already
             truncated before storage; the by-id rewrite catches the
             prefix, the by-position pass catches the rest.

    Every assistant tool_call gets a fresh id regardless of whether
    the original conformed — uniform output, no `is-this-9-char?`
    branch to get subtly wrong. Tool turns that don't pair with any
    preceding assistant (orphans — should be impossible after the
    history-trim orphan sweep, but defensive) get a fresh id too.

    Determinism: built via SHA256(`original_id`) :9 hex chars. Same
    input message list -> same output ids, every call. Replaces the
    earlier sequential `tcXXXXXXX` counter (2026-06-10 first pass)
    after looking at the OpenClaw codebase's `tool-call-id.ts`, which
    used the same approach for the same reason. Determinism keeps
    any future cache-supporting strict-format provider's prefix
    cache hits from invalidating on retries. See `_mint_short_id_9`.

    Returns a new list; the caller's input and on-disk history are
    untouched.

    `_MISTRAL_TOOL_CALL_ID_RE` and `_is_mistral_model` are still
    exposed for other call sites — this function no longer consults
    the regex itself."""
    used = set()
    def _next_id(seed):
        return _mint_short_id_9(seed, used)

    # Fast path: empty / no-tool messages → no work.
    has_any_tool_traffic = any(
        isinstance(m, dict)
        and (
            (m.get("role") == "assistant" and m.get("tool_calls"))
            or m.get("role") == "tool"
        )
        for m in messages
    )
    if not has_any_tool_traffic:
        return messages

    # Walk once, rewriting in-place into a new list. Track the queue
    # of fresh IDs from the most recent assistant tool_calls turn so
    # immediately following tool turns can pair by position when the
    # by-id lookup fails. The queue is consumed; a non-tool message
    # clears it (a new assistant tool_calls turn replaces it; a user
    # turn or anything else drops it).
    by_id = {}              # original_id -> fresh_id (only when original is non-empty + hashable)
    pending_queue = []      # list of fresh ids waiting for their tool turns
    out = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            pending_queue = []
            continue
        role = m.get("role")
        if role == "assistant" and isinstance(m.get("tool_calls"), list) and m["tool_calls"]:
            # Pre-scan original IDs in this turn so within-turn duplicates
            # don't pollute `by_id`. If two tcs in one turn share the
            # same original id, mapping by id collapses (both tool
            # turns would resolve to the LAST fresh id); position-based
            # pairing via the pending queue handles them correctly.
            orig_ids = [tc.get("id") if isinstance(tc, dict) else None
                        for tc in m["tool_calls"]]
            within_dup = {
                oid for oid, n in Counter(orig_ids).items()
                if n > 1 and isinstance(oid, str) and oid
            }
            new_tcs = []
            queue = []
            for tc in m["tool_calls"]:
                if not isinstance(tc, dict):
                    new_tcs.append(tc)
                    continue
                orig = tc.get("id")
                # Seed = original id when present, else empty string.
                # Duplicates (within-turn or across) hash to the same
                # first attempt → `_mint_short_id_9`'s used-set
                # collision path picks the next deterministic suffix.
                seed = orig if isinstance(orig, str) else ""
                fresh = _next_id(seed)
                # Only map by original id when it's a non-empty string
                # AND not a within-turn duplicate — empty / null /
                # duplicated ids would collapse the mapping and re-
                # create the original bug.
                if isinstance(orig, str) and orig and orig not in within_dup:
                    by_id[orig] = fresh
                queue.append(fresh)
                new_tc = dict(tc)
                new_tc["id"] = fresh
                new_tcs.append(new_tc)
            new_m = dict(m)
            new_m["tool_calls"] = new_tcs
            out.append(new_m)
            pending_queue = queue
        elif role == "tool":
            orig_tcid = m.get("tool_call_id")
            fresh = None
            if isinstance(orig_tcid, str) and orig_tcid and orig_tcid in by_id:
                fresh = by_id[orig_tcid]
            elif pending_queue:
                fresh = pending_queue.pop(0)
            else:
                # Orphan tool — pair to nothing. Give it a fresh 9-char
                # id so Mistral at least sees a valid-format id. The
                # `Unexpected role 'tool' after role 'system'` error
                # will likely fire anyway, but at least not for a
                # length / format reason.
                seed = orig_tcid if isinstance(orig_tcid, str) else ""
                fresh = _next_id(seed)
            new_m = dict(m)
            new_m["tool_call_id"] = fresh
            out.append(new_m)
        else:
            out.append(m)
            pending_queue = []
    return out


def _normalize_history_tool_args(messages, model):
    """Ensure each assistant turn's tool_calls[].function.arguments is in the
    shape the active provider's API requires:
      - OpenRouter / OpenAI: JSON string
      - Ollama: dict
    Walks the messages list once and rebuilds any turn that needs a shape fix.
    Other messages pass through by reference."""
    want_string = _is_openrouter_model(model)
    out = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        tcs = m.get("tool_calls")
        if not isinstance(tcs, list) or not tcs:
            out.append(m)
            continue
        new_tcs = []
        changed = False
        for tc in tcs:
            fn = _tc_field(tc, "function") or {}
            args_value = _tc_field(fn, "arguments", "{}")
            if want_string:
                if isinstance(args_value, str):
                    new_tcs.append(tc)
                    continue
                args_out = json.dumps(_coerce_tool_call_args(args_value))
            else:
                if isinstance(args_value, dict):
                    new_tcs.append(tc)
                    continue
                args_out = _coerce_tool_call_args(args_value)
            changed = True
            new_tcs.append({
                "id": _tc_field(tc, "id", ""),
                "type": _tc_field(tc, "type", "function") or "function",
                "function": {
                    "name": _tc_field(fn, "name", ""),
                    "arguments": args_out,
                },
            })
        if changed:
            new_m = dict(m)
            new_m["tool_calls"] = new_tcs
            out.append(new_m)
        else:
            out.append(m)
    return out


_DEFAULT_IMAGE_HISTORY_KEEP = 2

# (kin_name) -> (config.json mtime, cfg dict). Backs the per-call
# config resolvers below so the chat hot path doesn't re-read +
# re-parse config.json from disk on every chat() call (up to 9 reads
# per tool-loop turn). The mtime key means an operator edit in
# Settings (which rewrites the file) invalidates the entry on the
# very next call — no staleness window beyond the write itself.
_agent_cfg_mtime_cache = {}


def _load_agent_config_cached(kin_name):
    """Return the kin's config dict, cached against config.json's
    mtime. Falls back to a fresh load when the stat or cached entry
    is unusable. Returns {} on any failure — callers apply their own
    defaults."""
    try:
        from kin_persistence import agent_dir, load_agent_config
    except Exception:
        return {}
    try:
        mtime = (agent_dir(kin_name) / "config.json").stat().st_mtime
    except OSError:
        mtime = None
    cached = _agent_cfg_mtime_cache.get(kin_name)
    if cached is not None and mtime is not None and cached[0] == mtime:
        return cached[1]
    try:
        cfg = load_agent_config(kin_name) or {}
    except Exception:
        return {}
    if mtime is not None:
        _agent_cfg_mtime_cache[kin_name] = (mtime, cfg)
    return cfg


def _resolve_image_history_keep(kin_name):
    """Read the kin's `image_history_keep` config (with a safe default
    when load fails — defensive against a kin folder that's been
    deleted or a stale call-site that's passing a now-missing kin).
    Cached per (kin, config mtime) — see _load_agent_config_cached."""
    if not kin_name:
        return _DEFAULT_IMAGE_HISTORY_KEEP
    try:
        cfg = _load_agent_config_cached(kin_name)
        v = cfg.get("image_history_keep", _DEFAULT_IMAGE_HISTORY_KEEP)
        return int(v)
    except Exception:
        return _DEFAULT_IMAGE_HISTORY_KEEP


def _resolve_keep_alive(kin_name):
    """Read the kin's `keep_alive` setting (Ollama only). Returns the
    string verbatim ("5m" / "30m" / "1h" / "-1" / etc.) or "" when no
    override is set; the Ollama path treats "" as "don't include the
    field" so the daemon's own default (5 minutes) applies.

    No effect on OpenRouter calls — the dispatcher only consults this
    for the Ollama branch. Cached per (kin, config mtime) — see
    _load_agent_config_cached."""
    if not kin_name:
        return ""
    try:
        cfg = _load_agent_config_cached(kin_name)
        v = cfg.get("keep_alive", "")
        return str(v) if v not in (None, "") else ""
    except Exception:
        return ""


def _coerce_keep_alive(v):
    """Ollama's keep_alive wants a NUMBER for second-counts and sentinels
    (-1 = stay loaded forever, 0 = unload now) and a STRING only for unit
    durations ("30m", "1h"). A bare-integer string like "-1" trips Ollama's
    Go duration parser — `time: missing unit in duration "-1"`, HTTP 400 — so
    coerce bare integers (incl. negatives) to int and leave unit-duration
    strings alone. Returns None for empty/None so the caller omits the field."""
    if v in (None, ""):
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return s


def _expand_attachments_for_provider(messages, model, kin_name, *, image_history_keep=None):
    """Rewrite messages so any `attachments` field gets translated to
    the active provider's image-input shape:

      - Ollama: per-message `images: [base64-string-no-prefix, ...]`
        list. Content stays a string. Ollama accepts file paths too,
        but base64 keeps the call self-contained (and works for the
        kin's relative-path attachment refs without us having to
        teach the daemon about the kin directory).

      - OpenRouter: `content` becomes a content-block list
        `[{type: "text", text: "..."}, {type: "image_url",
        image_url: {url: "data:image/jpeg;base64,..."}}, ...]`.

    Messages without attachments pass through unchanged (no copy).
    Attachments that can't be read are dropped silently — better to
    deliver the text than to fail the whole turn over a missing
    image file (the file might have been moved or pruned).

    Provider detection uses the same `_is_openrouter_model` dispatch
    the rest of `chat()` uses, so behavior stays consistent across
    all four code paths (1-on-1 / room / Telegram / cron).
    """
    from kin_persistence import attachment_abspath
    import base64

    is_or = _is_openrouter_model(model)
    if image_history_keep is None:
        image_history_keep = _DEFAULT_IMAGE_HISTORY_KEEP
    try:
        image_history_keep = max(0, int(image_history_keep))
    except (TypeError, ValueError):
        image_history_keep = _DEFAULT_IMAGE_HISTORY_KEEP

    # Identify which user turns are "keepers" (most recent
    # image_history_keep) versus older image turns that get
    # text-only treatment. Walk backwards counting user turns with
    # attachments; mark the index of each keeper. Strip everything
    # else's `attachments` field below at iteration time.
    keep_indices = set()
    if image_history_keep > 0:
        seen = 0
        for idx in range(len(messages) - 1, -1, -1):
            m = messages[idx]
            if not isinstance(m, dict):
                continue
            if m.get("role") != "user":
                continue
            atts = m.get("attachments")
            if isinstance(atts, list) and atts:
                keep_indices.add(idx)
                seen += 1
                if seen >= image_history_keep:
                    break

    out = []
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            out.append(m)
            continue
        atts = m.get("attachments")
        # Role gate (defense in depth): only user turns are
        # legitimate attachment carriers. If something put an
        # `attachments` field on an assistant / system / tool turn
        # — a manually-edited jsonl, a future code path that forgot
        # the convention — we strip it rather than ship it. Most
        # providers reject image content blocks on non-user
        # messages; the few that don't reject get a confused signal.
        if m.get("role") != "user":
            if "attachments" in m:
                new_m = dict(m)
                new_m.pop("attachments", None)
                out.append(new_m)
            else:
                out.append(m)
            continue
        # History cap: if this turn isn't a keeper, strip its
        # attachments. The text content survives unchanged — the
        # kin's own past reply usually already describes what was
        # in the image, so context is preserved without re-paying
        # per-image input tokens on every turn.
        if i not in keep_indices:
            if "attachments" in m:
                new_m = dict(m)
                new_m.pop("attachments", None)
                out.append(new_m)
            else:
                out.append(m)
            continue
        if not (isinstance(atts, list) and atts and kin_name):
            # Strip an empty-list `attachments` field if present so
            # the provider doesn't see a stray empty key.
            if "attachments" in m:
                new_m = dict(m)
                new_m.pop("attachments", None)
                out.append(new_m)
            else:
                out.append(m)
            continue

        # Read all attachment files; drop ones we can't read.
        resolved = []
        for rel in atts:
            ap = attachment_abspath(kin_name, rel)
            if ap is None:
                continue
            try:
                data = ap.read_bytes()
            except OSError:
                continue
            suffix = ap.suffix.lower().lstrip(".")
            if suffix == "jpg":
                suffix = "jpeg"
            mime = f"image/{suffix}"
            resolved.append((mime, data, str(ap)))

        new_m = dict(m)
        new_m.pop("attachments", None)

        if not resolved:
            out.append(new_m)
            continue

        if is_or:
            text = new_m.get("content") or ""
            blocks = []
            if text:
                blocks.append({"type": "text", "text": text})
            for mime, data, _ in resolved:
                b64 = base64.b64encode(data).decode("ascii")
                blocks.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                })
            new_m["content"] = blocks
        else:
            # Ollama path: base64 strings (no data: prefix) in the
            # per-message `images` field. Content stays a string.
            new_m["images"] = [
                base64.b64encode(data).decode("ascii")
                for _, data, _ in resolved
            ]
        out.append(new_m)
    return out


def _coerce_tool_call_assistant_content(messages):
    """Replace `content: None` with `content: ""` on any assistant
    message that carries tool_calls. The OpenAI streaming spec
    allows null content there (it's the canonical "this turn was
    just tool calls, no narrative" shape), but Anthropic-via-
    OpenRouter interprets the null as a structural defect — the
    NEXT turn's generation degenerates into semantic chain walks,
    repetition runs, or other "seizure"-style output corruption.
    Empty string is the safe universal shape: every provider in
    Hearthkin's dispatch (Anthropic, OpenAI, Ollama) tolerates it
    in the tool-call slot.

    Walks once, rebuilds only the messages that need a fix, others
    pass through by reference. Run on every outbound chat() request
    so old records on disk with null content get healed before
    leaving the process."""
    out = []
    for m in messages:
        if (isinstance(m, dict)
                and m.get("role") == "assistant"
                and isinstance(m.get("tool_calls"), list)
                and m["tool_calls"]
                and m.get("content") is None):
            new_m = dict(m)
            new_m["content"] = ""
            out.append(new_m)
        else:
            out.append(m)
    return out


def _is_openrouter_model(model):
    return isinstance(model, str) and model.startswith("openrouter/")


def _openrouter_model_id(model):
    """Strip the `openrouter/` prefix to get the OpenRouter-native model ID."""
    return model[len("openrouter/"):] if _is_openrouter_model(model) else model


def _supports_caching(model):
    parts = _openrouter_model_id(model).split("/")
    if not parts:
        return False
    return parts[0].lower() in _CACHE_SUPPORTED_PROVIDERS


# Providers that honor an explicit "ttl" on cache_control. Anthropic
# added 5m/1h selection in 2024 and silently dropped the default from 1h
# to 5m on 2026-03-06 — explicit "ttl": "1h" opts back in. Google Gemini
# accepts the same shape. Other cache-supporting providers (OpenAI,
# DeepSeek, Groq, etc.) cache automatically with no TTL parameter; they
# silently ignore "ttl" if sent.
_EXPLICIT_TTL_PROVIDERS = frozenset({"anthropic", "google"})


def _provider_honors_explicit_ttl(model):
    parts = _openrouter_model_id(model).split("/")
    if not parts:
        return False
    return parts[0].lower() in _EXPLICIT_TTL_PROVIDERS


# Cache of OpenRouter model_id → bool. Populated lazily from the
# /models catalogue; cleared by clear_openrouter_caches() (wired into
# the Refresh Models button alongside the Ollama vision cache).
_openrouter_vision_cache = {}


def _openrouter_supports_images(model):
    """Return True/False/None for an OpenRouter model. Reads
    architecture.input_modalities from the model catalogue (cached
    on disk for 24h via list_openrouter_models). Returns None if
    we can't fetch the catalogue (offline, no API key, etc.) so
    callers can decide whether to optimistically allow or refuse."""
    model_id = _openrouter_model_id(model)
    if model_id in _openrouter_vision_cache:
        return _openrouter_vision_cache[model_id]
    try:
        catalogue = list_openrouter_models()
    except Exception:
        return None
    for m in catalogue:
        if m.get("id") != model_id:
            continue
        arch = m.get("architecture") or {}
        modalities_in = list(arch.get("input_modalities") or [])
        legacy_modality = arch.get("modality") or ""
        if legacy_modality:
            modalities_in.extend(legacy_modality.split("+"))
        result = any("image" in str(x).lower() for x in modalities_in)
        _openrouter_vision_cache[model_id] = result
        return result
    # Model not in catalogue (very new release, name mismatch). Unknown.
    return None


def model_supports_images(model):
    """Unified vision-capability check used by the desktop UI and the
    Telegram path to decide whether to allow image attachments.

    Returns True/False, defaulting to False when capability can't be
    determined. The default-False posture is intentional: enabling
    attach for an unknown model lets the user stage images for a
    model that will silently ignore them, which is worse than the
    user noticing "the button's dimmed, maybe I picked a non-vision
    model" and switching deliberately.
    """
    if not model:
        return False
    if _is_openrouter_model(model):
        result = _openrouter_supports_images(model)
    else:
        from model_utils import _model_supports_vision
        result = _model_supports_vision(model)
    return bool(result)  # None → False


def clear_openrouter_caches():
    """Clear in-process OpenRouter caches that the Refresh Models
    button should bust alongside the Ollama caches. Currently the
    vision lookup + pricing lookup; extend here if more OR-derived
    flags get cached."""
    _openrouter_vision_cache.clear()
    _openrouter_pricing_cache.clear()


# OpenRouter model_id → (prompt_price, completion_price) in USD per token.
# Populated lazily from the cached catalogue on first lookup. Numbers from
# OR are per-token prices (e.g. "0.000003" for $3 per million prompt tokens).
_openrouter_pricing_cache = {}


def _openrouter_pricing(model, *, allow_fetch=True):
    """Return (prompt_price, completion_price, cache_read_price) for an
    OpenRouter model. `cache_read_price` is the per-token price for
    prompt tokens served from the provider's cache (Anthropic ~1/10 of
    input cost; OpenAI similar; many others 0 or unavailable) and is
    None when the catalogue doesn't report one. Returns (None, None,
    None) when the catalogue isn't available or the model isn't found.

    Used by the usage-log writer to estimate per-call cost. Catalogue
    is cached on disk for 24h (list_openrouter_models), so this lookup
    is cheap after the first call.

    `allow_fetch=False` restricts the lookup to the already-cached
    catalogue (in-process memo + on-disk cache file) — never a network
    fetch. The usage-logging path passes False because it runs inside
    a send."""
    model_id = _openrouter_model_id(model)
    if model_id in _openrouter_pricing_cache:
        return _openrouter_pricing_cache[model_id]
    try:
        catalogue = list_openrouter_models(allow_fetch=allow_fetch)
    except Exception:
        return (None, None, None)
    for m in catalogue:
        if m.get("id") != model_id:
            continue
        pricing = m.get("pricing") or {}
        try:
            prompt = float(pricing.get("prompt"))
            completion = float(pricing.get("completion"))
        except (TypeError, ValueError):
            # Non-numeric pricing field (e.g. "varies" on some routed
            # endpoints). Cache the unknown so subsequent estimate
            # calls don't re-scan the whole catalogue forever (audit
            # L21).
            result = (None, None, None)
            _openrouter_pricing_cache[model_id] = result
            return result
        cache_read = None
        raw_cache = pricing.get("input_cache_read")
        if raw_cache is not None:
            try:
                cache_read = float(raw_cache)
            except (TypeError, ValueError):
                cache_read = None
        result = (prompt, completion, cache_read)
        _openrouter_pricing_cache[model_id] = result
        return result
    # Model not found in catalogue — cache the miss too, same reason.
    # EXCEPT in no-fetch mode: the "miss" may just mean the disk cache
    # doesn't exist yet, and memoizing it would poison later
    # fetch-allowed lookups.
    if allow_fetch:
        _openrouter_pricing_cache[model_id] = (None, None, None)
    return (None, None, None)


def _estimate_call_cost(model, prompt_tokens, completion_tokens, cached_tokens=0,
                        *, allow_fetch=True):
    """Best-effort USD cost estimate for one chat() call. Returns None
    for Ollama (free, local) and for OpenRouter models we can't price
    (catalogue unavailable, model unknown).

    `allow_fetch=False` forbids a network catalogue refresh during the
    lookup — pass it from anywhere that runs inside a send.

    Honors the provider's cache-read discount when both `cached_tokens`
    is supplied and the catalogue reports an input_cache_read price.
    Anthropic prices cache reads at ~1/10 of fresh input; without this
    discount applied, the estimate overstates by ~10x for a steady-
    state conversation where 99% of the prompt comes from cache (audit
    L4). When no cache pricing is available we bill the cached portion
    at the full prompt rate (upper-bound)."""
    if not _is_openrouter_model(model):
        # Ollama or unknown — no cost.
        return 0.0 if model else None
    prompt_price, completion_price, cache_read_price = _openrouter_pricing(
        model, allow_fetch=allow_fetch)
    if prompt_price is None or completion_price is None:
        return None
    p_total = int(prompt_tokens or 0)
    cached = max(0, min(int(cached_tokens or 0), p_total))
    fresh = p_total - cached
    if cache_read_price is None:
        cached_cost = cached * prompt_price
    else:
        cached_cost = cached * cache_read_price
    return (
        fresh * prompt_price
        + cached_cost
        + int(completion_tokens or 0) * completion_price
    )


# ─── Ollama backend ──────────────────────────────────────────────────────────

def _ollama_chunk_int(chunk, key):
    """Read an integer field off an Ollama streaming chunk, dict- or
    Pydantic-shaped. Ollama puts prompt_eval_count / eval_count on the
    final (done) chunk of a stream; absent or garbled -> 0."""
    if isinstance(chunk, dict):
        val = chunk.get(key)
    else:
        val = getattr(chunk, key, None)
    try:
        return int(val) if val is not None else 0
    except (TypeError, ValueError):
        return 0


def _chat_ollama_stream(model, messages, options, think, tools, keep_alive=None, ollama_host=None, request_timeout_secs=None) -> Iterator[Chunk]:
    if ollama is None:
        raise LLMBackendError("ollama library not installed (pip install ollama)")
    kwargs = {"model": model, "messages": messages, "stream": True}
    if options:
        kwargs["options"] = options
    if think is not None:
        # Sent even when False, deliberately. `if think:` omitted the field
        # entirely, which is NOT the same as asking for no thinking — it hands
        # the decision to the model's own default, and a reasoning model's
        # default is on. So "Thinking: off" silently did nothing on gemma4 and
        # friends, and on a turn with a reply cap the whole budget could go into
        # a reasoning block the person never sees: Ollama returns done_reason
        # "length", eval_count at the cap, and content "". That reads as the kin
        # having nothing to say. Verified against Ollama 0.32.5 that `think:
        # false` is accepted by non-reasoning models too, so this is safe to
        # send unconditionally rather than only for models we think can reason.
        kwargs["think"] = think
    if tools:
        kwargs["tools"] = tools
    ka = _coerce_keep_alive(keep_alive)
    if ka is not None:
        kwargs["keep_alive"] = ka

    chat_fn = _ollama_chat_callable(ollama_host, request_timeout_secs)
    raw = chat_fn(**kwargs)
    final_usage = None
    for chunk in raw:
        c = _normalize_ollama_chunk(chunk)
        if c is not None:
            yield c
        # Ollama reports token counts on the final (done) streamed
        # chunk — capture them so the streaming path feeds calibration
        # and usage.log the same way the blocking path does.
        pe = _ollama_chunk_int(chunk, "prompt_eval_count")
        ev = _ollama_chunk_int(chunk, "eval_count")
        ped = _ollama_chunk_int(chunk, "prompt_eval_duration")
        evd = _ollama_chunk_int(chunk, "eval_duration")
        if pe or ev:
            final_usage = {"prompt_tokens": pe, "completion_tokens": ev,
                           "prompt_eval_duration": ped, "eval_duration": evd}
    yield Chunk(done=True, usage=final_usage)


def _is_pydantic_validation_error(e):
    """Identify pydantic ValidationError without importing pydantic
    directly — robust across pydantic 1/2 and across ollama-python
    versions, which pin different majors. Walks the exception class's
    MRO looking for a class named ValidationError that lives under a
    `pydantic` module."""
    for ancestor in type(e).__mro__:
        if ancestor.__name__ == "ValidationError" and \
                "pydantic" in (ancestor.__module__ or ""):
            return True
    return False


def _ollama_chat_raw(model, messages, options, think, tools, keep_alive=None, ollama_host=None):
    """Direct HTTP fallback for the blocking Ollama path. The ollama-
    python client wraps responses in Pydantic models; for tool-call
    responses, `Message.tool_calls[i].function.arguments` is typed as
    `dict` — but smaller models (gpt-oss:20b, some qwen3 variants,
    older mistral fine-tunes) return arguments as a JSON-encoded string
    instead. Pydantic rejects that with ValidationError before our
    _coerce_tool_call_args can run. This path bypasses the Pydantic
    layer by POSTing to /api/chat directly and returning the raw JSON;
    _coerce_tool_call_args downstream handles the str-or-dict shape."""
    import json as _json
    import urllib.error as _urlerror
    import urllib.request as _urlrequest

    url = (ollama_host or _resolve_ollama_host()) + "/api/chat"
    body = {
        "model": model,
        "messages": list(messages),
        "stream": False,
    }
    if options:
        body["options"] = options
    if think is not None:
        body["think"] = think  # False too — see _chat_ollama_stream
    if tools:
        body["tools"] = tools
    ka = _coerce_keep_alive(keep_alive)
    if ka is not None:
        body["keep_alive"] = ka
    req = _urlrequest.Request(
        url,
        data=_json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        # 120s ceiling so a wedged local Ollama doesn't hang the worker
        # for 5 minutes — closer to the streaming watchdog window
        # rather than well past it (audit L15).
        with _urlrequest.urlopen(req, timeout=120) as resp:
            return _json.loads(resp.read().decode("utf-8"))
    except _urlerror.HTTPError as e:
        raise LLMBackendError(f"Ollama HTTP {e.code} {e.reason}") from e
    except _urlerror.URLError as e:
        raise LLMBackendError(f"Ollama unreachable: {e.reason}") from e
    except Exception as e:
        raise LLMBackendError(f"Ollama raw call failed: {type(e).__name__}: {e}") from e


def _chat_ollama_blocking(model, messages, options, think, tools, keep_alive=None, ollama_host=None, request_timeout_secs=None) -> ChatResult:
    if ollama is None:
        raise LLMBackendError("ollama library not installed (pip install ollama)")
    kwargs = {"model": model, "messages": messages}
    if options:
        kwargs["options"] = options
    if think is not None:
        kwargs["think"] = think  # False too — see _chat_ollama_stream
    if tools:
        kwargs["tools"] = tools
    ka = _coerce_keep_alive(keep_alive)
    if ka is not None:
        kwargs["keep_alive"] = ka

    chat_fn = _ollama_chat_callable(ollama_host, request_timeout_secs)
    try:
        resp = chat_fn(**kwargs)
    except Exception as e:
        # Smaller open models (gpt-oss:20b is the canonical offender;
        # some qwen3 + smaller mistral fine-tunes too) return tool-call
        # arguments as a JSON-encoded string instead of a parsed dict.
        # ollama-python's Pydantic Message model rejects that shape with
        # ValidationError. When this happens AND we passed tools in,
        # fall back to a direct HTTP call so we can parse the response
        # ourselves and let _coerce_tool_call_args handle the string.
        # On any other failure (network down, ollama not running, bad
        # model name, etc.), propagate as-is.
        if not tools or not _is_pydantic_validation_error(e):
            raise
        resp = _ollama_chat_raw(model, messages, options, think, tools, keep_alive, ollama_host)
    return _normalize_ollama_response(resp)


def _normalize_ollama_chunk(chunk):
    """Convert an Ollama streaming chunk to a Chunk. Returns None on empty."""
    if chunk is None:
        return None
    msg = chunk.get("message") if isinstance(chunk, dict) else getattr(chunk, "message", None)
    if msg is None:
        return None
    if isinstance(msg, dict):
        content = msg.get("content") or ""
        thinking = msg.get("thinking") or ""
        tool_calls = msg.get("tool_calls") or []
    else:
        content = getattr(msg, "content", "") or ""
        thinking = getattr(msg, "thinking", "") or ""
        tool_calls = getattr(msg, "tool_calls", []) or []
    if not content and not thinking and not tool_calls:
        return None
    # Pass tool_calls through (dict- or Pydantic-shaped — consumers
    # use _tc_field) instead of dropping the chunk: chat(stream=True,
    # tools=[...]) on Ollama used to silently discard tool-call
    # chunks entirely.
    return Chunk(content=content, thinking=thinking, tool_calls=list(tool_calls))


def _normalize_ollama_response(resp) -> ChatResult:
    msg = resp.get("message") if isinstance(resp, dict) else getattr(resp, "message", None)
    if msg is None:
        return ChatResult()
    if isinstance(msg, dict):
        content = msg.get("content") or ""
        thinking = msg.get("thinking") or ""
        tool_calls = msg.get("tool_calls") or []
    else:
        content = getattr(msg, "content", "") or ""
        thinking = getattr(msg, "thinking", "") or ""
        tool_calls = getattr(msg, "tool_calls", []) or []
    usage = {
        "prompt_tokens": (resp.get("prompt_eval_count") if isinstance(resp, dict) else getattr(resp, "prompt_eval_count", 0)) or 0,
        "completion_tokens": (resp.get("eval_count") if isinstance(resp, dict) else getattr(resp, "eval_count", 0)) or 0,
        # Prefill / generation durations (nanoseconds) — surfaced in
        # usage.log so local-model cache reuse is measurable, not a guess.
        "prompt_eval_duration": (resp.get("prompt_eval_duration") if isinstance(resp, dict) else getattr(resp, "prompt_eval_duration", 0)) or 0,
        "eval_duration": (resp.get("eval_duration") if isinstance(resp, dict) else getattr(resp, "eval_duration", 0)) or 0,
    }
    return ChatResult(content=content, thinking=thinking, tool_calls=tool_calls, usage=usage)


# ─── OpenRouter backend ──────────────────────────────────────────────────────

def build_openrouter_provider_routing(provider_order=None, allow_fallbacks=True):
    """Return a `provider` dict for OpenRouter's `/chat/completions` body, or
    None if no routing should be sent.

    `provider_order` is a list of OpenRouter provider slugs (e.g.
    ["DeepInfra", "Together"]). Empty list / None → return None, meaning
    "let OpenRouter pick by default" — we deliberately don't emit an empty
    `provider` block. `allow_fallbacks=False` is only included when
    explicitly set so the payload stays minimal in the common case.

    Use case: pin a kin to a specific inference provider for models whose
    NSFW/content policy is enforced provider-by-provider rather than
    model-baked (Xiaomi MiMo is the canonical example — some providers
    filter, others don't). Without pinning, OpenRouter's default routing
    can silently swap providers between requests.
    """
    if not provider_order:
        return None
    cleaned = [p.strip() for p in provider_order if isinstance(p, str) and p.strip()]
    if not cleaned:
        return None
    routing = {"order": cleaned}
    if not allow_fallbacks:
        routing["allow_fallbacks"] = False
    return routing


def _build_openrouter_payload(model, messages, options, think_effort, tools, cache, stream, show_thinking=True, cache_ttl="auto", provider_routing=None):
    """Build the JSON body for an OpenRouter /chat/completions request.

    `think_effort` is the four-state tier: "off" / "low" / "medium" /
    "high". OpenRouter normalizes the reasoning controls per provider:
      - "off"    → reasoning.enabled=False (explicit disable; needed
                   for models like Claude reasoning / o-series that
                   default to ON)
      - "low"    → reasoning.effort="low"
      - "medium" → reasoning.enabled=True (provider chooses budget)
      - "high"   → reasoning.effort="high"

    `show_thinking` controls whether the model's reasoning is returned
    in the response at all. False adds `reasoning.exclude=True` to the
    payload — the model thinks internally, but reasoning is not sent
    back over the wire, eliminating any chance of it leaking into
    visible output. This matters specifically for models like MiMo
    that don't structurally separate reasoning from content in their
    response (reasoning markdown like `**Considering response...**`
    appears in the `content` field, where our display-time
    `show_thinking=False` filter can't catch it because it never
    landed in the separate `thinking` field). With exclude=True the
    leak is structurally impossible — the reasoning never reaches us.

    We deliberately don't send max_tokens for thinking — that was an
    earlier bug where reasoning.max_tokens=0 meant "disabled" on
    Anthropic. effort is the cleaner cross-provider knob."""
    payload = {
        "model": _openrouter_model_id(model),
        "messages": messages,
        "stream": bool(stream),
    }
    # Provider routing — pin to specific inference providers when the
    # caller passes a routing dict (built by build_openrouter_provider_routing).
    # Used to make NSFW-content-policy enforcement predictable on models
    # where different providers behave differently (Xiaomi MiMo etc).
    if provider_routing:
        payload["provider"] = provider_routing
    if options:
        # Map Ollama-style options into OpenAI/OpenRouter-style fields where they exist
        if "temperature" in options:    payload["temperature"] = options["temperature"]
        if "top_p" in options:          payload["top_p"] = options["top_p"]
        if "top_k" in options:          payload["top_k"] = options["top_k"]
        if "min_p" in options:          payload["min_p"] = options["min_p"]
        if "repeat_penalty" in options: payload["repetition_penalty"] = options["repeat_penalty"]
        if "presence_penalty" in options:  payload["presence_penalty"] = options["presence_penalty"]
        if "frequency_penalty" in options: payload["frequency_penalty"] = options["frequency_penalty"]
        if "num_predict" in options:    payload["max_tokens"] = options["num_predict"]
        if "stop" in options:           payload["stop"] = options["stop"]
    if think_effort == "off":
        payload["reasoning"] = {"enabled": False}
    elif think_effort == "low":
        payload["reasoning"] = {"effort": "low"}
    elif think_effort == "medium":
        payload["reasoning"] = {"enabled": True}
    elif think_effort == "high":
        payload["reasoning"] = {"effort": "high"}
    # When the kin's show_thinking is False, add exclude=True so the
    # provider doesn't return reasoning in the response at all. The
    # model still benefits from thinking internally; we just never
    # see the trace. Required for models like MiMo that emit
    # reasoning as part of `content` instead of via a separate
    # field — there, our display-time filter can't help because
    # nothing identifies the prose as reasoning. Harmless on
    # well-behaved models (Anthropic, OpenAI o-series, DeepSeek-R1)
    # because their reasoning was already going to the separate
    # thinking field which the UI doesn't render.
    if not show_thinking and "reasoning" in payload and payload["reasoning"].get("enabled") is not False:
        payload["reasoning"]["exclude"] = True
    if tools:
        payload["tools"] = tools
    if cache and _supports_caching(model):
        # Top-level cache_control auto-advances the breakpoint across turns.
        # In testing (2026-05-11) this writes the cache fine in streaming mode
        # but doesn't read on subsequent streamed turns. The per-content-block
        # form below works for streaming reads — we apply both forms; the
        # server uses whichever it routes correctly.
        cc = {"type": "ephemeral"}
        # Per-kin cache_ttl: "auto" sends nothing (provider default — 5m
        # on Anthropic since 2026-03-06). "5m" / "1h" send the explicit
        # form, but only when the provider honors it (Anthropic + Google).
        # Other providers silently ignore "ttl"; we skip it anyway to keep
        # payloads clean and avoid future spec drift.
        if cache_ttl in ("5m", "1h") and _provider_honors_explicit_ttl(model):
            cc["ttl"] = cache_ttl
        payload["cache_control"] = cc
        # Copy the messages list + any system message we mutate so the
        # caller's input isn't modified. The tool loop reuses its
        # history across iterations; without this copy, cache_control
        # markers accumulate on the same dict each turn (audit L10).
        new_messages = list(messages)
        payload["messages"] = new_messages
        for i, m in enumerate(new_messages):
            if m.get("role") != "system":
                continue
            c = m.get("content")
            m_copy = dict(m)
            if isinstance(c, str) and c.strip():
                m_copy["content"] = [{
                    "type": "text",
                    "text": c,
                    "cache_control": dict(cc),
                }]
                new_messages[i] = m_copy
            elif isinstance(c, list) and c:
                last = c[-1]
                if isinstance(last, dict):
                    new_c = list(c)
                    new_c[-1] = dict(last)
                    new_c[-1]["cache_control"] = dict(cc)
                    m_copy["content"] = new_c
                    new_messages[i] = m_copy
            break  # only the first system msg
    return payload


class _HostConnectionCache:
    """Persistent HTTPSConnection cache for blocking OpenRouter calls.

    Stdlib's `urllib.request.urlopen` opens a fresh TCP + TLS handshake
    on every call — 200-500ms of per-request overhead that accumulates
    over a chat session and (more importantly) leaves us vulnerable to
    transient provider state after idle. `http.client.HTTPSConnection`
    supports keep-alive at the protocol level, but you have to hold
    onto the connection object to actually reuse it across requests.
    This cache does that.

    Two guards against the failure mode where pooled connections go
    stale silently:

      1. **Idle eviction.** Connections idle for more than 5 minutes
         get closed and rebuilt. Long-idle connections at intermediate
         proxies / load balancers tend to die without telling us, and
         the next request on a dead connection fails opaquely. Five
         minutes is conservative — most real failures we've observed
         are after 10+ minutes of idle, but ours is a desktop app
         that may genuinely have long gaps between sends.

      2. **Error retry.** Any socket-level error during a request
         drops the cached connection and retries once with a freshly
         opened one. Transparent to the caller.

    Thread-safe via check-out / check-in semantics: `_acquire` POPS
    the connection from the cache (so only one thread holds it at a
    time), and `_mark_used` returns it on success. If two threads
    race for the same host, one gets the cached conn and the other
    opens a fresh one; whichever finishes first re-caches its conn,
    and the second closes its own to avoid leaking. This is
    deliberately not per-host queueing — concurrent calls to the
    same host are rare enough (background distillation + Telegram
    send is the realistic collision) that the extra connection on
    contention is cheaper than serializing requests.

    Prior shape was peek-not-pop: both threads received the SAME
    HTTPSConnection object, both called `conn.request(...)`, the
    second hit `CannotSendRequest('Request-sent')` because the line
    was mid-call. The retry loop didn't help — `_acquire` handed it
    back. See git history for the incident that surfaced it.

    The streaming path doesn't use this cache. A streaming response's
    lifetime spans the entire reply (often tens of seconds for
    reasoning models), so the per-handshake savings is proportionally
    small AND we'd have to add explicit "I'm done reading" signaling
    so the cache can mark the connection idle. Not worth it for the
    streaming case; blocking is where the latency adds up.
    """

    IDLE_EVICTION_SECONDS = 300  # 5 minutes

    def __init__(self):
        # (host, port) -> (HTTPSConnection, last_used_monotonic_ts)
        self._connections = {}
        self._lock = threading.Lock()

    def request_json(self, url, *, headers, body, timeout):
        """POST `body` to `url` with `headers`, returning
        (status, response_body_str, response_headers_dict).

        Uses the cached HTTPSConnection if fresh; otherwise opens a
        new one. Retries once on socket-level failures with a fresh
        connection (which catches the stale-connection failure mode
        — long-idle connection that the server already closed
        without notifying us)."""
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname
        port = parsed.port or 443
        path = parsed.path or "/"
        if parsed.query:
            path = path + "?" + parsed.query
        key = (host, port)

        last_err = None
        for attempt in range(2):
            conn = self._acquire(key, timeout)
            try:
                conn.request("POST", path, body=body, headers=headers)
            except (http.client.HTTPException, OSError) as e:
                # Failure at request-WRITE time — the server never got
                # a complete request, so retrying with a fresh
                # connection is safe. This is the stale-connection
                # failure mode the retry exists for.
                last_err = e
                self._drop(key, conn)
                continue
            try:
                resp = conn.getresponse()
                status = resp.status
                raw = resp.read().decode("utf-8", errors="replace")
                resp_headers = dict(resp.getheaders())
            except (http.client.HTTPException, OSError):
                # Failure AFTER the request was sent (waiting on /
                # reading the response — socket.timeout is an OSError
                # subclass and lands here). The server may already be
                # processing — and billing — the completion, so a
                # re-POST would double-send and double-bill it. Drop
                # the connection and surface the error instead.
                self._drop(key, conn)
                raise
            self._mark_used(key, conn)
            return status, raw, resp_headers
        raise last_err if last_err is not None else RuntimeError("request_json: unreachable")

    def _acquire(self, key, timeout):
        """Check out a connection for this host. POPS the entry from
        the cache so no other thread can use the same connection
        concurrently. Returns either the previously-cached connection
        (if fresh) or a brand-new one."""
        host, port = key
        now = time.monotonic()
        with self._lock:
            entry = self._connections.pop(key, None)
        if entry is not None:
            conn, last_used = entry
            if now - last_used > self.IDLE_EVICTION_SECONDS:
                # Stale — close and rebuild. The HTTPSConnection
                # itself may still be technically "open" from our
                # side, but the server / intermediate proxy has
                # likely closed it already after this much idle.
                # Trying to reuse it would fail mid-request,
                # which is exactly the "first send after idle"
                # failure mode this cache exists to prevent.
                try:
                    conn.close()
                except Exception:
                    pass
            else:
                return conn
        return http.client.HTTPSConnection(host, port, timeout=timeout)

    def _mark_used(self, key, conn):
        """Return a connection to the cache after a successful request.
        If another thread already cached a different connection for
        the same host (because they raced and both opened fresh
        ones), close ours rather than overwriting — otherwise the
        displaced connection leaks open."""
        with self._lock:
            existing = self._connections.get(key)
            if existing is None:
                self._connections[key] = (conn, time.monotonic())
                return
        # Lock released — close the loser outside it.
        try:
            conn.close()
        except Exception:
            pass

    def _drop(self, key, conn):
        """Discard a connection after a failed request. The
        connection is not in the cache during use (see _acquire),
        so we just close it. The `key` arg is kept for symmetry
        with _mark_used and in case future logic wants it."""
        try:
            conn.close()
        except Exception:
            pass


# Single module-level cache, shared across threads. Telegram bot
# polling and desktop chat both go through this for blocking calls.
_OR_BLOCKING_CONN_CACHE = _HostConnectionCache()


def _openrouter_blocking_request(payload):
    """POST to OpenRouter /chat/completions (non-streaming) via the
    persistent-connection cache. Returns the parsed JSON dict.

    Raises OpenRouterAuthError / OpenRouterRateLimitError /
    LLMBackendError on non-2xx responses — same exception shape as
    the urllib-based path. Streaming calls still go through the
    older _openrouter_request() because their connection lifetime is
    too long to pool cleanly."""
    key_val = _resolve_openrouter_key()
    if not key_val:
        raise OpenRouterAuthError(
            "No OpenRouter API key. Set OPENROUTER_API_KEY env var or write "
            f"{{\"key\": \"sk-or-...\"}} to {OPENROUTER_KEY_FILE}."
        )
    url = f"{OPENROUTER_BASE}/chat/completions"
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {key_val}",
        "Content-Type": "application/json",
        # OpenRouter asks for these to identify your app in their dashboard.
        "HTTP-Referer": "https://github.com/glasswings-lang/hearthkin",
        "X-Title": "Hearthkin",
        # Explicit keep-alive — without it some intermediate proxies
        # may downgrade to Connection: close, which kills our pooling.
        "Connection": "keep-alive",
    }
    status, raw, resp_headers = _OR_BLOCKING_CONN_CACHE.request_json(
        url, headers=headers, body=body, timeout=60,
    )
    if status >= 400:
        _raise_openrouter_error_from_status(status, raw, resp_headers)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise LLMBackendError(
            f"OpenRouter returned non-JSON (status {status}): {raw[:200]}"
        ) from e


def _extract_openrouter_error_detail(body):
    """Pull a human-useful error message out of an OpenRouter error
    response body. OpenRouter's shape carries the upstream provider's
    raw error in `error.metadata.raw` — when present, that's almost
    always more diagnostic than the top-level `error.message` (which
    can be the generic "Provider returned error" with no specifics).

    Returns a single detail string combining the available signals.
    Always returns something non-empty (falls back to the raw body
    snippet if parsing fails entirely)."""
    if not body:
        return ""
    try:
        parsed = json.loads(body)
    except Exception:
        return body[:500]
    err = parsed.get("error") or {}
    msg = (err.get("message") or "").strip()
    metadata = err.get("metadata") or {}
    raw = (metadata.get("raw") or "").strip() if isinstance(metadata, dict) else ""
    provider = (metadata.get("provider_name") or "").strip() if isinstance(metadata, dict) else ""
    parts = []
    if msg:
        parts.append(msg)
    if raw and raw != msg:
        # Trim the raw to something readable while preserving the
        # diagnostic head. Mistral's overflow message + Anthropic's
        # validation messages both fit comfortably in 600 chars.
        snippet = raw[:600]
        if provider:
            parts.append(f"[{provider} raw: {snippet}]")
        else:
            parts.append(f"[raw: {snippet}]")
    if not parts:
        return body[:500]
    return " ".join(parts)


def _log_openrouter_error_body(status, body):
    """Append the full OpenRouter error response body to a forensic
    log so we can diagnose generic-message 400s after the fact. Always
    on; bypasses the conversation logging toggle (same pattern as
    empty_replies.log and telegram_failures.log).

    Body is truncated to 4000 chars on disk to avoid unbounded growth
    on pathological errors, but that's well above any real OpenRouter
    error response so we don't lose useful detail in practice."""
    try:
        from kin_persistence import LOGS_DIR
        import datetime
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        line = f"{ts} status={status} body={(body or '')[:4000]}\n"
        with open(LOGS_DIR / "openrouter_errors.log", "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        # Never let logging block error propagation.
        pass


def _raise_openrouter_error_from_status(status, body, headers):
    """Equivalent of _raise_openrouter_error for the cached blocking
    path. We don't get a urllib.error.HTTPError there — just the raw
    status / body / headers — so the parsing is split out."""
    _log_openrouter_error_body(status, body)
    detail = _extract_openrouter_error_detail(body) or body
    if status == 429:
        # `headers` here is a plain dict from resp.getheaders() — NOT
        # the case-insensitive HTTPMessage the urllib path gets — so
        # match the header name case-insensitively ourselves.
        retry_after = None
        try:
            ra_val = next(
                (v for k, v in (headers or {}).items()
                 if str(k).lower() == "retry-after"),
                "0",
            )
            retry_after = int(ra_val) or None
        except Exception:
            retry_after = None
        raise OpenRouterRateLimitError(f"Rate limited: {detail}", retry_after=retry_after)
    if status == 401:
        raise OpenRouterAuthError(f"OpenRouter auth error {status}: {detail}")
    raise LLMBackendError(f"OpenRouter error {status}: {detail}")


def _openrouter_request(payload, *, stream):
    """POST to OpenRouter /chat/completions. Returns the urllib response object.

    Caller is responsible for reading the response (line-by-line for SSE,
    .read() for blocking).
    """
    key = _resolve_openrouter_key()
    if not key:
        raise OpenRouterAuthError(
            "No OpenRouter API key. Set OPENROUTER_API_KEY env var or write "
            f"{{\"key\": \"sk-or-...\"}} to {OPENROUTER_KEY_FILE}."
        )
    url = f"{OPENROUTER_BASE}/chat/completions"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    # OpenRouter asks for these to identify your app in their dashboard.
    req.add_header("HTTP-Referer", "https://github.com/glasswings-lang/hearthkin")
    req.add_header("X-Title", "Hearthkin")
    try:
        return urllib.request.urlopen(req, timeout=120 if stream else 60)
    except urllib.error.HTTPError as e:
        _raise_openrouter_error(e)


def _raise_openrouter_error(http_err):
    """Convert a urllib HTTPError into a typed LLMBackendError. Logs the
    full body to openrouter_errors.log and includes upstream
    `metadata.raw` in the surfaced detail when present (Mistral and
    others sometimes return a generic top-level message with the real
    diagnostic in the metadata)."""
    status = http_err.code
    try:
        body = http_err.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    _log_openrouter_error_body(status, body)
    detail = _extract_openrouter_error_detail(body) or body
    if status == 429:
        retry_after = None
        try:
            retry_after = int(http_err.headers.get("Retry-After", "0")) or None
        except Exception:
            retry_after = None
        raise OpenRouterRateLimitError(f"Rate limited: {detail}", retry_after=retry_after)
    if status == 401:
        # Same typed error the blocking path raises, so callers'
        # "no/bad API key" handling works regardless of which path
        # the request took.
        raise OpenRouterAuthError(f"OpenRouter auth error {status}: {detail}")
    raise LLMBackendError(f"OpenRouter error {status}: {detail}")


# Wall-clock deadline for a stream that's alive but not progressing:
# SSE heartbeat comments reset the per-read socket timeout (bytes keep
# arriving) AND count as watchdog proof-of-life, so a provider that
# heartbeats forever without emitting tokens would otherwise hang the
# worker until app restart. If no REAL progress (content / thinking /
# tool_calls / usage frame) lands within this window, the stream is
# declared stalled and the call fails loudly instead.
_SSE_STALL_DEADLINE_SECS = 300


def _chat_openrouter_stream(model, messages, options, think_effort, tools, cache, show_thinking=True, cache_ttl="auto", provider_routing=None) -> Iterator[Chunk]:
    payload = _build_openrouter_payload(model, messages, options, think_effort, tools, cache, stream=True, show_thinking=show_thinking, cache_ttl=cache_ttl, provider_routing=provider_routing)
    resp = _openrouter_request(payload, stream=True)
    # SSE: lines starting with "data: ", terminated by "data: [DONE]".
    final_usage = None
    last_progress = time.monotonic()
    try:
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                continue
            # SSE comment line (`:` prefix) — used as a keepalive by some
            # providers. No content; the connection is alive but no new
            # data this beat. Emit a heartbeat Chunk so the watchdog's
            # chunk-count-based liveness check stays satisfied even when
            # a slow provider goes silent between actual tokens.
            if line.startswith(":"):
                # Heartbeats deliberately do NOT count as progress —
                # only real chunks reset the stall clock. See
                # _SSE_STALL_DEADLINE_SECS.
                if time.monotonic() - last_progress > _SSE_STALL_DEADLINE_SECS:
                    raise LLMBackendError(
                        "stream stalled: provider sent heartbeats but no "
                        f"tokens for {_SSE_STALL_DEADLINE_SECS}s"
                    )
                yield Chunk(heartbeat=True)
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = event.get("choices") or []
            if not choices:
                # Some events carry usage info only (final event in newer SSE format)
                if event.get("usage"):
                    last_progress = time.monotonic()
                final_usage = _merge_streaming_usage(final_usage, event)
                continue
            delta = choices[0].get("delta") or {}
            content = _extract_delta_content(delta)
            thinking = _extract_delta_thinking(delta)
            tool_call_deltas = delta.get("tool_calls") or []
            # Debug: log raw delta to diagnose interleaving issues
            if _LOG.isEnabledFor(logging.DEBUG):
                _LOG.debug(f"SSE delta: {json.dumps(delta, ensure_ascii=False)}")
                _LOG.debug(f"  -> content={content!r}  thinking={thinking!r}")
            if content or thinking or tool_call_deltas:
                last_progress = time.monotonic()
                yield Chunk(
                    content=content,
                    thinking=thinking,
                    tool_calls=tool_call_deltas,
                )
            # Final-usage event in some streams comes attached to a choices entry
            final_usage = _merge_streaming_usage(final_usage, event)
    finally:
        try:
            resp.close()
        except Exception:
            pass
    yield Chunk(done=True, usage=final_usage)


def _chat_openrouter_blocking(model, messages, options, think_effort, tools, cache, show_thinking=True, cache_ttl="auto", provider_routing=None) -> ChatResult:
    payload = _build_openrouter_payload(model, messages, options, think_effort, tools, cache, stream=False, show_thinking=show_thinking, cache_ttl=cache_ttl, provider_routing=provider_routing)
    # Retry-on-empty. OpenRouter's upstream providers occasionally
    # return a 200 with no choices, or choices with empty content +
    # tool_calls + thinking — typically a transient issue at the
    # provider (cold-start, momentary capacity blip, dropped
    # downstream stream). Without retry, the user sees "[no reply
    # from model]" and resends manually, where the second send
    # usually succeeds. This matches what every modern LLM client
    # (openai-sdk, anthropic-sdk, openclaw) does by default; urllib
    # gives us none of it, so we do it explicitly.
    #
    # Two attempts is enough — if both come back empty, something is
    # actually wrong (model misconfigured, prompt too long, etc.)
    # and we should surface the empty result so the user knows.
    last_data = None
    for attempt in range(2):
        data = _openrouter_blocking_request(payload)
        last_data = data
        choices = data.get("choices") or []
        if not choices:
            usage_dict = _usage_with_cost(data)
            # Don't retry an empty response that already cost money —
            # the second call would just bill us again (audit L1).
            billed_empty = (
                isinstance(usage_dict.get("cost"), (int, float))
                and usage_dict["cost"] > 0
            )
            if attempt == 0 and not billed_empty:
                time.sleep(0.5)
                continue
            return ChatResult(usage=usage_dict)
        message = choices[0].get("message") or {}
        content = _extract_message_content(message)
        thinking = _extract_message_thinking(message)
        tool_calls = message.get("tool_calls") or []
        # All-three-empty also counts as a failed response worth
        # retrying. A message with thinking but no content can be
        # legitimate (caller passed think=True and the model only
        # returned reasoning tokens), so we don't retry on that;
        # similarly tool_calls without content is a normal tool-loop
        # iteration. Only the empty-empty-empty case is a problem.
        if not content and not thinking and not tool_calls and attempt == 0:
            usage_dict = _usage_with_cost(data)
            billed_empty = (
                isinstance(usage_dict.get("cost"), (int, float))
                and usage_dict["cost"] > 0
            )
            if not billed_empty:
                time.sleep(0.5)
                continue
            # Billed empty — return so we don't burn a second call
            # on the same probably-broken provider response (audit L1).
            return ChatResult(
                content=content, thinking=thinking,
                tool_calls=tool_calls, usage=usage_dict,
            )
        return ChatResult(
            content=content,
            thinking=thinking,
            tool_calls=tool_calls,
            usage=_usage_with_cost(data),
        )
    # Unreachable — both attempts either return or sleep+continue.
    # Defensive fall-through to placate the type checker and to give
    # a sane result if some future edit changes the loop shape.
    return ChatResult(usage=_usage_with_cost(last_data))


# ─── Content / thinking extraction (OpenRouter variants) ─────────────────────

def _extract_delta_content(delta):
    """Pull text content from a streaming delta."""
    c = delta.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        # Anthropic-style content blocks; concatenate any text blocks
        out = []
        for b in c:
            if isinstance(b, dict) and b.get("type") in (None, "text"):
                out.append(b.get("text") or "")
        return "".join(out)
    return ""


def _extract_delta_thinking(delta):
    """Pull reasoning content from a streaming delta. Handles three providers."""
    # DeepSeek-R1 style
    rc = delta.get("reasoning_content")
    if isinstance(rc, str) and rc:
        return rc
    # OpenAI o-series style
    r = delta.get("reasoning")
    if isinstance(r, str) and r:
        return r
    # Anthropic style: thinking block in content list
    c = delta.get("content")
    if isinstance(c, list):
        out = []
        for b in c:
            if isinstance(b, dict) and b.get("type") == "thinking":
                out.append(b.get("text") or b.get("thinking") or "")
        if out:
            return "".join(out)
    return ""


def _extract_message_content(message):
    return _extract_delta_content(message)  # same shape rules


def _extract_message_thinking(message):
    return _extract_delta_thinking(message)


# ─── Model list (for the browser) ────────────────────────────────────────────

def _ollama_show_raw(name, timeout=5, host=None):
    """Hit Ollama's /api/show directly. The Python client's ShowResponse
    drops the `capabilities` field in some versions, so we go via HTTP
    when we need it. Returns the parsed dict on success, None on any
    failure (caller treats as "no info available")."""
    try:
        body = json.dumps({"name": name}).encode("utf-8")
        req = urllib.request.Request(
            (host or _resolve_ollama_host()) + "/api/show",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def set_ollama_keep_alive(model, keep_alive, *, host=None, only_if_loaded=True):
    """Update an already-running Ollama model's keep_alive timer WITHOUT
    reloading it — so a 'Keep model loaded' change takes effect live instead
    of only riding the next chat request. Pass keep_alive=0 (or "0") to
    unload.

    When `only_if_loaded` is True (the realtime-toggle case) this first
    checks /api/ps and does nothing if the model isn't currently in memory
    — applying a keep_alive must never force a cold load. Best-effort:
    returns True on success, False otherwise, never raises."""
    if not model or str(model).startswith("openrouter/"):
        return False
    base = (host or _resolve_ollama_host()).rstrip("/")
    try:
        if only_if_loaded:
            req = urllib.request.Request(base + "/api/ps")
            with urllib.request.urlopen(req, timeout=5) as resp:
                loaded = (json.loads(resp.read().decode("utf-8")).get("models")
                          or [])
            if not any((m.get("name") == model or m.get("model") == model)
                       for m in loaded):
                return False
        ka = _coerce_keep_alive(keep_alive)
        # No "prompt" key => Ollama treats this as a load/keep-alive op and
        # just (re)sets the timer on the resident model. Omitting keep_alive
        # falls back to Ollama's own default (the "" / 5-minute choice).
        body = {"model": model, "stream": False}
        if ka is not None:
            body["keep_alive"] = ka
        req = urllib.request.Request(
            base + "/api/generate", data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return True
    except Exception:
        return False


def list_ollama_models(host=None):
    """Return locally-installed Ollama models in an OpenRouter-shaped
    dict so the model browser can render them with the same filter +
    detail UI.

    Capabilities (tools/vision/thinking) are NOT fetched here — that's
    a per-model HTTP round-trip and we don't want a dialog open to do
    one call per locally-installed model. The browser can lazy-fetch
    via `_ollama_show_raw` when the user selects a row."""
    if ollama is None:
        return []
    # The model browser is the only caller, and it always passes the machine
    # the user explicitly picked: a URL for a saved/remote machine, or None
    # for "This machine" (localhost). Do NOT fall back to the global
    # _OLLAMA_HOST_OVERRIDE here. That override is set to the remote whenever a
    # remote-hosted kin is loaded — letting it leak in meant picking "This
    # machine" (host=None) still listed the remote's models, so the list never
    # switched back to local. None must mean localhost, per this function's
    # contract: bare ollama.list() reads OLLAMA_HOST env or the localhost
    # default, never the in-app override.
    target = host or None
    host_url = host or "localhost"
    try:
        if target:
            resp = ollama.Client(host=target).list()
        else:
            resp = ollama.list()
    except Exception as e:
        # Raise (don't swallow to []) so callers — the model browser — can
        # tell the user WHICH host failed and why, instead of silently
        # showing an empty list that reads as "no models installed."
        raise RuntimeError(f"Could not reach Ollama at {host_url}: {e}") from e
    if isinstance(resp, dict):
        items = resp.get("models") or []
    else:
        items = getattr(resp, "models", None) or []
    out = []
    for m in items:
        if isinstance(m, dict):
            name = m.get("model") or m.get("name") or ""
            size = m.get("size") or 0
            details = m.get("details") or {}
            modified_at = m.get("modified_at") or ""
        else:
            name = getattr(m, "model", "") or getattr(m, "name", "")
            size = getattr(m, "size", 0) or 0
            details = getattr(m, "details", None) or {}
            modified_at = str(getattr(m, "modified_at", "") or "")
        if not name:
            continue
        if not isinstance(details, dict):
            details = {
                "family": getattr(details, "family", "") or "",
                "parameter_size": getattr(details, "parameter_size", "") or "",
                "quantization_level": getattr(details, "quantization_level", "") or "",
            }
        family = details.get("family") or "?"
        param_size = details.get("parameter_size") or "?"
        quant = details.get("quantization_level") or "?"
        size_gb = size / (1024 ** 3) if size else 0
        description = (
            f"Local Ollama model. Family: {family}. Parameter size: {param_size}. "
            f"Quantization: {quant}. On-disk: {size_gb:.1f} GB. "
            f"Modified: {modified_at[:19] if modified_at else 'unknown'}."
        )
        out.append({
            "id": name,
            "name": name,
            "description": description,
            "context_length": None,  # lazy: fetched by browser if user selects
            "pricing": {"prompt": "0", "completion": "0"},
            "architecture": {"modality": "text", "input_modalities": ["text"]},
            "supported_parameters": [],  # populated lazily on select
            "_ollama_local": True,
            "_ollama_details": {
                "family": family,
                "parameter_size": param_size,
                "quantization_level": quant,
                "size_bytes": size,
                "modified_at": modified_at,
            },
        })
    return out


def list_openrouter_models(*, force_refresh=False, cache_max_age_hours=24,
                           allow_fetch=True):
    """Return the OpenRouter model catalogue. Cached on disk for a day.

    Fetches with a tight per-attempt timeout and one retry — slow
    networks or transient TLS issues used to stall the first chat-send
    after launch by up to 30s while the user waited on a single
    blocking call (audit L20). If both attempts fail, returns the
    stale cached copy if any (better than nothing for cost / model-
    list lookups) and otherwise an empty list.

    `allow_fetch=False` never touches the network: it returns the
    on-disk cache regardless of age, or [] when there is none. Used
    by the usage-logging pricing lookup, which runs inside a send —
    a stale-cache network refresh there would stall the user's reply
    by up to ~25s roughly daily."""
    cache_path = OPENROUTER_MODELS_CACHE
    if not allow_fetch:
        if cache_path.exists():
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                return data.get("models") or []
            except Exception:
                pass
        return []
    if cache_path.exists() and not force_refresh:
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            age_h = (time.time() - data.get("fetched_at", 0)) / 3600
            if age_h < cache_max_age_hours:
                return data.get("models") or []
        except Exception:
            pass
    # Fetch (with retry on transient failure)
    key = _resolve_openrouter_key()
    last_err = None
    for attempt in range(2):
        req = urllib.request.Request(f"{OPENROUTER_BASE}/models")
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw)
            models = parsed.get("data") or []
            # Atomic write (local temp + os.replace) so a concurrent
            # reader never sees a half-written cache file, and a cache-
            # write failure never discards a successful fetch. Not
            # using kin_persistence's atomic helper — this file lives
            # under ~/.ai_programs, and llm_backend stays importable
            # without the persistence layer.
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = cache_path.with_name(cache_path.name + ".tmp")
                tmp_path.write_text(
                    json.dumps({"fetched_at": time.time(), "models": models}, ensure_ascii=False),
                    encoding="utf-8",
                )
                os.replace(tmp_path, cache_path)
            except OSError:
                pass
            return models
        except Exception as e:
            last_err = e
            if attempt == 0:
                time.sleep(0.5)
                continue
    # Both attempts failed — fall back to the stale cache if any. Hand
    # the operator a diagnostic via save_failures.log.
    try:
        from kin_persistence import append_failure_log
        append_failure_log(
            "save_failures.log", "openrouter",
            "list_openrouter_models (both attempts failed)", last_err,
        )
    except Exception:
        pass
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            return data.get("models") or []
        except Exception:
            pass
    return []


# ─── Tool-call helpers (for callers that want to run a tools loop) ──────────

MAX_TOOL_ITERATIONS = 8

# Minimum per-call output budget for the tool loop. Callers pass a
# conversational reply cap in options["num_predict"] — telegram_token_cap
# (~900) or the room per_turn_token_cap (~800) — sized to stop a runaway
# model from flooding a chat surface with prose. But inside a tool loop
# the model's output also carries tool-call ARGUMENTS, and a write_file's
# `content` argument can be an entire file. Capped at ~900, that JSON
# gets truncated mid-string, fails to parse, and the tool ends up running
# with empty args ("missing required argument"). So the loop floors
# num_predict here: generous enough for a real file write, still a hard
# ceiling (not unbounded). The tight conversational cap still applies to
# plain (non-tool) replies — those take a code path that never reaches
# run_tool_loop.
TOOL_LOOP_MIN_OUTPUT_TOKENS = 8000

# Which tools can actually emit an argument long enough to need that floor.
#
# The floor above is right for a kin writing a file and wrong for every other
# turn, and it used to apply whenever ANY tool was available. What it costs is
# invisible from a chair: the reserve comes straight out of the room left for
# the conversation, so a kin's history quietly shrank on every tool turn and
# grew back on every plain one -- and a history that changes length at the FAR
# END is re-read from cold, which is minutes of silence before a reply starts.
# Measured on a real kin at num_ctx 32768: a configured reply cap of 1024 was
# raised to 8000 on any turn a tool was merely available, taking 5,434 tokens
# (20% of the window) away from history and putting them back the next turn.
# Two kin churned that way all day; a third, whose configured cap happened to
# equal the floor, never did -- same code, no mismatch, no cost.
#
# Names, not a capability probe: what makes an argument big is the argument
# being FILE CONTENT, which no schema field announces. Add a tool here when it
# takes prose or a document as an argument, not when it merely writes
# something somewhere.
_LARGE_ARGUMENT_TOOLS = frozenset({"write_file", "edit_file", "note"})


_kin_large_arg_cache = {}   # kin name -> can it emit a long argument at all


def _kin_may_emit_large_argument(kin_name):
    """Could THIS KIN, on any turn, call a tool that emits a long argument?

    Asked of the kin rather than of the call on purpose. The room held back
    for a reply has to be the same on every turn or the conversation window
    changes size between turns, and that is what makes a prompt get re-read
    from cold. A per-call answer gives two different numbers for the same
    conversation depending on whether that particular turn carried tools.

    Deliberately the kin's WHOLE enabled set, not the effective set for this
    surface or this person -- a narrower per-surface answer would just
    reintroduce the mismatch one level down.

    Cached per process. Fails safe to False: a kin whose tools we cannot read
    keeps the old, tighter behaviour rather than silently losing history to a
    reserve for a tool it may not even have.
    """
    if not kin_name:
        return False
    if kin_name in _kin_large_arg_cache:
        return _kin_large_arg_cache[kin_name]
    result = False
    try:
        from kin_persistence import load_kin_tools
        enabled = set(load_kin_tools(kin_name) or ())
        result = bool(enabled & _LARGE_ARGUMENT_TOOLS)
    except Exception:
        result = False
    _kin_large_arg_cache[kin_name] = result
    return result


def _needs_large_output_reserve(tools):
    """True when this turn's tool set contains something that can emit a long
    argument, so the reserve above is worth what it takes from history.

    Fails SAFE, in the direction of the old behaviour: a tool list we cannot
    read returns True and keeps the floor. A cut-off file write is a broken
    tool call, which is worse than a slow turn -- so uncertainty pays for the
    reserve rather than gambling the write.
    """
    if not tools:
        return False
    try:
        for t in tools:
            fn = (t or {}).get("function") or {}
            name = fn.get("name") or (t or {}).get("name")
            if not name:
                return True          # unreadable entry -- assume the worst
            if name in _LARGE_ARGUMENT_TOOLS:
                return True
        return False
    except Exception:
        return True


def _tc_field(obj, key, default=None):
    """Tool-call objects come in two shapes depending on the backend:
    dicts (OpenAI / OpenRouter) and Pydantic models (Ollama). Pull a
    field from either without caring which one we got."""
    if isinstance(obj, dict):
        val = obj.get(key, default)
    else:
        val = getattr(obj, key, default)
    return val if val is not None else default


def _coerce_tool_call_args(args_value):
    """Normalize a tool call's `arguments` field to a Python dict.
    Providers disagree: OpenAI / OpenRouter return JSON strings, Ollama
    returns pre-parsed dicts (its Pydantic Function model has arguments
    as a Mapping). Without this, json.loads(dict) raises
    `the JSON object must be str, bytes or bytearray, not dict` and the
    entire tool call dies inside the worker thread."""
    if isinstance(args_value, dict):
        return args_value
    if isinstance(args_value, str):
        try:
            return json.loads(args_value) if args_value else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _normalize_tool_call_for_history(tc, model):
    """Convert a tool call (dict or Pydantic) into a plain dict in the
    OpenAI shape: {id, type, function: {name, arguments}}. The
    `arguments` field's required type differs by provider:
    - OpenAI / OpenRouter: JSON string (their API rejects dicts)
    - Ollama: dict (its Pydantic Message model rejects strings — the
      validator literally errors with `Input should be a valid
      dictionary`)
    So we coerce to a dict internally, then serialize back to whichever
    shape the active model's backend expects."""
    fn = _tc_field(tc, "function") or {}
    name = _tc_field(fn, "name", "")
    args_value = _tc_field(fn, "arguments", "{}")
    args_dict = _coerce_tool_call_args(args_value)
    if _is_openrouter_model(model):
        args_out = json.dumps(args_dict)
    else:
        args_out = args_dict
    return {
        "id": _tc_field(tc, "id", ""),
        "type": "function",
        "function": {"name": name, "arguments": args_out},
    }


# Content-level tool-call markers some models emit instead of using the
# structured tool_calls channel. When _CONTENT_TOOL_CALL_PATTERNS find a
# match in result.content and result.tool_calls is empty, run_tool_loop
# treats those matches as if they had been emitted structurally.
#
# Conservative by design: only well-formed XML-tagged formats. Bare
# function-call-looking text ("read_file(path='x')") is NOT parsed —
# false positives there would mistake markdown code examples or
# python tutorials in the model's reply for actual tool invocations.
#
# Each pattern is paired with a shape tag that tells _extract_content_tool_calls
# how to read the captured groups:
#   "payload"      — group(1) is a JSON dict with name + arguments/parameters
#   "name+args"    — group(1) is the tool name, group(2) is a JSON args object
#   "xml-nested"   — group(1) is the tool name, group(2) is the inner XML
#                    body containing <parameter=key>value</parameter> tags
#                    (no JSON anywhere — used by MiMo and some Llama
#                    fine-tunes; this is what we observed in the wild)
_CONTENT_TOOL_CALL_PATTERNS = [
    # XML-nested (MiMo, some Llama variants):
    #   <tool_call>
    #     <function=name>
    #       <parameter=key>value</parameter>
    #     </function>
    #   </tool_call>
    # Goes first because the outer <tool_call>...</tool_call> would otherwise
    # match the JSON-payload pattern below with empty contents.
    #
    # Each pattern requires `(?:^|\n)\s*` before the opening tag so an
    # inline mention of `<tool_call>...</tool_call>` (e.g. a model
    # explaining the format in mid-paragraph or quoting a prior turn's
    # tag) doesn't get executed (audit L9). Legitimate tool-call
    # markers are always on their own line in observed traffic, so
    # this doesn't break real extraction.
    (re.compile(
        r"(?:^|\n)\s*<tool_call>\s*<function=([^>\s]+)>\s*(.*?)\s*</function>\s*</tool_call>",
        re.DOTALL,
    ), "xml-nested"),
    # Qwen / DeepSeek-style JSON payload:
    #   <tool_call>{"name": "x", "arguments": {...}}</tool_call>
    (re.compile(r"(?:^|\n)\s*<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL), "payload"),
    # Llama-3.1 function tag with JSON args:
    #   <function=name>{"arg": "val"}</function>
    (re.compile(r"(?:^|\n)\s*<function=([^>\s]+)>\s*(\{.*?\})\s*</function>", re.DOTALL), "name+args"),
    # Llama-3 python_tag:
    #   <|python_tag|>{"name": "x", "parameters": {...}}
    (re.compile(r"(?:^|\n)\s*<\|python_tag\|>\s*(\{.*?\})(?:\n|$)", re.DOTALL), "payload"),
]

# Max reply length scanned for content-embedded tool-call markers. Bounds the
# DOTALL patterns' backtracking cost on crafted input (audit I1). Far larger
# than any real tool call's position in a reply.
_CONTENT_TOOL_CALL_SCAN_CAP = 200_000

# Sub-pattern for the XML-nested format: each <parameter=key>value</parameter>
# pair inside a <function=...> body becomes one entry in the args dict.
_XML_PARAMETER_PATTERN = re.compile(
    r"<parameter=([^>\s]+)>\s*(.*?)\s*</parameter>",
    re.DOTALL,
)


# OpenAI's spec for function names: alphanumeric + underscore + hyphen,
# 1-64 chars. Mistral, Anthropic, Google all converge on this same
# shape via the OpenAI-compatible chat-completions API. Content-extracted
# tool calls go through this gate before being treated as live calls,
# because models that emit tool calls in content sometimes hallucinate
# names with spaces / punctuation / non-ASCII that the structured channel
# wouldn't have produced. Without the gate, an invalid name reaches the
# executor lookup, fails silently with "tool not available", and the
# user sees a cryptic error instead of the call getting skipped at the
# parsing layer.
_TOOL_CALL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_TOOL_CALL_NAME_MAX_LEN = 64


def _extract_content_tool_calls(content):
    """Pull tool calls out of plain-text content as a fallback when the
    model didn't use the structured tool_calls channel. Returns a list
    of OpenAI-shape dicts ({id, function: {name, arguments}}) matching
    what run_tool_loop expects from structured calls. Empty list if no
    recognized markers found or all matches failed to parse.

    See _CONTENT_TOOL_CALL_PATTERNS for the formats recognized. Parse
    failures (malformed JSON inside otherwise-valid tags) are silently
    skipped — the original markers stay in result.content and the user
    will see them, which is a useful "something went wrong" signal."""
    if not content:
        return []
    # ReDoS guard (2026-07 security audit I1): the DOTALL patterns below use
    # lazy `.*?` / `\{.*?\}` interiors that backtrack polynomially on a crafted
    # opener-without-closer blob (an untrusted Telegram/Discord group message
    # can prompt-inject the kin into emitting `<tool_call><function=x>` + a long
    # tail with no closer). Output is already num_predict-capped, but bound the
    # scanned length defensively so the per-reply cost stays constant. A real
    # tool call never sits past this many chars into a reply.
    if len(content) > _CONTENT_TOOL_CALL_SCAN_CAP:
        content = content[:_CONTENT_TOOL_CALL_SCAN_CAP]
    # Mask markdown code regions first: a kin QUOTING the tool-call
    # format inside a fenced block or inline backticks (an example,
    # a tutorial snippet, a prior turn it's discussing) must never
    # have the quoted markup executed. Same span detection
    # extract_inline_thinking uses for `<thinking>` markup.
    code_spans = _code_region_spans(content)
    calls = []
    for pattern, shape in _CONTENT_TOOL_CALL_PATTERNS:
        for match in pattern.finditer(content):
            # Test the tag body (group 1), not match.start() — the
            # match can begin at the newline just before a fence's
            # interior line.
            if _pos_in_spans(match.start(1), code_spans):
                continue
            try:
                if shape == "payload":
                    payload = json.loads(match.group(1))
                    if not isinstance(payload, dict):
                        continue
                    name = payload.get("name") or ""
                    args = payload.get("arguments")
                    if args is None:
                        args = payload.get("parameters", {})
                elif shape == "name+args":
                    name = match.group(1).strip()
                    args = json.loads(match.group(2))
                elif shape == "xml-nested":
                    # <tool_call><function=name><parameter=k>v</parameter>...</function></tool_call>
                    # No JSON anywhere — every parameter is its own XML
                    # element with the value as its text content.
                    name = match.group(1).strip()
                    inner = match.group(2)
                    args = {}
                    for pm in _XML_PARAMETER_PATTERN.finditer(inner):
                        args[pm.group(1)] = pm.group(2)
                else:
                    continue
            except (json.JSONDecodeError, AttributeError):
                continue
            if not name:
                continue
            # Validate the name against the OpenAI function-name spec
            # (^[A-Za-z0-9_-]{1,64}$). A model that emits a tool call
            # via content sometimes hallucinates names with spaces /
            # punctuation / non-ASCII that the structured channel
            # would never produce; without this gate the call reaches
            # the executor lookup, fails with "not available", and
            # the user sees a cryptic downstream error instead of
            # the call being skipped at the parsing layer. Pattern
            # taken from OpenClaw's `tool-call-id.ts` (2026-06-10).
            if (len(name) > _TOOL_CALL_NAME_MAX_LEN
                    or not _TOOL_CALL_NAME_RE.match(name)):
                continue
            # _normalize_tool_call_for_history expects arguments either
            # as a dict or as a JSON string; it coerces internally. We
            # pass through whatever shape we got.
            if not isinstance(args, (dict, str)):
                args = {}
            args_str = json.dumps(args) if isinstance(args, dict) else args
            calls.append({
                "id": f"content_tc_{len(calls)}",
                "type": "function",
                "function": {"name": name, "arguments": args_str},
            })
    return calls


def _strip_extracted_tool_calls(content):
    """Remove recognized tool-call markers from content after they've
    been extracted and routed through execution. The assistant turn
    stored in history then carries the surrounding text (if any) but
    not the raw syntax — important because otherwise the model sees
    its own <tool_call> tags in next-iteration history and may mirror
    the pattern back, creating duplicate calls.

    Applies the same code-region masking as
    _extract_content_tool_calls, so only markers that were actually
    eligible for extraction get stripped — a quoted example inside a
    fence / inline backticks survives in the stored turn."""
    if not content:
        return content
    # ReDoS guard (audit I1): mirror the scan cap in _extract_content_tool_calls
    # so the same DOTALL patterns can't backtrack on a pathologically long reply.
    # A >cap reply is left untouched rather than risking the cost — extraction
    # already only fired on markers within the cap, so nothing routed to
    # execution is left un-stripped in practice.
    if len(content) > _CONTENT_TOOL_CALL_SCAN_CAP:
        return content.strip()
    code_spans = _code_region_spans(content)
    cut = []
    for pattern, _shape in _CONTENT_TOOL_CALL_PATTERNS:
        for m in pattern.finditer(content):
            if _pos_in_spans(m.start(1), code_spans):
                continue
            cut.append([m.start(), m.end()])
    if not cut:
        return content.strip()
    cut.sort()
    merged = [cut[0]]
    for s, e in cut[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    parts = []
    prev = 0
    for s, e in merged:
        parts.append(content[prev:s])
        prev = e
    parts.append(content[prev:])
    return "".join(parts).strip()


def _maybe_inject_webcam_turn(tool_result, kin_name):
    """If a `use_webcam` tool result decodes to JSON announcing a
    captured image, return a synthetic user turn that attaches the
    image so the model sees it on the next iteration. Returns None
    when the result isn't a webcam-success JSON (failure, malformed,
    etc.) — caller skips injection in that case.

    Also returns None when `kin_name` is unset: without a kin to
    resolve relative paths against, `_expand_attachments_for_provider`
    would silently drop the attachment, leaving the model with an
    "(Here's the photo)" text but no actual image — a worse failure
    mode than skipping the injection entirely. In practice the
    desktop / Telegram / room call sites all pass kin_name; this
    guard is for defensive future-proofing.

    The synthetic user message uses a short, neutral prompt — we
    don't want to puppet the kin's voice into a specific reaction.
    """
    if not tool_result or not isinstance(tool_result, str):
        return None
    if not kin_name:
        return None
    import json as _json
    try:
        parsed = _json.loads(tool_result)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    if not parsed.get("ok"):
        return None
    rel = parsed.get("attachment")
    if not isinstance(rel, str) or not rel:
        return None
    return {
        "role": "user",
        "content": "(Here's the webcam photo you just captured.)",
        "attachments": [rel],
    }


def _accumulate_stream_tool_calls(acc, chunk_tcs):
    """Merge one streamed chunk's tool_calls into `acc` (a list of plain
    dicts), mutating it in place. Handles both shapes the streaming path
    yields:

      - OpenRouter / OpenAI **deltas**: carry an `index`, and
        `function.arguments` arrives as string fragments to concatenate
        across chunks; `id` / `name` land on whichever fragment has them.
      - Ollama **whole** tool_calls: no `index`, `arguments` already a
        dict, normally delivered complete in a single chunk.

    Reads every field through _tc_field so dict- and Pydantic-shaped calls
    both work."""
    for tc in chunk_tcs:
        idx = _tc_field(tc, "index", None)
        fn = _tc_field(tc, "function") or {}
        name = _tc_field(fn, "name", None)
        args = _tc_field(fn, "arguments", None)
        tid = _tc_field(tc, "id", None)
        ttype = _tc_field(tc, "type", None)
        if idx is None:
            # Whole tool_call (Ollama) — append complete.
            acc.append({
                "id": tid or "",
                "type": ttype or "function",
                "function": {"name": name or "",
                             "arguments": args if args is not None else ""},
            })
            continue
        # Delta by index (OpenRouter) — grow acc to hold this index.
        while len(acc) <= idx:
            acc.append({"id": "", "type": "function",
                        "function": {"name": "", "arguments": ""}})
        slot = acc[idx]
        if tid:
            slot["id"] = tid
        if ttype:
            slot["type"] = ttype
        if name:
            slot["function"]["name"] = name
        if isinstance(args, str):
            slot["function"]["arguments"] += args
        elif args is not None:
            slot["function"]["arguments"] = args


def chat_collect(model, messages, *, on_content=None, should_stop=None, **kwargs):
    """A streaming chat() call collected into a blocking-shaped ChatResult —
    the public door onto `_chat_collect_streaming`.

    For callers that want a plain blocking result but ALSO want to be able to
    stop the reply partway through: `chat(stream=False)` hands back one
    finished answer and there is no point inside it to interrupt, so a stop
    button can only exist on a streamed call. Pass `on_content=None` (the
    default) when you don't want to render deltas — nothing changes about
    what the caller gets back, it just becomes interruptible.
    """
    return _chat_collect_streaming(
        model, messages,
        on_content=(on_content if on_content is not None else (lambda _t: None)),
        should_stop=should_stop, **kwargs)


def _loop_should_stop(should_stop):
    """Poll a `should_stop` callback, treating any failure as "keep going".
    A flaky stop check must never truncate a healthy reply."""
    if should_stop is None:
        return False
    try:
        return bool(should_stop())
    except Exception:
        return False


def _chat_collect_streaming(model, messages, *, on_content, should_stop=None, **kwargs):
    """Run a STREAMING chat() call but return a blocking-shaped ChatResult,
    forwarding each content delta to `on_content(text)` the instant it
    arrives. This is what lets run_tool_loop stream its talking turn to a
    live surface (desktop sentence-paint / NVDA, Telegram in-place edit)
    while STILL resolving tool calls exactly as the blocking path does — the
    returned ChatResult (content + thinking + tool_calls + usage) is a
    drop-in for `chat(stream=False)`.

    Content deltas from a tool-CALLING turn (usually empty / minimal
    preamble) are forwarded too; that's harmless and, if a model narrates
    before calling a tool, actually surfaces its thinking-out-loud. `kwargs`
    (options, tools, cache, kin_name, surface, ollama_host, …) pass straight
    to chat(); usage logging + prompt fingerprinting happen inside chat() as
    usual. on_content exceptions are swallowed — a rendering hiccup must not
    break the tool loop.

    `should_stop`: optional callable() -> bool, polled once per chunk. When it
    returns True we stop reading the stream and return what the kin has said
    so far, with `stopped=True` set on the result. This is the ONLY way to
    interrupt a reply from outside — on_content exceptions are deliberately
    swallowed (see above), so a rendering callback can't double as a stop
    channel. Its own exceptions are swallowed too and treated as "keep
    going": a flaky stop check must never truncate a healthy reply."""
    content_parts = []
    thinking_parts = []
    tool_acc = []
    usage = {}
    stopped = False
    for chunk in chat(model, messages, stream=True, **kwargs):
        if _loop_should_stop(should_stop):
            stopped = True
            break
        if chunk is None or getattr(chunk, "heartbeat", False):
            continue
        c = getattr(chunk, "content", "") or ""
        if c:
            content_parts.append(c)
            try:
                on_content(c)
            except Exception:
                pass
        t = getattr(chunk, "thinking", "") or ""
        if t:
            thinking_parts.append(t)
        tcs = getattr(chunk, "tool_calls", None)
        if tcs:
            _accumulate_stream_tool_calls(tool_acc, tcs)
        if getattr(chunk, "done", False) and getattr(chunk, "usage", None):
            usage = chunk.usage or {}
    return ChatResult(
        content="".join(content_parts),
        thinking="".join(thinking_parts),
        tool_calls=tool_acc,
        usage=usage or {},
        stopped=stopped,
    )


def run_tool_loop(model, messages, tools, tool_executor, *, options=None, cache=False,
                  on_tool_call=None, on_content=None, on_turn=None,
                  max_iterations=MAX_TOOL_ITERATIONS,
                  tool_result_cap=8000, should_stop=None, **kwargs):
    """Run a non-streaming chat-with-tools loop. Blocks until no more tool calls
    or max_iterations is reached. Returns the final ChatResult.

    tool_executor: dict mapping tool name → callable(args_dict) → str.
    on_tool_call: optional callable(name, args, result, is_error) for
        logging/UI display. is_error is True when the call failed
        structurally — malformed/truncated arguments, an unknown tool,
        or the tool function raising — so a surface can choose to show
        failures even when it suppresses ordinary tool-call display.

    on_content: optional callable(text) — when given, each turn is run
        STREAMING (via _chat_collect_streaming) and every content delta is
        forwarded to it the instant it arrives, so a surface can render the
        kin's reply live (desktop sentence-paint / NVDA, Telegram in-place
        edit) instead of after the whole loop finishes. Tool-call resolution
        is unchanged — the streamed turn is collected into the same
        blocking-shaped ChatResult. When None (every existing caller), the
        loop runs non-streaming exactly as before. The callback fires on the
        loop's thread; the caller is responsible for marshalling to its UI.

    should_stop: optional callable() -> bool. Polled at the top of every
        iteration and, on a streaming turn, once per chunk — so a stop lands
        mid-sentence rather than waiting for a long tool loop to run itself
        out. The result carries `stopped=True` and keeps whatever the kin had
        already said and whatever tool round-trips already completed, so the
        caller can persist a coherent (if short) turn. A tool call already in
        flight is NOT abandoned: killing a half-run `write_file` or `exec`
        partway through would be worse than the wait. Only meaningful
        together with on_content for the mid-sentence case; without
        streaming, the granularity is one turn.

    tool_result_cap: per-tool-result character ceiling before truncation.
        Default 8000 (~2000 tokens) — preserves the original behavior.
        Callers pass the kin's per-config value so the operator can
        raise it for kin that legitimately need to chew through big
        files. A `read_file` returning 60K chars when the cap is 8K
        makes the kin see only 8K of the file with no way to ask for
        the rest in one call.

    Tool exceptions become plain-text error messages, not Python tracebacks.
    Handles both dict-shaped (OpenAI / OpenRouter) and Pydantic-shaped
    (Ollama) tool calls — see _tc_field and _coerce_tool_call_args.

    The returned ChatResult's `messages_added` field carries the intermediate
    assistant-with-tool_calls and tool-result turns the loop appended to its
    internal history (in order). The caller can splice these into the
    persisted conversation between the user message and the final reply so
    the model sees its own past tool calls on subsequent turns. The final
    assistant turn (carrying `result.content`) is NOT in `messages_added` —
    the caller is expected to append that themselves from `result.content`.
    """
    history = list(messages)
    messages_added = []
    last_signature = None
    last_results = None
    retried_empty_reply = False
    # Floor the output budget so tool-call arguments aren't truncated.
    # See TOOL_LOOP_MIN_OUTPUT_TOKENS for the full rationale.
    loop_options = dict(options or {})
    _np = loop_options.get("num_predict")
    if not _np or _np < TOOL_LOOP_MIN_OUTPUT_TOKENS:
        loop_options["num_predict"] = TOOL_LOOP_MIN_OUTPUT_TOKENS
    for _ in range(max_iterations):
        # Asked to stop between iterations — after a tool call resolved but
        # before spending another model call on it. Return what we have with
        # the round-trips so far attached, so the caller persists a coherent
        # turn rather than losing the completed work.
        if _loop_should_stop(should_stop):
            return ChatResult(
                content="", thinking="", tool_calls=[],
                usage={}, messages_added=messages_added, stopped=True,
            )
        # on_turn fires at the START of each turn so a streaming surface can
        # reset its live-paint buffer — the streamed reply then holds only the
        # FINAL talking turn's content, not any tool-turn preamble (which is
        # already captured in messages_added). No-op for non-streaming callers.
        if on_turn is not None:
            try:
                on_turn()
            except Exception:
                pass
        # on_content opt-in: stream this turn (forwarding content deltas to
        # the live surface) but collect a blocking-shaped result so the
        # tool-call handling below is unchanged. Without it, the original
        # non-streaming path — byte-identical for every existing caller.
        if on_content is not None:
            result = _chat_collect_streaming(
                model, history, on_content=on_content, should_stop=should_stop,
                options=loop_options, tools=tools, cache=cache, **kwargs)
        else:
            result = chat(model, history, options=loop_options, stream=False,
                          tools=tools, cache=cache, **kwargs)
        # getattr, not attribute access: the non-streaming branch above hands
        # back whatever chat() returns, and duck-typed stand-ins for it (tests,
        # future providers) predate this field.
        if getattr(result, "stopped", False):
            # Stopped mid-sentence. Any tool calls the model had begun
            # emitting when we cut the stream are half-formed by definition,
            # so they are dropped rather than executed — the person asked us
            # to stop, and running a truncated `write_file` would be the
            # opposite of stopping. What the kin said so far is kept.
            result.tool_calls = []
            result.messages_added = messages_added
            return result
        if not result.tool_calls:
            # Fallback: some models (smaller local Ollama variants
            # especially, but also some OpenRouter-routed models in
            # certain prompt contexts) write tool calls as plain-text
            # markers in the content field instead of using the
            # structured tool_calls channel. Without this fallback,
            # those calls silently drop on the floor, the tool never
            # runs, and the model confabulates a successful result on
            # the next turn — invented file contents, made-up search
            # hits, fictitious memory recalls. See
            # _CONTENT_TOOL_CALL_PATTERNS for the formats parsed.
            extracted = _extract_content_tool_calls(result.content or "")
            if extracted:
                # Strip the markers from content so the assistant
                # turn stored in history is clean. Otherwise the
                # model sees its own raw <tool_call> tags next
                # iteration and is encouraged to repeat the pattern,
                # which can lead to duplicate calls or runaway loops.
                result.content = _strip_extracted_tool_calls(result.content or "")
                result.tool_calls = extracted
            else:
                # Thinking-model-went-silent recovery: some local models
                # (qwen36-opus) ALWAYS think regardless of the think flag and
                # intermittently end a turn after the reasoning with no spoken
                # content — content empty, scratchpad full. The thinking is
                # meta-planning ("As <kin> I should say X"), NOT a usable reply,
                # so we can't surface it; instead re-sample once so the model
                # actually speaks rather than returning silence. Guarded: only
                # when content is empty AND the model produced thinking (so a
                # genuine empty from a non-thinking model is left alone) AND we
                # haven't already retried this turn (no infinite loop).
                if (not (result.content or "").strip()
                        and (result.thinking or "").strip()
                        and not retried_empty_reply):
                    retried_empty_reply = True
                    continue
                result.messages_added = messages_added
                return result
        # Stuck-loop detection (decided AFTER execution, below): the model is
        # only genuinely stuck when it repeats the same call AND gets the same
        # result — that's no progress. A tool like tff (the park game) returns a
        # DIFFERENT result for identical args (each "dig" finds different
        # materials) and IS making progress, so it must not be cut off just
        # for repeating args. We compute the call signature here and compare it
        # together with the results once this iteration's tools have run.
        signature = tuple(sorted(
            (
                _tc_field(_tc_field(tc, "function") or {}, "name", ""),
                json.dumps(
                    _coerce_tool_call_args(
                        _tc_field(_tc_field(tc, "function") or {}, "arguments", "{}")
                    ),
                    sort_keys=True,
                ),
            )
            for tc in result.tool_calls
        ))
        # Append the assistant's tool-calling turn. Normalize tool_calls
        # to plain dicts so the history is JSON-serializable and so the
        # next chat() call sees a consistent shape regardless of which
        # provider produced it.
        normalized_tcs = [_normalize_tool_call_for_history(tc, model) for tc in result.tool_calls]
        assistant_turn = {
            "role": "assistant",
            # "" (not None) for tool-call turns with no narrative: see
            # _coerce_tool_call_assistant_content for why. The chat()
            # entry point also coerces on outbound, but creating the
            # right shape here means run_tool_loop's iteration within
            # a single user turn (call → result → next call) doesn't
            # have to round-trip through the coercion every iteration.
            "content": result.content or "",
            "tool_calls": normalized_tcs,
        }
        history.append(assistant_turn)
        messages_added.append(assistant_turn)
        # Execute each tool call
        iteration_results = []
        for tc in result.tool_calls:
            fn = _tc_field(tc, "function") or {}
            name = _tc_field(fn, "name", "")
            args_value = _tc_field(fn, "arguments", "{}")
            args = _coerce_tool_call_args(args_value)
            # Malformed-args guard. The model emitted a tool call whose
            # arguments were a non-empty string that did not parse as
            # JSON — almost always a truncated/cut-off call. Without
            # this, _coerce_tool_call_args's silent {} fallback runs the
            # tool with no arguments and the user sees a cryptic
            # "missing required argument" from deep inside the function.
            # Catch it here with a message the model can actually act on.
            # (Test the parse directly rather than inferring failure from
            # the coerced value — "{}" / "{ }" are valid empty objects, a
            # legitimate shape for a no-required-arg tool call.)
            arg_malformed = False
            if isinstance(args_value, str) and args_value.strip():
                try:
                    json.loads(args_value)
                except ValueError:
                    arg_malformed = True
            is_error = False
            if arg_malformed:
                tool_result = (
                    f"{name}: tool call arguments were malformed or cut "
                    f"off before they finished — the tool was not run. "
                    f"Re-issue the call with complete arguments."
                )
                is_error = True
            else:
                executor = tool_executor.get(name)
                if executor is None:
                    tool_result = f"{name}: tool not available"
                    is_error = True
                else:
                    try:
                        tool_result = str(executor(args))
                    except Exception as e:
                        tool_result = f"{name}: {e}"
                        is_error = True
            # Size cap — the one exception to zero-transform
            if tool_result_cap > 0 and len(tool_result) > tool_result_cap:
                tool_result = (
                    tool_result[:tool_result_cap]
                    + f"\n[truncated at {tool_result_cap} chars]"
                )
            iteration_results.append(tool_result)
            if on_tool_call is not None:
                try:
                    on_tool_call(name, args, tool_result, is_error)
                except Exception:
                    pass
            tool_turn = {
                "role": "tool",
                "tool_call_id": _tc_field(tc, "id", ""),
                "content": tool_result,
            }
            history.append(tool_turn)
            messages_added.append(tool_turn)
            # use_webcam special case: the tool returned JSON
            # describing a freshly-captured image. To let the model
            # actually SEE the photo (a role=tool text result alone
            # would just be a filename, not a viewable image), inject
            # a synthetic user turn carrying the attachment ref. The
            # NEXT iteration's chat() call expands that attachment via
            # _expand_attachments_for_provider, so the model sees the
            # image natively in its content stream.
            if name == "use_webcam":
                injected = _maybe_inject_webcam_turn(tool_result, kwargs.get("kin_name"))
                if injected is not None:
                    history.append(injected)
                    messages_added.append(injected)
        # Result-aware stuck-loop guard: bail only when this iteration's call
        # signature AND its results both match the previous iteration — same
        # call, same output, genuinely no progress. Repeatable tools that vary
        # their output (tff's "dig", a dice roll, paginated search)
        # are never falsely cut off. On a real stuck loop, give the model one
        # final no-tools turn so the user gets a reply, not a bail marker.
        results_sig = tuple(iteration_results)
        if signature == last_signature and results_sig == last_results:
            final = chat(model, history, options=dict(options or {}),
                         stream=False, cache=cache, **kwargs)
            # This final turn is sent WITHOUT tools, so a model that still
            # wants to call one may emit a content-form tool marker
            # (`<tool_call>...`) as text. The normal loop path strips those
            # (see _strip_extracted_tool_calls above); the bail path must
            # too, or raw markup leaks to the user. Stripping to empty falls
            # through to the explanatory marker below.
            final.content = _strip_extracted_tool_calls(final.content or "")
            if not final.content:
                final.content = ("[Stopped: the last tool call repeated with "
                                 "no change in result.]")
            final.messages_added = messages_added
            return final
        last_signature = signature
        last_results = results_sig
    # Hit iteration cap — return the last result with an explanatory
    # note. Use the caller's original options (not loop_options with
    # the 8000 num_predict floor) and omit tools so chat()'s budget
    # math doesn't over-reserve for schemas + tool-output reserve
    # that this final no-tools call doesn't need (audit L7).
    final = chat(model, history, options=dict(options or {}), stream=False, cache=cache, **kwargs)
    # Same hygiene as the stuck-loop bail: strip any content-form tool
    # marker the model emitted on this final no-tools turn so it doesn't
    # reach the user as raw markup.
    final.content = _strip_extracted_tool_calls(final.content or "")
    if not final.content:
        final.content = (f"[Tool loop exceeded {max_iterations} iterations "
                         "without a final answer.]")
    final.messages_added = messages_added
    return final
