# SPDX-License-Identifier: CC0-1.0
"""Guard test: the sync notice says where turns actually came from.

Hearthkin polls the active kin's conversation file for changes made outside
the desktop UI, reloads, and announces what arrived. That announcement said
"from Telegram" — as a hard-coded string, because when it was written the
Telegram bot was the only thing that could write to a kin's conversation
behind the desktop's back.

That stopped being true. Imports, scheduled wake-ups, a kin's own reach_out,
Discord and rooms all write there now. So importing 204 turns of Skype history
into a kin with Telegram **switched off**, no bot token, and an empty allow_from
list announced:

    "Synced 191 new messages from Telegram."

Confidently, and about a surface that kin has never been on. The data was
perfect; the sentence about it was invented. That is the same failure as
telling a kin a person denied a command they never saw, or logging a stopped
reply as an empty one: the harness narrating something it did not check.

Every row already carries a `source`. This reads it.

The rule that matters most here is the mixed-batch one: when turns arrive from
more than one place, it says nothing rather than choosing. A count with no
provenance is honest. A count with the wrong provenance is what caused this.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from frame.chat_stream_mixin import ChatStreamMixin  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


class Frame:
    _describe_sync_source = ChatStreamMixin._describe_sync_source
    _SYNC_SOURCE_WORDS = ChatStreamMixin._SYNC_SOURCE_WORDS

    def __init__(self, sources):
        self.conversation = [{"role": "user", "content": "x", "source": s}
                             for s in sources]


def phrase(sources, delta=None):
    f = Frame(sources)
    return f._describe_sync_source(delta if delta is not None else len(sources))


# --- the actual incident -------------------------------------------------

check("an import is called an import, not Telegram",
      phrase(["import:skype"] * 4) == " from an import")
check("...and the word Telegram appears nowhere near it",
      "Telegram" not in phrase(["import:skype"] * 4))


# --- each source names itself -------------------------------------------

check("Telegram still says Telegram when it IS Telegram",
      phrase(["telegram:12345"] * 3) == " from Telegram")
check("a kin reaching out says so",
      phrase(["reach_out"] * 2) == " — a kin reached out")
check("a scheduled wake-up says so", phrase(["cron:07:30"]) == " from a scheduled wake-up")
check("Discord says Discord", phrase(["discord:99"]) == " from Discord")
check("a room says room", phrase(["room:hearth"]) == " from a room")


# --- mixed batches say nothing rather than guessing ----------------------

check("a mixed batch names no source at all",
      phrase(["telegram:1", "import:skype"]) == "")
check("...which still reads as a correct sentence",
      f"Synced 2 new messages{phrase(['telegram:1', 'import:skype'])}." ==
      "Synced 2 new messages.")


# --- it only looks at what just arrived ----------------------------------

f = Frame(["telegram:1"] * 10 + ["import:skype"] * 3)
check("older turns don't colour the description of new ones",
      f._describe_sync_source(3) == " from an import")


# --- unknown and absent sources are not guessed at -----------------------

check("an unrecognised source adds nothing", phrase(["something_new:1"]) == "")
check("rows with no source at all add nothing", phrase([None, None]) == "")


# --- never raises --------------------------------------------------------
#
# This runs inside a poll on the UI thread. A fault here must not be able to
# break the reload it is describing.

class Hostile:
    _describe_sync_source = ChatStreamMixin._describe_sync_source
    _SYNC_SOURCE_WORDS = ChatStreamMixin._SYNC_SOURCE_WORDS

    def __getattr__(self, name):
        raise RuntimeError("no conversation")


try:
    out = Hostile()._describe_sync_source(3)
    ok = out == ""
except Exception as e:
    ok = False
    print(f"   raised: {e!r}")
check("a broken frame degrades to saying nothing, not to raising", ok)

for junk in ([{"source": 12345}], ["not a dict"], [{}], []):
    f = Frame([])
    f.conversation = junk
    try:
        f._describe_sync_source(2)
        ok = True
    except Exception as e:
        ok = False
        print(f"   raised: {e!r}")
    check(f"survives rows like {str(junk)[:30]!r}", ok)


# --- the hard-coded string is gone --------------------------------------

# Assert the PROPERTY, not the absence of a phrase. The first version of this
# check grepped for "from Telegram" and failed on the docstring above
# explaining the bug — a test that forbids describing the thing it guards.
import pathlib  # noqa: E402
src = (pathlib.Path(__file__).resolve().parent.parent
       / "frame" / "chat_stream_mixin.py").read_text(encoding="utf-8")
notice = src[src.index("Synced {delta} new message"):]
notice = notice[:notice.index("\n            )")]
check("the sync notice derives its source rather than naming one",
      "_describe_sync_source(delta)" in notice)
check("...and hard-codes no surface of its own",
      not any(w in notice for w in ("Telegram", "Discord", "import")))


print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_sync_provenance: all checks passed")
