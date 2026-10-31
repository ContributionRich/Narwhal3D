//! Independent transcript ABI 1. Fixed mutation replies; immutable bounded snapshots.
use super::transcript::{self, Transcript, MAX_WIRE};
use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct Request {
    pub op: u32,
    pub flags: u32,
    pub revision: u64,
    pub a: i64,
    pub b: i64,
    pub c: i64,
}
#[repr(C)]
#[derive(Clone, Copy, Default, Debug)]
pub struct Reply {
    pub handle: u64,
    pub revision: u64,
    pub len: u64,
    pub token: u64,
    pub count: u64,
    pub bytes: u64,
    pub kind: u64,
}
struct Snapshot {
    owner: u64,
    revision: u64,
    data: Vec<u8>,
}
#[derive(Default)]
struct Registry {
    next: u64,
    next_snapshot: u64,
    owners: HashMap<u64, Transcript>,
    snapshots: HashMap<u64, Snapshot>,
    snapshot_bytes: usize,
}
static REGISTRY: OnceLock<Mutex<Registry>> = OnceLock::new();
fn registry() -> &'static Mutex<Registry> {
    REGISTRY.get_or_init(Default::default)
}
fn reply(handle: u64, owner: &Transcript) -> Reply {
    Reply {
        handle,
        revision: owner.revision,
        len: owner.lines.len() as u64,
        ..Reply::default()
    }
}
#[no_mangle]
pub extern "C" fn narwhal_transcript_abi() -> u32 {
    1
}
#[no_mangle]
pub extern "C" fn narwhal_transcript_capabilities() -> u64 {
    7
} // raw/normalized, Unicode15.1/16, snapshots

/// # Safety
/// Valid, aligned request/output records of the stated sizes; valid input bytes.
/// No overlap or concurrent access to buffers. Null/size checks cannot prove memory ownership.
#[no_mangle]
pub unsafe extern "C" fn narwhal_transcript_apply(
    handle: u64,
    request: *const Request,
    request_bytes: usize,
    input: *const u8,
    input_bytes: usize,
    output: *mut Reply,
    output_bytes: usize,
) -> u32 {
    if request.is_null()
        || output.is_null()
        || request_bytes != std::mem::size_of::<Request>()
        || output_bytes != std::mem::size_of::<Reply>()
        || input_bytes > MAX_WIRE
        || (input.is_null() && input_bytes != 0)
    {
        return 1;
    }
    let r = *request;
    if r.op > 8
        || (r.op != 0 && r.flags != 0)
        || (r.op == 0 && r.flags & !3 != 0)
        || (!matches!(r.op, 1 | 5) && input_bytes != 0)
    {
        return 1;
    }
    let input = if input_bytes == 0 {
        &[]
    } else {
        std::slice::from_raw_parts(input, input_bytes)
    };
    let Ok(mut reg) = registry().lock() else {
        return 5;
    };
    let result = apply(&mut reg, handle, r, input);
    match result {
        Ok(value) => {
            output.write(value);
            0
        }
        Err(code) => code,
    }
}
fn apply(reg: &mut Registry, handle: u64, r: Request, input: &[u8]) -> Result<Reply, u32> {
    if r.op == 0 || r.op == 6 {
        let owner = if r.op == 0 {
            if handle != 0 || r.revision != 0 || r.a < 0 || r.b != 0 || r.c != 0 {
                return Err(1);
            }
            Transcript::new(r.a as usize, r.flags & 1 != 0, r.flags & 2 != 0)
        } else {
            let owner = reg.owners.get(&handle).ok_or(2u32)?;
            if owner.revision != r.revision {
                return Err(2);
            }
            owner.clone()
        };
        if reg.owners.len() >= 65536 {
            return Err(4);
        }
        let id = reg.next.checked_add(1).ok_or(4u32)?;
        reg.owners.try_reserve(1).map_err(|_| 4u32)?;
        let out = reply(id, &owner);
        reg.owners.insert(id, owner);
        reg.next = id;
        return Ok(out);
    }
    let owner = reg.owners.get_mut(&handle).ok_or(2u32)?;
    if owner.revision != r.revision {
        return Err(2);
    }
    match r.op {
        1 => {
            owner.append(transcript::parse_lines(input)?)?;
            return Ok(reply(handle, owner));
        }
        2 => {
            if r.a < 0 {
                return Err(1);
            }
            owner.prune(r.a as usize)?;
            return Ok(reply(handle, owner));
        }
        3 => {
            owner.prune(owner.lines.len())?;
            return Ok(reply(handle, owner));
        }
        _ => {}
    }
    // Preparing a snapshot cannot mutate the transcript. Its data stays valid through
    // later append/prune/clear, until explicit release or owner disposal.
    let mut out = reply(handle, owner);
    let data = match r.op {
        4 | 8 => {
            let lines = if r.op == 8 {
                owner.last_command()
            } else {
                owner.range(r.a, r.b, r.c)?
            };
            out.kind = 1;
            out.count = lines.len() as u64;
            transcript::encode_lines(&lines)?
        }
        5 => {
            let indices = owner.search(&transcript::points(input)?)?;
            out.kind = 2;
            out.count = indices.len() as u64;
            let mut data = Vec::new();
            data.try_reserve_exact(indices.len() * 8)
                .map_err(|_| 4u32)?;
            for i in indices {
                data.extend_from_slice(&i.to_le_bytes());
            }
            data
        }
        7 => {
            out.kind = 3;
            out.count = owner.lines.len() as u64;
            let size = (owner.points + owner.lines.len()) * 4;
            let mut data = Vec::new();
            data.try_reserve_exact(size).map_err(|_| 4u32)?;
            for line in &owner.lines {
                for cp in line.iter() {
                    data.extend_from_slice(&cp.to_le_bytes());
                }
                data.extend_from_slice(&10u32.to_le_bytes());
            }
            data
        }
        _ => return Err(1),
    };
    if reg.snapshots.len() >= 4096 || reg.snapshot_bytes + data.len() > 256 * 1024 * 1024 {
        return Err(4);
    }
    let token = reg.next_snapshot.checked_add(1).ok_or(4u32)?;
    reg.snapshots.try_reserve(1).map_err(|_| 4u32)?;
    out.token = token;
    out.bytes = data.len() as u64;
    reg.snapshot_bytes += data.len();
    reg.snapshots.insert(
        token,
        Snapshot {
            owner: handle,
            revision: out.revision,
            data,
        },
    );
    reg.next_snapshot = token;
    Ok(out)
}

/// # Safety
/// `needed` is writable for one size_t. Non-null output is writable for capacity bytes.
/// Buffers do not overlap or have concurrent access. Query (null,0) is read-only.
#[no_mangle]
pub unsafe extern "C" fn narwhal_transcript_read(
    handle: u64,
    token: u64,
    revision: u64,
    output: *mut u8,
    capacity: usize,
    needed: *mut usize,
) -> u32 {
    if needed.is_null() || capacity > MAX_WIRE || (output.is_null() && capacity != 0) {
        return 1;
    }
    needed.write(0);
    let Ok(reg) = registry().lock() else {
        return 5;
    };
    let Some(snapshot) = reg.snapshots.get(&token) else {
        return 2;
    };
    if snapshot.owner != handle || snapshot.revision != revision {
        return 2;
    }
    needed.write(snapshot.data.len());
    if capacity < snapshot.data.len() {
        return 3;
    }
    if !snapshot.data.is_empty() {
        std::ptr::copy_nonoverlapping(snapshot.data.as_ptr(), output, snapshot.data.len());
    }
    0
}
#[no_mangle]
pub extern "C" fn narwhal_transcript_release(handle: u64, token: u64) -> u32 {
    let Ok(mut reg) = registry().lock() else {
        return 5;
    };
    if reg.snapshots.get(&token).is_none_or(|s| s.owner != handle) {
        return 2;
    }
    let snapshot = reg.snapshots.remove(&token).unwrap();
    reg.snapshot_bytes -= snapshot.data.len();
    0
}
#[no_mangle]
pub extern "C" fn narwhal_transcript_close(handle: u64) -> u32 {
    let Ok(mut reg) = registry().lock() else {
        return 5;
    };
    if reg.owners.remove(&handle).is_none() {
        return 2;
    }
    reg.snapshots.retain(|_, s| s.owner != handle);
    reg.snapshot_bytes = reg.snapshots.values().map(|s| s.data.len()).sum();
    0
}
/// # Safety
/// output is aligned/writable for four u64 values, with exclusive access.
#[no_mangle]
pub unsafe extern "C" fn narwhal_transcript_stats(output: *mut u64, count: usize) -> u32 {
    if output.is_null() || count != 4 {
        return 1;
    }
    let Ok(reg) = registry().lock() else {
        return 5;
    };
    let values = [
        reg.owners.len() as u64,
        reg.snapshots.len() as u64,
        reg.owners.values().map(|s| s.points as u64).sum(),
        reg.snapshot_bytes as u64,
    ];
    std::ptr::copy_nonoverlapping(values.as_ptr(), output, 4);
    0
}
