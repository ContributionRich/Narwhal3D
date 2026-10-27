# Windows build preparation

Narwhal 3D's published preview is an unsigned **0.4.6 Windows x86_64 portable ZIP** for early testing. It contains a native MSVC-built runtime, Windows CPython/OpenCASCADE dependencies, Rust native components, the native startup welcome window, and `Narwhal3D.exe`; it is not a renamed or wrapped Linux binary. Extract the complete folder and keep `Narwhal3D.exe` beside its `app` and `runtime` directories.

The ZIP remains an extremely early prerelease, not a supported Windows release. Native Windows headless checks and independent offline Wine checks passed for the published 0.4.6 ZIP, but physical-GPU/real-window acceptance, remaining internal runtime PE identity cleanup, installer/file associations, and optional signing are still open. SmartScreen may warn because the preview is unsigned.

**0.4.7 is in preparation**, not available to download or certified by the 0.4.6 results. Its updated native runtime and welcome, final integrated tests, source/inventory verification and extracted-ZIP acceptance are still pending.

## Reproducing the Windows build environment

Download [`scripts/collect_windows_build_info.ps1`](../scripts/collect_windows_build_info.ps1) onto the Windows desktop. Open PowerShell in that folder. In the PowerShell window already open, run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\collect_windows_build_info.ps1
```

Alternatively, start a specific host explicitly:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\collect_windows_build_info.ps1
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File .\collect_windows_build_info.ps1
```

Administrator rights are not required. To omit the HTTPS reachability checks, append `-SkipNetworkChecks` to any invocation.

The script creates these beside the user's normal Desktop files:

```text
Narwhal3D-Windows-Collection-YYYYMMDD-HHMMSS-milliseconds-random\
Narwhal3D-Windows-Collection-YYYYMMDD-HHMMSS-milliseconds-random.zip
```

The folder contains a readable report, a JSON report, sharing instructions, and the collector's SHA-256. It does not build software, install packages, request elevation, or upload anything.

Review the generated files before sharing the ZIP.

## Information collected

Only build-relevant information is recorded:

- Windows version, build, and architecture;
- 64-bit PowerShell status;
- CPU core/thread counts and total RAM;
- GPU names, driver versions, and reported adapter memory;
- free space on the system drive;
- Windows long-path status;
- Visual Studio installations containing the MSVC x64 C++ tools;
- Windows SDK and Developer Command Prompt status;
- availability and versions of Git, CMake, Ninja, Python, MSVC, Rust, Cargo, and Inno Setup;
- HTTPS reachability for the exact Narwhal/Blender revisions and Windows dependency wheels.

Paths beneath the user profile and occurrences of the Windows user name are redacted. The collector deliberately does not gather computer/user names, serial numbers, IP or MAC addresses, Wi-Fi details, complete environment variables, registry exports, personal files, browser data, or CAD documents.

## Prepare a machine with no build tools

When the report shows no native toolchain, install these from their official installers or Windows Package Manager, reviewing each installer normally:

1. **Visual Studio 2022 Build Tools or Community** with **Desktop development with C++**, MSVC v143, a current Windows 10/11 SDK, and CMake tools.
2. **Git for Windows**, **CMake**, **Ninja**, **Python 3.13 x64**, and **Subversion**.
3. Enable Windows long-path support and Git `core.longpaths`.
4. Restart Windows so the SDK, compiler, and PATH changes are visible.

Rust 1.95.0 is required by the current build script for the bundled document, converter, Grid Snap, native Explorer-dialog, and exact geometry components. Inno Setup remains optional until installer work begins. Automated package installation is deliberately deferred because Windows Package Manager cannot reliably add a missing Visual Studio workload to every already-installed/current Build Tools configuration.

After restarting, run the collector again and return the new collection ZIP.

## Build a new engineering candidate

The fresh report determines the exact bootstrap route and prevents repeatedly asking the Windows user to install or diagnose tools by hand. The example below targets a 0.4.7 engineering candidate, not an already validated release; supply the audited public source revision containing the 0.4.7 changes.

When Visual Studio C++, Git, Git LFS, CMake, Ninja, Python 3.13, long paths, free disk, and a pagefile are ready, download [`scripts/build_windows_preview.ps1`](../scripts/build_windows_preview.ps1) to the Desktop and run from ordinary 64-bit PowerShell:

```powershell
Set-Location ([Environment]::GetFolderPath('Desktop'))
$Revision = "PASTE_THE_40_CHARACTER_PUBLIC_SOURCE_COMMIT"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_windows_preview.ps1 `
  -NarwhalRevision $Revision `
  -Version 0.4.7
```

The engineering build uses the dedicated no-space path `C:\Narwhal3D-build`, resets only clones owned by that marked workspace, and can be rerun after a failure. It follows Blender's official Windows flow from the Blender source directory, but separates configuration and compilation so Ninja is limited to four jobs on the measured 12 GiB machine. It:

1. fetches the explicitly supplied 40-character Narwhal public source revision and the pinned Blender revision, then verifies both origins;
2. downloads Blender's matching Windows libraries through `make.bat update`;
3. applies every anchor-checked Narwhal native patch;
4. configures Visual Studio 2022 x64 Release with Ninja and builds one deterministic output directory;
5. installs SHA-256-locked CPython 3.13 Windows wheels for `rhino3dm` and Open CASCADE into the copied runtime;
6. builds the pure/Grid Snap, selection, native Explorer-dialog, and exact geometry cores with MSVC against pinned inputs;
7. proves native Save/Open, Grid Snap, platform ABI/imports, and an airtight Rust BinTools geometry move before packaging;
8. asserts packaged paths, version banner and dependency versions, then runs the packaged bootstrap headlessly;
9. creates, inventories, and hashes an atomic engineering ZIP on the Desktop;
10. always creates a small reviewable Desktop result ZIP when a phase fails.

Return both ZIP files named by the script. Its large ZIP is an engineering candidate until `scripts/verify_windows_release.py` and the release acceptance checks pass. The published 0.4.6 portable ZIP demonstrates the packaging route, but future candidates still require source/inventory verification and real Windows testing; installer/file associations, complete internal PE identity replacement, physical-GPU gates, and optional signing remain open.

Code signing is a separate release credential. An unsigned preview can be tested, but Windows SmartScreen may warn when it is launched.

## CorelDRAW boundary

Narwhal's checked built-in codec handles the supported CDR v15 subset without external software. Pinned TraceDraw Rust crates are compiled into Narwhal's bundled helper for CDR v16-v18 Import/Open and opt-in v17 Export/Print. Unsupported generations and structures may use an explicitly configured local CLI fallback; malformed files are refused rather than silently changing parsers. The collector may probe a legacy TraceDraw evaluation URL for reachability, but that mutable nightly is not downloaded and is not treated as release provenance or a release input.
