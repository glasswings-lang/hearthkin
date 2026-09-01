#!/usr/bin/env python
# SPDX-License-Identifier: CC0-1.0
"""Report capitalised words this project does not otherwise use.

THE GAP THIS FILLS

tests/test_no_private_strings.py checks a list, so it is silent about anything
not on the list. scripts/name_leaks.py derives names from the live profile --
kin folders, room folders, the git author -- so it is silent about anyone who
was never a kin. Both answer "is this a name I already know about?"

Neither would have caught a friend who is not a kin, has no folder anywhere,
and whom nobody thought to list. By the time you know to add such a name, it
is already in the history.

So this asks from the other side: not "is this a forbidden name?" but "is this
a word this project uses?" A capitalised word that appears nowhere else in the
vocabulary is worth a human glance.

HOW THE NOISE IS REMOVED, WITHOUT A DICTIONARY

Two filters, because the dominant noise is grammar rather than vocabulary.

1. A word that also appears LOWERCASE somewhere in the corpus is ordinary
   English. "Accept" is fine because "accept" occurs; a name almost never
   occurs lowercase.
2. A word must appear MID-sentence. Sentence-initial capitals are grammar, and
   a person is nearly always named mid-sentence ("in a group with X") while
   "Across" or "Applying" is usually opening one.

Cost of filter 1: a name that is also a common word -- a kin named after
weather, or after a park creature -- is suppressed, because the lowercase form
occurs. Deliberate, and covered from the other side -- name_leaks.py
derives those from the live kin and room folders. This half is aimed at people
with no folder anywhere, whose names are usually distinctive. The two together
are the coverage; neither is sufficient alone.

WHERE IT LOOKS

Prose only: comments and docstrings in .py, body text in .md, and commit
messages. Not identifiers -- people's names turn up in sentences, and scanning
code too would bury the signal under class names.

THE ALLOWLIST IS SAFE TO COMMIT

docs/known-words.txt is the PERMITTED set, not the forbidden one. Adding to it
is a deliberate "yes, this word is fine in public", and publishing it reveals
nothing -- the opposite of docs/private/forbidden-strings.txt, which is
gitignored precisely because a list of what must never be published is the
worst possible thing to publish.

OUTPUT IS A LEAD, NOT A VERDICT

Deciding which candidates are people is yours. A clean run means "no
unrecognised capitalised word", never "this reads fine to a stranger" --
context is not a string, and nothing here will see a comment that only makes
sense if you were there.

Usage:
    python scripts/unknown_names.py              # everything
    python scripts/unknown_names.py --messages   # commit messages only
    python scripts/unknown_names.py --files      # tracked prose only
"""

import os
import re
import subprocess
import sys
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOW_PATH = os.path.join(REPO, "docs", "known-words.txt")

# Capitalised, 3+ letters. Not ALLCAPS (acronyms, constants) and not initials.
WORD = re.compile(r"\b([A-Z][a-z]{2,})\b")
LOWER = re.compile(r"\b([a-z]{3,})\b")

# Characters after which a capital is grammar rather than a name.
SENTENCE_END = ".!?:;—-*>|/\\"


def run(args):
    return subprocess.run(
        ["git", "-C", REPO] + args, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout


def load_allow():
    allow = set()
    if os.path.exists(ALLOW_PATH):
        with open(ALLOW_PATH, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if line and not line.startswith("#"):
                    allow.add(line.lower())
    return allow


def prose_from_python(text):
    """Comments and docstrings only -- not identifiers."""
    out = []
    for line in text.splitlines():
        hit = line.find("#")
        if hit >= 0:
            out.append(line[hit + 1:])
    for block in re.findall(r'"""(.*?)"""', text, re.S):
        out.append(block)
    return "\n".join(out)


def prose_from_markdown(text):
    """Drop fenced and inline code; keep the sentences."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`]*`", " ", text)
    return text


def tracked_prose():
    for path in run(["ls-files"]).splitlines():
        if not path.endswith((".py", ".md")):
            continue
        full = os.path.join(REPO, path)
        if not os.path.exists(full):
            continue
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        if path.endswith(".py"):
            yield path, prose_from_python(text)
        else:
            yield path, prose_from_markdown(text)


def commit_messages():
    # A printable token, not a NUL: Windows cannot pass an embedded null in a
    # command-line argument, and git's format string is an argument.
    sep = "<<<HKSPLIT>>>"
    raw = run(["log", "--all", "--format=%h %s" + sep + "%B" + sep])
    for chunk in raw.split(sep):
        chunk = chunk.strip()
        if not chunk:
            continue
        head = chunk.splitlines()[0] if chunk.splitlines() else ""
        yield "commit " + head[:40], chunk


def mid_sentence(text, start):
    """True if the capital at `start` is not opening a sentence.

    A line START is normally grammar, so it is skipped -- EXCEPT on lines that
    look like data rather than prose. That exception is load-bearing: the worst
    leak this tool exists to find was a name->placeholder mapping laid out as
    an indented table, one name per column. Every one of those names sat at a
    line start or after a run of spaces, so a plain sentence filter hid exactly
    the thing most worth finding.

    "Looks like data" = the line is indented, or carries a mapping or column
    separator. Those lines are cheap to keep and are where lists of names live.
    """
    line_start = text.rfind("\n", 0, start) + 1
    line = text[line_start:text.find("\n", start) if text.find("\n", start) != -1 else len(text)]
    looks_like_data = (
        line[:1] in (" ", "\t")
        or "->" in line or "=>" in line or "|" in line
    )
    if looks_like_data:
        return True

    before = text[:start].rstrip(" \t")
    if not before:
        return False
    tail = before[-1]
    if tail in "\r\n":
        return False
    return tail not in SENTENCE_END


def collect(sources, allow):
    seen_lower = set()
    for _, text in sources:
        for m in LOWER.finditer(text):
            seen_lower.add(m.group(1))

    hits = defaultdict(set)
    for label, text in sources:
        for m in WORD.finditer(text):
            word = m.group(1)
            low = word.lower()
            if low in allow or low in seen_lower:
                continue
            if not mid_sentence(text, m.start()):
                continue
            hits[word].add(label)
    return hits


def main():
    args = set(sys.argv[1:])
    allow = load_allow()

    if "--messages" in args:
        sources = list(commit_messages())
    elif "--files" in args:
        sources = list(tracked_prose())
    else:
        sources = list(tracked_prose()) + list(commit_messages())

    hits = collect(sources, allow)
    if not hits:
        print("No unrecognised capitalised words.")
        return 0

    # Rarest first: a word used once is a likelier leak than one used 200
    # times, which is almost certainly vocabulary the allowlist is missing.
    ordered = sorted(hits.items(), key=lambda kv: (len(kv[1]), kv[0].lower()))
    print("%d unrecognised capitalised words, rarest first:\n" % len(ordered))
    for word, where in ordered:
        sample = sorted(where)[:2]
        more = "" if len(where) <= 2 else "  (+%d more)" % (len(where) - 2)
        print("  %-18s %3d  %s%s" % (word, len(where), "; ".join(sample), more))
    print("\nThis output names candidates. Do not paste it anywhere public.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
