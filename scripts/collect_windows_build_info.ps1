# Narwhal 3D Windows build-environment collector.
#
# Run from ordinary PowerShell (administrator rights are not required):
#   powershell -ExecutionPolicy Bypass -File .\collect_windows_build_info.ps1
#
# The script uploads nothing. It creates a timestamped folder and ZIP on the
# current user's Desktop. Review the files before sharing them.
[CmdletBinding()]
param(
    [switch]$SkipNetworkChecks
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$NarwhalRevision = "071200ac982437bff148d8a213fd896bad204555"
$BlenderRevision = "d13f752e3b9c4f8c261cda552b1021f8bcc0382c"
$TraceDrawRevision = "48c2e408f11f976b64a3fbb59ce1826315bf3b7c"

function Redact-Path([object]$Value) {
    if ($null -eq $Value) { return $null }
    $Text = [string]$Value
    if ($env:USERPROFILE) {
        $Text = [regex]::Replace(
            $Text, [regex]::Escape($env:USERPROFILE), "%USERPROFILE%",
            [Text.RegularExpressions.RegexOptions]::IgnoreCase)
    }
    if ($env:USERNAME) {
        $Text = [regex]::Replace(
            $Text, [regex]::Escape($env:USERNAME), "%USERNAME%",
            [Text.RegularExpressions.RegexOptions]::IgnoreCase)
    }
    # A build tool may live on a UNC share. Keep that fact without recording
    # the server name or address. Handle the extended \\?\UNC\host form first.
    $Text = [regex]::Replace(
        $Text, "(?i)^\\\\\?\\UNC\\[^\\]+\\", "\\%UNC_HOST%\")
    $Text = [regex]::Replace(
        $Text, "(?i)^\\\\[^\\\s]+\\", "\\%UNC_HOST%\")
    return $Text
}

function Try-Value([scriptblock]$Action, [object]$Fallback = $null) {
    try { return & $Action } catch { return $Fallback }
}

function Invoke-BoundedProcess([string]$FileName, [string[]]$Arguments, [int]$TimeoutMilliseconds = 15000) {
    try {
        $Start = New-Object System.Diagnostics.ProcessStartInfo
        $Start.FileName = $FileName
        $Start.Arguments = ($Arguments -join " ")
        $Start.UseShellExecute = $false
        $Start.CreateNoWindow = $true
        $Start.RedirectStandardOutput = $true
        $Start.RedirectStandardError = $true
        try {
            $Utf8 = New-Object System.Text.UTF8Encoding($false)
            $Start.StandardOutputEncoding = $Utf8
            $Start.StandardErrorEncoding = $Utf8
        } catch { }
        $Process = New-Object System.Diagnostics.Process
        $Process.StartInfo = $Start
        if (-not $Process.Start()) {
            return [ordered]@{ ok = $false; timed_out = $false; output = ""; error = "did not start" }
        }
        # Drain both redirected pipes while the process runs. Waiting first can
        # deadlock when a pipe buffer fills (notably localized vswhere JSON).
        $OutputTask = $Process.StandardOutput.ReadToEndAsync()
        $ErrorTask = $Process.StandardError.ReadToEndAsync()
        if (-not $Process.WaitForExit($TimeoutMilliseconds)) {
            try { $Process.Kill() } catch { }
            return [ordered]@{ ok = $false; timed_out = $true; output = ""; error = "timed out" }
        }
        $Output = $OutputTask.Result + "`n" + $ErrorTask.Result
        return [ordered]@{ ok = $true; timed_out = $false; output = $Output; error = $null }
    } catch {
        return [ordered]@{
            ok = $false; timed_out = $false; output = ""
            error = "process error (" + $_.Exception.GetType().Name + ")"
        }
    }
}

function Command-Info([string]$Name, [string[]]$VersionArguments) {
    $Command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $Command) {
        return [ordered]@{ name = $Name; found = $false; path = $null; version = $null }
    }
    $CommandPath = $Command.Path
    if (-not $CommandPath -or $CommandPath -match "(?i)\\Microsoft\\WindowsApps\\") {
        return [ordered]@{
            name = $Name; found = $false; path = Redact-Path $CommandPath
            version = "Windows App Execution Alias is not a build tool"
        }
    }
    $Probe = Invoke-BoundedProcess $CommandPath $VersionArguments
    if ($Probe.timed_out) {
        $Version = "probe timed out after 15 seconds"
    } elseif (-not $Probe.ok) {
        $Version = "found; " + $Probe.error
    } else {
        $FirstLine = ($Probe.output -split "`r?`n") |
            Where-Object { $_ -and $_.Trim() } | Select-Object -First 1
        $Version = if ($null -eq $FirstLine) { "found; no version text" } else { $FirstLine.Trim() }
    }
    return [ordered]@{
        name = $Name
        found = $true
        path = Redact-Path $CommandPath
        version = Redact-Path $Version
    }
}

function Network-Info([string]$Name, [string]$Url) {
    if ($SkipNetworkChecks) {
        return [ordered]@{ name = $Name; url = $Url; reachable = $null; result = "skipped" }
    }
    try {
        $Response = Invoke-WebRequest -Uri $Url -Method Head -UseBasicParsing -MaximumRedirection 5 -TimeoutSec 30
        return [ordered]@{
            name = $Name
            url = $Url
            reachable = $true
            result = "HTTP " + [int]$Response.StatusCode
        }
    } catch {
        return [ordered]@{
            name = $Name
            url = $Url
            reachable = $false
            result = "request failed (" + $_.Exception.GetType().Name + ")"
        }
    }
}

$Desktop = [Environment]::GetFolderPath("Desktop")
if (-not $Desktop) { throw "Windows did not provide a Desktop folder." }
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$RunSuffix = [Guid]::NewGuid().ToString("N").Substring(0, 8)
$OutputDirectory = Join-Path $Desktop ("Narwhal3D-Windows-Collection-" + $Stamp + "-" + $RunSuffix)
$ZipPath = $OutputDirectory + ".zip"
New-Item -ItemType Directory -Path $OutputDirectory -ErrorAction Stop | Out-Null

$OperatingSystem = Try-Value { Get-CimInstance Win32_OperatingSystem }
$ComputerSystem = Try-Value { Get-CimInstance Win32_ComputerSystem }
$Processors = @(Try-Value { Get-CimInstance Win32_Processor } @())
$VideoControllers = @(Try-Value { Get-CimInstance Win32_VideoController } @())
$SystemDrive = if ($env:SystemDrive) { $env:SystemDrive } else { "C:" }
$Disk = Try-Value { Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='" + $SystemDrive + "'") }
$LongPaths = Try-Value {
    [int](Get-ItemPropertyValue -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled)
} 0

$VsWhere = $null
$ProgramFilesX86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
if (-not $ProgramFilesX86) { $ProgramFilesX86 = $env:ProgramFiles }
if ($ProgramFilesX86) {
    $VsWhere = Join-Path $ProgramFilesX86 "Microsoft Visual Studio\Installer\vswhere.exe"
}
$VisualStudio = @()
if ($VsWhere -and (Test-Path -LiteralPath $VsWhere -PathType Leaf)) {
    $VsProbe = Invoke-BoundedProcess $VsWhere @(
        "-all", "-products", "*", "-requires",
        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-format", "json", "-utf8")
    if (-not $VsProbe.ok) {
        $VisualStudio += [ordered]@{
            error = if ($VsProbe.timed_out) { "vswhere probe timed out" } else { $VsProbe.error }
        }
    } else {
        try {
            $VsRecords = @($VsProbe.output | ConvertFrom-Json)
            foreach ($Record in $VsRecords) {
                $VisualStudio += [ordered]@{
                    display_name = $Record.displayName
                    version = $Record.installationVersion
                    path = Redact-Path $Record.installationPath
                    complete = [bool]$Record.isComplete
                    launchable = [bool]$Record.isLaunchable
                }
            }
        } catch {
            $VisualStudio += [ordered]@{ error = "vswhere returned invalid JSON" }
        }
    }
}

$Tools = @(
    Command-Info "git" @("--version")
    Command-Info "cmake" @("--version")
    Command-Info "ninja" @("--version")
    Command-Info "python" @("--version")
    Command-Info "py" @("--version")
    Command-Info "cl" @()
    Command-Info "rustc" @("--version")
    Command-Info "cargo" @("--version")
    Command-Info "iscc" @("/Q")
)

if (-not $SkipNetworkChecks) {
    try {
        [Net.ServicePointManager]::SecurityProtocol =
            [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    } catch { }
}

$Dependencies = @(
    Network-Info "Narwhal 3D source revision" ("https://github.com/ContributionRich/Narwhal3D/archive/" + $NarwhalRevision + ".zip")
    Network-Info "Blender source revision" ("https://projects.blender.org/blender/blender/archive/" + $BlenderRevision + ".zip")
    Network-Info "rhino3dm 8.35.0 cp313 win_amd64" "https://files.pythonhosted.org/packages/de/17/735bc8a95a5667b091c7e8f77e8ba54871cbdd99569609f72f01bb7789e9/rhino3dm-8.35.0-cp313-cp313-win_amd64.whl"
    Network-Info "cadquery-ocp-novtk 8.0.1.0.0 cp313 win_amd64" "https://files.pythonhosted.org/packages/d7/b9/e350209cc87210df5f989ffc5d19cae8c499d69bca9cb36ec770be6cd9c7/cadquery_ocp_novtk-8.0.1.0.0-cp313-cp313-win_amd64.whl"
    Network-Info "cadquery-ocp-proxy 8.0.1.0.0" "https://files.pythonhosted.org/packages/15/36/4a8dbfdf4ab61a7841c36398615c6ab3d3c932d4254b978963690650feea/cadquery_ocp_proxy-8.0.1.0.0-py3-none-any.whl"
    Network-Info "TraceDraw corrected Windows evaluation build" "https://github.com/igtoth/tracedraw/releases/download/nightly/tracedraw-0.4.0-dev.48c2e40-windows-x86_64-portable.zip"
)

$Report = [ordered]@{
    schema = 1
    product = "Narwhal 3D"
    purpose = "Windows native build planning"
    collected_utc = (Get-Date).ToUniversalTime().ToString("o")
    privacy = [ordered]@{
        uploaded = $false
        username_collected = $false
        computer_name_collected = $false
        serial_numbers_collected = $false
        network_addresses_collected = $false
        paths_redacted = $true
    }
    target = [ordered]@{
        architecture = "x86_64"
        narwhal_source_revision = $NarwhalRevision
        blender_source_revision = $BlenderRevision
        tracedraw_evaluation_revision = $TraceDrawRevision
    }
    powershell = [ordered]@{
        version = $PSVersionTable.PSVersion.ToString()
        edition = Try-Value { $PSVersionTable.PSEdition } "Desktop"
        process_is_64_bit = [Environment]::Is64BitProcess
        os_is_64_bit = [Environment]::Is64BitOperatingSystem
        execution_policy = Redact-Path (Get-ExecutionPolicy)
    }
    operating_system = [ordered]@{
        caption = Try-Value { $OperatingSystem.Caption }
        version = Try-Value { $OperatingSystem.Version }
        build_number = Try-Value { $OperatingSystem.BuildNumber }
        architecture = Try-Value { $OperatingSystem.OSArchitecture }
    }
    hardware = [ordered]@{
        memory_gib = if ($null -ne $ComputerSystem) { [math]::Round($ComputerSystem.TotalPhysicalMemory / 1GB, 2) } else { $null }
        processors = @($Processors | ForEach-Object {
            [ordered]@{ name = $_.Name.Trim(); cores = $_.NumberOfCores; logical_processors = $_.NumberOfLogicalProcessors }
        })
        video_controllers = @($VideoControllers | ForEach-Object {
            [ordered]@{ name = $_.Name; driver_version = $_.DriverVersion; memory_gib = if ($_.AdapterRAM) { [math]::Round($_.AdapterRAM / 1GB, 2) } else { $null } }
        })
        system_drive = $SystemDrive
        system_drive_free_gib = if ($null -ne $Disk) { [math]::Round($Disk.FreeSpace / 1GB, 2) } else { $null }
    }
    windows_build_settings = [ordered]@{
        long_paths_enabled = ($LongPaths -eq 1)
        visual_studio_with_cpp = $VisualStudio
        developer_command_prompt_active = [bool]$env:VSCMD_VER
        windows_sdk_dir = Redact-Path $env:WindowsSdkDir
        vc_tools_install_dir = Redact-Path $env:VCToolsInstallDir
    }
    tools = $Tools
    dependency_reachability = $Dependencies
}

$JsonPath = Join-Path $OutputDirectory "windows-build-report.json"
$TextPath = Join-Path $OutputDirectory "windows-build-report.txt"
$ReadmePath = Join-Path $OutputDirectory "README-BEFORE-SHARING.txt"
$HashPath = Join-Path $OutputDirectory "collector-sha256.txt"

$Report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $JsonPath -Encoding UTF8

$Lines = @(
    "Narwhal 3D Windows build-environment report"
    "==========================================="
    "Collected UTC: " + $Report.collected_utc
    "Windows: " + $Report.operating_system.caption + " " + $Report.operating_system.version + " build " + $Report.operating_system.build_number
    "64-bit OS/process: " + $Report.powershell.os_is_64_bit + "/" + $Report.powershell.process_is_64_bit
    "RAM GiB: " + $Report.hardware.memory_gib
    "System drive free GiB: " + $Report.hardware.system_drive_free_gib
    "Long paths enabled: " + $Report.windows_build_settings.long_paths_enabled
    "Visual Studio C++ installations: " + @($VisualStudio).Count
    ""
    "Video controllers:"
)
foreach ($Video in $Report.hardware.video_controllers) {
    $Lines += "- " + $Video.name + " | driver " + $Video.driver_version + " | memory GiB " + $Video.memory_gib
}
$Lines += ""
$Lines += "Tools:"
foreach ($Tool in $Tools) {
    $Lines += "- " + $Tool.name + ": found=" + $Tool.found + " | " + $Tool.version + " | " + $Tool.path
}
$Lines += ""
$Lines += "Dependency reachability:"
foreach ($Dependency in $Dependencies) {
    $Lines += "- " + $Dependency.name + ": " + $Dependency.result
}
$Lines | Set-Content -LiteralPath $TextPath -Encoding UTF8

@"
This folder was generated locally by the Narwhal 3D Windows collector.
Nothing was uploaded automatically.

Before sharing:
1. Open windows-build-report.txt and windows-build-report.json.
2. Confirm they contain no information you do not want to share.
3. Send the ZIP beside this folder back to the Narwhal 3D maintainer.

The collector intentionally does NOT gather user names, computer names, serial
numbers, IP addresses, MAC addresses, Wi-Fi details, environment dumps, personal
files, browser data, registry exports, or proprietary CAD documents.

The TraceDraw nightly URL is checked only for evaluation planning. A mutable
nightly binary will not be accepted as a final bundled release dependency.
"@ | Set-Content -LiteralPath $ReadmePath -Encoding UTF8

if ($PSCommandPath -and (Test-Path -LiteralPath $PSCommandPath -PathType Leaf)) {
    $CollectorHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $PSCommandPath).Hash.ToLowerInvariant()
    ($CollectorHash + "  collect_windows_build_info.ps1") | Set-Content -LiteralPath $HashPath -Encoding ASCII
}

$ArchiveInputs = @(Get-ChildItem -LiteralPath $OutputDirectory -File | ForEach-Object { $_.FullName })
if (-not $ArchiveInputs) { throw "No report files were created." }
Compress-Archive -LiteralPath $ArchiveInputs -DestinationPath $ZipPath -CompressionLevel Optimal -Force

Write-Host ""
Write-Host "Narwhal 3D Windows collection complete." -ForegroundColor Green
Write-Host ("Folder: " + $OutputDirectory)
Write-Host ("ZIP:    " + $ZipPath)
Write-Host "Review README-BEFORE-SHARING.txt and the report before sending the ZIP."
