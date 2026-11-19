//! Per-user managed executable, separate from version-addressed runtimes.
use crate::{cache::Cache, payload::Payload, shortcut, Limits};
use std::os::windows::ffi::OsStrExt;
use std::{
    fs, io,
    path::{Path, PathBuf},
};
use windows_sys::Win32::Storage::FileSystem::{MoveFileExW, MOVEFILE_WRITE_THROUGH};

pub fn managed(cache: &Cache, source: &Path, current_version: &str) -> io::Result<PathBuf> {
    let directory = cache.root().join("Install");
    crate::windows::private_directory(&directory)?;
    let _hold = crate::windows::hold_directory(&directory)?;
    crate::windows::verify_private_directory(&directory)?;
    let target = directory.join("Narwhal3D.exe");
    let lock = cache.lock("managed-install")?;
    fs2::FileExt::lock_exclusive(&lock)?;
    match fs::symlink_metadata(&target) {
        Ok(metadata) => {
            crate::payload::no_link(&metadata)?;
            if !metadata.is_file() {
                return Err(io::Error::other("managed launcher is not a regular file"));
            }
            if fs::canonicalize(source)? != fs::canonicalize(&target)? {
                let installed = Payload::open(&target, Limits::default())?;
                if crate::update::newer(&installed.manifest().identity.version, current_version) {
                    // A manually downloaded newer copy must never start an older version.
                    // The running managed installation owns updates to the stable target.
                    return Ok(source.to_path_buf());
                }
            }
        }
        Err(e) if e.kind() == io::ErrorKind::NotFound => {
            let stamp = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_err(io::Error::other)?
                .as_nanos();
            let staged = directory.join(format!("install-{}-{stamp}.stage", std::process::id()));
            crate::update::copy_new(source, &staged)?;
            let result = (|| {
                Payload::open(&staged, Limits::default())?.verify_contents(Limits::default())?;
                let wide = |p: &Path| {
                    p.as_os_str()
                        .encode_wide()
                        .chain(Some(0))
                        .collect::<Vec<_>>()
                };
                if unsafe {
                    MoveFileExW(
                        wide(&staged).as_ptr(),
                        wide(&target).as_ptr(),
                        MOVEFILE_WRITE_THROUGH,
                    )
                } == 0
                {
                    return Err(io::Error::last_os_error());
                }
                Ok(())
            })();
            if result.is_err() {
                let _ = fs::remove_file(&staged);
            }
            result?;
        }
        Err(e) => return Err(e),
    }
    Ok(target)
}

pub fn migrate(cache: &Cache, launcher: &Path) -> io::Result<()> {
    if launcher != cache.root().join("Install").join("Narwhal3D.exe") {
        return Ok(());
    }
    let lock = cache.lock("shortcut-migration")?;
    fs2::FileExt::lock_exclusive(&lock)?;
    let mut first_error = None;
    for (kind, name) in [
        (shortcut::LinkTarget::Programs, "programs"),
        (shortcut::LinkTarget::Desktop, "desktop"),
    ] {
        let receipt = cache.root().join(format!("shortcut-{name}-v1.done"));
        if fs::symlink_metadata(&receipt).is_ok() {
            continue;
        }
        match shortcut::publish(kind, launcher, "Narwhal 3D") {
            Ok(_) => {
                fs::OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(receipt)?
                    .sync_all()?;
            }
            Err(e) => {
                first_error.get_or_insert_with(|| io::Error::other(e.to_string()));
            }
        }
    }
    first_error.map_or(Ok(()), Err)
}
