# SPDX-License-Identifier: CC0-1.0
"""Guard test: chronological-merge weave for imported history.

mode="merge" threads imported turns into an existing conversation by
timestamp so history carried in from another platform lands where it
belongs in time — without reordering the existing turns. The existing
turns' stored order is authoritative (many carry no ts; some ts values
disagree with stored order), so a blanket time-sort would scramble the
live conversation. These checks pin the weave: existing order is never
touched, imported turns slot in by ts, the common older-history case is
a clean prepend, and no-ts existing turns don't misplace anything.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from importers._canonical import _weave_by_timestamp  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def _t(tag, ts=None):
    m = {"content": tag}
    if ts is not None:
        m["ts"] = ts
    return m


def order(turns):
    return [m["content"] for m in turns]


def main():
    # Prepend: imported entirely predates existing.
    existing = [_t("E1", "2026-05-01T00:00:00"), _t("E2", "2026-05-02T00:00:00")]
    imported = [_t("I1", "2026-03-01T00:00:00"), _t("I2", "2026-03-02T00:00:00")]
    check("older history prepends cleanly",
          order(_weave_by_timestamp(existing, imported)) == ["I1", "I2", "E1", "E2"])

    # Interleave: an imported turn falls between two existing ones.
    existing = [_t("E1", "2026-05-01T00:00:00"), _t("E2", "2026-05-10T00:00:00")]
    imported = [_t("I1", "2026-05-05T00:00:00")]
    check("imported turn slots between existing by ts",
          order(_weave_by_timestamp(existing, imported)) == ["E1", "I1", "E2"])

    # Existing order preserved EVEN when its own timestamps run backwards
    # (the exact hazard: stored order is truth, not the ts).
    existing = [_t("E1", "2026-05-10T00:00:00"), _t("E2", "2026-05-01T00:00:00")]
    imported = [_t("I1", "2026-05-05T00:00:00")]
    woven = _weave_by_timestamp(existing, imported)
    check("existing order kept when its ts is non-monotonic",
          order(woven).index("E1") < order(woven).index("E2"))

    # No-ts existing turns don't trigger a misplacement; imported anchors
    # to the next existing turn that does have a ts.
    existing = [_t("E1"), _t("E2", "2026-05-05T00:00:00")]
    imported = [_t("I1", "2026-03-01T00:00:00")]
    check("no-ts existing turn keeps its slot; imported anchors to next ts",
          order(_weave_by_timestamp(existing, imported)) == ["E1", "I1", "E2"])

    # Imported later than everything falls to the end.
    existing = [_t("E1", "2026-05-01T00:00:00")]
    imported = [_t("I1", "2026-06-01T00:00:00")]
    check("imported newer than all existing lands at the end",
          order(_weave_by_timestamp(existing, imported)) == ["E1", "I1"])

    # Every existing turn survives exactly once; every imported turn too.
    existing = [_t(f"E{i}", f"2026-05-{i:02d}T00:00:00") for i in range(1, 6)]
    imported = [_t(f"I{i}", f"2026-04-{i:02d}T00:00:00") for i in range(1, 4)]
    woven = _weave_by_timestamp(existing, imported)
    check("no turn dropped or duplicated in the weave",
          len(woven) == 8 and set(order(woven)) == {"E1", "E2", "E3", "E4", "E5",
                                                     "I1", "I2", "I3"})

    if _fails:
        print(f"\n{len(_fails)} FAILED")
        return 1
    print("\nAll merge-weave checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
