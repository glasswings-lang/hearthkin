# SPDX-License-Identifier: CC0-1.0
"""Guard test: no private string may reappear in a tracked file.

A privacy scrub rewrote every commit in this repo to remove real names,
handles, machine identifiers and quoted private conversation. That pass
verified zero survivors across the whole history — and then ordinary work put
some of them straight back. Three days after the scrub, the operator's handle
was live again in `kin_persistence.py`, `tests/test_app_prompts.py` and
`tests/test_park_sharing.py`, one of them inside a model-facing prompt string.
Nobody noticed, because nothing was watching.

A scrub is a one-off. This is the thing that keeps it true. Reintroduce a
scrubbed string and the suite goes red before it can be pushed.

## The list is deliberately not in this repo

The forbidden strings are read from `docs/private/forbidden-strings.txt`,
which is gitignored — because a list of the exact things that must never be
published is itself the most dangerous file you could publish. Same reasoning
that moved the private docs into a directory-level ignore rather than naming
each one in `.gitignore`.

The check binds where `docs/private/` exists — the machine that keeps private
docs is the machine that can leak. On such a machine a missing or empty list is
a FAILURE, not a skip. A guard that quietly passes while disarmed is worse than
no guard, because it produces confidence.

That is not hypothetical. This check sat green and unarmed in the public working
copy while the list existed only in the private archive — so every commit made
there would have been waved through by a test reporting ALL PASS.

A checkout with no `docs/private/` at all is a fork or CI. There the check prints
a loud DISARMED banner and passes, so a contributor who doesn't have the list
isn't blocked and CI doesn't fail on a file it can't see.

A committed, safe-to-publish template lives at `docs/forbidden-strings.example.txt`
— it explains the format and how to turn the check on. Copy it to
`docs/private/forbidden-strings.txt` and edit; adding a name later is just
editing that text file, no code change. A fork discovers the whole mechanism
from the template without ever seeing anyone's real list.

## Format of the list

One string per line. Blank lines and `#` comments ignored. Matching is
case-insensitive and substring-based, so `Lastname` catches `lastname` and
`LastnameTheKin`.

Prefix a line with `=` for a whole-word match instead, when a fragment would
collide with ordinary English — e.g. `=ash` matches the name `ash` but not
`crash` or `hashed`. That collision is real: the scrub found one name that
appears inside the word `filename`, and a blanket replace would have written
`fiTaliame` across 27 commit messages.

## Failure output names FILES ONLY

It never prints the matched string or the surrounding line. A CI log, a
terminal scrollback, or a screenshot of a failure would otherwise republish
exactly what the scrub removed.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIVATE_DIR = os.path.join(REPO, "docs", "private")
LIST_PATH = os.path.join(PRIVATE_DIR, "forbidden-strings.txt")

# Reading every tracked file is the point, but a few can't hold prose and are
# large enough to slow the suite noticeably.
SKIP_SUFFIXES = (".ico", ".png", ".jpg", ".gif", ".pdf", ".zip", ".exe")

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def load_terms():
    """Read the forbidden list. Returns (substrings, whole_words)."""
    subs, words = [], []
    with open(LIST_PATH, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("="):
                term = line[1:].strip()
                if term:
                    words.append(term.lower())
            else:
                subs.append(line.lower())
    return subs, words


def tracked_files():
    """Every file git knows about, so untracked scratch is ignored."""
    out = subprocess.run(
        ["git", "-C", REPO, "ls-files"],
        capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines()
            if p and not p.lower().endswith(SKIP_SUFFIXES)]


def word_hit(haystack, term):
    """True if `term` appears as a whole word (letters/digits/_ on neither side)."""
    start = 0
    while True:
        i = haystack.find(term, start)
        if i < 0:
            return False
        before = haystack[i - 1] if i > 0 else " "
        after_i = i + len(term)
        after = haystack[after_i] if after_i < len(haystack) else " "
        if not (before.isalnum() or before == "_") and \
           not (after.isalnum() or after == "_"):
            return True
        start = i + 1


def check_message_file(path):
    """Check a proposed COMMIT MESSAGE, for the commit-msg hook.

    Commit messages were the reason the old history could not be published,
    and `git ls-files` cannot see them - they are not files in the tree. So
    the same list, the same matching, applied to the message git is about to
    record. One implementation, so the two checks can never drift apart.

    Prints no part of the message on failure, for the same reason the file
    check prints no matched text.
    """
    armed = os.path.isdir(PRIVATE_DIR)
    if not os.path.exists(LIST_PATH):
        if armed:
            print("GUARD DISARMED on a private checkout - list is missing.")
            return 1
        return 0

    subs, words = load_terms()
    if not subs and not words:
        print("GUARD DISARMED - forbidden-strings.txt is empty.")
        return 1

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            # Comment lines are stripped by git before recording, so a name
            # in the "# Please enter the commit message" preamble is not a
            # leak and must not block the commit.
            kept = [l for l in fh.read().splitlines()
                    if not l.lstrip().startswith("#")]
            body = chr(10).join(kept).lower()
    except OSError:
        print("could not read the commit message file; refusing to guess")
        return 1

    if any(sub in body for sub in subs) or any(word_hit(body, w) for w in words):
        print("Your commit message contains a forbidden string.")
        print("The message is not shown here on purpose - printing it would")
        print("republish exactly what the list exists to keep out.")
        print("Reword the message and commit again.")
        return 1
    return 0


def main():
    # "Armed" means this checkout keeps private docs, so it is a checkout that
    # can leak. Presence of the LIST is not the signal - that was the old rule,
    # and it made a missing list indistinguishable from an innocent fork.
    armed = os.path.isdir(PRIVATE_DIR)

    if not os.path.exists(LIST_PATH):
        if armed:
            check("forbidden-strings.txt present (docs/private/ exists here, so "
                  "this checkout keeps private docs)", False)
            print("")
            print("docs/private/ exists but the list is gone, so nothing is being")
            print("checked. Restore it, or delete docs/private/ entirely if this")
            print("checkout genuinely holds nothing private.")
            print("")
            print("FAILED 1: guard disarmed on a private checkout")
            return 1
        print("=" * 68)
        print("GUARD DISARMED - no docs/private/ in this checkout.")
        print("Nothing is checking for private strings in this run.")
        print("To arm it: copy docs/forbidden-strings.example.txt to")
        print("docs/private/forbidden-strings.txt and add your terms.")
        print("=" * 68)
        print("")
        print("ALL PASS (nothing checked)")
        return 0

    subs, words = load_terms()
    if not subs and not words:
        check("forbidden-strings.txt has at least one term", False)
        print("")
        print("The list is present but empty, so nothing would ever match.")
        print("")
        print("FAILED 1: guard disarmed by an empty list")
        return 1


    print("checking %d term(s) against tracked files" % (len(subs) + len(words)))

    offenders = set()
    for rel in tracked_files():
        path = os.path.join(REPO, rel)
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                body = fh.read().lower()
        except OSError:
            continue
        if any(s in body for s in subs) or any(word_hit(body, w) for w in words):
            offenders.add(rel)

    # Deliberately reports paths and a count, never the term or the line.
    check("no forbidden string in any tracked file", not offenders)
    if offenders:
        print("\n%d file(s) contain a forbidden string:" % len(offenders))
        for rel in sorted(offenders):
            print("    " + rel)
        print("\nThe matched text is not printed on purpose — printing it here "
              "would republish what the scrub removed.")
        print("Grep those files yourself against docs/private/forbidden-strings.txt.")

    if _fails:
        print(f"\nFAILED {len(_fails)}: " + ", ".join(_fails))
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    # `--check-message <path>` is the commit-msg hook's entry point.
    if len(sys.argv) > 2 and sys.argv[1] == "--check-message":
        sys.exit(check_message_file(sys.argv[2]))
    sys.exit(main())
