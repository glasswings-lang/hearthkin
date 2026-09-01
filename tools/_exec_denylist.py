# SPDX-License-Identifier: CC0-1.0

"""Denylist patterns for the exec tool's harness-side approval.

Each entry is `(compiled regex, human-readable description)`. The regex
is matched against the entire command string. The description is shown
to the user in the approval dialog so they know why the pattern fired
(useful both for "this is genuinely dangerous, deny" decisions and for
"this pattern is too broad, the regex needs tightening" debugging).

Growth policy: patterns added only after a concrete near-miss or actual
misuse. No speculative additions. The list ships small and grows from
real incidents. Overly broad patterns reintroduce the hedging problem
through a different vector — this list is a safety net, not a cage.

Each pattern names ONE concrete destructive shape. The deliberate
non-catches matter as much as the catches: `rm -rf temp/` is NOT caught
here, because that's legitimate cleanup work and gating it would train
the operator to wave every prompt through."""

import re


# Building blocks for the order-independent flag matching below. Each
# pattern still names ONE destructive shape; the lookaheads just stop a
# flag reorder (`-Force -Recurse`), a PowerShell alias (`rm`, `ri`,
# `rd`), an unambiguous parameter abbreviation (`-r`, `-fo`), or a
# trailing token from slipping the exact shape past the rule. Targets
# stay anchored to bare roots so legitimate cleanup (`rm -rf temp/`,
# `del /f /s /q C:\Temp\x`) keeps passing.

# PowerShell -Recurse / -Force, full form or any unambiguous prefix
# (longest alternatives first so `-recurse` doesn't stop at `-rec`).
_PS_RECURSE = r"-r(?:ecurse|ecurs|ecur|ecu|ec|e)?"
_PS_FORCE = r"-f(?:orce|orc|or|o)?"

# Each entry: (compiled regex, human-readable description).
_PATTERNS = [
    (
        # rm with recursive+force in any spelling/order (-rf, -fr,
        # -r -f, --recursive --force) targeting / or /* specifically.
        re.compile(
            r"^\s*rm\b"
            r"(?=(?:.*\s)?(?:--recursive|-[a-z]*r[a-z]*)(?=\s|$))"
            r"(?=(?:.*\s)?(?:--force|-[a-z]*f[a-z]*)(?=\s|$))"
            r".*\s/\*?(?:\s|$)",
            re.IGNORECASE,
        ),
        "rm -rf / (Unix wipe-everything, incl. /* and split/reordered flags)",
    ),
    (
        # Remove-Item (or its aliases rm/ri/rd/rmdir) with -Recurse and
        # -Force in any order — full names or unambiguous PowerShell
        # prefixes — targeting a bare drive root (C:\ / C:/), quoted or
        # not, with trailing args allowed.
        re.compile(
            r"^\s*(?:remove-item|rmdir|rm|ri|rd)\b"
            rf"(?=(?:.*\s)?{_PS_RECURSE}(?=\s|$))"
            rf"(?=(?:.*\s)?{_PS_FORCE}(?=\s|$))"
            r".*\s[\"']?[a-z]:[\\/][\"']?(?:\s|$)",
            re.IGNORECASE,
        ),
        "Remove-Item -Recurse -Force <drive>:\\ (Windows wipe-everything, "
        "incl. aliases, abbreviated flags, and reordered flags)",
    ),
    (
        # `format <drive>:` with or without trailing switches — the
        # auto-confirm form `format C: /y /q` is the dangerous one.
        # `\s+` keeps `format-table` etc. from matching.
        re.compile(r"^\s*format\s+[a-z]:(?=\s|$)", re.IGNORECASE),
        "format <drive>: (Windows drive format)",
    ),
    (
        # del/erase with /f /s /q in any order targeting a bare drive
        # root ONLY — `del /f /s /q C:\Temp\x` deliberately passes
        # (v0.2.23 behavior preserved).
        re.compile(
            r"^\s*(?:del|erase)\b"
            r"(?=(?:.*\s)?/f(?=\s|$))"
            r"(?=(?:.*\s)?/s(?=\s|$))"
            r"(?=(?:.*\s)?/q(?=\s|$))"
            r".*\s[\"']?[a-z]:\\?[\"']?\s*$",
            re.IGNORECASE,
        ),
        "del /f /s /q <drive>:\\ (Windows wipe-everything via del/erase, "
        "any flag order)",
    ),
    (
        re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;\s*:"),
        "fork bomb :(){:|:&};:",
    ),
    (
        re.compile(r"^\s*git\s+reset\s+--hard\s+origin/", re.IGNORECASE),
        "git reset --hard origin/<branch> (clobber local work with remote)",
    ),
    (
        # Force-push to the default branch: --force, -f, or
        # --force-with-lease, flag anywhere in the command, trailing
        # tokens allowed. Other branches deliberately pass.
        re.compile(
            r"^\s*git\s+push"
            r"(?=.*\s(?:--force(?:-with-lease)?|-f)(?=\s|$))"
            r".*\sorigin\s+(?:main|master)(?:\s|$)",
            re.IGNORECASE,
        ),
        "git push --force origin main/master (force-push to default branch)",
    ),
]


def _split_command_segments(command):
    """Split a shell command on unquoted separators (`;`, `&&`, `||`, `|`,
    `&`, newlines) so each pipeline/chain stage can be tested independently.

    The denylist patterns are start-anchored (`^\\s*rm ...`), which the
    2026-07 security audit (C1) showed is trivially evaded by a prefix or
    chain — `echo x; rm -rf /`, `cd /tmp && rm -rf /` — because the anchor
    only ever sees the FIRST verb. Testing each segment restores the intent
    without broadening the patterns themselves. Quoted regions are not
    split, to limit false positives (a destructive string inside `echo "..."`
    is data, not a command) — though the whole command is still tested too,
    so a genuinely dangerous single-token shape is never missed."""
    segments = []
    buf = []
    i, n = 0, len(command)
    quote = None
    while i < n:
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if command[i:i + 2] in ("&&", "||"):
            segments.append("".join(buf)); buf = []; i += 2; continue
        if ch in (";", "|", "&", "\n", "\r"):
            segments.append("".join(buf)); buf = []; i += 1; continue
        buf.append(ch)
        i += 1
    segments.append("".join(buf))
    return [s.strip() for s in segments if s.strip()]


def match_denylist(command):
    """Check if `command` matches any denylist pattern. Returns the
    human-readable description of the matched pattern (so the approval
    dialog can show *which* rule fired), or None if no match.

    Tests the whole command AND each shell-separator-delimited segment, so a
    destructive shape hidden behind a prefix or a `;`/`&&`/`|` chain is
    caught despite the start-anchored patterns (audit C1). The whole-command
    test still runs first so multi-separator single shapes (the fork bomb,
    which itself contains `|`, `&`, `;`) are matched intact."""
    if not command:
        return None
    candidates = [command] + _split_command_segments(command)
    for regex, description in _PATTERNS:
        for cand in candidates:
            if regex.search(cand):
                return description
    return None
