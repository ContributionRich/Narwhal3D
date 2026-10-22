//! SPDX-License-Identifier: GPL-3.0-or-later
//!
//! Native acceleration core for Narwhal 3D's pure interchange and parsing modules.
//!
//! This crate is a dependency-free C ABI. It never owns document geometry and
//! never lets a Rust-owned pointer cross the call boundary: every input is a
//! caller-owned array, every output is a caller-owned buffer sized by a checked
//! two-pass query, and all fixed-width types are validated before any slice is
//! formed. The Python layer owns file I/O and is the complete fallback: when
//! the library is absent, an export is missing, a call errors, or the output is
//! malformed, Python runs its unchanged implementation.
//!
//! `lib.rs` holds export shims only; the implementations live in focused modules
//! under `src/`.

pub mod bearing;
pub mod calc;
pub mod coil;
pub mod command_search;
pub mod coords;
pub mod dxf;
pub mod gridsnap;
pub mod interpolate;
pub mod joinchain;
pub mod macro_parse;
pub mod macro_plan;
pub mod numparse;
pub mod obj;
pub mod stl;
pub mod svgpath;
pub mod tween;
pub mod vector_export;

use std::mem::{align_of, size_of};
use std::slice;

const ABI_VERSION: u32 = 1;

/// Validate the metadata needed to form a Rust slice from an ABI pointer.
///
/// Rust requires the complete byte span to fit in `isize`, the pointer to be
/// aligned, and the address calculation not to wrap. Allocation provenance and
/// readability/writability remain requirements of the C ABI contract.
fn raw_span_is_valid<T>(ptr: *const T, len: usize) -> bool {
    if len == 0 {
        return true;
    }
    if ptr.is_null() || !(ptr as usize).is_multiple_of(align_of::<T>()) {
        return false;
    }
    let Some(bytes) = len.checked_mul(size_of::<T>()) else {
        return false;
    };
    bytes <= isize::MAX as usize && (ptr as usize).checked_add(bytes).is_some()
}

/// Form a shared slice only after validating all metadata controlled by the ABI
/// caller. A null pointer is accepted only for an empty slice.
///
/// # Safety
///
/// For non-empty spans, `ptr` must reference `len` initialized `T` values for
/// the returned lifetime.
pub(crate) unsafe fn checked_slice<'a, T>(ptr: *const T, len: usize) -> Option<&'a [T]> {
    if len == 0 {
        return Some(&[]);
    }
    if !raw_span_is_valid(ptr, len) {
        return None;
    }
    Some(unsafe { slice::from_raw_parts(ptr, len) })
}

/// Form a mutable slice only after validating all metadata controlled by the
/// ABI caller. A null pointer is accepted only for an empty slice.
///
/// # Safety
///
/// For non-empty spans, `ptr` must exclusively reference `len` writable `T`
/// values for the returned lifetime.
pub(crate) unsafe fn checked_slice_mut<'a, T>(ptr: *mut T, len: usize) -> Option<&'a mut [T]> {
    if len == 0 {
        return Some(&mut []);
    }
    if !raw_span_is_valid(ptr.cast_const(), len) {
        return None;
    }
    Some(unsafe { slice::from_raw_parts_mut(ptr, len) })
}

/// Return the stable ABI version for the pure-core interface.
#[no_mangle]
pub extern "C" fn narwhal_pure_core_abi_version() -> u32 {
    ABI_VERSION
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn raw_slice_metadata_rejects_null_typed_overflow_and_address_wrap() {
        assert!(unsafe { checked_slice::<u8>(std::ptr::null(), 0) }.is_some());
        assert!(unsafe { checked_slice::<u8>(std::ptr::null(), 1) }.is_none());

        let f64_ptr = std::ptr::NonNull::<f64>::dangling().as_ptr();
        let too_many_f64 = (isize::MAX as usize / size_of::<f64>()) + 1;
        assert!(unsafe { checked_slice(f64_ptr, too_many_f64) }.is_none());

        let wrapping = usize::MAX as *const u8;
        assert!(unsafe { checked_slice(wrapping, 1) }.is_none());
    }
}
