//! Durable update intent. Recovery never publishes an image or rolls one back:
//! a resumed stage needs fresh host exit approval; a published image needs readiness.
use super::*;
use serde::{Deserialize, Serialize};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
struct Identity {
    dev: u64,
    ino: u64,
    uid: u32,
    mode: u32,
    len: u64,
    mtime: i64,
    nsec: i64,
}
impl Identity {
    fn of(m: &fs::Metadata) -> Self {
        Self {
            dev: m.dev(),
            ino: m.ino(),
            uid: m.uid(),
            mode: m.mode(),
            len: m.len(),
            mtime: m.mtime(),
            nsec: m.mtime_nsec(),
        }
    }
}
#[derive(Serialize, Deserialize)]
pub(super) struct Journal {
    schema: u32,
    target: PathBuf,
    staged: PathBuf,
    backup: PathBuf,
    original: Identity,
    staged_identity: Identity,
    old_hash: [u8; 32],
    new_hash: [u8; 32],
    version: String,
}
fn path(target: &Path) -> PathBuf {
    let mut name = target.as_os_str().to_os_string();
    name.push(".narwhal-update.json");
    PathBuf::from(name)
}
fn regular(path: &Path) -> Result<fs::Metadata, String> {
    let m = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    if !m.is_file() || m.file_type().is_symlink() {
        return Err("update path is not a regular non-symlink file".into());
    }
    Ok(m)
}
fn hash(path: &Path) -> Result<[u8; 32], String> {
    let before = regular(path)?;
    let mut file = fs::File::open(path).map_err(|e| e.to_string())?;
    if Identity::of(&before) != Identity::of(&file.metadata().map_err(|e| e.to_string())?) {
        return Err("update file identity changed".into());
    }
    let mut h = Sha256::new();
    let size = std::io::copy(&mut (&mut file).take(DOWNLOAD_LIMIT + 1), &mut h)
        .map_err(|e| e.to_string())?;
    if size > DOWNLOAD_LIMIT || Identity::of(&before) != Identity::of(&regular(path)?) {
        return Err("update file changed while hashing".into());
    }
    Ok(h.finalize().into())
}
impl Journal {
    fn validate(&self, target: &Path) -> Result<(), String> {
        if parse_version(&self.version).is_none()
            || self.schema != 1
            || self.target != target
            || !safe_absolute_path(target)
            || fs::canonicalize(target.parent().ok_or("missing image parent")?)
                .map_err(|e| e.to_string())?
                != target.parent().unwrap()
            || self.staged.parent() != target.parent()
            || self.backup.parent() != target.parent()
            || self.staged == self.backup
            || self.staged == target
            || self.backup == target
            || !self
                .staged
                .file_name()
                .is_some_and(|s| s.to_string_lossy().starts_with(".narwhal3d-update-"))
            || !self
                .backup
                .file_name()
                .is_some_and(|s| s.to_string_lossy().starts_with("Narwhal3D-previous-"))
        {
            return Err("update journal target/path binding is invalid".into());
        }
        Ok(())
    }
    pub(super) fn create(image: &LinuxImage) -> Result<(), String> {
        let j = Self {
            schema: 1,
            target: image.target.clone(),
            staged: image.staged.clone(),
            backup: image.backup.clone(),
            original: Identity::of(&image.original),
            staged_identity: Identity::of(&regular(&image.staged)?),
            old_hash: hash(&image.target)?,
            new_hash: image.expected,
            version: image.version.clone(),
        };
        j.validate(&image.target)?;
        if j.staged_identity.uid != j.original.uid
            || regular(&image.staged)?.nlink() != 1
            || Identity::of(&regular(&image.target)?) != j.original
            || hash(&image.staged)? != j.new_hash
        {
            return Err("image changed before journaling".into());
        }
        let bytes = serde_json::to_vec(&j).map_err(|e| e.to_string())?;
        // create_new is also the transaction reservation: another session cannot replace it.
        let mut f = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(path(&image.target))
            .map_err(|e| format!("cannot reserve durable update journal: {e}"))?;
        f.write_all(&bytes)
            .and_then(|_| f.sync_all())
            .and_then(|_| fs::File::open(image.target.parent().unwrap())?.sync_all())
            .map_err(|e| e.to_string())
    }
    fn load(target: &Path) -> Result<Option<Self>, String> {
        Self::load_from(target, &path(target))
    }
    fn load_from(target: &Path, p: &Path) -> Result<Option<Self>, String> {
        match fs::symlink_metadata(p) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(e) => return Err(e.to_string()),
            Ok(_) => {}
        }
        let m = regular(p)?;
        let owner = regular(target)?.uid();
        if m.uid() != owner || m.mode() & 0o077 != 0 || m.nlink() != 1 || m.len() > 16384 {
            return Err("unsafe update journal ownership/permissions/size".into());
        }
        let mut f = fs::File::open(p).map_err(|e| e.to_string())?;
        if Identity::of(&m) != Identity::of(&f.metadata().map_err(|e| e.to_string())?) {
            return Err("journal identity changed".into());
        }
        let mut bytes = Vec::new();
        (&mut f)
            .take(16385)
            .read_to_end(&mut bytes)
            .map_err(|e| e.to_string())?;
        let j: Self = serde_json::from_slice(&bytes)
            .map_err(|e| format!("interrupted/invalid update journal: {e}"))?;
        j.validate(target)?;
        Ok(Some(j))
    }
    fn is_old(&self) -> Result<bool, String> {
        Ok(Identity::of(&regular(&self.target)?) == self.original
            && hash(&self.target)? == self.old_hash)
    }
    fn published(&self) -> Result<bool, String> {
        let current = regular(&self.target)?;
        Ok(current.dev() == self.staged_identity.dev
            && current.ino() == self.staged_identity.ino
            && current.uid() == self.original.uid
            && current.mode() & 0o7777 == self.original.mode & 0o777
            && hash(&self.target)? == self.new_hash
            && Identity::of(&regular(&self.backup)?) == self.original
            && hash(&self.backup)? == self.old_hash)
    }
}
pub(super) fn lock_for_apply(image: &LinuxImage) -> Result<fs::File, String> {
    use std::os::fd::AsRawFd;
    unsafe extern "C" {
        fn flock(fd: i32, operation: i32) -> i32;
    }
    let j = Journal::load(&image.target)?.ok_or("durable update journal is missing")?;
    let file = fs::File::open(path(&image.target)).map_err(|e| e.to_string())?;
    // LOCK_EX | LOCK_NB. File lifetime holds the lock through atomic publication.
    if unsafe { flock(file.as_raw_fd(), 2 | 4) } != 0 {
        return Err("another session is applying this update".into());
    }
    if hash(&image.staged)? != j.new_hash
        || regular(&image.staged)?.ino() != j.staged_identity.ino
        || regular(&image.staged)?.dev() != j.staged_identity.dev
        || regular(&image.staged)?.uid() != j.original.uid
        || regular(&image.staged)?.nlink() != 1
        || Identity::of(&file.metadata().map_err(|e| e.to_string())?)
            != Identity::of(&regular(&path(&image.target))?)
        || !j.is_old()?
        || j.staged != image.staged
        || j.backup != image.backup
        || j.new_hash != image.expected
        || j.version != image.version
    {
        return Err("update journal/image identity changed before approval".into());
    }
    Ok(file)
}
pub(super) fn recover(target: &Path) -> Result<(Option<LinuxImage>, Option<String>), String> {
    let Some(j) = Journal::load(target)? else {
        let receipt = path(target).with_extension("completed.json");
        if let Some(done) = Journal::load_from(target, &receipt)? {
            if done.published().unwrap_or(false) {
                return Ok((
                    None,
                    Some(format!(
                        "Update {} completed successfully. Previous image retained at {}.",
                        done.version,
                        done.backup.display()
                    )),
                ));
            }
        }
        return Ok((None, None));
    };
    if j.is_old()? {
        if hash(&j.staged)? != j.new_hash {
            return Err("staged update hash changed; no image was replaced".into());
        }
        // A crash after backup linking but before rename must remain retryable.
        if j.backup.try_exists().map_err(|e| e.to_string())?
            && (Identity::of(&regular(&j.backup)?) != j.original || hash(&j.backup)? != j.old_hash)
        {
            return Err("previous-image identity changed".into());
        }
        let original = regular(target)?;
        return Ok((Some(LinuxImage { target: j.target, staged: j.staged, backup: j.backup, original, expected: j.new_hash, version: j.version }), Some("Interrupted update recovered. The verified download needs fresh Restart approval; your current image is unchanged.".into())));
    }
    if j.published()? {
        return Ok((None, Some(format!("Update image recovered; waiting for runtime readiness. Previous image retained at {}. No automatic rollback will occur.", j.backup.display()))));
    }
    Err(
        "update target or previous image changed; recovery refused without replacing anything"
            .into(),
    )
}
pub(super) fn complete(target: &Path, version: Option<&str>) -> Result<Option<String>, String> {
    let Some(j) = Journal::load(target)? else {
        return Ok(None);
    };
    if j.is_old()? || version != Some(j.version.as_str()) {
        return Ok(None);
    }
    if !j.published()? {
        return Err("cannot confirm update: image hash/identity mismatch".into());
    }
    fs::rename(path(target), path(target).with_extension("completed.json"))
        .and_then(|_| fs::File::open(target.parent().unwrap())?.sync_all())
        .map_err(|e| e.to_string())?;
    Ok(Some(format!(
        "Update completed: the new runtime is ready. Previous image retained at {}.",
        j.backup.display()
    )))
}
pub(super) fn expected_version(target: &Path) -> Result<String, String> {
    Journal::load(target)?
        .map(|journal| journal.version)
        .ok_or_else(|| "update journal is missing before relaunch".into())
}

pub(super) fn package_target() -> Option<PathBuf> {
    std::env::var_os("APPIMAGE")
        .or_else(|| std::env::var_os("NARWHAL3D_PACKAGE_PATH"))
        .map(PathBuf::from)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (PathBuf, LinuxImage) {
        let root = std::env::temp_dir().join(format!(
            "narwhal-journal-{}-{}",
            std::process::id(),
            unique_stamp()
        ));
        fs::create_dir(&root).unwrap();
        let target = root.join("app.AppImage");
        let staged = root.join(".narwhal3d-update-test.AppImage");
        fs::write(&target, b"old").unwrap();
        fs::write(&staged, b"new").unwrap();
        let image = LinuxImage {
            original: fs::metadata(&target).unwrap(),
            target,
            staged,
            backup: root.join("Narwhal3D-previous-test.AppImage"),
            expected: Sha256::digest(b"new").into(),
            version: "0.4.99".into(),
        };
        (root, image)
    }
    #[test]
    fn durable_stage_requires_fresh_approval_and_survives_drop() {
        let (root, image) = fixture();
        assert!(expected_version(&image.target).is_err());
        Journal::create(&image).unwrap();
        assert_eq!(expected_version(&image.target).unwrap(), "0.4.99");
        drop(image);
        let (recovered, message) = recover(&root.join("app.AppImage")).unwrap();
        assert!(recovered.is_some());
        assert!(message.unwrap().contains("fresh Restart"));
        assert_eq!(fs::read(root.join("app.AppImage")).unwrap(), b"old");
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn interruption_after_backup_and_after_publication_is_recoverable() {
        let (root, image) = fixture();
        Journal::create(&image).unwrap();
        fs::hard_link(&image.target, &image.backup).unwrap();
        assert!(recover(&image.target).unwrap().0.is_some());
        image.apply().unwrap();
        assert!(recover(&image.target).unwrap().0.is_none());
        assert!(complete(&image.target, Some("0.4.98")).unwrap().is_none());
        assert!(path(&image.target).exists());
        assert!(complete(&image.target, Some("0.4.99"))
            .unwrap()
            .unwrap()
            .contains("Update completed"));
        assert_eq!(fs::read(&image.backup).unwrap(), b"old");
        assert!(recover(&image.target)
            .unwrap()
            .1
            .unwrap()
            .contains("completed successfully"));
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn altered_stage_target_backup_and_journal_are_refused() {
        let (root, image) = fixture();
        Journal::create(&image).unwrap();
        fs::write(&image.staged, b"bad").unwrap();
        assert!(recover(&image.target).is_err());
        fs::write(&image.staged, b"new").unwrap();
        image.apply().unwrap();
        fs::write(&image.backup, b"bad").unwrap();
        assert!(complete(&image.target, Some("0.4.99")).is_err());
        assert_eq!(fs::read(&image.target).unwrap(), b"new");
        fs::write(path(&image.target), b"{").unwrap();
        assert!(recover(&image.target).is_err());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn stale_target_and_stage_symlink_never_publish() {
        let (root, image) = fixture();
        Journal::create(&image).unwrap();
        fs::write(&image.target, b"changed").unwrap();
        assert!(image.apply().is_err());
        assert!(recover(&image.target).is_err());
        assert_eq!(fs::read(&image.target).unwrap(), b"changed");
        fs::remove_dir_all(root).unwrap();
        let (root, image) = fixture();
        Journal::create(&image).unwrap();
        fs::remove_file(&image.staged).unwrap();
        let elsewhere = root.join("elsewhere");
        fs::write(&elsewhere, b"new").unwrap();
        std::os::unix::fs::symlink(&elsewhere, &image.staged).unwrap();
        assert!(image.apply().is_err());
        assert!(recover(&image.target).is_err());
        assert_eq!(fs::read(&image.target).unwrap(), b"old");
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn concurrent_apply_is_refused_and_completion_never_restores_old_bytes() {
        let (root, image) = fixture();
        Journal::create(&image).unwrap();
        let lock = lock_for_apply(&image).unwrap();
        assert!(image.apply().is_err());
        drop(lock);
        image.apply().unwrap();
        assert!(image.apply().is_err());
        assert!(complete(&image.target, None).unwrap().is_none());
        assert_eq!(fs::read(&image.target).unwrap(), b"new");
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn identical_bytes_at_different_inode_do_not_confirm_completion() {
        let (root, image) = fixture();
        Journal::create(&image).unwrap();
        image.apply().unwrap();
        let replacement = root.join("replacement");
        fs::write(&replacement, b"new").unwrap();
        fs::rename(replacement, &image.target).unwrap();
        assert!(complete(&image.target, Some("0.4.99")).is_err());
        assert_eq!(fs::read(&image.target).unwrap(), b"new");
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn journal_reservation_and_symlinks_are_not_overwritten() {
        let (root, image) = fixture();
        Journal::create(&image).unwrap();
        assert!(Journal::create(&image).is_err());
        fs::remove_file(path(&image.target)).unwrap();
        std::os::unix::fs::symlink(&image.staged, path(&image.target)).unwrap();
        assert!(recover(&image.target).is_err());
        assert!(Journal::create(&image).is_err());
        fs::remove_dir_all(root).unwrap();
    }
}
