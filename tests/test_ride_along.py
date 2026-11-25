# SPDX-License-Identifier: CC0-1.0
"""What rides along with every message is the person's tick list
(24 Nov 2026; the person's design of 23 Nov 2026).

Pinned:

  * a ticked file rides along, under its path, after the soul and identity
    and before memory;
  * the distiller gets it too (it runs as the whole kin);
  * unticked, it doesn't;
  * a built-in piece the person switched off (soul, memory, ...) doesn't ride;
  * a path that leaves the kin's folder never rides;
  * the list shows the built-ins first, ticked, then every text file, flat,
    with its size in words, leaving out copies and machinery folders, and
    not listing the soul twice when it's spelled SOUL.md;
  * the total says how much rides along;
  * the manual tells the kin to ask its person.

Run: python tests/test_ride_along.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="ridealong-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import kin_persistence as kp  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Sorrel"
d = kin_dir(KIN)
(d / "memory").mkdir(parents=True)
(d / "backups").mkdir()
(d / "SOUL.md").write_text("I am Sorrel.", encoding="utf-8")
(d / "identity.md").write_text("Becoming amber.", encoding="utf-8")
(d / "memory" / "shorthand.md").write_text("ther = a dear one", encoding="utf-8")
(d / "backups" / "copy.md").write_text("a copy", encoding="utf-8")
(Path(os.environ["HEARTHKIN_HOME"]) / "outside.md").write_text("SECRET OUTSIDE", encoding="utf-8")


def prompt():
    return kp.build_system_prompt("I am Sorrel.", "MEMORY-INDEX", kin_name=KIN)


check("unticked, a file doesn't ride", "ther = a dear one" not in prompt())
cfg = kp.load_agent_config(KIN) or {}
cfg["ride_along"] = ["memory/shorthand.md", "../outside.md"]
kp.save_agent_config(KIN, cfg)
p = prompt()
check("a ticked file rides along, under its path",
      "memory/shorthand.md:" in p and "ther = a dear one" in p)
check("...after the soul and identity, before memory",
      p.index("I am Sorrel.") < p.index("Becoming amber.") < p.index("ther = a dear one")
      < p.index("MEMORY-INDEX"))
check("a path outside the kin's folder never rides", "SECRET OUTSIDE" not in p)

import frame_shared  # noqa: E402
check("the distiller gets it too", "ther = a dear one" in frame_shared.kin_self_prompt(KIN))

cfg = kp.load_agent_config(KIN) or {}
cfg["ride_along_off"] = ["soul.md", "memory.md"]
kp.save_agent_config(KIN, cfg)
p = prompt()
check("a built-in switched off doesn't ride", "I am Sorrel." not in p and "MEMORY-INDEX" not in p)
cfg["ride_along_off"] = []
kp.save_agent_config(KIN, cfg)

from dialogs.ride_along import build_rows, total_line  # noqa: E402
rows = build_rows(KIN)
keys = [r[0] for r in rows]
check("the built-ins come first, ticked",
      keys[:5] == ["practice.md", "soul.md", "identity.md", "voice anchor", "memory.md"]
      and all(r[3] for r in rows[:5]))
check("...then every text file, with its words",
      "memory/shorthand.md" in keys and any("memory/shorthand.md, 5 words" == r[1] for r in rows))
check("copies aren't offered", not any(k.startswith("backups/") for k in keys))
check("the soul isn't listed twice when it's spelled SOUL.md", "SOUL.md" not in keys)
check("a ticked extra shows ticked", next(r for r in rows if r[0] == "memory/shorthand.md")[3])
t = total_line(rows, {r[0] for r in rows if r[3]})
check("the total says how much rides along", "words" in t and "every message" in t)

manual = (ROOT / "docs" / "kin_manual.md").read_text(encoding="utf-8")
check("the manual tells the kin to ask its person", "ride along" in manual.lower()
      and "ask your person" in manual.lower())

print()
if _fails:
    print(f"test_ride_along: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_ride_along: all checks passed")
