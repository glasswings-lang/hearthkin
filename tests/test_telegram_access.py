# SPDX-License-Identifier: CC0-1.0
"""Guard test: Telegram DM access and group participation are INDEPENDENT.

The design invariant (2026-07-02): `allow_from` gates who can DM a kin; a
per-group `exclude` list is the only thing that silences someone in an
opted-in group. Neither grants the other. Before this, `allow_from` doubled
as the group person-gate, so letting someone speak in a group forced DM
access on them.

This pins the two new pure pieces that make the split work:
  * `_group_excluded_ids` — parse a group's mute list (numeric, tolerant of
    telegram:/tg: prefixes, junk ignored).
  * `seen_group_members` — harvest (id, name) of everyone who's spoken in a
    group from stored history, so the operator mutes by NAME, never by
    hunting a numeric ID through a third-party bot.

The full `_handle_update` gate is integration-level (needs a live bot +
Telegram payloads); these unit checks cover the logic it now depends on.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import telegram_bot as T  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def _excluded(entry):
    # _group_excluded_ids is an unbound method needing only `self`-free logic.
    class _Fake:
        _group_excluded_ids = T.TelegramBot._group_excluded_ids
    return _Fake()._group_excluded_ids(entry)


def main():
    # ----- _group_excluded_ids parsing -----
    got = _excluded({"exclude": [111, "tg:222", "telegram:333", "-4", "@nope", "", 5]})
    check("exclude parse keeps numeric ids, strips prefixes, drops junk"
          + f" — got {sorted(got)}",
          got == {111, 222, 333, -4, 5})
    check("exclude missing key -> empty", _excluded({}) == set())
    check("exclude None entry -> empty", _excluded(None) == set())
    check("exclude empty list -> empty", _excluded({"exclude": []}) == set())

    # ----- seen_group_members harvesting (from disk) -----
    # Redirect the kin dir at a temp home so the test never touches real data.
    orig_agent_dir = T._agent_dir
    with tempfile.TemporaryDirectory() as tmp:
        T._agent_dir = lambda name: Path(tmp) / "kin" / name
        try:
            hist = {
                "group:-100999": [
                    {"role": "user", "sender_id": 111, "sender_name": "Alice", "content": "hi"},
                    {"role": "assistant", "content": "hello"},          # no sender
                    {"role": "user", "sender_id": 222, "sender_name": "Bob", "content": "yo"},
                    {"role": "user", "sender_id": 111, "sender_name": "Alice", "content": "again"},  # dup
                    {"role": "user", "content": "ghost"},               # no sender_id -> skipped
                ],
                "group:-100888": [
                    {"role": "user", "sender_id": 999, "sender_name": "Zed", "content": "elsewhere"},
                ],
            }
            T.save_telegram_history("_grptest_", hist)

            seen = T.seen_group_members("_grptest_", "-100999")
            ids = [m["id"] for m in seen]
            check("harvest dedups speakers in the group" + f" — got {ids}",
                  ids == [111, 222])
            check("harvest carries display names",
                  {m["id"]: m["name"] for m in seen} == {111: "Alice", 222: "Bob"})
            check("harvest skips turns with no sender_id",
                  all(m["id"] is not None for m in seen) and 0 not in ids)
            check("harvest is scoped to the one group (no cross-group bleed)",
                  999 not in ids)

            other = T.seen_group_members("_grptest_", "-100888")
            check("other group harvests its own speaker",
                  [m["id"] for m in other] == [999])

            empty = T.seen_group_members("_grptest_", "-100777")
            check("unknown group harvests empty", empty == [])
        finally:
            T._agent_dir = orig_agent_dir

    if _fails:
        print(f"\nFAILED {len(_fails)}: " + ", ".join(_fails))
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
