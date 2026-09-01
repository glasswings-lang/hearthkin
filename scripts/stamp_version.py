# SPDX-License-Identifier: CC0-1.0

"""Rewrite ``app_version.py`` to reflect the version we're about to build.

Called by both ``build.bat`` (local builds) and the GitHub Actions
release workflow immediately before PyInstaller runs. Reads the version
from the ``HEARTHKIN_VERSION`` environment variable — set by the
workflow from the git tag, and by ``build.bat`` from either the tag or
an explicit override.

If ``HEARTHKIN_VERSION`` is unset or empty, the script is a no-op (the
file already contains the placeholder value, which is correct for
unstamped builds).

Idempotent: rewriting the same version twice produces identical output.

Why a separate file (``app_version.py``) instead of patching
``hearthkin.pyw`` directly: keeping the mutation scoped to a tiny
dedicated file means a build that crashes after the stamp step doesn't
leave the main module in a confusing state, and ``git diff`` after a
local build only shows the one-line version change.
"""

import os
import re
import sys
from pathlib import Path


def main():
    version = os.environ.get("HEARTHKIN_VERSION", "").strip()
    if version.startswith("v"):
        version = version[1:]
    if not version:
        print("stamp_version: HEARTHKIN_VERSION not set; leaving placeholder")
        return 0

    # Sanity-check shape. Accept SemVer-ish (digits + dots + optional
    # pre-release/build suffix). Refuse anything wildly malformed so a
    # typo doesn't produce a release with a garbage version string.
    if not re.match(r"^\d+(\.\d+){0,3}([-+.][A-Za-z0-9.-]+)?$", version):
        print(f"stamp_version: refusing to stamp suspicious version {version!r}")
        return 1

    target = Path(__file__).parent.parent / "app_version.py"
    if not target.exists():
        print(f"stamp_version: {target} not found")
        return 1

    text = target.read_text(encoding="utf-8")
    new_text, n = re.subn(
        r'^__version__\s*=\s*"[^"]*"\s*$',
        f'__version__ = "{version}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n != 1:
        print(
            f"stamp_version: couldn't locate __version__ assignment in {target}"
        )
        return 1

    if new_text == text:
        print(f"stamp_version: already at {version}, nothing to do")
        return 0

    target.write_text(new_text, encoding="utf-8")
    print(f"stamp_version: app_version.py -> {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
