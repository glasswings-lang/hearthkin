# SPDX-License-Identifier: CC0-1.0

# -*- mode: python ; coding: utf-8 -*-


# NOTE: NVDA Controller Client (vendor/nvda/nvdaControllerClient64.dll)
# is deliberately NOT listed in binaries=[] / datas=[]. The DLL is
# LGPL 2.1; LGPL §6(b) requires the end user can replace it with
# a modified build. Embedding it inside the PyInstaller bundle would
# place it under the app's _internal/ directory where most users won't
# find it (and historically, in onefile mode, into a per-session temp
# directory). Instead the installer (Hearthkin.iss) ships the DLL
# alongside Hearthkin.exe in the install dir, where it IS user-
# replaceable. audio.py's _load_nvda_dll looks in the install dir first
# (os.path.dirname(sys.executable)), then falls back to system NVDA
# installs and an HEARTHKIN_NVDA_DLL env-var override.
#
# Build mode: --onedir (NOT --onefile). The onefile bootloader extracts
# ~32 MB of Python + app code to %TEMP%\_MEI<random> on every launch,
# which races against Windows Defender / EDR scanning and can leave
# the bootloader stub stuck mid-extraction — process alive, no window,
# Restart Manager unable to close it, subsequent installs blocked. See
# installer/INSTALLER_REWRITE.md for the full diagnosis. Onedir just
# launches Hearthkin.exe directly from the install dir; no extraction,
# no race. UPX is also disabled because UPX-packed PyInstaller binaries
# are AV-flagged far more often than unpacked ones, and the size win
# disappears once Inno LZMA-compresses the installer anyway.
# --- Time for Family game (bundled "outside" — fetched at build time) ---
# The `tff` tool bridges to Time for Family, which lives in its OWN repo
# (github.com/glasswings-lang/time-for-family), NOT in this one. The release
# ships a copy so the tool works out of the box. build.bat / the CI workflow
# clone the game into .bundle_game/ (or set HEARTHKIN_BUNDLE_GAME) before
# invoking PyInstaller. We bundle ONLY the headless subset (the wx GUI files
# are dead weight here) to games/time-for-family/ inside the app; the runtime
# seeder (kin_persistence.seed_bundled_game) copies it to a writable
# ~/.hearthkin/games/ on first run (the game writes its own data next to
# itself, so it can't run from the read-only bundle). If the game source
# isn't present, bundling is skipped — the build still succeeds and `tff`
# falls back to the operator's own clone / env var / path file at runtime.
import os as _os
# discord.py is a hard dependency (in requirements.txt). It loads ~130
# submodules dynamically, which PyInstaller's static analysis alone would
# miss, so we collect them explicitly here or the shipped .exe would ship a
# Discord surface that silently no-ops for every installed user — which is
# exactly what v0.9.0 did before discord.py was bundled. collect_submodules
# returns [] cleanly when discord isn't installed, so a stripped-down local
# build without it still works.
from PyInstaller.utils.hooks import collect_submodules as _collect_submodules
_DISCORD_HIDDEN = _collect_submodules("discord")

# --- Dictation (faster-whisper), bundled only if it is installed -------
# Speaking a message instead of typing it runs on the user's own machine
# by default, and for that to be true of the SHIPPED app the speech
# library has to be inside it: someone running an installed .exe cannot
# pip install into it, so "optional dependency" would mean "absent" for
# every one of them, and the free option would be the one only
# developers get.
#
# faster_whisper gets collect_all rather than a hidden import, because it
# carries a voice-activity model as a DATA FILE inside the package. A
# static import scan finds the code and not the file, and the failure
# then lands at the worst possible moment — the first time somebody
# speaks — rather than at build time.
#
# EVERYTHING ELSE IS DELIBERATELY NOT collect_all'd, and this is the
# expensive part to get wrong. `huggingface_hub` ships optional
# integrations that import torch and tensorflow; collecting all of its
# submodules makes PyInstaller follow those, and torch alone is over
# four gigabytes. Measured while writing this: a collect-all build sat
# in analysis for eighteen minutes and had not finished. ctranslate2
# needs only its compiled libraries; av, tokenizers and onnxruntime
# already have hooks in pyinstaller-hooks-contrib; huggingface_hub is
# ordinary Python that static analysis follows correctly. The excludes
# below are the belt to that braces — nothing here needs a deep-learning
# framework at runtime, so if one is reachable at all, that is the bug.
#
# Guarded, and silent when absent: a machine without faster-whisper still
# builds, and the app then says why dictation is unavailable rather than
# crashing. The other two backends (a Whisper server, ElevenLabs) need
# nothing bundled at all.
#
# The real cost is about 120 MB of CTranslate2 and onnxruntime. That is a
# lot, and it is the right trade: the alternative is a build in which the
# only dictation anyone can reach is the paid one.
#
# CUDA is deliberately NOT bundled either. The graphics-card path here
# borrows CUDA libraries that happen to arrive with torch, and shipping
# them would add hundreds of megabytes plus a redistribution question of
# their own. The packaged build transcribes on the processor instead --
# measured under a second for a spoken sentence -- and stt falls back to
# it automatically, at transcribe time as well as at load time, so this
# is invisible rather than an error naming a missing DLL.
from PyInstaller.utils.hooks import (
    collect_all as _collect_all,
    collect_dynamic_libs as _collect_dynamic_libs,
)
_WHISPER_HIDDEN, _WHISPER_DATAS, _WHISPER_BINARIES = [], [], []
# Frameworks nothing in Hearthkin uses at runtime. Named even when they
# are not installed — excludes cost nothing when absent, and the machine
# that builds a release is not necessarily the machine that tested it.
_HEAVY_EXCLUDES = [
    # PyAV, and therefore FFmpeg. faster-whisper imports it to decode
    # media files; nothing here ever asks it to, because stt.wav_to_array
    # reads our own WAV with the standard library and hands Whisper plain
    # samples. Excluding it is a LICENSING decision as much as a size one:
    # the PyAV wheel ships an FFmpeg built with libx264 and libx265, both
    # GPL, which would put copyleft obligations on the releases of a CC0
    # project. stt._install_av_stub satisfies the import at runtime and
    # says so plainly if anything ever does try to use it. Worth ~70 MB
    # as well.
    'av',
    'torch', 'torchaudio', 'torchvision',
    'tensorflow', 'tensorflow_hub', 'keras', 'jax', 'flax',
    'transformers', 'datasets', 'accelerate', 'safetensors',
    'matplotlib', 'pandas', 'scipy', 'sklearn', 'IPython', 'notebook',
]
try:
    import faster_whisper as _fw  # noqa: F401
except Exception:
    print("Hearthkin.spec: NOTE - faster-whisper not installed; local "
          "dictation will not be available in this build (a Whisper "
          "server or ElevenLabs still will be).")
else:
    _d, _b, _h = _collect_all("faster_whisper")
    _WHISPER_DATAS += _d
    _WHISPER_BINARIES += _b
    _WHISPER_HIDDEN += _h
    # The compiled inference engine. Its Python surface is small; what
    # matters is the DLLs sitting next to it.
    try:
        _WHISPER_BINARIES += _collect_dynamic_libs("ctranslate2")
    except Exception as _e:
        print("Hearthkin.spec: WARNING - no ctranslate2 libraries collected "
              "(%s); local dictation will not work in this build." % _e)
    # Hooks in pyinstaller-hooks-contrib handle these; they only need to
    # be reachable as imports.
    # NOT "av". See _HEAVY_EXCLUDES: PyAV is deliberately kept out and
    # stubbed at runtime, because its FFmpeg is GPL and nothing here
    # decodes a media file.
    _WHISPER_HIDDEN += ["ctranslate2", "tokenizers", "onnxruntime",
                        "huggingface_hub"]
    print("Hearthkin.spec: bundling dictation (faster-whisper): "
          "%d data file(s), %d binaries" % (len(_WHISPER_DATAS),
                                            len(_WHISPER_BINARIES)))

_game_src = _os.environ.get("HEARTHKIN_BUNDLE_GAME") or _os.path.join(
    _os.getcwd(), ".bundle_game", "time-for-family")
_GAME_DATAS = []
if _os.path.isfile(_os.path.join(_game_src, "tff_play.py")):
    for _f in ("tff_play.py", "tff_engine.py", "tff_announcements.py",
               "tff_species_author.py"):
        _p = _os.path.join(_game_src, _f)
        if _os.path.isfile(_p):
            _GAME_DATAS.append((_p, "games/time-for-family"))
    for _root, _dirs, _files in _os.walk(_os.path.join(_game_src, "assets")):
        for _fn in _files:
            _full = _os.path.join(_root, _fn)
            _rel = _os.path.relpath(_os.path.dirname(_full), _game_src)
            _GAME_DATAS.append(
                (_full, _os.path.join("games", "time-for-family", _rel)))
    print("Hearthkin.spec: bundling Time for Family (%d files) from %s"
          % (len(_GAME_DATAS), _game_src))
else:
    print("Hearthkin.spec: NOTE - no game at %s; `tff` will NOT be bundled "
          "(falls back to the operator's own clone at runtime)." % _game_src)

a = Analysis(
    ['hearthkin.pyw'],
    pathex=[],
    binaries=_WHISPER_BINARIES,
    # Bundle only the user-facing docs. PyInstaller 6 onedir places
    # datas under _internal/ (e.g. _internal/docs/user-guide.html).
    # tray._open_user_guide and kin_persistence._find_bundled_doc both
    # resolve via Path(__file__).parent / "docs" / filename when frozen,
    # which correctly points into _internal/docs/. The installer Start
    # Menu shortcut uses {app}\_internal\docs\user-guide.html to match.
    #
    # Only user-facing files ship: user-guide.html, kin_manual.md,
    # troubleshooting.md. Internal design/ and planning/ docs are NOT
    # bundled — they're developer material, not operator material, and
    # including them bloats the release artifact unnecessarily.
    datas=[
        ('docs/user-guide.html',   'docs'),
        ('docs/kin_manual.md',     'docs'),
        ('docs/troubleshooting.md','docs'),
    ] + _GAME_DATAS + _WHISPER_DATAS,
    # cv2 is imported inside tools/use_webcam.py behind a try/except so
    # missing-opencv produces an in-app error instead of an import-time
    # crash. PyInstaller's static analysis usually follows that import
    # fine, but listing it explicitly here is belt-and-suspenders —
    # ensures the bundled exe picks up the opencv-python wheel even if
    # the static analyzer ever decides the try-except branch is dead
    # code. pyinstaller-hooks-contrib has a built-in cv2 hook that
    # pulls in the bundled DLLs automatically.
    hiddenimports=['cv2'] + _DISCORD_HIDDEN + _WHISPER_HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_HEAVY_EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Hearthkin',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Embed Hearthkin.ico into the .exe so Windows shows the right
    # icon in Explorer / Alt-Tab / the taskbar before the app even
    # starts. The same .ico is also installed alongside the .exe (via
    # the Inno Setup script in installer/) so the tray code can load
    # it at runtime without reading from the embedded resource.
    icon='Hearthkin.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Hearthkin',
)
