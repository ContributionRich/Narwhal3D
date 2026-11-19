//! Windows outer-EXE updates. Never writes into a running payload cache.
//! A copied, current-version helper waits for the original launcher, verifies
//! both files again, and asks Windows to replace one file while retaining backup.
#![cfg(any(windows, test))]
#[cfg(windows)]
use crate::{payload::Payload, Limits};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use std::path::PathBuf;
use std::{
    fs::{self, OpenOptions},
    io::{self, Read, Write},
    path::Path,
};

const MAX_EXE: u64 = 5 * 1024 * 1024 * 1024;
fn invalid(text: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, text)
}
fn digest(path: &Path) -> io::Result<String> {
    crate::payload::no_link(&fs::symlink_metadata(path)?)?;
    let mut file = crate::pack::regular(path)?;
    if !file.metadata()?.is_file() || file.metadata()?.len() > MAX_EXE {
        return Err(invalid("update file size/type"));
    }
    let mut hash = Sha256::new();
    let mut buffer = [0; 131072];
    let mut count = 0u64;
    loop {
        let n = file.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        count += n as u64;
        if count > MAX_EXE {
            return Err(invalid("update file exceeds limit"));
        }
        hash.update(&buffer[..n]);
    }
    Ok(format!("{:x}", hash.finalize()))
}
pub(crate) fn copy_new(source: &Path, destination: &Path) -> io::Result<()> {
    let input = crate::pack::regular(source)?;
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(destination)?;
    let count = io::copy(&mut input.take(MAX_EXE + 1), &mut output)?;
    if count > MAX_EXE {
        return Err(invalid("update copy exceeds limit"));
    }
    output.flush()?;
    output.sync_all()
}
pub(crate) fn newer(old: &str, new: &str) -> bool {
    if !crate::version(old) || !crate::version(new) {
        return false;
    }
    new[4..]
        .parse::<u32>()
        .ok()
        .zip(old[4..].parse::<u32>().ok())
        .is_some_and(|(n, o)| n > o)
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Plan {
    target: PathBuf,
    old_sha256: String,
    new_sha256: String,
    old_version: String,
    new_version: String,
    launcher_pid: u32,
    launcher_created: u64,
}

#[cfg(windows)]
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Relaunch {
    helper_pid: u32,
    helper_created: u64,
}

fn record_receipt(directory: &Path, name: &str, hash: &str) -> io::Result<()> {
    let path = directory.join(name);
    match OpenOptions::new().write(true).create_new(true).open(&path) {
        Ok(mut file) => {
            file.write_all(hash.as_bytes())?;
            file.sync_all()
        }
        Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {
            if receipt(&path, hash)? {
                Ok(())
            } else {
                Err(invalid("invalid update receipt"))
            }
        }
        Err(e) => Err(e),
    }
}

/// OURS: installation is not runtime readiness. Only the supervising helper may
/// acknowledge its healthy relaunch; interrupted publication gets a notification
/// receipt instead, so it cannot masquerade as completed startup.
fn published_message(
    plan: &Plan,
    directory: &Path,
    supervised: bool,
) -> io::Result<Option<String>> {
    if supervised || receipt(&directory.join("recovery-notified"), &plan.new_sha256)? {
        return Ok(None);
    }
    record_receipt(directory, "recovery-notified", &plan.new_sha256)?;
    Ok(Some(format!("Update to {} is installed, but the previous relaunch was interrupted or its runtime readiness was not confirmed. Startup is continuing. Previous executable: {}", plan.new_version, directory.join("previous.exe").display())))
}

/// OURS: classify actual files, not a journal phase that may lag a power loss.
#[derive(Debug, PartialEq)]
enum Recovery {
    Prepared,
    Published,
    RestorePrevious,
}
fn receipt(path: &Path, expected: &str) -> io::Result<bool> {
    let mut bytes = Vec::new();
    match crate::pack::regular(path) {
        Ok(file) => {
            file.take(65).read_to_end(&mut bytes)?;
            Ok(bytes == expected.as_bytes())
        }
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(e) => Err(e),
    }
}
fn recovery(plan: &Plan, directory: &Path) -> io::Result<Recovery> {
    fn optional_hash(path: &Path) -> io::Result<Option<String>> {
        match digest(path) {
            Ok(hash) => Ok(Some(hash)),
            Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(None),
            Err(e) => Err(e),
        }
    }
    let target = optional_hash(&plan.target)?;
    let replacement = optional_hash(&directory.join("new.exe"))?;
    let backup = optional_hash(&directory.join("previous.exe"))?;
    if target.as_deref() == Some(&plan.new_sha256) && backup.as_deref() == Some(&plan.old_sha256) {
        return Ok(Recovery::Published);
    }
    if target.as_deref() == Some(&plan.old_sha256)
        && replacement.as_deref() == Some(&plan.new_sha256)
        && backup.is_none()
    {
        return Ok(Recovery::Prepared);
    }
    if target.is_none() && backup.as_deref() == Some(&plan.old_sha256) {
        return Ok(Recovery::RestorePrevious);
    }
    Err(invalid("Interrupted update has conflicting files; nothing was overwritten. Retain the transaction directory for recovery."))
}

#[cfg(windows)]
mod native {
    use super::*;
    use fs2::FileExt;
    use std::{os::windows::ffi::OsStrExt, ptr};
    use windows_sys::Win32::{
        Foundation::{CloseHandle, ERROR_INVALID_PARAMETER, FILETIME, WAIT_OBJECT_0},
        Storage::FileSystem::{MoveFileExW, ReplaceFileW},
        System::Threading::{
            CreateProcessW, GetProcessTimes, OpenProcess, QueryFullProcessImageNameW,
            WaitForSingleObject, CREATE_NO_WINDOW, PROCESS_INFORMATION,
            PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SYNCHRONIZE, STARTUPINFOW,
        },
    };
    fn wide(path: &Path) -> Vec<u16> {
        path.as_os_str().encode_wide().chain(Some(0)).collect()
    }
    fn spawn_helper(helper: &Path, plan: &Path, restart: bool) -> io::Result<()> {
        let executable = wide(helper);
        let mut command: Vec<u16> = "\"".encode_utf16().collect();
        command.extend(helper.as_os_str().encode_wide());
        command.extend("\" --portable-finish-update \"".encode_utf16());
        command.extend(plan.as_os_str().encode_wide());
        command.push('"' as u16);
        if restart {
            command.extend(" --restart".encode_utf16());
        }
        command.push(0);
        let mut startup = STARTUPINFOW::default();
        startup.cb = std::mem::size_of::<STARTUPINFOW>() as u32;
        let mut child = PROCESS_INFORMATION::default();
        // No inherited handles at all: redirected scheduler pipes must reach EOF
        // immediately even while this helper waits for the original application.
        let created = unsafe {
            CreateProcessW(
                executable.as_ptr(),
                command.as_mut_ptr(),
                ptr::null(),
                ptr::null(),
                0,
                CREATE_NO_WINDOW,
                ptr::null(),
                ptr::null(),
                &startup,
                &mut child,
            )
        };
        if created == 0 {
            return Err(io::Error::last_os_error());
        }
        unsafe {
            CloseHandle(child.hThread);
            CloseHandle(child.hProcess);
        }
        Ok(())
    }

    fn process(
        pid: u32,
    ) -> io::Result<Option<(windows_sys::Win32::Foundation::HANDLE, u64, PathBuf)>> {
        if pid == 0 {
            return Err(invalid("invalid launcher process"));
        }
        unsafe {
            let handle = OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE,
                0,
                pid,
            );
            if handle.is_null() {
                let e = io::Error::last_os_error();
                return if e.raw_os_error() == Some(ERROR_INVALID_PARAMETER as i32) {
                    Ok(None)
                } else {
                    Err(e)
                };
            }
            if WaitForSingleObject(handle, 0) == WAIT_OBJECT_0 {
                CloseHandle(handle);
                return Ok(None);
            }
            let result = (|| {
                let mut created = FILETIME::default();
                let mut exited = FILETIME::default();
                let mut kernel = FILETIME::default();
                let mut user = FILETIME::default();
                if GetProcessTimes(handle, &mut created, &mut exited, &mut kernel, &mut user) == 0 {
                    return Err(io::Error::last_os_error());
                }
                let mut buffer = vec![0u16; 32768];
                let mut size = buffer.len() as u32;
                if QueryFullProcessImageNameW(handle, 0, buffer.as_mut_ptr(), &mut size) == 0 {
                    return Err(io::Error::last_os_error());
                }
                use std::os::windows::ffi::OsStringExt;
                let path = PathBuf::from(std::ffi::OsString::from_wide(&buffer[..size as usize]));
                Ok((
                    (u64::from(created.dwHighDateTime) << 32) | u64::from(created.dwLowDateTime),
                    path,
                ))
            })();
            match result {
                Ok((time, path)) => Ok(Some((handle, time, path))),
                Err(e) => {
                    // The launcher can exit between the initial liveness check
                    // and its image query. Windows retains the process object
                    // while another caller owns a handle, but rejects that query.
                    let exited = WaitForSingleObject(handle, 0) == WAIT_OBJECT_0;
                    CloseHandle(handle);
                    if exited {
                        Ok(None)
                    } else {
                        Err(e)
                    }
                }
            }
        }
    }
    #[test]
    fn live_launcher_keeps_its_identity_check() {
        let (handle, created, image) = process(std::process::id()).unwrap().unwrap();
        unsafe {
            CloseHandle(handle);
        }
        assert_ne!(created, 0);
        assert_eq!(
            fs::canonicalize(image).unwrap(),
            fs::canonicalize(std::env::current_exe().unwrap()).unwrap()
        );
    }

    #[test]
    fn exited_launcher_with_retained_handle_is_not_live() {
        let mut child = std::process::Command::new("cmd.exe")
            .args(["/d", "/c", "exit", "0"])
            .spawn()
            .unwrap();
        child.wait().unwrap();
        let result = process(child.id());
        assert!(matches!(result, Ok(None)), "exited process: {result:?}");
    }

    fn live_relaunch(identity: &Relaunch, helper: &Path) -> io::Result<bool> {
        let Some((handle, created, image)) = process(identity.helper_pid)? else {
            return Ok(false);
        };
        unsafe {
            CloseHandle(handle);
        }
        Ok(created == identity.helper_created
            && fs::canonicalize(image)? == fs::canonicalize(helper)?)
    }

    fn supervised_relaunch(directory: &Path, ready_path: Option<&Path>) -> io::Result<bool> {
        if ready_path != Some(directory.join("runtime-ready").as_path()) {
            return Ok(false);
        }
        let file = match crate::pack::regular(&directory.join("relaunch.json")) {
            Ok(file) => file,
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(false),
            Err(e) => return Err(e),
        };
        let mut bytes = Vec::new();
        file.take(1025).read_to_end(&mut bytes)?;
        if bytes.len() > 1024 {
            return Err(invalid("relaunch identity too large"));
        }
        let identity: Relaunch = serde_json::from_slice(&bytes)?;
        live_relaunch(&identity, &directory.join("updater.exe"))
    }

    fn approve(directory: &Path, plan: &Plan) -> io::Result<()> {
        record_receipt(directory, "approved", &plan.new_sha256)
    }

    /// OURS: only a normally closed session authorizes unattended recovery.
    /// A crashed session's merely staged download must never be applied silently.
    pub fn recover_startup(executable: &Path, current_version: &str) -> io::Result<Vec<String>> {
        let target = fs::canonicalize(executable)?;
        let parent = target
            .parent()
            .ok_or_else(|| invalid("missing executable parent"))?;
        let cache = crate::cache::Cache::current_user()?;
        let name = format!(
            "update-{:x}",
            Sha256::digest(target.to_string_lossy().to_lowercase().as_bytes())
        );
        let lock = cache.lock(&name)?;
        lock.try_lock_exclusive()
            .map_err(|_| invalid("update recovery is already running"))?;
        let mut messages = Vec::new();
        let mut count = 0;
        let mut adopted = false;
        for entry in fs::read_dir(parent)? {
            let entry = entry?;
            if !entry
                .file_name()
                .to_str()
                .is_some_and(|s| s.starts_with(".narwhal-update-"))
            {
                continue;
            }
            count += 1;
            if count > 128 {
                return Err(invalid(
                    "too many update transactions; manual review required",
                ));
            }
            let directory = entry.path();
            crate::windows::verify_private_directory(&directory)?;
            let _hold = crate::windows::hold_directory(&directory)?;
            let path = directory.join("plan.json");
            let mut bytes = Vec::new();
            match crate::pack::regular(&path) {
                Ok(file) => {
                    file.take(16385).read_to_end(&mut bytes)?;
                }
                Err(e) if e.kind() == io::ErrorKind::NotFound => continue, // staging was never published
                Err(e) => return Err(e),
            }
            if bytes.len() > 16384 {
                return Err(invalid("recovery plan too large"));
            }
            let mut plan: Plan = serde_json::from_slice(&bytes)?;
            if plan.target != target {
                continue;
            }
            if !newer(&plan.old_version, &plan.new_version)
                || !crate::hex(&plan.old_sha256, 64)
                || !crate::hex(&plan.new_sha256, 64)
                || digest(&directory.join("updater.exe"))? != plan.old_sha256
            {
                return Err(invalid("recovery helper identity mismatch"));
            }
            // A previous completed transaction must not block future releases.
            if receipt(&directory.join("acknowledged"), &plan.new_sha256)? {
                continue;
            }
            if newer(&plan.new_version, current_version) {
                continue; // a later installed release supersedes this retained transaction
            }
            if let Some((handle, created, image)) = process(plan.launcher_pid)? {
                unsafe {
                    CloseHandle(handle);
                }
                if created == plan.launcher_created && fs::canonicalize(image)? == target {
                    continue;
                }
            }
            if !receipt(&directory.join("approved"), &plan.new_sha256)? {
                messages.push(format!("An update to {} was interrupted before normal shutdown approval. It has NOT been installed by recovery. Check for Updates to stage it again. Preserved files: {}", plan.new_version, directory.display()));
                continue;
            }
            match recovery(&plan, &directory)? {
                Recovery::Published => {
                    let ready = std::env::var_os("NARWHAL3D_UPDATE_READY_PATH").map(PathBuf::from);
                    let supervised = supervised_relaunch(&directory, ready.as_deref())?;
                    if let Some(message) = published_message(&plan, &directory, supervised)? {
                        messages.push(message);
                    }
                }
                Recovery::Prepared => {
                    if adopted {
                        messages.push(format!("Another interrupted update was preserved, not scheduled: {}. Only one update can apply per session.", directory.display()));
                        continue;
                    }
                    let (handle, created, _) = process(std::process::id())?
                        .ok_or_else(|| invalid("missing recovery launcher"))?;
                    unsafe {
                        CloseHandle(handle);
                    }
                    plan.launcher_pid = std::process::id();
                    plan.launcher_created = created;
                    let staged = directory.join(format!("resume-{}.tmp", std::process::id()));
                    let mut file = OpenOptions::new()
                        .write(true)
                        .create_new(true)
                        .open(&staged)?;
                    file.write_all(&serde_json::to_vec(&plan)?)?;
                    file.sync_all()?;
                    drop(file);
                    if unsafe {
                        MoveFileExW(
                            wide(&staged).as_ptr(),
                            wide(&path).as_ptr(),
                            windows_sys::Win32::Storage::FileSystem::MOVEFILE_REPLACE_EXISTING
                                | windows_sys::Win32::Storage::FileSystem::MOVEFILE_WRITE_THROUGH,
                        )
                    } == 0
                    {
                        return Err(io::Error::last_os_error());
                    }
                    let marker = cache
                        .root()
                        .join(format!("pending-{}-{created}.json", plan.launcher_pid));
                    let mut file = OpenOptions::new()
                        .write(true)
                        .create_new(true)
                        .open(marker)?;
                    file.write_all(&serde_json::to_vec(&path)?)?;
                    file.sync_all()?;
                    adopted = true;
                    messages.push(format!("The previously approved update to {} was interrupted. It will retry after this session closes normally. Your current version is unchanged.", plan.new_version));
                }
                Recovery::RestorePrevious => {
                    return Err(invalid(
                        "missing launcher; run the retained updater for recovery",
                    ))
                }
            }
        }
        Ok(messages)
    }

    #[test]
    fn native_relaunch_identity_requires_live_pid_creation_and_image() {
        let (handle, created, image) = process(std::process::id()).unwrap().unwrap();
        unsafe {
            CloseHandle(handle);
        }
        let mut identity = Relaunch {
            helper_pid: std::process::id(),
            helper_created: created,
        };
        assert!(live_relaunch(&identity, &image).unwrap());
        identity.helper_created ^= 1;
        assert!(!live_relaunch(&identity, &image).unwrap());
        identity.helper_created = created;
        let temp = crate::payload_tests::Temp::new();
        let wrong = temp.0.join("not-the-helper.exe");
        fs::write(&wrong, b"wrong image").unwrap();
        assert!(!live_relaunch(&identity, &wrong).unwrap());
        identity.helper_pid = u32::MAX;
        assert!(!live_relaunch(&identity, &image).unwrap());
    }

    #[test]
    fn native_ready_environment_alone_cannot_suppress_interruption_feedback() {
        let temp = crate::payload_tests::Temp::new();
        let directory = fs::canonicalize(&temp.0).unwrap();
        let ready = directory.join("runtime-ready");
        assert!(!supervised_relaunch(&directory, Some(&ready)).unwrap());
        let (handle, created, _) = process(std::process::id()).unwrap().unwrap();
        unsafe {
            CloseHandle(handle);
        }
        let identity = Relaunch {
            helper_pid: std::process::id(),
            helper_created: created,
        };
        fs::write(
            directory.join("relaunch.json"),
            serde_json::to_vec(&identity).unwrap(),
        )
        .unwrap();
        assert!(!supervised_relaunch(&directory, None).unwrap());
        assert!(!supervised_relaunch(&directory, Some(&directory.join("wrong-ready"))).unwrap());
        // A copied binary is not the live helper image, even if its bytes match.
        copy_new(
            &std::env::current_exe().unwrap(),
            &directory.join("updater.exe"),
        )
        .unwrap();
        assert!(!supervised_relaunch(&directory, Some(&ready)).unwrap());
    }

    #[test]
    fn native_receipts_are_idempotent_but_never_accept_conflicting_content() {
        let temp = crate::payload_tests::Temp::new();
        let hash = "a".repeat(64);
        record_receipt(&temp.0, "acknowledged", &hash).unwrap();
        record_receipt(&temp.0, "acknowledged", &hash).unwrap();
        assert!(record_receipt(&temp.0, "acknowledged", &"b".repeat(64)).is_err());
        assert_eq!(
            fs::read(temp.0.join("acknowledged")).unwrap(),
            hash.as_bytes()
        );
    }

    pub fn schedule(
        executable: &Path,
        current: &Payload,
        downloaded: &Path,
        expected: &str,
        pid: u32,
    ) -> io::Result<String> {
        if !crate::hex(expected, 64) || digest(downloaded)? != expected {
            return Err(invalid("download hash mismatch"));
        }
        let new = Payload::open(downloaded, Limits::default())?;
        new.verify_contents(Limits::default())?;
        let old_version = current.manifest().identity.version.clone();
        let new_version = new.manifest().identity.version.clone();
        if !newer(&old_version, &new_version) {
            return Err(invalid("update must be a newer 0.4.x release"));
        }
        drop(new);
        let target = fs::canonicalize(executable)?;
        let (handle, created, image) =
            process(pid)?.ok_or_else(|| invalid("launcher already exited"))?;
        unsafe {
            CloseHandle(handle);
        }
        if fs::canonicalize(image)? != target {
            return Err(invalid("process does not own this portable executable"));
        }
        let parent = target
            .parent()
            .ok_or_else(|| invalid("executable has no parent"))?;
        let needed = fs::metadata(executable)?
            .len()
            .checked_mul(2)
            .and_then(|n| n.checked_add(fs::metadata(downloaded).ok()?.len()))
            .ok_or_else(|| invalid("update size overflow"))?;
        if fs2::available_space(parent)? < needed + 256 * 1024 * 1024 {
            return Err(invalid("not enough disk space for update and rollback"));
        }
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(|_| invalid("invalid clock"))?
            .as_nanos();
        let directory = parent.join(format!(".narwhal-update-{}-{stamp}", std::process::id()));
        crate::windows::private_directory(&directory)?;
        let _hold = crate::windows::hold_directory(&directory)?;
        let helper = directory.join("updater.exe");
        let replacement = directory.join("new.exe");
        copy_new(executable, &helper)?;
        copy_new(downloaded, &replacement)?;
        let old_sha256 = digest(executable)?;
        if digest(&helper)? != old_sha256 || digest(&replacement)? != expected {
            return Err(invalid("update staging hash mismatch"));
        }
        let plan = Plan {
            target,
            old_sha256,
            new_sha256: expected.into(),
            old_version,
            new_version: new_version.clone(),
            launcher_pid: pid,
            launcher_created: created,
        };
        let path = directory.join("plan.json");
        let bytes = serde_json::to_vec(&plan)?;
        if bytes.len() > 16384 {
            return Err(invalid("update plan too large"));
        }
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)?;
        file.write_all(&bytes)?;
        file.sync_all()?;
        drop(file);
        let cache = crate::cache::Cache::current_user()?;
        let marker = cache.root().join(format!("pending-{pid}-{created}.json"));
        let staging_marker = cache
            .root()
            .join(format!("pending-{pid}-{created}-{stamp}.tmp"));
        let mut pending = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&staging_marker)?;
        let result = (|| {
            pending.write_all(&serde_json::to_vec(&path)?)?;
            pending.sync_all()
        })();
        drop(pending);
        let result = result.and_then(|()| {
            if unsafe {
                MoveFileExW(
                    wide(&staging_marker).as_ptr(),
                    wide(&marker).as_ptr(),
                    windows_sys::Win32::Storage::FileSystem::MOVEFILE_WRITE_THROUGH,
                )
            } == 0
            {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        });
        if result.is_err() {
            let _ = fs::remove_file(staging_marker);
        }
        result?;
        Ok(format!("Version {new_version} is verified and staged. It will apply after this session closes normally."))
    }
    pub fn apply_after_session(executable: &Path, restart: bool) -> io::Result<()> {
        let pid = std::process::id();
        let (handle, created, _) =
            process(pid)?.ok_or_else(|| invalid("missing launcher identity"))?;
        unsafe {
            CloseHandle(handle);
        }
        let cache = crate::cache::Cache::current_user()?;
        let marker = cache.root().join(format!("pending-{pid}-{created}.json"));
        let mut file = match crate::pack::regular(&marker) {
            Ok(file) => file,
            Err(error) if error.kind() == io::ErrorKind::NotFound && !restart => return Ok(()),
            Err(error) => return Err(error),
        };
        let mut bytes = Vec::new();
        (&mut file).take(16385).read_to_end(&mut bytes)?;
        if bytes.len() > 16384 {
            return Err(invalid("pending update marker is too large"));
        }
        let path: PathBuf = serde_json::from_slice(&bytes)?;
        let directory = path
            .parent()
            .ok_or_else(|| invalid("missing update directory"))?;
        let target = fs::canonicalize(executable)?;
        if directory.parent() != target.parent()
            || !directory
                .file_name()
                .and_then(|s| s.to_str())
                .is_some_and(|s| s.starts_with(".narwhal-update-"))
        {
            return Err(invalid("pending update directory identity mismatch"));
        }
        crate::windows::verify_private_directory(directory)?;
        let _hold = crate::windows::hold_directory(directory)?;
        if path != directory.join("plan.json") {
            return Err(invalid("unexpected pending plan path"));
        }
        let mut plan_bytes = Vec::new();
        crate::pack::regular(&path)?
            .take(16385)
            .read_to_end(&mut plan_bytes)?;
        if plan_bytes.len() > 16384 {
            return Err(invalid("pending update plan is too large"));
        }
        let plan: Plan = serde_json::from_slice(&plan_bytes)?;
        if plan.target != target || plan.launcher_pid != pid || plan.launcher_created != created {
            return Err(invalid(
                "pending update belongs to a different launcher session",
            ));
        }
        let helper = directory.join("updater.exe");
        if digest(&helper)? != plan.old_sha256 || digest(&target)? != plan.old_sha256 {
            return Err(invalid("pending helper hash mismatch"));
        }
        approve(directory, &plan)?;
        spawn_helper(&helper, &path, restart)
    }
    pub fn finish(
        executable: &Path,
        current: &Payload,
        path: &Path,
        restart: bool,
    ) -> io::Result<String> {
        let helper = fs::canonicalize(executable)?;
        let directory = helper
            .parent()
            .ok_or_else(|| invalid("helper has no directory"))?;
        if helper.file_name() != Some(std::ffi::OsStr::new("updater.exe"))
            || fs::canonicalize(path)? != directory.join("plan.json")
        {
            return Err(invalid("update plan is not beside its verified helper"));
        }
        crate::windows::verify_private_directory(directory)?;
        let _hold = crate::windows::hold_directory(directory)?;
        crate::payload::no_link(&fs::symlink_metadata(path)?)?;
        if fs::metadata(path)?.len() > 16384 {
            return Err(invalid("update plan too large"));
        }
        let plan: Plan = serde_json::from_slice(&fs::read(path)?)?;
        if plan.target.parent() != directory.parent()
            || plan.target == helper
            || current.manifest().identity.version != plan.old_version
            || !newer(&plan.old_version, &plan.new_version)
            || digest(&helper)? != plan.old_sha256
        {
            return Err(invalid("update plan identity mismatch"));
        }
        let cache = crate::cache::Cache::current_user()?;
        let name = format!(
            "update-{:x}",
            Sha256::digest(plan.target.to_string_lossy().to_lowercase().as_bytes())
        );
        let lock = cache.lock(&name)?;
        lock.try_lock_exclusive()
            .map_err(|_| invalid("another update is already pending"))?;
        if let Some((handle, created, image)) = process(plan.launcher_pid)? {
            let matches = created == plan.launcher_created
                && fs::canonicalize(image).ok().as_ref() == Some(&plan.target);
            let wait = if matches {
                unsafe { WaitForSingleObject(handle, 30000) }
            } else {
                u32::MAX
            };
            unsafe {
                CloseHandle(handle);
            }
            if !matches || wait != WAIT_OBJECT_0 {
                return Err(invalid(
                    "launcher identity changed or shutdown did not complete",
                ));
            }
        }
        let replacement = directory.join("new.exe");
        let backup = directory.join("previous.exe");
        match recovery(&plan, directory)? {
            Recovery::Published => {
                return Ok(format!("Update to {} was already applied. Previous executable: {}. Start Narwhal normally.", plan.new_version, backup.display()));
            }
            Recovery::RestorePrevious => {
                if unsafe {
                    MoveFileExW(
                        wide(&backup).as_ptr(),
                        wide(&plan.target).as_ptr(),
                        windows_sys::Win32::Storage::FileSystem::MOVEFILE_WRITE_THROUGH,
                    )
                } == 0
                {
                    return Err(io::Error::last_os_error());
                }
                return Err(invalid("Interrupted update: the verified previous executable was restored. Start Narwhal normally and download the update again."));
            }
            Recovery::Prepared => {}
        }
        if digest(&plan.target)? != plan.old_sha256 || digest(&replacement)? != plan.new_sha256 {
            return Err(invalid("executable changed after update approval"));
        }
        let new = Payload::open(&replacement, Limits::default())?;
        new.verify_contents(Limits::default())?;
        if new.manifest().identity.version != plan.new_version {
            return Err(invalid("staged version changed"));
        }
        drop(new);
        if fs::symlink_metadata(&backup).is_ok() {
            return Err(invalid("update backup already exists"));
        }
        let result = unsafe {
            ReplaceFileW(
                wide(&plan.target).as_ptr(),
                wide(&replacement).as_ptr(),
                wide(&backup).as_ptr(),
                0,
                ptr::null_mut(),
                ptr::null_mut(),
            )
        };
        if result == 0 {
            let failure = io::Error::last_os_error();
            if digest(&plan.target).ok().as_deref() == Some(&plan.new_sha256) {
                return Err(invalid("Update appears installed, but Windows reported a replacement failure. Runtime startup is not confirmed; retained transaction requires review."));
            }
            if fs::symlink_metadata(&plan.target).is_err()
                && digest(&backup).ok().as_deref() == Some(&plan.old_sha256)
            {
                if unsafe { MoveFileExW(wide(&backup).as_ptr(), wide(&plan.target).as_ptr(), 0) }
                    == 0
                {
                    return Err(invalid("update failed and automatic rollback failed; preserved backup requires manual recovery"));
                }
            }
            return Err(failure);
        }
        if digest(&plan.target)? != plan.new_sha256 {
            return Err(invalid(
                "installed update verification failed; backup retained",
            ));
        }
        FileExt::unlock(&lock)?;
        if restart {
            let ready = directory.join("runtime-ready");
            if fs::symlink_metadata(&ready).is_ok() {
                return Err(invalid("update readiness receipt already exists"));
            }
            let (handle, created, _) = process(std::process::id())?
                .ok_or_else(|| invalid("missing updater process identity"))?;
            unsafe {
                CloseHandle(handle);
            }
            let identity = Relaunch {
                helper_pid: std::process::id(),
                helper_created: created,
            };
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(directory.join("relaunch.json"))?;
            file.write_all(&serde_json::to_vec(&identity)?)?;
            file.sync_all()?;
            drop(file);
            let mut child = std::process::Command::new(&plan.target)
                .env("NARWHAL3D_UPDATE_READY_PATH", &ready)
                .spawn()
                .map_err(|error| io::Error::other(format!(
                    "Update applied, but could not restart Narwhal: {error}. Previous executable: {}", backup.display()
                )))?;
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(180);
            loop {
                if fs::read(&ready).is_ok_and(|bytes| bytes == plan.new_version.as_bytes()) {
                    break;
                }
                if child.try_wait()?.is_some() || std::time::Instant::now() >= deadline {
                    return Err(io::Error::other(format!(
                        "Update applied, but the new runtime did not confirm startup. Previous executable: {}", backup.display()
                    )));
                }
                std::thread::sleep(std::time::Duration::from_millis(200));
            }
        }
        record_receipt(directory, "acknowledged", &plan.new_sha256)?;
        Ok(format!(
            "Updated to {}. Previous executable retained beside the update helper.",
            plan.new_version
        ))
    }
}
#[cfg(windows)]
pub use native::{apply_after_session, finish, recover_startup, schedule};

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn versions_only_advance_on_the_approved_line() {
        assert!(newer("0.4.8", "0.4.9"));
        for bad in ["0.4.8", "0.4.7", "0.5.0", "0.4.09", "1.0.0", "oops"] {
            assert!(!newer("0.4.8", bad));
        }
    }
    #[cfg(windows)]
    fn staged_update(temp: &crate::payload_tests::Temp) -> (PathBuf, PathBuf, PathBuf) {
        let target = temp.0.join("Narwhal3D.exe");
        crate::payload_tests::fixture(&target, "");
        let directory = temp.0.join(".narwhal-update-test");
        crate::windows::private_directory(&directory).unwrap();
        let helper = directory.join("updater.exe");
        copy_new(&target, &helper).unwrap();
        let replacement = directory.join("new.exe");
        crate::payload_tests::fixture(&replacement, "version_049");
        let plan = Plan {
            target: fs::canonicalize(&target).unwrap(),
            old_sha256: digest(&target).unwrap(),
            new_sha256: digest(&replacement).unwrap(),
            old_version: "0.4.8".into(),
            new_version: "0.4.9".into(),
            launcher_pid: u32::MAX,
            launcher_created: 0,
        };
        let path = directory.join("plan.json");
        fs::write(&path, serde_json::to_vec(&plan).unwrap()).unwrap();
        (target, helper, path)
    }
    #[cfg(windows)]
    #[test]
    fn native_update_replaces_target_and_retains_exact_backup() {
        let temp = crate::payload_tests::Temp::new();
        let (target, helper, plan) = staged_update(&temp);
        let old = digest(&target).unwrap();
        let new = digest(&helper.with_file_name("new.exe")).unwrap();
        let current = Payload::open(&helper, Limits::default()).unwrap();
        super::finish(&helper, &current, &plan, false).unwrap();
        assert_eq!(digest(&target).unwrap(), new);
        assert_eq!(digest(&helper.with_file_name("previous.exe")).unwrap(), old);
        assert!(!helper.with_file_name("new.exe").exists());
    }
    #[cfg(windows)]
    #[test]
    fn native_update_refuses_changed_target_without_overwriting_it() {
        let temp = crate::payload_tests::Temp::new();
        let (target, helper, plan) = staged_update(&temp);
        fs::write(&target, b"replacement by someone else").unwrap();
        let current = Payload::open(&helper, Limits::default()).unwrap();
        assert!(super::finish(&helper, &current, &plan, false).is_err());
        assert_eq!(fs::read(&target).unwrap(), b"replacement by someone else");
        assert!(helper.with_file_name("new.exe").exists());
        assert!(!helper.with_file_name("previous.exe").exists());
    }
    #[cfg(windows)]
    #[test]
    fn native_update_locked_target_preserves_old_and_new_files() {
        use std::os::windows::fs::OpenOptionsExt;
        let temp = crate::payload_tests::Temp::new();
        let (target, helper, plan) = staged_update(&temp);
        let old = digest(&target).unwrap();
        let new = digest(&helper.with_file_name("new.exe")).unwrap();
        let hold = OpenOptions::new()
            .read(true)
            .share_mode(1)
            .open(&target)
            .unwrap();
        let current = Payload::open(&helper, Limits::default()).unwrap();
        assert!(super::finish(&helper, &current, &plan, false).is_err());
        assert_eq!(digest(&target).unwrap(), old);
        assert_eq!(digest(&helper.with_file_name("new.exe")).unwrap(), new);
        drop(hold);
    }
    fn recovery_fixture() -> (crate::payload_tests::Temp, Plan) {
        let temp = crate::payload_tests::Temp::new();
        let target = temp.0.join("Narwhal3D.exe");
        fs::write(&target, b"old executable").unwrap();
        fs::write(temp.0.join("new.exe"), b"new executable").unwrap();
        let plan = Plan {
            old_sha256: digest(&target).unwrap(),
            new_sha256: digest(&temp.0.join("new.exe")).unwrap(),
            target,
            old_version: "0.4.8".into(),
            new_version: "0.4.9".into(),
            launcher_pid: 1,
            launcher_created: 1,
        };
        (temp, plan)
    }
    #[test]
    fn healthy_supervised_publication_never_notifies_or_acknowledges_before_runtime_ready() {
        let (temp, plan) = recovery_fixture();
        assert!(published_message(&plan, &temp.0, true).unwrap().is_none());
        assert!(
            !temp.0.join("acknowledged").exists(),
            "only the helper may confirm readiness"
        );
        assert!(
            !temp.0.join("recovery-notified").exists(),
            "normal restart is not an interruption"
        );
        // A helper that disappears before readiness must not leave a suppression receipt.
        assert!(published_message(&plan, &temp.0, false).unwrap().is_some());
    }
    #[test]
    fn interrupted_publication_notifies_once_but_never_claims_runtime_completion() {
        let (temp, plan) = recovery_fixture();
        let message = published_message(&plan, &temp.0, false).unwrap().unwrap();
        assert!(message.contains("relaunch was interrupted"));
        assert!(message.contains("runtime readiness was not confirmed"));
        assert!(message.contains(&temp.0.join("previous.exe").display().to_string()));
        assert!(receipt(&temp.0.join("recovery-notified"), &plan.new_sha256).unwrap());
        assert!(!temp.0.join("acknowledged").exists());
        assert!(published_message(&plan, &temp.0, false).unwrap().is_none());
    }
    #[test]
    fn interrupted_publication_refuses_conflicting_notification_receipt() {
        let (temp, plan) = recovery_fixture();
        let path = temp.0.join("recovery-notified");
        fs::write(&path, b"wrong hash").unwrap();
        assert!(published_message(&plan, &temp.0, false).is_err());
        assert_eq!(fs::read(path).unwrap(), b"wrong hash");
        assert!(!temp.0.join("acknowledged").exists());
    }
    #[test]
    fn recovery_prepared_requires_exact_old_and_new_without_backup() {
        let (temp, plan) = recovery_fixture();
        assert_eq!(recovery(&plan, &temp.0).unwrap(), Recovery::Prepared);
        fs::write(temp.0.join("new.exe"), b"corrupt").unwrap();
        assert!(recovery(&plan, &temp.0).is_err());
    }
    #[test]
    fn recovery_published_requires_exact_backup_even_after_replacement_consumed() {
        let (temp, plan) = recovery_fixture();
        fs::rename(&plan.target, temp.0.join("previous.exe")).unwrap();
        fs::rename(temp.0.join("new.exe"), &plan.target).unwrap();
        assert_eq!(recovery(&plan, &temp.0).unwrap(), Recovery::Published);
        fs::write(temp.0.join("previous.exe"), b"wrong previous").unwrap();
        assert!(recovery(&plan, &temp.0).is_err());
    }
    #[test]
    fn recovery_missing_target_only_restores_verified_previous() {
        let (temp, plan) = recovery_fixture();
        fs::rename(&plan.target, temp.0.join("previous.exe")).unwrap();
        assert_eq!(recovery(&plan, &temp.0).unwrap(), Recovery::RestorePrevious);
        fs::write(temp.0.join("previous.exe"), b"wrong previous").unwrap();
        assert!(recovery(&plan, &temp.0).is_err());
    }
    #[test]
    fn recovery_never_overwrites_an_unknown_target_or_existing_backup() {
        let (temp, plan) = recovery_fixture();
        fs::write(temp.0.join("previous.exe"), b"old executable").unwrap();
        assert!(recovery(&plan, &temp.0).is_err());
        fs::write(&plan.target, b"third party file").unwrap();
        assert!(recovery(&plan, &temp.0).is_err());
        assert_eq!(fs::read(&plan.target).unwrap(), b"third party file");
    }
    #[test]
    fn recovery_receipt_requires_exact_bounded_hash() {
        let temp = crate::payload_tests::Temp::new();
        let path = temp.0.join("approved");
        let hash = "a".repeat(64);
        assert!(!receipt(&path, &hash).unwrap());
        fs::write(&path, &hash).unwrap();
        assert!(receipt(&path, &hash).unwrap());
        fs::write(&path, "b".repeat(64)).unwrap();
        assert!(!receipt(&path, &hash).unwrap());
        fs::write(&path, format!("{hash}extra")).unwrap();
        assert!(!receipt(&path, &hash).unwrap());
    }
    #[cfg(unix)]
    #[test]
    fn recovery_refuses_linked_target_and_receipt() {
        let (temp, plan) = recovery_fixture();
        fs::rename(&plan.target, temp.0.join("original.exe")).unwrap();
        std::os::unix::fs::symlink(temp.0.join("original.exe"), &plan.target).unwrap();
        assert!(recovery(&plan, &temp.0).is_err());
        let path = temp.0.join("approved");
        std::os::unix::fs::symlink(temp.0.join("original.exe"), &path).unwrap();
        assert!(receipt(&path, "anything").is_err());
    }
    #[cfg(windows)]
    #[test]
    fn native_update_can_resume_after_publication_without_replacing_twice() {
        let temp = crate::payload_tests::Temp::new();
        let (target, helper, path) = staged_update(&temp);
        let current = Payload::open(&helper, Limits::default()).unwrap();
        super::finish(&helper, &current, &path, false).unwrap();
        let installed = digest(&target).unwrap();
        let backup = digest(&helper.with_file_name("previous.exe")).unwrap();
        super::finish(&helper, &current, &path, false).unwrap();
        assert_eq!(digest(&target).unwrap(), installed);
        assert_eq!(
            digest(&helper.with_file_name("previous.exe")).unwrap(),
            backup
        );
    }
    #[cfg(windows)]
    #[test]
    fn native_update_restores_verified_backup_when_target_disappeared() {
        let temp = crate::payload_tests::Temp::new();
        let (target, helper, path) = staged_update(&temp);
        let old = digest(&target).unwrap();
        fs::rename(&target, helper.with_file_name("previous.exe")).unwrap();
        let current = Payload::open(&helper, Limits::default()).unwrap();
        let error = super::finish(&helper, &current, &path, false).unwrap_err();
        assert!(error.to_string().contains("was restored"));
        assert_eq!(digest(&target).unwrap(), old);
        assert!(helper.with_file_name("new.exe").exists());
    }
    #[cfg(windows)]
    #[test]
    fn native_startup_only_reattaches_durably_approved_update() {
        let temp = crate::payload_tests::Temp::new();
        let (target, helper, path) = staged_update(&temp);
        let original = fs::read(&path).unwrap();
        let messages = super::recover_startup(&target, "0.4.8").unwrap();
        assert!(messages[0].contains("NOT been installed"));
        assert_eq!(fs::read(&path).unwrap(), original);
        let plan: Plan = serde_json::from_slice(&original).unwrap();
        fs::write(helper.with_file_name("approved"), &plan.new_sha256).unwrap();
        let messages = super::recover_startup(&target, "0.4.8").unwrap();
        assert!(messages[0].contains("will retry after this session closes normally"));
        let rebound: Plan = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        assert_eq!(rebound.launcher_pid, std::process::id());
        assert_ne!(rebound.launcher_created, 0);
        let cache = crate::cache::Cache::current_user().unwrap();
        let marker = cache.root().join(format!(
            "pending-{}-{}.json",
            rebound.launcher_pid, rebound.launcher_created
        ));
        let recorded: PathBuf = serde_json::from_slice(&fs::read(&marker).unwrap()).unwrap();
        assert_eq!(
            fs::canonicalize(&recorded).unwrap(),
            fs::canonicalize(&path).unwrap(),
            "the pending marker must identify this exact plan despite 8.3/long-path aliases"
        );
        assert_eq!(rebound.target, plan.target);
        assert_eq!(rebound.old_sha256, plan.old_sha256);
        assert_eq!(rebound.new_sha256, plan.new_sha256);
        fs::remove_file(marker).unwrap();
        assert_eq!(digest(&target).unwrap(), plan.old_sha256);
        assert_eq!(
            digest(&helper.with_file_name("new.exe")).unwrap(),
            plan.new_sha256
        );
    }
    #[test]
    fn copied_update_is_new_and_byte_identical() {
        let temp = crate::payload_tests::Temp::new();
        let a = temp.0.join("old.exe");
        let b = temp.0.join("copy.exe");
        fs::write(&a, b"test executable bytes").unwrap();
        copy_new(&a, &b).unwrap();
        assert_eq!(digest(&a).unwrap(), digest(&b).unwrap());
        assert!(copy_new(&a, &b).is_err());
        assert_eq!(fs::read(&b).unwrap(), b"test executable bytes");
    }
}
