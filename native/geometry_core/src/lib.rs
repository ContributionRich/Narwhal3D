//! SPDX-License-Identifier: GPL-3.0-or-later
//!
//! Dependency-free Rust C ABI for exact OCCT BRep construction, inspection, and
//! policy-qualified topology-local movement. All public pointers refer to
//! caller-owned storage; no allocation crosses the ABI.

use std::ffi::c_void;
use std::mem::{align_of, size_of};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::ptr;
use std::sync::{Arc, Mutex, OnceLock};

pub const ABI_VERSION: u32 = 4;
pub const CERTIFICATE_VERSION: u32 = 3;
pub const CAPABILITY_MAKE_BOX_BREP: u64 = 1 << 0;
pub const CAPABILITY_INSPECT_BREP: u64 = 1 << 1;
pub const CAPABILITY_BINTOOLS_NO_TRIANGULATION: u64 = 1 << 2;
pub const CAPABILITY_EXACT_MASS_PROPERTIES: u64 = 1 << 3;
pub const CAPABILITY_TOPOLOGY_LOCAL_MOVE: u64 = 1 << 4;
pub const CAPABILITY_EDIT_SESSIONS: u64 = 1 << 5;

pub const COMPONENT_FACE: u32 = 1;
pub const COMPONENT_EDGE: u32 = 2;
pub const COMPONENT_VERTEX: u32 = 3;

pub const VALIDATION_LIVE: u32 = 1;
pub const VALIDATION_COMMIT: u32 = 2;

pub const MAX_BUFFER_BYTES: usize = 512 * 1024 * 1024;
pub const LOCAL_MOVE_ZERO_TRANSLATION: f64 = 1.0e-14;
pub const LOCAL_MOVE_MAX_NORMAL_JUMP: f64 = 2.0e-6;
pub const LOCAL_MOVE_MAX_CANDIDATES: u32 = 25;
pub const LOCAL_MOVE_EDGE_SAMPLES: u32 = 33;
pub const MAX_DECODED_FACES: u64 = 100_000;
pub const MAX_DECODED_EDGES: u64 = 500_000;
pub const MAX_DECODED_VERTICES: u64 = 500_000;

pub const QUALITY_RECONSTRUCTED_FACES: u32 = 1 << 0;
pub const QUALITY_BSPLINE_BOUNDARY: u32 = 1 << 1;
pub const QUALITY_INTERNAL_KNOT: u32 = 1 << 2;
pub const QUALITY_MOVED_VERTEX_MAPPING: u32 = 1 << 3;
pub const QUALITY_MAPPED_INCIDENCE: u32 = 1 << 4;
pub const QUALITY_UNCHANGED_GEOMETRY: u32 = 1 << 5;

pub const ERROR_INVALID_ARGUMENT: i64 = -1;
pub const ERROR_LIMIT_EXCEEDED: i64 = -2;
pub const ERROR_BUFFER_TOO_SMALL: i64 = -3;
pub const ERROR_KERNEL_FAILURE: i64 = -4;
pub const ERROR_MALFORMED_BREP: i64 = -5;
pub const ERROR_UNEXPECTED_FAILURE: i64 = -6;
pub const ERROR_POLICY_REFUSED: i64 = -7;
pub const ERROR_SMOOTHNESS_CREASE: i64 = -8;
pub const ERROR_INVALID_HANDLE: i64 = -9;

pub const REFUSAL_NONE: u32 = 0;
pub const REFUSAL_INPUT_NOT_AIRTIGHT: u32 = 1;
pub const REFUSAL_INDEX_OUTSIDE: u32 = 2;
pub const REFUSAL_DEGENERATE: u32 = 3;
pub const REFUSAL_UNSUPPORTED_BOUNDARY: u32 = 4;
pub const REFUSAL_STRICT_CONTAINMENT: u32 = 5;
pub const REFUSAL_TOPOLOGY: u32 = 6;
pub const REFUSAL_NO_CANDIDATE: u32 = 7;
pub const REFUSAL_AMBIGUOUS: u32 = 8;
pub const REFUSAL_SMOOTHNESS: u32 = 9;
pub const REFUSAL_TOLERANCE: u32 = 10;
pub const REFUSAL_SELF_INTERSECTION: u32 = 11;
pub const REFUSAL_RESOURCE_LIMIT: u32 = 12;

const ADAPTER_OK: i32 = 0;
const ADAPTER_BUFFER_TOO_SMALL: i32 = 1;

/// Fixed-layout inspection result. Its layout remains ABI-1 compatible.
#[repr(C)]
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct NarwhalBrepInspectionV1 {
    pub struct_size: u32,
    pub abi_version: u32,
    pub valid: u32,
    pub airtight: u32,
    pub face_count: u64,
    pub edge_count: u64,
    pub vertex_count: u64,
    pub volume: f64,
    pub plane_faces: u64,
    pub cylinder_faces: u64,
    pub cone_faces: u64,
    pub sphere_faces: u64,
    pub torus_faces: u64,
    pub bezier_faces: u64,
    pub bspline_faces: u64,
    pub revolution_faces: u64,
    pub extrusion_faces: u64,
    pub offset_faces: u64,
    pub other_faces: u64,
}

/// ABI-3 certificate for topology-local movement and refusal policy.
#[repr(C)]
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct NarwhalLocalMoveCertificateV3 {
    pub struct_size: u32,
    pub abi_version: u32,
    pub status: i32,
    pub refusal_reason: u32,
    pub valid: u32,
    pub airtight: u32,
    pub self_intersection_free: u32,
    pub topology_counts_preserved: u32,
    pub moved_vertex_mapping_verified: u32,
    pub mapped_incidence_preserved: u32,
    pub unchanged_geometry_preserved: u32,
    pub selected_match_count: u32,
    pub component_kind: u32,
    pub quality_scope_bits: u32,
    pub quality_checked_bits: u32,
    pub reconstructed_face_count: u32,
    pub reconstructed_bspline_face_count: u32,
    pub internal_knot_face_count: u32,
    pub verification_failure_stage: u32,
    pub verification_failure_detail: u32,
    pub face_count: u64,
    pub edge_count: u64,
    pub vertex_count: u64,
    pub volume: f64,
    pub selected_translation_error: f64,
    pub max_boundary_error: f64,
    pub max_internal_normal_jump: f64,
    pub comparison_tolerance: f64,
    pub build_tolerance: f64,
    pub sewing_tolerance: f64,
    pub plane_faces: u64,
    pub cylinder_faces: u64,
    pub cone_faces: u64,
    pub sphere_faces: u64,
    pub torus_faces: u64,
    pub bezier_faces: u64,
    pub bspline_faces: u64,
    pub revolution_faces: u64,
    pub extrusion_faces: u64,
    pub offset_faces: u64,
    pub other_faces: u64,
}

#[repr(C)]
struct AdapterMovePolicy {
    zero_translation: f64,
    max_normal_jump: f64,
    max_candidates: u32,
    edge_samples: u32,
    max_faces: u64,
    max_edges: u64,
    max_vertices: u64,
}

const _: [(); 136] = [(); size_of::<NarwhalBrepInspectionV1>()];
const _: [(); 248] = [(); size_of::<NarwhalLocalMoveCertificateV3>()];
const _: [(); 48] = [(); size_of::<AdapterMovePolicy>()];

extern "C" {
    fn narwhal_occt_adapter_make_box(
        dx: f64,
        dy: f64,
        dz: f64,
        output: *mut u8,
        capacity: usize,
        required: *mut usize,
    ) -> i32;
    fn narwhal_occt_adapter_inspect_brep(
        input: *const u8,
        input_length: usize,
        max_faces: u64,
        max_edges: u64,
        max_vertices: u64,
        output: *mut NarwhalBrepInspectionV1,
    ) -> i32;
    fn narwhal_occt_adapter_session_import(
        input: *const u8,
        input_length: usize,
        policy: *const AdapterMovePolicy,
        session: *mut *mut c_void,
    ) -> i32;
    fn narwhal_occt_adapter_session_move(
        session: *mut c_void,
        validation_mode: u32,
        component_kind: u32,
        component_index: u64,
        dx: f64,
        dy: f64,
        dz: f64,
        output: *mut u8,
        capacity: usize,
        required: *mut usize,
        certificate: *mut NarwhalLocalMoveCertificateV3,
    ) -> i32;
    fn narwhal_occt_adapter_session_destroy(session: *mut c_void) -> i32;
    #[cfg(test)]
    fn narwhal_occt_adapter_session_live_count() -> usize;
    fn narwhal_occt_adapter_move_component(
        input: *const u8,
        input_length: usize,
        component_kind: u32,
        component_index: u64,
        dx: f64,
        dy: f64,
        dz: f64,
        policy: *const AdapterMovePolicy,
        output: *mut u8,
        capacity: usize,
        required: *mut usize,
        certificate: *mut NarwhalLocalMoveCertificateV3,
    ) -> i32;
    #[cfg(test)]
    fn narwhal_occt_adapter_make_surface_family_fixture(
        family: u32,
        output: *mut u8,
        capacity: usize,
        required: *mut usize,
        selected_face: *mut u64,
    ) -> i32;
    #[cfg(test)]
    fn narwhal_occt_adapter_make_local_move_fixture(
        output: *mut u8,
        capacity: usize,
        required: *mut usize,
    ) -> i32;
}

fn span_metadata_is_valid<T>(pointer: *const T, length: usize) -> bool {
    if length == 0 {
        return true;
    }
    if pointer.is_null() || !(pointer as usize).is_multiple_of(align_of::<T>()) {
        return false;
    }
    let Some(bytes) = length.checked_mul(size_of::<T>()) else {
        return false;
    };
    bytes <= isize::MAX as usize && (pointer as usize).checked_add(bytes).is_some()
}

fn spans_overlap(
    first: *const u8,
    first_length: usize,
    second: *const u8,
    second_length: usize,
) -> bool {
    if first_length == 0 || second_length == 0 {
        return false;
    }
    let first_start = first as usize;
    let second_start = second as usize;
    let first_end = first_start + first_length;
    let second_end = second_start + second_length;
    first_start < second_end && second_start < first_end
}

fn adapter_error(status: i32) -> i64 {
    match i64::from(status) {
        ERROR_INVALID_ARGUMENT
        | ERROR_LIMIT_EXCEEDED
        | ERROR_KERNEL_FAILURE
        | ERROR_MALFORMED_BREP
        | ERROR_UNEXPECTED_FAILURE
        | ERROR_POLICY_REFUSED
        | ERROR_SMOOTHNESS_CREASE
        | ERROR_INVALID_HANDLE => i64::from(status),
        _ => ERROR_UNEXPECTED_FAILURE,
    }
}

fn output_metadata_is_valid(output: *mut u8, capacity: usize) -> bool {
    if capacity == 0 {
        output.is_null()
    } else {
        span_metadata_is_valid(output.cast_const(), capacity)
    }
}

fn set_certificate_error(
    certificate: *mut NarwhalLocalMoveCertificateV3,
    status: i64,
    component_kind: u32,
) {
    if span_metadata_is_valid(certificate.cast_const(), 1) {
        unsafe {
            ptr::write(
                certificate,
                NarwhalLocalMoveCertificateV3 {
                    struct_size: size_of::<NarwhalLocalMoveCertificateV3>() as u32,
                    abi_version: CERTIFICATE_VERSION,
                    status: status as i32,
                    component_kind,
                    ..NarwhalLocalMoveCertificateV3::default()
                },
            );
        }
    }
}

fn move_policy() -> AdapterMovePolicy {
    AdapterMovePolicy {
        zero_translation: LOCAL_MOVE_ZERO_TRANSLATION,
        max_normal_jump: LOCAL_MOVE_MAX_NORMAL_JUMP,
        max_candidates: LOCAL_MOVE_MAX_CANDIDATES,
        edge_samples: LOCAL_MOVE_EDGE_SAMPLES,
        max_faces: MAX_DECODED_FACES,
        max_edges: MAX_DECODED_EDGES,
        max_vertices: MAX_DECODED_VERTICES,
    }
}

struct AdapterSession(*mut c_void);

// The C++ session is immutable during moves, but OCCT access is serialized by
// `SessionEntry::adapter`; ownership is transferred only to this Rust wrapper.
unsafe impl Send for AdapterSession {}

impl Drop for AdapterSession {
    fn drop(&mut self) {
        if !self.0.is_null() {
            let _ = unsafe { narwhal_occt_adapter_session_destroy(self.0) };
            self.0 = ptr::null_mut();
        }
    }
}

struct SessionEntry {
    adapter: Mutex<AdapterSession>,
}

struct SessionSlot {
    generation: u32,
    entry: Option<Arc<SessionEntry>>,
}

#[derive(Default)]
struct SessionRegistry {
    slots: Vec<SessionSlot>,
}

static SESSION_REGISTRY: OnceLock<Mutex<SessionRegistry>> = OnceLock::new();

fn session_registry() -> &'static Mutex<SessionRegistry> {
    SESSION_REGISTRY.get_or_init(|| Mutex::new(SessionRegistry::default()))
}

fn encode_session_handle(slot: usize, generation: u32) -> Option<u64> {
    let one_based = u32::try_from(slot).ok()?.checked_add(1)?;
    if generation == 0 {
        return None;
    }
    Some((u64::from(generation) << 32) | u64::from(one_based))
}

fn decode_session_handle(handle: u64) -> Option<(usize, u32)> {
    let one_based = handle as u32;
    let generation = (handle >> 32) as u32;
    if one_based == 0 || generation == 0 {
        return None;
    }
    Some(((one_based - 1) as usize, generation))
}

fn next_generation(generation: u32) -> u32 {
    let next = generation.wrapping_add(1);
    if next == 0 {
        1
    } else {
        next
    }
}

fn lookup_session(handle: u64) -> Result<Arc<SessionEntry>, i64> {
    let (slot_index, generation) = decode_session_handle(handle).ok_or(ERROR_INVALID_HANDLE)?;
    let registry = session_registry()
        .lock()
        .map_err(|_| ERROR_UNEXPECTED_FAILURE)?;
    let slot = registry.slots.get(slot_index).ok_or(ERROR_INVALID_HANDLE)?;
    if slot.generation != generation {
        return Err(ERROR_INVALID_HANDLE);
    }
    slot.entry.clone().ok_or(ERROR_INVALID_HANDLE)
}

/// Return the stable geometry-core C ABI version.
#[no_mangle]
pub extern "C" fn narwhal_geometry_core_abi_version() -> u32 {
    ABI_VERSION
}

/// Return the additive ABI-2 capability bitset.
#[no_mangle]
pub extern "C" fn narwhal_geometry_core_capabilities() -> u64 {
    CAPABILITY_MAKE_BOX_BREP
        | CAPABILITY_INSPECT_BREP
        | CAPABILITY_BINTOOLS_NO_TRIANGULATION
        | CAPABILITY_EXACT_MASS_PROPERTIES
        | CAPABILITY_TOPOLOGY_LOCAL_MOVE
        | CAPABILITY_EDIT_SESSIONS
}

fn session_import_impl(input: *const u8, input_length: usize, output_handle: *mut u64) -> i32 {
    if !span_metadata_is_valid(output_handle.cast_const(), 1) {
        return ERROR_INVALID_ARGUMENT as i32;
    }
    unsafe { ptr::write(output_handle, 0) };
    if input_length == 0 || !span_metadata_is_valid(input, input_length) {
        return ERROR_INVALID_ARGUMENT as i32;
    }
    if input_length > MAX_BUFFER_BYTES {
        return ERROR_LIMIT_EXCEEDED as i32;
    }
    if spans_overlap(
        input,
        input_length,
        output_handle.cast::<u8>(),
        size_of::<u64>(),
    ) {
        return ERROR_INVALID_ARGUMENT as i32;
    }

    let policy = move_policy();
    let mut adapter_pointer = ptr::null_mut();
    let status = unsafe {
        narwhal_occt_adapter_session_import(input, input_length, &policy, &mut adapter_pointer)
    };
    if status != ADAPTER_OK {
        return adapter_error(status) as i32;
    }
    if adapter_pointer.is_null() {
        return ERROR_UNEXPECTED_FAILURE as i32;
    }
    let adapter = AdapterSession(adapter_pointer);
    let entry = Arc::new(SessionEntry {
        adapter: Mutex::new(adapter),
    });
    let mut registry = match session_registry().lock() {
        Ok(registry) => registry,
        Err(_) => return ERROR_UNEXPECTED_FAILURE as i32,
    };
    if let Some((index, slot)) = registry
        .slots
        .iter_mut()
        .enumerate()
        .find(|(_, slot)| slot.entry.is_none())
    {
        let Some(handle) = encode_session_handle(index, slot.generation) else {
            return ERROR_LIMIT_EXCEEDED as i32;
        };
        slot.entry = Some(entry);
        unsafe { ptr::write(output_handle, handle) };
        return 0;
    }
    if registry.slots.try_reserve(1).is_err() {
        return ERROR_LIMIT_EXCEEDED as i32;
    }
    let index = registry.slots.len();
    let generation = 1;
    let Some(handle) = encode_session_handle(index, generation) else {
        return ERROR_LIMIT_EXCEEDED as i32;
    };
    registry.slots.push(SessionSlot {
        generation,
        entry: Some(entry),
    });
    unsafe { ptr::write(output_handle, handle) };
    0
}

/// Import one exact BinTools BRep into a generation-checked edit session.
///
/// # Safety
///
/// `input` must be readable for `input_length` bytes and `output_handle` writable
/// for one `u64`. The spans must not overlap.
#[no_mangle]
pub unsafe extern "C" fn narwhal_geometry_core_edit_session_import(
    input: *const u8,
    input_length: usize,
    output_handle: *mut u64,
) -> i32 {
    catch_unwind(AssertUnwindSafe(|| {
        session_import_impl(input, input_length, output_handle)
    }))
    .unwrap_or_else(|_| {
        if span_metadata_is_valid(output_handle.cast_const(), 1) {
            unsafe { ptr::write(output_handle, 0) };
        }
        ERROR_UNEXPECTED_FAILURE as i32
    })
}

fn session_release_impl(handle: u64) -> i32 {
    let Some((slot_index, generation)) = decode_session_handle(handle) else {
        return ERROR_INVALID_HANDLE as i32;
    };
    let entry = {
        let mut registry = match session_registry().lock() {
            Ok(registry) => registry,
            Err(_) => return ERROR_UNEXPECTED_FAILURE as i32,
        };
        let Some(slot) = registry.slots.get_mut(slot_index) else {
            return ERROR_INVALID_HANDLE as i32;
        };
        if slot.generation != generation {
            return ERROR_INVALID_HANDLE as i32;
        }
        let Some(entry) = slot.entry.take() else {
            return ERROR_INVALID_HANDLE as i32;
        };
        slot.generation = next_generation(slot.generation);
        entry
    };
    drop(entry);
    0
}

/// Invalidate an edit-session handle and release its cached native state.
#[no_mangle]
pub extern "C" fn narwhal_geometry_core_edit_session_release(handle: u64) -> i32 {
    catch_unwind(AssertUnwindSafe(|| session_release_impl(handle)))
        .unwrap_or(ERROR_UNEXPECTED_FAILURE as i32)
}

/// Construct an exact axis-aligned OCCT box as caller-owned BinTools bytes.
///
/// # Safety
///
/// When `capacity > 0`, `output` must point to `capacity` writable caller-owned bytes.
#[no_mangle]
pub unsafe extern "C" fn narwhal_geometry_core_make_box_brep(
    dx: f64,
    dy: f64,
    dz: f64,
    output: *mut u8,
    capacity: usize,
) -> i64 {
    if !dx.is_finite() || !dy.is_finite() || !dz.is_finite() || dx <= 0.0 || dy <= 0.0 || dz <= 0.0
    {
        return ERROR_INVALID_ARGUMENT;
    }
    if capacity > MAX_BUFFER_BYTES {
        return ERROR_LIMIT_EXCEEDED;
    }
    if !output_metadata_is_valid(output, capacity) {
        return ERROR_INVALID_ARGUMENT;
    }
    let mut required = 0usize;
    let status =
        unsafe { narwhal_occt_adapter_make_box(dx, dy, dz, output, capacity, &mut required) };
    if required > MAX_BUFFER_BYTES || required > i64::MAX as usize {
        return ERROR_LIMIT_EXCEEDED;
    }
    match status {
        ADAPTER_OK | ADAPTER_BUFFER_TOO_SMALL => required as i64,
        _ => adapter_error(status),
    }
}

/// Inspect caller-owned OCCT BinTools bytes into a fixed ABI-1 result.
///
/// # Safety
///
/// `input` must be readable for `input_length` bytes and `output` writable for one result.
#[no_mangle]
pub unsafe extern "C" fn narwhal_geometry_core_inspect_brep(
    input: *const u8,
    input_length: usize,
    output: *mut NarwhalBrepInspectionV1,
) -> i32 {
    if !span_metadata_is_valid(output.cast_const(), 1) {
        return ERROR_INVALID_ARGUMENT as i32;
    }
    if input_length == 0
        || input_length > MAX_BUFFER_BYTES
        || !span_metadata_is_valid(input, input_length)
    {
        unsafe { ptr::write(output, NarwhalBrepInspectionV1::default()) };
        return if input_length > MAX_BUFFER_BYTES {
            ERROR_LIMIT_EXCEEDED as i32
        } else {
            ERROR_INVALID_ARGUMENT as i32
        };
    }
    if spans_overlap(
        input,
        input_length,
        output.cast::<u8>(),
        size_of::<NarwhalBrepInspectionV1>(),
    ) {
        return ERROR_INVALID_ARGUMENT as i32;
    }
    unsafe { ptr::write(output, NarwhalBrepInspectionV1::default()) };
    let status = unsafe {
        narwhal_occt_adapter_inspect_brep(
            input,
            input_length,
            MAX_DECODED_FACES,
            MAX_DECODED_EDGES,
            MAX_DECODED_VERTICES,
            output,
        )
    };
    if status == ADAPTER_OK {
        0
    } else {
        unsafe { ptr::write(output, NarwhalBrepInspectionV1::default()) };
        adapter_error(status) as i32
    }
}

/// Translate one face, edge, or vertex by exact topology-local BRep reconstruction.
///
/// This is a caller-owned two-pass API. A size query uses null `output` and zero
/// `capacity`; successful calls and undersized-buffer calls return the required byte
/// count. Policy refusals return a stable negative code and a populated certificate.
///
/// # Safety
///
/// `input` must be readable for `input_length` bytes, `certificate` must be aligned and
/// writable for one certificate, and nonzero `capacity` requires that many writable
/// bytes at `output`. Input and output spans must not overlap.
#[no_mangle]
pub unsafe extern "C" fn narwhal_geometry_core_move_component_brep(
    input: *const u8,
    input_length: usize,
    component_kind: u32,
    component_index: u64,
    dx: f64,
    dy: f64,
    dz: f64,
    output: *mut u8,
    capacity: usize,
    certificate: *mut NarwhalLocalMoveCertificateV3,
) -> i64 {
    if !span_metadata_is_valid(certificate.cast_const(), 1) {
        return ERROR_INVALID_ARGUMENT;
    }
    if input_length == 0 || !span_metadata_is_valid(input, input_length) {
        set_certificate_error(certificate, ERROR_INVALID_ARGUMENT, component_kind);
        return ERROR_INVALID_ARGUMENT;
    }
    if input_length > MAX_BUFFER_BYTES || capacity > MAX_BUFFER_BYTES {
        set_certificate_error(certificate, ERROR_LIMIT_EXCEEDED, component_kind);
        return ERROR_LIMIT_EXCEEDED;
    }
    if !output_metadata_is_valid(output, capacity) {
        set_certificate_error(certificate, ERROR_INVALID_ARGUMENT, component_kind);
        return ERROR_INVALID_ARGUMENT;
    }
    let certificate_bytes = size_of::<NarwhalLocalMoveCertificateV3>();
    if spans_overlap(
        input,
        input_length,
        certificate.cast::<u8>(),
        certificate_bytes,
    ) || spans_overlap(input, input_length, output.cast_const(), capacity)
        || spans_overlap(
            output.cast_const(),
            capacity,
            certificate.cast::<u8>(),
            certificate_bytes,
        )
    {
        return ERROR_INVALID_ARGUMENT;
    }
    set_certificate_error(certificate, ERROR_INVALID_ARGUMENT, component_kind);
    if !matches!(
        component_kind,
        COMPONENT_FACE | COMPONENT_EDGE | COMPONENT_VERTEX
    ) || component_index == 0
        || !dx.is_finite()
        || !dy.is_finite()
        || !dz.is_finite()
        || dx.abs().max(dy.abs()).max(dz.abs()) <= LOCAL_MOVE_ZERO_TRANSLATION
    {
        return ERROR_INVALID_ARGUMENT;
    }

    let policy = move_policy();
    let mut required = 0usize;
    let status = unsafe {
        narwhal_occt_adapter_move_component(
            input,
            input_length,
            component_kind,
            component_index,
            dx,
            dy,
            dz,
            &policy,
            output,
            capacity,
            &mut required,
            certificate,
        )
    };
    if required > MAX_BUFFER_BYTES || required > i64::MAX as usize {
        set_certificate_error(certificate, ERROR_LIMIT_EXCEEDED, component_kind);
        return ERROR_LIMIT_EXCEEDED;
    }
    match status {
        ADAPTER_OK | ADAPTER_BUFFER_TOO_SMALL => required as i64,
        _ => adapter_error(status),
    }
}

#[allow(clippy::too_many_arguments)]
fn session_move_impl(
    handle: u64,
    validation_mode: u32,
    component_kind: u32,
    component_index: u64,
    dx: f64,
    dy: f64,
    dz: f64,
    output: *mut u8,
    capacity: usize,
    certificate: *mut NarwhalLocalMoveCertificateV3,
) -> i64 {
    if !span_metadata_is_valid(certificate.cast_const(), 1) {
        return ERROR_INVALID_ARGUMENT;
    }
    if capacity > MAX_BUFFER_BYTES {
        set_certificate_error(certificate, ERROR_LIMIT_EXCEEDED, component_kind);
        return ERROR_LIMIT_EXCEEDED;
    }
    if !output_metadata_is_valid(output, capacity) {
        set_certificate_error(certificate, ERROR_INVALID_ARGUMENT, component_kind);
        return ERROR_INVALID_ARGUMENT;
    }
    if spans_overlap(
        output.cast_const(),
        capacity,
        certificate.cast::<u8>(),
        size_of::<NarwhalLocalMoveCertificateV3>(),
    ) {
        return ERROR_INVALID_ARGUMENT;
    }
    set_certificate_error(certificate, ERROR_INVALID_ARGUMENT, component_kind);
    if !matches!(validation_mode, VALIDATION_LIVE | VALIDATION_COMMIT)
        || !matches!(
            component_kind,
            COMPONENT_FACE | COMPONENT_EDGE | COMPONENT_VERTEX
        )
        || component_index == 0
        || !dx.is_finite()
        || !dy.is_finite()
        || !dz.is_finite()
        || dx.abs().max(dy.abs()).max(dz.abs()) <= LOCAL_MOVE_ZERO_TRANSLATION
    {
        return ERROR_INVALID_ARGUMENT;
    }

    let entry = match lookup_session(handle) {
        Ok(entry) => entry,
        Err(status) => {
            set_certificate_error(certificate, status, component_kind);
            return status;
        }
    };
    let adapter = match entry.adapter.lock() {
        Ok(adapter) => adapter,
        Err(_) => {
            set_certificate_error(certificate, ERROR_UNEXPECTED_FAILURE, component_kind);
            return ERROR_UNEXPECTED_FAILURE;
        }
    };
    let mut required = 0usize;
    let status = unsafe {
        narwhal_occt_adapter_session_move(
            adapter.0,
            validation_mode,
            component_kind,
            component_index,
            dx,
            dy,
            dz,
            output,
            capacity,
            &mut required,
            certificate,
        )
    };
    if required > MAX_BUFFER_BYTES || required > i64::MAX as usize {
        set_certificate_error(certificate, ERROR_LIMIT_EXCEEDED, component_kind);
        return ERROR_LIMIT_EXCEEDED;
    }
    match status {
        ADAPTER_OK | ADAPTER_BUFFER_TOO_SMALL => required as i64,
        _ => adapter_error(status),
    }
}

/// Move a component from an edit session's immutable original shape with explicit validation.
///
/// # Safety
///
/// Nonzero `capacity` requires that many writable caller-owned bytes at `output`;
/// `certificate` must be writable for one V3 certificate. The spans must not overlap.
#[no_mangle]
pub unsafe extern "C" fn narwhal_geometry_core_edit_session_move_mode(
    handle: u64,
    validation_mode: u32,
    component_kind: u32,
    component_index: u64,
    dx: f64,
    dy: f64,
    dz: f64,
    output: *mut u8,
    capacity: usize,
    certificate: *mut NarwhalLocalMoveCertificateV3,
) -> i64 {
    catch_unwind(AssertUnwindSafe(|| {
        session_move_impl(
            handle,
            validation_mode,
            component_kind,
            component_index,
            dx,
            dy,
            dz,
            output,
            capacity,
            certificate,
        )
    }))
    .unwrap_or_else(|_| {
        set_certificate_error(certificate, ERROR_UNEXPECTED_FAILURE, component_kind);
        ERROR_UNEXPECTED_FAILURE
    })
}

/// Move a component in mandatory COMMIT mode.
///
/// # Safety
///
/// Requirements are identical to [`narwhal_geometry_core_edit_session_move_mode`].
#[no_mangle]
pub unsafe extern "C" fn narwhal_geometry_core_edit_session_move(
    handle: u64,
    component_kind: u32,
    component_index: u64,
    dx: f64,
    dy: f64,
    dz: f64,
    output: *mut u8,
    capacity: usize,
    certificate: *mut NarwhalLocalMoveCertificateV3,
) -> i64 {
    unsafe {
        narwhal_geometry_core_edit_session_move_mode(
            handle,
            VALIDATION_COMMIT,
            component_kind,
            component_index,
            dx,
            dy,
            dz,
            output,
            capacity,
            certificate,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::MutexGuard;
    use std::thread;
    use std::time::{Duration, Instant};

    static SESSION_TEST_LOCK: Mutex<()> = Mutex::new(());

    fn session_test_lock() -> MutexGuard<'static, ()> {
        SESSION_TEST_LOCK
            .lock()
            .expect("session test lock is not poisoned")
    }

    unsafe fn exact_bytes_from_two_pass(mut call: impl FnMut(*mut u8, usize) -> i64) -> Vec<u8> {
        let required = call(ptr::null_mut(), 0);
        assert!(required > 0, "size query failed with {required}");
        let mut bytes = vec![0_u8; required as usize];
        assert_eq!(call(bytes.as_mut_ptr(), bytes.len()), required);
        bytes
    }

    unsafe fn make_box(dx: f64, dy: f64, dz: f64) -> Vec<u8> {
        unsafe {
            exact_bytes_from_two_pass(|output, capacity| {
                narwhal_geometry_core_make_box_brep(dx, dy, dz, output, capacity)
            })
        }
    }

    unsafe fn make_fixture() -> Vec<u8> {
        unsafe {
            exact_bytes_from_two_pass(|output, capacity| {
                let mut required = 0usize;
                let status =
                    narwhal_occt_adapter_make_local_move_fixture(output, capacity, &mut required);
                match status {
                    ADAPTER_OK | ADAPTER_BUFFER_TOO_SMALL => required as i64,
                    _ => adapter_error(status),
                }
            })
        }
    }

    unsafe fn make_surface_family_fixture(family: u32) -> (Vec<u8>, u64) {
        let mut selected_face = 0_u64;
        let bytes = unsafe {
            exact_bytes_from_two_pass(|output, capacity| {
                let mut required = 0usize;
                let status = narwhal_occt_adapter_make_surface_family_fixture(
                    family,
                    output,
                    capacity,
                    &mut required,
                    &mut selected_face,
                );
                match status {
                    ADAPTER_OK | ADAPTER_BUFFER_TOO_SMALL => required as i64,
                    _ => adapter_error(status),
                }
            })
        };
        (bytes, selected_face)
    }

    unsafe fn inspect(bytes: &[u8]) -> NarwhalBrepInspectionV1 {
        let mut result = NarwhalBrepInspectionV1::default();
        assert_eq!(
            unsafe { narwhal_geometry_core_inspect_brep(bytes.as_ptr(), bytes.len(), &mut result) },
            0
        );
        result
    }

    unsafe fn move_once(
        input: &[u8],
        kind: u32,
        index: u64,
        vector: [f64; 3],
    ) -> (Vec<u8>, NarwhalLocalMoveCertificateV3) {
        let mut query_certificate = NarwhalLocalMoveCertificateV3::default();
        let required = unsafe {
            narwhal_geometry_core_move_component_brep(
                input.as_ptr(),
                input.len(),
                kind,
                index,
                vector[0],
                vector[1],
                vector[2],
                ptr::null_mut(),
                0,
                &mut query_certificate,
            )
        };
        assert!(
            required > 0,
            "move query failed: {required}, {query_certificate:?}"
        );
        let mut output = vec![0_u8; required as usize];
        let mut certificate = NarwhalLocalMoveCertificateV3::default();
        assert_eq!(
            unsafe {
                narwhal_geometry_core_move_component_brep(
                    input.as_ptr(),
                    input.len(),
                    kind,
                    index,
                    vector[0],
                    vector[1],
                    vector[2],
                    output.as_mut_ptr(),
                    output.len(),
                    &mut certificate,
                )
            },
            required
        );
        assert_eq!(query_certificate, certificate);
        (output, certificate)
    }

    unsafe fn import_session(input: &[u8]) -> u64 {
        let mut handle = 0_u64;
        assert_eq!(
            unsafe {
                narwhal_geometry_core_edit_session_import(input.as_ptr(), input.len(), &mut handle)
            },
            0
        );
        assert_ne!(handle, 0);
        handle
    }

    unsafe fn session_move_once(
        handle: u64,
        kind: u32,
        index: u64,
        vector: [f64; 3],
    ) -> (Vec<u8>, NarwhalLocalMoveCertificateV3) {
        let mut query_certificate = NarwhalLocalMoveCertificateV3::default();
        let required = unsafe {
            narwhal_geometry_core_edit_session_move(
                handle,
                kind,
                index,
                vector[0],
                vector[1],
                vector[2],
                ptr::null_mut(),
                0,
                &mut query_certificate,
            )
        };
        assert!(
            required > 0,
            "session query failed: {required}, {query_certificate:?}"
        );
        let mut output = vec![0_u8; required as usize];
        let mut certificate = NarwhalLocalMoveCertificateV3::default();
        assert_eq!(
            unsafe {
                narwhal_geometry_core_edit_session_move(
                    handle,
                    kind,
                    index,
                    vector[0],
                    vector[1],
                    vector[2],
                    output.as_mut_ptr(),
                    output.len(),
                    &mut certificate,
                )
            },
            required
        );
        assert_eq!(certificate, query_certificate);
        (output, certificate)
    }

    fn assert_certificate(
        certificate: &NarwhalLocalMoveCertificateV3,
        baseline: &NarwhalBrepInspectionV1,
        kind: u32,
    ) {
        assert_eq!(
            certificate.struct_size as usize,
            size_of::<NarwhalLocalMoveCertificateV3>()
        );
        assert_eq!(certificate.abi_version, CERTIFICATE_VERSION);
        assert_eq!(certificate.status, 0);
        assert_eq!(certificate.refusal_reason, REFUSAL_NONE);
        assert_eq!(certificate.valid, 1);
        assert_eq!(certificate.airtight, 1);
        assert_eq!(certificate.self_intersection_free, 1);
        assert_eq!(certificate.topology_counts_preserved, 1);
        assert_eq!(certificate.moved_vertex_mapping_verified, 1);
        assert_eq!(certificate.mapped_incidence_preserved, 1);
        assert_eq!(certificate.unchanged_geometry_preserved, 1);
        assert_eq!(certificate.selected_match_count, 1);
        assert_eq!(
            certificate.quality_scope_bits,
            certificate.quality_checked_bits
        );
        assert_ne!(
            certificate.quality_checked_bits & QUALITY_RECONSTRUCTED_FACES,
            0
        );
        assert!(certificate.reconstructed_face_count > 0);
        assert_ne!(
            certificate.quality_checked_bits & QUALITY_MOVED_VERTEX_MAPPING,
            0
        );
        assert_ne!(
            certificate.quality_checked_bits & QUALITY_MAPPED_INCIDENCE,
            0
        );
        assert_ne!(
            certificate.quality_checked_bits & QUALITY_UNCHANGED_GEOMETRY,
            0
        );
        assert_eq!(certificate.component_kind, kind);
        assert_eq!(certificate.face_count, baseline.face_count);
        assert_eq!(certificate.edge_count, baseline.edge_count);
        assert_eq!(certificate.vertex_count, baseline.vertex_count);
        assert!(certificate.volume > 0.0);
        assert!(certificate.selected_translation_error <= certificate.comparison_tolerance);
        if certificate.quality_checked_bits & QUALITY_BSPLINE_BOUNDARY != 0 {
            assert!(certificate.reconstructed_bspline_face_count > 0);
            assert!(certificate.max_boundary_error <= certificate.comparison_tolerance);
        } else {
            assert_eq!(certificate.reconstructed_bspline_face_count, 0);
            assert_eq!(certificate.max_boundary_error, 0.0);
        }
        if certificate.quality_checked_bits & QUALITY_INTERNAL_KNOT != 0 {
            assert!(certificate.internal_knot_face_count > 0);
            assert!(certificate.max_internal_normal_jump <= LOCAL_MOVE_MAX_NORMAL_JUMP);
        } else {
            assert_eq!(certificate.internal_knot_face_count, 0);
            assert_eq!(certificate.max_internal_normal_jump, 0.0);
        }
        assert!(certificate.comparison_tolerance > 0.0);
        assert!(certificate.comparison_tolerance < certificate.build_tolerance);
        assert!(certificate.build_tolerance <= certificate.sewing_tolerance);
        assert_eq!(
            certificate.plane_faces
                + certificate.cylinder_faces
                + certificate.cone_faces
                + certificate.sphere_faces
                + certificate.torus_faces
                + certificate.bezier_faces
                + certificate.bspline_faces
                + certificate.revolution_faces
                + certificate.extrusion_faces
                + certificate.offset_faces
                + certificate.other_faces,
            certificate.face_count
        );
    }

    #[test]
    fn reports_version_capabilities_and_layouts() {
        assert_eq!(narwhal_geometry_core_abi_version(), 4);
        assert_eq!(narwhal_geometry_core_capabilities(), 0b11_1111);
        assert_eq!(size_of::<NarwhalBrepInspectionV1>(), 136);
        assert_eq!(size_of::<NarwhalLocalMoveCertificateV3>(), 248);
    }

    #[test]
    fn direct_native_box_is_exact_valid_and_airtight() {
        let bytes = unsafe { make_box(2.0, 3.0, 4.0) };
        let inspection = unsafe { inspect(&bytes) };
        assert_eq!(inspection.abi_version, 1);
        assert_eq!((inspection.valid, inspection.airtight), (1, 1));
        assert_eq!(
            (
                inspection.face_count,
                inspection.edge_count,
                inspection.vertex_count
            ),
            (6, 12, 8)
        );
        assert!((inspection.volume - 24.0).abs() <= 1.0e-12);
        assert_eq!(inspection.plane_faces, 6);
    }

    #[test]
    fn box_rejects_invalid_dimensions_pointers_and_capacities() {
        for dimensions in [
            (0.0, 2.0, 3.0),
            (-1.0, 2.0, 3.0),
            (f64::NAN, 2.0, 3.0),
            (1.0, f64::INFINITY, 3.0),
        ] {
            assert_eq!(
                unsafe {
                    narwhal_geometry_core_make_box_brep(
                        dimensions.0,
                        dimensions.1,
                        dimensions.2,
                        ptr::null_mut(),
                        0,
                    )
                },
                ERROR_INVALID_ARGUMENT
            );
        }
        assert_eq!(
            unsafe { narwhal_geometry_core_make_box_brep(1.0, 2.0, 3.0, ptr::null_mut(), 1) },
            ERROR_INVALID_ARGUMENT
        );
        assert_eq!(
            unsafe {
                narwhal_geometry_core_make_box_brep(
                    1.0,
                    2.0,
                    3.0,
                    ptr::NonNull::<u8>::dangling().as_ptr(),
                    MAX_BUFFER_BYTES + 1,
                )
            },
            ERROR_LIMIT_EXCEEDED
        );
    }

    #[test]
    fn box_two_pass_does_not_touch_an_undersized_buffer() {
        let required =
            unsafe { narwhal_geometry_core_make_box_brep(2.0, 3.0, 4.0, ptr::null_mut(), 0) };
        assert!(required > 1);
        let mut output = vec![0xA5_u8; required as usize - 1];
        assert_eq!(
            unsafe {
                narwhal_geometry_core_make_box_brep(
                    2.0,
                    3.0,
                    4.0,
                    output.as_mut_ptr(),
                    output.len(),
                )
            },
            required
        );
        assert!(output.iter().all(|byte| *byte == 0xA5));
    }

    #[test]
    fn inspection_rejects_bad_spans_and_clears_output() {
        let mut output = NarwhalBrepInspectionV1 {
            valid: 1,
            airtight: 1,
            volume: 99.0,
            ..NarwhalBrepInspectionV1::default()
        };
        assert_eq!(
            unsafe { narwhal_geometry_core_inspect_brep(ptr::null(), 1, &mut output) },
            ERROR_INVALID_ARGUMENT as i32
        );
        assert_eq!(output, NarwhalBrepInspectionV1::default());

        let byte = 0_u8;
        assert_eq!(
            unsafe { narwhal_geometry_core_inspect_brep(&byte, 0, &mut output) },
            ERROR_INVALID_ARGUMENT as i32
        );
        assert_eq!(
            unsafe {
                narwhal_geometry_core_inspect_brep(
                    ptr::NonNull::<u8>::dangling().as_ptr(),
                    MAX_BUFFER_BYTES + 1,
                    &mut output,
                )
            },
            ERROR_LIMIT_EXCEEDED as i32
        );
        assert_eq!(
            unsafe { narwhal_geometry_core_inspect_brep(&byte, 1, ptr::null_mut()) },
            ERROR_INVALID_ARGUMENT as i32
        );

        let mut unaligned = [0_u8; size_of::<NarwhalBrepInspectionV1>() + 1];
        let base = unaligned.as_mut_ptr() as usize;
        let offset = usize::from(base.is_multiple_of(align_of::<NarwhalBrepInspectionV1>()));
        let bad_output =
            unsafe { unaligned.as_mut_ptr().add(offset) }.cast::<NarwhalBrepInspectionV1>();
        assert_ne!(
            bad_output as usize % align_of::<NarwhalBrepInspectionV1>(),
            0
        );
        assert_eq!(
            unsafe { narwhal_geometry_core_inspect_brep(&byte, 1, bad_output) },
            ERROR_INVALID_ARGUMENT as i32
        );

        output.valid = 1;
        output.airtight = 1;
        output.volume = 99.0;
        let malformed = b"not an OCCT BinTools BRep";
        assert_eq!(
            unsafe {
                narwhal_geometry_core_inspect_brep(malformed.as_ptr(), malformed.len(), &mut output)
            },
            ERROR_MALFORMED_BREP as i32
        );
        assert_eq!(output, NarwhalBrepInspectionV1::default());
    }

    #[test]
    fn trailing_bintools_bytes_and_overlapping_spans_are_rejected() {
        let mut fixture = unsafe { make_fixture() };
        fixture.extend_from_slice(b"trailing");
        let mut inspection = NarwhalBrepInspectionV1::default();
        assert_eq!(
            unsafe {
                narwhal_geometry_core_inspect_brep(fixture.as_ptr(), fixture.len(), &mut inspection)
            },
            ERROR_MALFORMED_BREP as i32
        );
        let mut certificate = NarwhalLocalMoveCertificateV3::default();
        assert_eq!(
            unsafe {
                narwhal_geometry_core_move_component_brep(
                    fixture.as_ptr(),
                    fixture.len(),
                    COMPONENT_FACE,
                    2,
                    0.5,
                    0.0,
                    0.0,
                    ptr::null_mut(),
                    0,
                    &mut certificate,
                )
            },
            ERROR_MALFORMED_BREP
        );

        let mut alias = vec![NarwhalBrepInspectionV1::default(); 2];
        assert_eq!(
            unsafe {
                narwhal_geometry_core_inspect_brep(
                    alias.as_ptr().cast::<u8>(),
                    size_of::<NarwhalBrepInspectionV1>(),
                    alias.as_mut_ptr(),
                )
            },
            ERROR_INVALID_ARGUMENT as i32
        );
        let mut exact_fixture = unsafe { make_fixture() };
        assert_eq!(
            unsafe {
                narwhal_geometry_core_move_component_brep(
                    exact_fixture.as_ptr(),
                    exact_fixture.len(),
                    COMPONENT_FACE,
                    2,
                    0.5,
                    0.0,
                    0.0,
                    exact_fixture.as_mut_ptr(),
                    exact_fixture.len(),
                    &mut certificate,
                )
            },
            ERROR_INVALID_ARGUMENT
        );
    }

    #[test]
    fn local_move_fixture_has_expected_exact_topology() {
        let fixture = unsafe { make_fixture() };
        let inspection = unsafe { inspect(&fixture) };
        assert_eq!((inspection.valid, inspection.airtight), (1, 1));
        assert_eq!(inspection.face_count, 10);
        assert_eq!(
            (
                inspection.plane_faces,
                inspection.cylinder_faces,
                inspection.sphere_faces
            ),
            (6, 3, 1)
        );
        assert_eq!(inspection.bspline_faces, 0);
    }

    #[test]
    fn untouched_cone_and_torus_survive_local_reconstruction() {
        for family in [1_u32, 2_u32] {
            let (fixture, selected_face) = unsafe { make_surface_family_fixture(family) };
            let before = unsafe { inspect(&fixture) };
            let before_family_faces = if family == 1 {
                before.cone_faces
            } else {
                before.torus_faces
            };
            assert!(before_family_faces > 0);
            let (moved, certificate) =
                unsafe { move_once(&fixture, COMPONENT_FACE, selected_face, [0.0, 0.0, -0.5]) };
            let after = unsafe { inspect(&moved) };
            let after_family_faces = if family == 1 {
                after.cone_faces
            } else {
                after.torus_faces
            };
            assert_eq!(after_family_faces, before_family_faces);
            assert_eq!(certificate.unchanged_geometry_preserved, 1);
            assert_eq!(certificate.mapped_incidence_preserved, 1);
        }
    }

    #[test]
    fn current_local_move_matrix_is_exact_and_deterministic() {
        let fixture = unsafe { make_fixture() };
        let baseline = unsafe { inspect(&fixture) };
        let cases = [
            (COMPONENT_FACE, 2_u64, [true, false, true]),
            (COMPONENT_EDGE, 20, [true, true, true]),
            (COMPONENT_EDGE, 5, [true, true, true]),
            (COMPONENT_VERTEX, 1, [true, true, true]),
            (COMPONENT_VERTEX, 11, [true, true, true]),
        ];
        for (kind, index, axes) in cases {
            for axis in 0..3 {
                for sign in [1.0, -1.0] {
                    let mut vector = [0.0; 3];
                    vector[axis] = sign * 0.5;
                    if axes[axis] {
                        let (bytes, certificate) =
                            unsafe { move_once(&fixture, kind, index, vector) };
                        assert_certificate(&certificate, &baseline, kind);
                        let inspection = unsafe { inspect(&bytes) };
                        assert_eq!((inspection.valid, inspection.airtight), (1, 1));
                        assert_eq!(inspection.face_count, baseline.face_count);
                        assert_eq!(inspection.edge_count, baseline.edge_count);
                        assert_eq!(inspection.vertex_count, baseline.vertex_count);
                        if matches!((kind, index), (COMPONENT_EDGE, 20) | (COMPONENT_VERTEX, 11))
                            && matches!(axis, 0 | 2)
                        {
                            assert_ne!(
                                certificate.quality_checked_bits & QUALITY_BSPLINE_BOUNDARY,
                                0
                            );
                            assert_ne!(certificate.quality_checked_bits & QUALITY_INTERNAL_KNOT, 0);
                        }
                        if sign > 0.0 && axis == 0 {
                            let (repeat, repeat_certificate) =
                                unsafe { move_once(&fixture, kind, index, vector) };
                            assert_eq!(repeat, bytes);
                            assert_eq!(repeat_certificate, certificate);
                        }
                    } else {
                        let mut certificate = NarwhalLocalMoveCertificateV3::default();
                        let status = unsafe {
                            narwhal_geometry_core_move_component_brep(
                                fixture.as_ptr(),
                                fixture.len(),
                                kind,
                                index,
                                vector[0],
                                vector[1],
                                vector[2],
                                ptr::null_mut(),
                                0,
                                &mut certificate,
                            )
                        };
                        assert_eq!(status, ERROR_SMOOTHNESS_CREASE);
                        assert_eq!(certificate.status, ERROR_SMOOTHNESS_CREASE as i32);
                        assert_eq!(certificate.refusal_reason, REFUSAL_SMOOTHNESS);
                        assert_ne!(
                            certificate.quality_checked_bits & QUALITY_BSPLINE_BOUNDARY,
                            0
                        );
                        assert_ne!(certificate.quality_checked_bits & QUALITY_INTERNAL_KNOT, 0);
                        assert!(certificate.max_boundary_error <= certificate.comparison_tolerance);
                        assert!(certificate.max_internal_normal_jump > LOCAL_MOVE_MAX_NORMAL_JUMP);
                    }
                }
            }
        }
    }

    #[test]
    fn local_move_rejects_bad_arguments_and_malformed_input() {
        let fixture = unsafe { make_fixture() };
        let mut certificate = NarwhalLocalMoveCertificateV3::default();
        for (kind, index, vector) in [
            (0, 1, [0.5, 0.0, 0.0]),
            (4, 1, [0.5, 0.0, 0.0]),
            (COMPONENT_FACE, 0, [0.5, 0.0, 0.0]),
            (COMPONENT_FACE, 1, [0.0, 0.0, 0.0]),
            (COMPONENT_FACE, 1, [f64::NAN, 0.0, 0.0]),
        ] {
            assert_eq!(
                unsafe {
                    narwhal_geometry_core_move_component_brep(
                        fixture.as_ptr(),
                        fixture.len(),
                        kind,
                        index,
                        vector[0],
                        vector[1],
                        vector[2],
                        ptr::null_mut(),
                        0,
                        &mut certificate,
                    )
                },
                ERROR_INVALID_ARGUMENT
            );
        }
        assert_eq!(
            unsafe {
                narwhal_geometry_core_move_component_brep(
                    ptr::null(),
                    1,
                    COMPONENT_FACE,
                    1,
                    0.5,
                    0.0,
                    0.0,
                    ptr::null_mut(),
                    0,
                    &mut certificate,
                )
            },
            ERROR_INVALID_ARGUMENT
        );
        assert_eq!(
            unsafe {
                narwhal_geometry_core_move_component_brep(
                    fixture.as_ptr(),
                    fixture.len(),
                    COMPONENT_FACE,
                    u64::MAX,
                    0.5,
                    0.0,
                    0.0,
                    ptr::null_mut(),
                    0,
                    &mut certificate,
                )
            },
            ERROR_POLICY_REFUSED
        );
        assert_eq!(certificate.refusal_reason, REFUSAL_INDEX_OUTSIDE);

        let malformed = b"not a BinTools BRep";
        assert_eq!(
            unsafe {
                narwhal_geometry_core_move_component_brep(
                    malformed.as_ptr(),
                    malformed.len(),
                    COMPONENT_FACE,
                    1,
                    0.5,
                    0.0,
                    0.0,
                    ptr::null_mut(),
                    0,
                    &mut certificate,
                )
            },
            ERROR_MALFORMED_BREP
        );
        assert_eq!(certificate.status, ERROR_MALFORMED_BREP as i32);
        assert_eq!(
            unsafe {
                narwhal_geometry_core_move_component_brep(
                    fixture.as_ptr(),
                    fixture.len(),
                    COMPONENT_FACE,
                    2,
                    0.5,
                    0.0,
                    0.0,
                    ptr::null_mut(),
                    MAX_BUFFER_BYTES + 1,
                    &mut certificate,
                )
            },
            ERROR_LIMIT_EXCEEDED
        );
        assert_eq!(
            unsafe {
                narwhal_geometry_core_move_component_brep(
                    fixture.as_ptr(),
                    fixture.len(),
                    COMPONENT_FACE,
                    2,
                    0.5,
                    0.0,
                    0.0,
                    ptr::null_mut(),
                    1,
                    &mut certificate,
                )
            },
            ERROR_INVALID_ARGUMENT
        );
    }

    #[test]
    fn too_small_local_move_output_is_untouched() {
        let fixture = unsafe { make_fixture() };
        let mut certificate = NarwhalLocalMoveCertificateV3::default();
        let required = unsafe {
            narwhal_geometry_core_move_component_brep(
                fixture.as_ptr(),
                fixture.len(),
                COMPONENT_FACE,
                2,
                0.5,
                0.0,
                0.0,
                ptr::null_mut(),
                0,
                &mut certificate,
            )
        };
        assert!(required > 1);
        let mut output = vec![0xA5; required as usize - 1];
        assert_eq!(
            unsafe {
                narwhal_geometry_core_move_component_brep(
                    fixture.as_ptr(),
                    fixture.len(),
                    COMPONENT_FACE,
                    2,
                    0.5,
                    0.0,
                    0.0,
                    output.as_mut_ptr(),
                    output.len(),
                    &mut certificate,
                )
            },
            required
        );
        assert!(output.iter().all(|byte| *byte == 0xA5));
    }

    #[test]
    fn edit_session_matches_stateless_and_uses_immutable_original() {
        let _guard = session_test_lock();
        let fixture = unsafe { make_fixture() };
        let handle = unsafe { import_session(&fixture) };
        for (kind, index, vector) in [
            (COMPONENT_FACE, 2, [0.5, 0.0, 0.0]),
            (COMPONENT_EDGE, 20, [0.0, -0.5, 0.0]),
            (COMPONENT_VERTEX, 1, [0.0, 0.0, 0.5]),
        ] {
            let (stateless_bytes, stateless_certificate) =
                unsafe { move_once(&fixture, kind, index, vector) };
            let (session_bytes, session_certificate) =
                unsafe { session_move_once(handle, kind, index, vector) };
            let stateless_inspection = unsafe { inspect(&stateless_bytes) };
            let session_inspection = unsafe { inspect(&session_bytes) };
            assert_eq!(
                stateless_inspection.face_count,
                session_inspection.face_count
            );
            assert_eq!(
                stateless_inspection.edge_count,
                session_inspection.edge_count
            );
            assert_eq!(
                stateless_inspection.vertex_count,
                session_inspection.vertex_count
            );
            assert_eq!(
                stateless_inspection.plane_faces,
                session_inspection.plane_faces
            );
            assert_eq!(
                stateless_inspection.cylinder_faces,
                session_inspection.cylinder_faces
            );
            assert_eq!(
                stateless_inspection.bspline_faces,
                session_inspection.bspline_faces
            );
            assert!((stateless_inspection.volume - session_inspection.volume).abs() <= 1.0e-8);
            assert_eq!(stateless_certificate.status, session_certificate.status);
            assert_eq!(
                stateless_certificate.quality_checked_bits,
                session_certificate.quality_checked_bits
            );
            let repeat = unsafe { session_move_once(handle, kind, index, vector) };
            assert_eq!(repeat.0, session_bytes);
            assert_eq!(repeat.1, session_certificate);
        }
        assert_eq!(narwhal_geometry_core_edit_session_release(handle), 0);
        assert_eq!(lookup_session(handle).err(), Some(ERROR_INVALID_HANDLE));
    }

    #[test]
    fn explicit_live_and_mandatory_commit_modes_remain_distinct() {
        let _guard = session_test_lock();
        let fixture = unsafe { make_fixture() };
        let handle = unsafe { import_session(&fixture) };
        let mut output = vec![0_u8; 1024 * 1024];
        let mut live = NarwhalLocalMoveCertificateV3::default();
        let live_required = unsafe {
            narwhal_geometry_core_edit_session_move_mode(
                handle,
                VALIDATION_LIVE,
                COMPONENT_VERTEX,
                1,
                0.5,
                0.0,
                0.0,
                output.as_mut_ptr(),
                output.len(),
                &mut live,
            )
        };
        assert!(live_required > 0);
        assert_eq!(live.valid, 1);
        assert_eq!(live.airtight, 1);
        assert_eq!(live.self_intersection_free, 1);
        assert_eq!(live.selected_match_count, 1);
        assert_eq!(live.quality_checked_bits & QUALITY_MAPPED_INCIDENCE, 0);

        let mut commit = NarwhalLocalMoveCertificateV3::default();
        let commit_required = unsafe {
            narwhal_geometry_core_edit_session_move(
                handle,
                COMPONENT_VERTEX,
                1,
                0.55,
                0.0,
                0.0,
                output.as_mut_ptr(),
                output.len(),
                &mut commit,
            )
        };
        assert!(commit_required > 0);
        assert_ne!(commit.quality_checked_bits & QUALITY_MAPPED_INCIDENCE, 0);
        assert_ne!(commit.quality_checked_bits & QUALITY_UNCHANGED_GEOMETRY, 0);
        assert_eq!(commit.mapped_incidence_preserved, 1);
        assert_eq!(commit.unchanged_geometry_preserved, 1);
        assert_eq!(narwhal_geometry_core_edit_session_release(handle), 0);
    }

    #[test]
    fn edit_session_rejects_stale_double_release_and_random_handles() {
        let _guard = session_test_lock();
        let fixture = unsafe { make_fixture() };
        let baseline_live = unsafe { narwhal_occt_adapter_session_live_count() };
        let stale = unsafe { import_session(&fixture) };
        assert_eq!(
            unsafe { narwhal_occt_adapter_session_live_count() },
            baseline_live + 1
        );
        assert_eq!(narwhal_geometry_core_edit_session_release(stale), 0);
        assert_eq!(
            narwhal_geometry_core_edit_session_release(stale),
            ERROR_INVALID_HANDLE as i32
        );

        let current = unsafe { import_session(&fixture) };
        assert_eq!(stale as u32, current as u32);
        assert_ne!((stale >> 32) as u32, (current >> 32) as u32);
        let mut certificate = NarwhalLocalMoveCertificateV3::default();
        assert_eq!(
            unsafe {
                narwhal_geometry_core_edit_session_move(
                    stale,
                    COMPONENT_FACE,
                    2,
                    0.5,
                    0.0,
                    0.0,
                    ptr::null_mut(),
                    0,
                    &mut certificate,
                )
            },
            ERROR_INVALID_HANDLE
        );
        for random in [0, 1, u64::MAX, 0x1234_5678_9abc_def0] {
            assert_eq!(
                unsafe {
                    narwhal_geometry_core_edit_session_move(
                        random,
                        COMPONENT_FACE,
                        2,
                        0.5,
                        0.0,
                        0.0,
                        ptr::null_mut(),
                        0,
                        &mut certificate,
                    )
                },
                ERROR_INVALID_HANDLE
            );
        }
        assert_eq!(narwhal_geometry_core_edit_session_release(current), 0);
        assert_eq!(
            unsafe { narwhal_occt_adapter_session_live_count() },
            baseline_live
        );
    }

    #[test]
    fn edit_session_is_concurrency_safe_and_releases() {
        let _guard = session_test_lock();
        let fixture = unsafe { make_fixture() };
        let handle = unsafe { import_session(&fixture) };
        let workers = (0..4)
            .map(|worker| {
                thread::spawn(move || {
                    for iteration in 0..3 {
                        let axis = (worker + iteration) % 3;
                        let mut vector = [0.0; 3];
                        vector[axis] = if iteration % 2 == 0 { 0.5 } else { -0.5 };
                        let (bytes, certificate) =
                            unsafe { session_move_once(handle, COMPONENT_VERTEX, 1, vector) };
                        assert!(!bytes.is_empty());
                        assert_eq!(certificate.status, 0);
                    }
                })
            })
            .collect::<Vec<_>>();
        for worker in workers {
            worker.join().expect("session worker panicked");
        }
        assert_eq!(narwhal_geometry_core_edit_session_release(handle), 0);
        assert_eq!(lookup_session(handle).err(), Some(ERROR_INVALID_HANDLE));
    }

    #[test]
    fn poisoned_session_lock_fails_closed() {
        let _guard = session_test_lock();
        let fixture = unsafe { make_fixture() };
        let handle = unsafe { import_session(&fixture) };
        let entry = lookup_session(handle).expect("live session");
        let poisoner = entry.clone();
        let _ = thread::spawn(move || {
            let _adapter = poisoner.adapter.lock().expect("initial lock");
            panic!("intentional session-lock poison");
        })
        .join();
        let mut certificate = NarwhalLocalMoveCertificateV3::default();
        assert_eq!(
            unsafe {
                narwhal_geometry_core_edit_session_move(
                    handle,
                    COMPONENT_VERTEX,
                    1,
                    0.5,
                    0.0,
                    0.0,
                    ptr::null_mut(),
                    0,
                    &mut certificate,
                )
            },
            ERROR_UNEXPECTED_FAILURE
        );
        drop(entry);
        assert_eq!(narwhal_geometry_core_edit_session_release(handle), 0);
    }

    #[test]
    fn poisoned_registry_fails_closed_without_leaking_native_session() {
        let _guard = session_test_lock();
        let fixture = unsafe { make_fixture() };
        let baseline_live = unsafe { narwhal_occt_adapter_session_live_count() };
        let _ = thread::spawn(|| {
            let _registry = session_registry().lock().expect("initial registry lock");
            panic!("intentional registry poison");
        })
        .join();
        let mut handle = u64::MAX;
        assert_eq!(
            unsafe {
                narwhal_geometry_core_edit_session_import(
                    fixture.as_ptr(),
                    fixture.len(),
                    &mut handle,
                )
            },
            ERROR_UNEXPECTED_FAILURE as i32
        );
        assert_eq!(handle, 0);
        assert_eq!(
            unsafe { narwhal_occt_adapter_session_live_count() },
            baseline_live
        );
        session_registry().clear_poison();
    }

    fn benchmark_case(fixture: &[u8], kind: u32, index: u64) -> Duration {
        let mut output = vec![0_u8; MAX_BUFFER_BYTES.min(1024 * 1024)];
        let mut call = || {
            let mut certificate = NarwhalLocalMoveCertificateV3::default();
            let required = unsafe {
                narwhal_geometry_core_move_component_brep(
                    fixture.as_ptr(),
                    fixture.len(),
                    kind,
                    index,
                    0.5,
                    0.0,
                    0.0,
                    output.as_mut_ptr(),
                    output.len(),
                    &mut certificate,
                )
            };
            assert!(required > 0 && required as usize <= output.len());
            assert_eq!(certificate.status, 0);
        };
        call();
        let started = Instant::now();
        for _ in 0..5 {
            call();
        }
        started.elapsed() / 5
    }

    fn benchmark_session_case(handle: u64, kind: u32, index: u64) -> Duration {
        let mut output = vec![0_u8; MAX_BUFFER_BYTES.min(1024 * 1024)];
        let mut call = || {
            let mut certificate = NarwhalLocalMoveCertificateV3::default();
            let required = unsafe {
                narwhal_geometry_core_edit_session_move(
                    handle,
                    kind,
                    index,
                    0.5,
                    0.0,
                    0.0,
                    output.as_mut_ptr(),
                    output.len(),
                    &mut certificate,
                )
            };
            assert!(required > 0 && required as usize <= output.len());
            assert_eq!(certificate.status, 0);
        };
        call();
        let started = Instant::now();
        for _ in 0..5 {
            call();
        }
        started.elapsed() / 5
    }

    fn benchmark_distinct_session_case(
        handle: u64,
        mode: u32,
        kind: u32,
        index: u64,
    ) -> (Duration, Duration) {
        let mut output = vec![0_u8; MAX_BUFFER_BYTES.min(1024 * 1024)];
        let mut invoke = |distance: f64| {
            let mut certificate = NarwhalLocalMoveCertificateV3::default();
            let started = Instant::now();
            let required = unsafe {
                narwhal_geometry_core_edit_session_move_mode(
                    handle,
                    mode,
                    kind,
                    index,
                    distance,
                    0.0,
                    0.0,
                    output.as_mut_ptr(),
                    output.len(),
                    &mut certificate,
                )
            };
            let elapsed = started.elapsed();
            println!(
                "distinct sample mode={mode} kind={kind} index={index} distance={distance:.2}: {elapsed:?}"
            );
            assert!(required > 0 && required as usize <= output.len());
            assert_eq!(certificate.status, 0);
            assert_eq!(certificate.topology_counts_preserved, 1);
            assert_eq!(certificate.valid, 1);
            assert_eq!(certificate.airtight, 1);
            assert_eq!(certificate.self_intersection_free, 1);
            assert_eq!(certificate.selected_match_count, 1);
            if mode == VALIDATION_COMMIT {
                assert_ne!(
                    certificate.quality_checked_bits & QUALITY_MAPPED_INCIDENCE,
                    0
                );
                assert_eq!(certificate.mapped_incidence_preserved, 1);
                assert_eq!(certificate.unchanged_geometry_preserved, 1);
            } else {
                assert_eq!(
                    certificate.quality_checked_bits & QUALITY_MAPPED_INCIDENCE,
                    0
                );
                assert_eq!(certificate.mapped_incidence_preserved, 0);
                assert_eq!(certificate.unchanged_geometry_preserved, 0);
            }
            elapsed
        };
        let _ = invoke(0.45);
        let mut samples = [0.70, 0.65, 0.60, 0.55, 0.50].map(&mut invoke);
        samples.sort();
        let mean = samples.into_iter().sum::<Duration>() / 5;
        (samples[2], mean)
    }

    #[test]
    #[ignore = "direct native warm-call benchmark"]
    fn benchmark_local_move_direct_abi() {
        let _guard = session_test_lock();
        let fixture = unsafe { make_fixture() };
        let handle = unsafe { import_session(&fixture) };
        for (label, kind, index) in [
            ("face2 +X", COMPONENT_FACE, 2),
            ("edge20 +X", COMPONENT_EDGE, 20),
            ("vertex1 +X", COMPONENT_VERTEX, 1),
        ] {
            let repeat = benchmark_session_case(handle, kind, index);
            let live = benchmark_distinct_session_case(handle, VALIDATION_LIVE, kind, index);
            let commit = benchmark_distinct_session_case(handle, VALIDATION_COMMIT, kind, index);
            println!(
                "{label}: stateless {:?}; exact-repeat {:?}; distinct LIVE median {:?} mean {:?}; distinct COMMIT median {:?} mean {:?}",
                benchmark_case(&fixture, kind, index),
                repeat,
                live.0,
                live.1,
                commit.0,
                commit.1,
            );
        }
        assert_eq!(narwhal_geometry_core_edit_session_release(handle), 0);
    }
}
