// SPDX-License-Identifier: GPL-3.0-or-later

use narwhal_document_core::container::{
    inspect, pack, unpack, ENTRY_FIXED_SIZE, FORMAT_VERSION, HEADER_SIZE, MAGIC,
    MAX_CANONICAL_PATH_SIZE, MAX_CONTAINER_SIZE, MAX_ENTRIES, MAX_MEMBER_SIZE,
};
use narwhal_document_core::sha256::{digest, hex};
use std::fs::{self, File, OpenOptions};
use std::io::{Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};

static TEST_SEQUENCE: AtomicU64 = AtomicU64::new(0);

struct TestDirectory(PathBuf);

impl TestDirectory {
    fn new(label: &str) -> Self {
        let sequence = TEST_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "narwhal-document-core-test-{}-{label}-{sequence}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&path);
        fs::create_dir(&path).unwrap();
        Self(path)
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TestDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[derive(Clone)]
struct RawMember {
    path: Vec<u8>,
    payload: Vec<u8>,
    offset: Option<u64>,
    declared_size: Option<u64>,
    checksum: Option<[u8; 32]>,
}

impl RawMember {
    fn new(path: &str, payload: &[u8]) -> Self {
        Self {
            path: path.as_bytes().to_vec(),
            payload: payload.to_vec(),
            offset: None,
            declared_size: None,
            checksum: None,
        }
    }
}

fn valid_members() -> Vec<RawMember> {
    vec![
        RawMember::new("document.3dm", b"3dm"),
        RawMember::new("manifest.json", br#"{"format":1}"#),
    ]
}

fn raw_container(members: &[RawMember], header_payload_size: Option<u64>) -> Vec<u8> {
    let table_size: u64 = members
        .iter()
        .map(|member| ENTRY_FIXED_SIZE + member.path.len() as u64)
        .sum();
    let payload_start = HEADER_SIZE + table_size;
    let actual_payload_size: u64 = members
        .iter()
        .map(|member| member.payload.len() as u64)
        .sum();
    let mut next_offset = payload_start;
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&MAGIC);
    bytes.extend_from_slice(&FORMAT_VERSION.to_le_bytes());
    bytes.extend_from_slice(&(members.len() as u32).to_le_bytes());
    bytes.extend_from_slice(&table_size.to_le_bytes());
    bytes.extend_from_slice(
        &header_payload_size
            .unwrap_or(actual_payload_size)
            .to_le_bytes(),
    );
    for member in members {
        bytes.extend_from_slice(&(member.path.len() as u32).to_le_bytes());
        bytes.extend_from_slice(&member.path);
        bytes.extend_from_slice(&member.offset.unwrap_or(next_offset).to_le_bytes());
        bytes.extend_from_slice(
            &member
                .declared_size
                .unwrap_or(member.payload.len() as u64)
                .to_le_bytes(),
        );
        bytes.extend_from_slice(&member.checksum.unwrap_or_else(|| digest(&member.payload)));
        next_offset += member.payload.len() as u64;
    }
    for member in members {
        bytes.extend_from_slice(&member.payload);
    }
    bytes
}

fn write_raw(root: &Path, name: &str, bytes: &[u8]) -> PathBuf {
    let path = root.join(name);
    fs::write(&path, bytes).unwrap();
    path
}

fn inspect_error(path: &Path) -> String {
    let mut output = Vec::new();
    inspect(path, &mut output).unwrap_err().to_string()
}

fn create_fixture(root: &Path) -> PathBuf {
    let staging = root.join("staging");
    fs::create_dir_all(staging.join("assets/deep")).unwrap();
    fs::create_dir_all(staging.join("masters")).unwrap();
    fs::write(staging.join("manifest.json"), br#"{"name":"fixture"}"#).unwrap();
    fs::write(staging.join("document.3dm"), b"exact document bytes").unwrap();
    fs::write(staging.join("assets/deep/readme.txt"), b"nested member").unwrap();
    fs::write(
        staging.join("masters/12345678-1234-abcd-9876-0123456789ab.brep"),
        b"exact master",
    )
    .unwrap();
    staging
}

#[test]
fn cli_version_is_exact() {
    let output = Command::new(env!("CARGO_BIN_EXE_narwhal-document-core"))
        .arg("--version")
        .output()
        .unwrap();
    assert!(output.status.success());
    assert_eq!(output.stdout, b"narwhal-document-core 1\n");
    assert!(output.stderr.is_empty());
}

#[test]
fn cli_rejects_bad_usage() {
    let output = Command::new(env!("CARGO_BIN_EXE_narwhal-document-core"))
        .arg("pack")
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(2));
    assert!(output.stdout.is_empty());
    assert!(String::from_utf8(output.stderr).unwrap().contains("usage:"));
}

#[test]
fn pack_is_byte_for_byte_deterministic() {
    let root = TestDirectory::new("deterministic");
    let staging = create_fixture(root.path());
    let first = root.path().join("first.narwhal");
    let second = root.path().join("second.narwhal");
    pack(&staging, &first).unwrap();
    pack(&staging, &second).unwrap();
    assert_eq!(fs::read(first).unwrap(), fs::read(second).unwrap());
}

#[test]
fn pack_and_unpack_round_trip_nested_files_and_master() {
    let root = TestDirectory::new("round-trip");
    let staging = create_fixture(root.path());
    let archive = root.path().join("document.narwhal");
    let output = root.path().join("output");
    pack(&staging, &archive).unwrap();
    unpack(&archive, &output).unwrap();

    for relative in [
        "manifest.json",
        "document.3dm",
        "assets/deep/readme.txt",
        "masters/12345678-1234-abcd-9876-0123456789ab.brep",
    ] {
        assert_eq!(
            fs::read(staging.join(relative)).unwrap(),
            fs::read(output.join(relative)).unwrap()
        );
    }
}

#[test]
fn inspect_prints_sorted_checksum_size_and_path_lines() {
    let root = TestDirectory::new("inspect");
    let staging = create_fixture(root.path());
    let archive = root.path().join("document.narwhal");
    pack(&staging, &archive).unwrap();
    let mut output = Vec::new();
    inspect(&archive, &mut output).unwrap();
    let text = String::from_utf8(output).unwrap();
    let lines: Vec<_> = text.lines().collect();
    let paths: Vec<_> = lines
        .iter()
        .map(|line| line.split('\t').nth(2).unwrap())
        .collect();
    assert_eq!(
        paths,
        vec![
            "assets/deep/readme.txt",
            "document.3dm",
            "manifest.json",
            "masters/12345678-1234-abcd-9876-0123456789ab.brep",
        ]
    );
    assert!(lines.iter().all(|line| line.split('\t').count() == 3));
    assert!(lines[0].starts_with(&hex(&digest(b"nested member"))));
}

#[test]
fn checksum_mismatch_is_rejected_before_output_members_exist() {
    let root = TestDirectory::new("checksum");
    let archive = write_raw(
        root.path(),
        "bad.narwhal",
        &raw_container(&valid_members(), None),
    );
    let mut file = OpenOptions::new()
        .read(true)
        .write(true)
        .open(&archive)
        .unwrap();
    file.seek(SeekFrom::End(-1)).unwrap();
    file.write_all(b"X").unwrap();
    let output = root.path().join("output");
    let error = unpack(&archive, &output).unwrap_err().to_string();
    assert!(error.contains("checksum mismatch"));
    assert!(!output.exists());
}

#[test]
fn failed_pack_preserves_existing_target_and_leaves_no_temp_file() {
    let root = TestDirectory::new("atomic-preservation");
    let staging = root.path().join("staging");
    fs::create_dir(&staging).unwrap();
    fs::write(staging.join("manifest.json"), b"{}").unwrap();
    let target = root.path().join("document.narwhal");
    fs::write(&target, b"previous valid target").unwrap();
    assert!(pack(&staging, &target).is_err());
    assert_eq!(fs::read(&target).unwrap(), b"previous valid target");
    assert_eq!(
        fs::read_dir(root.path())
            .unwrap()
            .filter_map(Result::ok)
            .filter(|entry| entry.file_name().to_string_lossy().contains(".tmp"))
            .count(),
        0
    );
}

#[cfg(unix)]
#[test]
fn staging_symlink_is_rejected() {
    use std::os::unix::fs::symlink;

    let root = TestDirectory::new("symlink");
    let staging = create_fixture(root.path());
    symlink(staging.join("document.3dm"), staging.join("linked.3dm")).unwrap();
    let error = pack(&staging, &root.path().join("out.narwhal"))
        .unwrap_err()
        .to_string();
    assert!(error.contains("symlinks are not permitted"));
}

#[cfg(unix)]
#[test]
fn staging_special_file_is_rejected() {
    use std::os::unix::net::UnixListener;

    let root = TestDirectory::new("special");
    let staging = create_fixture(root.path());
    let socket_path = staging.join("socket");
    let _listener = UnixListener::bind(&socket_path).unwrap();
    let error = pack(&staging, &root.path().join("out.narwhal"))
        .unwrap_err()
        .to_string();
    assert!(error.contains("special files are not permitted"));
}

#[test]
fn traversal_absolute_drive_and_backslash_paths_are_rejected() {
    let root = TestDirectory::new("unsafe-paths");
    for (index, bad_path) in [
        "../escape",
        "a/../../escape",
        "/absolute",
        "C:/drive",
        "folder:name",
        "folder/file:stream",
        "folder\\member",
        "a//member",
        "a/./member",
        "trailing/",
    ]
    .iter()
    .enumerate()
    {
        let mut members = valid_members();
        members.push(RawMember::new(bad_path, b"bad"));
        members.sort_by(|left, right| left.path.cmp(&right.path));
        let archive = write_raw(
            root.path(),
            &format!("unsafe-{index}.narwhal"),
            &raw_container(&members, None),
        );
        assert!(
            inspect(&archive, &mut Vec::new()).is_err(),
            "accepted {bad_path}"
        );
    }
}

#[test]
fn duplicate_paths_are_rejected() {
    let root = TestDirectory::new("duplicate");
    let members = vec![
        RawMember::new("document.3dm", b"a"),
        RawMember::new("manifest.json", b"b"),
        RawMember::new("manifest.json", b"c"),
    ];
    let archive = write_raw(
        root.path(),
        "duplicate.narwhal",
        &raw_container(&members, None),
    );
    assert!(inspect_error(&archive).contains("duplicate member path"));
}

#[test]
fn unsorted_table_is_rejected() {
    let root = TestDirectory::new("unsorted");
    let members = vec![
        RawMember::new("manifest.json", b"a"),
        RawMember::new("document.3dm", b"b"),
    ];
    let archive = write_raw(
        root.path(),
        "unsorted.narwhal",
        &raw_container(&members, None),
    );
    assert!(inspect_error(&archive).contains("not sorted"));
}

#[test]
fn trailing_bytes_are_rejected() {
    let root = TestDirectory::new("trailing");
    let mut bytes = raw_container(&valid_members(), None);
    bytes.extend_from_slice(b"trailing");
    let archive = write_raw(root.path(), "trailing.narwhal", &bytes);
    assert!(inspect_error(&archive).contains("trailing or missing bytes"));
}

#[test]
fn payload_offset_before_payload_area_is_rejected() {
    let root = TestDirectory::new("offset-before");
    let mut members = valid_members();
    members[0].offset = Some(HEADER_SIZE);
    let archive = write_raw(
        root.path(),
        "before.narwhal",
        &raw_container(&members, None),
    );
    assert!(inspect_error(&archive).contains("out of bounds"));
}

#[test]
fn overlapping_payload_ranges_are_rejected() {
    let root = TestDirectory::new("overlap");
    let mut members = valid_members();
    let table_size: u64 = members
        .iter()
        .map(|member| ENTRY_FIXED_SIZE + member.path.len() as u64)
        .sum();
    let first_offset = HEADER_SIZE + table_size;
    members[1].offset = Some(first_offset);
    let archive = write_raw(
        root.path(),
        "overlap.narwhal",
        &raw_container(&members, None),
    );
    assert!(inspect_error(&archive).contains("overlap"));
}

#[test]
fn gapped_payload_ranges_are_rejected() {
    let root = TestDirectory::new("gap");
    let mut members = valid_members();
    let table_size: u64 = members
        .iter()
        .map(|member| ENTRY_FIXED_SIZE + member.path.len() as u64)
        .sum();
    members[0].offset = Some(HEADER_SIZE + table_size + 1);
    let archive = write_raw(root.path(), "gap.narwhal", &raw_container(&members, None));
    assert!(inspect_error(&archive).contains("not contiguous"));
}

#[test]
fn overflowing_payload_range_is_rejected() {
    let root = TestDirectory::new("offset-overflow");
    let mut members = valid_members();
    members[0].offset = Some(u64::MAX);
    let archive = write_raw(
        root.path(),
        "overflow.narwhal",
        &raw_container(&members, None),
    );
    assert!(inspect_error(&archive).contains("overflows"));
}

#[test]
fn malformed_utf8_path_is_rejected() {
    let root = TestDirectory::new("utf8");
    let mut members = valid_members();
    members.push(RawMember {
        path: vec![0xff],
        payload: b"bad".to_vec(),
        offset: None,
        declared_size: None,
        checksum: None,
    });
    members.sort_by(|left, right| left.path.cmp(&right.path));
    let archive = write_raw(root.path(), "utf8.narwhal", &raw_container(&members, None));
    assert!(inspect_error(&archive).contains("malformed UTF-8"));
}

#[test]
fn excessive_entry_count_is_rejected_without_allocating_entries() {
    let root = TestDirectory::new("entry-limit");
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&MAGIC);
    bytes.extend_from_slice(&FORMAT_VERSION.to_le_bytes());
    bytes.extend_from_slice(&(MAX_ENTRIES + 1).to_le_bytes());
    bytes.extend_from_slice(&0u64.to_le_bytes());
    bytes.extend_from_slice(&0u64.to_le_bytes());
    let archive = write_raw(root.path(), "entries.narwhal", &bytes);
    assert!(inspect_error(&archive).contains("entry count"));
}

#[test]
fn excessive_path_field_is_rejected() {
    let root = TestDirectory::new("path-limit");
    let mut members = valid_members();
    members.push(RawMember {
        path: vec![b'a'; MAX_CANONICAL_PATH_SIZE + 1],
        payload: Vec::new(),
        offset: None,
        declared_size: None,
        checksum: None,
    });
    members.sort_by(|left, right| left.path.cmp(&right.path));
    let archive = write_raw(root.path(), "path.narwhal", &raw_container(&members, None));
    assert!(inspect_error(&archive).contains("exceeds 1024 bytes"));
}

#[test]
fn oversized_member_declaration_is_rejected() {
    let root = TestDirectory::new("member-limit");
    let mut members = valid_members();
    members[0].payload.clear();
    members[0].declared_size = Some(MAX_MEMBER_SIZE + 1);
    let header_payload = members[1].payload.len() as u64;
    let archive = write_raw(
        root.path(),
        "member.narwhal",
        &raw_container(&members, Some(header_payload)),
    );
    assert!(inspect_error(&archive).contains("exceeds 512 MiB"));
}

#[test]
fn oversized_sparse_container_is_rejected_from_metadata() {
    let root = TestDirectory::new("container-limit");
    let archive = root.path().join("large.narwhal");
    let file = File::create(&archive).unwrap();
    file.set_len(MAX_CONTAINER_SIZE + 1).unwrap();
    assert!(inspect_error(&archive).contains("exceeds 4 GiB"));
}

#[test]
fn missing_required_root_member_is_rejected() {
    let root = TestDirectory::new("required");
    let members = vec![RawMember::new("manifest.json", b"{}")];
    let archive = write_raw(
        root.path(),
        "required.narwhal",
        &raw_container(&members, None),
    );
    assert!(inspect_error(&archive).contains("exactly one root"));
}

#[test]
fn invalid_master_locations_and_names_are_rejected() {
    let root = TestDirectory::new("master-rules");
    for (index, bad_path) in [
        "outside.brep",
        "masters/not-a-uuid.brep",
        "masters/12345678-1234-ABCD-9876-0123456789ab.brep",
        "masters/deep/12345678-1234-abcd-9876-0123456789ab.brep",
        "masters/12345678-1234-abcd-9876-0123456789ab.BREP",
        "masters/notes.txt",
    ]
    .iter()
    .enumerate()
    {
        let mut members = valid_members();
        members.push(RawMember::new(bad_path, b"bad"));
        members.sort_by(|left, right| left.path.cmp(&right.path));
        let archive = write_raw(
            root.path(),
            &format!("master-{index}.narwhal"),
            &raw_container(&members, None),
        );
        assert!(
            inspect(&archive, &mut Vec::new()).is_err(),
            "accepted {bad_path}"
        );
    }
}

#[test]
fn unpack_requires_an_absent_output_path_with_an_existing_parent() {
    let root = TestDirectory::new("output-policy");
    let archive = write_raw(
        root.path(),
        "valid.narwhal",
        &raw_container(&valid_members(), None),
    );

    let empty = root.path().join("empty");
    fs::create_dir(&empty).unwrap();
    assert!(unpack(&archive, &empty).is_err());

    let nonempty = root.path().join("nonempty");
    fs::create_dir(&nonempty).unwrap();
    fs::write(nonempty.join("keep.txt"), b"keep").unwrap();
    assert!(unpack(&archive, &nonempty).is_err());
    assert_eq!(fs::read(nonempty.join("keep.txt")).unwrap(), b"keep");

    let file = root.path().join("file");
    fs::write(&file, b"keep").unwrap();
    assert!(unpack(&archive, &file).is_err());
    assert_eq!(fs::read(&file).unwrap(), b"keep");

    let missing_parent_output = root.path().join("missing-parent/output");
    assert!(unpack(&archive, &missing_parent_output).is_err());
    assert!(!missing_parent_output.exists());

    let output = root.path().join("new-output");
    unpack(&archive, &output).unwrap();
    assert!(output.is_dir());
}

#[test]
fn malformed_header_variants_are_rejected() {
    let root = TestDirectory::new("headers");
    let valid = raw_container(&valid_members(), None);

    let truncated = write_raw(root.path(), "truncated.narwhal", &valid[..20]);
    assert!(inspect(&truncated, &mut Vec::new()).is_err());

    let mut bad_magic = valid.clone();
    bad_magic[0] ^= 1;
    let bad_magic = write_raw(root.path(), "magic.narwhal", &bad_magic);
    assert!(inspect_error(&bad_magic).contains("magic"));

    let mut bad_version = valid;
    bad_version[8..12].copy_from_slice(&2u32.to_le_bytes());
    let bad_version = write_raw(root.path(), "version.narwhal", &bad_version);
    assert!(inspect_error(&bad_version).contains("version"));
}

#[test]
fn unused_or_short_table_bytes_are_rejected() {
    let root = TestDirectory::new("table-shape");
    let valid = raw_container(&valid_members(), None);

    let mut unused = valid.clone();
    let table_size = u64::from_le_bytes(unused[16..24].try_into().unwrap());
    unused[16..24].copy_from_slice(&(table_size + 1).to_le_bytes());
    let payload_size = u64::from_le_bytes(unused[24..32].try_into().unwrap());
    unused[24..32].copy_from_slice(&(payload_size - 1).to_le_bytes());
    let unused = write_raw(root.path(), "unused.narwhal", &unused);
    assert!(inspect(&unused, &mut Vec::new()).is_err());

    let mut short = valid;
    short[16..24].copy_from_slice(&1u64.to_le_bytes());
    let short = write_raw(root.path(), "short.narwhal", &short);
    assert!(inspect_error(&short).contains("table size"));
}

#[test]
fn zero_length_required_members_round_trip() {
    let root = TestDirectory::new("empty-members");
    let staging = root.path().join("staging");
    fs::create_dir(&staging).unwrap();
    fs::write(staging.join("document.3dm"), b"").unwrap();
    fs::write(staging.join("manifest.json"), b"").unwrap();
    let archive = root.path().join("empty.narwhal");
    let output = root.path().join("output");
    pack(&staging, &archive).unwrap();
    unpack(&archive, &output).unwrap();
    assert_eq!(fs::read(output.join("document.3dm")).unwrap(), b"");
    assert_eq!(fs::read(output.join("manifest.json")).unwrap(), b"");
}

#[test]
fn cli_pack_inspect_and_unpack_protocol_succeeds() {
    let root = TestDirectory::new("cli-flow");
    let staging = create_fixture(root.path());
    let archive = root.path().join("cli.narwhal");
    let output = root.path().join("output");
    let binary = env!("CARGO_BIN_EXE_narwhal-document-core");

    assert!(Command::new(binary)
        .args(["pack"])
        .arg(&staging)
        .arg(&archive)
        .status()
        .unwrap()
        .success());
    let inspection = Command::new(binary)
        .arg("inspect")
        .arg(&archive)
        .output()
        .unwrap();
    assert!(inspection.status.success());
    assert!(String::from_utf8(inspection.stdout)
        .unwrap()
        .contains("\tdocument.3dm\n"));
    assert!(Command::new(binary)
        .arg("unpack")
        .arg(&archive)
        .arg(&output)
        .status()
        .unwrap()
        .success());
    assert_eq!(
        fs::read(output.join("document.3dm")).unwrap(),
        b"exact document bytes"
    );
}

#[test]
fn declared_path_length_cannot_run_past_table() {
    let root = TestDirectory::new("path-overrun");
    let mut bytes = raw_container(&valid_members(), None);
    bytes[32..36].copy_from_slice(&1024u32.to_le_bytes());
    let archive = write_raw(root.path(), "path-overrun.narwhal", &bytes);
    assert!(inspect_error(&archive).contains("extends beyond entry table"));
}

#[test]
fn inspect_refuses_checksum_claim_even_when_payload_sizes_are_valid() {
    let root = TestDirectory::new("checksum-claim");
    let mut members = valid_members();
    members[0].checksum = Some([0u8; 32]);
    let archive = write_raw(root.path(), "claim.narwhal", &raw_container(&members, None));
    assert!(inspect_error(&archive).contains("checksum mismatch"));
}

#[test]
fn successful_pack_atomically_replaces_an_existing_regular_target() {
    let root = TestDirectory::new("replace-target");
    let staging = create_fixture(root.path());
    let target = root.path().join("document.narwhal");
    fs::write(&target, b"old target").unwrap();
    pack(&staging, &target).unwrap();
    assert_ne!(fs::read(&target).unwrap(), b"old target");
    let mut output = Vec::new();
    inspect(&target, &mut output).unwrap();
}

#[test]
fn publish_failure_preserves_target_directory_and_removes_sibling_temp() {
    let root = TestDirectory::new("publish-failure");
    let staging = create_fixture(root.path());
    let target = root.path().join("occupied.narwhal");
    fs::create_dir(&target).unwrap();
    fs::write(target.join("keep.txt"), b"keep").unwrap();
    assert!(pack(&staging, &target).is_err());
    assert_eq!(fs::read(target.join("keep.txt")).unwrap(), b"keep");
    assert_eq!(
        fs::read_dir(root.path())
            .unwrap()
            .filter_map(Result::ok)
            .filter(|entry| entry.file_name().to_string_lossy().contains(".tmp"))
            .count(),
        0
    );
}

#[test]
fn revised_resource_limits_are_exact() {
    assert_eq!(MAX_ENTRIES, 10_000);
    assert_eq!(MAX_CANONICAL_PATH_SIZE, 1024);
    assert_eq!(MAX_MEMBER_SIZE, 512 * 1024 * 1024);
    assert_eq!(MAX_CONTAINER_SIZE, 4 * 1024 * 1024 * 1024);
}

#[test]
fn oversized_declared_payload_is_rejected() {
    let root = TestDirectory::new("payload-limit");
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&MAGIC);
    bytes.extend_from_slice(&FORMAT_VERSION.to_le_bytes());
    bytes.extend_from_slice(&0u32.to_le_bytes());
    bytes.extend_from_slice(&0u64.to_le_bytes());
    bytes.extend_from_slice(&(MAX_CONTAINER_SIZE + 1).to_le_bytes());
    let archive = write_raw(root.path(), "payload.narwhal", &bytes);
    assert!(inspect_error(&archive).contains("total payload exceeds 4 GiB"));
}

#[test]
fn pack_rejects_sparse_member_over_512_mib_before_hashing() {
    let root = TestDirectory::new("pack-member-limit");
    let staging = root.path().join("staging");
    fs::create_dir(&staging).unwrap();
    fs::write(staging.join("manifest.json"), b"{}").unwrap();
    let document = File::create(staging.join("document.3dm")).unwrap();
    document.set_len(MAX_MEMBER_SIZE + 1).unwrap();
    let error = pack(&staging, &root.path().join("out.narwhal"))
        .unwrap_err()
        .to_string();
    assert!(error.contains("exceeds 512 MiB"));
}

#[cfg(unix)]
#[test]
fn pack_rejects_colon_in_staged_filename() {
    let root = TestDirectory::new("pack-colon");
    let staging = create_fixture(root.path());
    fs::write(staging.join("asset:stream"), b"ads-like").unwrap();
    let error = pack(&staging, &root.path().join("out.narwhal"))
        .unwrap_err()
        .to_string();
    assert!(error.contains("colon is forbidden"));
}
