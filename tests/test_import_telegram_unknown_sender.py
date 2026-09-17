# SPDX-License-Identifier: CC0-1.0
"""Guard test: a Telegram message from a deleted account stays its own message.

A Telegram .txt export writes a message from an account that has since been
deleted with an EMPTY name, the time and then a colon:

    [01-02-2024 10:01:00] : Thanks for the parcel.

The line prefix used to require at least one character of name, so that line
didn't count as a new message. It was treated as continuation text and glued,
timestamp and all, onto the end of whoever spoke before it. Three messages went
in, two came out, and the deleted account's words were credited to someone else.

Now an empty name is a message from importers.text_log.UNKNOWN_SENDER. The
export carries no name or id for these, so nothing is guessed.

Run: python tests/test_import_telegram_unknown_sender.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault(
    "HEARTHKIN_HOME",
    tempfile.mkdtemp(prefix="hearthkin-unknown-sender-"))

from importers import text_log  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def parse_text(text, kin="SpeakerTwo"):
    with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text)
        path = f.name
    try:
        return text_log.parse(path, kin)
    finally:
        os.unlink(path)


# --- a deleted account's message between two named speakers ---------------
msgs, label, fmt = parse_text(
    "[01-02-2024 10:00:00] SpeakerOne: first message\n"
    "[01-02-2024 10:01:00] : sent by an account that was later deleted\n"
    "[01-02-2024 10:02:00] SpeakerTwo: reply\n"
)
check("the file is still read as a Telegram export", fmt == "telegram")
check("three messages in, three messages out", len(msgs) == 3)
if len(msgs) == 3:
    before, unknown, after = msgs
    check("the earlier message keeps only its own text",
          before["content"] == "first message")
    check("the empty-name line is filed under Unknown sender",
          unknown["speaker"] == text_log.UNKNOWN_SENDER == "Unknown sender")
    check("its text is only its own words",
          unknown["content"] == "sent by an account that was later deleted")
    check("it keeps its own time",
          unknown["ts"] == "2024-01-02T10:01:00")
    check("it goes to the user slot, never the kin's",
          unknown["role"] == "user")
    check("it carries the same attribution a named speaker would",
          unknown.get("sender_attribution") == text_log.UNKNOWN_SENDER)
    check("the kin's own reply is still the kin's",
          after["role"] == "assistant" and after["content"] == "reply")

# --- an export that OPENS with a deleted account's message -----------------
msgs, label, fmt = parse_text(
    "[03-04-2024 09:00:00] : opening line from a deleted account\n"
    "[03-04-2024 09:05:00] SpeakerTwo: answer\n"
)
check("an export starting with an empty name is still detected as Telegram",
      fmt == "telegram")
check("both of its messages come through", len(msgs) == 2)

# --- continuation lines and colons inside the message ----------------------
msgs, label, fmt = parse_text(
    "[05-06-2024 12:00:00] : note: this has a colon in it\n"
    "and a second line\n"
    "[05-06-2024 12:01:00] SpeakerOne: next\n"
)
check("a colon inside the text doesn't cut the message short",
      len(msgs) == 2
      and msgs[0]["content"] == "note: this has a colon in it\nand a second line")
check("a following named speaker is unaffected",
      len(msgs) == 2 and msgs[1]["speaker"] == "SpeakerOne")

print()
if _fails:
    print(f"{len(_fails)} check(s) failed.")
    sys.exit(1)
print("All checks passed.")
