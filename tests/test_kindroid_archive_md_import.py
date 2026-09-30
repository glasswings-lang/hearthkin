# SPDX-License-Identifier: CC0-1.0
"""Importing the readable half of a Kindroid archive.

The archiver writes two files per month: a .jsonl record and a Markdown view
of it. The Markdown is the one a person actually opens, so it is the one they
reach for in a file picker — and it used to fail with "No messages parsed.
Detected format: plain", which is true and helps nobody.

It is worth reading rather than refusing, because unlike Kindroid's own text
export this one has the times and the speakers written into its headings.

Fixtures are synthetic. Nobody's actual conversation should be a test
fixture, and a real one in here would ship with the repo.

No pytest. Plain checks, a summary line, exit 1 on any failure.
Run:  python tests/test_kindroid_archive_md_import.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from importers import kindroid_api, parse_history           # noqa: E402

_fails = []


def check(label, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + label
          + ((" — " + detail) if detail and not cond else ""))
    if not cond:
        _fails.append(label)


def write(text):
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8")
    handle.write(text)
    handle.close()
    return handle.name


SAMPLE = """# 2026-09

## 2026-09-01 14:11 - SpeakerFifteen

Is the kettle on?

## 2026-09-01 16:47 - Priya

It is. Two minutes.

## 2026-09-01 16:48 - SpeakerFifteen

Good.

*Image: a kettle on a windowsill*
"""


def test_it_is_recognised_without_being_told():
    path = write(SAMPLE)
    raw = open(path, encoding="utf-8").read()
    check("detected", kindroid_api.detect_markdown(raw) is True)
    messages, label, fmt = parse_history(path, "SpeakerFifteen")
    check("the dispatcher sends it here", fmt == "kindroid-archive-md", fmt)
    check("labelled as kindroid", label == "kindroid", label)
    check("all three messages", len(messages) == 3, str(len(messages)))


def test_it_does_not_swallow_other_markdown():
    """Claude's saved conversations are .md too, with bold speaker lines."""
    claude = ("**Human:**\n\nhello\n\n**Assistant:**\n\nhi there\n")
    check("bold-speaker markdown is left alone",
          kindroid_api.detect_markdown(claude) is False)
    check("a plain text export is left alone",
          kindroid_api.detect_markdown(
              "SpeakerFifteen\nplay\nmorning\n") is False)
    check("one lone heading is not enough to claim the file",
          kindroid_api.detect_markdown(
              "## 2026-09-01 14:11 - Someone\n\nhello\n") is False)


def test_the_times_in_the_headings_are_used():
    path = write(SAMPLE)
    messages, _label, _fmt = parse_history(path, "SpeakerFifteen")
    stamps = [m["ts"] for m in messages]
    check("the first one is its heading's time",
          stamps[0] == "2026-09-01T14:11:00", stamps[0])
    check("and they are in order", stamps == sorted(stamps), str(stamps))


def test_seconds_are_optional_in_a_heading():
    path = write(SAMPLE.replace("## 2026-09-01 14:11 -",
                                "## 2026-09-01 14:11:09 -"))
    messages, _label, _fmt = parse_history(path, "SpeakerFifteen")
    check("a heading with seconds reads right",
          messages[0]["ts"] == "2026-09-01T14:11:09", messages[0]["ts"])


def test_whose_turns_are_whose():
    path = write(SAMPLE)
    messages, _label, _fmt = parse_history(path, "SpeakerFifteen")
    mine = [m for m in messages if m["role"] == "assistant"]
    theirs = [m for m in messages if m["role"] == "user"]
    check("two are the kin's", len(mine) == 2, str(len(mine)))
    check("one is the person's", len(theirs) == 1, str(len(theirs)))
    check("and the person is named",
          theirs[0].get("sender_attribution") == "Priya", str(theirs[0]))
    check("the kin's own turns are not attributed to anyone",
          all("sender_attribution" not in m for m in mine))


def test_a_name_that_matches_nothing_claims_no_turns():
    """Deliberately the opposite of the .jsonl rule.

    In the record, "ai" is stated, so a non-matching name is a naming
    problem and the replies are still obviously the kin's. Here there is
    nothing to fall back on — every heading is just a name — so guessing
    would mean handing one person's words to another. The preview shows zero
    kin turns and the operator can fix the name.
    """
    path = write(SAMPLE)
    messages, _label, _fmt = parse_history(path, "Nobody")
    check("nothing was claimed for the kin",
          all(m["role"] == "user" for m in messages), str(messages))
    check("but nothing was lost either", len(messages) == 3,
          str(len(messages)))
    check("and everyone is named",
          {m.get("sender_attribution") for m in messages}
          == {"SpeakerFifteen", "Priya"}, str(messages))


def test_a_message_body_keeps_its_own_blank_lines():
    path = write("# 2026-09\n\n"
                 "## 2026-09-01 14:11 - SpeakerFifteen\n\n"
                 "first line\n\nsecond line\n\n"
                 "## 2026-09-01 14:12 - Priya\n\nreply\n")
    messages, _label, _fmt = parse_history(path, "SpeakerFifteen")
    check("the paragraph break survives",
          messages[0]["content"] == "first line\n\nsecond line",
          repr(messages[0]["content"]))


def test_the_notes_the_archiver_adds_come_through():
    path = write(SAMPLE)
    messages, _label, _fmt = parse_history(path, "SpeakerFifteen")
    joined = "\n".join(m["content"] for m in messages)
    check("the image note is kept",
          "a kettle on a windowsill" in joined, joined)


def test_a_heading_with_no_body_is_skipped():
    path = write("# 2026-09\n\n"
                 "## 2026-09-01 14:11 - SpeakerFifteen\n\n"
                 "## 2026-09-01 14:12 - Priya\n\nreal\n")
    messages, _label, _fmt = parse_history(path, "SpeakerFifteen")
    check("only the one with words in it", len(messages) == 1,
          str(messages))


def test_every_turn_says_where_it_came_from():
    path = write(SAMPLE)
    messages, _label, _fmt = parse_history(path, "SpeakerFifteen")
    check("stamped as an import",
          all(m["source"] == "import:kindroid" for m in messages))


def test_the_two_halves_of_one_archive_agree():
    """The readable copy is a view of the record. They must not disagree
    about who said what, or importing the wrong file changes the history."""
    import json
    rows = [
        {"id": "a", "sender": "ai", "display_name": "SpeakerFifteen",
         "timestamp": 1788300660000, "message": "Is the kettle on?"},
        {"id": "b", "sender": "user", "display_name": "Priya",
         "timestamp": 1788300720000, "message": "It is. Two minutes."},
    ]
    record = tempfile.NamedTemporaryFile(
        "w", suffix=".jsonl", delete=False, encoding="utf-8")
    for row in rows:
        record.write(json.dumps(row) + "\n")
    record.close()

    readable = write("# 2026-09\n\n"
                     "## 2026-09-01 14:11 - SpeakerFifteen\n\n"
                     "Is the kettle on?\n\n"
                     "## 2026-09-01 14:12 - Priya\n\n"
                     "It is. Two minutes.\n")

    from_record, _l1, fmt1 = parse_history(record.name, "SpeakerFifteen")
    from_readable, _l2, fmt2 = parse_history(readable, "SpeakerFifteen")
    check("read by different parsers", fmt1 != fmt2, "%s %s" % (fmt1, fmt2))
    check("same number of messages",
          len(from_record) == len(from_readable))
    check("same roles in the same order",
          [m["role"] for m in from_record]
          == [m["role"] for m in from_readable])
    check("same words",
          [m["content"] for m in from_record]
          == [m["content"] for m in from_readable])
    check("same speakers",
          [m["speaker"] for m in from_record]
          == [m["speaker"] for m in from_readable])


for fn in list(globals().values()):
    if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
        print("\n--- %s" % fn.__name__)
        fn()

if _fails:
    print("\nFAILED %d: %s" % (len(_fails), ", ".join(_fails)))
    sys.exit(1)
print("\ntest_kindroid_archive_md_import: all checks passed")
sys.exit(0)
