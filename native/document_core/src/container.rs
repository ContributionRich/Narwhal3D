// SPDX-License-Identifier: GPL-3.0-or-later

use crate::sha256::{digest_reader, hex, Sha256};

use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

pub const MAGIC: [u8; 8] = *b"NARWHAL\0";
pub const FORMAT_VERSION: u32 = 1;
pub const HEADER_SIZE: u64 = 32;
pub const ENTRY_FIXED_SIZE: u64 = 52;
pub const MAX_ENTRIES: u32 = 10_000;
pub const MAX_PATH_FIELD_SIZE: u32 = 1024;
pub const MAX_CANONICAL_PATH_SIZE: usize = 1024;
pub const MAX_MEMBER_SIZE: u64 = 512 * 1024 * 1024;
pub const MAX_CONTAINER_SIZE: u64 = 4 * 1024 * 1024 * 1024;

static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CoreError(String);

impl CoreError {
    fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }

    fn io(action: &str, path: &Path, error: io::Error) -> Self {
        Self(format!("{action} '{}': {error}", path.display()))
    }
}

impl fmt::Display for CoreError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for CoreError {}

type Result<T> = std::result::Result<T, CoreError>;

#[derive(Debug, Clone)]
struct Entry {
    path: String,
    offset: u64,
    size: u64,
    digest: [u8; 32],
}

#[derive(Debug)]
struct PackEntry {
    entry: Entry,
    source: PathBuf,
    file: File,
    identity: FileIdentity,
}

#[derive(Debug)]
struct ObservedDirectory {
    source: PathBuf,
    file: File,
    identity: FileIdentity,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum SourceKind {
    File,
    Directory,
}

#[cfg(unix)]
#[derive(Clone, Debug, PartialEq, Eq)]
struct FileIdentity {
    device: u64,
    inode: u64,
    length: u64,
    modified_seconds: i64,
    modified_nanoseconds: i64,
    changed_seconds: i64,
    changed_nanoseconds: i64,
    kind: SourceKind,
}

#[cfg(windows)]
#[derive(Clone, Debug, PartialEq, Eq)]
struct FileIdentity {
    volume: u32,
    index: u64,
    length: u64,
    modified: u64,
    attributes: u32,
    kind: SourceKind,
}

#[cfg(not(any(unix, windows)))]
#[derive(Clone, Debug, PartialEq, Eq)]
struct FileIdentity {
    length: u64,
    modified: Option<std::time::SystemTime>,
    kind: SourceKind,
}

struct ValidatedContainer {
    file: File,
    entries: Vec<Entry>,
}

pub fn pack(staging_dir: &Path, target_file: &Path) -> Result<()> {
    pack_with_hook(staging_dir, target_file, || {})
}

fn pack_with_hook<F>(staging_dir: &Path, target_file: &Path, after_collection: F) -> Result<()>
where
    F: FnOnce(),
{
    let root = open_observed_directory(staging_dir, staging_dir)?;
    let mut entries = Vec::new();
    let mut directories = Vec::new();
    let mut visited = 0u32;
    collect_staging(
        &root,
        Path::new(""),
        &mut entries,
        &mut directories,
        &mut visited,
    )?;
    directories.push(root);
    entries.sort_by(|left, right| left.entry.path.cmp(&right.entry.path));
    validate_member_set(entries.iter().map(|entry| entry.entry.path.as_str()))?;

    after_collection();
    verify_observed_sources(&entries, &directories)?;

    let entry_count = u32::try_from(entries.len())
        .map_err(|_| CoreError::new("entry count cannot be represented by the format"))?;
    if entry_count > MAX_ENTRIES {
        return Err(CoreError::new(format!(
            "entry count {entry_count} exceeds limit {MAX_ENTRIES}"
        )));
    }

    let mut table_size = 0u64;
    let mut payload_size = 0u64;
    for item in &entries {
        table_size = checked_add(
            table_size,
            checked_add(
                ENTRY_FIXED_SIZE,
                u64::try_from(item.entry.path.len())
                    .map_err(|_| CoreError::new("path length does not fit u64"))?,
                "entry table size",
            )?,
            "entry table size",
        )?;
        payload_size = checked_add(payload_size, item.entry.size, "total payload size")?;
    }
    if payload_size > MAX_CONTAINER_SIZE {
        return Err(CoreError::new("total payload exceeds 4 GiB"));
    }
    let payload_start = checked_add(HEADER_SIZE, table_size, "payload start")?;
    let container_size = checked_add(payload_start, payload_size, "container size")?;
    if container_size > MAX_CONTAINER_SIZE {
        return Err(CoreError::new("container exceeds 4 GiB"));
    }

    let mut next_offset = payload_start;
    for item in &mut entries {
        item.entry.offset = next_offset;
        next_offset = checked_add(next_offset, item.entry.size, "payload offset")?;
    }

    let parent = target_file.parent().unwrap_or_else(|| Path::new("."));
    require_real_directory(parent, "target parent directory")?;
    let target_name = target_file
        .file_name()
        .ok_or_else(|| CoreError::new("target file must have a file name"))?;
    let (temporary_path, mut temporary) = create_sibling_file(parent, target_name)?;
    let mut guard = FileGuard::new(temporary_path.clone());

    write_header(&mut temporary, entry_count, table_size, payload_size)?;
    for item in &entries {
        let path_length = u32::try_from(item.entry.path.len())
            .map_err(|_| CoreError::new("path length cannot be represented by the format"))?;
        temporary
            .write_all(&path_length.to_le_bytes())
            .map_err(|error| CoreError::io("write temporary container", &temporary_path, error))?;
        temporary
            .write_all(item.entry.path.as_bytes())
            .and_then(|()| temporary.write_all(&item.entry.offset.to_le_bytes()))
            .and_then(|()| temporary.write_all(&item.entry.size.to_le_bytes()))
            .and_then(|()| temporary.write_all(&item.entry.digest))
            .map_err(|error| CoreError::io("write temporary container", &temporary_path, error))?;
    }

    for item in &mut entries {
        verify_observed_file(item)?;
        item.file
            .seek(SeekFrom::Start(0))
            .map_err(|error| CoreError::io("rewind staged member", &item.source, error))?;
        let copied_digest = copy_exact_and_hash(&mut item.file, &mut temporary, item.entry.size)
            .map_err(|error| CoreError::io("copy staged member", &item.source, error))?;
        let mut extra = [0u8; 1];
        if item
            .file
            .read(&mut extra)
            .map_err(|error| CoreError::io("finish copying staged member", &item.source, error))?
            != 0
            || copied_digest != item.entry.digest
        {
            return Err(source_changed(&item.source));
        }
        verify_observed_file(item)?;
    }

    temporary
        .flush()
        .and_then(|()| temporary.sync_all())
        .map_err(|error| {
            CoreError::io("synchronize temporary container", &temporary_path, error)
        })?;
    verify_observed_sources(&entries, &directories)?;
    drop(temporary);
    atomic_replace(&temporary_path, target_file)
        .map_err(|error| CoreError::io("publish container", target_file, error))?;
    guard.disarm();
    sync_parent(parent)?;
    Ok(())
}

pub fn unpack(source_file: &Path, output_dir: &Path) -> Result<()> {
    unpack_with_publisher(source_file, output_dir, publish_directory_no_replace)
}

fn unpack_with_publisher<F>(source_file: &Path, output_dir: &Path, publisher: F) -> Result<()>
where
    F: FnOnce(&Path, &Path) -> io::Result<()>,
{
    require_absent_output_path(output_dir)?;
    let output_parent = output_dir.parent().unwrap_or_else(|| Path::new("."));
    require_real_directory(output_parent, "output parent directory")?;
    let output_name = output_dir
        .file_name()
        .ok_or_else(|| CoreError::new("output directory must have a final component"))?;
    let mut container = validate_container(source_file)?;
    let temporary_root = create_sibling_directory(output_parent, output_name)?;
    let mut guard = DirectoryGuard::new(temporary_root.clone());

    for entry in &container.entries {
        let destination = append_member_path(&temporary_root, &entry.path);
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent)
                .map_err(|error| CoreError::io("create unpack directory", parent, error))?;
        }
        let mut output = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&destination)
            .map_err(|error| CoreError::io("create unpacked member", &destination, error))?;
        container
            .file
            .seek(SeekFrom::Start(entry.offset))
            .map_err(|error| CoreError::io("seek container payload", source_file, error))?;
        let copied_digest = copy_exact_and_hash(
            (&mut container.file).take(entry.size),
            &mut output,
            entry.size,
        )
        .map_err(|error| CoreError::io("write unpacked member", &destination, error))?;
        if copied_digest != entry.digest {
            return Err(CoreError::new(format!(
                "source changed while unpacking member '{}'",
                entry.path
            )));
        }
        output
            .flush()
            .and_then(|()| output.sync_all())
            .map_err(|error| CoreError::io("synchronize unpacked member", &destination, error))?;
    }

    sync_directory(&temporary_root)?;
    require_absent_output_path(output_dir)?;
    publisher(&temporary_root, output_dir)
        .map_err(|error| CoreError::io("publish unpacked document", output_dir, error))?;
    guard.disarm();
    let _ = sync_directory(output_parent);
    Ok(())
}

pub fn inspect(source_file: &Path, output: &mut dyn Write) -> Result<()> {
    let container = validate_container(source_file)?;
    for entry in container.entries {
        writeln!(
            output,
            "{}\t{}\t{}",
            hex(&entry.digest),
            entry.size,
            entry.path
        )
        .map_err(|error| CoreError::new(format!("write inspection output: {error}")))?;
    }
    Ok(())
}

fn collect_staging(
    directory: &ObservedDirectory,
    relative_directory: &Path,
    entries: &mut Vec<PackEntry>,
    directories: &mut Vec<ObservedDirectory>,
    visited: &mut u32,
) -> Result<()> {
    let listing_path = directory_listing_path(directory);
    let children = fs::read_dir(&listing_path)
        .map_err(|error| CoreError::io("read staging directory", &directory.source, error))?;

    for child in children {
        let child = child
            .map_err(|error| CoreError::io("read staging directory", &directory.source, error))?;
        *visited = visited
            .checked_add(1)
            .ok_or_else(|| CoreError::new("staging entry count overflow"))?;
        if *visited > MAX_ENTRIES.saturating_mul(2) {
            return Err(CoreError::new(
                "staging tree contains too many filesystem entries",
            ));
        }

        let name = child.file_name();
        let relative = relative_directory.join(&name);
        let member_path = relative_path_to_member(&relative)?;
        validate_member_path(&member_path)?;
        let access_path = listing_path.join(&name);
        let source_path = directory.source.join(&name);
        let metadata = fs::symlink_metadata(&access_path)
            .map_err(|error| CoreError::io("inspect staging entry", &source_path, error))?;
        let file_type = metadata.file_type();
        if file_type.is_symlink() {
            return Err(CoreError::new(format!(
                "symlinks are not permitted in staging: '{}'",
                source_path.display()
            )));
        }
        if file_type.is_dir() {
            let child_directory = open_observed_directory(&access_path, &source_path)?;
            collect_staging(&child_directory, &relative, entries, directories, visited)?;
            directories.push(child_directory);
            continue;
        }
        if !file_type.is_file() {
            return Err(CoreError::new(format!(
                "special files are not permitted in staging: '{}'",
                source_path.display()
            )));
        }
        ensure_member_capacity(entries.len())?;

        let (mut file, identity) = open_observed(&access_path, &source_path, SourceKind::File)?;
        let size = identity.length();
        if size > MAX_MEMBER_SIZE {
            return Err(CoreError::new(format!(
                "member '{}' exceeds 512 MiB",
                member_path
            )));
        }
        let digest = hash_open_file(&mut file, &source_path, size, &identity)?;
        entries.push(PackEntry {
            entry: Entry {
                path: member_path,
                offset: 0,
                size,
                digest,
            },
            source: source_path,
            file,
            identity,
        });
    }
    verify_observed_directory(directory)
}

fn ensure_member_capacity(retained_entries: usize) -> Result<()> {
    let limit = usize::try_from(MAX_ENTRIES)
        .map_err(|_| CoreError::new("entry limit does not fit this platform"))?;
    if retained_entries >= limit {
        return Err(CoreError::new(format!(
            "entry count exceeds limit {MAX_ENTRIES}"
        )));
    }
    Ok(())
}

fn validate_container(source_file: &Path) -> Result<ValidatedContainer> {
    let mut file = File::open(source_file)
        .map_err(|error| CoreError::io("open container", source_file, error))?;
    let metadata = file
        .metadata()
        .map_err(|error| CoreError::io("inspect container", source_file, error))?;
    if !metadata.is_file() {
        return Err(CoreError::new("container source is not a regular file"));
    }
    let file_size = metadata.len();
    if file_size > MAX_CONTAINER_SIZE {
        return Err(CoreError::new("container exceeds 4 GiB"));
    }
    if file_size < HEADER_SIZE {
        return Err(CoreError::new("container is shorter than its header"));
    }

    let mut magic = [0u8; 8];
    read_exact(&mut file, &mut magic, "container magic")?;
    if magic != MAGIC {
        return Err(CoreError::new("invalid container magic"));
    }
    let version = read_u32(&mut file, "format version")?;
    if version != FORMAT_VERSION {
        return Err(CoreError::new(format!(
            "unsupported format version {version}"
        )));
    }
    let entry_count = read_u32(&mut file, "entry count")?;
    if entry_count > MAX_ENTRIES {
        return Err(CoreError::new(format!(
            "entry count {entry_count} exceeds limit {MAX_ENTRIES}"
        )));
    }
    let table_size = read_u64(&mut file, "table size")?;
    let payload_size = read_u64(&mut file, "payload size")?;
    if payload_size > MAX_CONTAINER_SIZE {
        return Err(CoreError::new("total payload exceeds 4 GiB"));
    }

    let minimum_table = u64::from(entry_count)
        .checked_mul(ENTRY_FIXED_SIZE)
        .ok_or_else(|| CoreError::new("minimum table size overflow"))?;
    let maximum_entry_size = checked_add(
        ENTRY_FIXED_SIZE,
        u64::from(MAX_PATH_FIELD_SIZE),
        "maximum table entry size",
    )?;
    let maximum_table = u64::from(entry_count)
        .checked_mul(maximum_entry_size)
        .ok_or_else(|| CoreError::new("maximum table size overflow"))?;
    if table_size < minimum_table || table_size > maximum_table {
        return Err(CoreError::new(
            "entry table size is inconsistent with entry count",
        ));
    }
    let payload_start = checked_add(HEADER_SIZE, table_size, "payload start")?;
    let expected_file_size = checked_add(payload_start, payload_size, "container size")?;
    if expected_file_size != file_size {
        return Err(CoreError::new(
            "container length does not match header (trailing or missing bytes)",
        ));
    }

    let capacity = usize::try_from(entry_count)
        .map_err(|_| CoreError::new("entry count does not fit this platform"))?;
    let mut entries = Vec::with_capacity(capacity);
    let mut consumed = 0u64;
    let mut total_member_size = 0u64;
    let mut previous_path: Option<String> = None;

    for _ in 0..entry_count {
        consumed = checked_add(consumed, 4, "entry table position")?;
        if consumed > table_size {
            return Err(CoreError::new("entry table ended before path length"));
        }
        let path_length = read_u32(&mut file, "path length")?;
        if path_length > MAX_PATH_FIELD_SIZE {
            return Err(CoreError::new("path field exceeds 1024 bytes"));
        }
        let path_length_usize = usize::try_from(path_length)
            .map_err(|_| CoreError::new("path length does not fit this platform"))?;
        if path_length_usize > MAX_CANONICAL_PATH_SIZE {
            return Err(CoreError::new("canonical path exceeds 1024 bytes"));
        }
        let remainder = checked_add(u64::from(path_length), 48, "entry table record size")?;
        consumed = checked_add(consumed, remainder, "entry table position")?;
        if consumed > table_size {
            return Err(CoreError::new("entry record extends beyond entry table"));
        }

        let mut path_bytes = vec![0u8; path_length_usize];
        read_exact(&mut file, &mut path_bytes, "member path")?;
        let path = String::from_utf8(path_bytes)
            .map_err(|_| CoreError::new("member path is malformed UTF-8"))?;
        validate_member_path(&path)?;
        if let Some(previous) = &previous_path {
            if path == *previous {
                return Err(CoreError::new(format!("duplicate member path '{path}'")));
            }
            if path < *previous {
                return Err(CoreError::new(
                    "member table is not sorted by canonical path",
                ));
            }
        }
        previous_path = Some(path.clone());

        let offset = read_u64(&mut file, "payload offset")?;
        let size = read_u64(&mut file, "member size")?;
        if size > MAX_MEMBER_SIZE {
            return Err(CoreError::new(format!("member '{path}' exceeds 512 MiB")));
        }
        total_member_size = checked_add(total_member_size, size, "total member size")?;
        if total_member_size > MAX_CONTAINER_SIZE {
            return Err(CoreError::new("total member size exceeds 4 GiB"));
        }
        let mut digest = [0u8; 32];
        read_exact(&mut file, &mut digest, "member checksum")?;
        let end = offset
            .checked_add(size)
            .ok_or_else(|| CoreError::new(format!("payload range overflows for '{path}'")))?;
        if offset < payload_start || end > file_size {
            return Err(CoreError::new(format!(
                "payload range is out of bounds for '{path}'"
            )));
        }
        entries.push(Entry {
            path,
            offset,
            size,
            digest,
        });
    }

    if consumed != table_size {
        return Err(CoreError::new("entry table contains unused bytes"));
    }
    if total_member_size != payload_size {
        return Err(CoreError::new(
            "declared payload size differs from member sizes",
        ));
    }

    let mut expected_offset = payload_start;
    for entry in &entries {
        if entry.offset < expected_offset {
            return Err(CoreError::new(format!(
                "payload ranges overlap or are out of order at '{}'",
                entry.path
            )));
        }
        if entry.offset > expected_offset {
            return Err(CoreError::new(format!(
                "payload ranges are not contiguous at '{}'",
                entry.path
            )));
        }
        expected_offset = checked_add(expected_offset, entry.size, "payload range")?;
    }
    if expected_offset != file_size {
        return Err(CoreError::new(
            "payload ranges do not cover the payload area",
        ));
    }
    validate_member_set(entries.iter().map(|entry| entry.path.as_str()))?;

    for entry in &entries {
        file.seek(SeekFrom::Start(entry.offset))
            .map_err(|error| CoreError::io("seek container payload", source_file, error))?;
        let actual = digest_reader(&mut file, entry.size)
            .map_err(|error| CoreError::io("read container payload", source_file, error))?;
        if actual != entry.digest {
            return Err(CoreError::new(format!(
                "checksum mismatch for member '{}'",
                entry.path
            )));
        }
    }
    let final_size = file
        .metadata()
        .map_err(|error| CoreError::io("reinspect container", source_file, error))?
        .len();
    if final_size != file_size {
        return Err(CoreError::new(
            "container changed while it was being validated",
        ));
    }

    Ok(ValidatedContainer { file, entries })
}

fn validate_member_set<'a>(paths: impl Iterator<Item = &'a str>) -> Result<()> {
    let mut manifest_count = 0u32;
    let mut document_count = 0u32;
    for path in paths {
        if path == "manifest.json" {
            manifest_count += 1;
        }
        if path == "document.3dm" {
            document_count += 1;
        }
        validate_master_path(path)?;
    }
    if manifest_count != 1 || document_count != 1 {
        return Err(CoreError::new(
            "container must contain exactly one root manifest.json and one root document.3dm",
        ));
    }
    Ok(())
}

fn validate_master_path(path: &str) -> Result<()> {
    let under_masters = path.starts_with("masters/");
    let brep_any_case = path
        .get(path.len().saturating_sub(5)..)
        .is_some_and(|suffix| suffix.eq_ignore_ascii_case(".brep"));
    if under_masters {
        let name = path
            .strip_prefix("masters/")
            .expect("checked masters prefix");
        if name.contains('/') || !is_lowercase_uuid_brep(name) {
            return Err(CoreError::new(format!(
                "exact masters must use masters/<lowercase-uuid>.brep: '{path}'"
            )));
        }
    } else if brep_any_case {
        return Err(CoreError::new(format!(
            "exact master is outside masters/<lowercase-uuid>.brep: '{path}'"
        )));
    }
    Ok(())
}

fn is_lowercase_uuid_brep(name: &str) -> bool {
    let Some(uuid) = name.strip_suffix(".brep") else {
        return false;
    };
    if uuid.len() != 36 {
        return false;
    }
    for (index, byte) in uuid.bytes().enumerate() {
        if matches!(index, 8 | 13 | 18 | 23) {
            if byte != b'-' {
                return false;
            }
        } else if !(byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)) {
            return false;
        }
    }
    true
}

fn validate_member_path(path: &str) -> Result<()> {
    let bytes = path.as_bytes();
    if bytes.is_empty() {
        return Err(CoreError::new("member path is empty"));
    }
    if bytes.len() > MAX_CANONICAL_PATH_SIZE {
        return Err(CoreError::new("canonical path exceeds 1024 bytes"));
    }
    if path.starts_with('/') {
        return Err(CoreError::new(format!(
            "absolute member path is forbidden: '{path}'"
        )));
    }
    if path.contains(':') {
        return Err(CoreError::new(format!(
            "colon is forbidden in member path: '{path}'"
        )));
    }
    if path.contains('\\') {
        return Err(CoreError::new(format!(
            "backslash is forbidden in member path: '{path}'"
        )));
    }
    if path.contains('\0') {
        return Err(CoreError::new("NUL is forbidden in member path"));
    }
    for component in path.split('/') {
        if component.is_empty() || component == "." || component == ".." {
            return Err(CoreError::new(format!(
                "noncanonical member path: '{path}'"
            )));
        }
    }
    Ok(())
}

fn relative_path_to_member(path: &Path) -> Result<String> {
    let mut components = Vec::new();
    for component in path.components() {
        match component {
            Component::Normal(value) => {
                let value = value.to_str().ok_or_else(|| {
                    CoreError::new(format!("staging path is not UTF-8: '{}'", path.display()))
                })?;
                components.push(value);
            }
            _ => {
                return Err(CoreError::new(format!(
                    "staging path is not canonical: '{}'",
                    path.display()
                )));
            }
        }
    }
    Ok(components.join("/"))
}

fn append_member_path(root: &Path, member: &str) -> PathBuf {
    let mut output = root.to_path_buf();
    for component in member.split('/') {
        output.push(component);
    }
    output
}

impl FileIdentity {
    fn length(&self) -> u64 {
        self.length
    }

    fn kind(&self) -> SourceKind {
        self.kind
    }
}

fn source_changed(path: &Path) -> CoreError {
    CoreError::new(format!(
        "staged source changed while packing: '{}'",
        path.display()
    ))
}

fn open_observed_directory(access_path: &Path, source_path: &Path) -> Result<ObservedDirectory> {
    let (file, identity) = open_observed(access_path, source_path, SourceKind::Directory)?;
    Ok(ObservedDirectory {
        source: source_path.to_path_buf(),
        file,
        identity,
    })
}

fn open_observed(
    access_path: &Path,
    source_path: &Path,
    expected_kind: SourceKind,
) -> Result<(File, FileIdentity)> {
    let before = fs::symlink_metadata(access_path)
        .map_err(|error| CoreError::io("inspect staged source", source_path, error))?;
    if before.file_type().is_symlink() {
        return Err(CoreError::new(format!(
            "symlinks are not permitted in staging: '{}'",
            source_path.display()
        )));
    }
    let before_kind = kind_from_file_type(before.file_type()).ok_or_else(|| {
        CoreError::new(format!(
            "special staged source: '{}'",
            source_path.display()
        ))
    })?;
    if before_kind != expected_kind {
        return Err(source_changed(source_path));
    }

    let file = open_no_follow(access_path, expected_kind).map_err(|error| {
        CoreError::io(
            "open staged source without following links",
            source_path,
            error,
        )
    })?;
    let identity = file_identity(&file)
        .map_err(|error| CoreError::io("identify opened staged source", source_path, error))?;
    if identity.kind() != expected_kind {
        return Err(source_changed(source_path));
    }
    #[cfg(unix)]
    if metadata_identity(&before)? != identity {
        return Err(source_changed(source_path));
    }
    #[cfg(not(any(unix, windows)))]
    if metadata_identity(&before)? != identity {
        return Err(source_changed(source_path));
    }
    Ok((file, identity))
}

fn hash_open_file(
    file: &mut File,
    path: &Path,
    size: u64,
    identity: &FileIdentity,
) -> Result<[u8; 32]> {
    if file_identity(file).map_err(|error| CoreError::io("reinspect staged member", path, error))?
        != *identity
    {
        return Err(source_changed(path));
    }
    file.seek(SeekFrom::Start(0))
        .map_err(|error| CoreError::io("rewind staged member", path, error))?;
    let digest = digest_reader(file, size)
        .map_err(|error| CoreError::io("hash staged member", path, error))?;
    let mut extra = [0u8; 1];
    if file
        .read(&mut extra)
        .map_err(|error| CoreError::io("finish hashing staged member", path, error))?
        != 0
        || file_identity(file)
            .map_err(|error| CoreError::io("reinspect staged member", path, error))?
            != *identity
    {
        return Err(source_changed(path));
    }
    Ok(digest)
}

fn verify_observed_sources(entries: &[PackEntry], directories: &[ObservedDirectory]) -> Result<()> {
    for directory in directories {
        verify_observed_directory(directory)?;
    }
    for entry in entries {
        verify_observed_file(entry)?;
    }
    Ok(())
}

fn verify_observed_directory(directory: &ObservedDirectory) -> Result<()> {
    verify_path_and_handle(
        &directory.source,
        &directory.file,
        &directory.identity,
        SourceKind::Directory,
    )
}

fn verify_observed_file(entry: &PackEntry) -> Result<()> {
    verify_path_and_handle(
        &entry.source,
        &entry.file,
        &entry.identity,
        SourceKind::File,
    )
}

fn verify_path_and_handle(
    path: &Path,
    retained: &File,
    identity: &FileIdentity,
    kind: SourceKind,
) -> Result<()> {
    let retained_identity = file_identity(retained)
        .map_err(|error| CoreError::io("reinspect staged source", path, error))?;
    if retained_identity != *identity {
        return Err(source_changed(path));
    }
    let current = open_no_follow(path, kind).map_err(|_| source_changed(path))?;
    let current_identity = file_identity(&current).map_err(|_| source_changed(path))?;
    if current_identity != *identity {
        return Err(source_changed(path));
    }
    Ok(())
}

fn kind_from_file_type(file_type: fs::FileType) -> Option<SourceKind> {
    if file_type.is_file() {
        Some(SourceKind::File)
    } else if file_type.is_dir() {
        Some(SourceKind::Directory)
    } else {
        None
    }
}

#[cfg(target_os = "linux")]
fn directory_listing_path(directory: &ObservedDirectory) -> PathBuf {
    use std::os::fd::AsRawFd;

    PathBuf::from(format!("/proc/self/fd/{}", directory.file.as_raw_fd()))
}

#[cfg(not(target_os = "linux"))]
fn directory_listing_path(directory: &ObservedDirectory) -> PathBuf {
    directory.source.clone()
}

#[cfg(target_os = "linux")]
fn open_no_follow(path: &Path, kind: SourceKind) -> io::Result<File> {
    use std::os::unix::fs::OpenOptionsExt;

    const O_DIRECTORY: i32 = 0o200000;
    const O_NOFOLLOW: i32 = 0o400000;

    let mut options = OpenOptions::new();
    options.read(true).custom_flags(O_NOFOLLOW);
    if kind == SourceKind::Directory {
        options.custom_flags(O_NOFOLLOW | O_DIRECTORY);
    }
    options.open(path)
}

#[cfg(windows)]
fn open_no_follow(path: &Path, kind: SourceKind) -> io::Result<File> {
    use std::os::windows::fs::OpenOptionsExt;

    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;

    let mut options = OpenOptions::new();
    if kind == SourceKind::File {
        options
            .read(true)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
    } else {
        options
            .access_mode(0)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS);
    }
    options.open(path)
}

#[cfg(not(any(target_os = "linux", windows)))]
fn open_no_follow(path: &Path, _kind: SourceKind) -> io::Result<File> {
    File::open(path)
}

#[cfg(unix)]
fn metadata_identity(metadata: &fs::Metadata) -> Result<FileIdentity> {
    use std::os::unix::fs::MetadataExt;

    let kind = kind_from_file_type(metadata.file_type())
        .ok_or_else(|| CoreError::new("staged source is not a regular file or directory"))?;
    Ok(FileIdentity {
        device: metadata.dev(),
        inode: metadata.ino(),
        length: metadata.len(),
        modified_seconds: metadata.mtime(),
        modified_nanoseconds: metadata.mtime_nsec(),
        changed_seconds: metadata.ctime(),
        changed_nanoseconds: metadata.ctime_nsec(),
        kind,
    })
}

#[cfg(unix)]
fn file_identity(file: &File) -> io::Result<FileIdentity> {
    let metadata = file.metadata()?;
    metadata_identity(&metadata).map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))
}

#[cfg(windows)]
fn file_identity(file: &File) -> io::Result<FileIdentity> {
    use std::ffi::c_void;
    use std::mem::MaybeUninit;
    use std::os::windows::io::AsRawHandle;

    const FILE_ATTRIBUTE_DIRECTORY: u32 = 0x10;
    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;

    #[repr(C)]
    struct FileTime {
        low: u32,
        high: u32,
    }

    #[repr(C)]
    struct ByHandleFileInformation {
        attributes: u32,
        creation_time: FileTime,
        last_access_time: FileTime,
        last_write_time: FileTime,
        volume_serial_number: u32,
        file_size_high: u32,
        file_size_low: u32,
        number_of_links: u32,
        file_index_high: u32,
        file_index_low: u32,
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn GetFileInformationByHandle(
            file: *mut c_void,
            information: *mut ByHandleFileInformation,
        ) -> i32;
    }

    let mut information = MaybeUninit::<ByHandleFileInformation>::uninit();
    let succeeded = unsafe {
        GetFileInformationByHandle(file.as_raw_handle().cast(), information.as_mut_ptr())
    };
    if succeeded == 0 {
        return Err(io::Error::last_os_error());
    }
    let information = unsafe { information.assume_init() };
    if information.attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "reparse points are not permitted in staging",
        ));
    }
    let kind = if information.attributes & FILE_ATTRIBUTE_DIRECTORY != 0 {
        SourceKind::Directory
    } else {
        SourceKind::File
    };
    Ok(FileIdentity {
        volume: information.volume_serial_number,
        index: (u64::from(information.file_index_high) << 32)
            | u64::from(information.file_index_low),
        length: (u64::from(information.file_size_high) << 32)
            | u64::from(information.file_size_low),
        modified: (u64::from(information.last_write_time.high) << 32)
            | u64::from(information.last_write_time.low),
        attributes: information.attributes,
        kind,
    })
}

#[cfg(not(any(unix, windows)))]
fn metadata_identity(metadata: &fs::Metadata) -> Result<FileIdentity> {
    let kind = kind_from_file_type(metadata.file_type())
        .ok_or_else(|| CoreError::new("staged source is not a regular file or directory"))?;
    Ok(FileIdentity {
        length: metadata.len(),
        modified: metadata.modified().ok(),
        kind,
    })
}

#[cfg(not(any(unix, windows)))]
fn file_identity(file: &File) -> io::Result<FileIdentity> {
    let metadata = file.metadata()?;
    metadata_identity(&metadata).map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))
}

fn copy_exact_and_hash<R: Read, W: Write>(
    mut reader: R,
    writer: &mut W,
    size: u64,
) -> io::Result<[u8; 32]> {
    let mut hash = Sha256::new();
    let mut remaining = size;
    let mut buffer = [0u8; 64 * 1024];
    while remaining != 0 {
        let buffer_size = u64::try_from(buffer.len()).expect("copy buffer length fits u64");
        let wanted = usize::try_from(remaining.min(buffer_size)).expect("bounded copy size");
        let count = reader.read(&mut buffer[..wanted])?;
        if count == 0 {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "payload ended before its declared size",
            ));
        }
        writer.write_all(&buffer[..count])?;
        hash.update(&buffer[..count]);
        remaining -= u64::try_from(count).expect("copy count fits u64");
    }
    Ok(hash.finalize())
}

fn write_header(
    file: &mut File,
    entry_count: u32,
    table_size: u64,
    payload_size: u64,
) -> Result<()> {
    file.write_all(&MAGIC)
        .and_then(|()| file.write_all(&FORMAT_VERSION.to_le_bytes()))
        .and_then(|()| file.write_all(&entry_count.to_le_bytes()))
        .and_then(|()| file.write_all(&table_size.to_le_bytes()))
        .and_then(|()| file.write_all(&payload_size.to_le_bytes()))
        .map_err(|error| CoreError::new(format!("write container header: {error}")))
}

fn read_exact(reader: &mut File, output: &mut [u8], field: &str) -> Result<()> {
    reader
        .read_exact(output)
        .map_err(|error| CoreError::new(format!("read {field}: {error}")))
}

fn read_u32(reader: &mut File, field: &str) -> Result<u32> {
    let mut bytes = [0u8; 4];
    read_exact(reader, &mut bytes, field)?;
    Ok(u32::from_le_bytes(bytes))
}

fn read_u64(reader: &mut File, field: &str) -> Result<u64> {
    let mut bytes = [0u8; 8];
    read_exact(reader, &mut bytes, field)?;
    Ok(u64::from_le_bytes(bytes))
}

fn checked_add(left: u64, right: u64, description: &str) -> Result<u64> {
    left.checked_add(right)
        .ok_or_else(|| CoreError::new(format!("{description} overflow")))
}

fn require_real_directory(path: &Path, description: &str) -> Result<()> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| CoreError::io(&format!("inspect {description}"), path, error))?;
    if metadata.file_type().is_symlink() || !metadata.file_type().is_dir() {
        return Err(CoreError::new(format!(
            "{description} is not a real directory: '{}'",
            path.display()
        )));
    }
    Ok(())
}

fn require_absent_output_path(path: &Path) -> Result<()> {
    match fs::symlink_metadata(path) {
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(CoreError::io("inspect output path", path, error)),
        Ok(_) => Err(CoreError::new(format!(
            "output path must not exist: '{}'",
            path.display()
        ))),
    }
}

fn unique_sibling_name(base: &std::ffi::OsStr, kind: &str, attempt: u64) -> std::ffi::OsString {
    let mut name = std::ffi::OsString::from(".");
    name.push(base);
    name.push(format!(
        ".narwhal-document-core.{kind}.{}.{}.tmp",
        std::process::id(),
        attempt
    ));
    name
}

fn create_sibling_file(parent: &Path, base: &std::ffi::OsStr) -> Result<(PathBuf, File)> {
    for _ in 0..128 {
        let attempt = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let path = parent.join(unique_sibling_name(base, "file", attempt));
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(file) => return Ok((path, file)),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(CoreError::io("create sibling temporary file", &path, error)),
        }
    }
    Err(CoreError::new("could not reserve a sibling temporary file"))
}

fn create_sibling_directory(parent: &Path, base: &std::ffi::OsStr) -> Result<PathBuf> {
    for _ in 0..128 {
        let attempt = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let path = parent.join(unique_sibling_name(base, "directory", attempt));
        match fs::create_dir(&path) {
            Ok(()) => return Ok(path),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => {
                return Err(CoreError::io(
                    "create sibling temporary directory",
                    &path,
                    error,
                ));
            }
        }
    }
    Err(CoreError::new(
        "could not reserve a sibling temporary directory",
    ))
}

struct FileGuard {
    path: PathBuf,
    armed: bool,
}

impl FileGuard {
    fn new(path: PathBuf) -> Self {
        Self { path, armed: true }
    }

    fn disarm(&mut self) {
        self.armed = false;
    }
}

impl Drop for FileGuard {
    fn drop(&mut self) {
        if self.armed {
            let _ = fs::remove_file(&self.path);
        }
    }
}

struct DirectoryGuard {
    path: PathBuf,
    armed: bool,
}

impl DirectoryGuard {
    fn new(path: PathBuf) -> Self {
        Self { path, armed: true }
    }

    fn disarm(&mut self) {
        self.armed = false;
    }
}

impl Drop for DirectoryGuard {
    fn drop(&mut self) {
        if self.armed {
            let _ = fs::remove_dir_all(&self.path);
        }
    }
}

#[cfg(target_os = "linux")]
fn publish_directory_no_replace(source: &Path, target: &Path) -> io::Result<()> {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;

    const AT_FDCWD: i32 = -100;
    const RENAME_NOREPLACE: u32 = 1;

    #[link(name = "c")]
    extern "C" {
        fn renameat2(
            old_directory: i32,
            old_path: *const std::ffi::c_char,
            new_directory: i32,
            new_path: *const std::ffi::c_char,
            flags: u32,
        ) -> i32;
    }

    let source = CString::new(source.as_os_str().as_bytes())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "source path contains NUL"))?;
    let target = CString::new(target.as_os_str().as_bytes())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "target path contains NUL"))?;
    let result = unsafe {
        renameat2(
            AT_FDCWD,
            source.as_ptr(),
            AT_FDCWD,
            target.as_ptr(),
            RENAME_NOREPLACE,
        )
    };
    if result == 0 {
        Ok(())
    } else {
        Err(io::Error::last_os_error())
    }
}

#[cfg(windows)]
fn publish_directory_no_replace(source: &Path, target: &Path) -> io::Result<()> {
    fs::rename(source, target)
}

#[cfg(not(any(target_os = "linux", windows)))]
fn publish_directory_no_replace(source: &Path, target: &Path) -> io::Result<()> {
    if target.try_exists()? {
        return Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            "output path already exists",
        ));
    }
    fs::rename(source, target)
}

#[cfg(windows)]
fn atomic_replace(source: &Path, target: &Path) -> io::Result<()> {
    use std::os::windows::ffi::OsStrExt;

    const MOVEFILE_REPLACE_EXISTING: u32 = 0x1;
    const MOVEFILE_WRITE_THROUGH: u32 = 0x8;

    #[link(name = "kernel32")]
    extern "system" {
        fn MoveFileExW(existing: *const u16, replacement: *const u16, flags: u32) -> i32;
    }

    let source: Vec<u16> = source.as_os_str().encode_wide().chain(Some(0)).collect();
    let target: Vec<u16> = target.as_os_str().encode_wide().chain(Some(0)).collect();
    let succeeded = unsafe {
        MoveFileExW(
            source.as_ptr(),
            target.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if succeeded == 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(not(windows))]
fn atomic_replace(source: &Path, target: &Path) -> io::Result<()> {
    fs::rename(source, target)
}

#[cfg(unix)]
fn sync_directory(path: &Path) -> Result<()> {
    let directory = File::open(path)
        .map_err(|error| CoreError::io("open directory for synchronization", path, error))?;
    directory
        .sync_all()
        .map_err(|error| CoreError::io("synchronize directory", path, error))
}

#[cfg(not(unix))]
fn sync_directory(_path: &Path) -> Result<()> {
    Ok(())
}

fn sync_parent(path: &Path) -> Result<()> {
    sync_directory(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_root(label: &str) -> (PathBuf, DirectoryGuard) {
        let sequence = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let root = std::env::temp_dir().join(format!(
            "narwhal-document-core-unit-{}-{label}-{sequence}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir(&root).unwrap();
        let guard = DirectoryGuard::new(root.clone());
        (root, guard)
    }

    fn staging_fixture(root: &Path) -> PathBuf {
        let staging = root.join("staging");
        fs::create_dir(&staging).unwrap();
        fs::create_dir(staging.join("assets")).unwrap();
        fs::write(staging.join("manifest.json"), b"{}").unwrap();
        fs::write(staging.join("document.3dm"), b"original document").unwrap();
        fs::write(staging.join("assets/note.txt"), b"nested asset").unwrap();
        staging
    }

    #[test]
    fn member_capacity_guard_rejects_before_retaining_entry_over_limit() {
        let limit = usize::try_from(MAX_ENTRIES).unwrap();
        assert!(ensure_member_capacity(limit - 1).is_ok());
        let error = ensure_member_capacity(limit).unwrap_err();
        assert!(error
            .to_string()
            .contains("entry count exceeds limit 10000"));
    }

    #[test]
    fn injected_unpack_publish_failure_leaves_destination_absent_and_removes_temp() {
        let (root, _guard) = test_root("unpack-publish-failure");
        let staging = staging_fixture(&root);
        let archive = root.join("document.narwhal");
        let output = root.join("output");
        pack(&staging, &archive).unwrap();

        let error = unpack_with_publisher(&archive, &output, |temporary, destination| {
            assert!(temporary.join("manifest.json").is_file());
            assert!(temporary.join("document.3dm").is_file());
            assert!(!destination.exists());
            Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "injected publish failure",
            ))
        })
        .unwrap_err();
        assert!(error.to_string().contains("injected publish failure"));
        assert!(!output.exists());
        assert_eq!(
            fs::read_dir(&root)
                .unwrap()
                .filter_map(std::result::Result::ok)
                .filter(|entry| entry.file_name().to_string_lossy().contains(".tmp"))
                .count(),
            0
        );
    }

    #[cfg(unix)]
    #[test]
    fn pack_rejects_regular_file_replacement_after_hashing() {
        let (root, _guard) = test_root("replace-after-hash");
        let staging = staging_fixture(&root);
        let document = staging.join("document.3dm");
        let retained = staging.join("retained-document");
        let target = root.join("document.narwhal");

        let error = pack_with_hook(&staging, &target, || {
            fs::rename(&document, &retained).unwrap();
            fs::write(&document, b"replacement document").unwrap();
        })
        .unwrap_err();
        assert!(error.to_string().contains("staged source changed"));
        assert!(!target.exists());
    }

    #[cfg(unix)]
    #[test]
    fn pack_rejects_directory_symlink_swap_after_collection() {
        use std::os::unix::fs::symlink;

        let (root, _guard) = test_root("directory-symlink-after-collection");
        let staging = staging_fixture(&root);
        let assets = staging.join("assets");
        let retained = staging.join("retained-assets");
        let target = root.join("document.narwhal");

        let error = pack_with_hook(&staging, &target, || {
            fs::rename(&assets, &retained).unwrap();
            symlink(&retained, &assets).unwrap();
        })
        .unwrap_err();
        assert!(error.to_string().contains("staged source changed"));
        assert!(!target.exists());
    }

    #[cfg(unix)]
    #[test]
    fn pack_rejects_symlink_swap_after_hashing() {
        use std::os::unix::fs::symlink;

        let (root, _guard) = test_root("symlink-after-hash");
        let staging = staging_fixture(&root);
        let document = staging.join("document.3dm");
        let retained = staging.join("retained-document");
        let target = root.join("document.narwhal");

        let error = pack_with_hook(&staging, &target, || {
            fs::rename(&document, &retained).unwrap();
            symlink(&retained, &document).unwrap();
        })
        .unwrap_err();
        assert!(error.to_string().contains("staged source changed"));
        assert!(!target.exists());
    }
}
