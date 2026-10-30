//! Streaming assembly of a flat, already-qualified Windows package directory.
//! No payload code is executed; output publication refuses overwrites.
use crate::{
    admit,
    payload::{Payload, MAGIC},
    FileRecord, Identity, Limits, Manifest,
};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::{self, Read, Write},
    path::{Path, PathBuf},
};
use zip::{write::SimpleFileOptions, CompressionMethod, ZipWriter};

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}
fn hash_file(path: &Path) -> io::Result<[u8; 32]> {
    let mut file = File::open(path)?;
    let mut hash = Sha256::new();
    let mut buffer = [0u8; 131072];
    loop {
        let n = file.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        hash.update(&buffer[..n]);
    }
    Ok(hash.finalize().into())
}
pub(crate) fn regular(path: &Path) -> io::Result<File> {
    let metadata = fs::symlink_metadata(path)?;
    crate::payload::no_link(&metadata)?;
    if !metadata.is_file() {
        return Err(invalid("package input is not a regular file"));
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options
            .custom_flags(windows_sys::Win32::Storage::FileSystem::FILE_FLAG_OPEN_REPARSE_POINT)
            .share_mode(1);
    }
    let file = options.open(path)?;
    crate::payload::no_link(&file.metadata()?)?;
    Ok(file)
}
fn stub_bytes(path: &Path) -> io::Result<Vec<u8>> {
    let mut file = regular(path)?;
    let length = file.metadata()?.len();
    if !(256..=32 * 1024 * 1024).contains(&length) {
        return Err(invalid("stub size outside bounds"));
    }
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)?;
    if &bytes[..2] != b"MZ" {
        return Err(invalid("stub is not PE"));
    }
    let pe = u32::from_le_bytes(bytes[60..64].try_into().unwrap()) as usize;
    if pe.checked_add(24 + 152).is_none_or(|end| end > bytes.len())
        || &bytes[pe..pe + 4] != b"PE\0\0"
    {
        return Err(invalid("invalid PE headers"));
    }
    let u16at = |n| u16::from_le_bytes(bytes[n..n + 2].try_into().unwrap());
    if u16at(pe + 4) != 0x8664
        || u16at(pe + 24) != 0x20b
        || u16at(pe + 20) < 152
        || u16at(pe + 22) & 0x2000 != 0
    {
        return Err(invalid("stub must be an x86_64 PE32+ executable"));
    }
    if bytes[pe + 24 + 144..pe + 24 + 152].iter().any(|b| *b != 0) {
        return Err(invalid(
            "signed stubs are not supported by this payload format",
        ));
    }
    if bytes.len() >= 112 && &bytes[bytes.len() - 112..bytes.len() - 96] == MAGIC {
        return Err(invalid("stub already contains a payload"));
    }
    Ok(bytes)
}

pub fn assemble(
    stub: &Path,
    root: &Path,
    output: &Path,
    version: &str,
    source: &str,
) -> io::Result<Manifest> {
    let limits = Limits::default();
    let stub = stub_bytes(stub)?;
    crate::payload::regular_directory(root)?;
    if fs::symlink_metadata(output).is_ok() {
        return Err(invalid("output already exists"));
    }
    let parent = output
        .parent()
        .ok_or_else(|| invalid("output has no parent"))?;
    crate::payload::regular_directory(parent)?;
    if fs::canonicalize(parent)?.starts_with(fs::canonicalize(root)?) {
        return Err(invalid("output and staging must be outside the input tree"));
    }
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|_| invalid("invalid clock"))?
        .as_nanos();
    let staging = parent.join(format!(".narwhal-pack-{}-{nonce}", std::process::id()));
    fs::create_dir(&staging)?;
    let archive_path = staging.join("payload.zip");
    let mut paths: Vec<(String, PathBuf)> = Vec::new();
    let mut pending = vec![(root.to_owned(), 0usize)];
    let mut entries = 0usize;
    while let Some((directory, depth)) = pending.pop() {
        if depth > 32 {
            return Err(invalid("package directory depth exceeds limit"));
        }
        for entry in fs::read_dir(directory)? {
            entries += 1;
            if entries > limits.files * 2 {
                return Err(invalid("package entry count exceeds limit"));
            }
            let entry = entry?;
            let path = entry.path();
            let metadata = fs::symlink_metadata(&path)?;
            crate::payload::no_link(&metadata)?;
            if metadata.is_dir() {
                pending.push((path, depth + 1));
            } else {
                if paths.len() >= limits.files || !metadata.is_file() {
                    return Err(invalid("package file limit/type"));
                }
                let name = path
                    .strip_prefix(root)
                    .map_err(|_| invalid("package path escaped root"))?
                    .to_str()
                    .ok_or_else(|| invalid("non-Unicode payload name"))?
                    .replace('\\', "/");
                crate::validate_path(&name).map_err(|_| invalid("unsafe payload name"))?;
                paths.push((name, path));
            }
        }
    }
    paths.sort_by(|a, b| a.0.cmp(&b.0));
    // Reserve a conservative uncompressed ZIP plus final EXE before writing either.
    let mut reservation = 256u64 * 1024 * 1024 + stub.len() as u64;
    for (_, path) in &paths {
        let size = regular(path)?.metadata()?.len();
        if size > limits.file_bytes {
            return Err(invalid("package member too large"));
        }
        reservation = reservation
            .checked_add(
                size.checked_mul(3)
                    .and_then(|n| n.checked_add(8192))
                    .ok_or_else(|| invalid("reservation overflow"))?,
            )
            .ok_or_else(|| invalid("reservation overflow"))?;
    }
    if fs2::available_space(parent)? < reservation {
        return Err(invalid("not enough disk space for package assembly"));
    }
    let mut zip = ZipWriter::new(
        OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&archive_path)?,
    );
    let mut files = Vec::new();
    let mut expanded = 0u64;
    let mut buffer = [0u8; 131072];
    for (name, path) in paths {
        let mut input = regular(&path)?;
        let expected = input.metadata()?.len();
        if expected > limits.file_bytes {
            return Err(invalid("package member too large"));
        }
        expanded = expanded
            .checked_add(expected)
            .ok_or_else(|| invalid("size overflow"))?;
        if expanded > limits.expanded_bytes {
            return Err(invalid("package exceeds expanded limit"));
        }
        zip.start_file(
            &name,
            SimpleFileOptions::default()
                .compression_method(CompressionMethod::Deflated)
                .unix_permissions(0o644),
        )?;
        let mut hash = Sha256::new();
        let mut size = 0u64;
        loop {
            let n = input.read(&mut buffer)?;
            if n == 0 {
                break;
            }
            size += n as u64;
            if size > expected {
                return Err(invalid("package input changed during assembly"));
            }
            zip.write_all(&buffer[..n])?;
            hash.update(&buffer[..n]);
        }
        if size != expected {
            return Err(invalid("package input truncated during assembly"));
        }
        files.push(FileRecord {
            path: name,
            size,
            sha256: format!("{:x}", hash.finalize()),
        });
    }
    zip.finish()?.sync_all()?;
    let archive_size = fs::metadata(&archive_path)?.len();
    let archive_hash = hash_file(&archive_path)?;
    let identity = Identity {
        version: version.into(),
        source_revision: source.into(),
        archive_sha256: archive_hash.iter().map(|b| format!("{b:02x}")).collect(),
    };
    let manifest = Manifest {
        schema: 1,
        platform: "windows-x86_64".into(),
        identity,
        archive_size,
        files,
    };
    admit(
        &manifest,
        &manifest.identity,
        limits,
        fs2::available_space(parent)?,
    )
    .map_err(|_| invalid("assembled payload admission failed"))?;
    let json = serde_json::to_vec(&manifest)?;
    if json.len() as u64 > crate::payload::MANIFEST_LIMIT {
        return Err(invalid("manifest exceeds limit"));
    }
    let part = staging.join("application.exe");
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&part)?;
    file.write_all(&stub)?;
    file.write_all(&json)?;
    io::copy(&mut File::open(&archive_path)?, &mut file)?;
    file.write_all(MAGIC)?;
    for number in [
        stub.len() as u64,
        json.len() as u64,
        stub.len() as u64 + json.len() as u64,
        archive_size,
    ] {
        file.write_all(&number.to_le_bytes())?;
    }
    file.write_all(&Sha256::digest(&json))?;
    file.write_all(&archive_hash)?;
    file.sync_all()?;
    drop(file);
    let payload = Payload::open(&part, limits)?;
    payload.verify_contents(limits)?;
    drop(payload);
    fs::hard_link(&part, output)?;
    if let Err(error) = fs::remove_dir_all(&staging) {
        eprintln!("Package published; retained assembly staging after cleanup failure: {error}");
    }
    Ok(manifest)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::payload_tests::Temp;
    fn setup(temp: &Temp) -> (PathBuf, PathBuf) {
        let stub = temp.0.join("stub.exe");
        let mut pe = vec![0u8; 512];
        pe[..2].copy_from_slice(b"MZ");
        pe[60..64].copy_from_slice(&64u32.to_le_bytes());
        pe[64..68].copy_from_slice(b"PE\0\0");
        pe[68..70].copy_from_slice(&0x8664u16.to_le_bytes());
        pe[70..72].copy_from_slice(&1u16.to_le_bytes());
        pe[84..86].copy_from_slice(&240u16.to_le_bytes());
        pe[86..88].copy_from_slice(&2u16.to_le_bytes());
        pe[88..90].copy_from_slice(&0x20bu16.to_le_bytes());
        fs::write(&stub, pe).unwrap();
        let root = temp.0.join("package");
        fs::create_dir(&root).unwrap();
        for name in [
            "Narwhal3D.exe",
            "runtime/blender.exe",
            "app/launch/bootstrap.py",
            "app/runtime/windows-x86_64/bin/narwhal-welcome.exe",
        ] {
            let path = root.join(name);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, format!("test data: {name}")).unwrap();
        }
        (stub, root)
    }
    #[test]
    fn assembled_payload_round_trips_every_file() {
        let temp = Temp::new();
        let (stub, root) = setup(&temp);
        let output = temp.0.join("application.exe");
        let manifest = assemble(&stub, &root, &output, "0.4.8", &"5".repeat(40)).unwrap();
        assert_eq!(manifest.files.len(), 4);
        let payload = Payload::open(&output, Limits::default()).unwrap();
        assert_eq!(payload.manifest(), &manifest);
        let extracted = temp.0.join("extracted");
        fs::create_dir(&extracted).unwrap();
        payload.extract(&extracted, Limits::default()).unwrap();
        for record in manifest.files {
            assert_eq!(
                fs::read(root.join(&record.path)).unwrap(),
                fs::read(extracted.join(record.path)).unwrap()
            );
        }
    }
    #[test]
    fn output_inside_input_tree_is_refused_before_staging() {
        let temp = Temp::new();
        let (stub, root) = setup(&temp);
        let output = root.join("application.exe");
        let error = assemble(&stub, &root, &output, "0.4.8", &"5".repeat(40)).unwrap_err();
        assert!(error.to_string().contains("outside the input tree"));
        assert!(!output.exists());
        assert_eq!(fs::read_dir(&root).unwrap().count(), 3);
    }
    #[test]
    fn output_collision_and_signed_stub_are_refused() {
        let temp = Temp::new();
        let (stub, root) = setup(&temp);
        let output = temp.0.join("application.exe");
        fs::write(&output, b"keep").unwrap();
        assert!(assemble(&stub, &root, &output, "0.4.8", &"5".repeat(40)).is_err());
        assert_eq!(fs::read(&output).unwrap(), b"keep");
        fs::remove_file(&output).unwrap();
        let mut bytes = fs::read(&stub).unwrap();
        bytes[64 + 24 + 144] = 1;
        fs::write(&stub, bytes).unwrap();
        assert!(assemble(&stub, &root, &output, "0.4.8", &"5".repeat(40)).is_err());
        assert!(!output.exists());
    }
    #[cfg(unix)]
    #[test]
    fn source_links_are_not_followed() {
        let temp = Temp::new();
        let (stub, root) = setup(&temp);
        let output = temp.0.join("application.exe");
        let path = root.join("runtime/blender.exe");
        fs::remove_file(&path).unwrap();
        std::os::unix::fs::symlink("../../private-file", path).unwrap();
        assert!(assemble(&stub, &root, &output, "0.4.8", &"5".repeat(40)).is_err());
        assert!(!output.exists());
    }
}
