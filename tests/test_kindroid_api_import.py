# SPDX-License-Identifier: CC0-1.0
"""Importing a Kindroid history archived through their API.

The text export guesses everything — timestamps from the file's modified
time, speakers by counting name-shaped lines — and drops images and links.
This format guesses nothing, and these checks are mostly about that: that
the real timestamp is used, that the stated speaker is used, that a group
chat's other Kindroids come through as themselves, and that nothing is
silently lost.

Fixtures are synthetic. Nobody's actual conversation should be a test
fixture, and a real history in here would be published with the repo.

No pytest. Plain checks, a summary line, exit 1 on any failure.
Run:  python tests/test_kindroid_api_import.py
"""

import json
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


def write(rows, suffix=".jsonl"):
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=suffix, delete=False, encoding="utf-8")
    for row in rows:
        handle.write(json.dumps(row) + "\n")
    handle.close()
    return handle.name


# Kindroid's timestamps are milliseconds. 1788300698998 is a real-looking
# one; the seconds equivalent is used to prove the unit is detected.
MS = 1788300698998
MS_LATER = 1788310072542


def row(ident, sender, name, text, ts, **extra):
    out = {"id": ident, "sender": sender, "display_name": name,
           "timestamp": ts, "message": text}
    out.update(extra)
    return out


def test_it_is_recognised_without_being_told():
    path = write([row("a", "ai", "SpeakerFifteen", "morning", MS),
                  row("b", "user", "Priya", "morning yourself", MS_LATER)])
    raw = open(path, encoding="utf-8").read()
    check("detected", kindroid_api.detect(raw) is True)
    messages, label, fmt = parse_history(path, "SpeakerFifteen")
    check("and the dispatcher sends it here", fmt == "kindroid-api", fmt)
    check("labelled as kindroid", label == "kindroid", label)
    check("both messages came through", len(messages) == 2, str(messages))


def test_our_own_conversations_are_not_mistaken_for_it():
    """Hearthkin's own jsonl carries role and content. It must not match."""
    ours = write([{"role": "user", "content": "hello", "ts": "2026-01-01"},
                  {"role": "assistant", "content": "hi", "ts": "2026-01-01"}])
    raw = open(ours, encoding="utf-8").read()
    check("our shape is left alone", kindroid_api.detect(raw) is False)


def test_plain_text_is_not_mistaken_for_it():
    check("a text export does not match",
          kindroid_api.detect(
              "SpeakerFifteen\nplay\nmorning\nPriya\nhi\n") is False)
    check("nor does an empty file", kindroid_api.detect("") is False)


def test_the_real_timestamp_is_used_rather_than_invented():
    """The whole reason this format exists."""
    path = write([row("a", "ai", "SpeakerFifteen", "morning", MS)])
    messages, _label, _fmt = parse_history(path, "SpeakerFifteen")
    stamp = messages[0].get("ts") or ""
    check("there is a timestamp", stamp != "", str(messages[0]))
    check("and it is 2026, not today", stamp.startswith("2026-"), stamp)


def test_seconds_and_milliseconds_both_read_right():
    """An export that switches units must not land in 1970 or 56000 AD."""
    path = write([row("a", "ai", "SpeakerFifteen", "ms", MS),
                  row("b", "ai", "SpeakerFifteen", "seconds", int(MS / 1000))])
    messages, _l, _f = parse_history(path, "SpeakerFifteen")
    stamps = sorted(m["ts"][:4] for m in messages)
    check("both land in the same year", stamps == ["2026", "2026"],
          str(stamps))


def test_the_kin_speaks_as_itself_and_the_person_as_the_person():
    path = write([row("a", "ai", "SpeakerFifteen", "morning", MS),
                  row("b", "user", "Priya", "hi", MS_LATER)])
    messages, _l, _f = parse_history(path, "SpeakerFifteen")
    by_role = {m["role"]: m for m in messages}
    check("the kin's turn is the assistant's",
          by_role["assistant"]["content"] == "morning", str(messages))
    check("the person's turn is the user's",
          by_role["user"]["content"] == "hi", str(messages))
    check("and the person is named",
          by_role["user"].get("sender_attribution") == "Priya",
          str(by_role["user"]))
    check("the kin's turn is not attributed to anyone else",
          "sender_attribution" not in by_role["assistant"])


def test_a_group_chat_keeps_everyone_separate():
    """Several Kindroids speak. Only one of them is this kin."""
    path = write([row("a", "ai", "SpeakerFifteen", "mine", MS),
                  row("b", "ai", "SpeakerSixteen", "hers", MS + 1000),
                  row("c", "user", "Priya", "both of you", MS + 2000)])
    messages, _l, _f = parse_history(path, "SpeakerFifteen")
    mine = [m for m in messages if m["role"] == "assistant"]
    others = [m for m in messages if m["role"] == "user"]
    check("only the kin's own turn is the kin's", len(mine) == 1, str(mine))
    check("and it is the right one", mine[0]["content"] == "mine")
    check("the other Kindroid comes through as a speaker",
          any(m.get("sender_attribution") == "SpeakerSixteen" for m in others),
          str(others))
    check("named, not merged into the person",
          {m.get("sender_attribution") for m in others}
          == {"SpeakerSixteen", "Priya"}, str(others))


def test_a_name_that_matches_nothing_still_imports_the_replies():
    """A renamed kin or a typo must not land every reply as somebody else.

    Wrong name on present replies is fixable by reading it. Every reply
    filed as another speaker is not.
    """
    path = write([row("a", "ai", "SpeakerFifteen", "morning", MS),
                  row("b", "user", "Priya", "hi", MS_LATER)])
    messages, _l, _f = parse_history(path, "SpeakerFifteenn")      # typo
    roles = [m["role"] for m in messages]
    check("the reply is still the kin's", "assistant" in roles, str(messages))


def test_images_links_and_lookups_are_not_dropped():
    """The text export loses these silently, which is how a conversation
    stops making sense — someone reacts to a picture that is not there."""
    path = write([
        row("a", "ai", "SpeakerFifteen", "look at this", MS,
            image_urls=["https://example.invalid/kettle.png"],
            image_description="a kettle on a windowsill"),
        row("b", "ai", "SpeakerFifteen", "and this", MS + 1000,
            link_url="https://example.invalid/page",
            link_description="the thing I meant"),
        row("c", "ai", "SpeakerFifteen", "I checked", MS + 2000,
            internet_response="the tide was out all week"),
        row("d", "user", "Priya", "", MS + 3000,
            image_description="a drawing of a boat"),
    ])
    messages, _l, _f = parse_history(path, "SpeakerFifteen")
    joined = "\n".join(m["content"] for m in messages)
    check("the image description survived",
          "a kettle on a windowsill" in joined, joined)
    check("so did its url", "kettle.png" in joined, joined)
    check("the link survived", "the thing I meant" in joined, joined)
    check("so did the web lookup", "the tide was out all week" in joined, joined)
    check("and a message that was ONLY an image is still a message",
          len(messages) == 4, str(len(messages)))


def test_a_truly_empty_message_is_skipped_rather_than_imported_blank():
    path = write([row("a", "ai", "SpeakerFifteen", "", MS),
                  row("b", "ai", "SpeakerFifteen", "   ", MS + 1000),
                  row("c", "ai", "SpeakerFifteen", "real", MS + 2000)])
    messages, _l, _f = parse_history(path, "SpeakerFifteen")
    check("only the real one came through", len(messages) == 1,
          str(messages))


def test_a_truncated_last_line_costs_that_line_only():
    """The usual result of a copy interrupted mid-write."""
    path = write([row("a", "ai", "SpeakerFifteen", "first", MS),
                  row("b", "ai", "SpeakerFifteen", "second", MS + 1000)])
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"id": "c", "sender": "ai", "message": "cut off')
    messages, _l, _f = parse_history(path, "SpeakerFifteen")
    check("the two good lines survive", len(messages) == 2, str(messages))


def test_a_broken_timestamp_does_not_lose_the_message():
    path = write([row("a", "ai", "SpeakerFifteen", "no stamp", "yesterday"),
                  row("b", "ai", "SpeakerFifteen", "fine", MS)])
    raw = open(path, encoding="utf-8").read()
    # Detection needs a numeric timestamp on most rows, so a file that is
    # half broken is still recognised — but the broken row must not vanish.
    check("still recognised", kindroid_api.detect(raw) is True)
    messages, _l, _f = kindroid_api.parse(path, "SpeakerFifteen")
    check("both messages are there", len(messages) == 2, str(messages))
    check("the unusable stamp is simply absent",
          "ts" not in messages[0] or "ts" not in messages[1],
          str(messages))


def test_out_of_order_rows_are_put_back_in_order():
    """A hand-merged or concatenated file reads as the kin losing its mind."""
    path = write([row("b", "ai", "SpeakerFifteen", "second", MS + 5000),
                  row("a", "ai", "SpeakerFifteen", "first", MS)])
    messages, _l, _f = parse_history(path, "SpeakerFifteen")
    check("oldest first",
          [m["content"] for m in messages] == ["first", "second"],
          str([m["content"] for m in messages]))


def test_the_documented_field_name_works_too():
    """Their docs say sender_type; the archive carries sender. Both are real."""
    path = write([{"id": "a", "sender_type": "ai", "display_name": "SpeakerFifteen",
                   "timestamp": MS, "message": "documented shape"}])
    messages, _l, _f = parse_history(path, "SpeakerFifteen")
    check("read as the kin's own turn",
          messages and messages[0]["role"] == "assistant", str(messages))


def test_an_unknown_speaker_word_is_not_taken_as_the_kin():
    """Mislabelling somebody else's words as the kin's own is the one error
    that cannot be spotted by reading it back."""
    path = write([{"id": "a", "sender": "moderator", "display_name": "Mod",
                   "timestamp": MS, "message": "settle down"}])
    messages, _l, _f = parse_history(path, "SpeakerFifteen")
    check("treated as another speaker", messages[0]["role"] == "user",
          str(messages))
    check("and named", messages[0].get("sender_attribution") == "Mod")


def test_every_turn_says_where_it_came_from():
    path = write([row("a", "ai", "SpeakerFifteen", "morning", MS)])
    messages, _l, _f = parse_history(path, "SpeakerFifteen")
    check("stamped as an import",
          messages[0]["source"] == "import:kindroid", str(messages[0]))


for fn in list(globals().values()):
    if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
        print("\n--- %s" % fn.__name__)
        fn()

if _fails:
    print("\nFAILED %d: %s" % (len(_fails), ", ".join(_fails)))
    sys.exit(1)
print("\ntest_kindroid_api_import: all checks passed")
sys.exit(0)
