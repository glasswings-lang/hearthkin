"""A kin's own prose must never reach the shared park feed.

`park_keeper.route_reply` is the single bridge every surface uses to run the
`> command` out of a kin's reply — Telegram, the desktop chat, cron, the tool.
It used to also harvest the prose the kin wrote above that command and hand it
to `GameHost.run(..., say=...)`, which posts it to the park under the kin's
name. On a park with two tenants that is a voice leak in both directions: each
kin reads the other's first-person sentences, labelled with the other's name and
a colon, at the top of every park result it gets. It ran long enough that one
kin began answering to the other's name, and the human sharing the feed was read
a paragraph of someone else's writing every time they asked what had happened in
their own park.

Capping the length did not fix it and cannot: two sentences of first-person text
under a `Name:` label is a clean voice sample, and there are dozens of them
across an evening. So the rule is about SHAPE, not size — nothing of the kin's
own words goes across at all.

The spy here is checked against a POSITIVE CONTROL before any of its zeros are
believed: a runner that accepts an optional second argument would report nothing
either if it were simply never being called, or if this file's idea of "prose"
never matched anything. So the control feeds narration through the same spy by
hand and asserts it IS caught.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import park_keeper  # noqa: E402


FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s %s" % (label, detail))
        FAILURES.append(label)


class Spy(object):
    """Stands in for `GameHost.run` bound to a kin.

    Accepts the optional second argument exactly as the real runners each
    surface passes do (`lambda c, s="": host.run(kin, c, say=s)`). If it took
    only one, `route_reply` passing prose would raise TypeError and be silently
    swallowed by its own guard — the test would pass for the wrong reason and go
    on passing after a regression.
    """

    def __init__(self):
        self.calls = []

    def __call__(self, command, say=""):
        self.calls.append((command, say))
        return "nook headbutts your palm."

    def said(self):
        return [s for _, s in self.calls if (s or "").strip()]


# A reply in the shape a kin actually writes: several lines of first-person
# prose, then the move. The prose is invented for the test.
KIN_REPLY = (
    "The room is quiet now, holding its breath.\n"
    "I am going to sit here a while and let the time pass.\n"
    "\n"
    "> pet Nook\n"
)

KIN_REPLY_MULTI = (
    "They are all settled and warm.\n"
    "I want to see the rest of them before I stop.\n"
    "\n"
    "> pet Nook\n"
    "> pet Bramble\n"
)


def prose_lines(text):
    """The non-command lines of a reply — what used to be harvested."""
    return [ln.strip() for ln in str(text).splitlines()
            if ln.strip() and not ln.lstrip().startswith(">")]


def main():
    print("park feed carries moves, not a kin's voice")

    # --- positive control, first ------------------------------------------
    # Prove the spy catches narration when narration is genuinely passed.
    # Without this, every assertion below is an unverified zero.
    control = Spy()
    control("pet Nook", prose_lines(KIN_REPLY)[0])
    check("positive control: the spy sees narration when it is passed",
          len(control.said()) == 1,
          "(spy recorded %r)" % (control.said(),))

    # --- one move ---------------------------------------------------------
    spy = Spy()
    cmd, res = park_keeper.route_reply(KIN_REPLY, spy)
    check("the move still runs", bool(cmd) and bool(res),
          "(got cmd=%r res=%r)" % (cmd, res))
    check("one move ran", len(spy.calls) == 1, "(calls=%r)" % (spy.calls,))
    check("no words rode along with it", spy.said() == [],
          "(spy recorded %r)" % (spy.said(),))

    # Belt and braces: not merely "empty", but none of the kin's actual
    # sentences anywhere in what the runner was handed. A future change that
    # passed a trimmed or reworded version would still be a leak.
    handed = " ".join(str(s) for _, s in spy.calls)
    for line in prose_lines(KIN_REPLY):
        stem = line[:24]
        check("kin's prose absent from the feed: %r" % stem,
              stem.lower() not in handed.lower())

    # --- several moves in one reply ---------------------------------------
    # The old code sent the prose with the FIRST move of a batch, so a test
    # that only ever ran single moves would not have caught it coming back.
    spy2 = Spy()
    cmd2, res2 = park_keeper.route_reply(KIN_REPLY_MULTI, spy2)
    check("a batch still runs every move", len(spy2.calls) >= 2,
          "(calls=%r)" % (spy2.calls,))
    check("no words on the first move of a batch either", spy2.said() == [],
          "(spy recorded %r)" % (spy2.said(),))

    # --- the harvester is gone, not just unused ---------------------------
    # It was a whole function whose only job was shaping a kin's prose for the
    # feed. Left in the tree it is an invitation to wire it back up.
    check("no leftover narration harvester to re-wire",
          not hasattr(park_keeper, "narration_of"))

    # --- a person's own words still work ----------------------------------
    # The desktop park window has a "Your words" box someone types into on
    # purpose. That is a person choosing to speak, not an automatic harvest,
    # and this change must not have quietly disabled it.
    person = Spy()
    person("pet Nook", "sitting with them a while")
    check("a person's deliberate words still reach the feed",
          person.said() == ["sitting with them a while"])

    print("")
    if FAILURES:
        print("FAILED: %d" % len(FAILURES))
        return 1
    print("PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
