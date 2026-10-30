mod native_stacking;
mod theme;

use eframe::egui::{self, Color32, RichText, Stroke, Vec2};
use reqwest::blocking::{Client, Response};
use reqwest::redirect::Policy;
use serde::Deserialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicI32, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const READY_MARKER: &str = "[narwhal3d] first presentation released (ui-ready)";
// MEASURED: final welcome.css refinement, not its superseded base rules.
const WINDOW_SIZE: [f32; 2] = [640.0, 470.0];
const MIN_VISIBLE_SECONDS: f32 = 1.8;
const FADE_SECONDS: f32 = 0.38;
const ATTENTION_GRACE_SECONDS: f32 = 0.75;
const EXIT_PENDING: i32 = i32::MIN;
const SUBMIT_DELAY: Duration = Duration::from_millis(50);
const RELEASES_API: &str =
    "https://api.github.com/repos/ContributionRich/Narwhal3D/releases?per_page=20";
const RELEASES_PAGE: &str = "https://github.com/ContributionRich/Narwhal3D/releases";
const UPDATE_USER_AGENT: &str = concat!(
    "Narwhal3D-Welcome/",
    env!("CARGO_PKG_VERSION"),
    " (+https://github.com/ContributionRich/Narwhal3D)"
);
const API_RESPONSE_LIMIT: u64 = 2 * 1024 * 1024;
const DOWNLOAD_LIMIT: u64 = 2 * 1024 * 1024 * 1024;
// MEASURED: a 4 s connect timeout failed 3 of 6 release checks on an ordinary home
// connection (connects took up to 2.9 s); 10 s passed 8 of 8. The check runs on a
// background thread after first paint, so a longer wait never blocks the window.
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(20);
const DOWNLOAD_TIMEOUT: Duration = Duration::from_secs(180);
const NEW_UNITS: [(&str, &str); 5] = [
    ("Millimeters", "Millimeters"),
    ("Centimeters", "Centimeters"),
    ("Meters", "Meters"),
    ("Inches", "Inches"),
    ("Feet", "Feet"),
];

#[derive(Clone, Copy, PartialEq)]
enum Tab {
    New,
    Recent,
    Open,
    About,
}

enum RuntimeEvent {
    Ready,
    Exited(i32),
    Failed(String),
}

#[cfg_attr(not(test), allow(dead_code))]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum UpdatePlatform {
    LinuxX86_64,
    WindowsX86_64,
}

impl UpdatePlatform {
    fn current() -> Option<Self> {
        #[cfg(all(target_os = "linux", target_arch = "x86_64"))]
        return Some(Self::LinuxX86_64);
        #[cfg(all(target_os = "windows", target_arch = "x86_64"))]
        return Some(Self::WindowsX86_64);
        #[allow(unreachable_code)]
        None
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
struct ReleaseAsset {
    name: String,
    browser_download_url: String,
    digest: Option<String>,
    size: u64,
}

#[derive(Clone, Debug, Deserialize)]
struct GithubRelease {
    tag_name: String,
    #[serde(default)]
    name: String,
    #[serde(default)]
    body: String,
    html_url: String,
    #[serde(default)]
    draft: bool,
    assets: Vec<ReleaseAsset>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct AvailableRelease {
    version: String,
    title: String,
    notes: String,
    page_url: String,
    asset: ReleaseAsset,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum UpdateStatus {
    Idle,
    Checking,
    Unavailable,
    Unsafe,
    Current,
    Available,
    Updating,
}

impl UpdateStatus {
    fn label(self) -> &'static str {
        match self {
            Self::Idle => "Update status not checked",
            Self::Checking => "Checking for updates…",
            Self::Unavailable => "Update check unavailable",
            Self::Unsafe => "Unable to check securely",
            Self::Current => "You're up to date",
            Self::Available => "Update available",
            Self::Updating => "Installing update…",
        }
    }

    fn shows_releases(self) -> bool {
        matches!(self, Self::Unavailable | Self::Unsafe | Self::Available)
    }
}

struct UpdateCheck {
    status: UpdateStatus,
    release: Option<AvailableRelease>,
}

enum UpdateEvent {
    Checked(Result<UpdateCheck, String>),
    Installed(Result<String, String>),
}

struct LaunchConfig {
    runtime: PathBuf,
    bootstrap: PathBuf,
    forwarded: Vec<String>,
    request: PathBuf,
}

struct PendingRequest {
    temporary: PathBuf,
    action: String,
    publish_after: Instant,
}

impl PendingRequest {
    fn prepare(
        request: &Path,
        action: &str,
        argument: Option<&str>,
        now: Instant,
    ) -> std::io::Result<Self> {
        let temporary = request.with_extension("tmp");
        let mut file = fs::File::create(&temporary)?;
        file.write_all(request_payload(action, argument).as_bytes())?;
        file.sync_all()?;
        Ok(Self {
            temporary,
            action: action.to_owned(),
            publish_after: now + SUBMIT_DELAY,
        })
    }

    fn is_due(&self, now: Instant) -> bool {
        now >= self.publish_after
    }

    fn publish(self, request: &Path) -> std::io::Result<String> {
        replace_request(&self.temporary, request)?;
        Ok(self.action)
    }
}

struct WelcomeApp {
    theme: theme::Theme,
    tab: Tab,
    recent: Vec<PathBuf>,
    request: PathBuf,
    events: Receiver<RuntimeEvent>,
    sender: Sender<RuntimeEvent>,
    update_events: Receiver<UpdateEvent>,
    update_sender: Sender<UpdateEvent>,
    launch: Option<LaunchConfig>,
    pending_request: Option<PendingRequest>,
    native_window: Option<native_stacking::NativeWindowId>,
    cancel_child: Arc<AtomicBool>,
    exit_code: Arc<AtomicI32>,
    started: Instant,
    first_paint: Option<Instant>,
    lifetime: WelcomeLifetime,
    hidden: bool,
    ready: bool,
    child_exited: bool,
    status: String,
    current_version: Option<String>,
    update_check_started: bool,
    update_busy: bool,
    update_status: UpdateStatus,
    available_release: Option<AvailableRelease>,
    update_message: Option<String>,
    logo: egui::TextureHandle,
}

fn data_dir() -> PathBuf {
    if let Some(path) = std::env::var_os("NARWHAL3D_DATA_DIR") {
        return PathBuf::from(path);
    }
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    home.join(".local").join("share").join("narwhal3d")
}

fn request_path() -> PathBuf {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    std::env::temp_dir().join(format!(
        "narwhal3d-welcome-{}-{stamp}.request",
        std::process::id()
    ))
}

fn parse_args_from(args: impl IntoIterator<Item = String>) -> Result<LaunchConfig, String> {
    let mut args = args.into_iter();
    let mut runtime = None;
    let mut bootstrap = None;
    let mut forwarded = Vec::new();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--runtime" => runtime = args.next().map(PathBuf::from),
            "--bootstrap" => bootstrap = args.next().map(PathBuf::from),
            "--" => {
                forwarded.extend(args);
                break;
            }
            _ => return Err(format!("unknown welcome argument: {arg}")),
        }
    }
    Ok(LaunchConfig {
        runtime: runtime.ok_or("--runtime is required")?,
        bootstrap: bootstrap.ok_or("--bootstrap is required")?,
        forwarded,
        request: request_path(),
    })
}

fn parse_args() -> Result<LaunchConfig, String> {
    parse_args_from(std::env::args().skip(1))
}

fn recent_files_from(path: &Path) -> Vec<PathBuf> {
    let Ok(text) = fs::read_to_string(path) else {
        return Vec::new();
    };
    let Ok(value) = serde_json::from_str::<Value>(&text) else {
        return Vec::new();
    };
    let items = value
        .get("items")
        .and_then(Value::as_array)
        .or_else(|| value.as_array());
    items
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(PathBuf::from)
        .filter(|path| path.is_file())
        .take(8)
        .collect()
}

fn recent_files() -> Vec<PathBuf> {
    recent_files_from(&data_dir().join("recent.json"))
}

fn replace_request(temporary: &Path, request: &Path) -> std::io::Result<()> {
    // Unix rename replaces atomically. Windows rename refuses an existing destination, so remove
    // it first; the Python consumer treats that brief missing-path claim race as retryable.
    #[cfg(target_os = "windows")]
    match fs::remove_file(request) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(error),
    }
    fs::rename(temporary, request)
}

fn request_payload(action: &str, argument: Option<&str>) -> String {
    let mut payload = format!("{action}\n");
    if let Some(argument) = argument {
        payload.push_str(argument);
        payload.push('\n');
    }
    payload
}

fn is_ready_line(line: &str) -> bool {
    line == READY_MARKER
}

fn progress_state(ready: bool, elapsed: Duration) -> (f32, bool) {
    if ready {
        (1.0, false)
    } else {
        (((elapsed.as_secs_f32() * 0.55) % 1.0).max(0.04), true)
    }
}

fn parse_version(value: &str) -> Option<[u64; 3]> {
    let value = value.trim().trim_start_matches(['v', 'V']);
    let core = value.split(['-', '+']).next()?;
    let mut parts = core.split('.');
    let version = [
        parts.next()?.parse().ok()?,
        parts.next()?.parse().ok()?,
        parts.next()?.parse().ok()?,
    ];
    (parts.next().is_none() && version[0] == 0 && version[1] == 4).then_some(version)
}

fn current_version() -> Result<String, String> {
    match std::env::var("NARWHAL3D_VERSION") {
        Ok(version) if parse_version(&version).is_some() => Ok(version),
        Ok(_) => Err("NARWHAL3D_VERSION must be a 0.4.x version".to_owned()),
        Err(std::env::VarError::NotPresent) if cfg!(debug_assertions) => {
            Ok(env!("CARGO_PKG_VERSION").to_owned())
        }
        Err(std::env::VarError::NotPresent) => {
            Err("release launcher did not provide NARWHAL3D_VERSION".to_owned())
        }
        Err(std::env::VarError::NotUnicode(_)) => {
            Err("NARWHAL3D_VERSION is not valid Unicode".to_owned())
        }
    }
}

fn parse_sha256(value: &str) -> Option<[u8; 32]> {
    let hex = value.strip_prefix("sha256:")?;
    if hex.len() != 64 || !hex.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return None;
    }
    let mut result = [0_u8; 32];
    for (index, slot) in result.iter_mut().enumerate() {
        *slot = u8::from_str_radix(&hex[index * 2..index * 2 + 2], 16).ok()?;
    }
    Some(result)
}

fn digest_matches(actual: &[u8; 32], expected: &[u8; 32]) -> bool {
    actual == expected
}

fn asset_matches_platform(name: &str, platform: UpdatePlatform) -> bool {
    let name = name.to_ascii_lowercase();
    match platform {
        UpdatePlatform::LinuxX86_64 => {
            name.ends_with("-x86_64.appimage") && name.starts_with("narwhal_3d-")
        }
        UpdatePlatform::WindowsX86_64 => {
            name.ends_with("-windows-x86_64.zip") && name.starts_with("narwhal_3d-")
        }
    }
}

fn concise_text(value: &str, fallback: &str, limit: usize) -> String {
    let joined = value.split_whitespace().collect::<Vec<_>>().join(" ");
    let source = if joined.is_empty() { fallback } else { &joined };
    let mut result: String = source.chars().take(limit).collect();
    if source.chars().count() > limit {
        result.push('…');
    }
    result
}

fn release_page_url(value: &str) -> String {
    if value.starts_with("https://github.com/ContributionRich/Narwhal3D/releases/") {
        value.to_owned()
    } else {
        RELEASES_PAGE.to_owned()
    }
}

fn select_release(
    releases: &[GithubRelease],
    current: &str,
    platform: UpdatePlatform,
) -> Option<AvailableRelease> {
    let current = parse_version(current)?;
    releases
        .iter()
        .filter(|release| !release.draft)
        .filter_map(|release| {
            let version = parse_version(&release.tag_name)?;
            if version <= current {
                return None;
            }
            let asset = release.assets.iter().find(|asset| {
                asset_matches_platform(&asset.name, platform)
                    && asset.size > 0
                    && asset.size <= DOWNLOAD_LIMIT
                    && asset.digest.as_deref().and_then(parse_sha256).is_some()
            })?;
            Some((
                version,
                AvailableRelease {
                    version: release.tag_name.trim_start_matches(['v', 'V']).to_owned(),
                    title: concise_text(&release.name, &release.tag_name, 72),
                    notes: concise_text(&release.body, "See the release page for details.", 220),
                    page_url: release_page_url(&release.html_url),
                    asset: asset.clone(),
                },
            ))
        })
        .max_by_key(|(version, _)| *version)
        .map(|(_, release)| release)
}

fn read_limited(mut reader: impl Read, limit: u64) -> Result<Vec<u8>, String> {
    let mut bytes = Vec::new();
    reader
        .by_ref()
        .take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| format!("could not read response: {error}"))?;
    if bytes.len() as u64 > limit {
        return Err(format!("response exceeds {limit} bytes"));
    }
    Ok(bytes)
}

fn trusted_production_host(host: Option<&str>) -> bool {
    matches!(
        host,
        Some("api.github.com")
            | Some("github.com")
            | Some("objects.githubusercontent.com")
            | Some("release-assets.githubusercontent.com")
    )
}

fn http_client(timeout: Duration, allow_insecure_override: bool) -> Result<Client, String> {
    let redirect = Policy::custom(move |attempt| {
        if attempt.previous().len() >= 5 {
            return attempt.stop();
        }
        if !allow_insecure_override
            && (attempt.url().scheme() != "https"
                || !trusted_production_host(attempt.url().host_str()))
        {
            return attempt.stop();
        }
        attempt.follow()
    });
    Client::builder()
        .user_agent(UPDATE_USER_AGENT)
        .connect_timeout(CONNECT_TIMEOUT)
        .timeout(timeout)
        .redirect(redirect)
        .build()
        .map_err(|error| format!("could not configure secure HTTP: {error}"))
}

// reqwest's top-level message ("error sending request") hides the cause; name every
// source (DNS, TLS, timeout) so a failed release check can be diagnosed from the log.
fn error_chain(error: &dyn std::error::Error) -> String {
    let mut text = error.to_string();
    let mut source = error.source();
    while let Some(cause) = source {
        text.push_str(": ");
        text.push_str(&cause.to_string());
        source = cause.source();
    }
    text
}

fn releases_api() -> (String, bool) {
    if cfg!(debug_assertions) {
        if let Ok(value) = std::env::var("NARWHAL3D_RELEASES_API") {
            if !value.trim().is_empty() {
                return (value, true);
            }
        }
    }
    (RELEASES_API.to_owned(), false)
}

// OURS: distinguish a successful comparison from an empty/unsupported/unsafe feed.
// Never offer an older usable asset when the newest release cannot be verified.
fn classify_update(
    releases: &[GithubRelease],
    current: &str,
    platform: UpdatePlatform,
    overridden: bool,
) -> UpdateCheck {
    let outcome = |status| UpdateCheck {
        status,
        release: None,
    };
    let Some(installed) = parse_version(current) else {
        return outcome(UpdateStatus::Unavailable);
    };
    let published: Vec<_> = releases.iter().filter(|release| !release.draft).collect();
    if published
        .iter()
        .any(|release| parse_version(&release.tag_name).is_none())
    {
        return outcome(UpdateStatus::Unavailable);
    }
    let Some(latest) = published
        .into_iter()
        .max_by_key(|release| parse_version(&release.tag_name))
    else {
        return outcome(UpdateStatus::Unavailable);
    };
    if parse_version(&latest.tag_name).is_some_and(|version| version <= installed) {
        return outcome(UpdateStatus::Current);
    }
    if !latest
        .assets
        .iter()
        .any(|asset| asset_matches_platform(&asset.name, platform))
    {
        return outcome(UpdateStatus::Unavailable);
    }
    match select_release(std::slice::from_ref(latest), current, platform) {
        Some(release) if trusted_asset_url(&release.asset.browser_download_url, overridden) => {
            UpdateCheck {
                status: UpdateStatus::Available,
                release: Some(release),
            }
        }
        _ => outcome(UpdateStatus::Unsafe),
    }
}

fn check_for_update(current: &str, platform: UpdatePlatform) -> Result<UpdateCheck, String> {
    let (api, overridden) = releases_api();
    let client = http_client(REQUEST_TIMEOUT, overridden)?;
    let response = client
        .get(&api)
        .header("Accept", "application/vnd.github+json")
        .header("X-GitHub-Api-Version", "2022-11-28")
        .send()
        .map_err(|error| format!("release check failed: {}", error_chain(&error)))?;
    if !response.status().is_success() {
        return Err(format!("release service returned {}", response.status()));
    }
    if response
        .content_length()
        .is_some_and(|size| size > API_RESPONSE_LIMIT)
    {
        return Err("release response is too large".to_owned());
    }
    let bytes = read_limited(response, API_RESPONSE_LIMIT)?;
    let releases: Vec<GithubRelease> = serde_json::from_slice(&bytes)
        .map_err(|error| format!("invalid release response: {error}"))?;
    Ok(classify_update(&releases, current, platform, overridden))
}

fn trusted_asset_url(value: &str, overridden: bool) -> bool {
    let Ok(url) = reqwest::Url::parse(value) else {
        return false;
    };
    if overridden {
        return matches!(url.scheme(), "http" | "https");
    }
    url.scheme() == "https"
        && matches!(
            url.host_str(),
            Some("github.com")
                | Some("objects.githubusercontent.com")
                | Some("release-assets.githubusercontent.com")
        )
}

fn unique_stamp() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
}

fn download_verified(asset: &ReleaseAsset, destination: &Path) -> Result<(), String> {
    let (_, overridden) = releases_api();
    if !trusted_asset_url(&asset.browser_download_url, overridden) {
        return Err("release asset URL is not trusted".to_owned());
    }
    if asset.size == 0 || asset.size > DOWNLOAD_LIMIT {
        return Err("release asset size is outside the safe limit".to_owned());
    }
    let expected = asset
        .digest
        .as_deref()
        .and_then(parse_sha256)
        .ok_or("release asset has no valid GitHub sha256 digest")?;
    let client = http_client(DOWNLOAD_TIMEOUT, overridden)?;
    let result = (|| -> Result<(), String> {
        let mut response: Response = client
            .get(&asset.browser_download_url)
            .header("Accept", "application/octet-stream")
            .send()
            .map_err(|error| format!("download failed: {error}"))?;
        if !response.status().is_success() {
            return Err(format!("asset service returned {}", response.status()));
        }
        if response
            .content_length()
            .is_some_and(|size| size > DOWNLOAD_LIMIT || size != asset.size)
        {
            return Err("asset length does not match the signed release metadata".to_owned());
        }
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(destination)
            .map_err(|error| format!("could not create update file: {error}"))?;
        let mut hasher = Sha256::new();
        let mut total = 0_u64;
        let mut buffer = [0_u8; 64 * 1024];
        loop {
            let count = response
                .read(&mut buffer)
                .map_err(|error| format!("download read failed: {error}"))?;
            if count == 0 {
                break;
            }
            total = total
                .checked_add(count as u64)
                .ok_or("download size overflow")?;
            if total > DOWNLOAD_LIMIT || total > asset.size {
                return Err("download exceeded its declared size".to_owned());
            }
            hasher.update(&buffer[..count]);
            file.write_all(&buffer[..count])
                .map_err(|error| format!("could not write update: {error}"))?;
        }
        if total != asset.size {
            return Err("download was truncated".to_owned());
        }
        let actual: [u8; 32] = hasher.finalize().into();
        if !digest_matches(&actual, &expected) {
            return Err("download sha256 does not match GitHub's asset digest".to_owned());
        }
        file.sync_all()
            .map_err(|error| format!("could not sync verified update: {error}"))?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(destination);
    }
    result
}

fn safe_absolute_path(path: &Path) -> bool {
    path.is_absolute()
        && !path
            .as_os_str()
            .to_string_lossy()
            .chars()
            .any(char::is_control)
}

#[cfg(target_os = "linux")]
fn install_linux(release: &AvailableRelease) -> Result<String, String> {
    use std::os::unix::fs::PermissionsExt;

    let raw_target = std::env::var_os("APPIMAGE")
        .or_else(|| std::env::var_os("NARWHAL3D_PACKAGE_PATH"))
        .ok_or("cannot locate the current AppImage; use the release page")?;
    let target = PathBuf::from(raw_target);
    if !safe_absolute_path(&target) {
        return Err("current AppImage path is not a safe absolute path".to_owned());
    }
    let metadata = fs::symlink_metadata(&target)
        .map_err(|error| format!("cannot inspect current AppImage: {error}"))?;
    if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
        return Err("current AppImage must be a regular non-symlink file".to_owned());
    }
    let parent = target
        .parent()
        .ok_or("current AppImage has no parent directory")?;
    let staged = parent.join(format!(
        ".narwhal3d-update-{}-{}.AppImage",
        std::process::id(),
        unique_stamp()
    ));
    download_verified(&release.asset, &staged)?;
    let result = (|| -> Result<(), String> {
        fs::set_permissions(
            &staged,
            fs::Permissions::from_mode(metadata.permissions().mode()),
        )
        .map_err(|error| format!("could not preserve executable mode: {error}"))?;
        fs::rename(&staged, &target)
            .map_err(|error| format!("could not atomically replace current AppImage: {error}"))?;
        fs::File::open(parent)
            .and_then(|directory| directory.sync_all())
            .map_err(|error| format!("could not sync AppImage directory: {error}"))?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&staged);
    }
    result?;
    Ok(format!(
        "Narwhal 3D {} is installed. Restart to use it.",
        release.version
    ))
}

#[cfg(any(test, target_os = "windows"))]
const WINDOWS_UPDATER_SCRIPT: &str = r#"param(
    [Parameter(Mandatory=$true)][string]$Archive,
    [Parameter(Mandatory=$true)][string]$Target,
    [Parameter(Mandatory=$true)][string]$Staging,
    [Parameter(Mandatory=$true)][string]$Backup,
    [Parameter(Mandatory=$true)][int]$WelcomePid
)
$ErrorActionPreference = 'Stop'
try { Wait-Process -Id $WelcomePid -ErrorAction SilentlyContinue } catch {}
Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $Backup -Recurse -Force -ErrorAction SilentlyContinue

# Validate the central directory before Expand-Archive sees any path. The asset
# digest authenticates bytes; these checks additionally confine extraction to
# one ordinary portable-package tree under our staging directory.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$Zip = [System.IO.Compression.ZipFile]::OpenRead($Archive)
try {
    $Seen = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $Root = $null
    [uint64]$ExpandedBytes = 0
    [int]$EntryCount = 0
    foreach ($Entry in $Zip.Entries) {
        $Name = $Entry.FullName
        $EntryCount++
        if ($EntryCount -gt 200000) { throw 'Update ZIP contains too many entries.' }
        if ([string]::IsNullOrWhiteSpace($Name) -or $Name.Contains('\') -or
            $Name.StartsWith('/') -or $Name -match '^[A-Za-z]:' -or $Name -match '[\x00-\x1f]') {
            throw "Unsafe update ZIP path: $Name"
        }
        $Parts = @($Name.Split('/') | Where-Object { $_ -ne '' })
        if ($Parts.Count -eq 0) { throw "Unsafe update ZIP path: $Name" }
        foreach ($Part in $Parts) {
            if ($Part -eq '.' -or $Part -eq '..' -or $Part.EndsWith('.') -or $Part.EndsWith(' ') -or $Part.Contains(':')) {
                throw "Unsafe update ZIP path: $Name"
            }
        }
        if ($null -eq $Root) { $Root = $Parts[0] }
        if ($Parts[0] -cne $Root) { throw 'Update ZIP must contain one package directory.' }
        if (-not $Seen.Add($Name.TrimEnd('/'))) { throw "Duplicate update ZIP path: $Name" }
        $UnixType = ($Entry.ExternalAttributes -shr 16) -band 0xF000
        if ($UnixType -ne 0 -and $UnixType -ne 0x4000 -and $UnixType -ne 0x8000) {
            throw "Update ZIP contains a link or special file: $Name"
        }
        $ExpandedBytes += [uint64]$Entry.Length
        if ($ExpandedBytes -gt 8589934592) { throw 'Update ZIP expands beyond the safe limit.' }
        $Candidate = [System.IO.Path]::GetFullPath((Join-Path $Staging $Name))
        $StagePrefix = [System.IO.Path]::GetFullPath($Staging).TrimEnd('\') + '\'
        if (-not $Candidate.StartsWith($StagePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Update ZIP escapes its staging directory: $Name"
        }
    }
    if ($EntryCount -eq 0 -or [string]::IsNullOrWhiteSpace($Root)) { throw 'Update ZIP is empty.' }
    if (-not $Seen.Contains(($Root + '/Narwhal3D.exe'))) { throw 'Update package is missing Narwhal3D.exe.' }
    if (-not ($Seen | Where-Object { $_.StartsWith(($Root + '/app/'), [System.StringComparison]::OrdinalIgnoreCase) })) {
        throw 'Update package is missing its app directory.'
    }
} finally {
    $Zip.Dispose()
}
Expand-Archive -LiteralPath $Archive -DestinationPath $Staging
$Entries = @(Get-ChildItem -LiteralPath $Staging -Force)
if ($Entries.Count -ne 1 -or -not $Entries[0].PSIsContainer) { throw 'Update ZIP must contain one package directory.' }
$NewRoot = $Entries[0].FullName
if (@(Get-ChildItem -LiteralPath $NewRoot -Recurse -Force -Attributes ReparsePoint).Count -ne 0) { throw 'Update package contains a reparse point.' }
if (-not (Test-Path -LiteralPath (Join-Path $NewRoot 'Narwhal3D.exe') -PathType Leaf)) { throw 'Update package is missing Narwhal3D.exe.' }
Move-Item -LiteralPath $Target -Destination $Backup
try {
    Move-Item -LiteralPath $NewRoot -Destination $Target
    Remove-Item -LiteralPath $Backup -Recurse -Force
    Remove-Item -LiteralPath $Staging -Recurse -Force
    Remove-Item -LiteralPath $Archive -Force
} catch {
    if (-not (Test-Path -LiteralPath $Target) -and (Test-Path -LiteralPath $Backup)) {
        Move-Item -LiteralPath $Backup -Destination $Target
    }
    throw
}
"#;

#[cfg(any(test, target_os = "windows"))]
fn validate_windows_update_paths(paths: [&Path; 4]) -> Result<(), String> {
    if paths.iter().any(|path| !safe_absolute_path(path)) {
        return Err("Windows updater paths must be safe absolute paths".to_owned());
    }
    for left in 0..paths.len() {
        for right in left + 1..paths.len() {
            if paths[left] == paths[right] {
                return Err("Windows updater paths must be distinct".to_owned());
            }
        }
    }
    Ok(())
}

#[cfg(target_os = "windows")]
fn windows_package_root() -> Result<PathBuf, String> {
    if let Some(root) = std::env::var_os("NARWHAL3D_PACKAGE_PATH") {
        return Ok(PathBuf::from(root));
    }
    let executable = std::env::current_exe()
        .map_err(|error| format!("cannot locate welcome executable: {error}"))?;
    executable
        .ancestors()
        .nth(5)
        .map(Path::to_path_buf)
        .ok_or_else(|| "welcome executable is not inside a portable package".to_owned())
}

#[cfg(target_os = "windows")]
fn schedule_windows_update(release: &AvailableRelease) -> Result<String, String> {
    use std::os::windows::process::CommandExt;

    let target = windows_package_root()?;
    if !target.join("Narwhal3D.exe").is_file() || !target.join("app").is_dir() {
        return Err("current Windows portable package layout is not valid".to_owned());
    }
    let parent = target
        .parent()
        .ok_or("portable package has no parent directory")?;
    let stamp = format!("{}-{}", std::process::id(), unique_stamp());
    let archive = std::env::temp_dir().join(format!("narwhal3d-update-{stamp}.zip"));
    let script = std::env::temp_dir().join(format!("narwhal3d-update-{stamp}.ps1"));
    let staging = parent.join(format!(".narwhal3d-update-{stamp}.stage"));
    let backup = parent.join(format!(".narwhal3d-update-{stamp}.backup"));
    validate_windows_update_paths([&archive, &target, &staging, &backup])?;
    download_verified(&release.asset, &archive)?;
    if let Err(error) = fs::write(&script, WINDOWS_UPDATER_SCRIPT.as_bytes()) {
        let _ = fs::remove_file(&archive);
        return Err(format!("could not write Windows updater: {error}"));
    }
    let spawned = Command::new("powershell.exe")
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
        ])
        .arg(&script)
        .arg("-Archive")
        .arg(&archive)
        .arg("-Target")
        .arg(&target)
        .arg("-Staging")
        .arg(&staging)
        .arg("-Backup")
        .arg(&backup)
        .arg("-WelcomePid")
        .arg(std::process::id().to_string())
        .creation_flags(0x0800_0000)
        .spawn();
    if let Err(error) = spawned {
        let _ = fs::remove_file(&archive);
        let _ = fs::remove_file(&script);
        return Err(format!("could not schedule Windows updater: {error}"));
    }
    Ok(format!(
        "Narwhal 3D {} is verified and will install after Narwhal closes.",
        release.version
    ))
}

fn install_update(release: &AvailableRelease) -> Result<String, String> {
    #[cfg(target_os = "linux")]
    return install_linux(release);
    #[cfg(target_os = "windows")]
    return schedule_windows_update(release);
    #[allow(unreachable_code)]
    Err("automatic updates are not available on this platform; use the release page".to_owned())
}

#[derive(Default)]
struct WelcomeLifetime {
    last_attention: Option<Instant>,
    fade_started: Option<Instant>,
    engaged: bool,
    dismissed: bool,
}

impl WelcomeLifetime {
    fn attention_active(&self, now: Instant) -> bool {
        self.last_attention.is_some_and(|attention| {
            now.saturating_duration_since(attention)
                < Duration::from_secs_f32(ATTENTION_GRACE_SECONDS)
        })
    }

    fn note_attention(&mut self, now: Instant, interaction_started: bool) {
        if self.dismissed {
            return;
        }
        self.last_attention = Some(now);
        self.engaged |= interaction_started;
        self.fade_started = None;
    }

    fn maybe_start_auto_fade(&mut self, ready: bool, visible_for: Duration, now: Instant) {
        if ready
            && self.fade_started.is_none()
            && !self.engaged
            && !self.dismissed
            && !self.attention_active(now)
            && visible_for >= Duration::from_secs_f32(MIN_VISIBLE_SECONDS)
        {
            self.fade_started = Some(now);
        }
    }

    fn dismiss(&mut self) {
        self.dismissed = true;
    }

    fn fade_alpha(&self, now: Instant) -> f32 {
        if self.dismissed {
            return 0.0;
        }
        self.fade_started
            .map(|started| {
                (1.0 - now.saturating_duration_since(started).as_secs_f32() / FADE_SECONDS)
                    .clamp(0.0, 1.0)
            })
            .unwrap_or(1.0)
    }
}

fn load_logo(cc: &eframe::CreationContext<'_>) -> egui::TextureHandle {
    let bytes = include_bytes!("../../../launch/narwhal3d.png");
    let decoded = image::load_from_memory(bytes)
        .expect("embedded Narwhal icon must decode")
        .to_rgba8();
    let size = [decoded.width() as usize, decoded.height() as usize];
    let image = egui::ColorImage::from_rgba_unmultiplied(size, decoded.as_raw());
    cc.egui_ctx
        .load_texture("narwhal-logo", image, egui::TextureOptions::LINEAR)
}

fn supervise_child(
    mut child: Child,
    sender: Sender<RuntimeEvent>,
    cancel_child: Arc<AtomicBool>,
    exit_code: Arc<AtomicI32>,
) {
    loop {
        if cancel_child.load(Ordering::Acquire) {
            let _ = child.kill();
            let code = child
                .wait()
                .ok()
                .and_then(|status| status.code())
                .unwrap_or(1);
            exit_code.store(code, Ordering::Release);
            let _ = sender.send(RuntimeEvent::Exited(code));
            break;
        }
        match child.try_wait() {
            Ok(Some(status)) => {
                let code = status.code().unwrap_or(1);
                exit_code.store(code, Ordering::Release);
                let _ = sender.send(RuntimeEvent::Exited(code));
                break;
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(50)),
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                exit_code.store(1, Ordering::Release);
                let _ = sender.send(RuntimeEvent::Failed(format!(
                    "Narwhal runtime failed: {error}"
                )));
                break;
            }
        }
    }
}

fn spawn_runtime(
    config: &LaunchConfig,
    sender: Sender<RuntimeEvent>,
    native_window: Option<native_stacking::NativeWindowId>,
    cancel_child: Arc<AtomicBool>,
    exit_code: Arc<AtomicI32>,
) -> Result<(), String> {
    let mut command = Command::new(&config.runtime);
    command
        .arg("--factory-startup")
        .arg("--python")
        .arg(&config.bootstrap)
        .arg("--")
        .args(&config.forwarded)
        .env("NARWHAL3D_DEFER_FIRST_PRESENT", "1")
        .env_remove("NARWHAL3D_UI_READY")
        .env_remove("NARWHAL3D_PRESENT_WATCHDOG")
        .env("NARWHAL3D_WELCOME_REQUEST", &config.request)
        .stdout(Stdio::inherit())
        .stderr(Stdio::piped());
    native_stacking::configure_child(&mut command, native_window);
    let mut child = command
        .spawn()
        .map_err(|error| format!("could not start Narwhal: {error}"))?;
    native_stacking::start_worker(native_window, child.id());
    let stderr = child
        .stderr
        .take()
        .ok_or("could not monitor Narwhal startup")?;
    let ready_sender = sender.clone();
    std::thread::spawn(move || {
        let reader = BufReader::new(stderr);
        for line in reader.lines().map_while(Result::ok) {
            eprintln!("{line}");
            if is_ready_line(&line) {
                let _ = ready_sender.send(RuntimeEvent::Ready);
            }
        }
    });
    std::thread::spawn(move || supervise_child(child, sender, cancel_child, exit_code));
    Ok(())
}

impl WelcomeApp {
    fn new(
        cc: &eframe::CreationContext<'_>,
        config: LaunchConfig,
        cancel_child: Arc<AtomicBool>,
        exit_code: Arc<AtomicI32>,
    ) -> Self {
        let (sender, events) = mpsc::channel();
        let (update_sender, update_events) = mpsc::channel();
        let current_version = current_version().ok();
        let theme = theme::Theme::load();
        theme.apply(&cc.egui_ctx);
        Self {
            theme,
            tab: Tab::Recent,
            recent: recent_files(),
            request: config.request.clone(),
            events,
            sender,
            update_events,
            update_sender,
            launch: Some(config),
            pending_request: None,
            native_window: native_stacking::welcome_window_id(cc),
            cancel_child,
            exit_code,
            started: Instant::now(),
            first_paint: None,
            lifetime: WelcomeLifetime::default(),
            hidden: false,
            ready: false,
            child_exited: false,
            status: "Preparing Narwhal 3D…".to_owned(),
            current_version,
            update_check_started: false,
            update_busy: false,
            update_status: UpdateStatus::Idle,
            available_release: None,
            update_message: None,
            logo: load_logo(cc),
        }
    }

    fn action_status(action: &str) -> String {
        match action {
            "open" => "Opening selected model…".to_owned(),
            "open-dialog" => "Opening file browser…".to_owned(),
            _ => "Creating a new model…".to_owned(),
        }
    }

    fn submit_and_hide(&mut self, action: &str, argument: Option<&str>, ctx: &egui::Context) {
        if self.pending_request.is_some() {
            return;
        }
        match PendingRequest::prepare(&self.request, action, argument, Instant::now()) {
            Ok(pending) => {
                self.status = Self::action_status(action);
                self.pending_request = Some(pending);
                self.hide_surface(ctx);
            }
            Err(error) => {
                self.status = format!("Could not send startup request: {error}");
            }
        }
    }

    fn poll_submission(&mut self, ctx: &egui::Context) {
        let now = Instant::now();
        if !self
            .pending_request
            .as_ref()
            .is_some_and(|pending| pending.is_due(now))
        {
            return;
        }
        let pending = self
            .pending_request
            .take()
            .expect("pending request checked above");
        match pending.publish(&self.request) {
            Ok(action) => {
                self.status = Self::action_status(&action);
                self.lifetime.dismiss();
                self.mark_closed();
            }
            Err(error) => {
                self.status = format!("Could not send startup request: {error}");
                self.hidden = false;
                ctx.send_viewport_cmd(egui::ViewportCommand::Visible(true));
                ctx.send_viewport_cmd(egui::ViewportCommand::Focus);
            }
        }
    }

    fn mark_closed(&self) {
        if let Err(error) = fs::write(self.request.with_extension("closed"), b"") {
            eprintln!("[narwhal-welcome] could not close request channel: {error}");
        }
    }

    fn hide_surface(&mut self, ctx: &egui::Context) {
        if self.hidden {
            return;
        }
        self.hidden = true;
        ctx.send_viewport_cmd(egui::ViewportCommand::Visible(false));
        ctx.request_repaint_after(Duration::from_millis(16));
    }

    fn hide_welcome(&mut self, ctx: &egui::Context) {
        self.mark_closed();
        self.hide_surface(ctx);
    }

    fn poll_runtime(&mut self, ctx: &egui::Context) {
        while let Ok(event) = self.events.try_recv() {
            match event {
                RuntimeEvent::Ready => {
                    self.status = "Ready".to_owned();
                    self.ready = true;
                }
                RuntimeEvent::Exited(code) => {
                    self.child_exited = true;
                    if self.lifetime.fade_started.is_none() {
                        self.status = format!("Narwhal closed during startup (status {code})");
                    }
                }
                RuntimeEvent::Failed(error) => {
                    self.child_exited = true;
                    self.status = error;
                }
            }
        }
        if self.child_exited {
            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
        }
    }

    fn start_update_check(&mut self) {
        if self.update_check_started || std::env::var_os("NARWHAL3D_DISABLE_UPDATE_CHECK").is_some()
        {
            return;
        }
        self.update_check_started = true;
        let Some(version) = self.current_version.clone() else {
            self.update_status = UpdateStatus::Unavailable;
            self.update_message =
                Some("The launcher did not provide a valid installed version.".to_owned());
            return;
        };
        let Some(platform) = UpdatePlatform::current() else {
            self.update_status = UpdateStatus::Unavailable;
            self.update_message =
                Some("Use the release page for updates on this platform.".to_owned());
            return;
        };
        self.update_status = UpdateStatus::Checking;
        let sender = self.update_sender.clone();
        std::thread::spawn(move || {
            let result = check_for_update(&version, platform);
            let _ = sender.send(UpdateEvent::Checked(result));
        });
    }

    fn start_install(&mut self, release: AvailableRelease) {
        if self.update_busy || self.update_status != UpdateStatus::Available {
            return;
        }
        self.update_busy = true;
        self.update_status = UpdateStatus::Updating;
        self.update_message = Some("Downloading and verifying update…".to_owned());
        let sender = self.update_sender.clone();
        std::thread::spawn(move || {
            let result = install_update(&release);
            let _ = sender.send(UpdateEvent::Installed(result));
        });
    }

    fn poll_updates(&mut self) {
        while let Ok(event) = self.update_events.try_recv() {
            match event {
                UpdateEvent::Checked(Ok(check)) => {
                    if check.release.is_some() {
                        self.lifetime.fade_started = None;
                    }
                    self.update_status = check.status;
                    self.available_release = check.release;
                    self.update_message = None;
                }
                UpdateEvent::Checked(Err(error)) => {
                    eprintln!("[narwhal-welcome] {error}");
                    self.update_status = UpdateStatus::Unsafe;
                    self.update_message = Some("Could not check for updates safely.".to_owned());
                }
                UpdateEvent::Installed(result) => {
                    self.update_busy = false;
                    self.available_release = None;
                    self.update_message = Some(match result {
                        Ok(message) => {
                            // The running version has not changed; a restart/exit is still required.
                            self.update_status = UpdateStatus::Idle;
                            message
                        }
                        Err(error) => {
                            self.update_status = UpdateStatus::Unsafe;
                            eprintln!("[narwhal-welcome] update failed: {error}");
                            "Update failed safely. Download it from the release page.".to_owned()
                        }
                    });
                }
            }
        }
    }

    fn version_panel(&mut self, ui: &mut egui::Ui) {
        let p = self.theme.palette();
        ui.label(RichText::new("Version").size(13.0).strong());
        ui.add_space(12.0);
        ui.label(RichText::new("Installed version").size(11.0).color(p.muted));
        ui.add_space(5.0);
        ui.label(
            RichText::new(self.current_version.as_deref().unwrap_or("Not provided")).size(16.0),
        );
        ui.add_space(12.0);
        egui::Frame::new()
            .fill(p.field)
            .stroke(Stroke::new(1.0, p.border))
            .corner_radius(8)
            .inner_margin(egui::Margin::symmetric(11, 10))
            .show(ui, |ui| {
                ui.horizontal_top(|ui| {
                    ui.spacing_mut().item_spacing.x = 8.0;
                    let dot = match self.update_status {
                        UpdateStatus::Current => p.status_current,
                        UpdateStatus::Available => p.status_available,
                        UpdateStatus::Checking | UpdateStatus::Updating => p.status_working,
                        _ => p.status_neutral,
                    };
                    let (rect, _) =
                        ui.allocate_exact_size(Vec2::new(8.0, 16.0), egui::Sense::hover());
                    ui.painter().circle_filled(rect.center(), 4.0, dot);
                    ui.add(
                        egui::Label::new(RichText::new(self.update_status.label()).size(12.0))
                            .wrap(),
                    );
                });
            });
        if let Some(release) = self.available_release.clone() {
            ui.add_space(10.0);
            ui.label(RichText::new(format!("Latest version: {}", release.version)).size(12.0))
                .on_hover_text(&release.title);
            if self.update_status == UpdateStatus::Available
                && ui
                    .button("Download update")
                    .on_hover_text(&release.notes)
                    .clicked()
            {
                self.start_install(release);
            }
        }
        if self.update_status.shows_releases() {
            let url = self
                .available_release
                .as_ref()
                .map_or(RELEASES_PAGE, |release| &release.page_url);
            ui.hyperlink_to("View releases", url);
        }
        if let Some(message) = &self.update_message {
            ui.add(egui::Label::new(RichText::new(message).size(11.5).color(p.muted)).wrap());
        }
    }

    fn alpha(&mut self, ctx: &egui::Context, now: Instant) -> f32 {
        let alpha = self.lifetime.fade_alpha(now);
        if alpha <= 0.0 {
            self.hide_welcome(ctx);
        }
        ctx.request_repaint_after(Duration::from_millis(16));
        alpha
    }

    fn top_tabs(&mut self, ui: &mut egui::Ui) {
        let palette = self.theme.palette();
        let tabs = [
            (Tab::New, "New"),
            (Tab::Recent, "Recent"),
            (Tab::Open, "Open…"),
            (Tab::About, "About"),
        ];
        ui.horizontal(|ui| {
            ui.spacing_mut().item_spacing.x = 5.0;
            ui.spacing_mut().button_padding = Vec2::new(14.0, 0.0);
            ui.spacing_mut().interact_size.y = 30.0;
            for (tab, label) in tabs {
                let selected = self.tab == tab;
                let text = RichText::new(label).size(12.5).color(if selected {
                    palette.accent
                } else {
                    palette.muted
                });
                let button = egui::Button::new(text)
                    .fill(if selected {
                        palette.accent_soft
                    } else {
                        Color32::TRANSPARENT
                    })
                    .corner_radius(7)
                    .stroke(if selected {
                        Stroke::new(1.0, palette.accent.gamma_multiply(0.18))
                    } else {
                        Stroke::NONE
                    })
                    .min_size(Vec2::new(75.0, 34.0));
                if ui.add(button).clicked() {
                    self.tab = tab;
                }
            }
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                let close = egui::Button::new(RichText::new("×").size(21.0).color(palette.muted))
                    .min_size(Vec2::new(30.0, 30.0))
                    .frame(false);
                if ui
                    .add(close)
                    .on_hover_text("Close welcome window")
                    .clicked()
                {
                    self.lifetime.dismiss();
                    self.hide_welcome(ui.ctx());
                }
                egui::Frame::new()
                    .fill(palette.accent_soft)
                    .corner_radius(6)
                    .inner_margin(egui::Margin::symmetric(9, 7))
                    .show(ui, |ui| {
                        ui.label(
                            RichText::new("EARLY PREVIEW")
                                .size(10.0)
                                .color(palette.accent),
                        );
                    });
            });
        });
    }

    fn recent_grid(&mut self, ui: &mut egui::Ui) {
        let palette = self.theme.palette();
        if self.recent.is_empty() {
            ui.vertical_centered(|ui| {
                ui.add_space(54.0);
                ui.label(
                    RichText::new("No recent models yet")
                        .size(15.0)
                        .color(palette.strong),
                );
                ui.label("Use Open… to choose a .3dm model.");
            });
            return;
        }
        let recent = self.recent.clone();
        let tile_width = (ui.available_width() - 36.0) / 4.0;
        egui::Grid::new("recent-grid")
            .num_columns(4)
            .spacing([12.0, 14.0])
            .show(ui, |ui| {
                for (index, path) in recent.iter().enumerate() {
                    let name = path
                        .file_name()
                        .and_then(|value| value.to_str())
                        .unwrap_or("Model.3dm");
                    ui.vertical(|ui| {
                        ui.set_width(tile_width);
                        let preview = egui::Button::new(
                            RichText::new("NARWHAL\n3DM")
                                .size(10.0)
                                .strong()
                                .color(palette.chrome_text),
                        )
                        .fill(palette.chrome)
                        .stroke(Stroke::new(1.0, palette.border_strong))
                        .corner_radius(8)
                        .min_size(Vec2::new(tile_width, 78.0));
                        if ui
                            .add(preview)
                            .on_hover_text(path.display().to_string())
                            .clicked()
                        {
                            self.submit_and_hide(
                                "open",
                                Some(path.to_string_lossy().as_ref()),
                                ui.ctx(),
                            );
                        }
                        ui.add(
                            egui::Label::new(RichText::new(name).size(11.0).color(palette.text))
                                .truncate(),
                        );
                    });
                    if index % 4 == 3 {
                        ui.end_row();
                    }
                }
            });
    }

    fn new_panel(&mut self, ui: &mut egui::Ui) {
        ui.heading(RichText::new("Start a new model").color(self.theme.palette().strong));
        ui.add_space(3.0);
        ui.label(
            RichText::new("Choose the document unit system:").color(self.theme.palette().muted),
        );
        ui.add_space(20.0);
        let width = (ui.available_width() - 10.0) / 2.0;
        egui::Grid::new("welcome-units")
            .num_columns(2)
            .spacing([10.0, 10.0])
            .show(ui, |ui| {
                for (index, (label, unit)) in NEW_UNITS.into_iter().enumerate() {
                    if ui
                        .add_sized(
                            [width, 42.0],
                            egui::Button::new("")
                                .left_text(label)
                                .stroke(Stroke::new(1.0, self.theme.palette().border_strong)),
                        )
                        .clicked()
                    {
                        self.submit_and_hide("new", Some(unit), ui.ctx());
                    }
                    if index % 2 == 1 {
                        ui.end_row();
                    }
                }
            });
    }

    fn open_panel(&mut self, ui: &mut egui::Ui) {
        ui.heading(RichText::new("Open a model").color(self.theme.palette().strong));
        ui.add_space(3.0);
        ui.label(
            RichText::new("Open a .3dm or another supported model file.")
                .color(self.theme.palette().muted),
        );
        ui.add_space(20.0);
        let p = self.theme.palette();
        if ui
            .add_sized(
                [220.0, 40.0],
                egui::Button::new(RichText::new("Choose a model…").color(p.on_accent))
                    .fill(p.accent)
                    .stroke(Stroke::new(1.0, p.accent)),
            )
            .clicked()
        {
            self.submit_and_hide("open-dialog", None, ui.ctx());
        }
    }
}

impl Drop for WelcomeApp {
    fn drop(&mut self) {
        self.cancel_child.store(true, Ordering::Release);
    }
}

impl eframe::App for WelcomeApp {
    fn clear_color(&self, _visuals: &egui::Visuals) -> [f32; 4] {
        // OURS: transparent outside the rounded native frame; UI opacity owns the fade.
        self.theme.clear_color(0.0)
    }

    fn logic(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.poll_runtime(ctx);
        self.poll_submission(ctx);
        self.poll_updates();
        if self.first_paint.is_some() {
            self.start_update_check();
        }
        if self
            .first_paint
            .is_some_and(|painted| painted.elapsed() >= Duration::from_millis(350))
        {
            if let Some(config) = self.launch.take() {
                self.status = match spawn_runtime(
                    &config,
                    self.sender.clone(),
                    self.native_window,
                    Arc::clone(&self.cancel_child),
                    Arc::clone(&self.exit_code),
                ) {
                    Ok(()) => "Loading application components…".to_owned(),
                    Err(error) => {
                        self.exit_code.store(1, Ordering::Release);
                        self.child_exited = true;
                        error
                    }
                };
            }
        }

        let now = Instant::now();
        let visible_for = self
            .first_paint
            .map(|painted| now.saturating_duration_since(painted))
            .unwrap_or_default();
        self.lifetime.maybe_start_auto_fade(
            self.ready && self.available_release.is_none() && !self.update_busy,
            visible_for,
            now,
        );
        if self.hidden {
            ctx.request_repaint_after(Duration::from_millis(100));
        }
    }

    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        self.first_paint.get_or_insert_with(Instant::now);
        let ctx = ui.ctx().clone();
        let now = Instant::now();
        let (pointer_over, interaction_started) = ctx.input(|input| {
            let interacted = input.events.iter().any(|event| {
                matches!(
                    event,
                    egui::Event::PointerMoved(_)
                        | egui::Event::MouseMoved(_)
                        | egui::Event::PointerButton { .. }
                        | egui::Event::MouseWheel { .. }
                        | egui::Event::Touch { .. }
                        | egui::Event::Key { .. }
                        | egui::Event::Text(_)
                        | egui::Event::Paste(_)
                )
            });
            (input.pointer.hover_pos().is_some(), interacted)
        });
        if pointer_over || interaction_started {
            self.lifetime.note_attention(now, interaction_started);
        }
        let alpha_f = self.alpha(&ctx, now);
        if self.hidden {
            return;
        }
        let palette = self.theme.palette();
        // Fade the entire UI once, including custom text and theme-provided widgets.
        ui.set_opacity(alpha_f);
        self.theme.style_ui(ui);
        egui::Frame::new()
            .fill(palette.panel)
            .stroke(Stroke::new(1.0, palette.border_strong))
            .corner_radius(12)
            .show(ui, |ui| {
                // OURS: reserve header/footer before scrollable content so long release
                // details or recent filenames can never push progress out of the window.
                let bounds = egui::Rect::from_min_size(ui.cursor().min, Vec2::new(638.0, 468.0));
                let header_bottom = bounds.top() + 58.0;
                let footer_top = bounds.bottom() - 60.0;
                let divider_x = bounds.right() - 205.0;
                let region = |left, top, right, bottom| egui::Rect::from_min_max(
                    egui::pos2(left, top), egui::pos2(right, bottom));
                let header = region(bounds.left() + 18.0, bounds.top() + 12.0,
                    bounds.right() - 18.0, header_bottom - 12.0);
                ui.scope_builder(egui::UiBuilder::new().max_rect(header), |ui| self.top_tabs(ui));
                ui.painter().hline(bounds.x_range(), header_bottom, Stroke::new(1.0, palette.border));
                let sidebar = region(divider_x, header_bottom, bounds.right(), footer_top);
                ui.painter().rect_filled(sidebar, 0, palette.sidebar);
                ui.painter().vline(divider_x, header_bottom..=footer_top, Stroke::new(1.0, palette.border));
                let content = region(bounds.left() + 28.0, header_bottom + 30.0,
                    divider_x - 28.0, footer_top - 24.0);
                ui.scope_builder(egui::UiBuilder::new().max_rect(content), |ui| {
                    egui::ScrollArea::vertical().id_salt("welcome-content").show(ui, |ui| {
                        match self.tab {
                            Tab::New => self.new_panel(ui),
                            Tab::Recent => self.recent_grid(ui),
                            Tab::Open => self.open_panel(ui),
                            Tab::About => {
                                ui.heading("Narwhal 3D");
                                ui.label("Early precision NURBS CAD preview");
                                ui.label("Open-source, independently developed, and still under active construction.");
                            }
                        }
                    });
                });
                let side_content = region(divider_x + 20.0, header_bottom + 27.0,
                    bounds.right() - 20.0, footer_top - 66.0);
                ui.scope_builder(egui::UiBuilder::new().max_rect(side_content), |ui| {
                    egui::ScrollArea::vertical().id_salt("welcome-sidebar").show(ui, |ui| {
                        self.version_panel(ui);
                        ui.add_space(23.0);
                        ui.label(RichText::new("Tips").size(13.0).strong());
                        ui.label(RichText::new("Use the viewport-title arrow to change display mode.")
                            .size(11.5).color(palette.muted));
                    });
                });
                let branding = region(divider_x + 20.0, footer_top - 61.0,
                    bounds.right() - 20.0, footer_top - 18.0);
                ui.scope_builder(egui::UiBuilder::new().max_rect(branding), |ui| {
                    ui.painter().hline(branding.x_range(), branding.top(), Stroke::new(1.0, palette.border));
                    ui.add_space(10.0);
                    ui.horizontal(|ui| {
                        ui.spacing_mut().item_spacing.x = 10.0;
                        ui.image((self.logo.id(), Vec2::splat(33.0)));
                        ui.label(RichText::new("Narwhal 3D").size(13.0).color(palette.accent));
                    });
                });
                ui.painter().hline(bounds.x_range(), footer_top, Stroke::new(1.0, palette.border));
                let footer = region(bounds.left() + 18.0, footer_top + 13.0,
                    bounds.right() - 18.0, bounds.bottom() - 16.0);
                ui.scope_builder(egui::UiBuilder::new().max_rect(footer), |ui| {
                    ui.spacing_mut().item_spacing.y = 8.0;
                    ui.add(egui::Label::new(RichText::new(&self.status).size(11.5).color(palette.muted))
                        .truncate()).on_hover_text(&self.status);
                    let (progress, animate) = progress_state(self.ready, self.started.elapsed());
                    ui.add(egui::ProgressBar::new(progress).animate(animate)
                        .desired_height(6.0).desired_width(ui.available_width()));
                });
                ui.allocate_rect(bounds, egui::Sense::hover());
            });
        ctx.request_repaint_after(Duration::from_millis(33));
    }
}

fn main() -> eframe::Result {
    #[cfg(target_os = "linux")]
    if std::env::var_os("DISPLAY").is_some() {
        // winit 0.30 chooses Wayland whenever its display variables are present; its old
        // WINIT_UNIX_BACKEND override no longer exists. Use X11 for this process so the
        // welcome and runtime windows can have a real ICCCM transient relationship.
        std::env::remove_var("WAYLAND_DISPLAY");
        std::env::remove_var("WAYLAND_SOCKET");
    }
    let config = match parse_args() {
        Ok(config) => config,
        Err(error) => {
            eprintln!("narwhal-welcome: {error}");
            std::process::exit(2);
        }
    };
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_title("Narwhal 3D")
            .with_inner_size(WINDOW_SIZE)
            .with_min_inner_size(WINDOW_SIZE)
            .with_max_inner_size(WINDOW_SIZE)
            .with_resizable(false)
            .with_decorations(false)
            .with_transparent(true)
            .with_always_on_top(),
        centered: true,
        ..Default::default()
    };
    let cancel_child = Arc::new(AtomicBool::new(false));
    let exit_code = Arc::new(AtomicI32::new(EXIT_PENDING));
    let app_cancel = Arc::clone(&cancel_child);
    let app_exit = Arc::clone(&exit_code);
    eframe::run_native(
        "Narwhal 3D",
        options,
        Box::new(move |cc| Ok(Box::new(WelcomeApp::new(cc, config, app_cancel, app_exit)))),
    )?;
    cancel_child.store(true, Ordering::Release);
    for _ in 0..40 {
        if exit_code.load(Ordering::Acquire) != EXIT_PENDING {
            break;
        }
        std::thread::sleep(Duration::from_millis(25));
    }
    let code = exit_code.load(Ordering::Acquire);
    if code != EXIT_PENDING && code != 0 {
        std::process::exit(code);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{
        asset_matches_platform, digest_matches, error_chain, is_ready_line, parse_args_from,
        parse_sha256, parse_version, progress_state, read_limited, recent_files_from,
        replace_request, request_payload, safe_absolute_path, select_release, supervise_child,
        trusted_production_host, validate_windows_update_paths, GithubRelease, PendingRequest,
        ReleaseAsset, RuntimeEvent, UpdatePlatform, WelcomeLifetime, API_RESPONSE_LIMIT,
        ATTENTION_GRACE_SECONDS, CONNECT_TIMEOUT, DOWNLOAD_LIMIT, EXIT_PENDING, NEW_UNITS,
        READY_MARKER, REQUEST_TIMEOUT, SUBMIT_DELAY, UPDATE_USER_AGENT, WINDOWS_UPDATER_SCRIPT,
    };
    use std::fs;
    use std::io::Cursor;
    use std::path::Path;
    use std::sync::atomic::{AtomicBool, AtomicI32, Ordering};
    use std::sync::{mpsc, Arc};
    use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

    fn temporary_dir(label: &str) -> std::path::PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "narwhal-welcome-test-{label}-{}-{stamp}",
            std::process::id()
        ));
        fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn parser_forwards_only_arguments_after_separator() {
        let config = parse_args_from(
            [
                "--runtime",
                "/runtime",
                "--bootstrap",
                "/bootstrap.py",
                "--",
                "a.3dm",
                "--flag",
            ]
            .into_iter()
            .map(str::to_owned),
        )
        .unwrap();
        assert_eq!(config.runtime, Path::new("/runtime"));
        assert_eq!(config.bootstrap, Path::new("/bootstrap.py"));
        assert_eq!(config.forwarded, ["a.3dm", "--flag"]);
        assert!(parse_args_from(["unexpected".to_owned()]).is_err());
    }

    #[test]
    fn release_check_waits_long_enough_for_a_slow_connection() {
        assert!(CONNECT_TIMEOUT >= Duration::from_secs(10));
        assert!(REQUEST_TIMEOUT > CONNECT_TIMEOUT);
    }

    #[test]
    fn error_chain_names_every_cause() {
        #[derive(Debug)]
        struct Wrapper(std::io::Error);
        impl std::fmt::Display for Wrapper {
            fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                formatter.write_str("error sending request")
            }
        }
        impl std::error::Error for Wrapper {
            fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
                Some(&self.0)
            }
        }
        let wrapped = Wrapper(std::io::Error::new(
            std::io::ErrorKind::TimedOut,
            "timed out",
        ));
        assert_eq!(error_chain(&wrapped), "error sending request: timed out");
    }

    #[test]
    fn versions_compare_only_well_formed_zero_four_releases() {
        assert_eq!(parse_version("0.4.4"), Some([0, 4, 4]));
        assert_eq!(parse_version("v0.4.5-preview.1"), Some([0, 4, 5]));
        assert!(parse_version("0.4.5") > parse_version("0.4.4"));
        assert_eq!(parse_version("0.5.0"), None);
        assert_eq!(parse_version("0.4"), None);
        assert_eq!(parse_version("0.4.5.1"), None);
        assert_eq!(env!("CARGO_PKG_VERSION"), "0.4.7");
        assert!(UPDATE_USER_AGENT.contains(env!("CARGO_PKG_VERSION")));
    }

    fn asset(name: &str, size: u64, digest: Option<&str>) -> ReleaseAsset {
        ReleaseAsset {
            name: name.to_owned(),
            browser_download_url:
                "https://github.com/ContributionRich/Narwhal3D/releases/download/v0.4.5/asset"
                    .to_owned(),
            digest: digest.map(str::to_owned),
            size,
        }
    }

    fn release(tag: &str, assets: Vec<ReleaseAsset>) -> GithubRelease {
        GithubRelease {
            tag_name: tag.to_owned(),
            name: format!("Narwhal {tag}"),
            body: "Small release notes".to_owned(),
            html_url: format!("https://github.com/ContributionRich/Narwhal3D/releases/tag/{tag}"),
            draft: false,
            assets,
        }
    }

    #[test]
    fn v44_update_results_never_confuse_missing_or_unsafe_assets_with_current() {
        use super::{classify_update, UpdateStatus};
        let digest = format!("sha256:{}", "ab".repeat(32));
        for (platform, filename) in [
            (
                UpdatePlatform::LinuxX86_64,
                "Narwhal_3D-0.4.8-x86_64.AppImage",
            ),
            (
                UpdatePlatform::WindowsX86_64,
                "Narwhal_3D-0.4.8-Windows-x86_64.zip",
            ),
        ] {
            let good = release("v0.4.8", vec![asset(filename, 10, Some(&digest))]);
            let check =
                |releases: &[GithubRelease]| classify_update(releases, "0.4.7", platform, false);
            assert_eq!(check(&[]).status, UpdateStatus::Unavailable);
            assert_eq!(
                check(&[release("v0.4.7", vec![])]).status,
                UpdateStatus::Current
            );
            assert_eq!(
                check(&[release("v0.5.0", vec![])]).status,
                UpdateStatus::Unavailable
            );
            assert_eq!(
                check(&[release("v0.4.8", vec![])]).status,
                UpdateStatus::Unavailable
            );
            assert_eq!(
                check(std::slice::from_ref(&good)).status,
                UpdateStatus::Available
            );
            assert_eq!(
                check(std::slice::from_ref(&good)).release.unwrap().version,
                "0.4.8"
            );
            let mut draft = good.clone();
            draft.draft = true;
            assert_eq!(check(&[draft]).status, UpdateStatus::Unavailable);
            for bad_asset in [
                asset(filename, 10, None),
                asset(filename, 0, Some(&digest)),
                asset(filename, DOWNLOAD_LIMIT + 1, Some(&digest)),
                asset(filename, 10, Some("sha256:bad")),
                ReleaseAsset {
                    browser_download_url: "https://untrusted.invalid/update".to_owned(),
                    ..asset(filename, 10, Some(&digest))
                },
            ] {
                let result = check(&[release("v0.4.8", vec![bad_asset])]);
                assert_eq!(result.status, UpdateStatus::Unsafe);
                assert!(result.release.is_none());
            }
            let newer_unusable = release("v0.4.9", vec![asset(filename, 10, None)]);
            assert_eq!(check(&[good, newer_unusable]).status, UpdateStatus::Unsafe);
        }
    }

    #[test]
    fn v44_status_copy_and_release_actions_are_explicit() {
        use super::UpdateStatus::*;
        for (state, label, releases) in [
            (Idle, "Update status not checked", false),
            (Checking, "Checking for updates…", false),
            (Unavailable, "Update check unavailable", true),
            (Unsafe, "Unable to check securely", true),
            (Current, "You're up to date", false),
            (Available, "Update available", true),
            (Updating, "Installing update…", false),
        ] {
            assert_eq!(state.label(), label);
            assert_eq!(state.shows_releases(), releases);
        }
    }

    #[test]
    fn release_selection_requires_newer_version_platform_asset_digest_and_size() {
        let digest = format!("sha256:{}", "ab".repeat(32));
        let releases = vec![
            release(
                "v0.4.4",
                vec![asset("Narwhal_3D-0.4.4-x86_64.AppImage", 10, Some(&digest))],
            ),
            release(
                "v0.4.6",
                vec![asset(
                    "Narwhal_3D-0.4.6-Windows-x86_64.zip",
                    10,
                    Some(&digest),
                )],
            ),
            release(
                "v0.4.7",
                vec![asset(
                    "Narwhal_3D-0.4.7-x86_64.AppImage",
                    DOWNLOAD_LIMIT + 1,
                    Some(&digest),
                )],
            ),
            release(
                "v0.4.8",
                vec![asset("Narwhal_3D-0.4.8-x86_64.AppImage", 10, None)],
            ),
            release(
                "v0.4.5",
                vec![asset("Narwhal_3D-0.4.5-x86_64.AppImage", 10, Some(&digest))],
            ),
        ];
        let linux = select_release(&releases, "0.4.4", UpdatePlatform::LinuxX86_64).unwrap();
        assert_eq!(linux.version, "0.4.5");
        assert!(linux.asset.name.ends_with("-x86_64.AppImage"));
        let windows = select_release(&releases, "0.4.4", UpdatePlatform::WindowsX86_64).unwrap();
        assert_eq!(windows.version, "0.4.6");
        assert!(windows.asset.name.ends_with("-Windows-x86_64.zip"));
    }

    #[test]
    fn digest_parser_is_exact_and_case_insensitive_only_for_hex() {
        let digest = format!("sha256:{}", "aB".repeat(32));
        assert_eq!(parse_sha256(&digest), Some([0xab; 32]));
        assert_eq!(parse_sha256(&format!("SHA256:{}", "ab".repeat(32))), None);
        assert_eq!(parse_sha256(&format!("sha256:{}", "ab".repeat(31))), None);
        assert_eq!(parse_sha256(&format!("sha256:{}zz", "ab".repeat(31))), None);
        assert!(digest_matches(&[0xab; 32], &[0xab; 32]));
        assert!(!digest_matches(&[0xab; 32], &[0xac; 32]));
    }

    #[test]
    fn bounded_reader_rejects_one_byte_over_limit() {
        assert_eq!(
            read_limited(Cursor::new(vec![1_u8; 4]), 4).unwrap(),
            vec![1_u8; 4]
        );
        assert!(read_limited(Cursor::new(vec![1_u8; 5]), 4).is_err());
        assert!(API_RESPONSE_LIMIT < DOWNLOAD_LIMIT);
    }

    #[test]
    fn production_redirect_hosts_are_explicitly_bounded() {
        assert!(trusted_production_host(Some("api.github.com")));
        assert!(trusted_production_host(Some("github.com")));
        assert!(trusted_production_host(Some(
            "release-assets.githubusercontent.com"
        )));
        assert!(!trusted_production_host(Some("github.com.example.invalid")));
        assert!(!trusted_production_host(Some("example.invalid")));
        assert!(!trusted_production_host(None));
    }

    #[test]
    fn platform_asset_names_are_exact() {
        assert!(asset_matches_platform(
            "Narwhal_3D-0.4.5-x86_64.AppImage",
            UpdatePlatform::LinuxX86_64
        ));
        assert!(!asset_matches_platform(
            "Narwhal_3D-0.4.5-aarch64.AppImage",
            UpdatePlatform::LinuxX86_64
        ));
        assert!(asset_matches_platform(
            "Narwhal_3D-0.4.5-Windows-x86_64.zip",
            UpdatePlatform::WindowsX86_64
        ));
        assert!(!asset_matches_platform(
            "source-Windows-x86_64.zip",
            UpdatePlatform::WindowsX86_64
        ));
    }

    #[test]
    fn windows_updater_uses_literal_parameters_and_rejects_unsafe_paths() {
        assert!(WINDOWS_UPDATER_SCRIPT.contains("Wait-Process -Id $WelcomePid"));
        assert!(
            WINDOWS_UPDATER_SCRIPT.contains("[System.IO.Compression.ZipFile]::OpenRead($Archive)")
        );
        assert!(WINDOWS_UPDATER_SCRIPT.contains("$Name.Contains('\\')"));
        assert!(WINDOWS_UPDATER_SCRIPT.contains("$Part -eq '..'"));
        assert!(WINDOWS_UPDATER_SCRIPT.contains("Unsafe update ZIP path"));
        assert!(WINDOWS_UPDATER_SCRIPT.contains("Duplicate update ZIP path"));
        assert!(WINDOWS_UPDATER_SCRIPT.contains("Update ZIP expands beyond the safe limit"));
        assert!(WINDOWS_UPDATER_SCRIPT.contains("-Attributes ReparsePoint"));
        assert!(WINDOWS_UPDATER_SCRIPT.contains("Expand-Archive -LiteralPath $Archive"));
        assert!(WINDOWS_UPDATER_SCRIPT.contains("Move-Item -LiteralPath $Target"));
        assert!(!WINDOWS_UPDATER_SCRIPT.contains("Invoke-Expression"));
        assert!(!WINDOWS_UPDATER_SCRIPT.contains("Start-Process"));
        let archive = Path::new("/tmp/update.zip");
        let target = Path::new("/opt/Narwhal 3D");
        let staging = Path::new("/opt/.narwhal-stage");
        let backup = Path::new("/opt/.narwhal-backup");
        assert!(validate_windows_update_paths([archive, target, staging, backup]).is_ok());
        assert!(validate_windows_update_paths([archive, target, target, backup]).is_err());
        assert!(!safe_absolute_path(Path::new("relative/package")));
        assert!(!safe_absolute_path(Path::new("/tmp/bad\npath")));
    }

    #[test]
    fn readiness_marker_must_match_the_complete_line() {
        assert!(is_ready_line(READY_MARKER));
        assert!(!is_ready_line(&format!("prefix {READY_MARKER}")));
        assert!(!is_ready_line(
            "[narwhal3d] first presentation released (watchdog)"
        ));
    }

    #[test]
    fn lifetime_holds_for_attention_and_manual_close_is_authoritative() {
        let start = Instant::now();
        let mut lifetime = WelcomeLifetime::default();

        lifetime.maybe_start_auto_fade(false, Duration::from_secs(10), start);
        assert!(lifetime.fade_started.is_none());
        lifetime.maybe_start_auto_fade(true, Duration::from_millis(1799), start);
        assert!(lifetime.fade_started.is_none());
        lifetime.maybe_start_auto_fade(true, Duration::from_millis(1800), start);
        assert_eq!(lifetime.fade_started, Some(start));

        let reentered = start + Duration::from_millis(100);
        lifetime.note_attention(reentered, false);
        assert_eq!(lifetime.last_attention, Some(reentered));
        assert!(!lifetime.engaged);
        assert!(lifetime.fade_started.is_none());
        lifetime.maybe_start_auto_fade(true, Duration::from_secs(10), reentered);
        assert!(lifetime.fade_started.is_none());

        let after_grace =
            reentered + Duration::from_secs_f32(ATTENTION_GRACE_SECONDS) + Duration::from_millis(1);
        lifetime.maybe_start_auto_fade(true, Duration::from_secs(10), after_grace);
        assert_eq!(lifetime.fade_started, Some(after_grace));

        let interacted = after_grace + Duration::from_millis(50);
        lifetime.note_attention(interacted, true);
        assert!(lifetime.engaged);
        assert!(lifetime.fade_started.is_none());
        lifetime.maybe_start_auto_fade(
            true,
            Duration::from_secs(30),
            interacted + Duration::from_secs(30),
        );
        assert!(lifetime.fade_started.is_none());

        lifetime.dismiss();
        assert!(lifetime.dismissed);
        lifetime.note_attention(interacted + Duration::from_secs(31), true);
        assert!(lifetime.dismissed && lifetime.engaged);
        assert_eq!(lifetime.fade_alpha(interacted), 0.0);
    }

    #[test]
    fn pending_request_waits_for_a_hidden_cycle_before_publication() {
        let root = temporary_dir("pending-publisher");
        let request = root.join("welcome.request");
        let start = Instant::now();
        let pending = PendingRequest::prepare(&request, "new", Some("Feet"), start).unwrap();
        assert!(!request.exists());
        assert!(!pending.is_due(start + SUBMIT_DELAY - Duration::from_millis(1)));
        assert!(pending.is_due(start + SUBMIT_DELAY));
        assert_eq!(pending.publish(&request).unwrap(), "new");
        assert_eq!(fs::read_to_string(&request).unwrap(), "new\nFeet\n");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn pending_request_preparation_failure_cannot_publish() {
        let root = temporary_dir("failed-publisher");
        let request = root.join("missing").join("welcome.request");
        assert!(PendingRequest::prepare(&request, "open-dialog", None, Instant::now()).is_err());
        assert!(!request.exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn request_publisher_replaces_an_existing_choice_portably() {
        let root = temporary_dir("publisher");
        let request = root.join("welcome.request");
        let temporary = root.join("welcome.tmp");
        fs::write(&request, "old\n").unwrap();
        fs::write(&temporary, "new\n").unwrap();
        replace_request(&temporary, &request).unwrap();
        assert_eq!(fs::read_to_string(&request).unwrap(), "new\n");
        assert!(!temporary.exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn request_payload_and_new_units_are_line_oriented_and_exact() {
        assert_eq!(request_payload("new", None), "new\n");
        assert_eq!(request_payload("new", Some("Inches")), "new\nInches\n");
        assert_eq!(
            request_payload("open", Some("/models/Clock face.3dm")),
            "open\n/models/Clock face.3dm\n"
        );
        assert_eq!(
            NEW_UNITS,
            [
                ("Millimeters", "Millimeters"),
                ("Centimeters", "Centimeters"),
                ("Meters", "Meters"),
                ("Inches", "Inches"),
                ("Feet", "Feet"),
            ]
        );
    }

    #[test]
    fn ready_progress_is_complete_and_not_animated() {
        assert_eq!(progress_state(true, Duration::from_secs(10)), (1.0, false));
        let (progress, animate) = progress_state(false, Duration::from_millis(500));
        assert!((0.04..1.0).contains(&progress));
        assert!(animate);
    }

    #[cfg(unix)]
    #[test]
    fn supervisor_reports_a_natural_nonzero_child_exit() {
        let child = std::process::Command::new("sh")
            .args(["-c", "exit 7"])
            .spawn()
            .unwrap();
        let (sender, events) = mpsc::channel();
        let cancel = Arc::new(AtomicBool::new(false));
        let code = Arc::new(AtomicI32::new(EXIT_PENDING));
        supervise_child(child, sender, cancel, Arc::clone(&code));
        assert!(matches!(events.recv().unwrap(), RuntimeEvent::Exited(7)));
        assert_eq!(code.load(Ordering::Acquire), 7);
    }

    #[cfg(unix)]
    #[test]
    fn supervisor_kills_and_reaps_an_abandoned_child() {
        let child = std::process::Command::new("sh")
            .args(["-c", "exec sleep 30"])
            .spawn()
            .unwrap();
        let (sender, events) = mpsc::channel();
        let cancel = Arc::new(AtomicBool::new(true));
        let code = Arc::new(AtomicI32::new(EXIT_PENDING));
        supervise_child(child, sender, cancel, Arc::clone(&code));
        assert!(matches!(events.recv().unwrap(), RuntimeEvent::Exited(_)));
        assert_ne!(code.load(Ordering::Acquire), EXIT_PENDING);
    }

    #[test]
    fn recent_reader_tolerates_missing_and_corrupt_json_and_filters_files() {
        let root = temporary_dir("recent");
        let recent = root.join("recent.json");
        assert!(recent_files_from(&recent).is_empty());
        fs::write(&recent, "not json").unwrap();
        assert!(recent_files_from(&recent).is_empty());
        let model = root.join("kept.3dm");
        fs::write(&model, b"model").unwrap();
        let missing = root.join("missing.3dm");
        fs::write(
            &recent,
            serde_json::json!({"items": [model, missing]}).to_string(),
        )
        .unwrap();
        assert_eq!(recent_files_from(&recent), [root.join("kept.3dm")]);
        fs::remove_dir_all(root).unwrap();
    }
}
