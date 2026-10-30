//! Bounded, hash-verified embedded ZIP payloads. No code is executed here.
//! Format: PE stub | manifest JSON | flat ZIP | 112-byte footer. Unsigned PE only;
//! Authenticode certificate-table support must be implemented before signing.
use crate::{admit, Admission, Limits, Manifest};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::Path;
use std::sync::{Arc, Mutex};
use zip::{CompressionMethod, ZipArchive};

pub const MAGIC: &[u8; 16] = b"NARWHAL-PAYLOAD1";
pub const FOOTER_SIZE: u64 = 112;
pub const MANIFEST_LIMIT: u64 = 16 * 1024 * 1024;
const CENTRAL_LIMIT: u64 = 32 * 1024 * 1024;

fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.into())
}
fn zipped(error: zip::result::ZipError) -> io::Error {
    invalid(error.to_string())
}

pub struct Payload {
    file: Arc<Mutex<File>>,
    start: u64,
    length: u64,
    manifest: Manifest,
    manifest_bytes: Vec<u8>,
}

/// Each read seeks under the same lock: cloned file handles may share an OS offset.
struct Segment {
    file: Arc<Mutex<File>>,
    start: u64,
    length: u64,
    position: u64,
}
impl Read for Segment {
    fn read(&mut self, bytes: &mut [u8]) -> io::Result<usize> {
        let count = (self.length - self.position).min(bytes.len() as u64) as usize;
        if count == 0 {
            return Ok(0);
        }
        let mut file = self
            .file
            .lock()
            .map_err(|_| invalid("payload reader lock poisoned"))?;
        file.seek(SeekFrom::Start(self.start + self.position))?;
        let read = file.read(&mut bytes[..count])?;
        self.position += read as u64;
        Ok(read)
    }
}
impl Seek for Segment {
    fn seek(&mut self, seek: SeekFrom) -> io::Result<u64> {
        let position = match seek {
            SeekFrom::Start(n) => i128::from(n),
            SeekFrom::Current(n) => i128::from(self.position) + i128::from(n),
            SeekFrom::End(n) => i128::from(self.length) + i128::from(n),
        };
        if position < 0 || position > i128::from(self.length) {
            return Err(invalid("ZIP seek outside embedded payload"));
        }
        self.position = position as u64;
        Ok(self.position)
    }
}

fn digest(mut input: impl Read, expected_size: u64) -> io::Result<[u8; 32]> {
    let mut hash = Sha256::new();
    let mut buffer = [0u8; 128 * 1024];
    let mut total = 0u64;
    loop {
        let n = input.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        total = total
            .checked_add(n as u64)
            .ok_or_else(|| invalid("size overflow"))?;
        if total > expected_size {
            return Err(invalid("content exceeds declared size"));
        }
        hash.update(&buffer[..n]);
    }
    if total != expected_size {
        return Err(invalid("content is truncated"));
    }
    Ok(hash.finalize().into())
}
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Check allocation-driving central-directory bounds before ZipArchive parses it.
/// The flat distribution format deliberately excludes ZIP64 and multi-disk ZIPs.
fn zip_admission(reader: &mut Segment, expected_count: usize, limits: Limits) -> io::Result<()> {
    let tail_size = reader.length.min(65_557);
    reader.seek(SeekFrom::End(-(tail_size as i64)))?;
    let mut tail = vec![0u8; tail_size as usize];
    reader.read_exact(&mut tail)?;
    let offset = tail
        .windows(4)
        .rposition(|w| w == b"PK\x05\x06")
        .ok_or_else(|| invalid("ZIP end record missing"))?;
    let e = &tail[offset..];
    if e.len() < 22 {
        return Err(invalid("truncated ZIP end record"));
    }
    let u16_at = |n| u16::from_le_bytes([e[n], e[n + 1]]);
    let u32_at = |n| u32::from_le_bytes(e[n..n + 4].try_into().unwrap());
    let count = usize::from(u16_at(10));
    let size = u64::from(u32_at(12));
    let start = u64::from(u32_at(16));
    let end_position = reader.length - tail_size + offset as u64;
    if u16_at(4) != 0
        || u16_at(6) != 0
        || u16_at(8) != u16_at(10)
        || count == 65_535
        || count != expected_count
        || count > limits.files
        || size > CENTRAL_LIMIT
        || start + size != end_position
        || e.len() != 22 + usize::from(u16_at(20))
    {
        return Err(invalid("unsupported or excessive ZIP central directory"));
    }
    reader.seek(SeekFrom::Start(0))?;
    Ok(())
}

impl Payload {
    pub fn open(path: &Path, limits: Limits) -> io::Result<Self> {
        let mut options = OpenOptions::new();
        options.read(true);
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            // Neither replacement nor writes are permitted while this payload is in use.
            options.share_mode(1);
        }
        let mut file = options.open(path)?;
        let size = file.metadata()?.len();
        if size < FOOTER_SIZE + 64 {
            return Err(invalid("portable executable is incomplete"));
        }
        let mut prefix = [0; 2];
        file.read_exact(&mut prefix)?;
        if prefix != *b"MZ" {
            return Err(invalid("portable payload must be attached to a Windows PE"));
        }
        file.seek(SeekFrom::End(-(FOOTER_SIZE as i64)))?;
        let mut footer = [0u8; FOOTER_SIZE as usize];
        file.read_exact(&mut footer)?;
        if &footer[..16] != MAGIC {
            return Err(invalid("portable payload footer missing"));
        }
        let word = |offset| u64::from_le_bytes(footer[offset..offset + 8].try_into().unwrap());
        let (manifest_start, manifest_length, start, length) =
            (word(16), word(24), word(32), word(40));
        if manifest_start < 64
            || manifest_length == 0
            || manifest_length > MANIFEST_LIMIT
            || length == 0
            || length > limits.archive_bytes
            || manifest_start.checked_add(manifest_length) != Some(start)
            || start.checked_add(length) != Some(size - FOOTER_SIZE)
        {
            return Err(invalid("invalid portable payload bounds"));
        }
        file.seek(SeekFrom::Start(manifest_start))?;
        let mut manifest_bytes = vec![0; manifest_length as usize];
        file.read_exact(&mut manifest_bytes)?;
        if Sha256::digest(&manifest_bytes)[..] != footer[48..80] {
            return Err(invalid("embedded manifest hash mismatch"));
        }
        let manifest: Manifest = serde_json::from_slice(&manifest_bytes)
            .map_err(|e| invalid(format!("payload manifest: {e}")))?;
        if manifest.archive_size != length
            || manifest.identity.archive_sha256 != hex(&footer[80..112])
        {
            return Err(invalid("manifest/archive identity mismatch"));
        }
        admit(&manifest, &manifest.identity, limits, u64::MAX)
            .map_err(|e| invalid(e.to_string()))?;
        let payload = Self {
            file: Arc::new(Mutex::new(file)),
            start,
            length,
            manifest,
            manifest_bytes,
        };
        if digest(payload.segment(), length)?[..] != footer[80..112] {
            return Err(invalid("embedded archive hash mismatch"));
        }
        let _ = payload.archive(limits)?;
        Ok(payload)
    }
    fn segment(&self) -> Segment {
        Segment {
            file: Arc::clone(&self.file),
            start: self.start,
            length: self.length,
            position: 0,
        }
    }
    fn archive(&self, limits: Limits) -> io::Result<ZipArchive<Segment>> {
        let mut reader = self.segment();
        zip_admission(&mut reader, self.manifest.files.len(), limits)?;
        let mut archive = ZipArchive::new(reader).map_err(zipped)?;
        if archive.len() != self.manifest.files.len() {
            return Err(invalid("ZIP inventory count differs"));
        }
        let expected: BTreeMap<_, _> = self
            .manifest
            .files
            .iter()
            .map(|r| (r.path.as_str(), r))
            .collect();
        let mut seen = BTreeSet::new();
        for i in 0..archive.len() {
            let member = archive.by_index(i).map_err(zipped)?;
            let name = member.name();
            let record = expected
                .get(name)
                .ok_or_else(|| invalid("unexpected ZIP member"))?;
            crate::validate_path(name).map_err(|e| invalid(e.to_string()))?;
            if !seen.insert(name.to_owned())
                || member.name_raw() != name.as_bytes()
                || member.is_dir()
                || member.is_symlink()
                || member.encrypted()
                || member
                    .unix_mode()
                    .is_some_and(|mode| mode & 0o170000 != 0 && mode & 0o170000 != 0o100000)
                || member.size() != record.size
                || !matches!(
                    member.compression(),
                    CompressionMethod::Stored | CompressionMethod::Deflated
                )
            {
                return Err(invalid("unsafe, duplicate or mismatched ZIP member"));
            }
        }
        Ok(archive)
    }
    /// Validate member bytes without extracting or executing the package.
    pub fn verify_contents(&self, limits: Limits) -> io::Result<()> {
        let mut archive = self.archive(limits)?;
        for record in &self.manifest.files {
            let input = archive.by_name(&record.path).map_err(zipped)?;
            if hex(&digest(input, record.size)?) != record.sha256 {
                return Err(invalid(format!(
                    "archive member hash mismatch: {}",
                    record.path
                )));
            }
        }
        Ok(())
    }
    pub fn manifest(&self) -> &Manifest {
        &self.manifest
    }
    pub fn manifest_bytes(&self) -> &[u8] {
        &self.manifest_bytes
    }
    pub fn admission(&self, limits: Limits, free_bytes: u64) -> io::Result<Admission> {
        admit(&self.manifest, &self.manifest.identity, limits, free_bytes)
            .map_err(|e| invalid(e.to_string()))
    }
    /// The caller must secure and exclusively own this empty staging directory.
    /// This routine refuses links and writes new files only; it never publishes or executes.
    pub fn extract(&self, staging: &Path, limits: Limits) -> io::Result<()> {
        regular_directory(staging)?;
        if fs::read_dir(staging)?.next().is_some() {
            return Err(invalid("staging directory is not empty"));
        }
        let mut archive = self.archive(limits)?;
        for record in &self.manifest.files {
            let path = staging.join(&record.path);
            let mut parent = staging.to_path_buf();
            let parts: Vec<_> = record.path.split('/').collect();
            for component in &parts[..parts.len() - 1] {
                parent.push(component);
                match fs::create_dir(&parent) {
                    Ok(()) => (),
                    Err(e) if e.kind() == io::ErrorKind::AlreadyExists => (),
                    Err(e) => return Err(e),
                }
                regular_directory(&parent)?;
            }
            let mut input = archive.by_name(&record.path).map_err(zipped)?;
            let mut output = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&path)?;
            let mut hash = Sha256::new();
            let mut total = 0u64;
            let mut buffer = [0; 128 * 1024];
            loop {
                let count = input.read(&mut buffer)?;
                if count == 0 {
                    break;
                }
                total = total
                    .checked_add(count as u64)
                    .ok_or_else(|| invalid("extraction size overflow"))?;
                if total > record.size {
                    return Err(invalid("ZIP expands beyond manifest bounds"));
                }
                hash.update(&buffer[..count]);
                output.write_all(&buffer[..count])?;
            }
            if total != record.size || hex(&hash.finalize()) != record.sha256 {
                return Err(invalid(format!(
                    "extracted file hash/size mismatch: {}",
                    record.path
                )));
            }
            output.sync_all()?;
        }
        self.verify_tree(staging)
    }
    /// Check every file and directory, including unexpected additions and reparses.
    pub fn verify_tree(&self, root: &Path) -> io::Result<()> {
        regular_directory(root)?;
        let mut expected = BTreeMap::new();
        let mut directories = BTreeSet::new();
        for record in &self.manifest.files {
            expected.insert(record.path.clone(), record);
            for (index, _) in record.path.match_indices('/') {
                directories.insert(record.path[..index].to_owned());
            }
        }
        let mut pending = vec![(root.to_path_buf(), String::new())];
        let mut count = 0;
        while let Some((directory, prefix)) = pending.pop() {
            for entry in fs::read_dir(directory)? {
                let entry = entry?;
                let name = entry
                    .file_name()
                    .into_string()
                    .map_err(|_| invalid("non-Unicode cache filename"))?;
                let relative = if prefix.is_empty() {
                    name
                } else {
                    format!("{prefix}/{name}")
                };
                let metadata = fs::symlink_metadata(entry.path())?;
                no_link(&metadata)?;
                if metadata.is_dir() {
                    if !directories.remove(&relative) {
                        return Err(invalid("unexpected cache directory"));
                    }
                    pending.push((entry.path(), relative));
                } else {
                    let record = expected
                        .remove(&relative)
                        .ok_or_else(|| invalid("unexpected cache file"))?;
                    if !metadata.is_file()
                        || metadata.len() != record.size
                        || hex(&digest(File::open(entry.path())?, record.size)?) != record.sha256
                    {
                        return Err(invalid(format!(
                            "cache file integrity mismatch: {relative}"
                        )));
                    }
                    count += 1;
                }
            }
        }
        if count != self.manifest.files.len() || !expected.is_empty() || !directories.is_empty() {
            return Err(invalid("cache inventory is incomplete"));
        }
        Ok(())
    }
}

pub(crate) fn no_link(metadata: &fs::Metadata) -> io::Result<()> {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return Err(invalid("cache reparse point refused"));
        }
    }
    if metadata.file_type().is_symlink() {
        return Err(invalid("cache symlink refused"));
    }
    Ok(())
}
pub(crate) fn regular_directory(path: &Path) -> io::Result<()> {
    let metadata = fs::symlink_metadata(path)?;
    no_link(&metadata)?;
    if !metadata.is_dir() {
        return Err(invalid("cache path is not a directory"));
    }
    Ok(())
}
