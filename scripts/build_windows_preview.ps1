# Build a native Narwhal 3D Windows x86_64 preview and place return artifacts on Desktop.
# Run from ordinary 64-bit PowerShell after the collector reports the toolchain ready:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_windows_preview.ps1
[CmdletBinding()]
param(
    [string]$Workspace = "C:\Narwhal3D-build",
    [ValidateRange(1, 8)][int]$Jobs = 4
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$NarwhalRevision = "071200ac982437bff148d8a213fd896bad204555"
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
Run-Native $Git @("-C", $BlenderSource, "fetch", "origin", $BlenderRevision) "fetch pinned Blender revision"
Run-Native $Git @("-C", $BlenderSource, "reset", "--hard", $BlenderRevision) "reset script-owned Blender clone"
Run-Native $Git @("-C", $BlenderSource, "clean", "-ffd") "clean script-owned Blender clone"

Run-Native $Cmd @("/d", "/s", "/c", "make.bat update") "Blender make.bat update" $BlenderSource
$LibraryDirectory = Join-Path $BlenderSource "lib\windows_x64"
if (-not (Test-Path -LiteralPath $LibraryDirectory -PathType Container)) {
    throw "Blender make update did not produce lib\\windows_x64"
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
$PatchDirectory = Join-Path $NarwhalSource "blender_patches"
Run-Native $Python @($PatchDriver, $BlenderSource, $PatchDirectory) "apply Narwhal native patches"

if (Test-Path -LiteralPath $BuildDirectory) {
    Log "Removing prior build output while retaining downloaded Blender libraries"
    Remove-Item -LiteralPath $BuildDirectory -Recurse -Force
}
Run-Native $Cmd @("/d", "/s", "/c", "make.bat 2022 x64 release ninja nobuild builddir $BuildDirectory") "configure Blender 2022 x64 Release Ninja build" $BlenderSource
Run-Native $Ninja @("-C", $BuildDirectory, "-j", [string]$Jobs, "install") "compile and install Blender with bounded Ninja jobs" $BlenderSource

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
$ProbeLines = @(Capture-Native $RuntimePythonPath @("-I", "-c", $Probe) "verify bundled geometry imports")
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
Run-Native (Join-Path $RuntimeDirectory "blender.exe") @(
    "-b", "--factory-startup", "--python-exit-code", "1", "--python",
    (Join-Path $NarwhalSource "spike\branding_test.py")) "run native branding source smoke test"

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
