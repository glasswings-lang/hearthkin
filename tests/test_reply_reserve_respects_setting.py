# SPDX-License-Identifier: CC0-1.0
"""A kin's configured reply cap survives a turn that merely HAS tools.

The tool loop reserves room for a long reply so a `write_file`'s content
argument can't be cut off mid-JSON -- a truncated tool call is a broken tool
call, and that is worth paying for. It used to be paid on every turn where any
tool was available at all.

That reserve comes out of the room left for the conversation. So a kin's
history shrank whenever it used a tool and grew back whenever it didn't, and a
history whose far end keeps moving is re-read from cold every time it moves --
minutes of silence before a reply starts, with nothing on screen saying why.

Measured on a real kin at a 32768 window: a configured reply cap of 1024 was
raised to 8000 on any tool-capable turn, taking 5,434 tokens -- a fifth of the
window -- away from history and handing them back on the following turn. Two
kin churned like that all day. A third, whose configured cap happened to equal
the floor, never did: same code, no mismatch, no cost. That third kin is why
this is a mismatch bug and not a tuning question.

So the reserve is now taken only when the turn's tools include one that can
emit a long argument, and it FAILS SAFE: a tool list this code cannot read
keeps the floor, because a gamble on someone's file write is not worth a
faster turn.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm_backend as L  # noqa: E402


FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s %s" % (label, detail))
        FAILURES.append(label)


def schema(name):
    """The shape tools.load_tools actually returns."""
    return {"type": "function", "function": {"name": name, "description": "x"}}


def main():
    print("a configured reply cap survives a tool-capable turn")
    need = L._needs_large_output_reserve

    # --- positive control ---------------------------------------------
    # Prove this test can see the reserve being demanded at all, before any
    # "it isn't demanded" result below is believed.
    check("positive control: a file write DOES ask for the reserve",
          need([schema("write_file")]) is True)

    # --- the reported case --------------------------------------------
    # A kin tending a park: it looks, it acts, it reads. Nothing it can do
    # produces a long argument, so nothing should be reserved against one.
    park_ish = [schema(n) for n in
                ("read_file", "memory_search", "context_status", "web_search")]
    check("read-only tools do not take room from the conversation",
          need(park_ish) is False)

    # --- every tool that can emit a long argument ----------------------
    for name in ("write_file", "edit_file", "note"):
        check("%s keeps the reserve" % name, need([schema(name)]) is True)

    # One large-argument tool anywhere in the set is enough.
    check("a mixed set still reserves, on the strength of one writer",
          need(park_ish + [schema("edit_file")]) is True)

    # --- no tools at all ----------------------------------------------
    check("a plain turn reserves nothing", need([]) is False)
    check("...and None is a plain turn too", need(None) is False)

    # --- fails safe ----------------------------------------------------
    # Uncertainty must cost a slow turn, never someone's truncated file.
    check("an entry with no name keeps the reserve",
          need([{"type": "function", "function": {}}]) is True)
    check("a garbled list keeps the reserve",
          need([None]) is True)
    check("a non-list keeps the reserve", need(object()) is True)

    # --- the alternate shape -------------------------------------------
    # Some callers pass a flat {"name": ...} rather than the nested form.
    check("a flat schema shape is read, not treated as unknown",
          need([{"name": "read_file"}]) is False)

    # --- the reserve follows the KIN, not the call --------------------
    # This is the half that actually stops the churn. Asking the call gives
    # two different answers for one conversation depending on whether that
    # turn happened to carry tools; asking the kin gives one.
    import kin_persistence as kp

    home = os.environ.get("HEARTHKIN_HOME")
    check("running against a sandbox HEARTHKIN_HOME, not a real kin folder",
          bool(home), "(refusing to write into someone's real kin)")
    if home:
        import json
        for kin, enabled in (("Writer", ["read_file", "write_file"]),
                             ("Reader", ["read_file", "memory_search"])):
            d = kp.agent_dir(kin)
            d.mkdir(parents=True, exist_ok=True)
            (d / "tools.json").write_text(json.dumps({"enabled": enabled}),
                                          encoding="utf-8")
        L._kin_large_arg_cache.clear()

        check("a kin that can write reserves on EVERY turn",
              L._kin_may_emit_large_argument("Writer") is True)
        check("a read-only kin reserves on none of them",
              L._kin_may_emit_large_argument("Reader") is False)

        # The point of the whole change: one answer, not two. A plain turn
        # and a tool turn for the same kin must agree, or the window changes
        # size between them and the prompt is re-read from cold.
        plain = L._kin_may_emit_large_argument("Writer")
        tooled = L._kin_may_emit_large_argument("Writer")
        check("plain turn and tool turn agree for the same kin",
              plain == tooled is True)

        # Fails safe: an unreadable kin keeps the tighter old behaviour
        # rather than quietly spending history on a tool it may not have.
        check("an unknown kin does not spend history on a guess",
              L._kin_may_emit_large_argument("NoSuchKinAnywhere") is False)

    # The reserve must be computed from the kin-level number, not from what
    # this turn may generate -- those were the same variable, and that was
    # the bug. Source-level because the arithmetic sits inside chat().
    import inspect
    src = inspect.getsource(L.chat)
    check("the budget reserve is taken from the kin-level number",
          "extra_reserve_real = max(0, reserve_tokens" in src)
    check("...and a plain reply is still as short as the person set it",
          "reserve_tokens = effective_num_predict" in src)

    print("")
    if FAILURES:
        print("FAILED: %d" % len(FAILURES))
        return 1
    print("PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
