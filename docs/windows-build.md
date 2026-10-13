# Windows build preparation

Narwhal 3D does not yet publish a Windows executable. A real Windows release requires a native Windows Blender build, Windows CPython/OCCT dependencies, a Windows launcher and installer, and validation on physical Windows graphics hardware. Renaming or wrapping the Linux executable is not acceptable.

## First step: collect the build environment

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

Rust and Inno Setup are not needed for the first native compile; they can be added later for a reproducible TraceDraw build and installer packaging. Automated package installation is deliberately deferred because Windows Package Manager cannot reliably add a missing Visual Studio workload to every already-installed/current Build Tools configuration.

After restarting, run the collector again and return the new collection ZIP.

## What happens after the prepared-machine report

The fresh report determines the exact bootstrap route and prevents repeatedly asking the Windows user to install or diagnose tools by hand.

When Visual Studio C++, Git, Git LFS, CMake, Ninja, Python 3.13, long paths, free disk, and a pagefile are ready, download [`scripts/build_windows_preview.ps1`](../scripts/build_windows_preview.ps1) to the Desktop and run from ordinary 64-bit PowerShell:

```powershell
Set-Location ([Environment]::GetFolderPath('Desktop'))
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_windows_preview.ps1
```

The engineering build uses the dedicated no-space path `C:\Narwhal3D-build`, resets only clones owned by that marked workspace, and can be rerun after a failure. It follows Blender's official Windows flow from the Blender source directory, but separates configuration and compilation so Ninja is limited to four jobs on the measured 12 GiB machine. It:

1. fetches Narwhal 0.4.0 and Blender at pinned commits and verifies their origins;
2. downloads Blender's matching Windows libraries through `make.bat update`;
3. applies every anchor-checked Narwhal native patch;
4. configures Visual Studio 2022 x64 Release with Ninja and builds one deterministic output directory;
5. installs SHA-256-locked CPython 3.13 Windows wheels for `rhino3dm` and Open CASCADE into the copied runtime;
6. asserts the packaged paths, Narwhal version banner and dependency versions, then runs the packaged bootstrap headlessly;
7. creates and inventories an atomic engineering ZIP on the Desktop;
8. always creates a small reviewable Desktop result ZIP when a phase fails.

Return both ZIP files named by the script. The large ZIP is an engineering runtime, not a distributable release. It still needs Windows PE icon/AppUserModelID branding, complete package inventory/verification, real-window and physical-GPU gates, an installer, and optional signing.

Code signing is a separate release credential. An unsigned preview can be tested, but Windows SmartScreen may warn when it is launched.

## TraceDraw/CorelDRAW boundary

TraceDraw's stable `v0.4.0` reader is useful, but its writer was measured to ignore requested CDR v17/v18 output. A later commit corrects that behavior, but its current published Windows artifact is a mutable nightly. The collector checks that evaluation URL only to establish whether the Windows machine can reach it; the nightly is not downloaded and must not be treated as a locked release input.

A bundled CorelDRAW converter waits for either an immutable TraceDraw release containing the writer correction or a reproducible build from a pinned source commit.
