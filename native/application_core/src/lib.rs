//! Persistent, process-local document-metadata authority. See README.md for ABI v1.
mod history;
mod load_jobs;
mod object_registry;
pub mod observation;
mod ordinal_map;
mod save_jobs;
mod session;
mod table;
mod wire;

use serde::Serialize;
use session::{Registry, Reply, Request};
use std::{
    panic::{AssertUnwindSafe, catch_unwind},
    sync::{Mutex, OnceLock},
};

pub const MIN_OUTPUT_CAPACITY: usize = 4096;
pub const MAX_REQUEST_BYTES: usize = 128 * 1024 * 1024;
pub const MAX_OUTPUT_CAPACITY: usize = 512 * 1024 * 1024;
static REGISTRY: OnceLock<Mutex<Registry>> = OnceLock::new();

#[derive(Serialize)]
struct Success {
    status: &'static str,
    #[serde(flatten)]
    reply: Reply,
}
fn error(code: &str) -> Vec<u8> {
    // Only fixed internal codes reach here; never echo untrusted text or metadata.
    format!("{{\"status\":\"error\",\"code\":\"{code}\"}}").into_bytes()
}
fn contain(f: impl FnOnce() -> Vec<u8>) -> Vec<u8> {
    catch_unwind(AssertUnwindSafe(f)).unwrap_or_else(|_| error("panic"))
}
fn dispatch(input: &[u8], registry: &Mutex<Registry>) -> Vec<u8> {
    let input = match std::str::from_utf8(input) {
        Ok(s) => s,
        Err(_) => return error("invalid_utf8"),
    };
    if let Err(code) = wire::preflight(input) {
        return error(code);
    }
    let request: Request = match serde_json::from_str(input) {
        Ok(r) => r,
        Err(e) => return error(wire::error_code(&e)),
    };
    // Allocate compact-reply storage BEFORE executing a mutation. Mutating reply
    // variants contain only fixed labels, bools and u64s: always below 4096 bytes.
    let mut bytes = Vec::with_capacity(MIN_OUTPUT_CAPACITY);
    let reply = match registry.lock() {
        Ok(mut r) => r.execute(request),
        Err(_) => return error("registry_poisoned"),
    };
    match reply {
        Ok(reply) => serde_json::to_writer(
            &mut bytes,
            &Success {
                status: "ok",
                reply,
            },
        )
        .expect("closed reply schema serializes infallibly to Vec"),
        Err(code) => return error(code),
    }
    bytes
}

#[unsafe(no_mangle)]
pub extern "C" fn narwhal_session_abi() -> u32 {
    1
}

/// Submit one UTF-8 JSON request; no NUL terminator is read or written.
///
/// Returns bytes written, or required length with NO copy when a read reply does
/// not fit. Invalid output descriptors return -1 because JSON cannot safely be
/// delivered. Every other failure is JSON; an undersized output gets a compact
/// error (or its required length), never executes the request. See the header.
///
/// # Safety
/// Non-null input must address `length` readable bytes and output must address
/// `capacity` writable bytes for the duration of the call. The caller must prevent
/// concurrent access to these buffers. Null/oversized descriptors are rejected,
/// but arbitrary dangling pointers cannot be validated by a C ABI. Buffers may
/// overlap: parsing owns its request before the final copy. No pointer is retained.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn narwhal_session_json(
    input: *const u8,
    length: usize,
    output: *mut u8,
    capacity: usize,
) -> isize {
    // Validate descriptors before dereference, allocation, registry access or any
    // mutation. In particular, do NOT use capacity negotiation for mutations.
    if output.is_null() || capacity > MAX_OUTPUT_CAPACITY {
        return -1;
    }
    let bytes = contain(|| {
        if capacity < MIN_OUTPUT_CAPACITY {
            return error("output_too_small");
        }
        if input.is_null() {
            return error("null_input");
        }
        if length > MAX_REQUEST_BYTES {
            return error("request_too_large");
        }
        // SAFETY: caller contract plus descriptor bounds checked above.
        let input = unsafe { std::slice::from_raw_parts(input, length) };
        dispatch(
            input,
            REGISTRY.get_or_init(|| Mutex::new(Registry::default())),
        )
    });
    let required = bytes.len();
    if required <= capacity {
        // SAFETY: output contract; input borrow has ended and source is an owned Vec.
        unsafe {
            std::ptr::copy_nonoverlapping(bytes.as_ptr(), output, required);
        }
    }
    required as isize
}

pub const MAX_HISTORY_REQUEST_BYTES: usize = 1024 * 1024;
static HISTORY_REGISTRY: OnceLock<Mutex<history::Registry>> = OnceLock::new();

#[derive(Serialize)]
struct HistorySuccess {
    status: &'static str,
    #[serde(flatten)]
    reply: history::Reply,
}
fn dispatch_history(input: &[u8], registry: &Mutex<history::Registry>) -> Vec<u8> {
    let text = match std::str::from_utf8(input) {
        Ok(text) => text,
        Err(_) => return error("invalid_utf8"),
    };
    let request: history::Request = match serde_json::from_str(text) {
        Ok(request) => request,
        Err(e) => return error(wire::error_code(&e)),
    };
    let mut bytes = Vec::with_capacity(MIN_OUTPUT_CAPACITY);
    let reply = match registry.lock() {
        Ok(mut registry) => registry.execute(request),
        Err(_) => return error("registry_poisoned"),
    };
    match reply {
        Ok(reply) => serde_json::to_writer(
            &mut bytes,
            &HistorySuccess {
                status: "ok",
                reply,
            },
        )
        .expect("closed history reply schema serializes infallibly to Vec"),
        Err(code) => return error(code),
    }
    bytes
}

#[unsafe(no_mangle)]
pub extern "C" fn narwhal_history_abi() -> u32 {
    1
}

/// Persistent history ABI1; see HISTORY.md. Mutation replies always fit 4096 bytes.
///
/// # Safety
/// The same buffer validity, overlap and concurrency contract as
/// `narwhal_session_json` applies. No caller pointer is retained.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn narwhal_history_json(
    input: *const u8,
    length: usize,
    output: *mut u8,
    capacity: usize,
) -> isize {
    if output.is_null() || capacity > MAX_OUTPUT_CAPACITY {
        return -1;
    }
    let bytes = contain(|| {
        if MIN_OUTPUT_CAPACITY > capacity {
            return error("output_too_small");
        }
        if input.is_null() {
            return error("null_input");
        }
        if length > MAX_HISTORY_REQUEST_BYTES {
            return error("request_too_large");
        }
        // SAFETY: caller contract and descriptor bounds checked above.
        let input = unsafe { std::slice::from_raw_parts(input, length) };
        dispatch_history(
            input,
            HISTORY_REGISTRY.get_or_init(|| Mutex::new(history::Registry::default())),
        )
    });
    let required = bytes.len();
    if required <= capacity {
        // SAFETY: the input borrow ended; bytes owns nonoverlapping storage.
        unsafe {
            std::ptr::copy_nonoverlapping(bytes.as_ptr(), output, required);
        }
    }
    required as isize
}

static OBJECT_REGISTRY: OnceLock<Mutex<object_registry::Registry>> = OnceLock::new();

fn dispatch_objects(input: &[u8], registry: &Mutex<object_registry::Registry>) -> Vec<u8> {
    let request = match serde_json::from_slice::<object_registry::Request>(input) {
        Ok(request) => request,
        Err(e) => return error(wire::error_code(&e)),
    };
    let mut bytes = Vec::with_capacity(MIN_OUTPUT_CAPACITY);
    let reply = match registry.lock() {
        Ok(mut registry) => registry.execute(request),
        Err(_) => return error("registry_poisoned"),
    };
    match reply {
        Ok(mut value) => {
            value["status"] = serde_json::Value::from("ok");
            serde_json::to_writer(&mut bytes, &value).expect("closed registry reply");
            bytes
        }
        Err(code) => error(code),
    }
}

#[unsafe(no_mangle)]
pub extern "C" fn narwhal_object_registry_abi() -> u32 {
    1
}

/// Additive object registry ABI1. Mutations require at least 4096 output bytes;
/// only read operations may negotiate a larger reply without side effects.
///
/// # Safety
/// Same buffer validity/nonconcurrent-access contract as narwhal_session_json.
/// No caller pointers are retained. Input and output may overlap.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn narwhal_object_registry_json(
    input: *const u8,
    length: usize,
    output: *mut u8,
    capacity: usize,
) -> isize {
    if output.is_null() || capacity > MAX_OUTPUT_CAPACITY {
        return -1;
    }
    let bytes = contain(|| {
        if capacity < MIN_OUTPUT_CAPACITY {
            return error("output_too_small");
        }
        if input.is_null() {
            return error("null_input");
        }
        if length > object_registry::MAX_REQUEST {
            return error("request_too_large");
        }
        // SAFETY: checked descriptors plus the caller's readable-buffer contract.
        let input = unsafe { std::slice::from_raw_parts(input, length) };
        dispatch_objects(
            input,
            OBJECT_REGISTRY.get_or_init(|| Mutex::new(Default::default())),
        )
    });
    let required = bytes.len();
    if required <= capacity {
        // SAFETY: source is owned and input borrow ended before this copy.
        unsafe {
            std::ptr::copy_nonoverlapping(bytes.as_ptr(), output, required);
        }
    }
    required as isize
}

#[unsafe(no_mangle)]
pub extern "C" fn narwhal_object_observation_abi() -> u32 {
    1
}

/// Submit an observation prepare/check/commit header and a binary NWOBS01 batch.
/// All operations can mutate state (check invalidates on mismatch); never retry an
/// unknown result. Replies fit4096 bytes. This does not deserialize geometry.
///
/// # Safety
/// All non-null buffers must be valid for their stated sizes during this call;
/// callers prevent concurrent buffer access. Input/output may overlap. No borrowed
/// pointers are retained; admitted witnesses are copied into bounded native state.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn narwhal_object_registry_observe(
    header: *const u8,
    header_length: usize,
    observation: *const u8,
    observation_length: usize,
    output: *mut u8,
    capacity: usize,
) -> isize {
    if output.is_null() || capacity > MAX_OUTPUT_CAPACITY {
        return -1;
    }
    let bytes = contain(|| {
        if capacity < MIN_OUTPUT_CAPACITY {
            return error("output_too_small");
        }
        if header.is_null() || observation.is_null() {
            return error("null_input");
        }
        if header_length > object_registry::MAX_REQUEST
            || observation_length > observation::MAX_BATCH
        {
            return error("request_too_large");
        }
        // SAFETY: descriptor limits and caller's readable-buffer contract above.
        let header = unsafe { std::slice::from_raw_parts(header, header_length) };
        let observation = unsafe { std::slice::from_raw_parts(observation, observation_length) };
        let request: object_registry::ObservationRequest = match serde_json::from_slice(header) {
            Ok(request) => request,
            Err(e) => return error(wire::error_code(&e)),
        };
        let mut bytes = Vec::with_capacity(MIN_OUTPUT_CAPACITY);
        let reply = match OBJECT_REGISTRY
            .get_or_init(|| Mutex::new(Default::default()))
            .lock()
        {
            Ok(mut registry) => registry.observe(request, observation),
            Err(_) => return error("registry_poisoned"),
        };
        match reply {
            Ok(mut value) => {
                value["status"] = serde_json::Value::from("ok");
                serde_json::to_writer(&mut bytes, &value).expect("closed observation reply");
                bytes
            }
            Err(code) => error(code),
        }
    });
    let required = bytes.len();
    if required <= capacity {
        // SAFETY: owned reply, input borrows ended, output validated by caller.
        unsafe {
            std::ptr::copy_nonoverlapping(bytes.as_ptr(), output, required);
        }
    }
    required as isize
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn history_poison_fails_closed() {
        let mutex = Mutex::new(history::Registry::default());
        let _ = catch_unwind(AssertUnwindSafe(|| {
            let _guard = mutex.lock().unwrap();
            panic!("history test-only poison");
        }));
        assert_eq!(
            dispatch_history(br#"{"op":"create","limit":1}"#, &mutex),
            error("registry_poisoned")
        );
    }
    #[test]
    fn panics_are_contained_and_poison_is_fail_closed() {
        let reply = contain(|| panic!("test-only injected failure"));
        assert_eq!(reply, error("panic"));
        let mutex = Mutex::new(Registry::default());
        let _ = catch_unwind(AssertUnwindSafe(|| {
            let _lock = mutex.lock().unwrap();
            panic!("poison local registry");
        }));
        assert_eq!(
            dispatch(br#"{"op":"create","rows":[]}"#, &mutex),
            error("registry_poisoned")
        );
    }
}
