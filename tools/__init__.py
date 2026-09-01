# SPDX-License-Identifier: CC0-1.0

"""Hearthkin tool registry.

How tools work in hearthkin:
  - Each tool is one Python function in its own file under `tools/`.
    The function's name matches the filename (memory_search.py defines
    `def memory_search(...)`, etc.). The model-facing schema is
    auto-derived from the function signature + docstring — see
    `_schema.py`.
  - Tools are opted into per-kin via `~/.hearthkin/kin/<kin>/tools.json`,
    which holds a list of tool names. A tool not in that list is invisible
    to that kin even if the file exists in `tools/`.
  - At chat time, `load_tools(allowed_names)` returns the pair
    (schemas, executor_dict) that `llm_backend.run_tool_loop` expects.

Adding a new tool (the drop-in path):
  1. Create `tools/<name>.py` with a single top-level function `<name>`,
     properly type-annotated, with a docstring whose first paragraph
     reads as the model-facing description.
  2. Import it below and add it to `_REGISTRY`.
  3. Add `"<name>"` to the relevant kin's `tools.json` to enable it.

Why static imports instead of importlib-based discovery: PyInstaller
needs to see imports statically to bundle them into Hearthkin.exe.
Two-line registration is a small price for "build.bat just works."
"""

import inspect

from ._schema import build_schema


# Per-tool imports — populate as tools land:
from .memory_search import memory_search
from .read_file import read_file
from .list_directory import list_directory
from .write_file import write_file
from .edit_file import edit_file
from .note import note
from .fetch_url import fetch_url
from .web_search import web_search
# Note: `exec` shadows the Python builtin inside this module scope.
# Safe because tools/__init__.py doesn't use the builtin, and the
# import-via-_REGISTRY callers don't care about the name.
from .exec import exec
from .list_processes import list_processes
from .kill_process import kill_process
from .context_status import context_status
from .recent_thinking import recent_thinking
from .use_webcam import use_webcam
from .read_staging import read_staging
from .archive_staging import archive_staging
# tff (Time for Family) — a cozy text-adventure game a kin can play through
# one command tool. Thin bridge to the game's headless tff_play layer; see
# tools/tff.py.
from .tff import tff
from .tff import _HOST as _TFF_HOST
from .reach_out import reach_out
from .analyze_sound import analyze_sound


# ----- play-by-typing game registry ----------------------------------- #
#
# The tff tool is one entry in a small name→GameHost registry, so the two
# human-facing ways to play — the desktop "Tend a kin's park" dialog and the
# Telegram `/play <game> <command>` command — dispatch through ONE path, and
# adding a future game means one line here (plus its tool + bucket). Each
# GameHost already serializes its save behind a cross-process lock, so every
# route (kin tool call, desktop dialog, cron, /play) shares the world safely.
GAMES = {
    "tff": _TFF_HOST,
}


def get_game(name):
    """The GameHost for a game by name (case-insensitive), or None."""
    return GAMES.get((name or "").strip().lower())


def list_games():
    """Names of the registered play-by-typing games."""
    return sorted(GAMES)


_REGISTRY = {
    "memory_search": memory_search,
    "read_file": read_file,
    "list_directory": list_directory,
    "write_file": write_file,
    "edit_file": edit_file,
    "note": note,
    "fetch_url": fetch_url,
    "web_search": web_search,
    "exec": exec,
    "list_processes": list_processes,
    "kill_process": kill_process,
    "context_status": context_status,
    "recent_thinking": recent_thinking,
    "use_webcam": use_webcam,
    "read_staging": read_staging,
    "archive_staging": archive_staging,
    "tff": tff,
    "reach_out": reach_out,
    "analyze_sound": analyze_sound,
}


# Tools that are only meaningful when the active model can accept image
# inputs. `load_tools` consults this set and skips the listed tools
# entirely (no schema, no executor) for non-vision models — there's no
# point letting a non-vision kin advertise a tool whose result would
# arrive as an injected user-turn image it can't actually look at.
_REQUIRES_VISION = frozenset({"use_webcam"})


# Tools that are only meaningful during memory tending. `read_staging`
# reads the summarizer's pending notes; `archive_staging` files them away
# once consumed.
#
# THESE ARE NO LONGER HIDDEN WHEN STAGING IS EMPTY, AND THE REASON IS THE
# PROMPT CACHE, NOT THE SCHEMA BUDGET.
#
# Hiding them saved two schemas on turns that could not use them, which is
# a real saving and was the original point. What it overlooked is WHERE
# the tool names sit: the tool-use hint names the available tools, and
# that hint is appended to the SYSTEM BLOCK — position zero of the prompt.
# So staging filling or emptying rewrote the very front of the prompt, and
# a local model reuses its cached work only for an unbroken run from the
# start. Every flip therefore cost a full cold re-read of the entire
# context.
#
# Measured on a real kin: its system block oscillated between 26,738 and
# 26,769 characters — a THIRTY-ONE character difference, exactly
# "archive_staging, " plus "read_staging, " — flipping back and forth
# across 76 turns, each flip discarding a 27,000-character prompt. The kin
# it hit hardest was the one distilling most often, because distillation
# is what writes staging notes and tending is what clears them, so the
# kin with the most memory work had the least cache. The two costs are not
# comparable: two schemas is a few hundred tokens once, a cache miss is
# the whole context re-read, in minutes.
#
# `read_staging` on an empty staging dir answers "nothing pending", which
# is a fine thing for a kin to be told. A stable prompt is worth more than
# a slightly smaller one. If you are tempted to gate something else out of
# the tool list to save schema, check first whether it lands in the system
# block — if it does, the saving is not what it appears to be.
_STAGING_TOOLS = frozenset({"read_staging", "archive_staging"})


def list_available():
    """Names of all tools registered in this build. The per-kin allowlist
    can only enable names that appear here."""
    return sorted(_REGISTRY.keys())


# `reach_out` is proactive-only: a kin uses it to message its operator
# unprompted. It is NOT a per-kin allowlist tool — on an everyday chat turn
# the kin is already in conversation, so offering it there is pointless (and
# was the confusing "why is reach_out a tool checkbox?" wart). It belongs to
# the Proactive-heartbeat feature, which grants it on its own; scheduled cron
# wakes get it too. load_tools strips it from everyday turns and re-grants it
# only when proactive_wake or cron_turn is set.
_PROACTIVE_TOOLS = frozenset({"reach_out"})


# Params that are supplied by the framework (never by the model) and must be
# stripped from every model-facing schema regardless of whether the caller
# passed them in `context`. See the hide logic in `load_tools`.
_FRAMEWORK_HIDDEN_PARAMS = frozenset({"agent_name", "confine_paths"})


def load_tools(allowed_names, *, context=None, model=None, cron_turn=False,
               proactive_wake=False):
    """Build the (schemas, executor_dict) pair for the named tools.

    `allowed_names` is typically the result of `load_agent_tools_file` —
    the per-kin allowlist. Names not in `_REGISTRY` are skipped silently
    (the model just won't see those schemas).

    `context` is an optional dict of framework-supplied parameters (e.g.
    `{"agent_name": "Opal"}`). For each tool, any context key that
    matches one of the tool function's parameter names gets bound: the
    executor injects it on every call, and `build_schema` omits it from
    the model-facing schema. This is how tools learn whose data they're
    operating on without exposing that as a model-controllable input.

    `model` (optional) is the active model id — used to filter out
    capability-gated tools (currently just `use_webcam`, which needs
    vision input). Passing None keeps all tools regardless of
    capability; that's appropriate for callers that don't have a
    model context (e.g. config-validation passes).

    `cron_turn` (optional) marks a scheduled-tend wake-up. It forces the
    staging tools (`read_staging` / `archive_staging`) into the set even
    if staging happens to be momentarily empty, so a tend always has its
    tools. On ordinary turns (the default) those tools appear only when
    the kin actually has pending staging — see `_STAGING_TOOLS`.

    Returns:
      schemas: list of OpenAI-shape tool schemas. Pass as `tools=` to
               `llm_backend.chat()` or `run_tool_loop()`.
      executor_dict: {name: callable(args_dict) -> str}. Pass as
                     `tool_executor=` to `run_tool_loop()`.
    """
    context = context or {}
    # Capability gate: drop vision-only tools when model can't see.
    # Done up here (before the per-tool loop) so the same filtering
    # applies whether the caller passed model explicitly or buried
    # inside context. A None model means "skip the check" — caller
    # is in a no-model context and we shouldn't second-guess.
    if model is not None and _REQUIRES_VISION:
        try:
            from llm_backend import model_supports_images
            vision_ok = model_supports_images(model)
        except Exception:
            vision_ok = False
        if not vision_ok:
            allowed_names = [n for n in allowed_names if n not in _REQUIRES_VISION]

    # Need gate: drop the tending-only tools unless there's something to
    # tend (or this is a scheduled tend). Keeps everyday/play turns lean
    # for small models without ever hiding the tools when tending matters.
    # Proactive gate: reach_out is never an everyday tool. Strip any stale
    # allowlist entry, then grant it only on a heartbeat (proactive_wake) or a
    # scheduled cron wake.
    allowed_names = [n for n in allowed_names if n not in _PROACTIVE_TOOLS]
    if proactive_wake or cron_turn:
        allowed_names = allowed_names + [
            n for n in _PROACTIVE_TOOLS
            if n in _REGISTRY and n not in allowed_names
        ]
    schemas = []
    executor = {}
    for name in allowed_names:
        fn = _REGISTRY.get(name)
        if fn is None:
            continue
        bound, hidden = _bind_context(fn, context)
        # Framework-only params must NEVER appear in the model-facing schema,
        # even if a caller forgot to supply them in context — otherwise they'd
        # become model-settable. `confine_paths` gating a remote surface's
        # path confinement is the load-bearing case (a model could send
        # confine_paths=false to escape); `agent_name` is hidden the same way
        # for consistency. build_schema drops any of these the tool declares.
        hidden = hidden | (_FRAMEWORK_HIDDEN_PARAMS
                           & set(inspect.signature(fn).parameters))
        schema = build_schema(fn, hide_params=hidden)
        if name == "reach_out":
            _name_reach_out_destinations(schema, context)
        schemas.append(schema)
        executor[name] = _make_executor(fn, bound)
    return schemas, executor


def _name_reach_out_destinations(schema, context):
    """List this kin's open destinations INSIDE reach_out's schema.

    The allowlist lives in the operator's config, and a kin cannot read config.
    So without this the kin is holding a `to` parameter with no way to learn
    what may go in it. Observed failure mode: a kin repeatedly composed finished
    letters to people it had no channel to, morning after morning — the door
    existed and nothing ever told it the door's name.

    The labels are the operator's own words for the places, so they are what
    the kin should say back. Best-effort: a config read that fails leaves the
    generic description, which is still true."""
    try:
        from kin_persistence import load_agent_config
        cfg = load_agent_config((context or {}).get("agent_name", "")) or {}
        allowed = (cfg.get("heartbeat") or {}).get("allowed_destinations") or []
        names = [str(d.get("label", "")).strip() for d in allowed
                 if isinstance(d, dict) and str(d.get("label", "")).strip()]
        if not names:
            return
        fn_block = schema.get("function") or {}
        desc = fn_block.get("description") or ""
        fn_block["description"] = desc.rstrip() + (
            "\n\nPlaces your operator has opened to you (pass one of these as "
            "`to`, exactly as written): "
            + ", ".join(f'"{n}"' for n in names)
            + ". Real people are in these. Leave `to` out to reach your "
              "operator instead."
        )
        # Also constrain the parameter itself, so a model that reads schemas
        # more carefully than prose gets the same list.
        props = ((fn_block.get("parameters") or {}).get("properties") or {})
        if "to" in props:
            props["to"]["enum"] = names
    except Exception:
        pass


def _bind_context(fn, context):
    """Return (bound_kwargs, hidden_param_names) — the subset of `context`
    that this function actually declares as a parameter. A context key
    the function doesn't accept is simply ignored (no error)."""
    sig = inspect.signature(fn)
    accepted = set(sig.parameters.keys())
    bound = {k: v for k, v in context.items() if k in accepted}
    return bound, set(bound.keys())


# Common argument-name synonyms keyed by the tool's REAL parameter name.
# Small models pick argument names from whatever tool conventions they were
# trained on, so a tool whose param is `path` gets called with `file` /
# `filename`, `content` arrives as `text` / `data`, etc. Without recovery the
# misnamed key is dropped and the required param blows up with a raw Python
# TypeError that the model can't act on. `_make_executor` consults this map
# (then a single-unknown→single-missing fallback) to re-home a misnamed value
# before giving up. Keyed by real param → synonyms the model might use.
_ARG_SYNONYMS = {
    "path": ("file", "filename", "filepath", "file_path", "pathname", "fname"),
    "content": ("text", "data", "body", "contents", "file_content"),
    "old_string": ("old", "old_str", "search", "find", "target", "old_text"),
    "new_string": ("new", "new_str", "replacement", "replace", "new_text"),
    "query": ("q", "search", "search_query", "term", "keywords"),
    "url": ("link", "uri", "address", "href"),
    "file": ("filename", "name", "path", "fname"),
    "command": ("cmd", "shell", "script", "commandline"),
    "scope": ("surface", "scope_key", "scope_name"),
}


def _make_executor(fn, bound_context):
    """Wrap a tool function so it accepts the args-dict shape the model
    sends (raw JSON-decoded kwargs). Unknown keys are normally dropped
    (defense against hallucinated parameters), BUT before dropping, a
    misnamed argument is re-homed onto a missing required parameter —
    by known synonym first, then by a single-unknown→single-missing
    fallback — so `read_file({"file": ...})` still works instead of
    dying with a cryptic TypeError. A genuinely missing required arg
    returns a clean steering string, never a raw exception. Framework
    context (e.g. agent_name) wins over anything the model sends."""
    sig = inspect.signature(fn)
    params = sig.parameters
    accepted = set(params.keys())
    # Params with no default the model is expected to supply — i.e. excluding
    # framework-bound ones (agent_name etc.), which get filled below.
    required = [
        name for name, p in params.items()
        if p.default is inspect.Parameter.empty
        and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
        and name not in bound_context
    ]

    def runner(args):
        provided = dict(args or {})
        valid = {k: v for k, v in provided.items() if k in accepted}
        unknown = {k: v for k, v in provided.items() if k not in accepted}

        # Synonym pass: re-home a misnamed value onto any accepted param
        # (required OR optional) that wasn't supplied — `text` -> `content`,
        # `file` -> `path`, etc.
        if unknown:
            for p in accepted:
                if p in valid or p in bound_context:
                    continue
                for syn in _ARG_SYNONYMS.get(p, ()):
                    if syn in unknown:
                        valid[p] = unknown.pop(syn)
                        break

        missing = [p for p in required if p not in valid]

        # Unambiguous fallback: exactly one leftover unknown and one missing
        # required param — they're almost certainly the same thing misnamed.
        # Restricted to required so a lone unknown never lands in some random
        # optional slot.
        if len(missing) == 1 and len(unknown) == 1:
            valid[missing[0]] = next(iter(unknown.values()))
            missing = []

        valid.update(bound_context)  # framework wins over model
        if missing:
            return (
                "error: this tool needs argument(s) %s, which were not "
                "provided. You sent: %s. Re-issue the call using the exact "
                "argument name(s) above." % (missing, sorted(provided.keys()))
            )
        return str(fn(**valid))

    return runner


# --- Per-kin allowlist file I/O ------------------------------------ #

def load_agent_tools_file(path):
    """Read the per-kin tools allowlist file. Returns a list of tool names,
    empty list on missing or malformed file. Caller passes the explicit
    path (typically `agent_dir(name) / "tools.json"`) so this module
    doesn't need to know about Hearthkin's runtime-data conventions."""
    import json
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    enabled = data.get("enabled")
    if not isinstance(enabled, list):
        return []
    return [n for n in enabled if isinstance(n, str)]


def save_agent_tools_file(path, enabled):
    """Write the per-kin tools allowlist."""
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"enabled": list(enabled)}, indent=2),
        encoding="utf-8",
    )
