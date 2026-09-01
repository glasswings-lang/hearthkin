# SPDX-License-Identifier: CC0-1.0
"""Standalone tests for _make_executor's argument-name recovery.

Small models name arguments from whatever tool conventions they were trained
on, so a tool whose real parameter is `path` gets called with `file` /
`filename`, `content` arrives as `text`, etc. Without recovery the misnamed key
is dropped and the required parameter blows up with a raw Python TypeError the
model can't act on. The executor re-homes a misnamed value onto a missing
required param (synonym first, then single-unknown→single-missing) and returns
a clean steering string when an argument is genuinely absent. These cases pin
that, and prove normal/extra-junk calls are unaffected.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def fake(path, content="", agent_name=""):
    """Stand-in tool: one required (path), one optional (content), one
    framework-bound (agent_name)."""
    return f"path={path!r}|content={content!r}|agent={agent_name!r}"


def main():
    run = tools._make_executor(fake, {"agent_name": "Kin"})

    # Synonym recovery onto the required param.
    check("file -> path", run({"file": "a.txt"}).startswith("path='a.txt'"))
    check("filename -> path", run({"filename": "a.txt"}).startswith("path='a.txt'"))
    # Synonym recovery onto an optional param (text -> content).
    check("text -> content", "content='hi'" in run({"path": "a.txt", "text": "hi"}))

    # Single-unknown -> single-missing fallback (no synonym needed).
    check("lone unknown fills lone missing required",
          run({"weirdkey": "a.txt"}).startswith("path='a.txt'"))

    # Genuinely missing required -> clean steer, never a TypeError.
    out = run({})
    check("missing required returns clean error", out.startswith("error:")
          and "path" in out and "TypeError" not in out)

    # Framework-bound arg is always injected and wins over the model.
    check("agent_name injected", "agent='Kin'" in run({"path": "a.txt"}))
    check("model cannot override bound agent_name",
          "agent='Kin'" in run({"path": "a.txt", "agent_name": "Spoofed"}))

    # Normal call and extra-junk call are unaffected.
    check("plain valid call works", run({"path": "a.txt"}).startswith("path='a.txt'"))
    check("extra junk key ignored when required already satisfied",
          run({"path": "a.txt", "junk": "x"}).startswith("path='a.txt'")
          and "content=''" in run({"path": "a.txt", "junk": "x"}))

    # Two typo'd args both missing -> we do NOT guess a 2:2 mapping; clean steer.
    run2 = tools._make_executor(
        lambda old_string, new_string, agent_name="": f"{old_string}->{new_string}",
        {"agent_name": "Kin"})
    out2 = run2({"oldd": "a", "neww": "b"})
    check("ambiguous 2:2 misnames are not guessed", out2.startswith("error:"))

    if _fails:
        print(f"\nFAILED {len(_fails)}: " + ", ".join(_fails))
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
