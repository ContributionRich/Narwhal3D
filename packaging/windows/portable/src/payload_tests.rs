//! Tiny real ZIP fixtures; never a substitute for a real application artifact.
use crate::{
    payload::{Payload, MAGIC},
    FileRecord, Identity, Limits, Manifest,
};
use sha2::{Digest, Sha256};
use std::fs;
use std::io::{Cursor, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use zip::{write::SimpleFileOptions, CompressionMethod, ZipWriter};
static NEXT: AtomicUsize = AtomicUsize::new(0);

pub(crate) struct Temp(pub PathBuf);
impl Temp {
    pub(crate) fn new() -> Self {
        let root = std::env::temp_dir().join(format!(
            "narwhal-payload-test-{}-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir(&root).unwrap();
        Self(root)
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

pub(crate) fn fixture(path: &Path, alteration: &str) -> Vec<u8> {
    let names = [
        "Narwhal3D.exe",
        "runtime/blender.exe",
        "app/launch/bootstrap.py",
        "app/runtime/windows-x86_64/bin/narwhal-welcome.exe",
    ];
    let mut files: Vec<_> = names
        .iter()
        .enumerate()
        .map(|(i, name)| {
            let bytes = format!("fixture-{i}").into_bytes();
            (name.to_string(), bytes)
        })
        .collect();
    let records = files
        .iter()
        .map(|(path, bytes)| FileRecord {
            path: path.clone(),
            size: bytes.len() as u64,
            sha256: hash(bytes),
        })
        .collect();
    if alteration == "traversal" {
        files[0].0 = "../Narwhal3D.exe".into();
    }
    if alteration == "unexpected" {
        files[0].0 = "not-in-manifest.exe".into();
    }
    if alteration == "duplicate" {
        files[0].0 = "runtime/bLender.exe".into();
    }
    let mut archive = ZipWriter::new(Cursor::new(Vec::new()));
    for (name, bytes) in files {
        let options = SimpleFileOptions::default()
            .compression_method(CompressionMethod::Deflated)
            .unix_permissions(0o644);
        if alteration == "symlink" && name == names[0] {
            archive.add_symlink(name, "outside", options).unwrap();
        } else {
            archive.start_file(name, options).unwrap();
            archive.write_all(&bytes).unwrap();
        }
    }
    let mut archive = archive.finish().unwrap().into_inner();
    if alteration == "duplicate" {
        // Writer rejects duplicate names. Change both local and central names,
        // preserving lengths, entry counts, file CRCs and all ZIP offsets.
        let old = b"runtime/bLender.exe";
        let offsets: Vec<_> = archive
            .windows(old.len())
            .enumerate()
            .filter_map(|(i, bytes)| (bytes == old).then_some(i))
            .collect();
        assert_eq!(offsets.len(), 2);
        for offset in offsets {
            archive[offset..offset + old.len()].copy_from_slice(b"runtime/blender.exe");
        }
    }
    let mut manifest = Manifest {
        schema: 1,
        platform: "windows-x86_64".into(),
        identity: Identity {
            version: if alteration == "version_049" {
                "0.4.9"
            } else {
                "0.4.8"
            }
            .into(),
            source_revision: "5".repeat(40),
            archive_sha256: hash(&archive),
        },
        archive_size: archive.len() as u64,
        files: records,
    };
    if alteration == "file_hash" {
        manifest.files[0].sha256 = "0".repeat(64);
    }
    if alteration == "expanded_size" {
        manifest.files[0].size = 1;
    }
    if alteration == "case_alias" {
        manifest.files[0].path = "RUNTIME/BLENDER.EXE".into();
    }
    let json = serde_json::to_vec(&manifest).unwrap();
    let mut exe = vec![0u8; 64];
    exe[..2].copy_from_slice(b"MZ");
    exe.extend_from_slice(&json);
    exe.extend_from_slice(&archive);
    exe.extend_from_slice(MAGIC);
    for n in [
        64u64,
        json.len() as u64,
        64 + json.len() as u64,
        archive.len() as u64,
    ] {
        exe.extend_from_slice(&n.to_le_bytes());
    }
    exe.extend_from_slice(&Sha256::digest(&json));
    exe.extend_from_slice(&Sha256::digest(&archive));
    fs::write(path, &exe).unwrap();
    exe
}

#[test]
fn archive_member_hashes_are_verified_without_extraction() {
    let temp = Temp::new();
    let exe = temp.0.join("fixture.exe");
    fixture(&exe, "file_hash");
    let payload = Payload::open(&exe, Limits::default()).unwrap();
    assert!(payload.verify_contents(Limits::default()).is_err());
    drop(payload);
    fixture(&exe, "");
    Payload::open(&exe, Limits::default())
        .unwrap()
        .verify_contents(Limits::default())
        .unwrap();
    assert_eq!(fs::read_dir(&temp.0).unwrap().count(), 1);
}

#[test]
fn extract_and_verify_real_compressed_bytes() {
    let temp = Temp::new();
    let exe = temp.0.join("fixture.exe");
    fixture(&exe, "");
    let payload = Payload::open(&exe, Limits::default()).unwrap();
    let staging = temp.0.join("stage");
    fs::create_dir(&staging).unwrap();
    payload.extract(&staging, Limits::default()).unwrap();
    assert_eq!(
        fs::read(staging.join("app/launch/bootstrap.py")).unwrap(),
        b"fixture-2"
    );
    payload.verify_tree(&staging).unwrap();
    assert!(payload.extract(&staging, Limits::default()).is_err());
}
#[test]
fn corrupt_footer_manifest_and_archive_are_rejected() {
    let temp = Temp::new();
    let exe = temp.0.join("fixture.exe");
    let original = fixture(&exe, "");
    let footer = &original[original.len() - 112..];
    let start = u64::from_le_bytes(footer[32..40].try_into().unwrap()) as usize;
    let name_len =
        u16::from_le_bytes(original[start + 26..start + 28].try_into().unwrap()) as usize;
    let extra_len =
        u16::from_le_bytes(original[start + 28..start + 30].try_into().unwrap()) as usize;
    // Damage compressed data while preserving a parseable central directory.
    let compressed_byte = start + 30 + name_len + extra_len;
    for index in [
        compressed_byte,
        0,
        64,
        original.len() - 112,
        original.len() - 113,
        original.len() - 1,
    ] {
        let mut bytes = original.clone();
        bytes[index] ^= 1;
        fs::write(&exe, bytes).unwrap();
        assert!(
            Payload::open(&exe, Limits::default()).is_err(),
            "corruption at {index}"
        );
    }
    for length in [0, 63, 175, original.len() - 1] {
        fs::write(&exe, &original[..length]).unwrap();
        assert!(Payload::open(&exe, Limits::default()).is_err());
    }
}
#[test]
fn unsafe_or_mismatched_zip_members_are_rejected() {
    let temp = Temp::new();
    let exe = temp.0.join("fixture.exe");
    for alteration in [
        "traversal",
        "unexpected",
        "duplicate",
        "symlink",
        "case_alias",
        "expanded_size",
    ] {
        fixture(&exe, alteration);
        assert!(
            Payload::open(&exe, Limits::default()).is_err(),
            "{alteration}"
        );
    }
}
#[test]
fn extraction_rejects_a_wrong_per_file_digest() {
    let temp = Temp::new();
    let exe = temp.0.join("fixture.exe");
    fixture(&exe, "file_hash");
    let payload = Payload::open(&exe, Limits::default()).unwrap();
    let stage = temp.0.join("stage");
    fs::create_dir(&stage).unwrap();
    assert!(payload.extract(&stage, Limits::default()).is_err());
}
#[test]
fn cache_reuse_detects_changed_missing_and_extra_files() {
    let temp = Temp::new();
    let exe = temp.0.join("fixture.exe");
    fixture(&exe, "");
    let payload = Payload::open(&exe, Limits::default()).unwrap();
    for mode in ["changed", "missing", "extra_file", "extra_directory"] {
        let stage = temp.0.join(mode);
        fs::create_dir(&stage).unwrap();
        payload.extract(&stage, Limits::default()).unwrap();
        match mode {
            "changed" => fs::write(stage.join("Narwhal3D.exe"), b"corrupt!!").unwrap(),
            "missing" => fs::remove_file(stage.join("Narwhal3D.exe")).unwrap(),
            "extra_file" => fs::write(stage.join("untrusted.dll"), b"bad").unwrap(),
            _ => fs::create_dir(stage.join("untrusted")).unwrap(),
        }
        assert!(payload.verify_tree(&stage).is_err(), "{mode}");
    }
}
#[cfg(unix)]
#[test]
fn symlinked_staging_and_cached_files_are_rejected() {
    let temp = Temp::new();
    let exe = temp.0.join("fixture.exe");
    fixture(&exe, "");
    let payload = Payload::open(&exe, Limits::default()).unwrap();
    let outside = temp.0.join("outside");
    fs::create_dir(&outside).unwrap();
    let stage = temp.0.join("stage");
    std::os::unix::fs::symlink(&outside, &stage).unwrap();
    assert!(payload.extract(&stage, Limits::default()).is_err());
    fs::remove_file(&stage).unwrap();
    fs::create_dir(&stage).unwrap();
    payload.extract(&stage, Limits::default()).unwrap();
    fs::remove_file(stage.join("Narwhal3D.exe")).unwrap();
    fs::write(outside.join("target"), b"fixture-0").unwrap();
    std::os::unix::fs::symlink(outside.join("target"), stage.join("Narwhal3D.exe")).unwrap();
    assert!(payload.verify_tree(&stage).is_err());
}
