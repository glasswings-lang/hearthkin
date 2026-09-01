# Installer rewrite — handoff for the next session

**Status (2026-05-19, follow-up session — IMPLEMENTED):** all four layered fixes below are now in master. `Hearthkin.spec` switched from `--onefile` to `--onedir` with `upx=False`; `installer/Hearthkin.iss` bundles the onedir tree via `Source: "..\dist\Hearthkin\*"` with `recursesubdirs createallsubdirs`, drops the `[Run]` auto-launch, and adds a `[Code]` `InitializeSetup` that taskkills any leftover `Hearthkin.exe` before file replacement. `build.bat` and `.github/workflows/build-release.yml` updated to point at the new output paths (the workflow zips `dist\Hearthkin\*` into `Hearthkin-Portable-{version}.zip` as the new portable Release asset, replacing the old `Hearthkin.exe` single-file portable). Original diagnosis preserved below as historical context.

---

**Original status (2026-05-18 ~19:00 PDT, latest release v0.2.20):** the Windows installer pipeline has a latent bug that has been silently breaking installs all day. This document is the handoff for the next session that picks this up.

## The actual bug

`Hearthkin.exe` is a PyInstaller **onefile** binary, UPX-compressed. When launched, the small C bootloader stub extracts ~32 MB of Python + app code into `%TEMP%\_MEI<random>` and then executes the real app. Under load — Windows Defender / EDR scanning the binary, slow disk, AV interference — **the bootloader can get stuck mid-extraction and never reach Python**. The stub stays alive holding the .exe file lock, has 1 thread, ~8 MB RAM, no message loop, no window.

When this happens:
- Inno Setup's Restart Manager (`CloseApplications=yes`) tries to close the running `Hearthkin.exe` before file replacement.
- RM sends `WM_QUERYENDSESSION` / `WM_ENDSESSION` to top-level windows.
- The stub has no top-level windows. RM can't close it.
- RM logs event 10006 "Application or service 'Hearthkin.exe' could not be shut down" and waits its timeout (~90s).
- After timeout, Inno proceeds anyway because `[Files]` flags include `ignoreversion` — the .exe gets overwritten on disk.
- The stuck stub remains in memory, still holding the OLD .exe content in its working set, still locking the on-disk file from a process-handle perspective.
- User launches the "new" Hearthkin from the Start Menu. A second bootloader process starts. Stubs accumulate. Eventually a triggering event (today: NVIDIA Container service crash storm, 30+ events 7023/7031 in two minutes correlated with one of the install attempts) destabilizes the machine.

## Why none of my v0.2.13-v0.2.16 "installer fixes" actually addressed this

Those fixes targeted the **fully-initialized Hearthkin app**:
- **v0.2.13:** added `EVT_QUERY_END_SESSION` / `EVT_END_SESSION` handlers on `wx.Frame`. Silent dead code (handlers don't fire on frames).
- **v0.2.14:** moved bindings to `wx.App` where they fire.
- **v0.2.16:** discovered the wxApp default `OnQueryEndSession` closes every TLW, and three of our close handlers (`MiniChatFrame`, Preferences/Usage dialog hides, `EditKinDialog` unsaved-changes modal) were unconditionally vetoing during shutdown. Gated those on `frame._quitting`.

All three of those fixes are CORRECT for a running Hearthkin doing close-to-tray. **They cannot help when the bootloader stub never reaches Python.** No `wx.App` exists in a stuck stub; no Python is loaded. The fixes are right, they're just for a different scenario than the actual install-blocker.

The "successful installs" of v0.2.17, v0.2.18, v0.2.19 today succeeded via the `ignoreversion` overwrite path, NOT because shutdown worked. The user thought we were making progress; we weren't, on this specific axis.

## The proposed fix (layered — implement in order)

### 1. Disable UPX compression in `Hearthkin.spec` (one-line change)

UPX-packed PyInstaller binaries are flagged by Windows Defender far more often than unpacked ones. The size win (~30% smaller binary) is not worth the launch-reliability cost — and the installer itself is LZMA-compressed at the Inno layer anyway, so binary size on the download side barely changes.

```python
# Hearthkin.spec
exe = EXE(
    ...,
    upx=False,   # was True (default)
    ...,
)
```

This is the cheapest, lowest-risk mitigation. Test before going to step 2 — UPX-off may already be enough to keep Defender from holding the binary mid-scan.

### 2. Switch from PyInstaller `--onefile` to `--onedir` (structural fix)

Onefile's whole architecture relies on the bootloader-extract-launch dance. Onedir mode produces a directory tree (`dist/Hearthkin/`) with `Hearthkin.exe` (small launcher) + Python DLL + all packaged modules + bundled data — and launches **instantly** with no extraction step. No race against AV, no temp dir, no possibility of the bootloader-stub failure mode.

The user-facing experience is unchanged because the Inno installer already bundles the result — instead of bundling a single .exe, it bundles the directory contents into `{app}\`.

Changes:

```python
# Hearthkin.spec — at the bottom, replace the onefile EXE() with onedir EXE() + COLLECT()
exe = EXE(
    pyz, a.scripts,
    [],
    exclude_binaries=True,       # was missing (or set False) for onefile
    name='Hearthkin',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon='Hearthkin.ico',
    # (drop the data/binaries args here — those go to COLLECT now)
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='Hearthkin',
)
```

```ini
; Hearthkin.iss — [Files] section becomes:
[Files]
Source: "..\dist\Hearthkin\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\Hearthkin.ico";        DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE";              DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md";            DestDir: "{app}"; Flags: ignoreversion
Source: "..\docs\*";               DestDir: "{app}\docs";     Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\licenses\*";           DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

; NVDA controller DLL stays in {app} next to Hearthkin.exe — same as before:
Source: "..\vendor\nvda\nvdaControllerClient64.dll"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\vendor\nvda\license.txt";                DestDir: "{app}\licenses"; DestName: "NVDA-ControllerClient-LGPL-2.1.txt"; Flags: ignoreversion
Source: "..\vendor\nvda\README.md";                  DestDir: "{app}\licenses"; DestName: "NVDA-ControllerClient-NOTICE.md";   Flags: ignoreversion
```

`build.bat` and `.github/workflows/build-release.yml` may also need updating if they reference the onefile output path. Check `dist/Hearthkin.exe` → `dist/Hearthkin/Hearthkin.exe` and the installer's `[Files]` path adjustments above.

### 3. Remove the `[Run]` post-install auto-launch from `Hearthkin.iss`

Inno's documentation explicitly recommends NOT auto-launching apps that use `CloseApplications=yes` Restart Manager integration. The auto-launch races against RM's "still finishing up" state and can fight with any leftover stub processes.

```ini
; Hearthkin.iss — remove or comment out:
; [Run]
; Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
```

The user can launch from Start Menu or desktop shortcut after install completes. Minor UX cost; structural safety win.

### 4. Defensive: kill any existing Hearthkin process before file replacement

In case future regressions ever leave a stub around, add an `[Code]` section to `Hearthkin.iss` that runs `taskkill /F /IM Hearthkin.exe` in `InitializeSetup`:

```pascal
[Code]
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  // Make sure no stale Hearthkin.exe holds the install dir locked. RM
  // can't kill PyInstaller bootloader stubs (no message loop); taskkill
  // can. Silent failure is fine — if nothing's running, the call no-ops.
  Exec('taskkill', '/F /IM Hearthkin.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := True;
end;
```

This is the belt-and-suspenders defense if steps 1-3 don't catch every edge case.

## Test plan after implementing

1. Build locally with `build.bat`. Verify `dist/Hearthkin/Hearthkin.exe` exists (not `dist/Hearthkin.exe`) and the directory has the Python DLL + packaged libs.
2. Install on a clean Windows VM if available, otherwise the user's machine after a reboot.
3. Verify Hearthkin launches and runs normally.
4. While Hearthkin is running, run the installer over it (same version, just to test the close behavior). It should close cleanly with no RM event 10006.
5. Manually kill `Hearthkin.exe` with `taskkill /F` to simulate a stuck stub, then run the installer — step 4's `InitializeSetup` should clear it and proceed.
6. NVDA verification: install with NVDA running, confirm the install completes audibly and the installed app launches with NVDA support intact.

## User context for whoever picks this up

- **User is non-coder.** They run the installer; they don't run `build.bat`. Walk them through behavior changes plainly; don't drop into diff-talk.
- **User uses NVDA.** All install / launch flows must be accessible. NVDA Controller Client DLL must stay alongside `Hearthkin.exe` in the install dir (LGPL 2.1 compliance — it's vendored at `vendor/nvda/nvdaControllerClient64.dll` and shipped external to the .exe so users can swap it).
- **User was exhausted by the time we wrote this.** Today (2026-05-18) had ~10 releases (v0.2.13 through v0.2.20) and several real model-stability fixes alongside the failed installer fixes. Be gentle; check the user's energy before piling on. The build-pipeline change is the right fix but it's invasive (PyInstaller mode change, installer rewrite, CI build verification). Don't ship it without a fresh test cycle.
- **Right-now state of user's machine:** stuck `Hearthkin.exe` PID 26612 (or whatever lingers after a reboot). Reboot recommended. After reboot, v0.2.20 should install via existing mechanism. The build-pipeline fix is for the NEXT release, not a desperately needed v0.2.21 hotfix.

## Won't-fix-here notes

- **Auto-update direct-download URL**: I'd flagged separately that the auto-update check opens the GitHub Releases HTML page in a browser (NVDA-noisy). Bundling that into the same release as this installer fix would make sense — `webbrowser.open` in `hearthkin.pyw` should hit `https://github.com/glasswings-lang/hearthkin/releases/download/v{version}/Hearthkin-Setup-{version}.exe` directly. Two lines.
- **Build size / startup time**: onedir mode produces a directory rather than a single .exe in the install dir. About the same disk usage post-install; same launch perf as today minus the extraction step (so actually FASTER startup).
- **NVDA**: the controller DLL placement is unchanged; LGPL compliance unchanged.

## Files touched in the proposed fix

- `Hearthkin.spec` — PyInstaller mode (onefile → onedir), `upx=False`
- `installer/Hearthkin.iss` — `[Files]` to bundle dir, remove `[Run]`, add `[Code]` InitializeSetup
- Possibly `build.bat` and `.github/workflows/build-release.yml` if they reference `dist/Hearthkin.exe` specifically

## Worktree context

Work-in-progress branch this session: `claude/nostalgic-kalam-dca210`. Already merged into master through v0.2.20. The worktree directory is `.claude/worktrees/nostalgic-kalam-dca210/`. Safe to delete the worktree if/when no further work is planned on that branch — all commits are on master and origin.
