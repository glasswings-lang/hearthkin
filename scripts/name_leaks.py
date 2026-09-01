#!/usr/bin/env python
# SPDX-License-Identifier: CC0-1.0
"""Find real names in tracked files and commit messages, BEFORE they need scrubbing.

The scrub pipeline (git-filter-repo --replace-text) is good at what it does:
it rewrites a known string everywhere it appears, in every blob and every
commit message, throughout history. What it cannot do is notice a name nobody
has told it about. tests/test_no_private_strings.py has the same shape -- it
checks a list, so it is silent about anything not on the list, which is most of
what you would actually be tempted to write.

That gap is discovery, not scrubbing. This closes it from the other end: it
takes the names that exist in the LIVE profile -- the kin folders, the room
folders, the git author -- and looks for them in what is actually tracked.

The names are read at runtime and never stored here, so this file is safe to
publish while the names are not. Nothing is written and nothing is rewritten:
it reports, and optionally prints replacement rules you can paste into a scrub
expression file. Deciding what is a leak and what is a legitimate mention is a
judgement, and it stays yours.

WHAT IT CANNOT DO. A name is a string, so a string tool can find it. Insider
CONTEXT is not a string -- a test fixture built out of someone's real notes, a
comment that says "the logged bug", a commit message that only makes sense if
you were there. Nothing here will see any of that, and no substitution would
fix it if it did; those passages have to be rewritten by hand. Treat a clean
run as "no known name appeared", never as "this reads fine to a stranger".

Usage:
    python scripts/name_leaks.py                 # report
    python scripts/name_leaks.py --samples 5     # more context per name
    python scripts/name_leaks.py --emit-rules    # scrub lines to paste
    python scripts/name_leaks.py --extra FILE    # one more name per line
    python scripts/name_leaks.py --no-messages   # skip commit messages
"""

import argparse
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _run(args):
    try:
        out = subprocess.run(args, capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return out.stdout or ""


def _live_names(extra_path=None):
    """Names from the live profile, plus the git author. Never hard-coded --
    a name written into this file would be the exact thing it is looking for."""
    names = set()
    try:
        from hearthkin_paths import config_dir
        for sub in ("kin", "rooms"):
            d = config_dir() / sub
            if d.exists():
                for p in d.iterdir():
                    if p.is_dir():
                        names.add(p.name)
    except Exception as e:
        print(f"[warn] could not read the profile ({e}); "
              f"falling back to the git author only", file=sys.stderr)
    author = _run(["git", "config", "user.name"]).strip()
    if author:
        names.add(author)
        for part in author.split():
            if len(part) > 2:
                names.add(part)
    if extra_path:
        try:
            with open(extra_path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        names.add(line)
        except Exception as e:
            print(f"[warn] could not read {extra_path}: {e}", file=sys.stderr)
    # A one- or two-character "name" matches everything and tells you nothing.
    return sorted(n for n in names if len(n) > 2)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=int, default=2,
                    help="lines of context to show per name (default 2)")
    ap.add_argument("--emit-rules", action="store_true",
                    help="print name==>replacement lines for a scrub file")
    ap.add_argument("--extra", help="file of additional names, one per line")
    ap.add_argument("--no-messages", action="store_true",
                    help="skip commit messages, scan tracked files only")
    args = ap.parse_args()

    names = _live_names(args.extra)
    if not names:
        print("No names found to search for. Nothing to do.")
        return 0
    print(f"Searching for {len(names)} name(s) from the live profile "
          f"+ git author.\n")

    tracked = [p for p in _run(["git", "ls-files"]).splitlines() if p.strip()]
    messages = "" if args.no_messages else _run(
        ["git", "log", "--format=%H%n%s%n%b"])

    findings = []
    for name in names:
        pat = re.compile(r"\b" + re.escape(name) + r"\b")
        hits = []
        for path in tracked:
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if pat.search(line):
                            hits.append((path, i, line.strip()))
            except (OSError, IsADirectoryError):
                continue
        msg_hits = len(pat.findall(messages)) if messages else 0
        if hits or msg_hits:
            findings.append((name, hits, msg_hits))

    findings.sort(key=lambda f: len(f[1]) + f[2], reverse=True)
    if not findings:
        print("No known name appears in tracked files or commit messages.")
        print("That is not the same as reading cleanly to a stranger. See the "
              "note at the top of this file.")
        return 0

    for name, hits, msg_hits in findings:
        files = sorted({h[0] for h in hits})
        print(f"* {name}: {len(hits)} line(s) in {len(files)} file(s), "
              f"{msg_hits} commit-message hit(s)")
        # A name that is also an ordinary word will be mostly false positives,
        # and blanket-substituting it would wreck the prose. Compare the
        # capitalised form against the lowercase one IN THE ORIGINAL TEXT. An
        # earlier version lowercased every line first and then looked for the
        # lowercase name, which matched every time and so warned about every
        # name equally -- which is the same as warning about none of them.
        if name[:1].isupper():
            lower_pat = re.compile(r"\b" + re.escape(name.lower()) + r"\b")
            lower_hits = sum(1 for h in hits if lower_pat.search(h[2]))
            if lower_hits:
                print(f"   note: the lowercase form is on {lower_hits} of "
                      f"these lines, so it is probably an ordinary word too "
                      f"- check before adding a blanket rule")
        for path, lineno, text in hits[:args.samples]:
            print(f"   {path}:{lineno}: {text[:110]}")
        if len(hits) > args.samples:
            print(f"   ... and {len(hits) - args.samples} more")
        print()

    if args.emit_rules:
        print("\n# Paste into a scrub expression file, EDITING each "
              "replacement first.")
        print("# Blanket-replacing a name that is also an ordinary word will "
              "damage prose;")
        print("# these are a starting point, not a list to apply unread.")
        for name, _hits, _m in findings:
            print(f"{name}==>a kin")

    print(f"\n{len(findings)} name(s) appear somewhere tracked. "
          f"None of this is automatically a leak - read the lines.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
