# Build the pinned Windows exact-save helper (brep_writer-win32-amd64.exe) from the official
# openNURBS source archive, mirroring scripts/build_brep_writer_source.sh. This is an explicit
# developer command for a Windows build guest: nothing downloads or compiles it at runtime, and
# it writes only to -WorkDir and -Out, never into a Narwhal source tree. Release assembly takes
# the -Out directory explicitly through assemble_windows_release.py --exact-save-helper-dir.
# -SelfCheck only validates this script's pinned identity and compile policy (no build).
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_brep_writer_windows.ps1 `
#     -Archive C:\inputs\opennurbs-eb92af3ba1806b0a34a99aba0d3bda83e3d46083.tar.gz `
#     -Source C:\inputs\main.cpp -Out C:\out\brep-writer-win32
[CmdletBinding()]
param(
    [string]$Archive = "",
    [string]$Source = "",
    [string]$Out = "",
    [string]$Vcvars = "",
    [string]$WorkDir = "",
    [switch]$SelfCheck
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

# Pins shared with scripts/build_brep_writer_source.sh (same openNURBS commit and archive).
$OpenNurbsCommit = "eb92af3ba1806b0a34a99aba0d3bda83e3d46083"
$ArchiveSha256 = "1a8bb8dbe363f4969f679f11810251433416b62e595a6f02c447381a9f2128fa"
# /MT and the MultiThreaded CMake runtime link the static MSVC runtime into both libraries and helper.
$CmakeOptions = @("-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded")
$CompileOptions = @("/nologo", "/O2", "/EHsc", "/std:c++17", "/MT", "/DNOMINMAX")
$SystemLibraries = @("shlwapi.lib", "usp10.lib", "shell32.lib")
$HelperName = "brep_writer-win32-amd64.exe"
$LicenseName = "openNURBS-LICENSE.txt"
$ProtocolPrefix = "brep_writer ONBREP 1"

if ($SelfCheck) {
    if ($OpenNurbsCommit -notmatch '^[0-9a-f]{40}$') { throw "pinned openNURBS commit is malformed" }
    if ($ArchiveSha256 -notmatch '^[0-9a-f]{64}$') { throw "pinned archive SHA-256 is malformed" }
    if ($CmakeOptions -notcontains "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded") {
        throw "CMake does not pin the MultiThreaded runtime"
    }
    if (($CompileOptions -notcontains "/MT") -or ($CompileOptions -notcontains "/DNOMINMAX")) {
        throw "compile options lack /MT or /DNOMINMAX"
    }
    Write-Output "build_brep_writer_windows.ps1 self-check: OK ($OpenNurbsCommit)"
    exit 0
}

if (-not $Archive -or -not $Source -or -not $Out) { throw "-Archive, -Source and -Out are required" }

function Get-RegularFile([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label is missing: $Path" }
    $Item = Get-Item -LiteralPath $Path -Force
    if ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "$Label must not be a link: $Path" }
    return $Item.FullName
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

$ArchivePath = Get-RegularFile $Archive "openNURBS archive"
$SourcePath = Get-RegularFile $Source "helper source"
$ArchiveDigest = Get-Sha256 $ArchivePath
if ($ArchiveDigest -ne $ArchiveSha256) {
    throw "openNURBS archive SHA-256 mismatch: expected $ArchiveSha256, got $ArchiveDigest"
}

if (-not $WorkDir) { $WorkDir = Join-Path $env:TEMP ("narwhal-brep-writer-" + [guid]::NewGuid().ToString("N")) }
$WorkDir = [IO.Path]::GetFullPath($WorkDir)
if ((Test-Path -LiteralPath $WorkDir) -and @(Get-ChildItem -LiteralPath $WorkDir -Force).Count -ne 0) {
    throw "WorkDir must be absent or empty: $WorkDir"
}
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$OutDirectory = (Resolve-Path -LiteralPath $Out).ProviderPath

if (-not $Vcvars) {
    $VsWhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path -LiteralPath $VsWhere -PathType Leaf) {
        $VisualStudioRoot = @(& $VsWhere -latest -version '[17.0,18.0)' -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath | Select-Object -First 1)
        if ($VisualStudioRoot.Count -gt 0) {
            $Vcvars = Join-Path ([string]$VisualStudioRoot[0]) "VC\Auxiliary\Build\vcvars64.bat"
        }
    }
}
if (-not $Vcvars) {
    foreach ($Edition in @("Community", "Professional", "Enterprise", "BuildTools")) {
        $Candidate = Join-Path "C:\Program Files\Microsoft Visual Studio\2022" (Join-Path $Edition "VC\Auxiliary\Build\vcvars64.bat")
        if (Test-Path -LiteralPath $Candidate -PathType Leaf) { $Vcvars = $Candidate; break }
    }
}
if (-not $Vcvars -or -not (Test-Path -LiteralPath $Vcvars -PathType Leaf)) {
    throw "Visual Studio C++ developer environment (vcvars64.bat) not found; pass -Vcvars"
}

# Each step runs in one cmd wrapper so cl, cmake and ninja share the same MSVC environment.
function Invoke-Msvc([string]$CommandLine, [string]$Purpose) {
    $Wrapper = Join-Path $WorkDir "msvc-step.cmd"
    Set-Content -LiteralPath $Wrapper -Encoding ASCII -Value @("@echo off", "call `"$Vcvars`" >nul", $CommandLine)
    & $env:ComSpec '/d' '/c' $Wrapper
    if ($LASTEXITCODE -ne 0) { throw "$Purpose failed with exit code $LASTEXITCODE" }
}

$BannerPath = Join-Path $WorkDir "cl-banner.txt"
Invoke-Msvc ('cl 2> "' + $BannerPath + '" & exit /b 0') "read MSVC compiler banner"
$CompilerBanner = (Get-Content -LiteralPath $BannerPath -TotalCount 1 | Out-String).Trim()
if (-not $CompilerBanner) { throw "MSVC compiler banner could not be read" }

$BuildRoot = Join-Path $WorkDir "opennurbs"
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
& "$env:SystemRoot\System32\tar.exe" -xzf $ArchivePath -C $BuildRoot --strip-components=1
if ($LASTEXITCODE -ne 0) { throw "tar could not extract the openNURBS archive" }
foreach ($Member in @("CMakeLists.txt", "opennurbs.h", "LICENSE")) {
    Get-RegularFile (Join-Path $BuildRoot $Member) "archive member $Member" | Out-Null
}
$LicensePath = Get-RegularFile (Join-Path $BuildRoot "LICENSE") "openNURBS LICENSE"

$BuildDirectory = Join-Path $WorkDir "build"
Invoke-Msvc ('cmake -S "' + $BuildRoot + '" -B "' + $BuildDirectory + '" ' + ($CmakeOptions -join " ")) "configure openNURBS"
Invoke-Msvc ('cmake --build "' + $BuildDirectory + '" --target opennurbsStatic zlib') "build openNURBS and zlib"

$OpenNurbsLibrary = @(Get-ChildItem -LiteralPath $BuildDirectory -Recurse -Filter "opennurbsStatic.lib" -File)
$ZlibLibrary = @(Get-ChildItem -LiteralPath $BuildDirectory -Recurse -Filter "zlib.lib" -File)
if ($OpenNurbsLibrary.Count -ne 1 -or $ZlibLibrary.Count -ne 1) {
    throw "expected exactly one opennurbsStatic.lib and one zlib.lib in the build tree"
}
$OpenNurbsPath = $OpenNurbsLibrary[0].FullName
$ZlibPath = $ZlibLibrary[0].FullName

$Executable = Join-Path $WorkDir $HelperName
$Object = Join-Path $WorkDir "brep_writer.obj"
$LinkInputs = (@($OpenNurbsPath, $ZlibPath) + $SystemLibraries | ForEach-Object { '"' + $_ + '"' }) -join " "
Invoke-Msvc ('cl ' + ($CompileOptions -join " ") + ' /I"' + $BuildRoot + '" "' + $SourcePath + '" /Fo"' + $Object + '" /Fe"' + $Executable + '" /link ' + $LinkInputs) "compile and link brep_writer"

$VersionOutput = @(& $Executable --version)
if ($LASTEXITCODE -ne 0) { throw "brep_writer --version exited $LASTEXITCODE" }
$VersionLine = ""
if ($VersionOutput.Count -gt 0) { $VersionLine = ([string]$VersionOutput[0]).Trim() }
if (-not $VersionLine.StartsWith($ProtocolPrefix, [StringComparison]::Ordinal)) {
    throw "brep_writer --version did not report '$ProtocolPrefix': '$VersionLine'"
}

Copy-Item -LiteralPath $Executable -Destination (Join-Path $OutDirectory $HelperName) -Force
Copy-Item -LiteralPath $LicensePath -Destination (Join-Path $OutDirectory $LicenseName) -Force
$OutputPath = Join-Path $OutDirectory $HelperName
$Record = [ordered]@{
    schema = 1
    helper = $HelperName
    opennurbs_commit = $OpenNurbsCommit
    archive_sha256 = $ArchiveDigest
    source_sha256 = Get-Sha256 $SourcePath
    license_sha256 = Get-Sha256 $LicensePath
    compiler_banner = $CompilerBanner
    cmake_options = $CmakeOptions
    compile_options = $CompileOptions
    system_libraries = $SystemLibraries
    library_sha256 = [ordered]@{
        opennurbsStatic = Get-Sha256 $OpenNurbsPath
        zlib = Get-Sha256 $ZlibPath
    }
    output = [ordered]@{
        sha256 = Get-Sha256 $OutputPath
        size = (Get-Item -LiteralPath $OutputPath).Length
    }
    version_line = $VersionLine
}
$Json = $Record | ConvertTo-Json -Depth 6
[IO.File]::WriteAllText((Join-Path $OutDirectory "build.json"), $Json + "`n", (New-Object System.Text.UTF8Encoding($false)))
Write-Output "built $OutputPath"
