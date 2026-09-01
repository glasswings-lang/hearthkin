"""A kin must never be sent its soul with no conversation in it.

Found live on 2026-08-06, on a brand-new kin made to try a model out.
num_ctx was 8192 and tools were switched on. The tool loop reserves 8,000
output tokens so a write_file's content argument can't be cut off
mid-JSON — sound on a big window, and on this one it took the whole
thing. The message budget collapsed to its floor, the floor was smaller
than that kin's own system prompt, and the trim dropped every single
turn of conversation.

What went out was the system prompt and nothing else. The kin still
answered — warmly, in voice, at length — with no idea that anyone was
talking to it. It re-introduced itself, ignored a file it had just read,
and addressed its own name. From a chat window that reads as the model
being vacant, and it does not clear on its own: every following turn had
the same window and the same result.

The app's own usage log had the proof the whole time: 2,852 prompt
tokens sent, of which 2,849 were the system block. Nothing surfaced it.

Three defences, and this file carries a positive control for each,
because all three are the kind that fail silently:

  1. the reply reserve can no longer be bigger than the window it sits in
  2. if the trim takes everything anyway, the newest question goes back
  3. it is written to an always-on log when it happens

Plus the pre-flight in compat.py, so a small window is caught in
Settings rather than in a conversation.

    python tests/test_context_starvation.py
"""

import os
import sys
import json
import pathlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm_backend as lb  # noqa: E402
import compat  # noqa: E402

_failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        _failures.append(name)


# The shape that failed: a system prompt bigger than the collapsed
# budget, plus a real conversation behind it.
SYSTEM_PROMPT = "S" * 11000          # ~2,750 estimated tokens
# write_file specifically, not just "a tool". The starvation guarded here is
# caused BY the large-output reserve, and that reserve is now taken only for
# tools that can emit a long argument -- a read-only turn keeps the person's
# own reply cap and never reaches the 8,000 floor that starves a small window.
# So the hazard is unchanged and still reachable; naming the tool that causes
# it is what keeps this test pointed at it. Swapping this back to read_file
# makes every check below pass for the wrong reason: nothing to clamp.
TOOL_SCHEMAS = [{"type": "function",
                 "function": {"name": "write_file", "description": "write a file",
                              "parameters": {"type": "object", "properties": {}}}}]


def _conversation(n=14):
    """The shape it happened in: an ordinary conversation ending in a
    tool round-trip. The trip matters — the trim drops a user turn with
    its assistant reply, and then sweeps the whole run of tool results
    that followed, which is how one pass can overshoot the "keep two"
    guard all the way to nothing."""
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for i in range(n):
        msgs.append({"role": "user", "content": f"question number {i} " + "x" * 900})
        msgs.append({"role": "assistant", "content": f"answer number {i} " + "y" * 900})
    msgs.append({"role": "user", "content": "THE QUESTION I JUST ASKED " + "z" * 200})
    msgs.append({"role": "assistant", "content": "",
                 "tool_calls": [{"id": "call_x", "type": "function",
                                 "function": {"name": "read_file",
                                              "arguments": '{"path": "a.txt"}'}}]})
    msgs.append({"role": "tool", "tool_call_id": "call_x",
                 "content": "FILE BODY " + "w" * 5000})
    return msgs


def _sent(num_ctx, *, tools=TOOL_SCHEMAS, kin="StarveTest", num_predict=900):
    """Drive the REAL chat() and capture the final message list, stopping
    at the network. Returns (messages, options_sent)."""
    captured = {}

    def spy(model, messages, options, *a, **kw):
        captured["messages"] = messages
        captured["options"] = options
        raise RuntimeError("stop-before-network")

    real = lb._chat_openrouter_blocking
    lb._chat_openrouter_blocking = spy
    try:
        lb.chat("openrouter/openai/gpt-5.6-luna", _conversation(), stream=False,
                options={"num_predict": num_predict}, tools=tools,
                kin_name=kin, surface=f"starve-{num_ctx}",
                max_context_tokens=num_ctx - 2000)
    except Exception:
        pass
    finally:
        lb._chat_openrouter_blocking = real
    return captured.get("messages", []), captured.get("options") or {}


def _conversation_turns(messages):
    return [m for m in messages if isinstance(m, dict) and m.get("role") != "system"]


# ── 1. The positive control ────────────────────────────────────────────
# Before believing any of the passes below, prove the underlying trim
# really does strip everything at this budget. If it stops doing that —
# because a floor moved, say — these tests would all pass while
# guarding nothing.

def test_control_the_trim_really_does_take_everything():
    convo = _conversation()
    out, _ = lb._truncate_messages(convo, 2048)
    check("control: at the collapsed budget the trim leaves NO conversation",
          not _conversation_turns(out))
    check("control: ...and the system prompt alone already exceeds that budget",
          lb._est_tokens([{"role": "system", "content": SYSTEM_PROMPT}]) > 2048)


# ── 2. The reserve can't eat the window ────────────────────────────────

def test_reply_reserve_is_capped_to_the_window():
    _, options = _sent(8192)
    np = int(options.get("num_predict") or 0)
    check("the 8,000-token tool reserve is cut down on a small window",
          0 < np < lb.TOOL_LOOP_MIN_OUTPUT_TOKENS)
    check("...to no more than half the window",
          np <= int((8192 - 2000) * 0.5))
    # The clamp must also be applied to what the model is ALLOWED to
    # generate, not only to the reservation. Reserving less than we then
    # let it generate would push prompt+reply past num_ctx, and an
    # overrun on local Ollama returns nothing at all.
    check("...and num_predict itself is clamped to match the reservation",
          np == int((8192 - 2000) * 0.5))


def test_a_big_window_is_left_alone():
    # On a roomy window chat() must not touch the caller's options at
    # all — the tool loop's own floor still applies downstream, and a
    # clamp that fired here would silently shorten every reply.
    _, options = _sent(65536, num_predict=900)
    check("a window with room leaves the caller's num_predict alone",
          int(options.get("num_predict") or 0) == 900)


# ── 3. Never a persona with no conversation ────────────────────────────

def test_the_newest_question_always_survives():
    for num_ctx in (8192, 12288, 16384, 32768, 65536):
        msgs, _ = _sent(num_ctx)
        turns = _conversation_turns(msgs)
        check(f"num_ctx {num_ctx}: something of the conversation is sent",
              len(turns) >= 1)
        check(f"num_ctx {num_ctx}: the model is given a question to answer",
              any(m.get("role") == "user" for m in turns))
        check(f"num_ctx {num_ctx}: and it is the MOST RECENT one",
              any("THE QUESTION I JUST ASKED" in (m.get("content") or "")
                  for m in turns))


def test_a_roomy_window_still_sends_history():
    msgs, _ = _sent(65536)
    check("a window with room still sends the whole conversation",
          len(_conversation_turns(msgs)) > 20)


# ── 4. It says so ──────────────────────────────────────────────────────

def test_starvation_is_written_to_an_always_on_log():
    written = []
    real = lb._append_context_overflow_line
    lb._append_context_overflow_line = lambda text: written.append(text)
    try:
        _sent(8192, kin="LogTest")
    finally:
        lb._append_context_overflow_line = real
    check("control: the spy captured something at all", bool(written))
    check("the clamped reserve is logged",
          any("reply_reserve" in w for w in written))
    check("the starved window is logged",
          any("no room for ANY of the conversation" in w for w in written))
    check("the log names the kin, so it can be traced back",
          any("LogTest" in w for w in written))
    check("...and says what to do about it",
          any("num_ctx" in w for w in written))

    # Negative control: a healthy window must say NOTHING. A log that
    # fires on every send is a log nobody reads.
    written.clear()
    lb._append_context_overflow_line = lambda text: written.append(text)
    try:
        _sent(65536, kin="LogTest")
    finally:
        lb._append_context_overflow_line = real
    check("a healthy window logs nothing at all", not written)


# ── 5. Caught in Settings, not in a conversation ───────────────────────

def test_preflight_warns_before_it_bites():
    # The check reads the kin's real files through kin_persistence, and
    # imports those names inside the function — so standing in for them
    # here needs no fake kin folder and no HEARTHKIN_HOME games.
    import kin_persistence as kp
    saved = {n: getattr(kp, n) for n in
             ("load_soul", "load_memory", "load_kin_tools",
              "build_system_prompt", "load_app_prompt")}
    state = {"tools": ["read_file", "note"]}
    kp.load_soul = lambda *a, **k: "You are a test kin.\n" * 20
    kp.load_memory = lambda *a, **k: ""
    kp.load_kin_tools = lambda *a, **k: list(state["tools"])
    # Stand in for the real system prompt at the size it actually was on
    # the kin this was found on — the built block plus the tool-use hint
    # came to about 11,000 characters.
    kp.build_system_prompt = lambda *a, **k: "S" * 8800
    kp.load_app_prompt = lambda slug, *a, **k: "H" * 1100

    def notes_for(num_ctx, tools=True):
        state["tools"] = ["read_file", "note"] if tools else []
        notes = []
        compat._check_window_fits_a_conversation(
            {"num_ctx": num_ctx}, None, notes, kin_name="StarvePreflight")
        return notes

    small = notes_for(8192)
    check("a small window with tools is flagged in Settings", len(small) == 1)
    if small:
        hint = small[0].action_hint or ""
        check("...with an actual number to type", "num_ctx to" in hint)
        # The advice has to be big enough to FIX it — a warning that
        # sends you to a setting which still fails is worse than none.
        suggested = int(hint.split("num_ctx to ")[1].split(" ")[0].replace(",", ""))
        check("...and that number clears the warning it just gave",
              not notes_for(suggested))
    check("a roomy window is not flagged", not notes_for(65536))
    check("a kin with no tools is not flagged (the reserve is what bites)",
          not notes_for(8192, tools=False))
    for name, fn in saved.items():
        setattr(kp, name, fn)


def main():
    test_control_the_trim_really_does_take_everything()
    test_reply_reserve_is_capped_to_the_window()
    test_a_big_window_is_left_alone()
    test_the_newest_question_always_survives()
    test_a_roomy_window_still_sends_history()
    test_starvation_is_written_to_an_always_on_log()
    test_preflight_warns_before_it_bites()

    print()
    if _failures:
        print(f"{len(_failures)} FAILURE(S): " + ", ".join(_failures))
        return 1
    print("test_context_starvation: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
