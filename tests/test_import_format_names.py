# SPDX-License-Identifier: CC0-1.0
"""Every format a parser can return has a name a person would recognise.

The dialog shows the detected format and writes a sentence into the marker
the kin reads afterwards. Both come from lookup tables, and a table is the
thing that gets forgotten when a parser is added — the raw internal string
then leaks into the window ("kindroid-api") and into the kin's own history.

This is the check that notices. It reads the parsers' own returned format
strings rather than a hand-kept list, so a new importer cannot be added
without either giving it a label or failing here.

No pytest. Plain checks, a summary line, exit 1 on any failure.
Run:  python tests/test_import_format_names.py
"""

import inspect
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importers                                            # noqa: E402
from dialogs.import_history import (_describe_source,        # noqa: E402
                                    _format_label)

_fails = []


def check(label, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + label
          + ((" — " + detail) if detail and not cond else ""))
    if not cond:
        _fails.append(label)


def formats_the_parsers_can_return():
    """Every literal a parser hands back as its `fmt`.

    Read out of the source rather than by calling them, because calling
    each one needs a fixture per format and this check should not depend on
    having fifteen of those.
    """
    found = set()
    for _detector, parser in importers._PARSERS:
        try:
            source = inspect.getsource(parser)
        except (OSError, TypeError):
            continue
        # `return canonical, "kindroid", "kindroid-api"` — the third item.
        for match in re.finditer(
                r'return\s+[^\n,]+,\s*["\']([^"\']+)["\']\s*,\s*'
                r'["\']([^"\']+)["\']', source):
            found.add(match.group(2))
    return found


def test_the_parsers_were_actually_read():
    """If the scrape finds nothing, this test is passing for free."""
    found = formats_the_parsers_can_return()
    check("format strings were found in the parsers", len(found) >= 4,
          str(sorted(found)))


def test_every_format_has_a_label_for_the_window():
    for fmt in sorted(formats_the_parsers_can_return()):
        label = _format_label(fmt)
        check("%s has a readable name" % fmt, label != fmt, label)
        check("%s's name is not the internal string" % fmt,
              "_" not in label and "-" not in label.split(" ")[0], label)


def test_every_format_describes_itself_for_the_kin():
    """The sentence in the marker is what the kin reads about its own past."""
    for fmt in sorted(formats_the_parsers_can_return()):
        said = _describe_source("history.jsonl", "SpeakerFifteen", fmt)
        check("%s describes itself" % fmt,
              isinstance(said, str) and len(said) > 8, repr(said))
        check("%s's description is a sentence fragment, not an id" % fmt,
              fmt not in said, repr(said))


def test_a_named_counterpart_is_used_when_there_is_one():
    said = _describe_source("history.jsonl", "SpeakerFifteen", "kindroid-api")
    check("the kin is named", "SpeakerFifteen" in said, said)
    without = _describe_source("history.jsonl", "", "kindroid-api")
    check("and without a name it falls back to the filename",
          "history.jsonl" in without, without)


def test_the_two_kindroid_formats_do_not_read_the_same():
    """A kin reading the marker later should be able to tell which it got:
    one has real timestamps in it and one has invented ones."""
    api = _describe_source("history.jsonl", "SpeakerFifteen", "kindroid-api")
    text = _describe_source("chat.txt", "SpeakerFifteen", "kindroid")
    check("they are described differently", api != text,
          "%r vs %r" % (api, text))
    check("the labels differ too",
          _format_label("kindroid-api") != _format_label("kindroid"))


def test_the_file_picker_offers_the_formats_people_have():
    """A file hidden by every filter but All Files might as well not import."""
    import dialogs.import_history as dialog
    source = inspect.getsource(dialog)
    for ext, why in ((r"\*\.jsonl", "API archives and OpenClaw sessions"),
                     (r"\*\.zip", "Claude's download"),
                     (r"\*\.txt", "Telegram and hand-authored logs"),
                     (r"\*\.md", "saved markdown conversations")):
        check("the picker offers %s (%s)" % (ext.replace("\\", ""), why),
              re.search(ext, source) is not None)
    check("and Kindroid is named in a filter, so it can be found",
          re.search(r"Kindroid[^|]*\*\.jsonl", source) is not None)


for fn in list(globals().values()):
    if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
        print("\n--- %s" % fn.__name__)
        fn()

if _fails:
    print("\nFAILED %d: %s" % (len(_fails), ", ".join(_fails)))
    sys.exit(1)
print("\ntest_import_format_names: all checks passed")
sys.exit(0)
