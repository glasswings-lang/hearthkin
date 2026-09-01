"""Regression tests for llm_backend's outbound message-normalization pipeline.

This is the code that keeps biting in production: every cross-provider /
cross-model quirk Hearthkin has hit lives here as a normalize / remap / strip
/ coerce step inside chat(). Each one was a real 400 (or worse, silent
corruption) before it was fixed. This suite pins the current behavior so a
future edit can't silently reopen one of those wounds — and so the codebase
is provably maintainable by anyone (human or AI), not just whoever has the
history in their head.

Same convention as test_app_prompts.py: NO pytest dependency, plain asserts
via check(), a summary line, exit 1 on any failure.

Run:  python tests/test_llm_normalization.py
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import llm_backend as lb  # noqa: E402

_failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        _failures.append(name)


# ─────────────────────────────────────────────────────────────────────────
# Provider detection — the dispatch that gates every provider-specific fix.
# ─────────────────────────────────────────────────────────────────────────

def test_provider_detection():
    check("openrouter prefix detected",
          lb._is_openrouter_model("openrouter/anthropic/claude-sonnet-4"))
    check("bare ollama name is not openrouter",
          not lb._is_openrouter_model("qwen36-opus-q4:latest"))
    check("non-string is not openrouter",
          not lb._is_openrouter_model(None))
    check("mistral routed via openrouter detected",
          lb._is_mistral_model("openrouter/mistralai/mistral-large"))
    check("anthropic is not mistral",
          not lb._is_mistral_model("openrouter/anthropic/claude-sonnet-4"))
    check("local model is not mistral",
          not lb._is_mistral_model("mistral-small3.2:24b"))


# ─────────────────────────────────────────────────────────────────────────
# _coerce_tool_call_args — the dict/string arguments coercion at the root of
# the Ollama-vs-OpenAI shape disagreement.
# ─────────────────────────────────────────────────────────────────────────

def test_coerce_tool_call_args():
    check("dict passes through",
          lb._coerce_tool_call_args({"a": 1}) == {"a": 1})
    check("valid json string parsed",
          lb._coerce_tool_call_args('{"a": 1}') == {"a": 1})
    check("empty string -> empty dict",
          lb._coerce_tool_call_args("") == {})
    check("malformed json -> empty dict (no raise)",
          lb._coerce_tool_call_args('{"a": ') == {})
    check("non-str/dict -> empty dict",
          lb._coerce_tool_call_args(None) == {})


# ─────────────────────────────────────────────────────────────────────────
# _strip_extra_message_fields — drops storage bookkeeping before send.
# Mistral 400s on unknown keys; this is the universal safety net.
# ─────────────────────────────────────────────────────────────────────────

def test_strip_extra_message_fields():
    msgs = [{
        "role": "user", "content": "hi",
        "ts": 123, "source": "telegram:group:5", "sender_name": "@x",
    }]
    out = lb._strip_extra_message_fields(msgs)
    check("bookkeeping keys dropped",
          set(out[0].keys()) == {"role", "content"})
    check("content preserved through strip",
          out[0]["content"] == "hi")

    clean = [{"role": "user", "content": "hi"}]
    check("already-clean message passes through by reference (no churn)",
          lb._strip_extra_message_fields(clean)[0] is clean[0])

    check("thinking is a whitelisted field, kept",
          "thinking" in lb._strip_extra_message_fields(
              [{"role": "assistant", "content": "x", "thinking": "t"}])[0])

    nondict = ["raw", {"role": "user", "content": "y", "bogus": 1}]
    out2 = lb._strip_extra_message_fields(nondict)
    check("non-dict entries pass through untouched", out2[0] == "raw")


# ─────────────────────────────────────────────────────────────────────────
# _coerce_tool_call_assistant_content — null content on tool-call turns
# triggered the Anthropic "seizure" corruption. Must become "".
# ─────────────────────────────────────────────────────────────────────────

def test_coerce_null_content():
    msgs = [{"role": "assistant", "content": None,
             "tool_calls": [{"id": "1", "function": {"name": "f", "arguments": "{}"}}]}]
    out = lb._coerce_tool_call_assistant_content(msgs)
    check("null content on tool-call turn becomes empty string",
          out[0]["content"] == "")

    no_tools = [{"role": "assistant", "content": None}]
    check("null content WITHOUT tool_calls is left alone",
          lb._coerce_tool_call_assistant_content(no_tools)[0]["content"] is None)

    normal = [{"role": "assistant", "content": "hello",
               "tool_calls": [{"id": "1", "function": {"name": "f", "arguments": "{}"}}]}]
    check("real content on tool-call turn untouched",
          lb._coerce_tool_call_assistant_content(normal)[0]["content"] == "hello")


# ─────────────────────────────────────────────────────────────────────────
# _normalize_history_tool_args — Ollama wants dict args, OpenRouter wants
# JSON-string args. Either shape can land on disk; coerce per active model.
# ─────────────────────────────────────────────────────────────────────────

def _one_tool_msg(args):
    return [{"role": "assistant", "content": "",
             "tool_calls": [{"id": "1", "type": "function",
                             "function": {"name": "read_file", "arguments": args}}]}]


def test_normalize_tool_args():
    # Ollama path: string args must become a dict.
    out = lb._normalize_history_tool_args(_one_tool_msg('{"path": "x"}'),
                                          "qwen36-opus-q4:latest")
    got = out[0]["tool_calls"][0]["function"]["arguments"]
    check("ollama: string args coerced to dict", got == {"path": "x"})

    # OpenRouter path: dict args must become a JSON string.
    out2 = lb._normalize_history_tool_args(_one_tool_msg({"path": "x"}),
                                           "openrouter/anthropic/claude-sonnet-4")
    got2 = out2[0]["tool_calls"][0]["function"]["arguments"]
    check("openrouter: dict args coerced to JSON string",
          isinstance(got2, str) and json.loads(got2) == {"path": "x"})

    # Already-correct shape passes through by reference (no needless rebuild).
    ok = _one_tool_msg({"path": "x"})
    check("ollama: already-dict args pass through by reference",
          lb._normalize_history_tool_args(ok, "qwen36-opus-q4:latest")[0] is ok[0])

    # No tool_calls -> untouched.
    plain = [{"role": "user", "content": "hi"}]
    check("no-tool message passes through by reference",
          lb._normalize_history_tool_args(plain, "qwen36-opus-q4:latest")[0] is plain[0])


# ─────────────────────────────────────────────────────────────────────────
# _consolidate_system_messages — today's fix. Strict Ollama templates (e.g.
# qwen36-opus-q4) reject a system message that isn't first; fold them all
# to a single leading block. No-op for the common single-system case.
# ─────────────────────────────────────────────────────────────────────────

def test_consolidate_system_messages():
    msgs = [
        {"role": "system", "content": "You are Vesper."},
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "[hearthkin: earlier messages truncated]"},
        {"role": "user", "content": "still there?"},
    ]
    out = lb._consolidate_system_messages(msgs)
    roles = [m["role"] for m in out]
    check("mid-conversation system note folded out of the body",
          roles == ["system", "user", "user"])
    check("exactly one system message after consolidation",
          roles.count("system") == 1)
    check("system message is first",
          out[0]["role"] == "system")
    check("system contents joined in original order",
          out[0]["content"] == "You are Vesper.\n\n[hearthkin: earlier messages truncated]")
    check("non-system messages keep their order",
          [m["content"] for m in out if m["role"] == "user"] == ["hi", "still there?"])

    # Fast no-op: the common case (one leading system) returns the SAME object.
    single = [{"role": "system", "content": "x"}, {"role": "user", "content": "y"}]
    check("single leading system returns input unchanged (by reference)",
          lb._consolidate_system_messages(single) is single)

    # No system message at all -> unchanged.
    nosys = [{"role": "user", "content": "y"}]
    check("no system message returns input unchanged (by reference)",
          lb._consolidate_system_messages(nosys) is nosys)

    # A single system message that isn't first DOES get moved to the front.
    late = [{"role": "user", "content": "y"},
            {"role": "system", "content": "s"}]
    out2 = lb._consolidate_system_messages(late)
    check("a non-leading single system message gets moved to the front",
          out2[0]["role"] == "system" and out2[1]["role"] == "user")


# ─────────────────────────────────────────────────────────────────────────
# _inline_mid_conversation_system_notes — runs BEFORE the fold above and
# leaves it almost nothing to do. Hearthkin's own `[hearthkin: ...]` notes
# accumulate in a kin's history one per turn; folding them all to position 0
# rewrote the front of the prompt every turn and re-read the whole context
# from cold. They now stay where they happened, re-roled to `user`.
# Full property test (append-only across turns, with a positive control):
# tests/test_system_note_placement.py.
# ─────────────────────────────────────────────────────────────────────────

def test_inline_mid_conversation_system_notes():
    msgs = [
        {"role": "system", "content": "You are Vesper."},
        {"role": "system", "content": "[hearthkin: earlier messages truncated]"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "system", "content": "[hearthkin: park — you did `look`]"},
    ]
    out = lb._inline_mid_conversation_system_notes(msgs)
    check("mid-conversation note is re-roled in place, not moved",
          [m["role"] for m in out]
          == ["system", "system", "user", "assistant", "user"])
    check("the leading system run is left alone (it is the system prompt, and "
          "the rolling-window marker sits inside it)",
          out[0] is msgs[0] and out[1] is msgs[1])
    check("note content survives the re-role untouched",
          out[4]["content"] == "[hearthkin: park — you did `look`]")
    check("the caller's list is not mutated", msgs[4]["role"] == "system")

    clean = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    check("no mid-conversation note returns the input unchanged (by reference)",
          lb._inline_mid_conversation_system_notes(clean) is clean)

    # Never split an assistant-tool_calls -> tool pairing; that is a 400.
    paired = [
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "system", "content": "[hearthkin: note]"},
        {"role": "tool", "tool_call_id": "c1", "content": "x"},
    ]
    check("a note immediately before a tool result stays system",
          lb._inline_mid_conversation_system_notes(paired)[1]["role"] == "system")

    blank = [{"role": "system", "content": "s"},
             {"role": "user", "content": "u"},
             {"role": "system", "content": "  "}]
    check("a blank note is dropped, not sent as an empty user turn",
          [m["role"] for m in lb._inline_mid_conversation_system_notes(blank)]
          == ["system", "user"])


# ─────────────────────────────────────────────────────────────────────────
# _mint_short_id_9 — deterministic 9-char hex id, collision-avoiding.
# ─────────────────────────────────────────────────────────────────────────

def test_truncate_hysteresis():
    sysm = [{"role": "system", "content": "S" * 4000}]
    convo = []
    for k in range(400):
        convo.append({"role": "user", "content": f"u{k} " + "x" * 200})
        convo.append({"role": "assistant", "content": f"a{k} " + "y" * 200})
    MAXT = 8000

    # A small (under-cap) conversation is returned unchanged, by reference.
    small = sysm + convo[:2]
    out_s, trunc_s = lb._truncate_messages(small, 1_000_000)
    check("under-cap conversation is not truncated", trunc_s is False and out_s is small)

    # An over-cap conversation truncates and fits (allowing for the marker).
    outN, tN = lb._truncate_messages(sysm + convo, MAXT)
    check("over-cap conversation gets truncated", tN is True)
    check("truncated result fits under cap (+marker)", lb._est_tokens(outN) <= MAXT + 200)

    # The cache-hit property: a small append must NOT shift the kept prefix.
    new = [{"role": "user", "content": "new q"},
           {"role": "assistant", "content": "new a"}]
    outN1, _ = lb._truncate_messages(sysm + convo + new, MAXT)
    keptN1_old = outN1[:len(outN1) - 2]
    stable = (len(outN) == len(keptN1_old)
              and all(outN[i] == keptN1_old[i] for i in range(len(outN))))
    check("kept prefix stays identical across a small append (cache-hit)", stable)


def test_collapse_consecutive_user_turns():
    # The snowball case: trailing consecutive user turns (failed crons).
    msgs = [
        {"role": "system", "content": "You are Vesper."},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "wake-up A"},
        {"role": "user", "content": "wake-up B"},
        {"role": "user", "content": "wake-up C"},
    ]
    out = lb._collapse_consecutive_user_turns(msgs)
    roles = [m["role"] for m in out]
    check("consecutive user turns merged to one",
          roles == ["system", "assistant", "user"])
    check("merged user content joins all in order",
          out[-1]["content"] == "wake-up A\n\nwake-up B\n\nwake-up C")

    # Proper alternation is left untouched (by reference).
    alt = [{"role": "user", "content": "a"},
           {"role": "assistant", "content": "b"},
           {"role": "user", "content": "c"}]
    check("alternating conversation passes through unchanged",
          lb._collapse_consecutive_user_turns(alt) is alt)

    # Tool turns between user turns are NOT disturbed.
    with_tools = [
        {"role": "user", "content": "do it"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "done"},
        {"role": "user", "content": "thanks"},
    ]
    out2 = lb._collapse_consecutive_user_turns(with_tools)
    check("tool round-trip pairing left intact",
          [m["role"] for m in out2] == ["user", "assistant", "tool", "user"])


def test_ensure_user_turn_present():
    # The trigger: a tool-loop continuation whose user query was dropped by
    # truncation — qwen36-opus-q4's template raises "No user query found in
    # messages." on this shape. A synthetic user turn must be spliced in
    # after the leading system block.
    dropped = [
        {"role": "system", "content": "You are Bracken."},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "file contents"},
    ]
    out = lb._ensure_user_turn_present(dropped)
    roles = [m["role"] for m in out]
    check("synthetic user turn inserted after system block",
          roles == ["system", "user", "assistant", "tool"])
    check("synthetic user turn is a non-empty plain string",
          isinstance(out[1]["content"], str) and out[1]["content"].strip())

    # A normal conversation with a real user turn passes through unchanged
    # (by reference — no allocation on the common path).
    normal = [
        {"role": "system", "content": "You are Bracken."},
        {"role": "user", "content": "hey"},
        {"role": "assistant", "content": "hi"},
    ]
    check("conversation with a user turn passes through unchanged",
          lb._ensure_user_turn_present(normal) is normal)

    # An image turn (list content) counts as a user query — no injection.
    image = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": [{"type": "text", "text": "look"}]},
    ]
    check("image (list-content) user turn counts as present",
          lb._ensure_user_turn_present(image) is image)

    # A user turn that is only a tool_response wrapper does NOT count — the
    # template excludes it, so we still inject.
    only_tool_resp = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "<tool_response>x</tool_response>"},
        {"role": "assistant", "content": "ok"},
    ]
    out2 = lb._ensure_user_turn_present(only_tool_resp)
    check("tool_response-only user turn triggers injection",
          [m["role"] for m in out2] == ["system", "user", "user", "assistant"])


def test_hang_watchdog_guard():
    # The wall-clock guard recognizes a request timeout however it's
    # wrapped, so it can be logged + surfaced instead of hanging a worker
    # (and every same-host kin behind it) forever. Type-name match, so
    # these stand in for httpx.ReadTimeout / ConnectTimeout.
    class ReadTimeout(Exception):
        pass

    class ConnectTimeout(Exception):
        pass

    check("direct ReadTimeout is recognized",
          lb._is_request_timeout(ReadTimeout("timed out")))
    # wrapped in a __cause__ chain (ollama may re-raise wrapping httpx)
    try:
        try:
            raise ConnectTimeout("boom")
        except ConnectTimeout as inner:
            raise RuntimeError("wrapper") from inner
    except RuntimeError as e:
        check("timeout seen through a __cause__ chain",
              lb._is_request_timeout(e))
    check("a plain error is NOT treated as a timeout",
          not lb._is_request_timeout(ValueError("nope")))
    check("ollama client timeout object builds",
          lb._ollama_client_timeout() is not None)


def test_watchdog_timeout_resolution():
    # The cron/blocking guard reads the SAME per-kin watchdog_timeout_minutes
    # the streaming UI watchdog uses, so one setting governs every path.
    orig = lb._load_agent_config_cached
    try:
        lb._load_agent_config_cached = lambda name: {"watchdog_timeout_minutes": 40}
        check("per-kin override wins (40 min -> 2400s)",
              lb._resolve_watchdog_timeout_secs("K", "qwen") == 2400)
        lb._load_agent_config_cached = lambda name: {
            "watchdog_timeout_minutes": 0, "num_ctx": 8192}
        check("auto at 8k ctx -> 5 min (300s)",
              lb._resolve_watchdog_timeout_secs("K", "qwen") == 300)
        lb._load_agent_config_cached = lambda name: {
            "watchdog_timeout_minutes": 0, "num_ctx": 32768}
        check("auto scales with ctx (32k -> 8 min = 480s)",
              lb._resolve_watchdog_timeout_secs("K", "qwen") == 480)
        lb._load_agent_config_cached = lambda name: {
            "watchdog_timeout_minutes": 0, "num_ctx": 10_000_000}
        check("auto caps at 30 min (1800s)",
              lb._resolve_watchdog_timeout_secs("K", "qwen") == 1800)
    finally:
        lb._load_agent_config_cached = orig
    check("no kin_name -> fallback default",
          lb._resolve_watchdog_timeout_secs("", "qwen") == lb._OLLAMA_READ_TIMEOUT)

    # Unattended surfaces get a floor on top. The size-derived formula is
    # tuned for a reply and sat barely above the real work of a big local
    # model: on gemma4:31b a distillation chunk of a 20k bite measured
    # 360-455s against a derived cap of 480s. That margin is a coin flip
    # per chunk, and it was losing about four times in ten — each loss
    # stopping the redistill it belonged to.
    orig = lb._load_agent_config_cached
    try:
        lb._load_agent_config_cached = lambda name: {
            "watchdog_timeout_minutes": 0, "num_ctx": 32768}
        check("a chat at 32k ctx still gets the derived 8 minutes",
              lb._resolve_watchdog_timeout_secs("K", "g", "desktop") == 480)
        check("...but an unattended distillation is not cut off at 8",
              lb._resolve_watchdog_timeout_secs("K", "g", "distill")
              == lb._UNATTENDED_MIN_TIMEOUT_SECS)
        check("consolidation counts as unattended too",
              lb._resolve_watchdog_timeout_secs("K", "g", "consolidate")
              == lb._UNATTENDED_MIN_TIMEOUT_SECS)
        # The floor RAISES, never lowers: a per-kin override tuned for chat
        # responsiveness must not become the budget for background work.
        lb._load_agent_config_cached = lambda name: {
            "watchdog_timeout_minutes": 6}
        check("a short per-kin override doesn't shorten unattended work",
              lb._resolve_watchdog_timeout_secs("K", "g", "distill")
              == lb._UNATTENDED_MIN_TIMEOUT_SECS)
        check("...while the chat path still honours it exactly",
              lb._resolve_watchdog_timeout_secs("K", "g", "desktop") == 360)
        lb._load_agent_config_cached = lambda name: {
            "watchdog_timeout_minutes": 90}
        check("a generous override still wins over the floor",
              lb._resolve_watchdog_timeout_secs("K", "g", "distill") == 5400)
    finally:
        lb._load_agent_config_cached = orig
    check("an unknown surface is treated as attended",
          lb._resolve_watchdog_timeout_secs("", "qwen", "telegram")
          == lb._OLLAMA_READ_TIMEOUT)

    t = lb._ollama_client_timeout(1200)
    check("client timeout uses the resolved read seconds",
          getattr(t, "read", None) == 1200.0)


def test_mint_short_id_9():
    used = set()
    a = lb._mint_short_id_9("seed", used)
    check("minted id is 9 chars", len(a) == 9)
    check("minted id is lowercase hex",
          all(c in "0123456789abcdef" for c in a))
    check("minting mutates the used set", a in used)

    # Same seed, fresh used set -> same id (determinism).
    b = lb._mint_short_id_9("seed", set())
    check("same seed + same pre-state -> same id", a == b)

    # Same seed, but already used -> a DIFFERENT id (collision avoidance).
    c = lb._mint_short_id_9("seed", used)
    check("collision avoided: second mint of same seed differs", c != a)


# ─────────────────────────────────────────────────────────────────────────
# _remap_tool_call_ids_for_mistral — rewrites every tool_call id to a unique
# 9-char form and keeps assistant<->tool pairing intact, including the nasty
# within-turn-duplicate case.
# ─────────────────────────────────────────────────────────────────────────

def test_remap_mistral_basic_pairing():
    long_id = "toolu_bdrk_01EXAMPLEEXAMPLEEXAMPLE"
    msgs = [
        {"role": "user", "content": "read it"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": long_id, "type": "function",
                         "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": long_id, "content": "file body"},
    ]
    out = lb._remap_tool_call_ids_for_mistral(msgs)
    new_call_id = out[1]["tool_calls"][0]["id"]
    check("remapped assistant tool_call id is 9 chars",
          len(new_call_id) == 9 and lb._MISTRAL_TOOL_CALL_ID_RE.match(new_call_id))
    check("tool result re-paired to the new assistant id",
          out[2]["tool_call_id"] == new_call_id)


def test_remap_mistral_within_turn_duplicates():
    # Two tool_calls in one assistant turn sharing the SAME original id —
    # the exact case an id-only mapping collapses. Position pairing must
    # keep them distinct and correctly paired.
    msgs = [
        {"role": "assistant", "content": "",
         "tool_calls": [
             {"id": "dup", "type": "function",
              "function": {"name": "a", "arguments": "{}"}},
             {"id": "dup", "type": "function",
              "function": {"name": "b", "arguments": "{}"}},
         ]},
        {"role": "tool", "tool_call_id": "dup", "content": "res-a"},
        {"role": "tool", "tool_call_id": "dup", "content": "res-b"},
    ]
    out = lb._remap_tool_call_ids_for_mistral(msgs)
    id0 = out[0]["tool_calls"][0]["id"]
    id1 = out[0]["tool_calls"][1]["id"]
    check("within-turn duplicate ids become distinct", id0 != id1)
    check("first tool result pairs to first call by position",
          out[1]["tool_call_id"] == id0)
    check("second tool result pairs to second call by position",
          out[2]["tool_call_id"] == id1)


def test_remap_mistral_determinism_and_passthrough():
    long_id = "toolu_bdrk_01EXAMPLEEXAMPLEEXAMPLE"
    base = [
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": long_id, "type": "function",
                         "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": long_id, "content": "x"},
    ]
    # Deep-ish copies so the two runs share no mutable state.
    run1 = lb._remap_tool_call_ids_for_mistral(json.loads(json.dumps(base)))
    run2 = lb._remap_tool_call_ids_for_mistral(json.loads(json.dumps(base)))
    check("remap is deterministic across runs",
          run1[0]["tool_calls"][0]["id"] == run2[0]["tool_calls"][0]["id"])

    plain = [{"role": "user", "content": "hi"},
             {"role": "assistant", "content": "hello"}]
    check("no tool traffic -> input returned unchanged (by reference)",
          lb._remap_tool_call_ids_for_mistral(plain) is plain)


# ─────────────────────────────────────────────────────────────────────────
# _fill_blank_tool_call_ids — Ollama stores tool calls with an empty id;
# OpenAI via OpenRouter 400s on an empty call_id. Fill the blanks, keep
# real ids untouched, keep pairing intact.
# ─────────────────────────────────────────────────────────────────────────

def _ollama_shaped_round_trip(name="read_file", args="{}", result="body"):
    """What an Ollama-run tool round-trip actually looks like on disk:
    no id anywhere."""
    return [
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "", "type": "function",
                         "function": {"name": name, "arguments": args}}]},
        {"role": "tool", "tool_call_id": "", "content": result},
    ]


def test_fill_blank_tool_call_ids():
    msgs = [{"role": "user", "content": "read it"}] + _ollama_shaped_round_trip()
    out = lb._fill_blank_tool_call_ids(msgs)
    new_id = out[1]["tool_calls"][0]["id"]
    check("blank assistant tool_call id is filled", bool(new_id))
    check("filled tool result pairs to the call",
          out[2]["tool_call_id"] == new_id)
    check("original message list not mutated",
          msgs[1]["tool_calls"][0]["id"] == "" and msgs[2]["tool_call_id"] == "")

    # Positive control: the shape that caused the live 400 must be gone.
    def _any_blank(ms):
        for m in ms:
            for tc in (m.get("tool_calls") or []):
                if not tc.get("id"):
                    return True
            if m.get("role") == "tool" and not m.get("tool_call_id"):
                return True
        return False
    check("control: the unfixed history really does carry a blank id",
          _any_blank(msgs))
    check("no blank id survives the fill", not _any_blank(out))


def test_fill_blank_ids_leaves_real_ids_alone():
    real = "toolu_bdrk_01EXAMPLEEXAMPLEEXAMPLE"
    msgs = [
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": real, "type": "function",
                         "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": real, "content": "x"},
    ]
    out = lb._fill_blank_tool_call_ids(msgs)
    check("a history with real ids is returned unchanged (by reference)",
          out is msgs)

    plain = [{"role": "user", "content": "hi"},
             {"role": "assistant", "content": "hello"}]
    check("no tool traffic -> input returned unchanged (by reference)",
          lb._fill_blank_tool_call_ids(plain) is plain)


def test_fill_blank_ids_multi_call_turn_pairs_by_position():
    msgs = [
        {"role": "assistant", "content": "",
         "tool_calls": [
             {"id": "", "type": "function",
              "function": {"name": "a", "arguments": "{}"}},
             {"id": "", "type": "function",
              "function": {"name": "b", "arguments": "{}"}},
         ]},
        {"role": "tool", "tool_call_id": "", "content": "res-a"},
        {"role": "tool", "tool_call_id": "", "content": "res-b"},
    ]
    out = lb._fill_blank_tool_call_ids(msgs)
    id0 = out[0]["tool_calls"][0]["id"]
    id1 = out[0]["tool_calls"][1]["id"]
    check("two blank calls in one turn get distinct ids", id0 != id1)
    check("first result pairs to first call", out[1]["tool_call_id"] == id0)
    check("second result pairs to second call", out[2]["tool_call_id"] == id1)


def test_fill_blank_ids_is_stable_across_turns():
    # The ids land in the prompt, so they must not move between turns —
    # a moving id is a cold prefill. Growing the conversation (and
    # trimming its front) must leave earlier ids byte-identical.
    turn1 = ([{"role": "user", "content": "q1"}]
             + _ollama_shaped_round_trip("read_file", '{"path": "a"}', "A"))
    turn2 = (json.loads(json.dumps(turn1))
             + [{"role": "user", "content": "q2"}]
             + _ollama_shaped_round_trip("read_file", '{"path": "b"}', "B"))
    out1 = lb._fill_blank_tool_call_ids(turn1)
    out2 = lb._fill_blank_tool_call_ids(turn2)
    check("id from the earlier turn is unchanged when the history grows",
          out1[1]["tool_calls"][0]["id"] == out2[1]["tool_calls"][0]["id"])

    trimmed = lb._fill_blank_tool_call_ids(json.loads(json.dumps(turn2))[3:])
    check("id is unchanged when the front of the history is trimmed away",
          trimmed[1]["tool_calls"][0]["id"] == out2[4]["tool_calls"][0]["id"])


def test_fill_blank_ids_orphan_tool_turn():
    msgs = [{"role": "system", "content": "s"},
            {"role": "tool", "tool_call_id": "", "content": "leftover"}]
    out = lb._fill_blank_tool_call_ids(msgs)
    check("orphan tool turn still gets a non-empty id",
          bool(out[1]["tool_call_id"]))


# ─────────────────────────────────────────────────────────────────────────
# _repair_tool_pairing — OpenAI requires an exact one-to-one pairing of
# tool calls and tool results. Either half can go missing when a window
# cuts through a round-trip, and filling in ids does NOT fix that: an
# unpaired result just trades "empty string" for "No tool call found".
# ─────────────────────────────────────────────────────────────────────────

def _pairing_faults(msgs):
    """(unpaired results, calls with no result) after ids are filled in —
    i.e. exactly the two shapes OpenAI rejects."""
    out = lb._fill_blank_tool_call_ids(msgs)
    call_ids = {tc.get("id") for m in out for tc in (m.get("tool_calls") or [])}
    result_ids = {m.get("tool_call_id") for m in out if m.get("role") == "tool"}
    return (sorted(result_ids - call_ids), sorted(call_ids - result_ids))


def test_repair_orphan_result_is_kept_not_dropped():
    # The live failure: a window that begins on a tool result whose call
    # was cut away. Reported 2026-08-06 as
    #   "No tool call found for function call output with call_id call_..."
    msgs = [
        {"role": "tool", "tool_call_id": "", "content": "SEARCH RESULT BODY"},
        {"role": "assistant", "content": "I found it!"},
    ]
    check("control: the unrepaired window really is unpairable",
          _pairing_faults(msgs)[0] != [])

    out = lb._repair_tool_pairing(msgs)
    check("after repair nothing is unpaired", _pairing_faults(out) == ([], []))
    check("the orphan result is no longer a tool turn",
          out[0]["role"] != "tool")
    check("...and its content is carried through, not dropped",
          "SEARCH RESULT BODY" in out[0]["content"])
    check("...labelled so the kin doesn't read it as someone speaking",
          out[0]["content"].lstrip().startswith("[hearthkin:"))
    check("...as a user turn, never a second assistant turn in a row",
          out[0]["role"] == "user")


def test_repair_call_with_no_result():
    # The mirror image: the call survived the cut, its result didn't.
    msgs = [
        {"role": "user", "content": "look it up"},
        {"role": "assistant", "content": "on it",
         "tool_calls": [{"id": "", "type": "function",
                         "function": {"name": "web_search", "arguments": "{}"}}]},
    ]
    check("control: an unanswered call is a fault too",
          _pairing_faults(msgs)[1] != [])
    out = lb._repair_tool_pairing(msgs)
    check("unanswered call is removed", _pairing_faults(out) == ([], []))
    check("...but the kin's own words survive it",
          any(m.get("content") == "on it" for m in out))


def test_repair_partial_answer_keeps_the_answered_call():
    # Two calls, one result. The answered call must survive intact —
    # dropping the whole turn would orphan the result that DID arrive.
    msgs = [
        {"role": "assistant", "content": "",
         "tool_calls": [
             {"id": "", "type": "function",
              "function": {"name": "a", "arguments": "{}"}},
             {"id": "", "type": "function",
              "function": {"name": "b", "arguments": "{}"}},
         ]},
        {"role": "tool", "tool_call_id": "", "content": "res-a"},
    ]
    out = lb._repair_tool_pairing(msgs)
    check("partial answer leaves exactly one call", len(out[0]["tool_calls"]) == 1)
    check("...the one that was actually answered",
          out[0]["tool_calls"][0]["function"]["name"] == "a")
    check("...and the pairing is clean", _pairing_faults(out) == ([], []))


def test_repair_leaves_a_system_note_between_call_and_result_alone():
    # `_inline_mid_conversation_system_notes` deliberately leaves a note
    # in this position as role=system so the pairing holds. Treating it
    # as a break would manufacture the very orphan this repairs.
    msgs = [
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "", "type": "function",
                         "function": {"name": "a", "arguments": "{}"}}]},
        {"role": "system", "content": "[hearthkin: note]"},
        {"role": "tool", "tool_call_id": "", "content": "res"},
    ]
    out = lb._repair_tool_pairing(msgs)
    check("a note between call and result is not a break", out is msgs)
    check("...and the result still pairs to its call",
          _pairing_faults(out) == ([], []))


def test_repair_is_a_no_op_on_healthy_history():
    msgs = ([{"role": "user", "content": "read it"}]
            + _ollama_shaped_round_trip())
    check("an intact round-trip is returned unchanged (by reference)",
          lb._repair_tool_pairing(msgs) is msgs)
    plain = [{"role": "user", "content": "hi"},
             {"role": "assistant", "content": "hello"}]
    check("no tool traffic -> input returned unchanged (by reference)",
          lb._repair_tool_pairing(plain) is plain)


def test_repair_survives_every_cut_point():
    # A window can be cut anywhere, by any of several layers. Rather than
    # trust one of them, assert the repair holds at EVERY cut — with the
    # unrepaired count carried alongside as a positive control, so a
    # repair that quietly stopped working can't read as a clean sweep.
    convo = [{"role": "system", "content": "soul"}]
    for i in range(8):
        convo.append({"role": "user", "content": f"q{i}"})
        convo.append({"role": "assistant", "content": "",
                      "tool_calls": [{"id": "", "type": "function",
                                      "function": {"name": "web_search",
                                                   "arguments": f'{{"q":"{i}"}}'}}]})
        convo.append({"role": "tool", "tool_call_id": "", "content": f"result {i}"})
        convo.append({"role": "assistant", "content": f"a{i}"})
    broken_before = sum(1 for i in range(len(convo))
                        if any(_pairing_faults(convo[i:])))
    broken_after = sum(1 for i in range(len(convo))
                       if any(_pairing_faults(lb._repair_tool_pairing(convo[i:]))))
    check("control: cutting a conversation anywhere really does break pairs",
          broken_before > 0)
    check("no cut point leaves a broken pair after repair", broken_after == 0)


# ─────────────────────────────────────────────────────────────────────────
# _extract_content_tool_calls — content-channel fallback + the hallucinated-
# name gate that stops garbage names reaching the executor.
# ─────────────────────────────────────────────────────────────────────────

def test_extract_content_tool_calls():
    good = '<tool_call>{"name": "read_file", "arguments": {"path": "x"}}</tool_call>'
    calls = lb._extract_content_tool_calls(good)
    check("valid content tool-call extracted",
          len(calls) == 1 and calls[0]["function"]["name"] == "read_file")

    # Hallucinated name with a space must be rejected (not passed downstream).
    bad = '<tool_call>{"name": "read file please", "arguments": {}}</tool_call>'
    check("tool name with spaces is rejected",
          lb._extract_content_tool_calls(bad) == [])

    # A marker QUOTED inside a fenced code block must not be executed.
    fenced = "Here is the format:\n```\n<tool_call>{\"name\": \"read_file\", \"arguments\": {}}</tool_call>\n```"
    check("tool-call markup inside a code fence is NOT extracted",
          lb._extract_content_tool_calls(fenced) == [])

    check("empty content -> no calls", lb._extract_content_tool_calls("") == [])


def main():
    test_provider_detection()
    test_coerce_tool_call_args()
    test_strip_extra_message_fields()
    test_coerce_null_content()
    test_normalize_tool_args()
    test_consolidate_system_messages()
    test_inline_mid_conversation_system_notes()
    test_truncate_hysteresis()
    test_collapse_consecutive_user_turns()
    test_ensure_user_turn_present()
    test_hang_watchdog_guard()
    test_watchdog_timeout_resolution()
    test_mint_short_id_9()
    test_remap_mistral_basic_pairing()
    test_remap_mistral_within_turn_duplicates()
    test_remap_mistral_determinism_and_passthrough()
    test_fill_blank_tool_call_ids()
    test_fill_blank_ids_leaves_real_ids_alone()
    test_fill_blank_ids_multi_call_turn_pairs_by_position()
    test_fill_blank_ids_is_stable_across_turns()
    test_fill_blank_ids_orphan_tool_turn()
    test_repair_orphan_result_is_kept_not_dropped()
    test_repair_call_with_no_result()
    test_repair_partial_answer_keeps_the_answered_call()
    test_repair_leaves_a_system_note_between_call_and_result_alone()
    test_repair_is_a_no_op_on_healthy_history()
    test_repair_survives_every_cut_point()
    test_extract_content_tool_calls()

    print("\n" + ("-" * 50))
    if _failures:
        print(f"{len(_failures)} FAILURE(S): " + ", ".join(_failures))
        sys.exit(1)
    print("ALL NORMALIZATION TESTS PASS")


if __name__ == "__main__":
    main()
