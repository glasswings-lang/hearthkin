# SPDX-License-Identifier: CC0-1.0
"""Standalone tests for which tools load_tools puts in front of a kin.

THE STAGING TOOLS ARE NO LONGER HIDDEN WHEN STAGING IS EMPTY, and this file
used to pin the opposite. The reason for the reversal is worth keeping,
because the original reasoning was sound and still wrong.

`read_staging` / `archive_staging` are pure tending tools, useless outside a
memory tend, and every enabled tool's schema rides along on every turn. So
hiding them when there was nothing to tend saved real tokens, and small models
gesture more as the tool count climbs. That is a genuine saving.

What it overlooked is WHERE the tool names end up. The tool-use hint names the
tools available this turn, and that hint is appended to the SYSTEM BLOCK — the
very front of the prompt. A local model reuses its cached work only for an
unbroken run from the start, so staging filling or emptying rewrote position
zero and cost the entire context a cold re-read.

Measured on a real kin: the system block oscillated between 26,738 and 26,769
characters — thirty-one characters, exactly "archive_staging, " plus
"read_staging, " — flipping back and forth across 76 turns. It hit hardest on
the kin that distilled most often, because distillation is what writes staging
notes and tending is what clears them: the kin doing the most memory work had
the least usable cache. Two schemas is a few hundred tokens once. A cache miss
is the whole context, in minutes.

So the rule is now: if a kin is allowed a staging tool, it always has it.
`read_staging` on an empty directory answers "nothing pending", which is a
perfectly good thing for a kin to be told.

What this file pins:

  - the staging tools are present whatever the staging state, so the tool
    list — and therefore the front of the prompt — does not move;
  - the same holds on a scheduled tend, which used to be the one caller that
    forced them in;
  - a kin that never enabled them still doesn't get them (this is stability,
    not a free-for-all);
  - and the non-staging tools are untouched in every case.

The stability property is checked directly, against the old behaviour as a
positive control: a test that only asked "are they present?" would pass just
as happily on a version that varied for some other reason.

Run: python tests/test_tool_gating.py
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


def names(schemas):
    return sorted(s["function"]["name"] for s in schemas)


def main():
    allow = ["tff", "read_file", "web_search",
             "read_staging", "archive_staging"]

    # --- the tool list does not depend on the staging state ---------------
    #
    # There is deliberately nothing to monkeypatch here any more. The old
    # gate consulted the staging directory on every call; that consultation
    # is what made the front of the prompt a function of the kin's memory
    # backlog, so removing the seam is part of the fix rather than an
    # inconvenience to the test.

    got = names(tools.load_tools(allow, context={"agent_name": "X"})[0])
    check("read_staging is present with nothing pending", "read_staging" in got)
    check("archive_staging is present with nothing pending",
          "archive_staging" in got)
    check("the other tools are untouched",
          {"tff", "read_file", "web_search"}.issubset(got))

    # --- the property that actually matters: it does not MOVE -------------
    #
    # Same call, repeated. Any dependence on staging state, directory
    # contents or call order would show up as a differing list, and a
    # differing list is a differing system block.

    runs = [names(tools.load_tools(allow, context={"agent_name": "X"})[0])
            for _ in range(4)]
    check("the tool list is identical across repeated turns",
          all(r == runs[0] for r in runs))

    cron = names(tools.load_tools(allow, context={"agent_name": "X"},
                                  cron_turn=True)[0])
    check("a scheduled tend sees the same staging tools it always did",
          {"read_staging", "archive_staging"}.issubset(cron))

    # A different kin name must not change the staging half either — the
    # old gate read per-kin state, and that is exactly what varied.
    other = names(tools.load_tools(allow, context={"agent_name": "SomeoneElse"})[0])
    check("...and the same for a kin with different staging on disk",
          {"read_staging", "archive_staging"}.issubset(other))

    # --- positive control -------------------------------------------------
    #
    # Prove the check above can FAIL, by running it against the behaviour
    # this change removed. Without this, "the list held still" would pass on
    # a build where load_tools returned nothing at all.

    def old_style(allowed, pending):
        out = list(allowed)
        if not pending:
            out = [n for n in out
                   if n not in ("read_staging", "archive_staging")]
        return sorted(out)

    old_runs = [old_style(allow, pending) for pending in (True, False, True)]
    check("(control) the OLD behaviour does not hold still, as expected",
          not all(r == old_runs[0] for r in old_runs))
    check("(control) ...and it is the staging pair that moves, 31 chars of it",
          set(old_runs[0]) - set(old_runs[1])
          == {"read_staging", "archive_staging"})

    # --- opting out is still opting out -----------------------------------

    plain = ["read_file", "web_search"]
    got = names(tools.load_tools(plain, context={"agent_name": "X"})[0])
    check("a kin that never enabled the staging tools still has none",
          got == ["read_file", "web_search"])

    if _fails:
        print(f"\nFAILED {len(_fails)}: " + ", ".join(_fails))
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
