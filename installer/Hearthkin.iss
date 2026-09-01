; SPDX-License-Identifier: CC0-1.0
;
; Hearthkin installer script for Inno Setup 6.
; Build with the Inno Setup Compiler (iscc.exe) — typically:
;
;     iscc installer\Hearthkin.iss
;
; Produces dist\Hearthkin-Setup-<version>.exe.
;
; Prerequisites (run before invoking iscc):
;   1. python -m PyInstaller --noconfirm Hearthkin.spec
;        — produces dist\Hearthkin\Hearthkin.exe + the supporting
;          _internal\ subdirectory (onedir mode, NOT onefile — see the
;          Hearthkin.spec comment and installer/INSTALLER_REWRITE.md
;          for why)
;   2. python scripts\bundle_licenses.py
;        — produces licenses\ with one .txt per bundled dependency
;
; The build.bat at the repo root runs all three steps in order.

#define MyAppName       "Hearthkin"
; Version is read from the HEARTHKIN_VERSION env var so the workflow
; can derive it from the git tag (stripping the leading 'v') without
; us having to bump a hardcoded constant on every release. Local
; builds without the env var get a 0.0.0-dev marker so the installer
; file name makes its origin obvious.
#define MyAppVersion GetEnv("HEARTHKIN_VERSION")
#if MyAppVersion == ""
  #define MyAppVersion "0.0.0-dev"
#endif
#define MyAppPublisher  "Glasswings"
#define MyAppURL        "https://github.com/glasswings-lang/hearthkin"
#define MyAppExeName    "Hearthkin.exe"

[Setup]
AppId={{1AC7B3F4-D8B6-4F3F-B6B3-5C5E6F7A8B9D}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppPublisher}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
PrivilegesRequired=admin
OutputDir=..\dist
OutputBaseFilename=Hearthkin-Setup-{#MyAppVersion}
SetupIconFile=..\Hearthkin.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

; Restart Manager integration. When the user runs the installer over
; an existing install while Hearthkin is running, Inno Setup detects
; the file lock on Hearthkin.exe via the Windows Restart Manager API
; and offers to close the running instance automatically. After the
; install finishes, RestartApplications relaunches it.
;
; Without these, the installer either fails silently or pops a
; "file in use" prompt the user has to resolve manually by hand.
CloseApplications=yes
RestartApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startmenu";   Description: "Add &Start Menu shortcuts";        GroupDescription: "Additional icons:"
Name: "desktopicon"; Description: "Create a &desktop shortcut";       GroupDescription: "Additional icons:"; Flags: unchecked
Name: "startupicon"; Description: "Start Hearthkin when &Windows starts"; GroupDescription: "Auto-start:";  Flags: unchecked

[Files]
; PyInstaller onedir output — Hearthkin.exe plus the _internal\ subdir
; containing the Python runtime and packaged modules. The recursesubdirs
; flag walks the whole tree; createallsubdirs makes the _internal/ tree
; on the destination side. This replaced an earlier onefile setup that
; was getting bootloader stubs stuck mid-extraction under AV scanning
; and blocking subsequent installs — see installer/INSTALLER_REWRITE.md.
Source: "..\dist\Hearthkin\*";     DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\Hearthkin.ico";        DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE";              DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md";            DestDir: "{app}"; Flags: ignoreversion
; Hearthkin.spec bundles only user-facing docs (user-guide.html,
; kin_manual.md, troubleshooting.md) into _internal\docs\ — they ship
; via the dist line above. Internal design/ and planning/ docs are NOT
; bundled. The [Icons] user-guide shortcut targets {app}\_internal\docs\.
Source: "..\licenses\*";           DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

; NVDA Controller Client (LGPL 2.1, unmodified from NV Access).
; Shipped EXTERNAL to Hearthkin.exe — placed next to it in the install
; dir — so end users can replace the file with their own LGPL-compatible
; build. This is the LGPL 2.1 §6(b) "suitable shared library mechanism"
; requirement; do NOT move this into PyInstaller's binaries=[] in
; Hearthkin.spec (that would bury the DLL under _internal/ where users
; can't trivially find or replace it).
;
; The full LGPL 2.1 license text ships alongside the other third-party
; license files in {app}\licenses\ as NVDA-ControllerClient-LGPL-2.1.txt.
; The vendor README (attribution, source pointer, written offer) ships
; as NVDA-ControllerClient-NOTICE.md in the same dir.
Source: "..\vendor\nvda\nvdaControllerClient64.dll"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\vendor\nvda\license.txt";                DestDir: "{app}\licenses"; DestName: "NVDA-ControllerClient-LGPL-2.1.txt"; Flags: ignoreversion
Source: "..\vendor\nvda\README.md";                  DestDir: "{app}\licenses"; DestName: "NVDA-ControllerClient-NOTICE.md";   Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}";              Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\Hearthkin.ico"; Tasks: startmenu
; PyInstaller 6 onedir places datas under _internal\; the user guide
; lives at {app}\_internal\docs\user-guide.html — NOT {app}\docs\.
; (In-app Help → User guide also works via __file__-relative lookup.)
Name: "{group}\{#MyAppName} user guide";   Filename: "{app}\_internal\docs\user-guide.html";                        Tasks: startmenu
Name: "{group}\Uninstall {#MyAppName}";    Filename: "{uninstallexe}";                                              Tasks: startmenu
Name: "{commondesktop}\{#MyAppName}";      Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\Hearthkin.ico"; Tasks: desktopicon

[Registry]
; NOTE: This installer runs with admin privileges (PrivilegesRequired=admin).
; Under UAC elevation, HKCU refers to the ADMINISTRATOR's hive, NOT the
; standard (non-elevated) user who launched the installer. This means the
; Run entry lands in the wrong hive on most setups and the auto-start will
; not fire for the actual logged-in user.
;
; windows_startup.py (ships with Hearthkin) writes the SAME Run key via
; winreg.HKEY_CURRENT_USER from within the running (non-elevated) app,
; which always hits the correct hive. The Preferences → "Start with Windows"
; toggle uses that module, so the in-app toggle is the correct way to
; enable auto-start.
;
; This [Registry] entry is intentionally REMOVED to avoid writing to the
; wrong hive from an elevated process. The installer task "startupicon" is
; kept for the user-visible checkbox text, but no corresponding registry
; action is emitted — the user should use Preferences instead.
;
; (Reference: Inno Setup docs "Elevated installation: HKCU entries";
; Raymond Chen's "The HKCU hive is not the current user's hive when running
; elevated" — 2016-06-29.)

; [Run] used to auto-launch Hearthkin.exe at the end of install with
; the standard "postinstall skipifsilent" flags. Removed deliberately:
; Inno's docs explicitly recommend NOT auto-launching apps that use
; CloseApplications=yes Restart Manager integration, because the launch
; races against RM's "still finishing up" state and can collide with
; any leftover bootloader processes that didn't exit cleanly. The user
; can launch from the Start Menu / desktop shortcut after the wizard
; completes — minor UX cost, structural safety win.

[InstallDelete]
; Remove the entire _internal\ tree before reinstalling. PyInstaller's
; onedir output changes between releases (renamed/removed .pyd files,
; updated packages). Without this, stale files from a previous install
; accumulate and can shadow newer ones, producing subtle runtime failures.
; The [Files] section's ignoreversion flag handles the new files; this
; handles the deletes.
;
; {app}\* static files (Hearthkin.exe, icon, LICENSE, etc.) are overwritten
; normally by [Files]; only _internal\ needs an explicit sweep.
Type: filesandordirs; Name: "{app}\_internal"
; Old-layout orphan: a pre-onedir installer placed the docs at {app}\docs\.
; The current build ships them under {app}\_internal\docs\ and no longer
; writes {app}\docs\, so an upgrade left the OLD docs behind there — stale
; enough to still say "~/.hearthkin/agents/" (pre-`kin/`-rename). Sweep it.
Type: filesandordirs; Name: "{app}\docs"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  // Kill any stale Hearthkin.exe after the user has confirmed the install
  // (this runs post-wizard-confirm, pre-file-copy). Restart Manager
  // (CloseApplications=yes) handles a cleanly-running Hearthkin via
  // WM_QUERYENDSESSION, but it can't kill a PyInstaller bootloader stub
  // that never reached Python (no message loop, no top-level window).
  // taskkill /F /IM is the fallback for that edge case.
  //
  // Tradeoff: /F /IM matches any process named Hearthkin.exe system-wide,
  // not just the one under {app}. This is acceptable for a desktop app
  // that isn't normally multi-installed; a portable copy running from a
  // different directory will also be killed. If that's a concern in future,
  // switch to taskkill /F /PID after reading the PID from a lock file under
  // {app}.
  //
  // Belt-and-suspenders against the bootloader-stuck regression diagnosed
  // in installer/INSTALLER_REWRITE.md. The onedir switch makes this
  // scenario unlikely, but keeping the kill costs nothing.
  //
  // Moved from InitializeSetup (which ran pre-confirmation) to here so
  // the user can cancel without their running Hearthkin being killed.
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM Hearthkin.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  // Return empty string = proceed with install. (PrepareToInstall is an
  // event FUNCTION returning String, not a procedure — a non-empty return
  // aborts the install and shows the string as the error. Inno 6.7 rejects
  // the procedure form outright: "Invalid prototype for 'PrepareToInstall'".)
  Result := '';
end;

[UninstallDelete]
; Hearthkin writes per-user data to ~/.hearthkin/. Don't touch it on
; uninstall — that's the user's data, not ours, and reinstalling
; should pick it up again. Same for ~/.ai_programs/ (API keys).
Type: files; Name: "{app}\Hearthkin.ico"
Type: files; Name: "{app}\nvdaControllerClient64.dll"
