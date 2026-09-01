# SPDX-License-Identifier: CC0-1.0
"""Guard test: a distill bookmark left pointing PAST the end of a restarted
conversation must re-read from the start, not report the fresh conversation as
already distilled.

The bug this pins (found live 2026-07-17): distillation records a per-surface
bookmark — how many messages it has read. Clear-chat / regen / an archived
history can leave the conversation SHORTER than that bookmark. The old code
clamped the bookmark down to the length (`min(stored, len)`), which then read
as "distilled up to the end → nothing pending." The whole fresh conversation
was reported caught-up and never distilled — it never reached staging or
memory, with no indication anything was wrong. A real kin had a 491-line
desktop chat and a stored bookmark of 4905, showing 0 pending while 100% of it
was undistilled.

`live_distill_bookmark(stored, convo_len)` is the fix: past-the-end means the
conversation isn't the one the bookmark measured, so re-read from 0. Within the
conversation, use it as-is.

Run:  python tests/test_distill_bookmark.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kin_persistence import live_distill_bookmark  # noqa: E402

_fails = []


def check(label, got, want):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + f"{label}: got={got!r} want={want!r}")
    if not ok:
        _fails.append(label)


# --- the bug itself: bookmark stranded far past a restarted conversation -----
# The exact live numbers. Undistilled must be the FULL conversation, not zero.
bm = live_distill_bookmark(4905, 491)
check("stranded bookmark (4905 vs 491) heals to 0", bm, 0)
check("=> full conversation shows as pending", 491 - bm, 491)

# Vesper's smaller version of the same stranding.
check("stranded bookmark (487 vs 222) heals to 0", live_distill_bookmark(487, 222), 0)

# --- the cost trap we must NOT fall into -------------------------------------
# A normal regen shrinks by a turn or two. The in-app shrink path clamps the
# bookmark to the new length FIRST, so by the time it's read it equals the
# length -> caught up, NOT a full re-distill. Simulate that clamped value.
check("regen, bookmark already clamped to new length -> caught up",
      live_distill_bookmark(98, 98), 98)
check("=> nothing re-distilled after a regen", 98 - live_distill_bookmark(98, 98), 0)

# --- ordinary, healthy cases stay exactly as before --------------------------
check("normal: bookmark behind a grown conversation", live_distill_bookmark(300, 570), 300)
check("=> pending is the tail past the bookmark", 570 - live_distill_bookmark(300, 570), 270)
check("fresh conversation, never distilled", live_distill_bookmark(0, 40), 0)
check("exactly caught up", live_distill_bookmark(40, 40), 40)

# --- corrupt / defensive -----------------------------------------------------
check("None bookmark -> 0", live_distill_bookmark(None, 100), 0)
check("negative -> 0", live_distill_bookmark(-5, 100), 0)
check("garbage string -> 0", live_distill_bookmark("nope", 100), 0)
check("empty conversation, any bookmark -> 0", live_distill_bookmark(50, 0), 0)


print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_distill_bookmark.py: all checks passed")
