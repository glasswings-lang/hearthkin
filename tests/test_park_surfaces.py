"""Every surface a kin replies on must route its `> ` line. Plain Python.

A kin plays the park by putting `> command` on a line of its reply. Whether
that line RUNS depended, until 2026-08-12, on which surface it was said on:

    desktop        routes it   (chat_stream_mixin -> _maybe_route_park_command)
    telegram DM    routes it   (_handle_normal_message -> _route_park_command)
    cron keeper    routes it   (hearthkin_cron -> park_keeper.route_reply)
    telegram GROUP did not
    discord        does not

Reported from a live group: `> make room roost`, then `> make a new room
called "roost"`, then `> make roost`. All three lines were correct -- verified
against the game afterwards, all three start the room walkthrough. All three
met silence.

Nothing was filtered and nothing was refused. The router was never called, so
no log anywhere recorded that a move had even been asked for -- not the park's
own play log, not park_unreachable.log. From inside the chat that is
indistinguishable from the game being broken, which is how it was read. The
game was running the whole time, on the same machine, serving that same
account without complaint.

The shape of the bug is why this test is structural rather than behavioural. A
missing call cannot fail a test of what happens when it is called. The only
thing that catches it is asking, of every surface: does this one route at all?

KNOWN_HOLES is a ratchet. A surface in it is a hole we know about and have
decided not to close yet; entries come out as they are wired up and must never
go in to make a run pass.
"""

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_failures = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _failures.append(label)


# (file, function that finishes a kin's reply, what routes the park line there)
SURFACES = [
    ("telegram_bot.py", "_handle_normal_message", "_route_park_command"),
    ("telegram_bot.py", "_handle_group_message", "_route_park_command"),
    ("frame/chat_stream_mixin.py", None, "_maybe_route_park_command"),
    ("hearthkin_cron.py", None, "route_reply"),
    ("discord_bot.py", "_generate", "_route_park_command"),
]

# Surfaces that send a kin's words somewhere and do NOT route its `> ` line.
# Lower this list; never add to it.
KNOWN_HOLES = []


def _source(rel):
    path = os.path.join(ROOT, rel)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _function_body(src, name):
    """The source of one function/method, or None."""
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(src, node) or ""
    return None


def main():
    print("\n-- every reply surface runs a kin's '> ' line --")
    for rel, func, router in SURFACES:
        src = _source(rel)
        if src is None:
            check(False, f"{rel}: file is missing")
            continue
        if func is None:
            check(router + "(" in src, f"{rel} routes a park line ({router})")
            continue
        body = _function_body(src, func)
        if body is None:
            check(False, f"{rel}: {func} not found -- surface renamed?")
            continue
        check(router + "(" in body,
              f"{rel}: {func} routes a kin's '> ' line ({router})")

    print("\n-- the detector can actually see a missing call --")
    # A structural check that cannot fail is the whole reason this bug lived:
    # prove it spots a surface with no router before believing the passes.
    fake = "class X:\n    def _handle_group_message(self):\n        return 1\n"
    check(_function_body(fake, "_handle_group_message") is not None
          and "_route_park_command(" not in _function_body(
              fake, "_handle_group_message"),
          "positive control: a surface that routes nothing is spotted")

    print("\n-- holes we know about --")
    for rel in KNOWN_HOLES:
        src = _source(rel)
        if src is None:
            continue
        routes = "route_park" in src or "route_reply(" in src
        check(not routes,
              f"{rel} is still a known hole -- if it now routes, take it out "
              f"of KNOWN_HOLES")
    print(f"  ({len(KNOWN_HOLES)} known hole(s): "
          f"{', '.join(KNOWN_HOLES) or 'none'})")

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + "; ".join(_failures))
        return 1
    print("test_park_surfaces.py: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
