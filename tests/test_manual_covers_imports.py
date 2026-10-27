# SPDX-License-Identifier: CC0-1.0
"""The kin manual has to name every history format the harness can read.

Not for completeness' sake. A kin gets asked "can you remember our old
conversations?" and the useful answer is "yes, if you still have the export
— which app was it?". It can only say that if it knows what the harness
accepts, and the manual is the only place it can find out.

So an importer added without a line in the manual leaves every kin unable
to direct the person in front of it. This is the check that notices. It
reads the format strings out of the parsers themselves rather than keeping
its own list, so the list cannot quietly fall behind.

No pytest. Plain checks, a summary line, exit 1 on any failure.
Run:  python tests/test_manual_covers_imports.py
"""

import inspect
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importers                                            # noqa: E402

_fails = []

# What a kin would have to say out loud for each format, mapped to the
# internal name the parser returns. The manual is prose, so the check is
# that the format is *findable* by someone reading it — not that an id
# string appears.
SPOKEN = {
    "kindroid-api": ("kindroid", "api"),
    "kindroid-archive-md": ("kindroid", "readable copy"),
    "openclaw": ("openclaw", "folder"),
    "kindroid": ("kindroid", "text export"),
    "claude_json": ("claude", ".zip"),
    "claude_markdown": ("claude", "markdown"),
    "claude_code": ("claude code", ".jsonl"),
    "skype_json": ("skype",),
    "skype_txt": ("skype",),
    "telegram": ("telegram",),
    "hand_authored": ("hand-authored",),
}


def check(label, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + label
          + ((" — " + detail) if detail and not cond else ""))
    if not cond:
        _fails.append(label)


def manual_text():
    here = pathlib.Path(__file__).resolve().parent.parent
    return (here / "docs" / "kin_manual.md").read_text(
        encoding="utf-8", errors="replace")


def formats_the_parsers_can_return():
    """Every literal a dispatcher parser hands back as its `fmt`."""
    found = set()
    for _detector, parser in importers._PARSERS:
        try:
            source = inspect.getsource(parser)
        except (OSError, TypeError):
            continue
        for match in re.finditer(
                r'return\s+[^\n,]+,\s*["\']([^"\']+)["\']\s*,\s*'
                r'["\']([^"\']+)["\']', source):
            found.add(match.group(2))
    return found


def test_the_scrape_actually_found_parsers():
    """If this finds nothing, every check below passes for free."""
    found = formats_the_parsers_can_return()
    check("format strings were found", len(found) >= 4, str(sorted(found)))


def test_the_manual_has_a_section_about_being_given_a_past():
    text = manual_text().lower()
    check("there is an import section",
          "carrying a history in" in text or "imported" in text)
    check("and it tells the kin the turns are not its memory yet",
          "not your memory" in text or "nothing lands in `memory.md`" in text)


def test_every_readable_format_is_findable_in_the_manual():
    text = manual_text().lower()
    for fmt in sorted(formats_the_parsers_can_return()):
        words = SPOKEN.get(fmt)
        check("%s is described in words a person would use" % fmt,
              words is not None,
              "no spoken description mapped — add one to SPOKEN")
        if not words:
            continue
        missing = [w for w in words if w.lower() not in text]
        check("%s: the manual mentions %s" % (fmt, ", ".join(words)),
              not missing, "missing: %s" % missing)


def test_the_lossy_kindroid_export_is_marked_as_lossy():
    """Three Kindroid formats exist and they are not equally good. A kin
    that recommends the text export when an API archive is available has
    cost the person their timestamps and their speakers."""
    text = manual_text().lower()
    check("the manual says the text export loses things",
          "lossiest" in text or "lossy" in text, "")
    check("and says which to prefer",
          "prefer it" in text or "prefer the api" in text, "")


def test_the_manual_does_not_promise_formats_that_do_not_exist():
    """The opposite failure: a kin confidently telling someone to export
    from something the harness cannot read."""
    text = manual_text().lower()
    for never in ("whatsapp", "discord export", "signal export",
                  "messenger export", "instagram"):
        check("the manual does not promise %s" % never, never not in text)


for fn in list(globals().values()):
    if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
        print("\n--- %s" % fn.__name__)
        fn()

if _fails:
    print("\nFAILED %d: %s" % (len(_fails), ", ".join(_fails)))
    sys.exit(1)
print("\ntest_manual_covers_imports: all checks passed")
sys.exit(0)
