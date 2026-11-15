# SPDX-License-Identifier: CC0-1.0
"""An edit_file call whose old and new text are the same says nothing changed.

It used to report "replaced 1 occurrence" for an edit that changed nothing,
and a kin and its person both took that as a save (15 Nov 2026). Pinned:

  * identical old and new text: the reply says nothing changed;
  * the file is byte-for-byte as it was, and not even rewritten;
  * a real edit still works and says it replaced;
  * the check can fail: the real edit's reply doesn't say "nothing changed".

Run: python tests/test_edit_file_same_text.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="editsame-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hearthkin_paths import kin_dir  # noqa: E402
from tools.edit_file import edit_file  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Sorrel"
d = kin_dir(KIN)
(d / "memory").mkdir(parents=True, exist_ok=True)
f = d / "memory" / "anchors.md"
original = "# The Five Anchors — Grounding\n\n1. Breath.\n".encode("utf-8")
f.write_bytes(original)
stamp = f.stat().st_mtime_ns

out = edit_file("memory/anchors.md", "# The Five Anchors — Grounding",
                "# The Five Anchors — Grounding", agent_name=KIN)
check("identical old and new text says nothing changed", "nothing changed" in out)
check("...and doesn't claim it replaced anything", "replaced" not in out)
check("the file is byte-for-byte as it was", f.read_bytes() == original)
check("...and wasn't even rewritten", f.stat().st_mtime_ns == stamp)

out = edit_file("memory/anchors.md", "1. Breath.", "1. Breath. 4-4-6.", agent_name=KIN)
check("a real edit still works", "replaced 1 occurrence" in out and "4-4-6" in f.read_text(encoding="utf-8"))
check("the check can fail: a real edit doesn't say nothing changed", "nothing changed" not in out)

print()
if _fails:
    print(f"test_edit_file_same_text: {len(_fails)} FAILED")
    for x in _fails:
        print("  - " + x)
    sys.exit(1)
print("test_edit_file_same_text: all checks passed")
