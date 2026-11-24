# SPDX-License-Identifier: CC0-1.0
"""Plain jobs done without the model (24 Nov 2026, from an outside review).

  * scripts/backup_kin.py zips each kin's folder (not its backups/, which
    are already copies), keeps the newest N, logs one line per kin, and
    never changes a kin folder;
  * a wake-up set to run only when there are memory notes to tend is
    skipped, without a model call, when none are waiting; and runs when
    some are; and a wake-up without the setting always runs.

Run: python tests/test_backup_and_tend_skip.py
"""

import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="bktend-")
os.environ["HEARTHKIN_HOME"] = HOME
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


k = Path(HOME) / "kin" / "Sorrel"
(k / "backups" / "cleared").mkdir(parents=True)
(k / "memory").mkdir()
(k / "soul.md").write_text("I am Sorrel.", encoding="utf-8")
(k / "memory" / "color.md").write_text("amber", encoding="utf-8")
(k / "backups" / "cleared" / "old.jsonl").write_text("{}\n", encoding="utf-8")
before = sorted(str(p) for p in k.rglob("*"))

env = dict(os.environ, HEARTHKIN_HOME=HOME)
for _ in range(3):
    subprocess.run([sys.executable, str(ROOT / "scripts" / "backup_kin.py"), "--keep", "2"],
                   env=env, capture_output=True, text=True)
    time.sleep(1.1)
zips = sorted((Path(HOME) / "kin-backups" / "Sorrel").glob("*.zip"))
names = zipfile.ZipFile(zips[-1]).namelist() if zips else []
check("each kin's folder is zipped", "Sorrel/soul.md" in names and "Sorrel/memory/color.md" in names)
check("...without its backups (already copies)", not any("backups" in n for n in names))
check("only the newest --keep zips stay", len(zips) == 2)
check("one line per kin is logged",
      "Sorrel:" in (Path(HOME) / "logs" / "kin_backups.log").read_text(encoding="utf-8"))
check("the kin folder isn't changed", sorted(str(p) for p in k.rglob("*")) == before)

import hearthkin_cron as hc  # noqa: E402
import kin_persistence as kp  # noqa: E402
entry = {"only_if_tending": True}
check("with nothing waiting, a tend-only wake-up is skipped", hc.nothing_to_tend("Sorrel", entry))
kp.append_staging("Sorrel", "desktop", "a note to tend")
check("...with notes waiting, it runs", not hc.nothing_to_tend("Sorrel", entry))
check("a wake-up without the setting always runs", not hc.nothing_to_tend("Sorrel", {}))
src = (ROOT / "hearthkin_cron.py").read_text(encoding="utf-8")
check("the skip happens before any model call",
      src.index("if nothing_to_tend(kin, entry):") < src.index("cfg = cfg_for_entry(cfg, entry)"))
dlg = (ROOT / "dialogs" / "cron_entry.py").read_text(encoding="utf-8")
check("the wake-up window has the switch", '"only_if_tending"' in dlg)

print()
if _fails:
    print(f"test_backup_and_tend_skip: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_backup_and_tend_skip: all checks passed")
