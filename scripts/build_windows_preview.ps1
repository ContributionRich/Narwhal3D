# Build a native Narwhal 3D Windows x86_64 preview and place return artifacts on Desktop.
# Run from ordinary 64-bit PowerShell after the collector reports the toolchain ready:
# To skip an already successful compilation, use -ResumePackaging.
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_windows_preview.ps1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$SelectionCoreArtifacts,
    [string]$Workspace = "C:\Narwhal3D-build",
    [ValidateRange(1, 8)][int]$Jobs = 4,
    [switch]$ResumePackaging
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$NarwhalRevision = "874586bdbcf5cc79ffef7b2ebe33ca69802533f3"
$BlenderRevision = "d13f752e3b9c4f8c261cda552b1021f8bcc0382c"
$NarwhalRepository = "https://github.com/ContributionRich/Narwhal3D.git"
$BlenderRepository = "https://projects.blender.org/blender/blender.git"
$WheelRecords = @(
    [ordered]@{
        name = "rhino3dm"
        filename = "rhino3dm-8.35.0-cp313-cp313-win_amd64.whl"
        url = "https://files.pythonhosted.org/packages/de/17/735bc8a95a5667b091c7e8f77e8ba54871cbdd99569609f72f01bb7789e9/rhino3dm-8.35.0-cp313-cp313-win_amd64.whl"
        sha256 = "6acffcc7493d35d59b25ee8843b28c2b16b605aa072567e112ee7e5ae12b642e"
    }
    [ordered]@{
        name = "cadquery-ocp-novtk"
        filename = "cadquery_ocp_novtk-8.0.1.0.0-cp313-cp313-win_amd64.whl"
        url = "https://files.pythonhosted.org/packages/d7/b9/e350209cc87210df5f989ffc5d19cae8c499d69bca9cb36ec770be6cd9c7/cadquery_ocp_novtk-8.0.1.0.0-cp313-cp313-win_amd64.whl"
        sha256 = "a9eedaa4e549ac2eb4be9b1cb85072497263ae663fae582203966086f9926e04"
    }
    [ordered]@{
        name = "cadquery-ocp-proxy"
        filename = "cadquery_ocp_proxy-8.0.1.0.0-py3-none-any.whl"
        url = "https://files.pythonhosted.org/packages/15/36/4a8dbfdf4ab61a7841c36398615c6ab3d3c932d4254b978963690650feea/cadquery_ocp_proxy-8.0.1.0.0-py3-none-any.whl"
        sha256 = "d6a6909a73d2300ca4581ff762ccf0137993ac75c57a2dc82e12bffa3a12b680"
    }
)

function Redact([object]$Value) {
    if ($null -eq $Value) { return $null }
    $Text = [string]$Value
    foreach ($Secret in @($env:USERPROFILE, $env:USERNAME)) {
        if ($Secret) {
            $Text = [regex]::Replace($Text, [regex]::Escape($Secret), "%USER%",
                [Text.RegularExpressions.RegexOptions]::IgnoreCase)
        }
    }
    return $Text
}

if (-not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess) {
    throw "The Windows preview build requires 64-bit Windows and 64-bit PowerShell."
}
if ($Workspace -match "\s") {
    throw "The Blender Windows build path cannot contain spaces: $Workspace"
}

$Desktop = [Environment]::GetFolderPath("Desktop")
if (-not $Desktop) { throw "Windows did not provide a Desktop folder." }
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$RunSuffix = [Guid]::NewGuid().ToString("N").Substring(0, 8)
$ReturnDirectory = Join-Path $Desktop ("Narwhal3D-Windows-Build-" + $Stamp + "-" + $RunSuffix)
New-Item -ItemType Directory -Path $ReturnDirectory -Force | Out-Null
$LogPath = Join-Path $ReturnDirectory "windows-build.log"
$ResultPath = Join-Path $ReturnDirectory "windows-build-result.json"
Set-Content -LiteralPath $LogPath -Value "" -Encoding UTF8

function Log([string]$Message) {
    $Line = (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "  " + (Redact $Message)
    Write-Host $Line
    Add-Content -LiteralPath $LogPath -Value $Line -Encoding UTF8
}

$script:Phase = "initialization"
trap {
    $FailureMessage = Redact $_.Exception.Message
    try {
        Log ("FAILED phase " + $script:Phase + ": " + $FailureMessage)
        $FailureResult = [ordered]@{
            schema = 1; product = "Narwhal 3D"; target = "windows-x86_64-engineering"
            completed = $false; failed_phase = $script:Phase; error = $FailureMessage
            narwhal_revision = $NarwhalRevision; blender_revision = $BlenderRevision
            uploaded = $false
        }
        $FailureResult | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $ResultPath -Encoding UTF8
        @"
The engineering build did not complete. Nothing was uploaded automatically.
Review windows-build.log and windows-build-result.json before sharing this ZIP.
The C:\Narwhal3D-build workspace is resumable; run the same script again after
the reported problem is corrected.
"@ | Set-Content -LiteralPath (Join-Path $ReturnDirectory "README-BEFORE-SHARING.txt") -Encoding UTF8
        $FailureZip = $ReturnDirectory + ".zip"
        $FailureInputs = @(Get-ChildItem -LiteralPath $ReturnDirectory -File | ForEach-Object { $_.FullName })
        if ($FailureInputs) {
            Compress-Archive -LiteralPath $FailureInputs -DestinationPath $FailureZip -CompressionLevel Optimal -Force
        }
    } catch { }
    Write-Host ("Build stopped during " + $script:Phase + ". Return the Desktop build ZIP.") -ForegroundColor Red
    exit 2
}

function Run-Native([string]$Program, [string[]]$Arguments, [string]$Purpose,
                    [string]$WorkingDirectory = "") {
    $script:Phase = $Purpose
    Log ("START " + $Purpose + ": " + $Program + " " + ($Arguments -join " "))
    $PriorPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    if ($WorkingDirectory) { Push-Location -LiteralPath $WorkingDirectory }
    try {
        & $Program @Arguments 2>&1 | ForEach-Object {
            $Safe = Redact $_
            Write-Host $Safe
            Add-Content -LiteralPath $LogPath -Value $Safe -Encoding UTF8
        }
        $Code = $LASTEXITCODE
    } finally {
        if ($WorkingDirectory) { Pop-Location }
        $ErrorActionPreference = $PriorPreference
    }
    if ($Code -ne 0) { throw "$Purpose failed with exit code $Code" }
    Log ("DONE " + $Purpose)
}

function Capture-Native([string]$Program, [string[]]$Arguments, [string]$Purpose,
                        [string]$WorkingDirectory = "") {
    $script:Phase = $Purpose
    $PriorPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    if ($WorkingDirectory) { Push-Location -LiteralPath $WorkingDirectory }
    try {
        $Lines = @(& $Program @Arguments 2>&1 | ForEach-Object { [string]$_ })
        $Code = $LASTEXITCODE
    } finally {
        if ($WorkingDirectory) { Pop-Location }
        $ErrorActionPreference = $PriorPreference
    }
    foreach ($Line in $Lines) { Log $Line }
    if ($Code -ne 0) { throw "$Purpose failed with exit code $Code" }
    return $Lines
}

function Require-Application([string]$Name) {
    $Command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $Command -or -not $Command.Path) {
        throw "Required application is not available on PATH: $Name"
    }
    return $Command.Path
}

function Download-Locked([object]$Record, [string]$Directory) {
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    $Target = Join-Path $Directory $Record.filename
    if (Test-Path -LiteralPath $Target -PathType Leaf) {
        $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Target).Hash.ToLowerInvariant()
        if ($Actual -eq $Record.sha256) {
            Log ("Using verified cached wheel " + $Record.filename)
            return $Target
        }
        Remove-Item -LiteralPath $Target -Force
    }
    $Temporary = $Target + ".part"
    Remove-Item -LiteralPath $Temporary -Force -ErrorAction SilentlyContinue
    Log ("Downloading locked wheel " + $Record.filename)
    Invoke-WebRequest -Uri $Record.url -OutFile $Temporary -UseBasicParsing -TimeoutSec 300
    $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Temporary).Hash.ToLowerInvariant()
    if ($Actual -ne $Record.sha256) {
        Remove-Item -LiteralPath $Temporary -Force
        throw "SHA-256 mismatch for $($Record.filename): expected $($Record.sha256), got $Actual"
    }
    Move-Item -LiteralPath $Temporary -Destination $Target
    return $Target
}

$Git = Require-Application "git.exe"
$Python = Require-Application "python.exe"
$CMake = Require-Application "cmake.exe"
$Ninja = Require-Application "ninja.exe"
$Cmd = Require-Application "cmd.exe"
$Tar = Require-Application "tar.exe"
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }
Log "Narwhal 3D Windows native engineering build"
Log ("Workspace: " + $Workspace)
Log ("Parallel jobs: " + $Jobs)
$SelectionCoreArtifacts = [IO.Path]::GetFullPath($SelectionCoreArtifacts)
Log ("Selection core artifacts: " + $SelectionCoreArtifacts)
Log ("Git: " + $Git)
Log ("Python: " + $Python)
Log ("CMake: " + $CMake)
Log ("Ninja: " + $Ninja)
Run-Native $Git @("lfs", "version") "verify Git LFS"
$LongPathsEnabled = [int](Get-ItemPropertyValue -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled)
if ($LongPathsEnabled -ne 1) { throw "Windows long-path support is not enabled" }
$GitLongPaths = @(Capture-Native $Git @("config", "--system", "--get", "core.longpaths") "verify Git long paths")
if (-not $GitLongPaths -or $GitLongPaths[-1].Trim().ToLowerInvariant() -ne "true") {
    throw "Git system core.longpaths is not true"
}
$SystemDrive = Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='" + $env:SystemDrive + "'")
$FreeGiB = [math]::Round($SystemDrive.FreeSpace / 1GB, 2)
if ($FreeGiB -lt 60) { throw "At least 60 GiB free is required; found $FreeGiB GiB" }
$MemoryGiB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 2)
if ($MemoryGiB -lt 16 -and -not @(Get-CimInstance Win32_PageFileUsage -ErrorAction SilentlyContinue)) {
    throw "A Windows pagefile is required on machines with less than 16 GiB RAM"
}
Log ("Preflight: long paths enabled, free GiB=" + $FreeGiB + ", RAM GiB=" + $MemoryGiB)

$WorkspaceMarker = Join-Path $Workspace ".narwhal3d-windows-build-workspace"
if (Test-Path -LiteralPath $Workspace) {
    if (-not (Test-Path -LiteralPath $WorkspaceMarker -PathType Leaf)) {
        throw "Refusing existing unowned workspace: $Workspace"
    }
} else {
    New-Item -ItemType Directory -Path $Workspace | Out-Null
    Set-Content -LiteralPath $WorkspaceMarker -Value "Narwhal 3D owned build workspace" -Encoding ASCII
}

$NarwhalSource = Join-Path $Workspace "narwhal-source"
$BlenderSource = Join-Path $Workspace "blender-source"
$BuildDirectory = Join-Path $Workspace "blender-build"
$WheelCache = Join-Path $Workspace "wheels"
$PatchDirectory = Join-Path $NarwhalSource "blender_patches"
if (-not $ResumePackaging) {
if (-not (Test-Path -LiteralPath (Join-Path $NarwhalSource ".git") -PathType Container)) {
    Run-Native $Git @("clone", "--filter=blob:none", $NarwhalRepository, $NarwhalSource) "clone Narwhal source"
}
$NarwhalOrigin = @(Capture-Native $Git @("-C", $NarwhalSource, "remote", "get-url", "origin") "verify Narwhal origin")[-1].Trim()
if ($NarwhalOrigin -ne $NarwhalRepository) { throw "Unexpected Narwhal origin: $NarwhalOrigin" }
Run-Native $Git @("-C", $NarwhalSource, "fetch", "origin", $NarwhalRevision) "fetch pinned Narwhal revision"
Run-Native $Git @("-C", $NarwhalSource, "reset", "--hard", $NarwhalRevision) "reset script-owned Narwhal clone"
Run-Native $Git @("-C", $NarwhalSource, "clean", "-ffd") "clean script-owned Narwhal clone"

if (-not (Test-Path -LiteralPath (Join-Path $BlenderSource ".git") -PathType Container)) {
    Run-Native $Git @("clone", "--filter=blob:none", $BlenderRepository, $BlenderSource) "clone Blender source"
}
$BlenderOrigin = @(Capture-Native $Git @("-C", $BlenderSource, "remote", "get-url", "origin") "verify Blender origin")[-1].Trim()
if ($BlenderOrigin -ne $BlenderRepository) { throw "Unexpected Blender origin: $BlenderOrigin" }
# An interrupted previous make.bat update may have left a Git rebase in progress.
# Only touch the script-owned Blender checkout and only after the old build has stopped.
$RebaseMergePath = @(Capture-Native $Git @("-C", $BlenderSource, "rev-parse", "--git-path", "rebase-merge") "locate Blender rebase metadata")[-1].Trim()
$RebaseApplyPath = @(Capture-Native $Git @("-C", $BlenderSource, "rev-parse", "--git-path", "rebase-apply") "locate Blender apply metadata")[-1].Trim()
if (-not [IO.Path]::IsPathRooted($RebaseMergePath)) { $RebaseMergePath = Join-Path $BlenderSource $RebaseMergePath }
if (-not [IO.Path]::IsPathRooted($RebaseApplyPath)) { $RebaseApplyPath = Join-Path $BlenderSource $RebaseApplyPath }
if ((Test-Path -LiteralPath $RebaseMergePath) -or (Test-Path -LiteralPath $RebaseApplyPath)) {
    Log "An interrupted Blender rebase was detected; aborting it before restoring the pinned revision"
    Run-Native $Git @("-C", $BlenderSource, "rebase", "--abort") "abort interrupted Blender rebase"
}
Run-Native $Git @("-C", $BlenderSource, "fetch", "origin", $BlenderRevision) "fetch pinned Blender revision"
Run-Native $Git @("-C", $BlenderSource, "reset", "--hard", $BlenderRevision) "reset script-owned Blender clone"
Run-Native $Git @("-C", $BlenderSource, "clean", "-ffd") "clean script-owned Blender clone"

# Blender make.bat does not accept --no-blender as an argument.
# Call the pinned revision's dependency updater directly with --no-blender,
# so it cannot run an unwanted `git pull --rebase` on the source repository.
$LibraryDirectory = Join-Path $BlenderSource "lib\windows_x64"
$DependencyPython = Join-Path $LibraryDirectory "python\313\bin\python.exe"
$UpdateScript = Join-Path $BlenderSource "build_files\utils\make_update.py"
$UpdatePython = if (Test-Path -LiteralPath $DependencyPython -PathType Leaf) {
    $DependencyPython
} else {
    Log "Initializing Blender libraries with system Python; later runs use the pinned dependency Python"
    $Python
}
Run-Native $UpdatePython @("-B", $UpdateScript, "--git-command", $Git, "--no-blender") "update Blender libraries without changing Blender source" $BlenderSource
if (-not (Test-Path -LiteralPath $DependencyPython -PathType Leaf)) {
    throw "Blender libraries update did not provide dependency Python: $DependencyPython"
}
$ActualBlenderRevision = @(Capture-Native $Git @("-C", $BlenderSource, "rev-parse", "HEAD") "verify Blender pinned revision after libraries update")[-1].Trim()
if ($ActualBlenderRevision -ne $BlenderRevision) {
    throw "Blender checkout moved from pinned revision $BlenderRevision to $ActualBlenderRevision"
}
if (-not (Test-Path -LiteralPath $LibraryDirectory -PathType Container)) {
    throw "Blender libraries update did not produce lib\\windows_x64"
}

$PatchDriver = Join-Path $Workspace "apply_narwhal_patches.py"
@'
from __future__ import annotations
import importlib.util
from pathlib import Path
import sys

source = Path(sys.argv[1])
patch_dir = Path(sys.argv[2])

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load patch {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

branding = load("narwhal_branding", patch_dir / "apply_narwhal3d_branding.py")
changed = branding.apply(str(source), str(patch_dir / "narwhal3d_app_icon.svg"))
print(f"branding changed {len(changed)} path(s)")
for name in ("apply_app_drawn.py", "apply_cad_boundary_wires.py"):
    module = load("narwhal_" + name.replace(".py", ""), patch_dir / name)
    module.SRC = str(source / "source" / "blender")
    module.main()
'@ | Set-Content -LiteralPath $PatchDriver -Encoding UTF8
Run-Native $Python @($PatchDriver, $BlenderSource, $PatchDirectory) "apply Narwhal native patches"

if (Test-Path -LiteralPath $BuildDirectory) {
    Log "Removing prior build output while retaining downloaded Blender libraries"
    Remove-Item -LiteralPath $BuildDirectory -Recurse -Force
}
Run-Native $Cmd @("/d", "/s", "/c", "make.bat 2022 x64 release ninja nobuild builddir $BuildDirectory") "configure Blender 2022 x64 Release Ninja build" $BlenderSource
# Ninja must execute in the same MSVC Developer environment used by Blender's
# make.bat configure step. Environment changes inside make.bat cannot propagate
# back into PowerShell, so launch Ninja through vcvars64.bat explicitly.
$VsWhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
$VisualStudioRoot = ""
if (Test-Path -LiteralPath $VsWhere -PathType Leaf) {
    $VisualStudioRoot = @(& $VsWhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath | Select-Object -First 1)
    if ($VisualStudioRoot.Count -gt 0) { $VisualStudioRoot = [string]$VisualStudioRoot[0] }
    else { $VisualStudioRoot = "" }
}
if (-not $VisualStudioRoot) {
    foreach ($Edition in @("Community", "Professional", "Enterprise", "BuildTools")) {
        $Candidate = Join-Path "C:\Program Files\Microsoft Visual Studio\2022" $Edition
        if (Test-Path -LiteralPath (Join-Path $Candidate "VC\Auxiliary\Build\vcvars64.bat") -PathType Leaf) {
            $VisualStudioRoot = $Candidate
            break
        }
    }
}
if (-not $VisualStudioRoot) { throw "Visual Studio C++ build tools not found (vcvars64.bat)." }
$Vcvars64 = Join-Path $VisualStudioRoot "VC\Auxiliary\Build\vcvars64.bat"
if (-not (Test-Path -LiteralPath $Vcvars64 -PathType Leaf)) {
    throw "Missing Visual Studio developer environment: $Vcvars64"
}
$NinjaWrapper = Join-Path $Workspace "run_ninja_with_msvc.cmd"
@"
@echo off
call "$Vcvars64" >nul
if errorlevel 1 exit /b 1
if not defined INCLUDE (
  echo ERROR: Visual Studio INCLUDE is not defined.
  exit /b 1
)
if not defined LIB (
  echo ERROR: Visual Studio LIB is not defined.
  exit /b 1
)
"$Ninja" -C "$BuildDirectory" -j $Jobs install
exit /b %errorlevel%
"@ | Set-Content -LiteralPath $NinjaWrapper -Encoding ASCII
Log ("Ninja will use Visual Studio environment: " + $Vcvars64)
Run-Native $Cmd @("/d", "/c", $NinjaWrapper) "compile and install Blender with MSVC environment" $BlenderSource

 } else {
    Log "Resuming packaging: skipping source updates, patches, configuration, and compilation"
    $ExpectedBinary = Join-Path $BuildDirectory "bin\blender.exe"
    if (-not (Test-Path -LiteralPath $ExpectedBinary -PathType Leaf)) {
        throw "ResumePackaging requires existing compiled executable: $ExpectedBinary"
    }
    foreach ($Pair in @(@($NarwhalSource, $NarwhalRevision), @($BlenderSource, $BlenderRevision))) {
        $Found = @(Capture-Native $Git @("-C", $Pair[0], "rev-parse", "HEAD") "verify existing source revision")[-1].Trim()
        if ($Found -ne $Pair[1]) { throw "Source revision mismatch in $($Pair[0]): $Found" }
    }
}

$BuiltBlenderPath = Join-Path $BuildDirectory "bin\blender.exe"
if (-not (Test-Path -LiteralPath $BuiltBlenderPath -PathType Leaf)) {
    throw "The build finished but expected blender.exe is missing: $BuiltBlenderPath"
}
$BuiltBlender = Get-Item -LiteralPath $BuiltBlenderPath
$BuiltRuntime = $BuiltBlender.Directory.FullName
Log ("Built runtime: " + $BuiltRuntime)

$PackageName = "Narwhal_3D-0.4.0-Windows-x86_64-Engineering"
$PackageDirectory = Join-Path $ReturnDirectory $PackageName
$RuntimeDirectory = Join-Path $PackageDirectory "runtime"
$ApplicationDirectory = Join-Path $PackageDirectory "app"
New-Item -ItemType Directory -Path $PackageDirectory | Out-Null
Log "Copying native runtime into portable package"
Copy-Item -LiteralPath $BuiltRuntime -Destination $RuntimeDirectory -Recurse
New-Item -ItemType Directory -Path $ApplicationDirectory | Out-Null
foreach ($Directory in @("narwhal3d", "launch", "licenses")) {
    Copy-Item -LiteralPath (Join-Path $NarwhalSource $Directory) -Destination $ApplicationDirectory -Recurse
}
foreach ($File in @("LICENSE", "THIRD_PARTY_NOTICES", "CHANGELOG.md", "STATUS.md")) {
    Copy-Item -LiteralPath (Join-Path $NarwhalSource $File) -Destination $ApplicationDirectory
}

$SelectionManifestPath = Join-Path $SelectionCoreArtifacts "selection-core-manifest.json"
if (-not (Test-Path -LiteralPath $SelectionManifestPath -PathType Leaf)) {
    throw "Selection-core manifest is missing: $SelectionManifestPath"
}
$SelectionManifest = Get-Content -LiteralPath $SelectionManifestPath -Raw | ConvertFrom-Json
if ($SelectionManifest.schema -ne 1 -or $SelectionManifest.component -ne "narwhal-selection-core" -or
    $SelectionManifest.rust_toolchain -ne "1.95.0" -or $SelectionManifest.abi -ne 1) {
    throw "Selection-core manifest identity is invalid"
}
foreach ($SourceRecord in @($SelectionManifest.source_files)) {
    $Relative = [string]$SourceRecord.path
    if ([IO.Path]::IsPathRooted($Relative) -or $Relative.Contains("..") -or $Relative.Contains("\")) {
        throw "Unsafe selection-core source path: $Relative"
    }
    $SourcePath = Join-Path (Join-Path $NarwhalSource "native\selection_core") $Relative
    if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
        throw "Selection-core source input is missing: $Relative"
    }
    $SourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $SourcePath).Hash.ToLowerInvariant()
    if ($SourceHash -ne [string]$SourceRecord.sha256) {
        throw "Selection-core source hash differs: $Relative"
    }
}
$WindowsSelectionRecords = @($SelectionManifest.artifacts | Where-Object { $_.platform -eq "windows-x86_64" })
if ($WindowsSelectionRecords.Count -ne 1) { throw "Selection-core manifest lacks one Windows x86-64 artifact" }
$WindowsSelectionRecord = $WindowsSelectionRecords[0]
if ($WindowsSelectionRecord.target -ne "x86_64-pc-windows-gnu" -or
    $WindowsSelectionRecord.path -ne "windows-x86_64/narwhal_selection_core.dll" -or
    $WindowsSelectionRecord.abi -ne 1 -or $WindowsSelectionRecord.coff_timestamp -ne 0) {
    throw "Selection-core Windows artifact metadata is invalid"
}
$SelectionDllSource = Join-Path $SelectionCoreArtifacts "windows-x86_64\narwhal_selection_core.dll"
if (-not (Test-Path -LiteralPath $SelectionDllSource -PathType Leaf)) {
    throw "Selection-core Windows DLL is missing: $SelectionDllSource"
}
$SelectionDllHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $SelectionDllSource).Hash.ToLowerInvariant()
if ($SelectionDllHash -ne [string]$WindowsSelectionRecord.sha256 -or
    (Get-Item -LiteralPath $SelectionDllSource).Length -ne [int64]$WindowsSelectionRecord.size) {
    throw "Selection-core Windows DLL size/hash differs from its manifest"
}
$SelectionNativeDirectory = Join-Path $ApplicationDirectory "narwhal3d\native"
New-Item -ItemType Directory -Path $SelectionNativeDirectory -Force | Out-Null
$SelectionDll = Join-Path $SelectionNativeDirectory "narwhal_selection_core.dll"
Copy-Item -LiteralPath $SelectionDllSource -Destination $SelectionDll

$RuntimePython = @(Get-ChildItem -LiteralPath $RuntimeDirectory -Filter "python.exe" -File -Recurse |
    Where-Object { $_.FullName -match "\\python\\bin\\python\.exe$" } |
    Select-Object -First 1)
if ($RuntimePython.Count -ne 1) { throw "Bundled Blender python.exe was not found" }
$RuntimePythonPath = $RuntimePython[0].FullName
$WheelPaths = @()
foreach ($Record in $WheelRecords) {
    $WheelPaths += Download-Locked $Record $WheelCache
}
Run-Native $RuntimePythonPath @("-m", "ensurepip", "--upgrade") "enable pip in bundled Python"
$PipArguments = @("-m", "pip", "install", "--no-index", "--no-deps", "--force-reinstall") + $WheelPaths
Run-Native $RuntimePythonPath $PipArguments "install locked geometry wheels"

$Launcher = Join-Path $PackageDirectory "Narwhal3D.cmd"
@'
@echo off
setlocal
set "ROOT=%~dp0"
"%ROOT%runtime\blender.exe" --factory-startup --python "%ROOT%app\launch\bootstrap.py" -- %*
'@ | Set-Content -LiteralPath $Launcher -Encoding ASCII

foreach ($RequiredPath in @(
    (Join-Path $RuntimeDirectory "blender.exe"), $RuntimePythonPath,
    (Join-Path $ApplicationDirectory "narwhal3d\__init__.py"),
    (Join-Path $ApplicationDirectory "narwhal3d\native\narwhal_selection_core.dll"),
    (Join-Path $ApplicationDirectory "launch\bootstrap.py"), $Launcher)) {
    if (-not (Test-Path -LiteralPath $RequiredPath -PathType Leaf)) {
        throw "Required packaged path is missing: $RequiredPath"
    }
}
$VersionLines = @(Capture-Native (Join-Path $RuntimeDirectory "blender.exe") @("--version") "verify packaged native version banner")
if (-not $VersionLines -or -not $VersionLines[0].StartsWith("Narwhal 3D 5.2.2")) {
    throw "Packaged executable has the wrong version banner"
}
$Probe = 'import importlib.metadata,json,sys; import rhino3dm,OCP; print(json.dumps({"python":list(sys.version_info[:2]),"rhino3dm":importlib.metadata.version("rhino3dm"),"cadquery-ocp-novtk":importlib.metadata.version("cadquery-ocp-novtk"),"cadquery-ocp-proxy":importlib.metadata.version("cadquery-ocp-proxy")},sort_keys=True))'
# A standalone .py file avoids Windows command-shell quote mangling of JSON keys.
$ProbeScriptPath = Join-Path $Workspace "verify_bundled_geometry.py"
Set-Content -LiteralPath $ProbeScriptPath -Value $Probe -Encoding UTF8
$ProbeLines = @(Capture-Native $RuntimePythonPath @("-I", $ProbeScriptPath) "verify bundled geometry imports")
$DependencyResult = $ProbeLines[-1] | ConvertFrom-Json
if (($DependencyResult.python -join ".") -ne "3.13" -or
    $DependencyResult.rhino3dm -ne "8.35.0" -or
    $DependencyResult.'cadquery-ocp-novtk' -ne "8.0.1.0.0" -or
    $DependencyResult.'cadquery-ocp-proxy' -ne "8.0.1.0.0") {
    throw "Packaged dependency versions do not match the Windows lock"
}
Run-Native (Join-Path $RuntimeDirectory "blender.exe") @(
    "-b", "--factory-startup", "--python-exit-code", "1", "--python",
    (Join-Path $ApplicationDirectory "launch\bootstrap.py")) "run packaged headless bootstrap"
$PriorSelectionCore = [Environment]::GetEnvironmentVariable("NARWHAL_SELECTION_CORE", "Process")
try {
    [Environment]::SetEnvironmentVariable("NARWHAL_SELECTION_CORE", $SelectionDll, "Process")
    Run-Native $RuntimePythonPath @(
        "-I", (Join-Path $NarwhalSource "spike\test_rust_selection_core.py")) `
        "run packaged Windows selection-core ABI acceptance"
} finally {
    [Environment]::SetEnvironmentVariable("NARWHAL_SELECTION_CORE", $PriorSelectionCore, "Process")
}
# The upstream branding smoke test uses os.access(..., os.X_OK) to check
# POSIX executable mode on a .desktop template. Windows does not implement
# POSIX executable permission bits, so this check is not meaningful here.
# Run a temporary Windows-adjusted copy next to the original test, preserving
# __file__-relative paths and every other assertion. Keep the source untouched.
$OriginalBrandingTest = Join-Path $NarwhalSource "spike\branding_test.py"
$WindowsBrandingTest = Join-Path $NarwhalSource "spike\branding_test_windows_preview.py"
$BrandingText = [IO.File]::ReadAllText($OriginalBrandingTest)
$PosixAssertion = 'check(not os.access(TEMPLATE, os.X_OK), "desktop template must not be executable")'
if (-not $BrandingText.Contains($PosixAssertion)) {
    throw "Expected POSIX-only desktop permission assertion not found in branding test; refusing to alter its checks"
}
$WindowsAssertion = 'if os.name != "nt":' + "`n" + '    ' + $PosixAssertion
$BrandingText = $BrandingText.Replace($PosixAssertion, $WindowsAssertion)
try {
    [IO.File]::WriteAllText($WindowsBrandingTest, $BrandingText, [Text.UTF8Encoding]::new($false))
    Run-Native (Join-Path $RuntimeDirectory "blender.exe") @(
        "-b", "--factory-startup", "--python-exit-code", "1", "--python",
        $WindowsBrandingTest) "run Windows-compatible native branding source smoke test"
} finally {
    Remove-Item -LiteralPath $WindowsBrandingTest -Force -ErrorAction SilentlyContinue
}

$BinaryHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $RuntimeDirectory "blender.exe")).Hash.ToLowerInvariant()
$PatchInputs = @()
foreach ($Relative in @(
    "apply_narwhal3d_branding.py", "apply_app_drawn.py",
    "apply_cad_boundary_wires.py", "narwhal3d_app_icon.svg")) {
    $Path = Join-Path $PatchDirectory $Relative
    $PatchInputs += [ordered]@{ name = $Relative; sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant() }
}
$Result = [ordered]@{
    schema = 1
    product = "Narwhal 3D"
    target = "windows-x86_64-engineering"
    completed_utc = (Get-Date).ToUniversalTime().ToString("o")
    narwhal_revision = $NarwhalRevision
    blender_revision = $BlenderRevision
    parallel_jobs = $Jobs
    blender_exe_sha256 = $BinaryHash
    patch_inputs = $PatchInputs
    selection_core = [ordered]@{
        source_aggregate_sha256 = [string]$SelectionManifest.source_aggregate_sha256
        rust_toolchain = [string]$SelectionManifest.rust_toolchain
        abi = [int]$SelectionManifest.abi
        artifact = $WindowsSelectionRecord
        windows_packaged_abi_acceptance = $true
    }
    package_directory = $PackageName
    physical_gpu_validation_complete = $false
    code_signed = $false
}
$Result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ResultPath -Encoding UTF8

$PackageZip = Join-Path $Desktop ($PackageName + ".zip")
$TemporaryPackageZip = $PackageZip + ".part.zip"
Remove-Item -LiteralPath $TemporaryPackageZip -Force -ErrorAction SilentlyContinue
Log ("Creating engineering package ZIP: " + $PackageZip)
Run-Native $Tar @("-a", "-cf", $TemporaryPackageZip, "-C", $ReturnDirectory, $PackageName) "create engineering package archive"
$ArchiveEntries = @(Capture-Native $Tar @("-tf", $TemporaryPackageZip) "verify engineering package archive")
foreach ($RequiredEntry in @(
    "$PackageName/runtime/blender.exe", "$PackageName/app/narwhal3d/__init__.py",
    "$PackageName/app/narwhal3d/native/narwhal_selection_core.dll",
    "$PackageName/app/launch/bootstrap.py", "$PackageName/Narwhal3D.cmd")) {
    if ($RequiredEntry -notin $ArchiveEntries) { throw "Archive is missing $RequiredEntry" }
}
if (Test-Path -LiteralPath $PackageZip) { Remove-Item -LiteralPath $PackageZip -Force }
Move-Item -LiteralPath $TemporaryPackageZip -Destination $PackageZip
$PackageHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $PackageZip).Hash.ToLowerInvariant()
($PackageHash + "  " + (Split-Path $PackageZip -Leaf)) |
    Set-Content -LiteralPath (Join-Path $ReturnDirectory "portable-package.sha256") -Encoding ASCII

Log "Windows engineering build completed"
$ReturnZip = $ReturnDirectory + ".zip"
$ReturnInputs = @(Get-ChildItem -LiteralPath $ReturnDirectory -File | ForEach-Object { $_.FullName })
Compress-Archive -LiteralPath $ReturnInputs -DestinationPath $ReturnZip -CompressionLevel Optimal
Write-Host ""
Write-Host "Narwhal 3D Windows engineering build completed." -ForegroundColor Green
Write-Host ("Portable package: " + $PackageZip)
Write-Host ("Logs to return:  " + $ReturnZip)
Write-Host "Return both ZIP files. This is an engineering artifact, not a distributable release."
Write-Host "Windows PE identity, installer verification, signing, and physical-GPU acceptance remain required."
