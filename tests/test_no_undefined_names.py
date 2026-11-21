"""Every name the app code uses must exist. Two Telegram error paths
called a logging helper that was never imported, so a failed download
crashed instead of being logged (found 21 Nov 2026). pyflakes catches
the whole class of mistake; this runs it over the app's own code."""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRS = ["", "frame", "dialogs", "tools"]


def _files():
    out = []
    for d in DIRS:
        folder = os.path.join(ROOT, d)
        for n in sorted(os.listdir(folder)):
            if n.endswith(".py"):
                out.append(os.path.join(folder, n))
    return out


def _run_pyflakes(files):
    try:
        import pyflakes  # noqa: F401
        cmd = [sys.executable, "-m", "pyflakes"]
    except ImportError:
        launcher = shutil.which("py")
        if not launcher:
            return None
        probe = subprocess.run([launcher, "-m", "pyflakes", "--version"],
                               capture_output=True, text=True)
        if probe.returncode != 0:
            return None
        cmd = [launcher, "-m", "pyflakes"]
    r = subprocess.run(cmd + files, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout + r.stderr


def main():
    out = _run_pyflakes(_files())
    if out is None:
        print("SKIP: pyflakes not available")
        return
    bad = [l for l in out.splitlines() if "undefined name" in l]
    assert not bad, "names used but never defined:\n" + "\n".join(bad)
    print("PASS: no undefined names in app code")


if __name__ == "__main__":
    main()
