#!/usr/bin/env python3
"""Public contract checks for the privacy-conscious Windows build collector."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "collect_windows_build_info.ps1"
BUILD = ROOT / "scripts" / "build_windows_preview.ps1"
GUIDE = ROOT / "docs" / "windows-build.md"
README = ROOT / "README.md"

checks = 0


def check(condition: bool, label: str) -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition:
        raise AssertionError(label)


script = SCRIPT.read_text(encoding="utf-8")
build = BUILD.read_text(encoding="utf-8")
guide = GUIDE.read_text(encoding="utf-8")
readme = README.read_text(encoding="utf-8")

check("[CmdletBinding()]" in script and "[switch]$SkipNetworkChecks" in script,
      "collector is an ordinary parameterized PowerShell script")
check("[Environment]::GetFolderPath(\"Desktop\")" in script
      and "Narwhal3D-Windows-Collection-" in script
      and "Compress-Archive" in script,
      "collector creates a timestamped Desktop folder and ZIP")
check("071200ac982437bff148d8a213fd896bad204555" in script
      and "d13f752e3b9c4f8c261cda552b1021f8bcc0382c" in script
      and "48c2e408f11f976b64a3fbb59ce1826315bf3b7c" in script,
      "collector records the exact Narwhal, Blender, and TraceDraw evaluation revisions")
check("Win32_VideoController" in script and "driver_version" in script
      and "AdapterRAM" in script,
      "collector records GPU identity, driver, and reported memory")
check("Microsoft.VisualStudio.Component.VC.Tools.x86.x64" in script
      and "WindowsSdkDir" in script and "VCToolsInstallDir" in script,
      "collector identifies the Visual Studio C++ and Windows SDK toolchain")
check(all(f'Command-Info "{tool}"' in script for tool in
          ("git", "cmake", "ninja", "python", "cl", "rustc", "cargo", "iscc")),
      "collector checks every planned build and installer tool")
check("rhino3dm-8.35.0-cp313-cp313-win_amd64.whl" in script
      and "cadquery_ocp_novtk-8.0.1.0.0-cp313-cp313-win_amd64.whl" in script
      and "cadquery_ocp_proxy-8.0.1.0.0-py3-none-any.whl" in script,
      "collector probes the exact Windows CPython 3.13 geometry dependencies")
check("Invoke-WebRequest" in script and "-Method Head" in script
      and "Start-BitsTransfer" not in script
      and "[Net.SecurityProtocolType]::Tls12" in script,
      "dependency probes use TLS 1.2 HEAD requests and download no artifacts")
check("-CommandType Application" in script
      and "WaitForExit($TimeoutMilliseconds)" in script
      and "StandardOutputEncoding" in script
      and "ReadToEndAsync()" in script
      and "Microsoft\\\\WindowsApps" in script,
      "tool and UTF-8 vswhere probes reject aliases and have bounded execution time")
check("RegexOptions]::IgnoreCase" in script and "%UNC_HOST%" in script
      and "?\\\\UNC" in script and "$_.Exception.Message" not in script,
      "redaction is case-insensitive and free-form exception/ordinary/extended UNC details stay private")
check("GetEnvironmentVariable(\"ProgramFiles(x86)\")" in script
      and "[Guid]::NewGuid()" in script
      and "Compress-Archive -LiteralPath $ArchiveInputs" in script,
      "collector handles 32-bit environments, concurrent names, and literal Desktop paths")
check("uploaded = $false" in script and "paths_redacted = $true" in script
      and "%USERPROFILE%" in script and "%USERNAME%" in script,
      "report states local-only behavior and redacts user-profile paths")
for forbidden in ("Win32_BIOS", "Win32_BaseBoard", "SerialNumber", "ipconfig",
                  "Win32_NetworkAdapter", "$env:COMPUTERNAME", "Start-Process -Verb RunAs"):
    check(forbidden not in script, f"collector does not invoke privacy/elevation source {forbidden}")
check("Nothing was uploaded automatically" in script
      and "Review the generated files before sharing" in guide,
      "collector and guide require review before sharing")
check("scripts/collect_windows_build_info.ps1" in guide
      and "docs/windows-build.md" in readme
      and "pwsh.exe" in guide and "powershell.exe" in guide,
      "public guide and README link to the collector for both PowerShell hosts")
check("mutable nightly" in guide.lower() and "not downloaded" in guide.lower(),
      "guide keeps the TraceDraw evaluation artifact out of release provenance")
check("function Compiler-Info" in script and "VC\\Tools\\MSVC" in script
      and "Hostx64\\x64\\cl.exe" in script,
      "collector detects installed MSVC outside an ordinary PowerShell PATH")

check("C:\\Narwhal3D-build" in build and "\\s" in build
      and ".narwhal3d-windows-build-workspace" in build,
      "engineering build owns one conservative no-space workspace")
check("make_update.py" in build and '"--no-blender"' in build
      and '"update Blender libraries without changing Blender source" $BlenderSource' in build
      and "verify Blender pinned revision after libraries update" in build,
      "library update runs in the source without rebasing the pinned Blender checkout")
check("2022 x64 release ninja nobuild builddir" in build
      and "vcvars64.bat" in build and "run_ninja_with_msvc.cmd" in build
      and "-j $Jobs install" in build,
      "build forces VS 2022 x64 Release and bounds Ninja inside the MSVC environment")

check(all(text in build for text in ("git", "lfs", "LongPathsEnabled", "core.longpaths",
                                      "Win32_PageFileUsage", "At least 60 GiB")),
      "engineering build preflights source, long paths, disk, and low-memory pagefile")

check('"reset", "--hard", $NarwhalRevision' in build
      and '"reset", "--hard", $BlenderRevision' in build
      and '"clean", "-ffd"' in build and "remote\", \"get-url" in build,
      "only script-owned clones are reset and their origins/revisions are verified")
check("6acffcc7493d35d59b25ee8843b28c2b16b605aa072567e112ee7e5ae12b642e" in build
      and "a9eedaa4e549ac2eb4be9b1cb85072497263ae663fae582203966086f9926e04" in build
      and "d6a6909a73d2300ca4581ff762ccf0137993ac75c57a2dc82e12bffa3a12b680" in build,
      "engineering build pins all Windows geometry-wheel hashes")
check("run packaged headless bootstrap" in build
      and "verify_bundled_geometry.py" in build
      and "Windows-compatible native branding source smoke test" in build
      and "Packaged dependency versions do not match" in build
      and "Packaged executable has the wrong version banner" in build,
      "engineering output is validated through Windows-safe copied-package probes")
check("[switch]$ResumePackaging" in build and "Resuming packaging" in build
      and "abort interrupted Blender rebase" in build,
      "owner-measured fixes support packaging resume and interrupted-update recovery")
check(".part.zip" in build and 'Capture-Native $Tar @("-tf"' in build
      and "Archive is missing" in build,
      "engineering archive is written temporarily and its required entries are inventoried")
check("trap {" in build and "failed_phase" in build
      and "README-BEFORE-SHARING.txt" in build and "uploaded = $false" in build,
      "failed builds always leave a private reviewable result bundle")
check("engineering artifact, not a distributable release" in build
      and "Windows PE identity" in build and "physical-GPU acceptance" in build,
      "script does not overclaim the returned engineering runtime")
check("build_windows_preview.ps1" in guide,
      "Windows guide links and bounds the engineering build script")

print(f"\n{checks}/{checks} Windows collector/build checks passed")
