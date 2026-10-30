//! Windows portable manifest admission plus verified payload/cache modules.
//!
//! OURS: packaged names are ASCII-only to avoid unproven Windows Unicode aliases.
//! The measured 0.4.7 ZIP uses only ASCII payload names. This restriction does not
//! apply to user documents, launcher arguments or the user's cache-root path.
//! Manifest admission alone does not verify archive bytes, ACLs or cached files;
//! payload/cache modules enforce those separate checks. No signing claim is made.

use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use std::fmt;

pub mod cache;
pub mod pack;
pub mod payload;
#[cfg(test)]
mod payload_tests;
pub mod update;
#[cfg(windows)]
pub mod windows;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Identity {
    pub version: String,
    pub source_revision: String,
    pub archive_sha256: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FileRecord {
    /// Canonical slash-separated payload-relative name, never a destination path.
    pub path: String,
    pub size: u64,
    pub sha256: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    pub schema: u32,
    pub platform: String,
    pub identity: Identity,
    pub archive_size: u64,
    /// Regular files only. An extractor must reject archive links/directories
    /// masquerading as records, and all archive members absent from this list.
    pub files: Vec<FileRecord>,
}

#[derive(Clone, Copy, Debug)]
pub struct Limits {
    pub files: usize,
    pub archive_bytes: u64,
    pub expanded_bytes: u64,
    pub file_bytes: u64,
    pub free_space_reserve: u64,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            files: 50_000,
            archive_bytes: 4 * 1024 * 1024 * 1024,
            expanded_bytes: 8 * 1024 * 1024 * 1024,
            file_bytes: 2 * 1024 * 1024 * 1024,
            free_space_reserve: 256 * 1024 * 1024,
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub struct Admission {
    /// A single component below an independently secured per-user cache root.
    pub cache_key: String,
    pub expanded_bytes: u64,
    pub required_free_bytes: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Error {
    Schema,
    Platform,
    Identity,
    Version,
    Digest,
    FileCount,
    ArchiveSize,
    Path,
    Collision,
    FileSize,
    ExpandedSize,
    ArithmeticOverflow,
    MissingEntrypoint,
    LowDisk,
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let message = match self {
            Self::Schema => "unsupported payload manifest schema",
            Self::Platform => "payload is not Windows x86_64",
            Self::Identity => "payload identity does not match the expected release",
            Self::Version => "release must use canonical 0.4.x version numbering",
            Self::Digest => "invalid source revision or SHA-256 spelling",
            Self::FileCount => "payload file count is empty or exceeds its limit",
            Self::ArchiveSize => "compressed payload size is empty or exceeds its limit",
            Self::Path => "unsafe or unsupported Windows payload path",
            Self::Collision => "payload contains a Windows path alias or file/directory collision",
            Self::FileSize => "payload file exceeds its size limit",
            Self::ExpandedSize => "expanded payload exceeds its size limit",
            Self::ArithmeticOverflow => "payload size arithmetic overflow",
            Self::MissingEntrypoint => "payload is missing a required launch component",
            Self::LowDisk => "not enough free cache-disk space to stage the payload safely",
        };
        f.write_str(message)
    }
}

impl std::error::Error for Error {}

fn hex(text: &str, length: usize) -> bool {
    text.len() == length
        && text
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

fn version(text: &str) -> bool {
    let Some(patch) = text.strip_prefix("0.4.") else {
        return false;
    };
    !patch.is_empty()
        && patch.len() <= 9
        && patch.bytes().all(|c| c.is_ascii_digit())
        && (patch == "0" || !patch.starts_with('0'))
}

/// Reject Windows namespace aliases before constructing any filesystem path.
/// Short-name (8.3) aliases are excluded conservatively by rejecting '~'.
pub fn validate_path(path: &str) -> Result<(), Error> {
    if path.is_empty() || path.len() > 240 || !path.is_ascii() {
        return Err(Error::Path);
    }
    for component in path.split('/') {
        if component.is_empty()
            || component == "."
            || component == ".."
            || component.ends_with(['.', ' '])
            || component
                .bytes()
                .any(|c| c < 32 || c == 127 || b"\\:<>\"|?*~".contains(&c))
        {
            return Err(Error::Path);
        }
        let stem = component
            .split('.')
            .next()
            .unwrap()
            .trim_end_matches(' ')
            .to_ascii_uppercase();
        if matches!(
            stem.as_str(),
            "CON" | "PRN" | "AUX" | "NUL" | "CONIN$" | "CONOUT$"
        ) || (stem.len() == 4
            && (stem.starts_with("COM") || stem.starts_with("LPT"))
            && stem.as_bytes()[3].is_ascii_digit())
        {
            return Err(Error::Path);
        }
    }
    Ok(())
}

/// Validate manifest metadata against separately supplied release identity.
/// `free_bytes` must be measured on the private cache filesystem immediately
/// before staging. Allocation can still fail afterwards and must be recoverable.
pub fn admit(
    manifest: &Manifest,
    expected: &Identity,
    limits: Limits,
    free_bytes: u64,
) -> Result<Admission, Error> {
    if manifest.schema != 1 {
        return Err(Error::Schema);
    }
    if manifest.platform != "windows-x86_64" {
        return Err(Error::Platform);
    }
    if &manifest.identity != expected {
        return Err(Error::Identity);
    }
    if !version(&expected.version) {
        return Err(Error::Version);
    }
    if !hex(&expected.source_revision, 40) || !hex(&expected.archive_sha256, 64) {
        return Err(Error::Digest);
    }
    if manifest.files.is_empty() || manifest.files.len() > limits.files {
        return Err(Error::FileCount);
    }
    if manifest.archive_size == 0 || manifest.archive_size > limits.archive_bytes {
        return Err(Error::ArchiveSize);
    }
    let mut names = BTreeSet::new();
    let mut expanded = 0u64;
    for file in &manifest.files {
        validate_path(&file.path)?;
        if !hex(&file.sha256, 64) {
            return Err(Error::Digest);
        }
        if !names.insert(file.path.to_ascii_lowercase()) {
            return Err(Error::Collision);
        }
        if file.size > limits.file_bytes {
            return Err(Error::FileSize);
        }
        expanded = expanded
            .checked_add(file.size)
            .ok_or(Error::ArithmeticOverflow)?;
        if expanded > limits.expanded_bytes {
            return Err(Error::ExpandedSize);
        }
    }
    for name in &names {
        for (end, _) in name.match_indices('/') {
            if names.contains(&name[..end]) {
                return Err(Error::Collision);
            }
        }
    }
    for required in [
        "Narwhal3D.exe",
        "runtime/blender.exe",
        "app/launch/bootstrap.py",
        "app/runtime/windows-x86_64/bin/narwhal-welcome.exe",
    ] {
        if !manifest
            .files
            .iter()
            .any(|file| file.path == required && file.size > 0)
        {
            return Err(Error::MissingEntrypoint);
        }
    }
    // Reserve a staging archive too, even if a later embedded-payload reader can
    // stream directly. Runtime updates must never consume the desktop's last bytes.
    let required_free_bytes = expanded
        .checked_add(manifest.archive_size)
        .and_then(|n| n.checked_add(limits.free_space_reserve))
        .ok_or(Error::ArithmeticOverflow)?;
    if free_bytes < required_free_bytes {
        return Err(Error::LowDisk);
    }
    Ok(Admission {
        cache_key: format!("{}-{}", expected.version, expected.archive_sha256),
        expanded_bytes: expanded,
        required_free_bytes,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> Manifest {
        Manifest {
            schema: 1,
            platform: "windows-x86_64".into(),
            identity: Identity {
                version: "0.4.8".into(),
                source_revision: "58506732456af52150087da1a9d3c2f2f224b1df".into(),
                archive_sha256: "a".repeat(64),
            },
            archive_size: 100,
            files: [
                "Narwhal3D.exe",
                "runtime/blender.exe",
                "app/launch/bootstrap.py",
                "app/runtime/windows-x86_64/bin/narwhal-welcome.exe",
            ]
            .into_iter()
            .map(|path| FileRecord {
                path: path.into(),
                size: 10,
                sha256: "b".repeat(64),
            })
            .collect(),
        }
    }

    fn check(m: &Manifest) -> Result<Admission, Error> {
        admit(m, &m.identity, Limits::default(), u64::MAX)
    }

    #[test]
    fn canonical_paths_and_manifest_are_admitted() {
        for path in [
            "app/a.py",
            ".runtime.json",
            "runtime/5.2/python/lib/a-b_c+1.dll",
        ] {
            assert_eq!(validate_path(path), Ok(()), "{path}");
        }
        let a = check(&fixture()).unwrap();
        assert_eq!(a.expanded_bytes, 40);
        assert_eq!(a.required_free_bytes, 268_435_596);
        assert_eq!(a.cache_key, format!("0.4.8-{}", "a".repeat(64)));
    }

    #[test]
    fn windows_namespaces_and_aliases_are_refused() {
        for path in [
            "",
            "/escape",
            "../escape",
            "app/../../escape",
            "app//a",
            "app/./a",
            "C:/escape",
            "C:relative",
            "//server/share",
            "\\\\?\\C:\\a",
            "app\\a",
            "app/a:stream",
            "app/a.",
            "app/a ",
            "app/CON",
            "app/nul.txt",
            "AUX.exe",
            "app/COM1.log",
            "app/COM1 .log",
            "app/NUL .txt",
            "app/lpt9",
            "app/CONIN$",
            "app/conout$.log",
            "app/a\0b",
            "app/a\nb",
            "app/?.dll",
            "app/*.dll",
            "app/a|b",
            "app/a<b",
            "app/a>b",
            "app/a\"b",
            "app/PROGRA~1/a",
            "app/caf\u{e9}.py",
            "app/COM\u{b9}",
            "app/",
        ] {
            assert_eq!(validate_path(path), Err(Error::Path), "{path:?}");
        }
        assert_eq!(validate_path(&"a".repeat(241)), Err(Error::Path));
    }

    #[test]
    fn exact_and_casefold_duplicates_fail() {
        for path in ["runtime/blender.exe", "RUNTIME/BLENDER.EXE"] {
            let mut m = fixture();
            m.files.push(FileRecord {
                path: path.into(),
                size: 1,
                sha256: "c".repeat(64),
            });
            assert_eq!(check(&m), Err(Error::Collision));
        }
    }

    #[test]
    fn file_directory_collisions_fail_in_both_orders() {
        for reverse in [false, true] {
            let mut m = fixture();
            m.files.push(FileRecord {
                path: "APP".into(),
                size: 1,
                sha256: "c".repeat(64),
            });
            if reverse {
                m.files.reverse();
            }
            assert_eq!(check(&m), Err(Error::Collision));
        }
    }

    #[test]
    fn version_and_expected_identity_are_bound() {
        let original = fixture();
        let mut changed = original.clone();
        changed.identity.archive_sha256 = "c".repeat(64);
        assert_eq!(
            admit(&changed, &original.identity, Limits::default(), u64::MAX),
            Err(Error::Identity)
        );
        assert_ne!(
            check(&changed).unwrap().cache_key,
            check(&original).unwrap().cache_key
        );
        for value in ["0.5.0", "0.4.08", "0.4.", "v0.4.8", "0.4.8/..", "0.4.-1"] {
            changed.identity.version = value.into();
            assert_eq!(check(&changed), Err(Error::Version));
        }
    }

    #[test]
    fn invalid_digests_are_not_cache_keys() {
        for invalid in [
            "g".repeat(64),
            "a".repeat(63),
            "A".repeat(64),
            "../".repeat(22),
        ] {
            let mut m = fixture();
            m.identity.archive_sha256 = invalid;
            assert_eq!(check(&m), Err(Error::Digest));
        }
        let mut m = fixture();
        m.files[0].sha256 = "bad".into();
        assert_eq!(check(&m), Err(Error::Digest));
        m = fixture();
        m.identity.source_revision = "bad".into();
        assert_eq!(check(&m), Err(Error::Digest));
    }

    #[test]
    fn unknown_schema_and_wrong_architecture_fail() {
        let mut m = fixture();
        m.schema = 2;
        assert_eq!(check(&m), Err(Error::Schema));
        m.schema = 1;
        m.platform = "linux-x86_64".into();
        assert_eq!(check(&m), Err(Error::Platform));
    }

    #[test]
    fn counts_sizes_and_free_space_have_hard_bounds() {
        let m = fixture();
        let limits = Limits {
            files: 4,
            archive_bytes: 100,
            expanded_bytes: 40,
            file_bytes: 10,
            free_space_reserve: 20,
        };
        assert!(admit(&m, &m.identity, limits, 160).is_ok());
        assert_eq!(admit(&m, &m.identity, limits, 159), Err(Error::LowDisk));
        assert_eq!(
            admit(&m, &m.identity, Limits { files: 3, ..limits }, 160),
            Err(Error::FileCount)
        );
        assert_eq!(
            admit(
                &m,
                &m.identity,
                Limits {
                    archive_bytes: 99,
                    ..limits
                },
                160
            ),
            Err(Error::ArchiveSize)
        );
        assert_eq!(
            admit(
                &m,
                &m.identity,
                Limits {
                    expanded_bytes: 39,
                    ..limits
                },
                160
            ),
            Err(Error::ExpandedSize)
        );
        assert_eq!(
            admit(
                &m,
                &m.identity,
                Limits {
                    file_bytes: 9,
                    ..limits
                },
                160
            ),
            Err(Error::FileSize)
        );
        let mut empty = m.clone();
        empty.files.clear();
        assert_eq!(check(&empty), Err(Error::FileCount));
        empty = m.clone();
        empty.archive_size = 0;
        assert_eq!(check(&empty), Err(Error::ArchiveSize));
    }

    #[test]
    fn expanded_and_reservation_arithmetic_cannot_wrap() {
        let mut m = fixture();
        let limits = Limits {
            file_bytes: u64::MAX,
            expanded_bytes: u64::MAX,
            ..Limits::default()
        };
        m.files[0].size = u64::MAX;
        assert_eq!(
            admit(&m, &m.identity, limits, u64::MAX),
            Err(Error::ArithmeticOverflow)
        );
        m.files[0].size = u64::MAX - 30;
        assert_eq!(
            admit(&m, &m.identity, limits, u64::MAX),
            Err(Error::ArithmeticOverflow)
        );
    }

    #[test]
    fn every_entrypoint_must_be_present_nonempty_and_canonical() {
        for index in 0..4 {
            let mut m = fixture();
            m.files.remove(index);
            assert_eq!(check(&m), Err(Error::MissingEntrypoint));
            m = fixture();
            m.files[index].size = 0;
            assert_eq!(check(&m), Err(Error::MissingEntrypoint));
            m = fixture();
            m.files[index].path = m.files[index].path.to_ascii_uppercase();
            assert_eq!(check(&m), Err(Error::MissingEntrypoint));
        }
    }
}
