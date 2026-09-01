"""Authoring-bridge extraction/commit tests. Plain Python; run via tests/run_all.py."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from authoring_bridge import (
    extract_authoring_writes,
    looks_like_write_gesture,
    commit_authoring_writes,
)

_failures = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _failures.append(label)


def writes(reply):
    return [(w.path, w.content, w.form) for w in extract_authoring_writes(reply)]


# --- Named fence (the simple form): filename on the fence, no keyword --------
check(
    writes("here you go\n```owl.json\n{\"a\": 1}\n```")
    == [("owl.json", '{"a": 1}', "named-fence")],
    "filename-labelled fence -> committable write (no keyword needed)",
)
check(
    writes("```notes/day.md\n# today\n```")
    == [("notes/day.md", "# today", "named-fence")],
    "filename fence with a path -> write",
)

# Regression: a model tags the fence with BOTH a language and a filename
# (```markdown:memory/foo.md```). The ':' is invalid in a Windows path, so the
# whole thing used to be taken as the name and the write died with a WinError,
# silently losing the file (Bracken hit this). The language tag is now stripped.
check(
    writes("```markdown:memory/somatic.md\n# body\n```")
    == [("memory/somatic.md", "# body", "named-fence")],
    "language-tagged fence ('markdown:path.md') -> tag stripped, path kept",
)
check(
    writes("```md:notes/day.md\nhi\n```")
    == [("notes/day.md", "hi", "named-fence")],
    "short language tag ('md:') stripped too",
)

# --- Form 1: labelled fence (write:/save:) still works -----------------------
check(
    writes("here you go\n```write:owl.json\n{\"a\": 1}\n```")
    == [("owl.json", '{"a": 1}', "label")],
    "labelled fence -> committable write",
)
check(
    writes("```save: notes/day.md\nhello\nworld\n```")
    == [("notes/day.md", "hello\nworld", "label")],
    "save: label with whitespace + path + multiline body",
)

# --- Form 2: emote + following plain fence ----------------------------------
check(
    writes("*writes owl.json*\n```\n{\"b\": 2}\n```")
    == [("owl.json", '{"b": 2}', "emote")],
    "write-emote + plain fence -> committable write",
)
check(
    writes("*saves it as species.md* okay!\n```\n# Owl\n```")
    == [("species.md", "# Owl", "emote")],
    "emote naming file, prose after, then fence",
)

# --- Safety: a plain code fence must NOT be treated as a write --------------
check(
    writes("here's an example:\n```json\n{\"demo\": true}\n```") == [],
    "plain ```json fence is NOT a write (no accidental disk writes)",
)
check(
    writes("*flaps happily* eeee here we go!!") == [],
    "content-less gesture extracts nothing (Vesper's actual failure shape)",
)
check(writes("just talking, no fences") == [], "plain prose -> nothing")
check(writes("") == [], "empty reply -> []")

# --- Order + multiple -------------------------------------------------------
check(
    [w.path for w in extract_authoring_writes(
        "```write:a.txt\nA\n```\nand\n*writes b.txt*\n```\nB\n```")]
    == ["a.txt", "b.txt"],
    "two writes preserved in document order",
)

# An emote does not steal a fence that a later label owns.
check(
    [(w.path, w.form) for w in extract_authoring_writes(
        "*writes x.txt*\n```write:y.txt\nY\n```")]
    == [("y.txt", "label")],
    "labelled fence claimed by label, not by preceding emote",
)

# --- Gesture detection (for the teach-nudge) -------------------------------
check(looks_like_write_gesture("*writes owl.json* here we go!!") == "owl.json",
      "gesture: write-emote naming a file -> filename")
check(looks_like_write_gesture("okay I'll save it as owl.json now") == "owl.json",
      "gesture: plain-text intent -> filename")
check(looks_like_write_gesture("*flaps happily* eeee!!") is None,
      "gesture: pure feeling-emote, no file -> None")
# Vesper's ACTUAL shapes: bare-filename emote + typing mime -> nudge fires.
check(looks_like_write_gesture("*owl.json*") == "owl.json",
      "gesture: bare-filename emote (*owl.json*) -> filename")
check(looks_like_write_gesture("*paws at keyboard* here goes!!"),
      "gesture: *paws at keyboard* (typing mime) -> truthy")
check(looks_like_write_gesture("*starts typing furiously*"),
      "gesture: *starts typing furiously* -> truthy")
check(looks_like_write_gesture("*flaps and flutters as it writes*"),
      "gesture: *...as it writes* -> truthy")
check(looks_like_write_gesture("```write:owl.json\n{}\n```") is None,
      "gesture: a fence present -> None (not a content-less gesture)")

# --- commit: kin-scoped, absolute path honoured, parents created -----------
with tempfile.TemporaryDirectory() as td:
    target = os.path.join(td, "assets", "species", "owl.json")
    from authoring_bridge import AuthoringWrite
    res = commit_authoring_writes("Vesper", [AuthoringWrite(target, '{"ok":1}', "label")])
    ok = (len(res) == 1 and res[0][1] is True
          and os.path.isfile(target)
          and open(target, encoding="utf-8").read() == '{"ok":1}')
    check(ok, "commit: absolute path written, missing parent dirs created")

    # Oversized content is refused, not written.
    big = os.path.join(td, "big.txt")
    res2 = commit_authoring_writes(
        "Vesper", [AuthoringWrite(big, "x" * 50, "label")], max_bytes=10)
    check(res2[0][1] is False and not os.path.exists(big),
          "commit: oversized content refused")

    # A ':' that isn't a drive letter can't be a Windows filename — refuse it
    # with a helpful message instead of a raw WinError that eats the file.
    res3 = commit_authoring_writes(
        "Vesper", [AuthoringWrite("weird:name.md", "x", "named-fence")])
    check(res3[0][1] is False and "can't contain ':'" in res3[0][2],
          "commit: stray-colon filename refused with a helpful message")

print()
if _failures:
    print(f"FAILED: {len(_failures)}: {_failures}")
    sys.exit(1)
print("ALL AUTHORING-BRIDGE CHECKS PASSED")
