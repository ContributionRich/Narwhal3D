//! Private cache publication and leases. Runtime settings/documents live elsewhere.
use crate::{payload::Payload, Limits};
use fs2::FileExt;
use std::fs::{self, File, OpenOptions};
use std::io;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

pub struct Cache {
    root: PathBuf,
    // Windows directory handles disallow rename while paths are validated/in use.
    _guards: Vec<File>,
}
pub struct Lease {
    pub directory: PathBuf,
    pub reused: bool,
    _lock: File,
}
fn error(text: &str) -> io::Error {
    io::Error::new(io::ErrorKind::PermissionDenied, text)
}

impl Cache {
    #[cfg(windows)]
    pub fn current_user() -> io::Result<Self> {
        let local = crate::windows::local_app_data()?;
        crate::payload::regular_directory(&local)?;
        let mut guards = vec![crate::windows::hold_directory(&local)?];
        let root = local.join("Narwhal3D-Portable");
        crate::windows::private_directory(&root)?;
        guards.push(crate::windows::hold_directory(&root)?);
        crate::windows::verify_private_directory(&root)?;
        Ok(Self {
            root: fs::canonicalize(root)?,
            _guards: guards,
        })
    }
    /// Test-only POSIX root. Production Windows must use the ACL-checked root above.
    #[cfg(all(unix, test))]
    fn fixture(root: PathBuf) -> io::Result<Self> {
        use std::os::unix::fs::PermissionsExt;
        fs::create_dir(&root)?;
        fs::set_permissions(&root, fs::Permissions::from_mode(0o700))?;
        Ok(Self {
            root,
            _guards: Vec::new(),
        })
    }
    pub(crate) fn lock(&self, key: &str) -> io::Result<File> {
        let path = self.root.join(format!("{key}.lock"));
        match fs::symlink_metadata(&path) {
            Ok(metadata) => {
                crate::payload::no_link(&metadata)?;
                if !metadata.is_file() {
                    return Err(error("cache lock is not a regular file"));
                }
            }
            Err(e) if e.kind() == io::ErrorKind::NotFound => (),
            Err(e) => return Err(e),
        }
        let mut options = OpenOptions::new();
        options.read(true).write(true).create(true).truncate(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.custom_flags(libc::O_NOFOLLOW).mode(0o600);
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            options
                .custom_flags(windows_sys::Win32::Storage::FileSystem::FILE_FLAG_OPEN_REPARSE_POINT)
                .share_mode(3);
        }
        let file = options.open(path)?;
        let metadata = file.metadata()?;
        crate::payload::no_link(&metadata)?;
        if !metadata.is_file() {
            return Err(error("cache lock handle is not a regular file"));
        }
        Ok(file)
    }
    pub fn prepare(&self, payload: &Payload, limits: Limits) -> io::Result<Lease> {
        self.prepare_after_miss(payload, limits, || {})
    }
    fn prepare_after_miss(
        &self,
        payload: &Payload,
        limits: Limits,
        mut after_miss: impl FnMut(),
    ) -> io::Result<Lease> {
        let admission = payload.admission(limits, u64::MAX)?;
        let key = &admission.cache_key;
        let lock = self.lock(key)?;
        let directory = self.root.join(key);
        loop {
            FileExt::lock_shared(&lock)?;
            if directory.exists() {
                // Never repair an in-use version. Every shared lease verifies it.
                payload.verify_tree(&directory)?;
                return Ok(Lease {
                    directory,
                    reused: true,
                    _lock: lock,
                });
            }
            FileExt::unlock(&lock)?;
            after_miss();
            match lock.try_lock_exclusive() {
                Ok(()) => break,
                Err(e) if e.raw_os_error() == fs2::lock_contended_error().raw_os_error() => {
                    // Another cold starter can publish then retain a shared lease
                    // for hours. Recheck under a shared lock, never queue an
                    // exclusive lock behind that now-running application.
                    std::thread::sleep(std::time::Duration::from_millis(10));
                }
                Err(e) => return Err(e),
            }
        }
        let reused = directory.exists();
        if !reused {
            payload.admission(limits, fs2::available_space(&self.root)?)?;
            let nonce = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_err(|_| error("system clock predates the Unix epoch"))?
                .as_nanos();
            let staging = self
                .root
                .join(format!("{key}.stage-{}-{nonce}", std::process::id()));
            fs::create_dir(&staging)?;
            let result = payload.extract(&staging, limits).and_then(|_| {
                // The per-payload exclusive lock protects this destination check/rename.
                if directory.exists() {
                    return Err(error("cache destination appeared while locked"));
                }
                #[cfg(windows)]
                crate::windows::runtime_permissions(&staging, false)?;
                fs::rename(&staging, &directory)
            });
            if let Err(e) = result {
                // Preserve failed staging for diagnosis. No recursive deletion of an
                // unverified tree, and no deletion of another launch's staging/cache.
                return Err(io::Error::new(
                    e.kind(),
                    format!("payload staging failed: {e}; incomplete staging was not published"),
                ));
            }
            #[cfg(unix)]
            File::open(&self.root)?.sync_all()?;
        }
        // Unlock/relock works on both Windows and POSIX. Revalidate after taking
        // the shared lease: an explicit cleanup could win the transition race.
        FileExt::unlock(&lock)?;
        FileExt::lock_shared(&lock)?;
        payload.verify_tree(&directory)?;
        Ok(Lease {
            directory,
            reused,
            _lock: lock,
        })
    }
    /// Explicit cleanup only. A running version's shared lease prevents deletion.
    /// Verify exact payload bytes first; unknown/stale versions are never guessed.
    pub fn remove_verified_unused(&self, payload: &Payload, limits: Limits) -> io::Result<bool> {
        let admission = payload.admission(limits, u64::MAX)?;
        let key = &admission.cache_key;
        let lock = self.lock(key)?;
        if lock.try_lock_exclusive().is_err() {
            return Ok(false);
        }
        let directory = self.root.join(key);
        if !directory.exists() {
            return Ok(true);
        }
        payload.verify_tree(&directory)?;
        #[cfg(windows)]
        crate::windows::runtime_permissions(&directory, true)?;
        fs::remove_dir_all(directory)?;
        Ok(true)
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use crate::payload_tests::{fixture, Temp};
    use std::sync::{mpsc, Arc};
    use std::time::Duration;

    #[test]
    fn concurrent_reuse_does_not_wait_for_running_application_and_cleanup_is_leased() {
        let temp = Temp::new();
        let exe = temp.0.join("fixture.exe");
        fixture(&exe, "");
        let payload = Arc::new(Payload::open(&exe, Limits::default()).unwrap());
        let cache = Arc::new(Cache::fixture(temp.0.join("cache")).unwrap());
        let first = cache.prepare(&payload, Limits::default()).unwrap();
        assert!(!first.reused);
        let modified = fs::metadata(first.directory.join("runtime/blender.exe"))
            .unwrap()
            .modified()
            .unwrap();
        let (tx, rx) = mpsc::channel();
        let c = Arc::clone(&cache);
        let p = Arc::clone(&payload);
        let thread = std::thread::spawn(move || {
            let second = c.prepare(&p, Limits::default()).unwrap();
            assert!(second.reused);
            tx.send(second).unwrap();
        });
        let second = rx
            .recv_timeout(Duration::from_secs(3))
            .expect("concurrent launch blocked by running application");
        thread.join().unwrap();
        assert_eq!(
            modified,
            fs::metadata(second.directory.join("runtime/blender.exe"))
                .unwrap()
                .modified()
                .unwrap()
        );
        assert!(!cache
            .remove_verified_unused(&payload, Limits::default())
            .unwrap());
        let directory = first.directory.clone();
        drop(first);
        drop(second);
        assert!(cache
            .remove_verified_unused(&payload, Limits::default())
            .unwrap());
        assert!(!directory.exists());
    }

    #[test]
    fn cold_start_loser_rechecks_after_winner_takes_lifetime_lease() {
        let temp = Temp::new();
        let exe = temp.0.join("fixture.exe");
        fixture(&exe, "");
        let payload = Arc::new(Payload::open(&exe, Limits::default()).unwrap());
        let cache = Arc::new(Cache::fixture(temp.0.join("cache")).unwrap());
        let (missed_tx, missed_rx) = mpsc::channel();
        let (resume_tx, resume_rx) = mpsc::channel();
        let (done_tx, done_rx) = mpsc::channel();
        let c = Arc::clone(&cache);
        let p = Arc::clone(&payload);
        let thread = std::thread::spawn(move || {
            let mut first = true;
            let lease = c.prepare_after_miss(&p, Limits::default(), || {
                if first {
                    first = false;
                    missed_tx.send(()).unwrap();
                    resume_rx.recv_timeout(Duration::from_secs(5)).unwrap();
                }
            });
            done_tx.send(lease).unwrap();
        });
        missed_rx.recv_timeout(Duration::from_secs(5)).unwrap();
        let winner = cache.prepare(&payload, Limits::default()).unwrap();
        resume_tx.send(()).unwrap();
        let finished = done_rx.recv_timeout(Duration::from_secs(2));
        // Always release the winner before joining, including the regression case.
        drop(winner);
        thread.join().unwrap();
        assert!(
            finished.is_ok(),
            "cold starter waited for the running application to exit"
        );
        assert!(finished.unwrap().unwrap().reused);
    }

    #[test]
    fn dangling_lock_link_never_creates_or_locks_its_target() {
        use std::os::unix::fs::symlink;
        let temp = Temp::new();
        let exe = temp.0.join("fixture.exe");
        fixture(&exe, "");
        let payload = Payload::open(&exe, Limits::default()).unwrap();
        let cache = Cache::fixture(temp.0.join("cache")).unwrap();
        let key = payload
            .admission(Limits::default(), u64::MAX)
            .unwrap()
            .cache_key;
        let victim = temp.0.join("must-not-be-created");
        symlink(&victim, cache.root.join(format!("{key}.lock"))).unwrap();
        assert!(cache.prepare(&payload, Limits::default()).is_err());
        assert!(cache
            .remove_verified_unused(&payload, Limits::default())
            .is_err());
        assert!(!victim.exists());
    }

    #[test]
    fn failed_extraction_is_not_published_and_a_later_launch_recovers() {
        let temp = Temp::new();
        let exe = temp.0.join("fixture.exe");
        fixture(&exe, "file_hash");
        let payload = Payload::open(&exe, Limits::default()).unwrap();
        let cache = Cache::fixture(temp.0.join("cache")).unwrap();
        let key = payload
            .admission(Limits::default(), u64::MAX)
            .unwrap()
            .cache_key;
        assert!(cache.prepare(&payload, Limits::default()).is_err());
        assert!(!cache.root.join(&key).exists());
        drop(payload);
        fixture(&exe, "");
        let payload = Payload::open(&exe, Limits::default()).unwrap();
        let lease = cache.prepare(&payload, Limits::default()).unwrap();
        assert!(!lease.reused);
        payload.verify_tree(&lease.directory).unwrap();
    }
}
