# SPDX-License-Identifier: CC0-1.0
"""Standalone tests for the "one more try if no tool runs" job setting
(no network, no window).

A scheduled journal job replied "Let me check the journal and write
February 26" and called no tool. Nothing was written and nobody was awake.
The tending retry only covered tending. This setting gives ANY job one more
turn when it ended with no tool having run -- judged by outcome, because that
exact sentence slips past every phrase detector. Each "retries" case has a
"doesn't retry" control beside it. All content is invented.
"""

import os
import sys
import tempfile

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="hk-gretry-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hearthkin_cron  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


class R:
    """Stand-in for a tool-loop result."""
    def __init__(self, content, added=None):
        self.content = content
        self.messages_added = added or []


TOOLS = ["read_file", "write_file", "edit_file", "note"]
MSGS = [{"role": "system", "content": "base"},
        {"role": "user", "content": "[hearthkin: scheduled wake-up] write today's entry"}]
TOOL_TURN = [{"role": "assistant", "content": "",
              "tool_calls": [{"function": {"name": "note", "arguments": "{}"}}]},
             {"role": "tool", "content": "note: added"}]


def run(result, tools=TOOLS):
    calls = []

    def rerun(msgs):
        calls.append(msgs)
        return R("Done -- wrote the entry.", TOOL_TURN)
    out = hearthkin_cron._maybe_gesture_retry(
        "Wren", result, MSGS, tools, "test-model", rerun)
    return out, calls


def main():
    # The setting itself.
    check("off when absent", not hearthkin_cron.gesture_retry_enabled({}))
    check("off for a non-dict", not hearthkin_cron.gesture_retry_enabled(None))
    check("on when ticked", hearthkin_cron.gesture_retry_enabled({"gesture_retry": True}))

    # The real 11 Nov sentence: no tool named, nothing acted out.
    out, calls = run(R("Let me check the journal and write February 26."))
    check("the exact narrated sentence gets one more try", len(calls) == 1)
    check("...and the retry's result is kept", out.content.startswith("Done"))
    sent = calls[0] if calls else []
    check("...with the general reminder as the last message",
          bool(sent) and sent[-1]["role"] == "system"
          and "without any tool actually running" in sent[-1]["content"])
    check("...which says 'nothing to do' is an allowed answer",
          bool(sent) and "nothing to do" in sent[-1]["content"])
    check("...and keeps the original conversation in front of it",
          bool(sent) and sent[:len(MSGS)] == MSGS)

    # An acted-out call: the detector's specific note is used instead.
    out, calls = run(R("*writes the entry to memory/journal.md*"))
    check("an acted-out write gets one more try", len(calls) == 1)
    check("...with the detector's own note, not the general one",
          bool(calls) and "without any tool actually running" not in calls[0][-1]["content"])

    # An empty reply is the plainest miss of all.
    out, calls = run(R(""))
    check("an empty reply gets one more try", len(calls) == 1)

    # Controls: no retry.
    out, calls = run(R("Wrote the entry.", TOOL_TURN))
    check("no retry when a tool really ran", calls == [] and out.content == "Wrote the entry.")
    out, calls = run(R("Let me write it.", None), tools=[])
    check("no retry for a job with no tools", calls == [])
    out, calls = run(R("```memory/journal.md\n## Day 3\n\nFog.\n```"))
    check("no retry when the reply saved a file as a fenced block", calls == [])

    # Only once: the helper never calls rerun twice, even if the retry misses too.
    n = []

    def miss_again(msgs):
        n.append(1)
        return R("Let me write it now.")
    out = hearthkin_cron._maybe_gesture_retry(
        "Wren", R("Let me write it."), MSGS, TOOLS, "m", miss_again)
    check("only one retry, even if the retry misses too", len(n) == 1)

    # A failing rerun never breaks the job; the original result is kept.
    def boom(msgs):
        raise RuntimeError("provider down")
    orig = R("Let me write it.")
    out = hearthkin_cron._maybe_gesture_retry("Wren", orig, MSGS, TOOLS, "m", boom)
    check("a failed retry keeps the original result", out is orig)

    # End to end: a whole scheduled run, with the model stubbed out.
    import json
    import llm_backend
    from hearthkin_paths import kin_dir
    from kin_persistence import load_agent_config
    k = kin_dir("Wren")
    k.mkdir(parents=True, exist_ok=True)
    (k / "config.json").write_text(json.dumps({"model": "stub-model"}), encoding="utf-8")
    (k / "tools.json").write_text(json.dumps({"enabled": ["note", "read_file"]}),
                                  encoding="utf-8")
    (k / "soul.md").write_text("A small kin who keeps a garden journal.", encoding="utf-8")
    surfaces = []

    def fake_loop(model, messages, **kw):
        surfaces.append(kw.get("surface"))
        if len(surfaces) == 1:
            return R("Let me check the journal and write February 26.")
        return R("Wrote it.", TOOL_TURN)
    real_loop = llm_backend.run_tool_loop
    llm_backend.run_tool_loop = fake_loop
    try:
        for flag in (True, False):
            surfaces.clear()
            hearthkin_cron._run_isolated_inner(
                "Wren", load_agent_config("Wren"), "08:00",
                "Write today's journal entry.", gesture_retry=flag)
            if flag:
                check("a whole run with the box ticked gets its second turn",
                      surfaces == ["cron-subprocess-tools", "cron-gesture-retry"])
            else:
                check("...and with it unticked, it doesn't",
                      surfaces == ["cron-subprocess-tools"])
    finally:
        llm_backend.run_tool_loop = real_loop

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
