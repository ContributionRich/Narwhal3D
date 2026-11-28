//! Real descriptor ABI tests: independent published vectors, bounds and ownership.
use narwhal_application_core::{narwhal_runtime_hash_abi, narwhal_runtime_hash_file_v1};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

static NEXT: AtomicU64 = AtomicU64::new(0);
struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "narwhal-runtime-hash-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }
    fn file(&self, bytes: &[u8]) -> File {
        let mut file = OpenOptions::new()
            .create_new(true)
            .read(true)
            .write(true)
            .open(self.0.join("input"))
            .unwrap();
        file.write_all(bytes).unwrap();
        file.flush().unwrap();
        file
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}
#[cfg(unix)]
fn descriptor(file: &File) -> usize {
    use std::os::fd::AsRawFd;
    file.as_raw_fd() as usize
}
#[cfg(windows)]
fn descriptor(file: &File) -> usize {
    use std::os::windows::io::AsRawHandle;
    file.as_raw_handle() as usize
}
fn call(file: &File, size: u64, output: &mut [u8; 32]) -> i32 {
    // SAFETY: both the live file and exclusive fixed output outlive the call.
    unsafe {
        narwhal_runtime_hash_file_v1(descriptor(file), size, output.as_mut_ptr(), output.len())
    }
}
fn check(bytes: &[u8], expected: &str) {
    let fixture = Fixture::new();
    let mut file = fixture.file(bytes);
    // The digest always starts at zero, not at the caller's current cursor.
    file.seek(SeekFrom::End(0)).unwrap();
    let mut output = [0xa5; 32];
    assert_eq!(call(&file, bytes.len() as u64, &mut output), 0);
    assert_eq!(
        output
            .iter()
            .map(|x| format!("{x:02x}"))
            .collect::<String>(),
        expected
    );
    // Native duplication must never close the caller's original file.
    file.seek(SeekFrom::Start(0)).unwrap();
    let mut actual = Vec::new();
    file.read_to_end(&mut actual).unwrap();
    assert_eq!(actual, bytes);
}
#[test]
fn published_sha256_vectors_and_stream_boundaries() {
    assert_eq!(narwhal_runtime_hash_abi(), 1);
    check(
        b"",
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    );
    check(
        b"abc",
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
    check(
        b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
        "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
    );
    check(
        &vec![b'a'; 1_000_000],
        "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0",
    );
}
#[test]
fn size_output_and_descriptor_preflight_are_fail_closed() {
    let fixture = Fixture::new();
    let file = fixture.file(b"abc");
    let mut output = [0xa5; 32];
    for size in [0, 2, 4] {
        assert_eq!(call(&file, size, &mut output), 4);
        assert_eq!(output, [0xa5; 32]);
    }
    assert_eq!(call(&file, 16 * 1024 * 1024 * 1024 + 1, &mut output), 3);
    // SAFETY: output preflight rejects null/short buffers before inspecting the descriptor.
    unsafe {
        assert_eq!(
            narwhal_runtime_hash_file_v1(usize::MAX, 3, std::ptr::null_mut(), 32),
            6
        );
        assert_eq!(
            narwhal_runtime_hash_file_v1(descriptor(&file), 3, output.as_mut_ptr(), 31),
            6
        );
        assert_eq!(
            narwhal_runtime_hash_file_v1(usize::MAX, 3, output.as_mut_ptr(), 32),
            1
        );
    }
    assert_eq!(output, [0xa5; 32]);
}
#[test]
fn changed_contents_are_rehashed_and_large_sparse_files_refused() {
    let fixture = Fixture::new();
    let mut file = fixture.file(b"abc");
    let mut first = [0; 32];
    assert_eq!(call(&file, 3, &mut first), 0);
    file.seek(SeekFrom::Start(0)).unwrap();
    file.write_all(b"abd").unwrap();
    file.flush().unwrap();
    let mut second = [0; 32];
    assert_eq!(call(&file, 3, &mut second), 0);
    assert_ne!(first, second);
    file.set_len(16 * 1024 * 1024 * 1024 + 1).unwrap();
    let mut sentinel = [0xa5; 32];
    assert_eq!(
        call(&file, file.metadata().unwrap().len(), &mut sentinel),
        3
    );
    assert_eq!(sentinel, [0xa5; 32]);
}
#[cfg(unix)]
#[test]
fn directories_and_devices_are_not_runtime_binaries() {
    let fixture = Fixture::new();
    for file in [
        File::open(&fixture.0).unwrap(),
        File::open("/dev/null").unwrap(),
    ] {
        let mut output = [0xa5; 32];
        assert_eq!(call(&file, 0, &mut output), 2);
        assert_eq!(output, [0xa5; 32]);
    }
}
