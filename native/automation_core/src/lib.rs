//! Native CAD conversation backend. No HTTP, Blender, command or kernel access.
pub mod actions;
pub mod approval_lifecycle;
pub mod box_action;
pub mod catalog;
pub mod conversation;
pub mod diagnostics;
pub mod evidence_contract;
pub mod json;
pub mod plan;
pub mod plan_review;
pub mod plan_schema;
pub mod primitives;
pub mod provider;
pub mod verification;
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
    if let Input::CheckPrimitiveGeometry { arguments, facts } = request {
        return arguments.check_geometry(facts);
    }
    if let Input::GeometrySummaryContract { claim } = request {
        return claim.validate();
    }
    if matches!(request, Input::IdentityContract {}) {
        return Ok(evidence_contract::capabilities());
    }
    if let Input::ValidatePlan { plan } = request {
        return plan.describe();
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
        Input::ProposeBox {
            token,
            binding,
            call_id,
            context,
        } => conversation.stage_box(token, binding, call_id, context),
        Input::DecideBox {
            binding,
            proposal,
            current,
            accepted,
        } => conversation.decide_box(binding, proposal, current, accepted),
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
        Input::StagePrimitivePrototype {
            owner,
            binding,
            arguments,
            context,
        } => conversation.stage_primitive(owner, binding, arguments, context),
        Input::DecidePrimitivePrototype {
            binding,
            proposal,
            current,
            accepted,
        } => conversation.decide_primitive(binding, proposal, current, accepted),
        Input::TakePrimitivePrototype {
            binding,
            proposal,
            current,
        } => conversation.take_primitive(binding, proposal, current),
        Input::StageAcceptedReview {
            owner,
            binding,
            call_id,
            context,
        } => conversation.stage_accepted_review(owner, binding, call_id, context),
        Input::AcceptedReviewState {
            owner,
            binding,
            call_id,
        } => conversation.accepted_review_state(owner, binding, call_id),
        Input::DecideAcceptedReview {
            token,
            binding,
            action,
            current,
            allow,
        } => conversation.decide_accepted_review(token, binding, action, current, allow),
        Input::ConsumeAcceptedReview {
            token,
            binding,
            current,
        } => conversation.consume_accepted_review(token, binding, current),
        Input::RecordReviewPublication {
            token,
            binding,
            outcome,
        } => conversation.record_review_publication(token, binding, *outcome),
        Input::AcceptedReviewResult {
            owner,
            binding,
            call_id,
        } => conversation.accepted_review_result(owner, binding, call_id),
        Input::StagePlanDraft { binding, plan } => conversation.stage_plan_draft(binding, plan),
        Input::ReviewPlanDraft {
            ticket,
            binding,
            accepted,
        } => conversation.review_plan_draft(ticket, binding, accepted),
        Input::RevisePlanDraft {
            ticket,
            binding,
            plan,
        } => conversation.revise_plan_draft(ticket, binding, plan),
        Input::State => Ok(conversation.public_state()),
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
        let value = diagnostics::annotate(
            result.unwrap_or_else(|code| json!({"status":"failed","code":code})),
        );
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

#[cfg(test)]
mod v4_tests {
    use super::*;
    struct Boundary {
        handle: u64,
        scratch: Vec<u8>,
        calls: usize,
        input_bytes: usize,
        output_bytes: usize,
    }
    impl Boundary {
        fn new() -> Self {
            Self {
                handle: 0,
                scratch: vec![0; OUTPUT_CAPACITY],
                calls: 0,
                input_bytes: 0,
                output_bytes: 0,
            }
        }
        fn rpc(&mut self, value: Value) -> Value {
            let raw = serde_json::to_vec(&value).unwrap();
            // Test-owned nonoverlapping buffers satisfy the unchanged FFI contract.
            let size = unsafe {
                narwhal_workflow_json(
                    self.handle,
                    raw.as_ptr(),
                    raw.len(),
                    self.scratch.as_mut_ptr(),
                    self.scratch.len(),
                )
            };
            assert!(size > 0 && size as usize <= self.scratch.len());
            self.calls += 1;
            self.input_bytes += raw.len();
            self.output_bytes += size as usize;
            let result: Value = serde_json::from_slice(&self.scratch[..size as usize]).unwrap();
            assert_ne!(result["status"], "failed", "{result}");
            if result["status"] == "created" {
                self.handle = result["handle"].as_u64().unwrap();
            }
            result
        }
    }
    /// OURS: process-local fixture for Main's real publisher tests, never a library export.
    #[test]
    fn v4_main_publisher_bridge() {
        use std::io::{BufRead, Read, Write};

        const TEST: &str = "v4_tests::v4_main_publisher_bridge";
        let args: Vec<String> = std::env::args().skip(1).collect();
        // Do not even inspect stdin in a normal test run (it may be an open terminal).
        if std::env::var("NARWHAL_TEST_V4_PUBLISHER_BRIDGE").as_deref() != Ok("1")
            || !args.iter().any(|arg| arg == "--exact")
            || !args.iter().any(|arg| arg == TEST)
            || !args.iter().any(|arg| arg == "--nocapture")
        {
            return;
        }
        #[derive(serde::Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Request {
            handle: u64,
            // Bytes preserve duplicate keys, invalid UTF-8 and all original ABI input.
            data: Vec<u8>,
        }
        const MAX_FRAME: usize = MAX_INPUT * 4 + 128;
        let stdin = std::io::stdin();
        let mut reader = stdin.lock();
        let stdout = std::io::stdout();
        let mut writer = stdout.lock();
        let mut output = vec![0u8; OUTPUT_CAPACITY];
        // The launcher additionally bounds total lifetime and each exchange, including
        // partial lines. A message/count limit alone cannot bound a blocked pipe read.
        for _ in 0..4096 {
            let mut line = Vec::new();
            let count = reader
                .by_ref()
                .take((MAX_FRAME + 1) as u64)
                .read_until(b'\n', &mut line)
                .unwrap();
            if count == 0 {
                return;
            }
            assert!(
                count <= MAX_FRAME && line.last() == Some(&b'\n'),
                "bridge_frame_limit"
            );
            let request: Request = serde_json::from_slice(&line).expect("bridge_request");
            // Explicit test-process control, not a normal ABI/JSON operation. It
            // drives real expiry logic without a five-minute GUI test sleep.
            let control = std::str::from_utf8(&request.data)
                .ok()
                .and_then(|text| crate::json::strict_json(text).ok());
            if let Some(control) = control.filter(|v| v["op"] == "fixture_advance_review_ms") {
                let result = (|| {
                    if control.as_object().is_none_or(|map| map.len() != 2) {
                        return Err("fixture_clock_request");
                    }
                    let delta = control["delta_ms"]
                        .as_u64()
                        .ok_or("fixture_clock_request")?;
                    let mut sessions = SESSIONS
                        .get()
                        .ok_or("unknown_session")?
                        .lock()
                        .map_err(|_| "session_lock")?;
                    sessions
                        .items
                        .get_mut(&request.handle)
                        .ok_or("unknown_session")?
                        .advance_review_fixture_clock(delta)
                })();
                let data = match result {
                    Ok(()) => json!({"status":"fixture_clock_advanced"}),
                    Err(code) => json!({"status":"failed","code":code}),
                }
                .to_string();
                writeln!(
                    writer,
                    "\nNARWHAL_V4_PUBLISHER_JSON:{}",
                    json!({"size":data.len(),"data":data})
                )
                .unwrap();
                writer.flush().unwrap();
                continue;
            }
            let size = unsafe {
                narwhal_workflow_json(
                    request.handle,
                    request.data.as_ptr(),
                    request.data.len(),
                    output.as_mut_ptr(),
                    output.len(),
                )
            };
            let data = if size > 0 {
                let data = std::str::from_utf8(&output[..size as usize]).unwrap();
                let value: Value = serde_json::from_str(data).unwrap();
                if value["status"] == "created" {
                    // Creation, parsing and binding validation above are the ordinary flow.
                    // Test-only catalog admission; no accepted calls or receipts are injected.
                    let handle = value["handle"].as_u64().unwrap();
                    SESSIONS
                        .get()
                        .unwrap()
                        .lock()
                        .unwrap()
                        .items
                        .get_mut(&handle)
                        .unwrap()
                        .enable_primitive_fixture_catalog();
                }
                data
            } else {
                ""
            };
            // Leading newline separates this from libtest's `test ...` progress text.
            writeln!(
                writer,
                "\nNARWHAL_V4_PUBLISHER_JSON:{}",
                json!({"size":size,"data":data})
            )
            .unwrap();
            writer.flush().unwrap();
        }
    }

    fn tool_reply(name: &str, id: &str, args: Value) -> String {
        json!({"choices":[{"finish_reason":"tool_calls","message":{"role":"assistant","content":null,"tool_calls":[{"id":id,"type":"function",
            "function":{"name":name,"arguments":args.to_string()}}]}}]}).to_string()
    }
    #[test]
    fn v4_a30_full_ffi_lifecycle_profile() {
        let mut times = vec![];
        let mut totals = (0, 0, 0);
        for _ in 0..20 {
            let start = std::time::Instant::now();
            let mut io = Boundary::new();
            let binding = json!({"config":{"provider_id":"openai_compatible","model":"fixture","endpoint":"http://localhost/v1","credential_env":"TEST_KEY",
                "allow_insecure_remote":false,"include_selection":true},"document_context":"00000000000000000000000000000001","allow_reads":true});
            io.rpc(json!({"op":"create","binding":binding}));
            SESSIONS
                .get()
                .unwrap()
                .lock()
                .unwrap()
                .items
                .get_mut(&io.handle)
                .unwrap()
                .enable_primitive_fixture_catalog();
            io.rpc(json!({"op":"validate_plan","plan":{"version":1,"goal":"Inspect","units":"Millimeters","intent":"advice","questions":[],"steps":[
                {"id":"read","depends_on":[],"inputs":[],"action":{"operation":"read","arguments":{"tool":"narwhal.workspace","arguments":{}}},"expected":{"kind":"metadata","count":1},"verify":[{"kind":"object_count","count":1}]}]}}));
            let ready=io.rpc(json!({"op":"begin","binding":binding,"text":"profile","context":null,"timeout_ms":30000,"max_tokens":4096}));
            io.rpc(json!({"op":"complete","binding":binding,"token":ready["token"],"raw":tool_reply("narwhal_create_sphere","p1",json!({"center":[0,0,0],"radius":4})),"failure":null}));
            let context = json!({"append":{"document_session":binding["document_context"],"layer_id":"00000000-0000-0000-0000-000000000001","units":"Millimeters",
                "tolerance":0.01,"layer_available":true,"command_idle":true,"undo_ready":true},"undo_top_serial":0,"undo_next_serial":1});
            let review=io.rpc(json!({"op":"stage_accepted_review","owner":ready["token"],"binding":binding,"call_id":"p1","context":context}))["review"].clone();
            io.rpc(json!({"op":"decide_accepted_review","token":review["token"],"binding":binding,"action":review["action"],"current":context,"allow":true}));
            io.rpc(json!({"op":"consume_accepted_review","token":review["token"],"binding":binding,"current":context}));
            let object_id = "00000000-0000-0000-0000-000000000007";
            io.rpc(json!({"op":"record_review_publication","token":review["token"],"binding":binding,"outcome":{"outcome":"succeeded","object_id":object_id,"units":"Millimeters","action":review["action"],
                "geometry":{"proxy_solid":true,"airtight":true,"face_types":{"SPHERE":1},"recipe_kind":"sphere","volume":4./3.*std::f64::consts::PI*64.,"bounds":[[-4,-4,-4],[4,4,4]]}}}));
            let result=io.rpc(json!({"op":"accepted_review_result","owner":ready["token"],"binding":binding,"call_id":"p1"}))["result"].clone();
            io.rpc(
                json!({"op":"results","token":ready["token"],"binding":binding,"results":[result]}),
            );
            let read=io.rpc(json!({"op":"begin","binding":binding,"text":null,"context":null,"timeout_ms":30000,"max_tokens":4096}));
            io.rpc(json!({"op":"complete","binding":binding,"token":read["token"],"raw":tool_reply("narwhal_object_summary","read1",json!({"object_id":object_id})),"failure":null}));
            io.rpc(json!({"op":"results","token":read["token"],"binding":binding,"results":[{"call_id":"read1","output":{"object_id":object_id},"is_error":false}]}));
            let verify=io.rpc(json!({"op":"begin","binding":binding,"text":null,"context":null,"timeout_ms":30000,"max_tokens":4096}));
            io.rpc(json!({"op":"complete","binding":binding,"token":verify["token"],"raw":tool_reply("narwhal_verify","verify1",json!({"call_id":"p1","object_id":object_id})),"failure":null}));
            let outcome=io.rpc(json!({"op":"resolve_call","token":verify["token"],"binding":binding,"call_id":"verify1"}));
            assert_eq!(outcome["output"]["creation_receipt_verified"], true);
            assert_eq!(outcome["output"]["post_action_object_observed"], true);
            assert_eq!(outcome["output"]["current_geometry_verified"], false);
            io.rpc(json!({"op":"close"}));
            assert!(
                !SESSIONS
                    .get()
                    .unwrap()
                    .lock()
                    .unwrap()
                    .items
                    .contains_key(&io.handle)
            );
            totals = (io.calls, io.input_bytes, io.output_bytes);
            assert!(io.calls <= 20 && io.output_bytes < 100_000);
            times.push(start.elapsed().as_micros());
        }
        times.sort_unstable();
        let memory = std::fs::read_to_string("/proc/self/status").unwrap_or_default();
        let high_water = memory
            .lines()
            .find(|line| line.starts_with("VmHWM:"))
            .unwrap_or("VmHWM unavailable");
        println!(
            "V4_FFI_PROFILE rounds=20 median_us={} calls={} input_bytes={} output_bytes={} scratch_bytes={} {}; synthetic publisher facts, no geometry/network",
            times[10], totals.0, totals.1, totals.2, OUTPUT_CAPACITY, high_water
        );
    }
    #[test]
    fn v4_a27_close_removes_a_real_accepted_review_session() {
        let binding = json!({"config":{"provider_id":"openai_compatible","model":"fixture","endpoint":"http://localhost/v1","credential_env":"TEST_KEY",
            "allow_insecure_remote":false,"include_selection":true},"document_context":"00000000000000000000000000000001","allow_reads":true});
        let created = exchange(0, &json!({"op":"create","binding":binding}).to_string()).unwrap();
        let handle = created["handle"].as_u64().unwrap();
        SESSIONS
            .get()
            .unwrap()
            .lock()
            .unwrap()
            .items
            .get_mut(&handle)
            .unwrap()
            .enable_primitive_fixture_catalog();
        let ready=exchange(handle,&json!({"op":"begin","binding":binding,"text":"fixture","context":null,"timeout_ms":30000,"max_tokens":4096}).to_string()).unwrap();
        let raw=json!({"choices":[{"finish_reason":"tool_calls","message":{"role":"assistant","content":null,"tool_calls":[{"id":"p1","type":"function",
            "function":{"name":"narwhal_create_sphere","arguments":"{\"center\":[0,0,0],\"radius\":4}"}}]}}]}).to_string();
        assert_eq!(exchange(handle,&json!({"op":"complete","binding":binding,"token":ready["token"],"raw":raw,"failure":null}).to_string()).unwrap()["status"],"tool_calls");
        let context = json!({"append":{"document_session":binding["document_context"],"layer_id":"00000000-0000-0000-0000-000000000001","units":"Millimeters",
            "tolerance":0.01,"layer_available":true,"command_idle":true,"undo_ready":true},"undo_top_serial":0,"undo_next_serial":1});
        let review=exchange(handle,&json!({"op":"stage_accepted_review","owner":ready["token"],"binding":binding,"call_id":"p1","context":context}).to_string()).unwrap()["review"].clone();
        assert_eq!(
            exchange(handle, "{\"op\":\"close\"}").unwrap()["status"],
            "closed"
        );
        let late = json!({"op":"decide_accepted_review","token":review["token"],"binding":binding,"action":review["action"],"current":context,"allow":true});
        assert_eq!(
            exchange(handle, &late.to_string()).unwrap_err(),
            "unknown_session"
        );
    }
}
