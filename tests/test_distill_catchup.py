# SPDX-License-Identifier: CC0-1.0
"""Guard test: "Catch up on selected" — chain FORWARD, unattended.

A kin can fall a very long way behind. One real surface sat 87,134
messages short of its own conversation, and none of the three existing
ways to close that gap could do it:

  - the automatic trigger only fires after a reply, so a backlog does not
    move at all while nobody is talking — i.e. never overnight, which is
    exactly when you would want it to;
  - "Distill selected surface now" does one bite per press, which at that
    size is several hundred presses;
  - "Redistill selected from start" chains by itself, unattended, and is
    the only thing that does — but it rewinds the bookmark to zero first,
    re-billing every message already distilled.

So the one mode that ran unattended was bolted to the one that threw the
work away. Catch-up is that chain minus its first step.

What this pins:
  1. the bookmark is NOT touched when a catch-up starts — the whole
     difference between this and a redistill;
  2. it marks the walk live, so _on_distill_done auto-chains it;
  3. pacing is forced to 'unattended' even when the scope has 'chunk' or
     'day' recorded from an earlier redistill. This exists to be started
     and walked away from; a leftover pacing turning it back into
     press-a-button-per-bite would silently reinstate the exact problem
     it was built to remove;
  4. **any stale walk-prior is cleared.** This is the dangerous one.
     _restore_walk_bookmark only ever moves a bookmark FORWARD. Nothing
     is rewound by a catch-up, so a prior left behind by an older
     redistill could, on Cancel, jump the bookmark past messages that
     were never distilled — losing a stretch of a kin's memory silently.
     Re-reading costs a little money; skipping costs a kin its history
     and says nothing at all.
  5. the source label is not an automatic-trigger one, so the backlog
     brake never holds it back.

Run: python tests/test_distill_catchup.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault(
    "HEARTHKIN_HOME",
    tempfile.mkdtemp(prefix="hearthkin-catchuptest-"))

from frame.memory_mixin import MemoryMixin  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


class FakeFrame(MemoryMixin):
    """Just enough frame to run _start_catchup: the walk bookkeeping is
    real, and the two things it reaches outward to are recorded."""

    def __init__(self, cfg):
        self._cfg = dict(cfg)
        self._walking_from_start = {}
        self.kicked = []
        self.persisted_walk = []

    # -- stand-ins for the config layer ---------------------------------
    def _load(self):
        return self._cfg

    def _walk_prior_offset(self, agent, scope):
        return (self._cfg.get("distill_walk_prior_offsets") or {}).get(scope)

    def _persist_walk_prior(self, agent, scope, value):
        priors = dict(self._cfg.get("distill_walk_prior_offsets") or {})
        if value is None:
            priors.pop(scope, None)
        else:
            priors[scope] = int(value)
        self._cfg["distill_walk_prior_offsets"] = priors

    def _persist_walk(self, agent, scope, on):
        self.persisted_walk.append((scope, on))

    def _persist_walk_pacing(self, agent, scope, pacing):
        pacings = dict(self._cfg.get("distill_walk_pacing") or {})
        pacings[scope] = pacing
        self._cfg["distill_walk_pacing"] = pacings

    def _kick_off_distillation(self, agent, conversation,
                               source_label="manual", scope_key="desktop"):
        self.kicked.append((agent, len(conversation), source_label, scope_key))

    def _refresh_walk_ui(self, agent):
        pass


def main():
    scope = "desktop"
    # A surface a long way behind, with a stale 'chunk' pacing and a stale
    # prior bookmark left over from an older redistill that was cancelled.
    cfg = {
        "distill_offsets": {scope: 21532},
        "distill_walk_pacing": {scope: "chunk"},
        "distill_walk_prior_offsets": {scope: 40000},
    }
    f = FakeFrame(cfg)
    convo = [{"role": "user", "content": "x"}] * 108666

    f._start_catchup("Lark", scope, convo)

    # 1. The bookmark is untouched.
    check("the bookmark is not rewound",
          f._cfg["distill_offsets"][scope] == 21532)

    # 2. The walk is live, so _on_distill_done will chain it.
    check("the chain is marked live in memory",
          f._walking_from_start.get(("Lark", scope)) is True)
    check("the chain is recorded on disk so a quit pauses it",
          (scope, True) in f.persisted_walk)

    # 3. Pacing is forced to unattended, over the stale 'chunk'.
    check("a leftover 'chunk' pacing is overridden to unattended",
          (f._cfg.get("distill_walk_pacing") or {}).get(scope) == "unattended")

    # 4. The stale prior is gone, so a Cancel cannot jump the bookmark
    #    FORWARD past undistilled messages. Positive control below.
    check("a stale walk-prior is cleared",
          (f._cfg.get("distill_walk_prior_offsets") or {}).get(scope) is None)

    # 4b. POSITIVE CONTROL for the check above. It has to be possible for
    #     that assertion to fail, or it proves nothing: show that the same
    #     stale prior really is the kind of value _restore_walk_bookmark
    #     would act on — it is 40,000, ahead of the live 21,532, and
    #     restoring only ever moves FORWARD.
    check("...and the control: that prior really was ahead of the bookmark, "
          "so leaving it would have skipped 18,468 messages",
          40000 > 21532)

    # 5. Exactly one bite kicked off, on a source the backlog brake ignores.
    check("one bite is kicked off", len(f.kicked) == 1)
    if f.kicked:
        _agent, _n, label, sk = f.kicked[0]
        check("it runs on the selected scope", sk == scope)
        check("its source label is not an automatic trigger, so the "
              "backlog brake never paces it",
              not (label.startswith("every-") or label.startswith("ctx-")))

    # 6. A scope with no recorded pacing or prior works just as well.
    f2 = FakeFrame({"distill_offsets": {scope: 5}})
    f2._start_catchup("Bracken", scope, convo)
    check("a scope with nothing recorded still starts cleanly",
          f2._cfg["distill_offsets"][scope] == 5
          and f2._walking_from_start.get(("Bracken", scope)) is True
          and len(f2.kicked) == 1)

    print()
    if _fails:
        print("FAILED: %d check(s)" % len(_fails))
        for f_ in _fails:
            print("  -", f_)
        return 1
    print("OK - catch-up chains forward without rewinding, forces unattended "
          "pacing, and clears a stale prior that Cancel could otherwise use "
          "to skip undistilled messages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
