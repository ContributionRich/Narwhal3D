#!/usr/bin/env python3
"""Public contract checks for the privacy-conscious Windows build collector."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "collect_windows_build_info.ps1"
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

print(f"\n{checks}/{checks} Windows collector checks passed")
