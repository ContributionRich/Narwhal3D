# Build a native Narwhal 3D Windows x86_64 preview and place return artifacts on Desktop.
# Run from ordinary 64-bit PowerShell after the collector reports the toolchain ready:
# -ResumePackaging skips native source updates/patch/configure/compile, but requires
# current compiled-input provenance. An old/unattested runtime must be rebuilt.
# -RebuildRuntime preserves blender-build and cached libraries; never use the default
# clean build for a cached release update. Modes are mutually exclusive.
# -SourceOverlay merges a trusted public source export (no .git/tools/vendor) after
# resetting only the marked workspace's app clone. Allowed only with -RebuildRuntime.
# Stop any other build in this workspace before running. -Jobs bounds native parallelism.
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_windows_preview.ps1 `
#     -NarwhalRevision <40-character-public-commit>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$NarwhalRevision,
    [ValidatePattern('^0\.4\.[0-9]+$')]
    [string]$Version = "0.4.7",
    [string]$Workspace = "C:\Narwhal3D-build",
    [ValidateRange(1, 8)][int]$Jobs = 4,
    [switch]$ResumePackaging,
    [switch]$RebuildRuntime,
    [ValidateRange(1, 1440)][int]$RuntimeTimeoutMinutes = 180,
    [string]$SourceOverlay = "",
    [ValidatePattern('^[0-9]+\.[0-9]+(\.[0-9]+)?$')]
    [string]$MsvcToolsetVersion = "14.44",
    [ValidatePattern('^$|^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$')]
    [string]$WindowsSdkVersion = ""
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
if ($ResumePackaging -and $RebuildRuntime) { throw "Choose -ResumePackaging OR -RebuildRuntime" }
if ($SourceOverlay -and -not $RebuildRuntime) { throw "-SourceOverlay requires -RebuildRuntime" }

$BlenderRevision = "d13f752e3b9c4f8c261cda552b1021f8bcc0382c"
$OcctRevision = "b8f597c677811d1f9f4d8a97f5ae2825c0353a42"
$NarwhalRepository = "https://github.com/ContributionRich/Narwhal3D.git"
$BlenderRepository = "https://projects.blender.org/blender/blender.git"
$OcctRepository = "https://github.com/Open-Cascade-SAS/OCCT.git"
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
            version = $Version; narwhal_revision = $NarwhalRevision; blender_revision = $BlenderRevision
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
                        [string]$WorkingDirectory = "", [bool]$Quiet = $false) {
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
    if (-not $Quiet) { foreach ($Line in $Lines) { Log $Line } }
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
# OURS: packaging reserves 16 GiB for runtime/app copies, Rust cores and ZIP staging.
# Both clean and incremental native compilation retain the 60 GiB reservation.
$MinimumFreeGiB = if ($ResumePackaging) { 16 } else { 60 }
if ($SystemDrive.FreeSpace -lt ($MinimumFreeGiB * 1GB)) { throw "At least $MinimumFreeGiB GiB free is required; found $FreeGiB GiB" }
$MemoryGiB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 2)
if ($MemoryGiB -lt 16 -and -not @(Get-CimInstance Win32_PageFileUsage -ErrorAction SilentlyContinue)) {
    throw "A Windows pagefile is required on machines with less than 16 GiB RAM"
}
Log ("Preflight: long paths enabled, free GiB=" + $FreeGiB + ", RAM GiB=" + $MemoryGiB)

$Workspace = [IO.Path]::GetFullPath($Workspace).TrimEnd('\')
if ($Workspace -eq [IO.Path]::GetPathRoot($Workspace).TrimEnd('\')) {
    throw "The workspace must not be a drive root"
}
function Assert-NoReparseAncestor([string]$Path) {
    $Cursor = [IO.Path]::GetFullPath($Path)
    while ($Cursor) {
        if (Test-Path -LiteralPath $Cursor) {
            if ((Get-Item -Force -LiteralPath $Cursor).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Refusing junction/symlink in owned workspace path: $Cursor"
            }
        }
        $Cursor = Split-Path -Parent $Cursor
    }
}
Assert-NoReparseAncestor $Workspace
$WorkspaceMarker = Join-Path $Workspace ".narwhal3d-windows-build-workspace"
if (Test-Path -LiteralPath $Workspace) {
    Assert-NoReparseAncestor $WorkspaceMarker
    if (-not (Test-Path -LiteralPath $WorkspaceMarker -PathType Leaf) -or
        ([IO.File]::ReadAllText($WorkspaceMarker).Trim() -ne "Narwhal 3D owned build workspace")) {
        throw "Refusing existing unowned workspace: $Workspace"
    }
} else {
    if ($RebuildRuntime -or $ResumePackaging) { throw "Reuse requires an existing marked workspace" }
    New-Item -ItemType Directory -Path $Workspace | Out-Null
    Set-Content -LiteralPath $WorkspaceMarker -Value "Narwhal 3D owned build workspace" -Encoding ASCII
}

$WorkspaceLockPath = Join-Path $Workspace '.native-build.lock'
Assert-NoReparseAncestor $WorkspaceLockPath
$WorkspaceLock = [IO.File]::Open($WorkspaceLockPath, [IO.FileMode]::OpenOrCreate,
    [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
function Assert-OwnedCheckout([string]$Path) {
    Assert-NoReparseAncestor (Join-Path $Path '.git')
    if (-not (Test-Path -LiteralPath (Join-Path $Path '.git') -PathType Container)) {
        throw "Not a standalone script-owned checkout: $Path"
    }
    $Top = @(Capture-Native $Git @('-C', $Path, 'rev-parse', '--show-toplevel') 'verify owned checkout root')[-1].Trim()
    if ([IO.Path]::GetFullPath($Top) -ne $Path -or (Split-Path -Parent $Path) -ne $Workspace) {
        throw "Refusing Git reset outside the marked workspace checkout: $Path"
    }
}

$NarwhalSource = Join-Path $Workspace "narwhal-source"
$BlenderSource = Join-Path $Workspace "blender-source"
$OcctSource = Join-Path $Workspace "occt-source"
$BuildDirectory = Join-Path $Workspace "blender-build"
$WheelCache = Join-Path $Workspace "wheels"
$PatchDirectory = Join-Path $NarwhalSource "blender_patches"
foreach ($OwnedPath in @($NarwhalSource, $BlenderSource, $OcctSource, $BuildDirectory,
                        (Join-Path $NarwhalSource '.git'), (Join-Path $BlenderSource '.git'),
                        (Join-Path $OcctSource '.git'))) {
    Assert-NoReparseAncestor $OwnedPath
}
$RuntimeMarker = Join-Path $BuildDirectory "bin\.narwhal3d-native-runtime.json"
$NativeInputNames = @(
    "apply_narwhal3d_branding.py", "apply_app_drawn.py", "apply_cad_boundary_wires.py",
    "apply_deferred_first_present.py", "apply_statusbar_height.py",
    "narwhal3d_app_icon.svg", "narwhal3d_window_icons.json", "apply_window_overlay.py"
)
if ($RebuildRuntime) {
    # Fail before resetting either clone or touching the retained build tree.
    foreach ($Required in @((Join-Path $BuildDirectory 'CMakeCache.txt'),
                           (Join-Path $BuildDirectory 'build.ninja'),
                           (Join-Path $BlenderSource 'lib\windows_x64\python\313\bin\python.exe'))) {
        Assert-NoReparseAncestor $Required
        if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) { throw "Incremental runtime prerequisite missing: $Required" }
    }
    $CachedRevision = @(Capture-Native $Git @('-C', $BlenderSource, 'rev-parse', 'HEAD') 'verify incremental Blender revision')[-1].Trim()
    if ($CachedRevision -ne $BlenderRevision) { throw "Incremental Blender source revision mismatch" }
    Assert-OwnedCheckout $NarwhalSource
    Assert-OwnedCheckout $BlenderSource
    $CacheCheckDriver = Join-Path $Workspace 'check_incremental_cache.py'
@'
# CMake cache inspection only: never reconfigure or rewrite a mismatched cache.
import json
from pathlib import Path, PureWindowsPath
import re
import sys

def validate_cache(text, source, toolset, sdk, exists=lambda path: Path(path).is_file()):
    cache = dict(re.findall(r'^([^#/:\n][^:\n]*):[^=\n]+=(.*)$', text, re.M))
    if (PureWindowsPath(cache.get('CMAKE_HOME_DIRECTORY', '')) != PureWindowsPath(source)
            or cache.get('CMAKE_GENERATOR') != 'Ninja' or cache.get('CMAKE_BUILD_TYPE') != 'Release'):
        raise SystemExit('Incremental CMake source/generator/configuration mismatch; cache was not changed')
    for key in ('CMAKE_C_COMPILER', 'CMAKE_CXX_COMPILER'):
        compiler = cache.get(key, '')
        if (not exists(compiler) or not re.search(r'[/\\]2022[/\\]', compiler)
                or not re.search(r'[/\\]MSVC[/\\]' + re.escape(toolset) + r'(\.|[/\\])', compiler)):
            raise SystemExit(f'Incremental compiler does not match VS2022 / requested MSVC toolset: {key}={compiler}')
    cached_sdk = cache.get('CMAKE_VS_WINDOWS_TARGET_PLATFORM_VERSION', '')
    if sdk and cached_sdk and sdk != cached_sdk:
        raise SystemExit('Incremental Windows SDK mismatch')
    return {'windows_sdk': sdk or cached_sdk}

if __name__ == '__main__':
    cache_path, source, toolset, sdk = sys.argv[1:]
    print(json.dumps(validate_cache(Path(cache_path).read_text(encoding='utf-8'), source, toolset,
                                   '' if sdk == 'auto' else sdk)))
'@ | Set-Content -LiteralPath $CacheCheckDriver -Encoding UTF8
    # Explicit sentinel avoids Windows PowerShell dropping an empty native argv item.
    $SdkArgument = if ($WindowsSdkVersion) { $WindowsSdkVersion } else { 'auto' }
    $CacheResult = @(Capture-Native $Python @('-B', $CacheCheckDriver,
        (Join-Path $BuildDirectory 'CMakeCache.txt'), $BlenderSource, $MsvcToolsetVersion,
        $SdkArgument) 'validate retained CMake cache')[-1] | ConvertFrom-Json
    $WindowsSdkVersion = $CacheResult.windows_sdk
}
if (-not $ResumePackaging) {
if (-not (Test-Path -LiteralPath (Join-Path $NarwhalSource ".git") -PathType Container)) {
    Run-Native $Git @("clone", "--filter=blob:none", $NarwhalRepository, $NarwhalSource) "clone Narwhal source"
}
Assert-OwnedCheckout $NarwhalSource
$NarwhalOrigin = @(Capture-Native $Git @("-C", $NarwhalSource, "remote", "get-url", "origin") "verify Narwhal origin")[-1].Trim()
if ($NarwhalOrigin -ne $NarwhalRepository) { throw "Unexpected Narwhal origin: $NarwhalOrigin" }
# A VM helper may have already imported the exact public commit without network.
if ($RebuildRuntime) {
    # Fail closed if the helper has not imported this commit; do not unexpectedly fetch.
    $LocalRevision = @(Capture-Native $Git @('-C', $NarwhalSource, 'rev-parse', '--verify',
        ($NarwhalRevision + '^{commit}')) 'require locally available incremental app revision')[-1].Trim()
    if ($LocalRevision -ne $NarwhalRevision) { throw "Incremental app revision is not locally available" }
} else {
    Run-Native $Git @("-C", $NarwhalSource, "fetch", "origin", $NarwhalRevision) "fetch pinned Narwhal revision"
}
Run-Native $Git @("-C", $NarwhalSource, "reset", "--hard", $NarwhalRevision) "reset script-owned Narwhal clone"
Run-Native $Git @("-C", $NarwhalSource, "clean", "-ffd") "clean script-owned Narwhal clone"
$ActualNarwhalRevision = @(Capture-Native $Git @('-C', $NarwhalSource, 'rev-parse', 'HEAD') 'verify pinned Narwhal revision')[-1].Trim()
if ($ActualNarwhalRevision -ne $NarwhalRevision) { throw "Narwhal source revision mismatch" }
if ($SourceOverlay) {
    $OverlayDriver = Join-Path $Workspace 'merge_source_overlay.py'
@'
# Build-time file orchestration only; never copy Git metadata or build caches.
from pathlib import Path
import shutil
import sys
source, target = (Path(p) for p in sys.argv[1:])
if source.is_symlink() or getattr(source, 'is_junction', lambda: False)():
    raise SystemExit('overlay root must not be a symlink/junction')
source, target = source.resolve(), target.resolve()
if not source.is_dir() or source == target or source in target.parents or target in source.parents:
    raise SystemExit('overlay must be a separate public source export directory')
forbidden = {'.git', 'tools', 'vendor', 'more_data', 'artifacts', '__pycache__', 'target'}
files = []
for path in source.rglob('*'):
    relative = path.relative_to(source)
    if (path.is_symlink() or getattr(path, 'is_junction', lambda: False)()
            or forbidden.intersection(part.lower() for part in relative.parts)
            or any(part.endswith(('.', ' ')) or ':' in part for part in relative.parts)):
        raise SystemExit(f'unsafe overlay entry: {relative}')
    destination = target / relative
    for parent in (destination, *destination.parents):
        if parent == target.parent:
            break
        if parent.is_symlink() or getattr(parent, 'is_junction', lambda: False)():
            raise SystemExit(f'unsafe overlay destination: {relative}')
    if path.is_file():
        files.append((path, destination))
for path, destination in files:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, destination)
print(f'merged {len(files)} public overlay files; base commit is not an attestation of overlay contents')
'@ | Set-Content -LiteralPath $OverlayDriver -Encoding UTF8
    Run-Native $Python @('-B', $OverlayDriver, $SourceOverlay, $NarwhalSource) 'merge explicit public source overlay into owned app clone'
}

if (-not (Test-Path -LiteralPath (Join-Path $BlenderSource ".git") -PathType Container)) {
    Run-Native $Git @("clone", "--filter=blob:none", $BlenderRepository, $BlenderSource) "clone Blender source"
}
Assert-OwnedCheckout $BlenderSource
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
if (-not $RebuildRuntime) {
    Run-Native $Git @("-C", $BlenderSource, "fetch", "origin", $BlenderRevision) "fetch pinned Blender revision"
}
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
if (-not $RebuildRuntime) {
    Run-Native $UpdatePython @("-B", $UpdateScript, "--git-command", $Git, "--no-blender") "update Blender libraries without changing Blender source" $BlenderSource
} else {
    Log "Incremental runtime: retaining existing pinned-checkout libraries; no dependency update"
}
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
for name in ("apply_app_drawn.py", "apply_cad_boundary_wires.py", "apply_deferred_first_present.py"):
    module = load("narwhal_" + name.replace(".py", ""), patch_dir / name)
    module.SRC = str(source / "source" / "blender")
    module.main()
# These newer entry points parse argv; never pass build-driver arguments to main().
for name in ("apply_statusbar_height.py", "apply_window_overlay.py"):
    module = load("narwhal_" + name.replace(".py", ""), patch_dir / name)
    changed = module.apply(source / "source" / "blender")
    print(f"{name} changed {len(changed)} path(s)")
'@ | Set-Content -LiteralPath $PatchDriver -Encoding UTF8
# Invalidate successful-build provenance BEFORE patching, including failed rebuilds.
Remove-Item -LiteralPath $RuntimeMarker -Force -ErrorAction SilentlyContinue
Run-Native $Python @('-B', $PatchDriver, $BlenderSource, $PatchDirectory) "apply Narwhal native patches"

if (-not $RebuildRuntime -and (Test-Path -LiteralPath $BuildDirectory)) {
    Log "Removing prior build output while retaining downloaded Blender libraries"
    Remove-Item -LiteralPath $BuildDirectory -Recurse -Force
}
if (-not $RebuildRuntime) {
    Run-Native $Cmd @("/d", "/s", "/c", "make.bat 2022 x64 release ninja nobuild builddir $BuildDirectory") "configure Blender 2022 x64 Release Ninja build" $BlenderSource
}
# Ninja must execute in the same MSVC Developer environment used by Blender's
# make.bat configure step. Environment changes inside make.bat cannot propagate
# back into PowerShell, so launch Ninja through vcvars64.bat explicitly.
$VsWhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
$VisualStudioRoot = ""
if (Test-Path -LiteralPath $VsWhere -PathType Leaf) {
    $VisualStudioRoot = @(& $VsWhere -latest -version '[17.0,18.0)' -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath | Select-Object -First 1)
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
$IncrementalConfigure = if ($RebuildRuntime) {
    '"' + $CMake + '" -S "' + $BlenderSource + '" -B "' + $BuildDirectory + '" -G Ninja -DCMAKE_BUILD_TYPE=Release'
} else { 'rem Clean build was configured by make.bat' }
@"
@echo off
call "$Vcvars64" $WindowsSdkVersion -vcvars_ver=$MsvcToolsetVersion >nul
if errorlevel 1 exit /b 1
$IncrementalConfigure
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
$RuntimeBuildRunner = Join-Path $Workspace 'run_bounded_runtime_build.py'
@'
# Process orchestration only. On timeout kill cmd + Ninja + compiler descendants.
import subprocess
import sys
process = subprocess.Popen(sys.argv[2:])
try:
    code = process.wait(timeout=int(sys.argv[1]) * 60)
except subprocess.TimeoutExpired:
    subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                   check=True, timeout=30)
    process.wait(timeout=30)
    raise SystemExit('native runtime build timed out; build cache retained; rerun -RebuildRuntime')
raise SystemExit(code)
'@ | Set-Content -LiteralPath $RuntimeBuildRunner -Encoding UTF8
Run-Native $Python @('-B', $RuntimeBuildRunner, [string]$RuntimeTimeoutMinutes,
    $Cmd, '/d', '/c', $NinjaWrapper) "compile and install Blender with bounded MSVC environment" $BlenderSource

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

# Build-time provenance and source inspection are Python orchestration, not runtime logic.
# This same driver checks ResumePackaging before any package is created. It cannot
# certify rendering/physical DPI; those remain real-window release acceptance gates.
$ProvenanceDriver = Join-Path $Workspace 'native_runtime_provenance.py'
@'
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

NATIVE_MARKERS = (b'NARWHAL3D_DEFER_FIRST_PRESENT', b'NARWHAL3D_STATUSBAR_HEIGHT',
                  b'RNA_WINDOW_DRAW_HANDLE', b'narwhal_app_drawn', b'narwhal_boundary_wires')

def regular(path):
    if not path.is_file() or any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)()
                                 for p in (path, *path.parents)):
        raise SystemExit(f'missing or linked native input: {path}')
    return path

def inspect(source, patch_dir, binary, commit, names):
    records = []
    aggregate = hashlib.sha256()
    for name in sorted(names):
        path = regular(patch_dir / name)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        logical = 'blender_patches/' + name
        records.append({'path': logical, 'sha256': digest})
        aggregate.update(logical.encode() + b'\0' + digest.encode('ascii') + b'\n')
        if name.endswith('.py') and name != 'apply_narwhal3d_branding.py':
            spec = importlib.util.spec_from_file_location('verify_' + path.stem, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for relative, old, new in module.EDITS:
                text = regular(source / 'source/blender' / relative).read_text(encoding='utf-8')
                if text.count(new) != 1 or text.count(old) != new.count(old):
                    raise SystemExit(f'current native patch missing from source: {name}: {relative}')
            if name == 'apply_window_overlay.py':
                header = regular(source / 'source/blender' / module.HEADER)
                if header.read_text(encoding='utf-8') != module.HEADER_TEXT:
                    raise SystemExit('current window-overlay header missing from source')
    data = regular(binary).read_bytes()
    for marker in NATIVE_MARKERS:
        if marker not in data:
            raise SystemExit(f'runtime lacks native marker {marker!r}; use -RebuildRuntime')
    return {'schema': 1, 'product': 'Narwhal 3D', 'platform': 'windows-x86_64',
            'blender_source_commit': commit, 'binary_sha256': hashlib.sha256(data).hexdigest(),
            'patch_inputs': records, 'patch_aggregate_sha256': aggregate.hexdigest()}

def main():
    mode, source, patch_dir, runtime, commit, *names = sys.argv[1:]
    runtime = Path(runtime)
    marker = runtime / '.narwhal3d-native-runtime.json'
    expected = inspect(Path(source), Path(patch_dir), runtime / 'blender.exe', commit, names)
    if mode == 'check':
        if not marker.is_file():
            raise SystemExit('no compiled runtime provenance; use -RebuildRuntime, not -ResumePackaging')
        actual = json.loads(regular(marker).read_text(encoding='utf-8'))
        if actual != expected:
            raise SystemExit('stale compiled runtime provenance; use -RebuildRuntime')
    elif mode == 'record':
        temporary = marker.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(expected, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        temporary.replace(marker)
    else:
        raise SystemExit('invalid provenance mode')
    print(json.dumps(expected, sort_keys=True))

if __name__ == '__main__':
    main()
'@ | Set-Content -LiteralPath $ProvenanceDriver -Encoding UTF8
$RuntimeApiProbe = Join-Path $Workspace 'verify_native_ui_api.py'
@'
# bpy integration only. No UI layout changes or saved preferences.
import bpy
import json
import math

def require(ok, message):
    if not ok:
        raise SystemExit(message)

require('narwhal_app_drawn' in bpy.types.Collection.bl_rna.properties, 'missing Collection app-drawn API')
require('narwhal_boundary_wires' in bpy.types.Mesh.bl_rna.properties, 'missing Mesh boundary-wire API')
require(callable(getattr(bpy.types.STATUSBAR_HT_header, 'draw', None)), 'missing statusbar header draw API')
for owner, region in ((bpy.types.SpaceView3D, 'HEADER'), (bpy.types.SpaceInfo, 'WINDOW')):
    handle = owner.draw_handler_add(lambda: None, (), region, 'POST_PIXEL')
    owner.draw_handler_remove(handle, region)
window = bpy.types.Window
require(callable(getattr(window, 'draw_handler_add', None)) and
        callable(getattr(window, 'draw_handler_remove', None)), 'missing Window overlay API')
handle = window.draw_handler_add(lambda: None, (), 'POST_PIXEL')
window.draw_handler_remove(handle)
window.draw_handler_remove(handle)
scale = bpy.context.preferences.system.ui_scale
# MEASURED: background Windows startup exposes zero before window DPI initialization.
# A real window must still report a positive scale; never coerce invalid values.
require(math.isfinite(scale) and (scale > 0 or (bpy.app.background and scale == 0)),
        'invalid native UI scale')
print(json.dumps({'native_ui_api': 'ok', 'ui_scale': scale, 'background': bpy.app.background,
                  'statusbar_height_env': 'NARWHAL3D_STATUSBAR_HEIGHT',
                  'statusbar_logical_pixels': 37}))
'@ | Set-Content -LiteralPath $RuntimeApiProbe -Encoding UTF8
$NativeRuntimeDirectory = Join-Path $BuildDirectory 'bin'
$ProvenanceArguments = @('-B', $ProvenanceDriver, 'check', $BlenderSource, $PatchDirectory,
                        $NativeRuntimeDirectory, $BlenderRevision) + $NativeInputNames
if ($ResumePackaging) {
    Run-Native $Python $ProvenanceArguments 'require current compiled runtime before resuming packaging'
}
Run-Native (Join-Path $NativeRuntimeDirectory 'blender.exe') @(
    '-b', '--factory-startup', '--python-exit-code', '1', '--python', $RuntimeApiProbe
) 'verify compiled native UI APIs before packaging'
if (-not $ResumePackaging) {
    $RecordArguments = @('-B', $ProvenanceDriver, 'record', $BlenderSource, $PatchDirectory,
                         $NativeRuntimeDirectory, $BlenderRevision) + $NativeInputNames
    Run-Native $Python $RecordArguments 'record successfully built and probed native runtime inputs'
}
$RuntimeProvenance = [IO.File]::ReadAllText($RuntimeMarker) | ConvertFrom-Json

# geometry_core compiles against the exact official headers matching the pinned OCP
# wheel. This checkout is script-owned and independently verified by build.rs markers.
if (-not (Test-Path -LiteralPath (Join-Path $OcctSource ".git") -PathType Container)) {
    Run-Native $Git @("clone", "--filter=blob:none", "--no-checkout", $OcctRepository, $OcctSource) "clone pinned OCCT source"
}
Assert-OwnedCheckout $OcctSource
$OcctOrigin = @(Capture-Native $Git @("-C", $OcctSource, "remote", "get-url", "origin") "verify OCCT origin")[-1].Trim()
if ($OcctOrigin -ne $OcctRepository) { throw "Unexpected OCCT origin: $OcctOrigin" }
$ExistingOcctRevision = @(Capture-Native $Git @("-C", $OcctSource, "rev-parse", "HEAD") "inspect existing OCCT revision")[-1].Trim()
if ($ExistingOcctRevision -ne $OcctRevision) {
    Run-Native $Git @("-C", $OcctSource, "fetch", "--depth", "1", "origin", $OcctRevision) "fetch pinned OCCT revision"
} else {
    Log "Using existing verified pinned OCCT revision without a network request"
}
Run-Native $Git @("-C", $OcctSource, "reset", "--hard", $OcctRevision) "reset script-owned OCCT source"
Run-Native $Git @("-C", $OcctSource, "clean", "-ffd") "clean script-owned OCCT source"
$ActualOcctRevision = @(Capture-Native $Git @("-C", $OcctSource, "rev-parse", "HEAD") "verify pinned OCCT revision")[-1].Trim()
if ($ActualOcctRevision -ne $OcctRevision) { throw "OCCT checkout is not the pinned revision" }

$BuiltBlenderPath = Join-Path $BuildDirectory "bin\blender.exe"
if (-not (Test-Path -LiteralPath $BuiltBlenderPath -PathType Leaf)) {
    throw "The build finished but expected blender.exe is missing: $BuiltBlenderPath"
}
$BuiltBlender = Get-Item -LiteralPath $BuiltBlenderPath
$BuiltRuntime = $BuiltBlender.Directory.FullName
Log ("Built runtime: " + $BuiltRuntime)

$PackageName = "Narwhal_3D-$Version-Windows-x86_64-Engineering"
$PackageDirectory = Join-Path $ReturnDirectory $PackageName
$RuntimeDirectory = Join-Path $PackageDirectory "runtime"
$ApplicationDirectory = Join-Path $PackageDirectory "app"
New-Item -ItemType Directory -Path $PackageDirectory | Out-Null
Log "Copying native runtime into portable package"
Copy-Item -LiteralPath $BuiltRuntime -Destination $RuntimeDirectory -Recurse
$PackagedProvenanceArguments = @('-B', $ProvenanceDriver, 'check', $BlenderSource, $PatchDirectory,
                                $RuntimeDirectory, $BlenderRevision) + $NativeInputNames
Run-Native $Python $PackagedProvenanceArguments 'verify packaged runtime matches compiled input provenance'
New-Item -ItemType Directory -Path $ApplicationDirectory | Out-Null
foreach ($Directory in @("narwhal3d", "launch", "licenses")) {
    Copy-Item -LiteralPath (Join-Path $NarwhalSource $Directory) -Destination $ApplicationDirectory -Recurse
}
foreach ($File in @("LICENSE", "THIRD_PARTY_NOTICES", "CHANGELOG.md", "STATUS.md")) {
    Copy-Item -LiteralPath (Join-Path $NarwhalSource $File) -Destination $ApplicationDirectory
}

$CargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
$RustupCommand = Get-Command rustup.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
$CargoCommand = Get-Command cargo.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
$Rustup = if ($RustupCommand) { $RustupCommand.Source } else { Join-Path $CargoBin "rustup.exe" }
$Cargo = if ($CargoCommand) { $CargoCommand.Source } else { Join-Path $CargoBin "cargo.exe" }
if (-not (Test-Path -LiteralPath $Rustup -PathType Leaf)) { throw "rustup.exe is required for native Windows components" }
if (-not (Test-Path -LiteralPath $Cargo -PathType Leaf)) { throw "cargo.exe is required for native Windows components" }
Run-Native $Rustup @("target", "add", "x86_64-pc-windows-msvc", "--toolchain", "1.95.0") "install pinned native-component target"
# Cargo/rustc must inherit the same real VS 2022 x64 environment as Blender's
# Ninja build. This wrapper is build-time only; the packaged launcher does not
# mutate PATH or invoke a command shell to discover adjacent native DLLs.
if (-not (Get-Variable Vcvars64 -Scope Script -ErrorAction SilentlyContinue) -or
    -not (Test-Path -LiteralPath $Vcvars64 -PathType Leaf)) {
    $VsWhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    $VisualStudioRoot = ""
    if (Test-Path -LiteralPath $VsWhere -PathType Leaf) {
        $VisualStudioRoot = @(& $VsWhere -latest -version '[17.0,18.0)' -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath | Select-Object -First 1)
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
}
$CargoWrapper = Join-Path $Workspace "run_cargo_with_msvc.cmd"
@"
@echo off
call "$Vcvars64" >nul
if errorlevel 1 exit /b 1
"$Cargo" +1.95.0 %*
exit /b %errorlevel%
"@ | Set-Content -LiteralPath $CargoWrapper -Encoding ASCII
# Only the in-app ABI is portable to Windows; the external MCP server uses Unix FDs.
$AutomationManifest = Join-Path $NarwhalSource "native\automation_core\Cargo.toml"
Run-Native $Cmd @("/d", "/c", $CargoWrapper, "build", "--release", "--locked", "--lib", "--manifest-path", $AutomationManifest, "--target", "x86_64-pc-windows-msvc") "build in-app automation ABI with pinned Rust MSVC" $NarwhalSource
$ApplicationInputsPath = Join-Path $Workspace "application-core-inputs.json"
$ApplicationInputsDriver = Join-Path $Workspace "application_core_inputs.py"
@'
# Build/source inventory orchestration only, not application logic.
import json
from pathlib import Path
import sys
source, mode, snapshot = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
sys.path.insert(0, str(source / "scripts"))
from build_linux_native import inventory, digest
inputs = inventory(source, "application_core")
if mode == "before":
    snapshot.write_text(json.dumps(inputs, sort_keys=True))
elif mode == "after":
    if json.loads(snapshot.read_text()) != inputs:
        raise SystemExit("application-core source changed during build")
    from verify_windows_release import verify_application_core_identity
    library = Path(sys.argv[4])
    verify_application_core_identity(library)
    library.with_name("application-core-source.json").write_text(json.dumps(
        {"inputs": inputs, "sha256": digest(library), "abi": 1, "history_abi": 1}, sort_keys=True))
else:
    raise SystemExit("unknown application-core inventory mode")
'@ | Set-Content -LiteralPath $ApplicationInputsDriver -Encoding UTF8
Run-Native $Python @('-B', $ApplicationInputsDriver, $NarwhalSource, 'before', $ApplicationInputsPath) 'record application-core source inventory'
foreach ($Crate in @("cdr_core", "cdr_multiversion", "welcome", "application_core")) {
    $Manifest = Join-Path $NarwhalSource "native\$Crate\Cargo.toml"
    Run-Native $Cmd @("/d", "/c", $CargoWrapper, "build", "--release", "--locked", "--manifest-path", $Manifest, "--target", "x86_64-pc-windows-msvc") "build $Crate with pinned Rust MSVC" $NarwhalSource
}
$DocumentManifest = Join-Path $NarwhalSource "native\document_core\Cargo.toml"
Run-Native $Cmd @("/d", "/c", $CargoWrapper, "build", "--release", "--locked", "--manifest-path", $DocumentManifest, "--target", "x86_64-pc-windows-msvc") "build native Narwhal document core with pinned Rust MSVC" $NarwhalSource
$SupervisorManifest = Join-Path $NarwhalSource "native\converter_supervisor\Cargo.toml"
Run-Native $Cmd @("/d", "/c", $CargoWrapper, "build", "--release", "--locked", "--manifest-path", $SupervisorManifest, "--target", "x86_64-pc-windows-msvc") "build bounded converter supervisor with pinned Rust MSVC" $NarwhalSource
$PureManifest = Join-Path $NarwhalSource "native\pure_core\Cargo.toml"
$PureGridSource = Join-Path $NarwhalSource "native\pure_core\src\gridsnap.rs"
if (-not (Select-String -LiteralPath $PureGridSource -SimpleMatch 'fn narwhal_pure_core_grid_snap(' -Quiet)) {
    throw "Pinned pure-core source lacks the required Grid Snap ABI export"
}
Run-Native $Cmd @("/d", "/c", $CargoWrapper, "build", "--release", "--locked", "--manifest-path", $PureManifest, "--target", "x86_64-pc-windows-msvc") "build current pure core including Grid Snap with pinned Rust MSVC" $NarwhalSource
$SelectionManifest = Join-Path $NarwhalSource "native\selection_core\Cargo.toml"
Run-Native $Cmd @("/d", "/c", $CargoWrapper, "build", "--release", "--locked", "--manifest-path", $SelectionManifest, "--target", "x86_64-pc-windows-msvc") "build selection core with pinned Rust MSVC" $NarwhalSource
$PlatformManifest = Join-Path $NarwhalSource "native\platform_core\Cargo.toml"
Run-Native $Cmd @("/d", "/c", $CargoWrapper, "build", "--release", "--locked", "--manifest-path", $PlatformManifest, "--target", "x86_64-pc-windows-msvc") "build Windows platform core with pinned Rust MSVC" $NarwhalSource
$DocumentCoreSource = Join-Path $NarwhalSource "native\document_core\target\x86_64-pc-windows-msvc\release\narwhal-document-core.exe"
$SupervisorSource = Join-Path $NarwhalSource "native\converter_supervisor\target\x86_64-pc-windows-msvc\release\narwhal-converter-supervisor.exe"
$PureCoreSource = Join-Path $NarwhalSource "native\pure_core\target\x86_64-pc-windows-msvc\release\narwhal_pure_core.dll"
$SelectionCoreSource = Join-Path $NarwhalSource "native\selection_core\target\x86_64-pc-windows-msvc\release\narwhal_selection_core.dll"
$PlatformCoreSource = Join-Path $NarwhalSource "native\platform_core\target\x86_64-pc-windows-msvc\release\narwhal_platform_core.dll"
$NativeComponentDirectory = Join-Path $ApplicationDirectory "runtime\windows-x86_64\bin"
$AdjacentNativeDirectory = Join-Path $ApplicationDirectory "narwhal3d\native"
New-Item -ItemType Directory -Path $NativeComponentDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $AdjacentNativeDirectory -Force | Out-Null
foreach ($Component in @(
    @("application_core", "narwhal_application_core.dll"),
    @("cdr_core", "narwhal_cdr_core.dll"),
    @("cdr_multiversion", "narwhal-cdr-multiversion.exe"),
    @("welcome", "narwhal-welcome.exe")
)) {
    $Built = Join-Path $NarwhalSource "native\$($Component[0])\target\x86_64-pc-windows-msvc\release\$($Component[1])"
    Copy-Item -LiteralPath $Built -Destination (Join-Path $NativeComponentDirectory $Component[1])
}
$ApplicationCoreTarget = Join-Path $NativeComponentDirectory "narwhal_application_core.dll"
Run-Native $Python @('-B', $ApplicationInputsDriver, $NarwhalSource, 'after', $ApplicationInputsPath, $ApplicationCoreTarget) 'verify application-core source and ABI inventory'
Copy-Item -LiteralPath $ApplicationCoreTarget -Destination (Join-Path $AdjacentNativeDirectory "narwhal_application_core.dll")
Copy-Item -LiteralPath (Join-Path $NarwhalSource "native\application_core\OPENNURBS_LICENSE") -Destination (Join-Path $ApplicationDirectory "licenses\application_core-OPENNURBS.txt")
$AutomationSource = Join-Path $NarwhalSource "native\automation_core\target\x86_64-pc-windows-msvc\release\narwhal_automation.dll"
$AutomationTarget = Join-Path $NativeComponentDirectory "narwhal_automation.dll"
Copy-Item -LiteralPath $AutomationSource -Destination $AutomationTarget
$DocumentCoreTarget = Join-Path $NativeComponentDirectory "narwhal-document-core.exe"
$SupervisorTarget = Join-Path $NativeComponentDirectory "narwhal-converter-supervisor.exe"
$PureCoreTarget = Join-Path $AdjacentNativeDirectory "narwhal_pure_core.dll"
$SelectionCoreTarget = Join-Path $AdjacentNativeDirectory "narwhal_selection_core.dll"
$PlatformCoreTarget = Join-Path $AdjacentNativeDirectory "narwhal_platform_core.dll"
Copy-Item -LiteralPath $DocumentCoreSource -Destination $DocumentCoreTarget
Copy-Item -LiteralPath $SupervisorSource -Destination $SupervisorTarget
Copy-Item -LiteralPath $PureCoreSource -Destination $PureCoreTarget
Copy-Item -LiteralPath $SelectionCoreSource -Destination $SelectionCoreTarget
Copy-Item -LiteralPath $PlatformCoreSource -Destination $PlatformCoreTarget
$DocumentVersion = @(Capture-Native $DocumentCoreTarget @("--version") "verify native document-core protocol")
if (($DocumentVersion -join "`n").Trim() -ne "narwhal-document-core 1") {
    throw "Native document core has the wrong protocol"
}
$SupervisorText = @(Capture-Native $SupervisorTarget @(
    "--timeout-ms", "5000", "--max-output", "4096", "--memory-mib", "64",
    "--", $Cmd, "/d", "/c", "exit", "0") "verify converter supervisor memory containment")
$SupervisorReport = (($SupervisorText -join "`n") | ConvertFrom-Json)
if ($SupervisorReport.returncode -ne 0 -or $SupervisorReport.timed_out -ne $false -or $null -ne $SupervisorReport.error) {
    throw "Native converter supervisor memory containment probe failed"
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

$OcctLibraryDirectories = @(Get-ChildItem -LiteralPath $RuntimeDirectory -Directory -Filter "cadquery_ocp_novtk.libs" -Recurse)
if ($OcctLibraryDirectories.Count -ne 1) {
    throw "Expected exactly one pinned Windows OCP DLL directory; found $($OcctLibraryDirectories.Count)"
}
$GeometryManifest = Join-Path $NarwhalSource "native\geometry_core\Cargo.toml"
$PriorOcctSource = $env:NARWHAL_OCCT_SOURCE_DIR
$PriorOcctLibraries = $env:NARWHAL_OCCT_LIB_DIR
try {
    $env:NARWHAL_OCCT_SOURCE_DIR = $OcctSource
    $env:NARWHAL_OCCT_LIB_DIR = $OcctLibraryDirectories[0].FullName
    Run-Native $Cmd @("/d", "/c", $CargoWrapper, "build", "--release", "--locked", "--manifest-path", $GeometryManifest, "--target", "x86_64-pc-windows-msvc") "build exact geometry core with pinned OCCT and MSVC" $NarwhalSource
} finally {
    $env:NARWHAL_OCCT_SOURCE_DIR = $PriorOcctSource
    $env:NARWHAL_OCCT_LIB_DIR = $PriorOcctLibraries
}
$GeometryCoreSource = Join-Path $NarwhalSource "native\geometry_core\target\x86_64-pc-windows-msvc\release\narwhal_geometry_core.dll"
$GeometryCoreTarget = Join-Path $AdjacentNativeDirectory "narwhal_geometry_core.dll"
if (-not (Test-Path -LiteralPath $GeometryCoreSource -PathType Leaf)) { throw "Windows geometry core build produced no DLL" }
Copy-Item -LiteralPath $GeometryCoreSource -Destination $GeometryCoreTarget

$PackagedTestDirectory = Join-Path $ApplicationDirectory "spike"
New-Item -ItemType Directory -Path $PackagedTestDirectory -Force | Out-Null
$PackagedDocumentTest = Join-Path $PackagedTestDirectory "test_narwhal_document.py"
Copy-Item -LiteralPath (Join-Path $NarwhalSource "spike\test_narwhal_document.py") -Destination $PackagedDocumentTest
try {
    Run-Native (Join-Path $RuntimeDirectory "blender.exe") @(
        "-b", "--factory-startup", "--python-exit-code", "1", "--python",
        $PackagedDocumentTest) "run packaged native-document Save/Open round trip"
} finally {
    Remove-Item -LiteralPath $PackagedTestDirectory -Recurse -Force -ErrorAction SilentlyContinue
}

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
Run-Native (Join-Path $RuntimeDirectory 'blender.exe') @(
    '-b', '--factory-startup', '--python-exit-code', '1', '--python', $RuntimeApiProbe
) 'verify packaged native UI APIs'
$NativeProbePath = Join-Path $Workspace "verify_packaged_native_cores.py"
@'
from __future__ import annotations
import ctypes
import json
from pathlib import Path
import sys

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if len(args) != 1:
    raise SystemExit("usage: packaged native probe -- <application-directory>")
app = Path(args[0]).resolve()
sys.path.insert(0, str(app))
from narwhal3d import native_geometry, native_platform, native_pure
from narwhal3d.kernel import occt_backend

native = app / "narwhal3d" / "native"
application = ctypes.CDLL(str(native / "narwhal_application_core.dll"))
application.narwhal_session_abi.argtypes = []
application.narwhal_session_abi.restype = ctypes.c_uint32
if application.narwhal_session_abi() != 1:
    raise SystemExit("application-core ABI mismatch")
getattr(application, "narwhal_session_json")
application.narwhal_history_abi.argtypes = []
application.narwhal_history_abi.restype = ctypes.c_uint32
if application.narwhal_history_abi() != 1:
    raise SystemExit("application-core history ABI mismatch")
getattr(application, "narwhal_history_json")
if (native / "narwhal_application_core.dll").read_bytes() != (app / "runtime/windows-x86_64/bin/narwhal_application_core.dll").read_bytes():
    raise SystemExit("application-core runtime/adjacent copies differ")
platform = ctypes.WinDLL(str(native / "narwhal_platform_core.dll"))
platform.narwhal_platform_core_abi_version.restype = ctypes.c_uint32
if platform.narwhal_platform_core_abi_version() != 1:
    raise SystemExit("platform-core ABI mismatch")
for name in (
    "narwhal_platform_file_dialog_v1",
    "narwhal_platform_dialog_result_copy_v1",
    "narwhal_platform_dialog_result_discard_v1",
    "narwhal_platform_message_box_v1",
):
    getattr(platform, name)
if native_platform._load() is None:
    raise SystemExit("packaged adjacent platform core was not discovered")
result = native_pure.grid_snap(
    (0.6, -0.6, 8.0), (0.0, 0.0, 0.0),
    (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), 1.0)
if result != (1.0, -1.0, 0.0):
    raise SystemExit(f"packaged Grid Snap ABI failed: {result!r}")
source = occt_backend.shape_to_bytes(occt_backend.make_box(4, 5, 6), triangles=False)
moved = native_geometry.move_component(object(), lambda: source, 1, 1, (1, 0, 0), live=False)
if not moved.ok or not moved.data or moved.data == source:
    raise SystemExit(f"packaged geometry-core move failed: {moved.kind} {moved.detail}")
shape = occt_backend.shape_from_bytes(moved.data)
if not occt_backend.is_airtight(shape):
    raise SystemExit("packaged geometry-core move is not airtight")
print(json.dumps({"geometry_abi": 4, "geometry_move": "airtight",
                  "application_abi": 1, "platform_abi": 1, "grid_snap": list(result)}, sort_keys=True))
'@ | Set-Content -LiteralPath $NativeProbePath -Encoding UTF8
Run-Native (Join-Path $RuntimeDirectory "blender.exe") @(
    "-b", "--factory-startup", "--python-exit-code", "1", "--python",
    $NativeProbePath, "--", $ApplicationDirectory) "verify adjacent native DLL discovery and Grid Snap ABI without shell hacks"

$PlatformPeProbePath = Join-Path $Workspace "verify_packaged_platform_pe.py"
@'
from __future__ import annotations
import json
from pathlib import Path
import sys

source = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(source / "scripts"))
import verify_windows_release
identity = verify_windows_release.verify_platform_core_identity(Path(sys.argv[2]))
print(json.dumps({
    "machine": identity["machine"],
    "exports": list(identity["exports"]),
    "dependencies": list(identity["dependencies"]),
}, sort_keys=True))
'@ | Set-Content -LiteralPath $PlatformPeProbePath -Encoding UTF8
Run-Native $RuntimePythonPath @("-I", $PlatformPeProbePath, $NarwhalSource, $PlatformCoreTarget) "verify platform-core PE ABI and native dialog imports"

Run-Native (Join-Path $RuntimeDirectory "blender.exe") @(
    "-b", "--factory-startup", "--python-exit-code", "1", "--python",
    (Join-Path $ApplicationDirectory "launch\bootstrap.py")) "run packaged headless bootstrap"
# The upstream branding smoke test uses os.access(..., os.X_OK) to check
# POSIX executable mode on a .desktop template. Windows does not implement
# POSIX executable permission bits, so this check is not meaningful here.
# Run a temporary Windows-adjusted copy next to the original test, preserving
# __file__-relative paths and every other assertion. Keep the source untouched.
# Icon generation fixtures need Pillow; keep this build-only dependency out of
# the distributed interpreter and retain the isolated test environment for reuse.
$BrandingVenv = Join-Path $Workspace 'branding-test-venv'
$BrandingPython = Join-Path $BrandingVenv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $BrandingPython)) {
    Run-Native $Python @('-m', 'venv', $BrandingVenv) 'create isolated branding-test interpreter'
}
$PillowWheel = Download-Locked ([pscustomobject]@{
    filename = 'pillow-12.0.0-cp313-cp313-win_amd64.whl'
    url = 'https://files.pythonhosted.org/packages/6d/2a/dd43dcfd6dae9b6a49ee28a8eedb98c7d5ff2de94a5d834565164667b97b/pillow-12.0.0-cp313-cp313-win_amd64.whl'
    sha256 = '4cf7fed4b4580601c4345ceb5d4cbf5a980d030fd5ad07c4d2ec589f95f09905'
}) $WheelCache
Run-Native $BrandingPython @('-m', 'pip', 'install', '--no-index', '--no-deps', $PillowWheel) 'install verified build-only icon test dependency'
$PreviousTestPython = $env:NARWHAL3D_TEST_PYTHON
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
    $env:NARWHAL3D_TEST_PYTHON = $BrandingPython
    [IO.File]::WriteAllText($WindowsBrandingTest, $BrandingText, [Text.UTF8Encoding]::new($false))
    Run-Native (Join-Path $RuntimeDirectory "blender.exe") @(
        "-b", "--factory-startup", "--python-exit-code", "1", "--python",
        $WindowsBrandingTest) "run Windows-compatible native branding source smoke test"
} finally {
    $env:NARWHAL3D_TEST_PYTHON = $PreviousTestPython
    Remove-Item -LiteralPath $WindowsBrandingTest -Force -ErrorAction SilentlyContinue
}

$BinaryHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $RuntimeDirectory "blender.exe")).Hash.ToLowerInvariant()
$PatchInputs = @()
foreach ($Relative in $NativeInputNames) {
    $Path = Join-Path $PatchDirectory $Relative
    $PatchInputs += [ordered]@{ name = $Relative; sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant() }
}
$Result = [ordered]@{
    schema = 1
    product = "Narwhal 3D"
    target = "windows-x86_64-engineering"
    version = $Version
    narwhal_revision = $NarwhalRevision
    blender_revision = $BlenderRevision
    parallel_jobs = $Jobs
    rebuild_runtime = [bool]$RebuildRuntime
    source_overlay_used = [bool]$SourceOverlay
    native_runtime_provenance = $RuntimeProvenance
    blender_exe_sha256 = $BinaryHash
    document_core_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $DocumentCoreTarget).Hash.ToLowerInvariant()
    converter_supervisor_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $SupervisorTarget).Hash.ToLowerInvariant()
    pure_core_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $PureCoreTarget).Hash.ToLowerInvariant()
    selection_core_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $SelectionCoreTarget).Hash.ToLowerInvariant()
    platform_core_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $PlatformCoreTarget).Hash.ToLowerInvariant()
    geometry_core_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $GeometryCoreTarget).Hash.ToLowerInvariant()
    platform_core_abi = 1
    geometry_core_abi = 4
    packaged_grid_snap_probe = $true
    packaged_geometry_move_probe = "airtight"
    converter_supervisor_memory_limit_probe_mib = 64
    packaged_native_document_round_trip = $true
    patch_inputs = $PatchInputs
    package_directory = $PackageName
    physical_gpu_validation_complete = $false
    code_signed = $false
}

$PackageZip = Join-Path $Desktop ($PackageName + ".zip")
$TemporaryPackageZip = $PackageZip + ".part.zip"
Remove-Item -LiteralPath $TemporaryPackageZip -Force -ErrorAction SilentlyContinue
Log ("Creating engineering package ZIP: " + $PackageZip)
Run-Native $Tar @("-a", "-cf", $TemporaryPackageZip, "-C", $ReturnDirectory, $PackageName) "create engineering package archive"
$ArchiveEntries = @(Capture-Native $Tar @("-tf", $TemporaryPackageZip) "verify engineering package archive" "" $true)
Log "DONE verify engineering package archive inventory"
foreach ($RequiredEntry in @(
    "$PackageName/runtime/blender.exe", "$PackageName/app/narwhal3d/__init__.py",
    "$PackageName/app/launch/bootstrap.py", "$PackageName/app/runtime/windows-x86_64/bin/narwhal-document-core.exe",
    "$PackageName/app/runtime/windows-x86_64/bin/narwhal-converter-supervisor.exe",
    "$PackageName/app/narwhal3d/native/narwhal_pure_core.dll",
    "$PackageName/app/narwhal3d/native/narwhal_selection_core.dll",
    "$PackageName/app/narwhal3d/native/narwhal_platform_core.dll",
    "$PackageName/app/narwhal3d/native/narwhal_geometry_core.dll",
    "$PackageName/Narwhal3D.cmd")) {
    if ($RequiredEntry -notin $ArchiveEntries) { throw "Archive is missing $RequiredEntry" }
}
if (Test-Path -LiteralPath $PackageZip) { Remove-Item -LiteralPath $PackageZip -Force }
Move-Item -LiteralPath $TemporaryPackageZip -Destination $PackageZip
$PackageHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $PackageZip).Hash.ToLowerInvariant()
($PackageHash + "  " + (Split-Path $PackageZip -Leaf)) |
    Set-Content -LiteralPath (Join-Path $ReturnDirectory "portable-package.sha256") -Encoding ASCII
$Result["completed_utc"] = (Get-Date).ToUniversalTime().ToString("o")
$Result["portable_package_sha256"] = $PackageHash
$Result["portable_package_size"] = (Get-Item -LiteralPath $PackageZip).Length
$Result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ResultPath -Encoding UTF8

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
$WorkspaceLock.Dispose()
