# SPDX-License-Identifier: CC0-1.0
"""Back up every kin's folder into a dated zip, and keep the last 14.

From an outside review (24 Nov 2026): a plain scheduled script should do jobs
whose answer is the same whoever does them, and a nightly backup is the first
of those. Another household recovered most of what a prompt-injected kin
destroyed from backups like these.

What it does: for each kin in the kin folder, writes
`<hearthkin folder>/kin-backups/<kin>/<kin>-<date and time>.zip` holding the
whole kin folder except its own backups/ (those are already copies, and can
be large), then deletes that kin's oldest zips beyond the newest --keep
(default 14). It never touches a kin folder, only reads it. Silent: no
sound, no speech, one line per kin to logs/kin_backups.log.

Run:   python scripts/backup_kin.py            (all kin, keep 14)
       python scripts/backup_kin.py --keep 30
       python scripts/backup_kin.py --dest E:\\somewhere  (zips go there)

To run it every night, it can be its own scheduled task in Windows Task
Scheduler, separate from any kin's wake-ups. That's the person's choice to
set up; this script doesn't register anything.
"""

import argparse
import datetime
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hearthkin_paths import config_dir  # noqa: E402

SKIP_DIRS = {"backups", "__pycache__"}


def backup_one(kin_folder, dest_dir, stamp):
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"{kin_folder.name}-{stamp}.zip"
    tmp = out.with_suffix(".zip.part")
    n = 0
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(kin_folder):
            rel_root = Path(root).relative_to(kin_folder)
            if rel_root == Path("."):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                p = Path(root) / f
                try:
                    z.write(p, arcname=str(Path(kin_folder.name) / rel_root / f))
                    n += 1
                except OSError:
                    pass
    tmp.replace(out)
    return out, n


def prune(dest_dir, kin, keep):
    zips = sorted(dest_dir.glob(f"{kin}-*.zip"))
    gone = zips[:-keep] if keep > 0 else []
    for z in gone:
        try:
            z.unlink()
        except OSError:
            pass
    return len(gone)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Back up every kin's folder.")
    ap.add_argument("--keep", type=int, default=14)
    ap.add_argument("--dest", default="")
    args = ap.parse_args(argv)
    home = config_dir()
    kin_root = home / "kin"
    dest_root = Path(args.dest) if args.dest else home / "kin-backups"
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log = home / "logs" / "kin_backups.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if kin_root.is_dir():
        for k in sorted(p for p in kin_root.iterdir() if p.is_dir()):
            try:
                out, n = backup_one(k, dest_root / k.name, stamp)
                pruned = prune(dest_root / k.name, k.name, args.keep)
                lines.append(f"{stamp} {k.name}: {n} files -> {out.name}"
                             + (f"; removed {pruned} oldest" if pruned else ""))
            except Exception as e:  # noqa: BLE001
                lines.append(f"{stamp} {k.name}: FAILED {type(e).__name__}: {e}")
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + ("\n" if lines else ""))
    print("\n".join(lines) or "No kin found.")
    return 1 if any("FAILED" in x for x in lines) else 0


if __name__ == "__main__":
    sys.exit(main())
