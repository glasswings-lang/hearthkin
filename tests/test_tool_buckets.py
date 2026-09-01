# SPDX-License-Identifier: CC0-1.0
"""Guard test: every registered tool is reachable on Telegram, or is
deliberately, explicitly not.

The Telegram surface filters a kin's tools through a bucket (read / write /
full). A tool that's in NO bucket gets silently dropped for every Telegram
user, no matter what's enabled in the kin's tools.json — which is exactly how
creature_park appeared in Settings but never showed up for a Telegram-side kin
until the missing `_buckets.py` line was found.

This test makes that failure mode loud: it asserts every name in the tool
registry is either placed in a bucket or named in INTENTIONALLY_TELEGRAM_BLOCKED
(desktop-only by design). Add a tool without doing one of those two things and
this test fails with the tool's name — so a forgotten bucket can't ship.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools  # noqa: E402
from tools._buckets import BUCKETS, INTENTIONALLY_TELEGRAM_BLOCKED  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def main():
    registered = set(tools.list_available())
    bucketed = set().union(*BUCKETS.values())
    blocked = set(INTENTIONALLY_TELEGRAM_BLOCKED)

    unaccounted = registered - bucketed - blocked
    check(
        "every registered tool is bucketed or explicitly Telegram-blocked"
        + (f" — UNACCOUNTED: {sorted(unaccounted)}" if unaccounted else ""),
        not unaccounted,
    )

    # The two lists should only ever name real, registered tools — a typo or a
    # deleted tool left behind in _buckets.py would otherwise rot silently.
    bucket_ghosts = bucketed - registered
    check(
        "no bucket names a non-existent tool"
        + (f" — GHOSTS: {sorted(bucket_ghosts)}" if bucket_ghosts else ""),
        not bucket_ghosts,
    )
    block_ghosts = blocked - registered
    check(
        "no Telegram-block entry names a non-existent tool"
        + (f" — GHOSTS: {sorted(block_ghosts)}" if block_ghosts else ""),
        not block_ghosts,
    )

    # tff specifically (the park game, then named creature_park) — the tool
    # this guard was born from — must be reachable (write-tier game tool).
    check("tff is bucketed (the bug that started this)",
          "tff" in bucketed)

    # CLAUDE.md's tool list must match the registry. It had drifted to "15"
    # with three registered tools missing from the list — in the file loaded
    # into context every session, so it reads as authoritative, and those
    # three absent names are exactly the tools somebody would then conclude
    # do not exist. Derived rather than remembered, for the same reason the
    # bucket check above exists.
    import re
    from pathlib import Path as _Path
    doc = (_Path(__file__).resolve().parent.parent / "CLAUDE.md").read_text(
        encoding="utf-8")
    hit = re.search(r"Currently registered \((\d+)\)", doc)
    check("CLAUDE.md still states a registered-tool count", bool(hit))
    if hit:
        real, claimed = len(tools._REGISTRY), int(hit.group(1))
        check(f"CLAUDE.md's tool count matches the registry "
              f"(says {claimed}, registry has {real})", claimed == real)
        undocumented = [n for n in sorted(tools._REGISTRY)
                        if f"`{n}`" not in doc]
        check("every registered tool is named in CLAUDE.md"
              + (f" — MISSING: {undocumented}" if undocumented else ""),
              not undocumented)

    if _fails:
        print(f"\nFAILED {len(_fails)}: " + ", ".join(_fails))
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

