//! Native CAD conversation backend. No HTTP, Blender, command or kernel access.
pub mod actions;
pub mod box_action;
pub mod catalog;
pub mod conversation;
pub mod json;
pub mod provider;
pub mod workspace;

use conversation::{Conversation, Input};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    sync::{Mutex, OnceLock},
};

const MAX_INPUT: usize = 1_048_576;
const OUTPUT_CAPACITY: usize = 524_288;
struct Sessions {
    next: u64,
    items: HashMap<u64, Conversation>,
}
static SESSIONS: OnceLock<Mutex<Sessions>> = OnceLock::new();

fn exchange(handle: u64, input: &str) -> Result<Value, &'static str> {
    let value = crate::json::strict_json(input).map_err(|_| "invalid_json")?;
    let request: Input = serde_json::from_value(value).map_err(|_| "invalid_request")?;
    if let Input::WorkspaceLayers { layers, targets } = request {
        return Ok(json!({"status":"layers","flags":workspace::layer_flags(layers,targets)?}));
    }
    if matches!(request, Input::Catalog) {
        return Ok(
            json!({"status":"catalog","tools":catalog::definitions(),"operations":actions::operations()}),
        );
    }
    let mut sessions = SESSIONS
        .get_or_init(|| {
            Mutex::new(Sessions {
                next: 1,
                items: HashMap::new(),
            })
        })
        .lock()
        .map_err(|_| "backend_unavailable")?;
    if let Input::Create { binding } = request {
        if handle != 0 || sessions.items.len() >= 32 {
            return Err("session_limit");
        }
        let id = sessions.next;
        let conversation = Conversation::new(id, binding)?;
        sessions.next += 1;
        sessions.items.insert(id, conversation);
        return Ok(json!({"status":"created","handle":id}));
    }
    if matches!(request, Input::Close) {
        sessions.items.remove(&handle).ok_or("unknown_session")?;
        return Ok(json!({"status":"closed"}));
    }
    let conversation = sessions.items.get_mut(&handle).ok_or("unknown_session")?;
    match request {
        Input::Reset { binding } => conversation.rebind(binding),
        Input::Begin {
            binding,
            text,
            context,
            timeout_ms,
            max_tokens,
        } => conversation.begin(binding, text, context, timeout_ms, max_tokens),
        Input::Complete {
            token,
            binding,
            raw,
            failure,
        } => Ok(conversation.complete(token, binding, raw, failure)),
        Input::Results {
            token,
            binding,
            results,
        } => conversation.results(token, binding, results),
        Input::ResolveCall {
            token,
            binding,
            call_id,
        } => conversation.resolve_call(&token, binding, &call_id),
        Input::ProposeCircle {
            token,
            binding,
            call_id,
            expected,
        } => conversation.stage_circle(token, binding, call_id, expected),
        Input::ProposeBox { token, binding, call_id, context } =>
            conversation.stage_box(token, binding, call_id, context),
        Input::DecideBox { binding, proposal, current, accepted } =>
            conversation.decide_box(binding, proposal, current, accepted),
        Input::ReviewProposal {
            binding,
            proposal,
            current,
            accepted,
        } => conversation.review_proposal(binding, proposal, current, accepted),
        Input::CheckProposal {
            binding,
            id,
            current,
        } => conversation.check_proposal(binding, id, current),
        Input::Poll => Ok(conversation.poll_timeout()),
        Input::Cancel => Ok(conversation.cancel()),
        _ => Err("invalid_request"),
    }
}

#[unsafe(no_mangle)]
pub extern "C" fn narwhal_workflow_abi() -> u32 {
    1
}

/// Caller owns valid nonoverlapping buffers. The full fixed output capacity is
/// required BEFORE running an operation, so a too-small buffer cannot consume a
/// request and induce an ambiguous retry. No allocation/pointer crosses the ABI.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn narwhal_workflow_json(
    handle: u64,
    input: *const u8,
    length: usize,
    output: *mut u8,
    capacity: usize,
) -> isize {
    if input.is_null() || output.is_null() || length > MAX_INPUT || capacity < OUTPUT_CAPACITY {
        return -1;
    }
    let result = std::panic::catch_unwind(|| {
        let bytes = unsafe { std::slice::from_raw_parts(input, length) };
        let result = std::str::from_utf8(bytes)
            .map_err(|_| "invalid_utf8")
            .and_then(|s| exchange(handle, s));
        let value = result.unwrap_or_else(|code| json!({"status":"failed","code":code}));
        let bytes = serde_json::to_vec(&value).map_err(|_| ())?;
        if bytes.len() > OUTPUT_CAPACITY {
            return Err(());
        }
        unsafe {
            std::ptr::copy_nonoverlapping(bytes.as_ptr(), output, bytes.len());
        }
        Ok(bytes.len() as isize)
    });
    result.ok().and_then(Result::ok).unwrap_or(-1)
}
