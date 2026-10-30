//! Windows outer-EXE updates. Never writes into a running payload cache.
//! A copied, current-version helper waits for the original launcher, verifies
//! both files again, and asks Windows to replace one file while retaining backup.
#![cfg(any(windows, test))]
#[cfg(windows)]
use crate::{payload::Payload, Limits};
#[cfg(windows)]
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

#[cfg(windows)]
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
fn copy_new(source: &Path, destination: &Path) -> io::Result<()> {
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
fn newer(old: &str, new: &str) -> bool {
    if !crate::version(old) || !crate::version(new) {
        return false;
    }
    new[4..]
        .parse::<u32>()
        .ok()
        .zip(old[4..].parse::<u32>().ok())
        .is_some_and(|(n, o)| n > o)
}
#[cfg(windows)]
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
    fn spawn_helper(helper: &Path, plan: &Path) -> io::Result<()> {
        let executable = wide(helper);
        let mut command: Vec<u16> = "\"".encode_utf16().collect();
        command.extend(helper.as_os_str().encode_wide());
        command.extend("\" --portable-finish-update \"".encode_utf16());
        command.extend(plan.as_os_str().encode_wide());
        command.extend(['\"' as u16, 0]);
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
        spawn_helper(&helper, &path)?;
        Ok(format!("Version {new_version} is verified. Close all Narwhal windows within five minutes to finish updating."))
    }
    pub fn finish(executable: &Path, current: &Payload, path: &Path) -> io::Result<String> {
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
                unsafe { WaitForSingleObject(handle, 300000) }
            } else {
                u32::MAX
            };
            unsafe {
                CloseHandle(handle);
            }
            if !matches || wait != WAIT_OBJECT_0 {
                return Err(invalid(
                    "launcher changed or did not close within five minutes",
                ));
            }
        }
        let replacement = directory.join("new.exe");
        let backup = directory.join("previous.exe");
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
                return Ok(format!(
                    "Updated to {}; backup housekeeping requires review.",
                    plan.new_version
                ));
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
        Ok(format!(
            "Updated to {}. Previous executable retained beside the update helper.",
            plan.new_version
        ))
    }
}
#[cfg(windows)]
pub use native::{finish, schedule};

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
        let directory = temp.0.join("transaction");
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
        super::finish(&helper, &current, &plan).unwrap();
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
        assert!(super::finish(&helper, &current, &plan).is_err());
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
        assert!(super::finish(&helper, &current, &plan).is_err());
        assert_eq!(digest(&target).unwrap(), old);
        assert_eq!(digest(&helper.with_file_name("new.exe")).unwrap(), new);
        drop(hold);
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
