"""Discord must reach a kin's depth logs the way every other surface does.

Per-turn recall is what puts a kin's own depth logs in front of it WITHOUT the
kin having to think of calling a memory tool. Discord shipped without it: the
bot loaded tools, ran the tool loop and distilled its conversations correctly,
so nothing looked broken -- a kin there simply never had its own depth surfaced
unless it went looking, which is the exact behaviour smaller models don't do.

That is invisible from the outside. A kin that answers without its depth reads
as a kin with nothing to say about the subject, not as a wiring gap, so nothing
would ever have reported it.

Pinned at SOURCE level rather than by driving the bot, because reaching the
Discord send path needs a live gateway connection. What can be checked without
one is that the call is present and shaped right, which is the part that was
missing.

Every check carries a POSITIVE CONTROL against a surface known to have the
wiring, so a detector that has quietly stopped detecting fails loudly instead
of reporting a clean sweep.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_FAILED = []


def check(label, ok):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        _FAILED.append(label)


def _src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8", errors="replace") as f:
        return f.read()


DISCORD = _src("discord_bot.py")
TELEGRAM = _src("telegram_bot.py")          # positive control: has the wiring
CRON = _src("hearthkin_cron.py")            # positive control: has the wiring


# --- 1. the call exists at all -------------------------------------------

check("discord calls inject_into_messages",
      "inject_into_messages" in DISCORD)

check("...control: telegram does too (detector works)",
      "inject_into_messages" in TELEGRAM)
check("...control: cron does too (detector works)",
      "inject_into_messages" in CRON)


# --- 2. it is told which brackets are ours -------------------------------
# Every Discord turn is stored as "[display name] text". Without
# speaker_names, the sender's name is part of every message the matcher
# reads, so a depth log named after that person qualifies on every single
# turn regardless of subject -- the bug the Telegram path already fixed.

_call = re.search(
    r"inject_into_messages\((.*?)\)\s*\n", DISCORD, re.S)
check("discord passes speaker_names to recall",
      bool(_call) and "speaker_names" in _call.group(1))

check("...control: telegram passes speaker_names too",
      bool(re.search(r"inject_into_messages\((?:.*?)speaker_names",
                     TELEGRAM, re.S)))


# --- 3. the names come from what WE bracketed, never from a shape --------
# CLAUDE.md's rule: match supplied names, never a bracket pattern. A kin
# opens replies with bracketed emotes, and a person may bracket their own
# words; a blind strip would eat either.

def _recall_region(src, width=1800):
    """The source around the recall call: from the local import down past the
    call itself. Wide enough to reach the guard that wraps it -- an earlier
    version of this test sliced 600 chars, which stopped short of the except
    and reported a guard that was plainly there as missing. A window too
    narrow to see the thing it checks is a detector that always says no."""
    i = src.find("from memory_recall import inject_into_messages")
    return "" if i == -1 else src[i:i + width]


_D_REGION = _recall_region(DISCORD)

check("the author's own display name is among the names passed",
      "{who}" in _D_REGION)

check("history brackets are gathered too (a channel has several people)",
      "_bracketed" in _D_REGION and "for _m in history" in _D_REGION)


# --- 4. it cannot cost a reply -------------------------------------------
# Recall is a nicety; a reply is not. Any failure must leave messages
# exactly as they were.

check("recall is wrapped so a failure can't break the turn",
      "except Exception" in _D_REGION)

check("...control: telegram guards it the same way",
      "except Exception" in _recall_region(TELEGRAM))


# --- 5. ordering: recall must see the user's turn ------------------------
# The block is inserted immediately before the latest user turn, so the
# user turn has to be on the list before recall runs.

_i_user = DISCORD.find("messages.append(user_msg)")
_i_recall = DISCORD.find("inject_into_messages")
check("recall runs after the user turn is appended",
      _i_user != -1 and _i_recall != -1 and _i_user < _i_recall)


print()
if _FAILED:
    print("test_discord_recall: %d FAILED" % len(_FAILED))
    for f in _FAILED:
        print("   - " + f)
    sys.exit(1)
print("test_discord_recall: all checks passed")
