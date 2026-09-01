# SPDX-License-Identifier: CC0-1.0
"""Guard test: a kin sharing the operator's own name.

Nothing on disk breaks when a kin is named the same as the operator —
which is exactly why it's easy to walk into. What breaks is attribution:

  * Rooms. Room history reaches each kin as `assistant: [Name]: content`
    and the operator's turns are prefixed `[<user_name>] `. One string
    for two speakers leaves the model no way to separate them.
  * Distillation. `_infer_operator_name` needs exactly one unique non-kin
    speaker; a collision leaves none, and the summarizer then attributes
    the operator's own past to the harness.

So creation and rename warn (and let the operator proceed anyway — it's
a real choice, not an error). These checks pin the detection rule: it
fires on the operator's name regardless of case or padding, and never
fires when no operator name is set.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from frame.kin_mgmt_mixin import KinMgmtMixin  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


class _Stub(KinMgmtMixin):
    """Just enough object to exercise the detector."""

    def __init__(self, user_name):
        self.config = {"user_name": user_name}


def main():
    me = _Stub("Wanderer")
    check("fires on an exact match", me._name_collides_with_operator("Wanderer"))
    check("fires regardless of case", me._name_collides_with_operator("wanderer"))
    check("fires on WANDERER", me._name_collides_with_operator("WANDERER"))
    check("fires through surrounding space",
          me._name_collides_with_operator("  Wanderer  "))

    check("leaves a different name alone",
          not me._name_collides_with_operator("Vesper"))
    check("doesn't fire on a name merely containing it",
          not me._name_collides_with_operator("Wanderer-kin"))
    check("doesn't fire on a prefix",
          not me._name_collides_with_operator("Wand"))

    # No operator name set: nothing to collide with, and an empty
    # user_name must not make every kin name "match" the empty string.
    nobody = _Stub("")
    check("never fires when no operator name is set",
          not nobody._name_collides_with_operator("Wanderer"))
    check("an empty kin name doesn't match an empty operator name",
          not nobody._name_collides_with_operator(""))

    spaces = _Stub("   ")
    check("a whitespace-only operator name counts as unset",
          not spaces._name_collides_with_operator("Wanderer"))

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
        return 1
    print("all operator-name-collision checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
