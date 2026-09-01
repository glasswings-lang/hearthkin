# NVDA Controller Client (bundled)

This directory holds the unmodified NVDA Controller Client DLL, redistributed
with Hearthkin under the terms of the LGPL 2.1. It lets Hearthkin send speech
to a running NVDA screen reader without depending on a system-wide NVDA install
being discoverable at one of the hardcoded paths the previous loader used.

## What's here

| File | Purpose |
|------|---------|
| `nvdaControllerClient64.dll` | 64-bit DLL, shipped alongside `Hearthkin.exe` in the installer and looked up by `audio.py` for `python hearthkin.pyw` dev runs. |
| `nvdaControllerClient32.dll` | 32-bit DLL, kept for symmetry with the upstream distribution. Hearthkin's PyInstaller build is x64, so this file isn't loaded in normal use — it's here so a 32-bit local build still works. |
| `license.txt` | Verbatim LGPL 2.1 license text from NV Access's `controllerClient.zip`. **Do not edit.** |
| `NV_ACCESS_UPSTREAM_README.md` | The upstream NV Access readme, kept verbatim for attribution. **Do not edit.** |

## Source / provenance

- **Upstream:** [NV Access — NVDA](https://github.com/nvaccess/nvda) (controller client lives in `extras/controllerClient/`)
- **Release artifact:** `nvda_2026.1_controllerClient.zip` from <https://download.nvaccess.org/releases/stable/>
- **Version pulled:** NVDA 2026.1 (release dated 2026-05-05)
- **Date vendored into Hearthkin:** 2026-05-15

To update: download the latest `*_controllerClient.zip` from the NV Access
stable releases directory and replace `x64/nvdaControllerClient.dll` →
`nvdaControllerClient64.dll`, `x86/nvdaControllerClient.dll` →
`nvdaControllerClient32.dll`, plus `license.txt` and `NV_ACCESS_UPSTREAM_README.md`
if they changed. Bump the "Version pulled" line above.

## License

The NVDA Controller Client is licensed under the **GNU Lesser General Public
License, version 2.1** (LGPL 2.1). The full license text is in `license.txt`
in this directory.

In plain terms (NV Access's own summary, from the upstream readme):

> You can use this library in any application, but if you modify the library
> in any way, you must contribute the changes back to the community under the
> same license.

Hearthkin uses the DLL **unmodified** and loads it dynamically at runtime via
`ctypes.windll.LoadLibrary` — this is "use of the Library" under LGPL 2.1,
not derivation of Hearthkin from it. The DLL is shipped alongside
`Hearthkin.exe`, not embedded inside it, so end users can replace it with a
modified build of their own (LGPL 2.1 §6(b) "suitable shared library
mechanism" requirement).

## Compliance summary

For each install of Hearthkin that includes the bundled DLL:

1. **License text shipped** — `license.txt` is copied into the installed
   `licenses\` directory as `NVDA-ControllerClient-LGPL-2.1.txt`. LGPL 2.1 §6(f)
   requirement.
2. **Notice that the library is used** — `vendor/nvda/README.md` (this file)
   plus an entry in Hearthkin's Preferences → Connections / About surface.
   LGPL 2.1 §6 cover page requirement.
3. **Source available** — The DLL is unmodified upstream code, and NV Access
   publishes the source openly at <https://github.com/nvaccess/nvda> (and the
   built artifacts at <https://download.nvaccess.org/releases/stable/>).
   Anyone redistributing Hearthkin can satisfy LGPL 2.1 §6(a)–(c) by pointing
   recipients at those URLs. As a belt-and-suspenders measure, Hearthkin's
   own README and About box also include this written offer:
   > **Written offer (LGPL 2.1 §6(c)):** For the source code corresponding
   > to the bundled `nvdaControllerClient64.dll` (NVDA 2026.1), open an issue
   > at <https://github.com/glasswings-lang/hearthkin/issues>, or download
   > directly from <https://github.com/nvaccess/nvda> (the canonical upstream).
4. **User can replace the DLL** — The file lives in the install directory
   (e.g. `C:\Program Files\Glasswings\Hearthkin\nvdaControllerClient64.dll`)
   and is loaded by absolute path. Replacing the file with a compatible build
   is sufficient — no recompile of Hearthkin needed. LGPL 2.1 §6(b)
   requirement.

If you find a compliance gap in this setup, please open an issue.
