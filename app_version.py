# SPDX-License-Identifier: CC0-1.0

"""Single source of truth for Hearthkin's version string.

This file is committed with a placeholder value (``"0.0.0-dev"``) and
gets rewritten by ``scripts/stamp_version.py`` immediately before
PyInstaller runs in a release build, so the bundled ``Hearthkin.exe``
self-reports the tag it was built from. CI sets HEARTHKIN_VERSION from
the git tag; local builds via ``build.bat`` propagate the same env var.

Anyone running from source (``python hearthkin.pyw``) without going
through the build pipeline sees the placeholder, which is correct —
they're running an unreleased build.

Do NOT manually bump this per release. The build does it. Editing
this file by hand on master defeats the whole point of the indirection
and re-introduces the drift bug it was created to prevent.
"""

__version__ = "0.0.0-dev"