# SPDX-License-Identifier: CC0-1.0

"""
model_utils — Ollama model name parsing, capability detection, dropdown
listing. Extracted from hearthkin.pyw.

ANNOTATION_NO_TOOLS is the visible suffix appended to model names that
don't support tool calls, so the dropdown surfaces capability at a
glance without an extra column. strip_model_annotation strips it for
the actual model name passed to llm_backend.

_ollama_show_raw and _model_supports_tools talk directly to the
Ollama daemon to read model capabilities (Ollama's Python client's
ShowResponse Pydantic model silently drops the `capabilities` field
on some versions, so HTTP is more reliable). Result is cached per
session in _tool_cap_cache, cleared by the "Refresh models" button.

get_models() is what the dropdown calls. Returns annotated names
(`gemma3:27b` or `qwen2.5:7b-instruct  (no tools)`), or a placeholder
string when ollama isn't installed / running.
"""

try:
    import ollama
except ImportError:
    ollama = None


ANNOTATION_NO_TOOLS = "  (no tools)"

# Cached per session, cleared by the user-facing "Refresh models" button.
_tool_cap_cache = {}
_thinking_cap_cache = {}
_context_length_cache = {}
_vision_cap_cache = {}
# Behavioural probe verdicts, keyed by model name. Separate from
# _tool_cap_cache on purpose: that one holds what the model CLAIMS,
# this one holds what it actually DID when asked.
_tool_probe_cache = {}

# One throwaway tool for the probe. Named with a hearthkin_ prefix so it
# can never collide with a real registered tool, and trivial enough that
# failing to call it is about the model, not about the schema.
_PROBE_TOOL = [{
    "type": "function",
    "function": {
        "name": "hearthkin_probe_echo",
        "description": "Echo a word back to the user. Call this whenever "
                       "you are asked to echo a word.",
        "parameters": {
            "type": "object",
            "properties": {
                "word": {"type": "string", "description": "The word to echo."},
            },
            "required": ["word"],
        },
    },
}]
# Cached result of get_models(). Populated lazily on first call,
# cleared by clear_models_cache() — wired to the Refresh Models button.
# Without this cache, kin-switching fires ollama.list() (one HTTP hit)
# plus one /api/show per model on the first switch, every time. With
# ~10-30 local models that's noticeable lag per switch.
_models_cache = None


def _ollama_show_raw(name, timeout=10):
    """Hit ollama's /api/show endpoint directly. The Python client's
    ShowResponse Pydantic model silently drops the `capabilities` field,
    so we can't rely on `ollama.show()` for capability detection.

    Thin delegator: the single implementation lives in
    llm_backend._ollama_show_raw (which honors the host configured via
    llm_backend.set_ollama_host() / the OLLAMA_HOST env var, so
    capability detection always hits the same daemon the chat path
    uses). This wrapper keeps model_utils' callers and its more
    patient 10s default timeout, and imports lazily so model_utils
    never imports llm_backend at module load (llm_backend lazily
    imports model_utils back — the load-time graph stays acyclic)."""
    try:
        from llm_backend import _ollama_show_raw as _backend_show_raw
    except Exception:
        return None
    return _backend_show_raw(name, timeout=timeout)


def _model_supports_tools(name):
    """Return True/False/None (unknown). Cached after first call."""
    if name in _tool_cap_cache:
        return _tool_cap_cache[name]
    if ollama is None:
        return None
    result = None

    # Primary: HTTP API. Returns capabilities reliably across ollama versions.
    raw = _ollama_show_raw(name)
    if raw is not None:
        caps = raw.get("capabilities")
        if isinstance(caps, list):
            result = "tools" in caps or any("tool" in str(c).lower() for c in caps)
            _tool_cap_cache[name] = result
            return result
        # Fall through to template-sniff if capabilities missing
        tmpl = (raw.get("template") or "").lower()
        if ".tools" in tmpl or ".toolcalls" in tmpl or "{{tools}}" in tmpl:
            _tool_cap_cache[name] = True
            return True
        # Capabilities field absent and no template hints -> unknown
        _tool_cap_cache[name] = None
        return None

    # Fallback: Python client (only used if HTTP API is unreachable, which
    # is unusual since we just used it for the model list)
    try:
        # Honor the GUI-configured remote host here too (this is the rare
        # fallback when the HTTP /api/show path failed). Without it, capability
        # detection for a remote-pointed kin would query localhost and come
        # back "unknown." Mirrors get_models() above.
        try:
            from llm_backend import _OLLAMA_HOST_OVERRIDE
        except Exception:
            _OLLAMA_HOST_OVERRIDE = ""
        if _OLLAMA_HOST_OVERRIDE:
            info = ollama.Client(host=_OLLAMA_HOST_OVERRIDE).show(name)
        else:
            info = ollama.show(name)
        caps = info.get("capabilities") if isinstance(info, dict) else getattr(info, "capabilities", None)
        if caps:
            try:
                if any("tool" in str(c).lower() for c in caps):
                    result = True
            except TypeError:
                pass
        if result is None:
            tmpl = info.get("template") if isinstance(info, dict) else getattr(info, "template", "")
            tmpl = (tmpl or "")
            low = tmpl.lower()
            if ".tools" in low or ".toolcalls" in low or "{{tools}}" in low:
                result = True
            else:
                result = None  # we can't tell
    except Exception:
        result = None
    _tool_cap_cache[name] = result
    return result


def probe_tool_calling(name, force=False):
    """Actually ASK the model for one tool call and see whether it makes one.

    `_model_supports_tools` reads Ollama's `capabilities` flag, and that
    flag describes the PLUMBING -- whether the model's template (or
    Ollama's compiled renderer) can express a tool call at all. It cannot
    say whether the weights will ever emit one. A roleplay finetune built
    on a tool-trained base keeps the base's template, so it reports
    `tools` truthfully and then never calls anything: it writes a
    description of the call instead, in prose, which no parser can rescue
    because there is nothing malformed to repair.

    That gap cost a real evening. A kin was moved to such a model, kept
    answering warmly, and simply stopped doing anything -- told outright
    "try it like a tool call", it replied with the identical prose twice.
    Nothing in the app could have said so in advance, because the only
    signal anyone had was the flag that was already saying yes.

    So this asks the question the flag can't: one throwaway tool, one
    direct instruction, through `llm_backend.chat` so it exercises the
    same path a real turn takes rather than a tidier one.

    Returns a dict, cached per session:
      ok      True  = it made the call
              False = it answered without calling
              None  = we couldn't tell (model unreachable, backend error)
      called  list of tool names it called (empty unless ok is True)
      said    what it wrote instead -- the evidence, for showing a person
      error   failure text when ok is None
    """
    if not force and name in _tool_probe_cache:
        return _tool_probe_cache[name]
    result = {"ok": None, "called": [], "said": "", "error": ""}
    try:
        from llm_backend import chat
    except Exception as e:
        result["error"] = "Could not load the chat backend: %s" % e
        return result
    try:
        res = chat(
            name,
            [{"role": "user",
              "content": "Echo the word hearthkin back to me using the "
                         "echo tool. Use the tool; do not just type the word."}],
            tools=_PROBE_TOOL,
            stream=False,
            think=False,
            options={"temperature": 0},
        )
    except Exception as e:
        result["error"] = str(e)
        _tool_probe_cache[name] = result
        return result
    calls = list(getattr(res, "tool_calls", None) or [])
    names = []
    for c in calls:
        try:
            fn = c.get("function") if isinstance(c, dict) else getattr(c, "function", None)
            nm = (fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", None))
            if nm:
                names.append(str(nm))
        except Exception:
            continue
    result["called"] = names
    result["said"] = (getattr(res, "content", "") or "").strip()
    result["ok"] = bool(calls)
    _tool_probe_cache[name] = result
    _save_probe_verdict(name, result["ok"])
    return result


# --- Probe verdicts outlive the process ---------------------------------
#
# The in-memory cache above is per-process, and the process that most needs
# this answer is the one that never has it: `hearthkin_cron` is a FRESH
# SUBPROCESS on every scheduled wake-up, so `probed_tool_calling` returned
# None there every single time. A capability-based decision built on the
# memory cache alone would therefore never once fire at 3am -- the exact
# hour it exists for, and the exact shape of silent uselessness this project
# keeps finding. So the verdict goes to disk.
#
# Only the verdict is persisted, not `said` (the model's evidence text),
# which can be long and is only ever shown live.
#
# Staleness is real: a re-pulled model under the same name can behave
# differently. `clear_tool_probe_cache` already exists and is wired to the
# Refresh Models button; it now clears the file too.
PROBE_VERDICT_FILE = "tool_probe.json"


def _probe_verdict_path():
    from hearthkin_paths import config_dir
    return config_dir() / PROBE_VERDICT_FILE


def _load_probe_verdicts():
    """Disk verdicts as {model: bool}. Never raises -- an unreadable cache
    must degrade to 'never probed', not break a wake-up."""
    try:
        p = _probe_verdict_path()
        if not p.exists():
            return {}
        import json as _json
        with open(p, encoding="utf-8") as f:
            data = _json.load(f)
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, bool)}
    except Exception:
        return {}


def _save_probe_verdict(name, ok):
    """Record one verdict. Only True/False -- an inconclusive probe (model
    unreachable, backend error) must NOT be written, or a network blip
    becomes a permanent 'this model cannot call tools'."""
    if ok is None:
        return
    try:
        data = _load_probe_verdicts()
        if data.get(name) is ok:
            return
        data[name] = bool(ok)
        p = _probe_verdict_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        import json as _json
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(data, f, indent=1, sort_keys=True)
        tmp.replace(p)
    except Exception:
        pass


def probed_tool_calling(name):
    """The cached probe verdict, or None if this model was never probed.

    Deliberately does NOT probe: the model-swap pre-flight runs on the UI
    thread, and an inference call there is a multi-second freeze with a
    screen reader saying nothing -- the exact defect the cron test was
    rewritten to avoid. Callers that want a fresh answer run
    `probe_tool_calling` on a worker thread and report when it lands.
    """
    rec = _tool_probe_cache.get(name)
    if rec:
        return rec.get("ok")
    # Memory miss -> disk. The caller that most needs this answer is a
    # cron subprocess, whose in-memory cache is empty by definition.
    return _load_probe_verdicts().get(name)


def clear_tool_probe_cache():
    """Also removes the on-disk verdicts -- clearing only the memory
    copy would leave a stale answer that outlives the button."""
    """Forget every probe verdict. Wired to the same Refresh Models
    button that clears the capability caches -- a re-pulled model can
    genuinely behave differently."""
    _tool_probe_cache.clear()
    try:
        _probe_verdict_path().unlink()
    except OSError:
        pass


def _model_supports_thinking(name):
    """Return True/False/None (unknown). Cached after first call.

    Ollama's /api/show capabilities list includes a `thinking` entry for
    models that support reasoning tokens (e.g. deepseek-r1, gpt-oss).
    Sending think=True to a model without this capability is a 400 from
    the daemon — exactly the failure mode we hit with Gemma3-27b when a
    kin's think config was left on from a prior model.

    Returns None when capability can't be determined (Ollama unreachable,
    older daemon version, etc.) so the caller can choose to pass through
    rather than second-guess."""
    if name in _thinking_cap_cache:
        return _thinking_cap_cache[name]
    if ollama is None:
        return None
    raw = _ollama_show_raw(name)
    if raw is None:
        return None
    caps = raw.get("capabilities")
    if isinstance(caps, list):
        result = any(str(c).lower() == "thinking" for c in caps)
        _thinking_cap_cache[name] = result
        return result
    # capabilities field absent — we can't tell from /api/show alone
    _thinking_cap_cache[name] = None
    return None


def _model_supports_vision(name):
    """Return True/False/None (unknown) for the Ollama model's image-
    input capability. Cached after first call.

    Ollama's /api/show capabilities list includes a `vision` entry
    for multimodal models (llava family, llama3.2-vision, qwen2-vl,
    bakllava, moondream, etc.). When that capability is present the
    model accepts an `images` field on user messages — a list of
    base64-encoded image bytes or absolute file paths. Without it,
    Ollama will accept the field but the model ignores the image
    entirely and just answers the text (worst-of-both-worlds
    silent failure)."""
    if name in _vision_cap_cache:
        return _vision_cap_cache[name]
    if ollama is None:
        return None
    raw = _ollama_show_raw(name)
    if raw is None:
        return None
    caps = raw.get("capabilities")
    if isinstance(caps, list):
        result = any(str(c).lower() == "vision" for c in caps)
        _vision_cap_cache[name] = result
        return result
    _vision_cap_cache[name] = None
    return None


def _model_context_length(name):
    """Return the model's declared max context length in tokens, or None
    when unknown. Cached after first call.

    Ollama's /api/show response includes `model_info[<arch>.context_length]`
    where `<arch>` varies by model family (e.g. `gemma4.context_length`,
    `llama.context_length`, `qwen2.context_length`). We scan for any key
    ending in `.context_length` rather than hard-coding architectures.
    Useful for surfacing the model's actual ceiling next to the per-kin
    num_ctx slider so the user can choose a sensible value."""
    if name in _context_length_cache:
        return _context_length_cache[name]
    if ollama is None:
        return None
    raw = _ollama_show_raw(name)
    if raw is None:
        return None
    info = raw.get("model_info")
    if isinstance(info, dict):
        for k, v in info.items():
            if isinstance(k, str) and k.endswith(".context_length"):
                try:
                    ctx = int(v)
                except (TypeError, ValueError):
                    continue
                _context_length_cache[name] = ctx
                return ctx
    _context_length_cache[name] = None
    return None


def annotate_model_name(name, supports_tools):
    if supports_tools is False:
        return f"{name}{ANNOTATION_NO_TOOLS}"
    return name


def strip_model_annotation(s):
    if s and s.endswith(ANNOTATION_NO_TOOLS):
        return s[: -len(ANNOTATION_NO_TOOLS)]
    return s


def get_models(force_refresh=False):
    """Return annotated display names for the dropdown. Cached across
    calls — the cache is invalidated by clear_models_cache() (wired to
    the Refresh Models button) and starts empty on app launch. Pass
    `force_refresh=True` to bypass the cache without invalidating it.

    Error states ('(Ollama not running)' etc.) are NOT cached so the
    next call retries — useful when Ollama gets started after Hearthkin
    is already up."""
    global _models_cache
    if _models_cache is not None and not force_refresh:
        return list(_models_cache)
    if ollama is None:
        return ["(ollama not installed)"]
    try:
        # Honor the configured host so a remote Ollama setup sees its
        # own model list rather than whatever happens to be installed
        # on localhost. When no override is set the module-level
        # ollama.list() is used unchanged.
        try:
            from llm_backend import _OLLAMA_HOST_OVERRIDE
            if _OLLAMA_HOST_OVERRIDE:
                result = ollama.Client(host=_OLLAMA_HOST_OVERRIDE).list()
            else:
                result = ollama.list()
        except Exception:
            result = ollama.list()
        names = [m["model"] for m in result.get("models", [])]
        if not names:
            _models_cache = ["(no models found)"]
            return list(_models_cache)
        _models_cache = [
            annotate_model_name(n, _model_supports_tools(n)) for n in names
        ]
        return list(_models_cache)
    except Exception:
        return ["(Ollama not running)"]


def clear_models_cache():
    """Force the next get_models() call to re-fetch from Ollama. Called
    from the Refresh Models button alongside _tool_cap_cache.clear() so
    'refresh' really does reload everything model-related."""
    global _models_cache
    _models_cache = None
    _context_length_cache.clear()
    _vision_cap_cache.clear()
    # Thinking capability is detected from the same /api/show data —
    # without this, a model update that adds/removes the `thinking`
    # capability stays invisible until app restart.
    _thinking_cap_cache.clear()
    # The behavioural probe too: a re-pulled tag under the same name can
    # be a different finetune with different habits, and a stale "this
    # one never calls tools" is exactly the wrong thing to keep.
    _tool_probe_cache.clear()


def find_annotated(clean_name, annotated_list):
    for s in annotated_list:
        if strip_model_annotation(s) == clean_name:
            return s
    return clean_name
