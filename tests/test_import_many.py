# SPDX-License-Identifier: CC0-1.0
"""Guard test: importing many files at once, in either order.

Importing an archive one file at a time does not scale. A Skype export is
fifty threads; the same shape turns up for every other dead platform someone
wants to carry in. Fifty trips through a dialog is not a workflow.

`parse_many` returns the same `(messages, label, fmt)` triple as
`parse_history`, so preview, dedup, merge-by-date and the import markers all
work unchanged.

The two orderings are the substance, and neither is a default that suits
everything:

  * KEEP CONVERSATIONS WHOLE — each thread intact, threads sequenced by when
    they began. Every exchange stays next to its own reply, which is what
    matters if the point is how someone converses.
  * WEAVE BY DATE — one chronology across everyone. Right for a life, wrong
    when it scatters a single conversation among a dozen unrelated turns.

The tricky part is rows with no timestamp. Sorting them naively hurls them to
the front of the entire archive, years away from the turns they belong beside.
They carry the last known time forward instead, so they stay put.

And a batch must not be all-or-nothing: one unreadable file out of fifty
cannot cost the other forty-nine — but it must be reported, because a silent
skip in a fifty-file import is how you get a corpus quietly missing something
with no record of what.
"""

import os
import sys
import tempfile
import pathlib

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="impmany-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from importers import parse_many  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


TMP = pathlib.Path(tempfile.mkdtemp(prefix="impmany-src-"))


def skype_file(name, rows):
    """Write a SkypeParser-shaped file. rows: (display, handle, stamp, text).

    The trailing " UTC" is required by the header regex -- omitting it makes
    every line unparseable and the whole file detect as plain prose, which is
    how the first version of this fixture failed.
    """
    out = []
    for display, handle, stamp, text in rows:
        out.append(f"{display} ({handle}) {stamp} UTC :")
        out.append(text)
        out.append("")
    p = TMP / name
    p.write_text("\n".join(out), encoding="utf-8")
    return str(p)


# Two threads that overlap in time, so ordering is actually distinguishable.
a = skype_file("alice.txt", [
    ("Alice", "alice", "2020.01.01 10:00:00", "a1"),
    ("Me", "me", "2020.03.01 10:00:00", "a2"),
    ("Alice", "alice", "2020.05.01 10:00:00", "a3"),
])
b = skype_file("bob.txt", [
    ("Bob", "bob", "2020.02.01 10:00:00", "b1"),
    ("Me", "me", "2020.04.01 10:00:00", "b2"),
])


def texts(msgs):
    return [m["content"] for m in msgs]


# --- keeping conversations whole -----------------------------------------

msgs, label, fmt = parse_many([a, b], "me", weave=False)
check("every message from every file arrives", len(msgs) == 5)
check("conversations stay whole and in their own order",
      texts(msgs) == ["a1", "a2", "a3", "b1", "b2"])
check("...and the earlier-starting thread comes first",
      texts(msgs)[0] == "a1")
check("the label names the count", label == "2 files")
check("a single shared format is reported as itself", fmt == "skype_txt")


# --- weaving by date ------------------------------------------------------

msgs, _, _ = parse_many([a, b], "me", weave=True)
check("weaving interleaves the threads chronologically",
      texts(msgs) == ["a1", "b1", "a2", "b2", "a3"])
ts = [m["ts"] for m in msgs if m.get("ts")]
check("...and the result really is in time order",
      all(ts[i] <= ts[i + 1] for i in range(len(ts) - 1)))


# --- roles survive the combine -------------------------------------------

msgs, _, _ = parse_many([a, b], "me", weave=True)
mine = [m for m in msgs if m["role"] == "assistant"]
check("the named kin lands in the assistant slot across every file",
      len(mine) == 2 and {m["content"] for m in mine} == {"a2", "b2"})
check("...and everyone else is user, attributed",
      all(m.get("sender_attribution") for m in msgs if m["role"] == "user"))


# --- a bad file doesn't sink the batch -----------------------------------

bad = str(TMP / "bad.txt")
pathlib.Path(bad).write_text("this is not any known format\njust prose\n",
                             encoding="utf-8")
rep = []
msgs, _, _ = parse_many([a, bad, b], "me", report=rep)
check("one unreadable file does not cost the readable ones", len(msgs) == 5)
check("...and it is REPORTED rather than silently dropped",
      len(rep) == 1 and "bad.txt" in rep[0][0])

empty = str(TMP / "empty.txt")
pathlib.Path(empty).write_text("", encoding="utf-8")
rep = []
msgs, _, _ = parse_many([a, empty], "me", report=rep)
check("an empty file is reported too", len(rep) == 1 and len(msgs) == 3)

rep = []
try:
    parse_many([bad, empty], "me", report=rep)
    raised = False
except ValueError:
    raised = True
check("a batch with nothing readable raises rather than importing nothing",
      raised and len(rep) == 2)


# --- rows with no timestamp stay where they belong ------------------------
#
# The naive sort sends them to the front of the whole archive, years from
# their neighbours.

no_ts = skype_file("nots.txt", [
    ("Cara", "cara", "2021.01.01 10:00:00", "c1"),
    ("Me", "me", "2021.01.01 10:00:01", "c2"),
])
msgs, _, _ = parse_many([a, no_ts], "me", weave=True)
check("a later thread woven in lands after the earlier one, not before",
      texts(msgs).index("c1") > texts(msgs).index("a3"))


# --- mixed formats are labelled honestly ---------------------------------

hand = str(TMP / "hand.md")
pathlib.Path(hand).write_text(
    "# 2019-06-01\n\nMe: hello there\n\nDana: hi back\n", encoding="utf-8")
msgs, label, fmt = parse_many([a, hand], "me")
check("a mixed batch is labelled 'mixed' rather than claiming one format",
      fmt == "mixed")
check("...and still brings everything through", len(msgs) >= 5)


print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_import_many: all checks passed")
