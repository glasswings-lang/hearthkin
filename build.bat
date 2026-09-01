:: SPDX-License-Identifier: CC0-1.0
@echo off
setlocal
cd /d "%~dp0"

echo === Hearthkin build ===
echo.
echo This script does three things:
echo   1. Build dist\Hearthkin\Hearthkin.exe via PyInstaller (using Hearthkin.spec)
echo   2. Bundle third-party licenses into licenses\
echo   3. (Optional) Build the Windows installer via Inno Setup
echo.

:: --- Dependencies ---
python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo PyInstaller not found. Installing...
    python -m pip install pyinstaller
    if errorlevel 1 goto :fail
)

echo Installing/updating runtime dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 goto :fail

:: --- Step 0: stamp the version into app_version.py ---
:: If HEARTHKIN_VERSION isn't set explicitly, derive it from the current
:: git tag (so a local build of a checked-out tag produces a correctly-
:: stamped binary without ceremony). Falls through to "0.0.0-dev" if
:: neither is available.
if "%HEARTHKIN_VERSION%"=="" (
    for /f "delims=" %%v in ('git describe --tags --exact-match HEAD 2^>nul') do set "HEARTHKIN_VERSION=%%v"
) else (
    echo.
    echo NOTE: Using pre-set HEARTHKIN_VERSION=%HEARTHKIN_VERSION% from the environment.
    echo       This overrides the git-describe auto-detection. If this was unintentional,
    echo       run "set HEARTHKIN_VERSION=" before building to clear it.
    echo.
)
set "_VERSION_STAMPED=0"
echo Stamping app_version.py with HEARTHKIN_VERSION=%HEARTHKIN_VERSION%
python scripts\stamp_version.py
if errorlevel 1 goto :fail
set "_VERSION_STAMPED=1"

:: --- Step 0.5: fetch the Time for Family game to bundle ---
:: The `tff` tool's game lives in its OWN repo; ship a copy so the tool works
:: out of the box. Clone the game into .bundle_game\ (gitignored) for
:: Hearthkin.spec to pick up. Set HEARTHKIN_BUNDLE_GAME to bundle a local
:: checkout instead (e.g. to test unpushed game edits). If git/network is
:: unavailable the build still proceeds -- the game just won't be bundled and
:: `tff` falls back to a per-user clone at runtime.
if not "%HEARTHKIN_BUNDLE_GAME%"=="" (
    echo Using pre-set HEARTHKIN_BUNDLE_GAME=%HEARTHKIN_BUNDLE_GAME% for the bundled game.
) else (
    echo.
    echo Fetching Time for Family game to bundle...
    if exist ".bundle_game\time-for-family" rmdir /s /q ".bundle_game\time-for-family"
    git clone --depth 1 https://github.com/glasswings-lang/time-for-family.git ".bundle_game\time-for-family"
    if errorlevel 1 echo WARNING: could not clone the game; tff will NOT be bundled in this build.
)

:: --- Step 1: PyInstaller ---
echo.
echo [1/3] Building dist\Hearthkin\Hearthkin.exe (this takes ~1-2 minutes)...
echo (onedir mode: the EXE lives in dist\Hearthkin\ alongside _internal\)
python -m PyInstaller --noconfirm Hearthkin.spec
if errorlevel 1 goto :fail

if not exist "dist\Hearthkin\Hearthkin.exe" (
    echo PyInstaller finished but dist\Hearthkin\Hearthkin.exe is missing.
    echo The spec is configured for onedir mode -- make sure the COLLECT()
    echo step in Hearthkin.spec ran.
    goto :fail
)

:: --- Step 2: License bundling ---
echo.
echo [2/3] Bundling third-party licenses...
python scripts\bundle_licenses.py
if errorlevel 1 goto :fail

:: --- Stage extra files into the portable tree ---
:: Mirror what the installer [Files] section ships separately, so that
:: the dist\Hearthkin\ directory is a complete portable distribution
:: without needing the installer. Same files; same layout.
echo.
echo Staging extra files into dist\Hearthkin\...
:: NVDA Controller Client (LGPL 2.1) — must sit next to Hearthkin.exe,
:: NOT inside _internal\, so users can replace it (LGPL §6(b) compliance).
copy /y "vendor\nvda\nvdaControllerClient64.dll" "dist\Hearthkin\" >nul
:: NVDA vendor notices into licenses\
if not exist "dist\Hearthkin\licenses" mkdir "dist\Hearthkin\licenses"
copy /y "vendor\nvda\license.txt" "dist\Hearthkin\licenses\NVDA-ControllerClient-LGPL-2.1.txt" >nul
copy /y "vendor\nvda\README.md"   "dist\Hearthkin\licenses\NVDA-ControllerClient-NOTICE.md"   >nul
:: bundle_licenses.py output — copy any generated license texts
if exist "licenses" (
    xcopy /y /q "licenses\*" "dist\Hearthkin\licenses\" >nul
)
:: Top-level attribution and readme
copy /y "LICENSE"      "dist\Hearthkin\" >nul
copy /y "README.md"    "dist\Hearthkin\" >nul
:: Application icon (tray.py looks for this next to Hearthkin.exe)
copy /y "Hearthkin.ico" "dist\Hearthkin\" >nul
echo   Done staging.

:: --- Step 3: Inno Setup (optional) ---
echo.
echo [3/3] Building Windows installer via Inno Setup...

:: Look for iscc.exe in common locations. Fall back to PATH.
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\iscc.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\iscc.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\iscc.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\iscc.exe"
if not defined ISCC where iscc >nul 2>nul && set "ISCC=iscc"

if not defined ISCC (
    echo.
    echo Inno Setup not found. Skipping installer build.
    echo Get it from https://jrsoftware.org/isdl.php and rerun this
    echo script to produce dist\Hearthkin-Setup-^<version^>.exe.
    echo.
    echo dist\Hearthkin\ is ready as a portable build (Hearthkin.exe
    echo plus its _internal\ sibling -- ship the whole folder).
    goto :done
)

"%ISCC%" installer\Hearthkin.iss
if errorlevel 1 goto :fail

:done
:: Restore app_version.py to its committed placeholder so the local
:: working tree stays clean after a build. If git isn't available
:: (e.g., the user built from a downloaded zip), best-effort — leave
:: the stamped version in place; nothing depends on it post-build.
git checkout -- app_version.py >nul 2>nul

echo.
echo === Done ===
if exist "dist\Hearthkin-Setup-*.exe" (
    echo Installer: dist\Hearthkin-Setup-^<version^>.exe
)
echo Portable:  dist\Hearthkin\Hearthkin.exe  (the whole dist\Hearthkin\
echo            directory is the portable distribution -- the .exe needs
echo            its _internal\ sibling to launch).
echo.
pause
exit /b 0

:fail
echo.
echo Build failed. Scroll up to see the error.
:: Restore app_version.py if we stamped it, so a failed build doesn't
:: leave the working tree dirty. Without this, a failure after stamp
:: would leave the version constant set to the release string, which
:: causes confusing self-reports ("0.5.9-dev" instead of "0.0.0-dev")
:: if the developer runs the app directly after a failed build.
if "%_VERSION_STAMPED%"=="1" (
    git checkout -- app_version.py >nul 2>nul
)
pause
exit /b 1
