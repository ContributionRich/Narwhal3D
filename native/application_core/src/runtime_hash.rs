//! Same-byte streaming SHA256 over a caller-owned runtime-file descriptor.
//! Geometry, loaded-image authority and replacement/cache guards remain with the host.
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    io,
    panic::{AssertUnwindSafe, catch_unwind},
};

const MAX_BYTES: u64 = 16 * 1024 * 1024 * 1024;
const BLOCK_BYTES: usize = 64 * 1024;

#[cfg(unix)]
fn duplicate(descriptor: usize) -> io::Result<File> {
    use std::os::fd::BorrowedFd;
    let fd = i32::try_from(descriptor).map_err(|_| io::Error::other("descriptor"))?;
    // SAFETY: the ABI caller keeps this valid descriptor alive throughout the call.
    let borrowed = unsafe { BorrowedFd::borrow_raw(fd) };
    Ok(File::from(borrowed.try_clone_to_owned()?))
}
#[cfg(windows)]
fn duplicate(descriptor: usize) -> io::Result<File> {
    use std::os::windows::io::BorrowedHandle;
    if descriptor == 0 || descriptor > isize::MAX as usize {
        return Err(io::Error::other("descriptor"));
    }
    // SAFETY: the ABI caller owns this live readable file HANDLE, not a CRT fd.
    let borrowed = unsafe { BorrowedHandle::borrow_raw(descriptor as *mut std::ffi::c_void) };
    Ok(File::from(borrowed.try_clone_to_owned()?))
}
fn read_at(file: &File, block: &mut [u8], offset: u64) -> io::Result<usize> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::FileExt;
        file.read_at(block, offset)
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::FileExt;
        file.seek_read(block, offset)
    }
}
fn digest(descriptor: usize, expected_size: u64) -> Result<[u8; 32], i32> {
    if expected_size > MAX_BYTES {
        return Err(3);
    }
    let file = duplicate(descriptor).map_err(|_| 1)?;
    let before = file.metadata().map_err(|_| 4)?;
    if !before.is_file() {
        return Err(2);
    }
    if before.len() != expected_size {
        return Err(4);
    }
    let mut hash = Sha256::new();
    let mut block = [0u8; BLOCK_BYTES];
    let mut offset = 0u64;
    while offset < expected_size {
        let count = (expected_size - offset).min(BLOCK_BYTES as u64) as usize;
        let read = match read_at(&file, &mut block[..count], offset) {
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            value => value.map_err(|_| 4)?,
        };
        if read == 0 {
            return Err(4);
        }
        hash.update(&block[..read]);
        offset += read as u64;
    }
    let after = file.metadata().map_err(|_| 4)?;
    if before.len() != after.len()
        || before.modified().map_err(|_| 4)? != after.modified().map_err(|_| 4)?
    {
        return Err(4);
    }
    Ok(hash.finalize().into())
}
#[unsafe(no_mangle)]
pub extern "C" fn narwhal_runtime_hash_abi() -> u32 {
    1
}

/// Hash exactly the complete regular file (<=16 GiB) with a fixed64KiB buffer.
/// The original descriptor is never closed. No path is searched or opened here.
/// Returns0 success,1 descriptor,2 nonregular,3 limit,4 IO/change,5 panic,6 output.
/// Failed calls leave output untouched. Host pre/post fstat, path and Windows
/// ChangeTime checks remain mandatory; this primitive does not authorize reuse.
///
/// # Safety
/// descriptor is a live readable POSIX fd or Windows HANDLE owned by the caller;
/// it must stay open throughout the call. output addresses capacity writable
/// bytes (exactly32), exclusively held for this call. No pointer is retained.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn narwhal_runtime_hash_file_v1(
    descriptor: usize,
    expected_size: u64,
    output: *mut u8,
    capacity: usize,
) -> i32 {
    if output.is_null() || capacity != 32 {
        return 6;
    }
    let value = catch_unwind(AssertUnwindSafe(|| digest(descriptor, expected_size)));
    match value {
        Ok(Ok(bytes)) => {
            // SAFETY: validated capacity plus the caller's exclusive output contract.
            unsafe {
                std::ptr::copy_nonoverlapping(bytes.as_ptr(), output, 32);
            }
            0
        }
        Ok(Err(code)) => code,
        Err(_) => 5,
    }
}
