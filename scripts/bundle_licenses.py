# SPDX-License-Identifier: CC0-1.0

"""Collect third-party license texts into ./licenses/ for the
installer to ship alongside Hearthkin.exe.

Hearthkin's own source is CC0 (no obligations) but it links to and
ships with code under more constrained licenses — wxPython's wxWindows
License (modified LGPL with a static-linking exception) is the main
one; Pillow / urllib3 / etc. depend on what PyInstaller bundles. This
script walks the installed Python distributions, extracts each
package's LICENSE-equivalent file via importlib.metadata, and writes
one file per package into ./licenses/.

Run after `pip install -r requirements.txt` (so the deps are
discoverable) and before invoking the Inno Setup compiler:

    python scripts/bundle_licenses.py

The Inno Setup script in installer/Hearthkin.iss picks the licenses/
directory up wholesale; the install lays it down at
{app}\\licenses\\<package>.txt.

Hearthkin's own LICENSE file (the CC0 dedication) is copied here too,
under the name HEARTHKIN.txt, so the licenses/ directory is a single
place to point users for "what can I do with this software"."""

import importlib.metadata
import shutil
import sys
from pathlib import Path


# Packages we deliberately want license text for. Anything in the
# installed Python env not on this list is skipped — bundling
# transitively-installed dev tools (pytest, black, etc.) would be
# noise in the licenses/ dir.
WANTED = {
    "wxPython",
    "ollama",
    "pyinstaller",
    "pyinstaller-hooks-contrib",
    "altgraph",
    "pefile",
    "pywin32-ctypes",
    "macholib",
    "packaging",
    "setuptools",
    "pillow",
    # Voice: microphone capture, and text-to-speech playback:
    "sounddevice",
    "numpy",
    "cffi",       # sounddevice transitive
    "pycparser",  # cffi transitive
    # Dictation on this machine (faster-whisper). Bundled when it is
    # installed at build time; see Hearthkin.spec. Absent from a build
    # without it, which is why every name here has to survive not being
    # found rather than failing the build.
    #
    # PyAV is deliberately NOT here, because it is deliberately not
    # shipped: its FFmpeg is built with libx264/libx265 and is therefore
    # GPL, and nothing in this app decodes a media file. If PyAV ever
    # returns to the bundle, its licence AND FFmpeg's have to come with
    # it, and the CC0 claim on the release needs revisiting first.
    "faster-whisper",
    "ctranslate2",
    "tokenizers",
    "onnxruntime",    # voice-activity detection
    "huggingface_hub",
    "tqdm",           # huggingface-hub transitive
    "filelock",       # huggingface-hub transitive
    "fsspec",         # huggingface-hub transitive
    "requests",       # huggingface-hub transitive
    "urllib3",        # requests transitive
    "charset-normalizer",
    "PyYAML",
    "flatbuffers",    # onnxruntime transitive
    "protobuf",       # onnxruntime transitive
    "sympy",          # onnxruntime transitive
    "mpmath",         # sympy transitive
    # Webcam capture (use_webcam tool):
    "opencv-python",
    # ollama-python transitives (bundled by PyInstaller):
    "httpx",
    "httpcore",
    "pydantic",
    "pydantic_core",
    "certifi",
    "anyio",
    "idna",
    "sniffio",
    "h11",
    "annotated-types",
    "typing_extensions",
    # Stdlib/Python itself isn't installed via pip, but the Python
    # license is also redistributable; we copy it out below from a
    # known location.
}


def repo_root():
    return Path(__file__).parent.parent


def find_license_text(dist):
    """Return the license text for a metadata Distribution object,
    or None if we can't find one. importlib.metadata exposes both
    the License-File field (preferred — actual LICENSE file ships
    with the wheel) and the License field (a one-line classifier
    summary). Try the file first, fall back to the metadata field."""
    # PEP 639 / modern wheels expose license files under the dist's
    # files attribute. Look for a file named LICENSE, LICENCE, or
    # COPYING (case-insensitive, with or without an extension).
    files = list(dist.files or [])
    for f in files:
        name = f.name.upper()
        base = name.split(".")[0]
        if base in ("LICENSE", "LICENCE", "COPYING", "NOTICE"):
            try:
                text = (dist.locate_file(f)).read_text(encoding="utf-8", errors="replace")
                if text.strip():
                    return text
            except Exception:
                continue
    # Fallback to the metadata License field (one-line summary).
    meta = dist.metadata
    license_classifier = meta.get("License") or ""
    if license_classifier and license_classifier != "UNKNOWN":
        # Wrap the summary in some context so it's clear what it is.
        return (
            f"License (from package metadata): {license_classifier}\n\n"
            f"Package: {meta.get('Name', '?')} {meta.get('Version', '')}\n"
            f"Author: {meta.get('Author', '?')}\n"
            f"Home-page: {meta.get('Home-page', '?')}\n\n"
            "(No LICENSE file shipped with the wheel; see the project's "
            "own repository for the full license text.)\n"
        )
    return None


def main():
    out = repo_root() / "licenses"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    # Always include Hearthkin's own LICENSE first so the dir is
    # self-explanatory even if dep extraction fails.
    own_license = repo_root() / "LICENSE"
    if own_license.exists():
        (out / "HEARTHKIN.txt").write_text(
            own_license.read_text(encoding="utf-8", errors="replace"),
            encoding="utf-8",
        )

    found = 0
    skipped_unmatched = []
    skipped_no_license = []
    for dist in importlib.metadata.distributions():
        name = (dist.metadata.get("Name") or "").strip()
        if not name:
            continue
        if name.lower() not in {n.lower() for n in WANTED}:
            skipped_unmatched.append(name)
            continue
        text = find_license_text(dist)
        if not text:
            skipped_no_license.append(name)
            continue
        version = dist.metadata.get("Version", "")
        out_path = out / f"{name}-{version}.txt"
        # Prepend a short header naming the package so the file is
        # self-describing when a user opens it from Explorer.
        header = (
            f"License for {name} {version}\n"
            f"{'=' * (12 + len(name) + len(version) + 1)}\n\n"
        )
        out_path.write_text(header + text, encoding="utf-8")
        found += 1

    # Index file listing what's in here.
    index = ["# Third-party licenses bundled with Hearthkin\n\n"]
    index.append(
        "Hearthkin's own source is released under CC0 1.0 Universal "
        "(see HEARTHKIN.txt), but the application links to and ships "
        "with code under other licenses. Each file in this directory "
        "is the verbatim license text for one bundled component.\n\n"
    )
    index.append("## Bundled components\n\n")
    for f in sorted(out.iterdir()):
        if f.is_file() and f.suffix == ".txt":
            index.append(f"- `{f.name}`\n")
    (out / "README.md").write_text("".join(index), encoding="utf-8")

    print(f"Bundled {found} third-party licenses into {out}")
    if skipped_no_license:
        print(
            "  Warning: no LICENSE found for: "
            + ", ".join(sorted(skipped_no_license))
        )
        print(
            "  These packages are bundled by PyInstaller but their wheels "
            "didn't ship a license file. Check upstream repos before "
            "redistributing."
        )

    # Warn when a WANTED package wasn't found at all in the environment.
    # This catches WANTED list entries that were never installed, which
    # would leave a compliance gap in the shipped licenses/ directory.
    found_names = {(dist.metadata.get("Name") or "").lower() for dist in importlib.metadata.distributions()}
    missing_wanted = {n for n in WANTED if n.lower() not in found_names}
    if missing_wanted:
        print(
            "  Warning: WANTED packages not found in environment (not installed?): "
            + ", ".join(sorted(missing_wanted)),
            file=sys.stderr,
        )
        print(
            "  Their license texts were NOT included. Install them (pip install -r requirements.txt)"
            " before building a release artifact.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
