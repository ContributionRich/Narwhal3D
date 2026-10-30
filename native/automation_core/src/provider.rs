//! OURS: bounded provider wire adapters, not authorization or execution.
//! Openai remains Chat Completions; OpenaiResponses explicitly selects Responses.
//! Model names never select a protocol. Session owns authorization and call/result IDs.
//!
//! MEASURED 2026-10-29: official Responses schema / stateless replay sources:
//! https://developers.openai.com/api/docs/guides/conversation-state.md
//! https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response.py
//! https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_output_message.py
//! https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_output_message_param.py
//! https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_output_text.py
//! https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_output_refusal.py
//! https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_function_tool_call.py
//! https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_function_tool_call_param.py
//! https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_reasoning_item.py
//! https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_reasoning_item_param.py
//! https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_usage.py
//! https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_create_params.py
//! https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_includable.py
//! https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/function_tool_param.py
//! https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_input_item_param.py
//! Encrypted reasoning is now returned by default; explicit include remains supported.
//! Output Optional fields are not all nullable in the input TypedDict schemas:
//! replay omits only absent-equivalent nulls from optional, nonnullable input fields.
//! OURS: accept only completed local-function/text/reasoning output, never server tools.
//! Missing encrypted_content is schema-legal, but refused for stateless reasoning:
//! we cannot promise faithful replay without it, and never drop/reconstruct the item.
use crate::json::strict_json;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Provider {
    Openai,
    OpenaiResponses,
    OpenaiCompatible,
    Anthropic,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub arguments: Map<String, Value>,
}
#[derive(Clone, Serialize, Deserialize)]
pub struct Reply {
    pub text: String,
    pub tool_calls: Vec<ToolCall>,
    pub output_tokens: Option<u64>,
    // Internal replay only, never part of the UI outcome's serialization.
    #[serde(default, skip_serializing, skip_deserializing)]
    replay_items: Vec<Value>,
}
impl std::fmt::Debug for Reply {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Reply")
            .field("text", &self.text)
            .field("tool_calls", &self.tool_calls)
            .field("output_tokens", &self.output_tokens)
            .field("replay_item_count", &self.replay_items.len())
            .finish()
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ToolResult {
    pub call_id: String,
    pub output: Value,
    pub is_error: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ToolDefinition {
    pub name: String,
    pub description: String,
    pub parameters: Value,
}

fn name_ok(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 128
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}
fn string<'a>(v: &'a Value, key: &str) -> Result<&'a str, &'static str> {
    v.get(key).and_then(Value::as_str).ok_or("invalid_response")
}
fn call(reply: &mut Reply, id: &str, name: &str, args: &Value) -> Result<(), &'static str> {
    if id.is_empty()
        || id.len() > 128
        || id.chars().any(|c| c.is_whitespace() || c.is_control())
        || !name_ok(name)
        || reply.tool_calls.iter().any(|c| c.id == id)
    {
        return Err("invalid_tool_call");
    }
    let arguments = args.as_object().ok_or("invalid_arguments")?;
    if reply.tool_calls.len() >= 4 || args.to_string().len() > 4096 {
        return Err("tool_call_limit");
    }
    reply.tool_calls.push(ToolCall {
        id: id.into(),
        name: name.into(),
        arguments: arguments.clone(),
    });
    Ok(())
}
fn usage(root: &Value, key: &str, cap: u64) -> Result<Option<u64>, &'static str> {
    let Some(usage) = root.get("usage") else {
        return Ok(None);
    };
    let n = usage
        .as_object()
        .and_then(|u| u.get(key))
        .and_then(Value::as_u64)
        .filter(|n| *n > 0)
        .ok_or("invalid_usage")?;
    if n > cap {
        return Err("output_token_limit");
    }
    Ok(Some(n))
}
fn optional_tag(v: &Value, key: &str, expected: &str) -> Result<(), &'static str> {
    if v.get(key).is_some_and(|tag| tag.as_str() != Some(expected)) {
        return Err("invalid_response");
    }
    Ok(())
}

fn only_fields(v: &Value, allowed: &[&str]) -> Result<(), &'static str> {
    if v.as_object()
        .ok_or("invalid_response")?
        .keys()
        .any(|k| !allowed.contains(&k.as_str()))
    {
        return Err("unsupported_response_field");
    }
    Ok(())
}

fn array<'a>(v: &'a Value, key: &str) -> Result<&'a Vec<Value>, &'static str> {
    v.get(key)
        .and_then(Value::as_array)
        .ok_or("invalid_response")
}

fn wire_id(id: &str) -> Result<(), &'static str> {
    // OURS: the same bounded opaque-ID policy as local call correlation.
    if id.is_empty() || id.len() > 128 || id.chars().any(|c| c.is_whitespace() || c.is_control()) {
        return Err("invalid_response_id");
    }
    Ok(())
}

fn item_status(item: &Value, required: bool) -> Result<(), &'static str> {
    match item.get("status") {
        Some(Value::String(s)) if s == "completed" => Ok(()),
        None | Some(Value::Null) if !required => Ok(()),
        _ => Err("nonfinal_response_item"),
    }
}

fn reasoning_texts(v: &[Value], kind: &str) -> Result<(), &'static str> {
    for block in v {
        only_fields(block, &["type", "text"])?;
        if string(block, "type")? != kind {
            return Err("unsupported_response_block");
        }
        string(block, "text")?;
    }
    Ok(())
}

fn annotation(a: &Value) -> Result<(), &'static str> {
    let (strings, integers): (&[&str], &[&str]) = match string(a, "type")? {
        "file_citation" => (&["file_id", "filename"], &["index"]),
        "url_citation" => (&["title", "url"], &["start_index", "end_index"]),
        "container_file_citation" => (
            &["container_id", "file_id", "filename"],
            &["start_index", "end_index"],
        ),
        "file_path" => (&["file_id"], &["index"]),
        _ => return Err("unsupported_response_block"),
    };
    let mut keys = vec!["type"];
    keys.extend_from_slice(strings);
    keys.extend_from_slice(integers);
    only_fields(a, &keys)?;
    for key in strings {
        string(a, key)?;
    }
    for key in integers {
        a.get(*key)
            .and_then(Value::as_u64)
            .ok_or("invalid_response")?;
    }
    Ok(())
}

fn logprob(v: &Value, top: bool) -> Result<(), &'static str> {
    let keys: &[&str] = if top {
        &["token", "bytes", "logprob"]
    } else {
        &["token", "bytes", "logprob", "top_logprobs"]
    };
    only_fields(v, keys)?;
    string(v, "token")?;
    v.get("logprob")
        .and_then(Value::as_f64)
        .ok_or("invalid_response")?;
    for byte in array(v, "bytes")? {
        byte.as_u64()
            .filter(|n| *n <= 255)
            .ok_or("invalid_response")?;
    }
    if !top {
        for p in array(v, "top_logprobs")? {
            logprob(p, true)?;
        }
    }
    Ok(())
}

fn responses_usage(root: &Value, cap: u64) -> Result<Option<u64>, &'static str> {
    let Some(u) = root.get("usage").filter(|u| !u.is_null()) else {
        return Ok(None);
    };
    only_fields(
        u,
        &[
            "input_tokens",
            "input_tokens_details",
            "output_tokens",
            "output_tokens_details",
            "total_tokens",
        ],
    )?;
    // SDK int has no positive minimum: zero is meaningful, bool/float are not ints.
    let n = u
        .get("output_tokens")
        .and_then(Value::as_u64)
        .ok_or("invalid_usage")?;
    if n > cap {
        return Err("output_token_limit");
    }
    for key in ["input_tokens", "total_tokens"] {
        if let Some(v) = u.get(key) {
            v.as_u64().ok_or("invalid_usage")?;
        }
    }
    for (key, fields) in [
        (
            "input_tokens_details",
            &["cached_tokens", "cache_write_tokens"][..],
        ),
        ("output_tokens_details", &["reasoning_tokens"][..]),
    ] {
        if let Some(details) = u.get(key) {
            only_fields(details, fields)?;
            for v in details.as_object().ok_or("invalid_usage")?.values() {
                v.as_u64().ok_or("invalid_usage")?;
            }
        }
    }
    Ok(Some(n))
}

fn parse_responses(root: &Value, reply: &mut Reply, cap: u64) -> Result<(), &'static str> {
    // Only known envelope fields may be ignored as metadata/configuration echoes.
    // New structural fields require review, rather than silently changing meaning.
    only_fields(
        root,
        &[
            "id",
            "object",
            "status",
            "output",
            "error",
            "incomplete_details",
            "usage",
            "created_at",
            "completed_at",
            "model",
            "instructions",
            "metadata",
            "tools",
            "tool_choice",
            "parallel_tool_calls",
            "temperature",
            "top_p",
            "text",
            "background",
            "store",
            "conversation",
            "previous_response_id",
            "truncation",
            "max_output_tokens",
            "max_tool_calls",
            "reasoning",
            "service_tier",
            "user",
            "safety_identifier",
            "prompt",
            "prompt_cache_key",
            "prompt_cache_retention",
            "prompt_cache_options",
            "prompt_cache_diagnostics",
            "top_logprobs",
            "access_programs",
            "moderation",
            "tool_usage",
        ],
    )?;
    if string(root, "object")? != "response" {
        return Err("invalid_response");
    }
    wire_id(string(root, "id")?)?;
    match string(root, "status")? {
        "completed" => {}
        "incomplete" => return Err("truncated_response"),
        _ => return Err("nonfinal_response"),
    }
    if root.get("incomplete_details").is_some_and(|v| !v.is_null()) {
        return Err("truncated_response");
    }
    // OURS: this adapter cannot interpret hosted execution/moderation results or
    // continue server-side state. Null/absent optional echoes do not imply state.
    for key in [
        "conversation",
        "previous_response_id",
        "moderation",
        "tool_usage",
    ] {
        if root.get(key).is_some_and(|v| !v.is_null()) {
            return Err("unsupported_response_field");
        }
    }
    for key in ["background", "store"] {
        if root
            .get(key)
            .is_some_and(|v| !v.is_null() && v.as_bool() != Some(false))
        {
            return Err("unsupported_response_field");
        }
    }
    if root
        .get("truncation")
        .is_some_and(|v| !v.is_null() && v.as_str() != Some("disabled"))
    {
        return Err("unsupported_response_field");
    }
    reply.output_tokens = responses_usage(root, cap)?;
    let output = array(root, "output")?;
    // Check BEFORE cloning replay data. This includes opaque encrypted state and
    // every raw item's keys/IDs/annotations/arguments, not only visible text.
    if serde_json::to_vec(output)
        .map_err(|_| "invalid_response")?
        .len()
        > 32 * 1024
    {
        return Err("normalized_output_limit");
    }
    let mut ids = std::collections::HashSet::new();
    for item in output {
        let kind = string(item, "type")?;
        match item.get("id") {
            Some(Value::String(id)) => {
                wire_id(id)?;
                if !ids.insert(id.as_str()) {
                    return Err("duplicate_response_id");
                }
            }
            None | Some(Value::Null) if kind == "function_call" => {}
            _ => return Err("invalid_response_id"),
        }
        match kind {
            "message" => {
                only_fields(item, &["type", "id", "role", "status", "content", "phase"])?;
                item_status(item, true)?;
                if string(item, "role")? != "assistant" {
                    return Err("invalid_response");
                }
                if let Some(phase) = item.get("phase").filter(|v| !v.is_null()) {
                    if !matches!(phase.as_str(), Some("commentary" | "final_answer")) {
                        return Err("unsupported_response_field");
                    }
                }
                for block in array(item, "content")? {
                    match string(block, "type")? {
                        "output_text" => {
                            only_fields(block, &["type", "text", "annotations", "logprobs"])?;
                            for a in array(block, "annotations")? {
                                annotation(a)?;
                            }
                            if let Some(probs) = block.get("logprobs").filter(|v| !v.is_null()) {
                                for p in probs.as_array().ok_or("invalid_response")? {
                                    logprob(p, false)?;
                                }
                            }
                            reply.text.push_str(string(block, "text")?);
                        }
                        "refusal" => {
                            only_fields(block, &["type", "refusal"])?;
                            string(block, "refusal")?;
                            return Err("refused_response");
                        }
                        _ => return Err("unsupported_response_block"),
                    }
                }
            }
            "function_call" => {
                only_fields(
                    item,
                    &[
                        "type",
                        "id",
                        "call_id",
                        "name",
                        "arguments",
                        "status",
                        "async",
                        "caller",
                        "namespace",
                    ],
                )?;
                item_status(item, false)?;
                if item
                    .get("async")
                    .is_some_and(|v| !v.is_null() && v.as_bool() != Some(false))
                    || item.get("namespace").is_some_and(|v| !v.is_null())
                {
                    return Err("unsupported_response_field");
                }
                if let Some(caller) = item.get("caller").filter(|v| !v.is_null()) {
                    only_fields(caller, &["type"])?;
                    if string(caller, "type")? != "direct" {
                        return Err("unsupported_response_field");
                    }
                }
                let args =
                    strict_json(string(item, "arguments")?).map_err(|_| "invalid_arguments")?;
                // call_id correlates results. item.id identifies a replay item only.
                call(
                    reply,
                    string(item, "call_id")?,
                    string(item, "name")?,
                    &args,
                )?;
            }
            "reasoning" => {
                only_fields(
                    item,
                    &[
                        "type",
                        "id",
                        "summary",
                        "content",
                        "status",
                        "encrypted_content",
                    ],
                )?;
                item_status(item, false)?;
                reasoning_texts(array(item, "summary")?, "summary_text")?;
                if let Some(content) = item.get("content").filter(|v| !v.is_null()) {
                    reasoning_texts(
                        content.as_array().ok_or("invalid_response")?,
                        "reasoning_text",
                    )?;
                }
                match item.get("encrypted_content") {
                    Some(Value::String(s)) if !s.is_empty() => {}
                    None | Some(Value::Null) | Some(Value::String(_)) => {
                        return Err("missing_reasoning_state");
                    }
                    _ => return Err("invalid_response"),
                }
                // No decoding, logging or visible text from any reasoning field.
            }
            _ => return Err("unsupported_response_block"),
        }
    }
    reply.replay_items = output.clone();
    Ok(())
}

pub fn parse(provider: Provider, raw: &str, max_output_tokens: u64) -> Result<Reply, &'static str> {
    if raw.len() > 512 * 1024 {
        return Err("response_size_limit");
    }
    if max_output_tokens == 0 {
        return Err("invalid_token_limit");
    }
    let root = strict_json(raw).map_err(|_| "invalid_json")?;
    if !root.is_object() || root.get("error").is_some_and(|e| !e.is_null()) {
        return Err("invalid_response");
    }
    let mut reply = Reply {
        text: String::new(),
        tool_calls: Vec::new(),
        output_tokens: None,
        replay_items: Vec::new(),
    };
    let expects_calls;
    if provider == Provider::OpenaiResponses {
        parse_responses(&root, &mut reply, max_output_tokens)?;
        expects_calls = !reply.tool_calls.is_empty();
    } else if provider == Provider::Anthropic {
        optional_tag(&root, "type", "message")?;
        optional_tag(&root, "role", "assistant")?;
        expects_calls = match string(&root, "stop_reason")? {
            "tool_use" => true,
            "end_turn" | "stop_sequence" => false,
            "max_tokens" => return Err("truncated_response"),
            "refusal" => return Err("refused_response"),
            _ => return Err("unsupported_finish_reason"),
        };
        for block in root
            .get("content")
            .and_then(Value::as_array)
            .ok_or("invalid_response")?
        {
            match string(block, "type")? {
                "text" => reply.text.push_str(string(block, "text")?),
                "tool_use" => call(
                    &mut reply,
                    string(block, "id")?,
                    string(block, "name")?,
                    block.get("input").ok_or("invalid_arguments")?,
                )?,
                _ => return Err("unsupported_response_block"),
            }
        }
        reply.output_tokens = usage(&root, "output_tokens", max_output_tokens)?;
    } else {
        let choices = root
            .get("choices")
            .and_then(Value::as_array)
            .ok_or("invalid_response")?;
        if choices.len() != 1 {
            return Err("invalid_choices");
        }
        expects_calls = match string(&choices[0], "finish_reason")? {
            "tool_calls" => true,
            "stop" => false,
            "length" => return Err("truncated_response"),
            "content_filter" => return Err("refused_response"),
            _ => return Err("unsupported_finish_reason"),
        };
        let message = choices[0]
            .get("message")
            .filter(|m| m.is_object())
            .ok_or("invalid_response")?;
        optional_tag(message, "role", "assistant")?;
        if message.get("refusal").is_some_and(|r| !r.is_null()) {
            return Err("refused_response");
        }
        if ["function_call", "audio"]
            .iter()
            .any(|k| message.get(*k).is_some_and(|v| !v.is_null()))
        {
            return Err("unsupported_response_block");
        }
        match message.get("content") {
            Some(Value::String(text)) => reply.text.push_str(text),
            Some(Value::Null) => {}
            _ => return Err("invalid_response"),
        }
        if let Some(calls) = message.get("tool_calls").filter(|c| !c.is_null()) {
            for c in calls.as_array().ok_or("invalid_tool_call")? {
                if string(c, "type")? != "function" {
                    return Err("unsupported_response_block");
                }
                let f = c.get("function").ok_or("invalid_tool_call")?;
                let args = strict_json(string(f, "arguments")?).map_err(|_| "invalid_arguments")?;
                call(&mut reply, string(c, "id")?, string(f, "name")?, &args)?;
            }
        }
        reply.output_tokens = usage(&root, "completion_tokens", max_output_tokens)?;
    }
    if expects_calls != !reply.tool_calls.is_empty() {
        return Err("inconsistent_finish_reason");
    }
    if reply.tool_calls.is_empty() && reply.text.trim().is_empty() {
        return Err("empty_response");
    }
    let bytes = reply.text.len()
        + reply
            .tool_calls
            .iter()
            .map(|c| Value::Object(c.arguments.clone()).to_string().len())
            .sum::<usize>();
    // Replay has its own bound: encrypted state, annotations and raw argument
    // whitespace must not evade the visible/normalized-content budget.
    if bytes > 32 * 1024
        || serde_json::to_vec(&reply.replay_items)
            .map_err(|_| "invalid_response")?
            .len()
            > 32 * 1024
    {
        return Err("normalized_output_limit");
    }
    Ok(reply)
}

/// Encodes validated normalized content; text and call order are each preserved.
/// Reply intentionally cannot retain Anthropic text/tool interleaving positions.
/// Legacy-only API: Responses callers must use assistant_messages, not synthesize
/// a single assistant message and silently lose reasoning, phase or item ordering.
pub fn assistant_message(provider: Provider, reply: &Reply) -> Value {
    assert!(
        provider != Provider::OpenaiResponses,
        "Responses requires assistant_messages"
    );
    if provider == Provider::Anthropic {
        let mut blocks = Vec::new();
        if !reply.text.is_empty() || reply.tool_calls.is_empty() {
            blocks.push(json!({"type":"text", "text":reply.text}));
        }
        blocks.extend(
            reply
                .tool_calls
                .iter()
                .map(|c| json!({"type":"tool_use", "id":c.id, "name":c.name, "input":c.arguments})),
        );
        json!({"role":"assistant", "content":blocks})
    } else {
        let mut message = json!({"role":"assistant", "content":reply.text});
        if !reply.tool_calls.is_empty() {
            if reply.text.is_empty() {
                message["content"] = Value::Null;
            }
            message["tool_calls"] = Value::Array(
                reply
                    .tool_calls
                    .iter()
                    .map(|c| {
                        json!({"id":c.id, "type":"function", "function":{"name":c.name,
                    "arguments":Value::Object(c.arguments.clone()).to_string()}})
                    })
                    .collect(),
            );
        }
        message
    }
}

/// Convert a validated output item to its input-schema representation. Output
/// models allow null for these optional fields; input TypedDicts allow omission,
/// but not null. Never recursively strip nulls or rewrite opaque replay values.
fn responses_input_item(item: &Value) -> Value {
    let mut input = item.clone();
    let omit_if_null: &[&str] = match item.get("type").and_then(Value::as_str) {
        Some("function_call") => &["id", "status", "namespace", "async"],
        Some("reasoning") => &["content", "status"],
        _ => &[],
    };
    if let Some(fields) = input.as_object_mut() {
        for key in omit_if_null {
            if fields.get(*key).is_some_and(Value::is_null) {
                fields.remove(*key);
            }
        }
    }
    input
}

/// Responses items retain output order and values, except absent-equivalent nulls
/// forbidden by the input schema. Keep these in bounded internal history, not in
/// a serialized UI outcome or logs.
pub fn assistant_messages(provider: Provider, reply: &Reply) -> Vec<Value> {
    if provider == Provider::OpenaiResponses {
        reply
            .replay_items
            .iter()
            .map(responses_input_item)
            .collect()
    } else {
        vec![assistant_message(provider, reply)]
    }
}

pub fn tool_result_messages(provider: Provider, results: &[ToolResult]) -> Vec<Value> {
    if results.is_empty() {
        return Vec::new();
    }
    if provider == Provider::OpenaiResponses {
        results
            .iter()
            .map(|r| {
                json!({"type":"function_call_output", "call_id":r.call_id,
                "output":json!({"is_error":r.is_error,"output":r.output}).to_string()})
            })
            .collect()
    } else if provider == Provider::Anthropic {
        vec![json!({"role":"user", "content":results.iter().map(|r|
            json!({"type":"tool_result", "tool_use_id":r.call_id,
                "content":r.output.to_string(), "is_error":r.is_error})).collect::<Vec<_>>()})]
    } else {
        // Chat Completions has no status field, so preserve it in our JSON envelope.
        results
            .iter()
            .map(|r| {
                json!({"role":"tool", "tool_call_id":r.call_id,
            "content":json!({"is_error":r.is_error,"output":r.output}).to_string()})
            })
            .collect()
    }
}

pub fn build_request(
    provider: Provider,
    model: &str,
    messages: &[Value],
    tools: &[ToolDefinition],
    max_tokens: u64,
) -> Result<Value, &'static str> {
    if model.trim().is_empty()
        || model.len() > 200
        || model.chars().any(char::is_control)
        || messages.len() > 64
        || tools.len() > 16
        || !(1..=4096).contains(&max_tokens)
    {
        return Err("invalid_request_limits");
    }

    for (i, tool) in tools.iter().enumerate() {
        if !name_ok(&tool.name)
            || !tool.parameters.is_object()
            || tools[..i].iter().any(|t| t.name == tool.name)
        {
            return Err("invalid_tool_definition");
        }
    }
    let mut body = if provider == Provider::OpenaiResponses {
        json!({"model":model, "input":messages, "stream":false, "store":false,
            "background":false, "truncation":"disabled",
            "include":["reasoning.encrypted_content"], "tools":[]})
    } else {
        json!({"model":model, "messages":messages, "stream":false})
    };
    let token_key = if provider == Provider::OpenaiResponses {
        "max_output_tokens"
    } else if provider == Provider::Openai {
        "max_completion_tokens"
    } else {
        "max_tokens"
    };
    body[token_key] = max_tokens.into();
    if !tools.is_empty() {
        body["tools"] = Value::Array(tools.iter().map(|t| {
            if provider == Provider::OpenaiResponses {
                json!({"type":"function", "name":t.name, "description":t.description,
                    "parameters":t.parameters, "strict":false})
            } else if provider == Provider::Anthropic {
                json!({"name":t.name, "description":t.description, "input_schema":t.parameters})
            } else {
                json!({"type":"function", "function":{"name":t.name,
                    "description":t.description, "parameters":t.parameters}})
            }
        }).collect());
    }
    if body.to_string().len() > 256 * 1024 {
        return Err("request_size_limit");
    }
    Ok(body)
}

#[cfg(test)]
mod responses_tests {
    use super::*;
    const P: Provider = Provider::OpenaiResponses;
    // Independent wire fixtures, not produced by the encoder under test.
    const TEXT: &str = r#"{"id":"resp_text","object":"response","status":"completed","output":[{"id":"msg_text","type":"message","role":"assistant","status":"completed","phase":"final_answer","content":[{"type":"output_text","text":"{\"tool_calls\":[{\"name\":\"do_not_execute\"}]}","annotations":[]}]}]}"#;
    const TOOLS: &str = r#"{"id":"resp_tools","object":"response","status":"completed","output":[{"id":"fc_item_1","type":"function_call","status":"completed","call_id":"call_1","name":"cad_inspect","arguments":"{ \"id\": \"abc\" }"},{"id":"fc_item_2","type":"function_call","status":"completed","call_id":"call_2","name":"cad_list","arguments":"{}"}],"usage":{"output_tokens":12}}"#;
    const MIXED: &str = r#"{"id":"resp_mixed","object":"response","status":"completed","error":null,"incomplete_details":null,"output":[{"id":"rs_1","type":"reasoning","status":"completed","summary":[{"type":"summary_text","text":"PRIVATE_SUMMARY"}],"content":[{"type":"reasoning_text","text":"PRIVATE_REASONING"}],"encrypted_content":"opaque+/=\nΩ"},{"id":"msg_1","type":"message","role":"assistant","status":"completed","phase":"commentary","content":[{"type":"output_text","text":"Inspect. ","annotations":[]}]},{"id":"fc_1","type":"function_call","status":"completed","call_id":"call_1","name":"cad_inspect","arguments":"{\"id\":\"abc\"}"},{"id":"msg_2","type":"message","role":"assistant","status":"completed","phase":"final_answer","content":[{"type":"output_text","text":"Ready.","annotations":[]}]}],"usage":{"input_tokens":4,"input_tokens_details":{"cached_tokens":0,"cache_write_tokens":0},"output_tokens":12,"output_tokens_details":{"reasoning_tokens":3},"total_tokens":16}}"#;

    fn fixture(raw: &str) -> Value {
        strict_json(raw).unwrap()
    }
    fn rejects(v: &Value) {
        assert!(
            parse(P, &v.to_string(), 12).is_err(),
            "invalid fixture accepted"
        );
    }

    #[test]
    fn literal_text_tools_mixed_and_multiple() {
        let text = parse(P, TEXT, 12).unwrap();
        assert_eq!(text.text, r#"{"tool_calls":[{"name":"do_not_execute"}]}"#);
        assert!(text.tool_calls.is_empty());
        assert_eq!(text.output_tokens, None);
        let tools = parse(P, TOOLS, 12).unwrap();
        assert!(tools.text.is_empty());
        assert_eq!(tools.tool_calls.len(), 2);
        assert_eq!(tools.tool_calls[0].id, "call_1");
        assert_eq!(tools.tool_calls[1].id, "call_2");
        assert_eq!(tools.tool_calls[0].arguments["id"], "abc");
        assert_eq!(tools.output_tokens, Some(12));
        let mixed = parse(P, MIXED, 12).unwrap();
        assert_eq!(mixed.text, "Inspect. Ready.");
        assert_eq!(mixed.tool_calls.len(), 1);
        assert_eq!(mixed.tool_calls[0].id, "call_1");
        for raw in [TEXT, TOOLS, MIXED] {
            let r = parse(P, raw, 12).unwrap();
            let replay = assistant_messages(P, &r);
            assert!(
                replay == *fixture(raw)["output"].as_array().unwrap(),
                "replay changed"
            );
        }
    }

    #[test]
    fn single_tool_and_optional_schema_fields() {
        let raw = r#"{"id":"resp_1","object":"response","status":"completed","output":[{"type":"function_call","call_id":"call_1","name":"cad_list","arguments":"{}","async":false,"caller":{"type":"direct"},"namespace":null}]}"#;
        let r = parse(P, raw, 12).unwrap();
        assert_eq!(r.tool_calls.len(), 1);
        assert_eq!(r.tool_calls[0].id, "call_1");
        let mut nullable = fixture(raw);
        nullable["output"][0]["id"] = Value::Null;
        nullable["output"][0]["status"] = Value::Null;
        nullable["output"][0]["caller"] = Value::Null;
        nullable["output"][0]["async"] = Value::Null;
        assert!(parse(P, &nullable.to_string(), 12).is_ok());
        let mut reasoning = fixture(MIXED);
        reasoning["output"][0]
            .as_object_mut()
            .unwrap()
            .remove("status");
        reasoning["output"][0]["content"] = Value::Null;
        reasoning["output"][1]["phase"] = Value::Null;
        assert!(parse(P, &reasoning.to_string(), 12).is_ok());
    }

    #[test]
    fn test_output_nulls_are_omitted_only_where_input_schema_requires() {
        let raw = r#"{
            "id":"resp_nulls","object":"response","status":"completed","output":[
                {"type":"reasoning","id":"rs_nulls","summary":[],"content":null,
                 "status":null,"encrypted_content":"opaque+/=\nΩ"},
                {"type":"message","id":"msg_nulls","role":"assistant","status":"completed",
                 "phase":null,"content":[{"type":"output_text","text":"Inspect.","annotations":[]}]},
                {"type":"function_call","id":null,"status":null,"namespace":null,"async":null,
                 "caller":null,"call_id":"call_nulls","name":"cad_inspect",
                 "arguments":"{ \"nested\": {\"id\":null,\"status\":null} }"},
                {"type":"function_call","id":"fc_kept","status":"completed","async":false,
                 "caller":{"type":"direct"},"call_id":"call_kept","name":"cad_list","arguments":"{}"},
                {"type":"reasoning","id":"rs_kept","summary":[],"content":[],
                 "status":"completed","encrypted_content":"second-opaque"}
            ]
        }"#;
        let expected = fixture(
            r#"[
            {"type":"reasoning","id":"rs_nulls","summary":[],"encrypted_content":"opaque+/=\nΩ"},
            {"type":"message","id":"msg_nulls","role":"assistant","status":"completed",
             "phase":null,"content":[{"type":"output_text","text":"Inspect.","annotations":[]}]},
            {"type":"function_call","caller":null,"call_id":"call_nulls","name":"cad_inspect",
             "arguments":"{ \"nested\": {\"id\":null,\"status\":null} }"},
            {"type":"function_call","id":"fc_kept","status":"completed","async":false,
             "caller":{"type":"direct"},"call_id":"call_kept","name":"cad_list","arguments":"{}"},
            {"type":"reasoning","id":"rs_kept","summary":[],"content":[],
             "status":"completed","encrypted_content":"second-opaque"}
        ]"#,
        );
        let reply = parse(P, raw, 12).unwrap();
        let replay = assistant_messages(P, &reply);
        assert!(
            replay == *expected.as_array().unwrap(),
            "input canonicalization changed more than absent-equivalent nulls"
        );
        assert!(
            reply.replay_items == *fixture(raw)["output"].as_array().unwrap(),
            "input canonicalization changed stored output"
        );
        assert!(
            replay.iter().map(responses_input_item).collect::<Vec<_>>() == replay,
            "input canonicalization is not idempotent"
        );
        let body = build_request(P, "fixture-model", &replay, &[], 12).unwrap();
        assert!(body["input"] == expected, "request changed canonical input");
    }

    #[test]
    fn replay_keeps_phase_encrypted_content_and_raw_arguments_private() {
        let r = parse(P, MIXED, 12).unwrap();
        let replay = assistant_messages(P, &r);
        assert_eq!(replay[0]["encrypted_content"], "opaque+/=\nΩ");
        assert_eq!(replay[1]["phase"], "commentary");
        assert_eq!(replay[3]["phase"], "final_answer");
        let wire = serde_json::to_value(&r).unwrap();
        assert!(wire.get("replay_items").is_none());
        for private in [
            "encrypted_content",
            "opaque",
            "PRIVATE_SUMMARY",
            "PRIVATE_REASONING",
        ] {
            assert!(
                !wire.to_string().contains(private),
                "private replay leaked to serialization"
            );
            assert!(
                !format!("{r:?}").contains(private),
                "private replay leaked to Debug"
            );
            assert!(!r.text.contains(private));
        }
        let tools = parse(P, TOOLS, 12).unwrap();
        assert_eq!(
            assistant_messages(P, &tools)[0]["arguments"],
            "{ \"id\": \"abc\" }"
        );
        // Normalized UI deserialization cannot install unvalidated replay state.
        let old: Reply =
            serde_json::from_str(r#"{"text":"ok","tool_calls":[],"output_tokens":null}"#).unwrap();
        assert!(old.replay_items.is_empty());
        let injected: Reply = serde_json::from_str(r#"{"text":"ok","tool_calls":[],"output_tokens":null,"replay_items":[{"type":"shell_call"}]}"#).unwrap();
        assert!(injected.replay_items.is_empty());
    }

    #[test]
    fn stateless_reasoning_requires_encrypted_state_without_reconstruction() {
        for state in [None, Some(Value::Null), Some(json!(""))] {
            let mut v = fixture(MIXED);
            v["output"][0]
                .as_object_mut()
                .unwrap()
                .remove("encrypted_content");
            if let Some(state) = state {
                v["output"][0]["encrypted_content"] = state;
            }
            assert_eq!(
                parse(P, &v.to_string(), 12).unwrap_err(),
                "missing_reasoning_state"
            );
        }
        let mut v = fixture(MIXED);
        v["output"][0]["encrypted_content"] = json!({"ciphertext":"not-a-string"});
        rejects(&v);
        for (key, value) in [
            ("summary", json!(null)),
            ("summary", json!([{"type":"summary_text","text":false}])),
            (
                "summary",
                json!([{"type":"output_text","text":"not visible"}]),
            ),
            ("content", json!({})),
            (
                "content",
                json!([{"type":"reasoning_text","text":"hidden","execute":true}]),
            ),
        ] {
            let mut v = fixture(MIXED);
            v["output"][0][key] = value;
            rejects(&v);
        }
    }

    #[test]
    fn rejects_duplicate_item_and_call_ids_and_strict_arguments() {
        for raw in [
            TOOLS.replace("fc_item_2", "fc_item_1"),
            TOOLS.replace("call_2", "call_1"),
            TOOLS.replace(
                "\"call_id\":\"call_1\"",
                "\"call_id\":\"call_1\",\"call_id\":\"other\"",
            ),
            TOOLS.replace(
                "\"arguments\":\"{}\"",
                "\"arguments\":\"{\\\"x\\\":1,\\\"x\\\":2}\"",
            ),
            TOOLS.replace(
                "\"arguments\":\"{}\"",
                "\"arguments\":\"{\\\"x\\\":{\\\"y\\\":1,\\\"y\\\":2}}\"",
            ),
        ] {
            assert!(parse(P, &raw, 12).is_err(), "duplicate accepted");
        }
        // Distinct kinds must share the item-ID uniqueness check.
        let mut mixed = fixture(MIXED);
        mixed["output"][2]["id"] = json!("rs_1");
        rejects(&mixed);
        for args in [
            json!({}),
            json!("[]"),
            json!("null"),
            json!("{} trailing"),
            json!("{\"x\":NaN}"),
            json!("{\"x\":1e999}"),
        ] {
            let mut v = fixture(TOOLS);
            v["output"][0]["arguments"] = args;
            rejects(&v);
        }
        for key in ["id", "call_id", "name", "arguments"] {
            let mut v = fixture(TOOLS);
            v["output"][0][key] = json!(false);
            rejects(&v);
        }
        for key in ["call_id", "name", "arguments"] {
            let mut v = fixture(TOOLS);
            v["output"][0].as_object_mut().unwrap().remove(key);
            rejects(&v);
        }
        for id in [
            "".to_string(),
            "bad id".into(),
            "bad\nID".into(),
            "i".repeat(129),
        ] {
            for key in ["id", "call_id"] {
                let mut v = fixture(TOOLS);
                v["output"][0][key] = json!(id);
                rejects(&v);
            }
        }
        for name in [
            "".to_string(),
            "cad.inspect".into(),
            "bad name".into(),
            "é".into(),
            "a".repeat(129),
        ] {
            let mut v = fixture(TOOLS);
            v["output"][0]["name"] = json!(name);
            rejects(&v);
        }
    }

    #[test]
    fn completed_envelope_and_completed_items_only() {
        for status in [
            json!("failed"),
            json!("cancelled"),
            json!("queued"),
            json!("in_progress"),
            json!("incomplete"),
            json!("refused"),
            json!(null),
            json!(true),
        ] {
            let mut v = fixture(MIXED);
            v["status"] = status.clone();
            rejects(&v);
            for i in 0..4 {
                let mut v = fixture(MIXED);
                v["output"][i]["status"] = status.clone();
                // Reasoning/call status is optional and nullable in the SDK.
                if !status.is_null() || i == 1 || i == 3 {
                    rejects(&v);
                }
            }
        }
        for key in ["object", "status", "output", "id"] {
            let mut v = fixture(TEXT);
            v.as_object_mut().unwrap().remove(key);
            rejects(&v);
        }
        for (key, value) in [
            ("object", json!("chat.completion")),
            ("output", json!({})),
            ("output", json!([null])),
            ("output", json!([])),
            ("error", json!({"code":"server_error","message":"failed"})),
            ("incomplete_details", json!({"reason":"max_output_tokens"})),
            ("incomplete_details", json!({"reason":"content_filter"})),
            ("status", json!("response.completed")),
        ] {
            let mut v = fixture(TEXT);
            v[key] = value;
            rejects(&v);
        }
        let mut truncated = fixture(TOOLS);
        truncated["status"] = json!("incomplete");
        assert_eq!(
            parse(P, &truncated.to_string(), 12).unwrap_err(),
            "truncated_response"
        );
        // No partial success/calls can escape a refusal after a valid call.
        let mut refused = fixture(MIXED);
        refused["output"][3]["content"] = json!([{"type":"refusal","refusal":"No."}]);
        assert_eq!(
            parse(P, &refused.to_string(), 12).unwrap_err(),
            "refused_response"
        );
        let mut only_reasoning = fixture(MIXED);
        only_reasoning["output"].as_array_mut().unwrap().truncate(1);
        assert_eq!(
            parse(P, &only_reasoning.to_string(), 12).unwrap_err(),
            "empty_response"
        );
    }

    #[test]
    fn refuses_unknown_shapes_and_execution_changing_fields() {
        for kind in [
            "web_search_call",
            "file_search_call",
            "computer_call",
            "shell_call",
            "mcp_call",
            "program",
            "custom_tool_call",
            "function_call_output",
            "future_type",
        ] {
            let mut v = fixture(TOOLS);
            v["output"][1]["type"] = json!(kind);
            rejects(&v);
        }
        for (key, value) in [
            ("namespace", json!("other")),
            ("namespace", json!("")),
            ("async", json!(true)),
            ("async", json!(0)),
            ("caller", json!({"type":"program","caller_id":"program_1"})),
            ("caller", json!({"type":"direct","execute":true})),
            ("future_execution", json!(null)),
        ] {
            let mut v = fixture(TOOLS);
            v["output"][0][key] = value;
            rejects(&v);
        }
        for (key, value) in [
            ("role", json!("user")),
            ("phase", json!("analysis")),
            ("phase", json!(false)),
            ("status", json!(null)),
            ("content", json!("not blocks")),
            ("content", json!([{"type":"input_text","text":"x"}])),
            ("future", json!(true)),
        ] {
            let mut v = fixture(TEXT);
            v["output"][0][key] = value;
            rejects(&v);
        }
        for key in ["role", "status", "type", "id", "content"] {
            let mut v = fixture(TEXT);
            v["output"][0].as_object_mut().unwrap().remove(key);
            rejects(&v);
        }
        for (key, value) in [
            ("type", json!("reasoning_text")),
            ("text", json!(false)),
            ("annotations", json!(null)),
            ("logprobs", json!({})),
            ("future", json!(null)),
        ] {
            let mut v = fixture(TEXT);
            v["output"][0]["content"][0][key] = value;
            rejects(&v);
        }
        for key in ["type", "text", "annotations"] {
            let mut v = fixture(TEXT);
            v["output"][0]["content"][0]
                .as_object_mut()
                .unwrap()
                .remove(key);
            rejects(&v);
        }
        for (key, value) in [
            ("future", json!(null)),
            ("conversation", json!({"id":"conv_1"})),
            ("previous_response_id", json!("resp_old")),
            ("store", json!(true)),
            ("background", json!(true)),
            ("truncation", json!("auto")),
            ("moderation", json!({})),
            ("tool_usage", json!({})),
        ] {
            let mut v = fixture(TEXT);
            v[key] = value;
            rejects(&v);
        }
    }

    #[test]
    fn annotations_and_logprobs_are_validated_and_replayed_not_executed() {
        let mut v = fixture(TEXT);
        v["output"][0]["content"][0]["annotations"] = json!([
            {"type":"file_citation","file_id":"file_1","filename":"x","index":0},
            {"type":"url_citation","url":"https://example.invalid","title":"x","start_index":0,"end_index":1},
            {"type":"container_file_citation","container_id":"container_1","file_id":"file_1","filename":"x","start_index":0,"end_index":1},
            {"type":"file_path","file_id":"file_1","index":0}
        ]);
        v["output"][0]["content"][0]["logprobs"] = json!([
            {"token":"a","bytes":[97],"logprob":-0.2,"top_logprobs":[{"token":"b","bytes":[98],"logprob":-1.0}]}
        ]);
        let r = parse(P, &v.to_string(), 12).unwrap();
        assert!(assistant_messages(P, &r) == *v["output"].as_array().unwrap());
        for bad in [
            json!({"type":"future"}),
            json!({"type":"file_path","file_id":"file_1","index":true}),
            json!({"type":"url_citation","url":"x","title":"x","start_index":0.0,"end_index":1}),
        ] {
            let mut v = v.clone();
            v["output"][0]["content"][0]["annotations"] = json!([bad]);
            rejects(&v);
        }
        for bad in [
            json!({"token":"a","bytes":[256],"logprob":-0.2,"top_logprobs":[]}),
            json!({"token":"a","bytes":[97],"logprob":false,"top_logprobs":[]}),
            json!({"token":"a","bytes":[97],"logprob":-0.2,"top_logprobs":[{}]}),
        ] {
            let mut v = v.clone();
            v["output"][0]["content"][0]["logprobs"] = json!([bad]);
            rejects(&v);
        }
    }

    #[test]
    fn usage_zero_missing_nullable_strict_integer_and_budget() {
        let mut v = fixture(TOOLS);
        v["usage"]["output_tokens"] = json!(0);
        assert_eq!(parse(P, &v.to_string(), 12).unwrap().output_tokens, Some(0));
        v["usage"] = Value::Null;
        assert_eq!(parse(P, &v.to_string(), 12).unwrap().output_tokens, None);
        v.as_object_mut().unwrap().remove("usage");
        assert_eq!(parse(P, &v.to_string(), 12).unwrap().output_tokens, None);
        for n in [
            json!(-1),
            json!(true),
            json!(12.0),
            json!(null),
            json!("12"),
            json!(13),
        ] {
            let mut v = fixture(TOOLS);
            v["usage"]["output_tokens"] = n;
            rejects(&v);
        }
        for u in [json!({}), json!([]), json!(false)] {
            let mut v = fixture(TOOLS);
            v["usage"] = u;
            rejects(&v);
        }
        assert_eq!(parse(P, TOOLS, 11).unwrap_err(), "output_token_limit");
        assert_eq!(parse(P, TEXT, 0).unwrap_err(), "invalid_token_limit");
        let mut full = fixture(MIXED);
        full["usage"]["output_tokens_details"]["reasoning_tokens"] = json!(true);
        rejects(&full);
    }

    #[test]
    fn four_calls_arguments_raw_and_hidden_replay_limits() {
        let mut v = fixture(TOOLS);
        for (id, call_id) in [("fc_3", "call_3"), ("fc_4", "call_4")] {
            v["output"].as_array_mut().unwrap().push(json!({"type":"function_call",
                "id":id,"call_id":call_id,"name":"cad_list","arguments":"{}","status":"completed"}));
        }
        assert_eq!(parse(P, &v.to_string(), 12).unwrap().tool_calls.len(), 4);
        v["output"].as_array_mut().unwrap().push(json!({"type":"function_call",
            "id":"fc_5","call_id":"call_5","name":"cad_list","arguments":"{}","status":"completed"}));
        assert_eq!(parse(P, &v.to_string(), 12).unwrap_err(), "tool_call_limit");
        let mut args = fixture(TOOLS);
        args["output"][0]["arguments"] = json!({"x":"a".repeat(4088)}).to_string().into();
        assert!(parse(P, &args.to_string(), 12).is_ok());
        args["output"][0]["arguments"] = json!({"x":"a".repeat(4089)}).to_string().into();
        assert_eq!(
            parse(P, &args.to_string(), 12).unwrap_err(),
            "tool_call_limit"
        );
        args["output"][0]["arguments"] = format!("{{}}{}", " ".repeat(32 * 1024)).into();
        assert_eq!(
            parse(P, &args.to_string(), 12).unwrap_err(),
            "normalized_output_limit"
        );
        let mut hidden = fixture(MIXED);
        hidden["output"][0]["encrypted_content"] = json!("");
        let overhead = hidden["output"].to_string().len();
        hidden["output"][0]["encrypted_content"] = json!("x".repeat(32 * 1024 - overhead));
        assert!(parse(P, &hidden.to_string(), 12).is_ok());
        hidden["output"][0]["encrypted_content"] = json!("x".repeat(32 * 1024 - overhead + 1));
        assert_eq!(
            parse(P, &hidden.to_string(), 12).unwrap_err(),
            "normalized_output_limit"
        );
        for key in ["summary", "content"] {
            let mut v = fixture(MIXED);
            v["output"][0][key][0]["text"] = json!("é".repeat(16384));
            assert_eq!(
                parse(P, &v.to_string(), 12).unwrap_err(),
                "normalized_output_limit"
            );
        }
        let mut visible = fixture(TEXT);
        visible["output"][0]["content"][0]["text"] = json!("x".repeat(32 * 1024 + 1));
        assert_eq!(
            parse(P, &visible.to_string(), 12).unwrap_err(),
            "normalized_output_limit"
        );
        assert_eq!(
            parse(P, &" ".repeat(512 * 1024 + 1), 12).unwrap_err(),
            "response_size_limit"
        );
    }

    #[test]
    fn explicit_wire_provider_and_no_cross_protocol_parsing() {
        assert_eq!(serde_json::to_string(&P).unwrap(), "\"openai_responses\"");
        assert_eq!(
            serde_json::from_str::<Provider>("\"openai_responses\"").unwrap(),
            P
        );
        for p in [
            Provider::Openai,
            Provider::OpenaiCompatible,
            Provider::Anthropic,
        ] {
            assert!(parse(p, TEXT, 12).is_err());
        }
        for raw in [
            r#"{"choices":[{"finish_reason":"stop","message":{"content":"ok"}}]}"#,
            r#"{"stop_reason":"end_turn","content":[{"type":"text","text":"ok"}]}"#,
        ] {
            assert!(parse(P, raw, 12).is_err());
        }
    }

    #[test]
    #[should_panic(expected = "Responses requires assistant_messages")]
    fn old_single_message_helper_cannot_silently_discard_responses_state() {
        assistant_message(P, &parse(P, MIXED, 12).unwrap());
    }

    #[test]
    fn literal_request_retention_flat_tools_and_result_roundtrip() {
        let tools = [ToolDefinition {
            name: "cad_list".into(),
            description: "List".into(),
            parameters: json!({"type":"object","properties":{}}),
        }];
        let input = [json!({"role":"user","content":"Inspect"})];
        let body = build_request(P, "fixture-model", &input, &tools, 12).unwrap();
        let expected = fixture(
            r#"{"model":"fixture-model","input":[{"role":"user","content":"Inspect"}],"max_output_tokens":12,"stream":false,"store":false,"background":false,"truncation":"disabled","include":["reasoning.encrypted_content"],"tools":[{"type":"function","name":"cad_list","description":"List","parameters":{"type":"object","properties":{}},"strict":false}]}"#,
        );
        assert!(body == expected, "request shape changed");
        for key in [
            "messages",
            "max_tokens",
            "max_completion_tokens",
            "previous_response_id",
            "conversation",
            "instructions",
        ] {
            assert!(body.get(key).is_none());
        }
        assert_eq!(
            build_request(P, "m", &[], &[], 12).unwrap()["tools"],
            json!([])
        );
        let r = parse(P, TOOLS, 12).unwrap();
        let results = [
            ToolResult {
                call_id: r.tool_calls[0].id.clone(),
                output: json!({"id":"abc"}),
                is_error: false,
            },
            ToolResult {
                call_id: r.tool_calls[1].id.clone(),
                output: json!("unavailable"),
                is_error: true,
            },
        ];
        let outputs = tool_result_messages(P, &results);
        let expected = fixture(
            r#"[{"type":"function_call_output","call_id":"call_1","output":"{\"is_error\":false,\"output\":{\"id\":\"abc\"}}"},{"type":"function_call_output","call_id":"call_2","output":"{\"is_error\":true,\"output\":\"unavailable\"}"}]"#,
        );
        assert!(
            outputs == *expected.as_array().unwrap(),
            "result wire shape changed"
        );
        for (output, result) in outputs.iter().zip(&results) {
            assert_eq!(output["call_id"], result.call_id);
            let envelope = strict_json(output["output"].as_str().unwrap()).unwrap();
            assert_eq!(envelope["is_error"], result.is_error);
            assert_eq!(envelope["output"], result.output);
            assert!(output.get("id").is_none());
            assert!(output.get("tool_call_id").is_none());
        }
        let mut history = assistant_messages(P, &r);
        history.extend(outputs);
        let next = build_request(P, "fixture-model", &history, &tools, 12).unwrap();
        assert!(
            next["input"] == json!(history),
            "replay/result sequence changed"
        );
        assert_eq!(next["input"][0]["id"], "fc_item_1");
        assert_eq!(next["input"][2]["call_id"], "call_1");
        assert!(tool_result_messages(P, &[]).is_empty());
    }

    #[test]
    fn request_limits_and_local_tool_validation_unchanged() {
        let tool = ToolDefinition {
            name: "cad_list".into(),
            description: "List".into(),
            parameters: json!({"type":"object"}),
        };
        for name in ["", "cad.list", "bad name", "é"] {
            let mut bad = tool.clone();
            bad.name = name.into();
            assert_eq!(
                build_request(P, "m", &[], &[bad], 12).unwrap_err(),
                "invalid_tool_definition"
            );
        }
        let mut bad = tool.clone();
        bad.parameters = json!([]);
        assert!(build_request(P, "m", &[], &[bad], 12).is_err());
        assert!(build_request(P, "m", &[], &[tool.clone(), tool.clone()], 12).is_err());
        assert!(build_request(P, "m", &[], &vec![tool; 17], 12).is_err());
        assert!(build_request(P, "m", &vec![json!({}); 65], &[], 12).is_err());
        assert_eq!(
            build_request(P, "m", &[json!("x".repeat(256 * 1024))], &[], 12).unwrap_err(),
            "request_size_limit"
        );
        for cap in [0, 4097] {
            assert!(build_request(P, "m", &[], &[], cap).is_err());
        }
        for model in ["".to_string(), "m\n".into(), "m".repeat(201)] {
            assert!(build_request(P, &model, &[], &[], 12).is_err());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const OA: &str = r#"{"choices":[{"finish_reason":"tool_calls","message":{"role":"assistant","content":null,"tool_calls":[{"id":"c1","type":"function","function":{"name":"cad_inspect","arguments":"{\"id\":\"abc\"}"}},{"id":"c2","type":"function","function":{"name":"cad_list","arguments":"{}"}}]}}],"usage":{"completion_tokens":12}}"#;
    const AN: &str = r#"{"type":"message","role":"assistant","stop_reason":"tool_use","content":[{"type":"text","text":"Inspect."},{"type":"tool_use","id":"a1","name":"cad_inspect","input":{"id":"abc"}},{"type":"tool_use","id":"a2","name":"cad_list","input":{}}],"usage":{"output_tokens":12}}"#;
    const TEXT: &str =
        r#"{"choices":[{"finish_reason":"stop","message":{"content":"{\"tool_calls\":[]}"}}]}"#;

    #[test]
    fn literal_success_fixtures() {
        for p in [Provider::Openai, Provider::OpenaiCompatible] {
            let r = parse(p, OA, 12).unwrap();
            assert_eq!(
                r.tool_calls
                    .iter()
                    .map(|c| c.id.as_str())
                    .collect::<Vec<_>>(),
                ["c1", "c2"]
            );
            assert_eq!(r.tool_calls[0].arguments["id"], "abc");
            assert_eq!(r.output_tokens, Some(12));
            assert!(r.text.is_empty());
            let text = parse(p, TEXT, 12).unwrap();
            assert_eq!(text.text, "{\"tool_calls\":[]}");
            assert!(text.tool_calls.is_empty());
            assert_eq!(text.output_tokens, None);
        }
        let r = parse(Provider::Anthropic, AN, 12).unwrap();
        assert_eq!(r.text, "Inspect.");
        assert_eq!(r.tool_calls[1].id, "a2");
        let text = parse(
            Provider::Anthropic,
            r#"{"stop_reason":"end_turn","content":[{"type":"text","text":"ok"}]}"#,
            12,
        )
        .unwrap();
        assert_eq!(text.text, "ok");
        assert_eq!(text.output_tokens, None);
        assert!(parse(Provider::Anthropic,
            r#"{"stop_reason":"tool_use","content":[{"type":"tool_use","id":"a","name":"x","input":{}}]}"#, 12).unwrap().text.is_empty());
    }
    #[test]
    fn rejects_malformed_duplicates_and_partial_outputs() {
        for (p, raw) in [(Provider::Openai, OA), (Provider::Anthropic, AN)] {
            let stop = if p == Provider::Anthropic {
                "tool_use\",\"content"
            } else {
                "tool_calls\",\"message"
            };
            for reason in if p == Provider::Anthropic {
                ["max_tokens", "refusal", "end_turn", "unknown"]
            } else {
                ["length", "content_filter", "stop", "unknown"]
            } {
                let suffix = if p == Provider::Anthropic {
                    "content"
                } else {
                    "message"
                };
                let bad = raw.replacen(stop, &format!("{reason}\",\"{suffix}"), 1);
                assert!(parse(p, &bad, 12).is_err(), "finish reason accepted");
            }
            assert!(parse(p, raw, 11).is_err());
            for n in ["0", "-1", "true", "12.0", "null", "\"12\""] {
                assert!(parse(p, &raw.replace(":12", &format!(":{n}")), 12).is_err());
            }
            for name in ["cad.inspect", "", "bad name", "é", &"a".repeat(129)] {
                assert!(parse(p, &raw.replace("cad_inspect", name), 12).is_err());
            }
        }
        for bad in [
            OA.replace("c2", "c1"),
            OA.replace("c1", "bad id"),
            OA.replace("\"type\":\"function\"", "\"type\":\"custom\""),
            OA.replace("\"arguments\":\"{}\"", "\"arguments\":{}"),
            OA.replace("\"arguments\":\"{}\"", "\"arguments\":\"[]\""),
            OA.replace(
                "\"arguments\":\"{}\"",
                "\"arguments\":\"{\\\"x\\\":1,\\\"x\\\":2}\"",
            ),
            OA.replace("\"id\":\"c1\"", "\"id\":\"c1\",\"id\":\"c3\""),
        ] {
            assert!(parse(Provider::Openai, &bad, 12).is_err());
        }
        for bad in [
            AN.replace("a2", "a1"),
            AN.replace("\"input\":{}", "\"input\":[]"),
            AN.replace("\"input\":{}", "\"input\":{\"nested\":{\"x\":1,\"x\":2}}"),
            AN.replace("\"type\":\"text\"", "\"type\":\"thinking\""),
            AN.replace("\"type\":\"tool_use\"", "\"type\":\"server_tool_use\""),
        ] {
            assert!(parse(Provider::Anthropic, &bad, 12).is_err());
        }
        for raw in [
            "null",
            "[]",
            "{}",
            r#"{"choices":[]}"#,
            r#"{"choices":[{},{}]}"#,
            r#"{"choices":[{"finish_reason":"stop","message":{"content":[]}}]}"#,
        ] {
            assert!(parse(Provider::Openai, raw, 12).is_err());
        }
    }
    #[test]
    fn sizes_and_call_count() {
        let mut v: Value = serde_json::from_str(OA).unwrap();
        let calls = v["choices"][0]["message"]["tool_calls"]
            .as_array_mut()
            .unwrap();
        for id in ["c3", "c4"] {
            let mut c = calls[1].clone();
            c["id"] = id.into();
            calls.push(c);
        }
        assert_eq!(
            parse(Provider::Openai, &v.to_string(), 12)
                .unwrap()
                .tool_calls
                .len(),
            4
        );
        let mut bad = v.clone();
        let calls = bad["choices"][0]["message"]["tool_calls"]
            .as_array_mut()
            .unwrap();
        let mut c = calls[0].clone();
        c["id"] = "c5".into();
        calls.push(c);
        assert!(parse(Provider::Openai, &bad.to_string(), 12).is_err());
        v["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] =
            json!({"x":"a".repeat(4090)}).to_string().into();
        assert!(parse(Provider::Openai, &v.to_string(), 12).is_err());
        let mut text: Value = serde_json::from_str(TEXT).unwrap();
        text["choices"][0]["message"]["content"] = "é".repeat(16384).into();
        assert!(parse(Provider::Openai, &text.to_string(), 12).is_ok());
        text["choices"][0]["message"]["content"] = "é".repeat(16385).into();
        assert!(parse(Provider::Openai, &text.to_string(), 12).is_err());
        assert!(parse(Provider::Openai, &" ".repeat(512 * 1024 + 1), 12).is_err());
    }
    #[test]
    fn empty_and_nullable_calls() {
        for p in [Provider::Openai, Provider::OpenaiCompatible] {
            for raw in [
                r#"{"choices":[{"finish_reason":"stop","message":{"content":null}}]}"#,
                r#"{"choices":[{"finish_reason":"stop","message":{"content":" \t\n","tool_calls":null}}]}"#,
                r#"{"choices":[{"finish_reason":"stop","message":{"content":"","tool_calls":[]}}]}"#,
            ] {
                assert_eq!(parse(p, raw, 12).unwrap_err(), "empty_response");
            }
            let r = parse(p, r#"{"choices":[{"finish_reason":"stop","message":{"content":"ok","tool_calls":null}}]}"#, 12).unwrap();
            assert_eq!(r.text, "ok");
            assert!(r.tool_calls.is_empty());
            assert_eq!(parse(p, r#"{"choices":[{"finish_reason":"tool_calls","message":{"content":"ok","tool_calls":null}}]}"#, 12).unwrap_err(), "inconsistent_finish_reason");
            assert!(parse(p, r#"{"choices":[{"finish_reason":"stop","message":{"content":"ok","tool_calls":{}}}]}"#, 12).is_err());
            assert_eq!(parse(p, OA, 12).unwrap().tool_calls.len(), 2);
        }
        for raw in [
            r#"{"stop_reason":"end_turn","content":[]}"#,
            r#"{"stop_reason":"end_turn","content":[{"type":"text","text":" \t\n"}]}"#,
        ] {
            assert_eq!(
                parse(Provider::Anthropic, raw, 12).unwrap_err(),
                "empty_response"
            );
        }
    }

    #[test]
    fn result_status_and_strict_contract() {
        let r: ToolResult =
            serde_json::from_str(r#"{"call_id":"c","output":"unavailable","is_error":true}"#)
                .unwrap();
        for p in [Provider::Openai, Provider::OpenaiCompatible] {
            for is_error in [true, false] {
                let result = ToolResult {
                    is_error,
                    ..r.clone()
                };
                let messages = tool_result_messages(p, &[result]);
                let content: Value =
                    serde_json::from_str(messages[0]["content"].as_str().unwrap()).unwrap();
                let expected: Value = serde_json::from_str(if is_error {
                    r#"{"is_error":true,"output":"unavailable"}"#
                } else {
                    r#"{"is_error":false,"output":"unavailable"}"#
                })
                .unwrap();
                assert_eq!(content, expected);
                assert_eq!(messages[0]["tool_call_id"], "c");
            }
        }
        assert!(
            serde_json::from_str::<ToolResult>(
                r#"{"call_id":"c","output":null,"is_error":false,"extra":1}"#
            )
            .is_err()
        );
        assert!(serde_json::from_str::<ToolResult>(r#"{"call_id":"c","output":null}"#).is_err());
    }

    #[test]
    fn explicit_provider_not_model_name_selects_protocol() {
        let tools = [ToolDefinition {
            name: "inspect".into(),
            description: "Inspect".into(),
            parameters: json!({"type":"object"}),
        }];
        for model in [
            "gpt-6",
            "gpt-6-mini",
            "gpt-6-2026-10-28",
            "gpt-4.1",
            "other-gpt-6",
        ] {
            let chat = build_request(Provider::Openai, model, &[], &tools, 12).unwrap();
            assert!(chat.get("messages").is_some());
            assert!(chat.get("input").is_none());
            assert_eq!(chat["max_completion_tokens"], 12);
            let responses =
                build_request(Provider::OpenaiResponses, model, &[], &tools, 12).unwrap();
            assert!(responses.get("input").is_some());
            assert!(responses.get("messages").is_none());
            assert_eq!(responses["max_output_tokens"], 12);
            assert!(build_request(Provider::Openai, model, &[], &[], 12).is_ok());
            for p in [Provider::OpenaiCompatible, Provider::Anthropic] {
                assert!(build_request(p, model, &[], &tools, 12).is_ok());
            }
        }
        assert!(build_request(Provider::Openai, "gpt-4.1", &[], &tools, 12).is_ok());
    }

    #[test]
    fn wire_encoding_and_requests() {
        let results = vec![
            ToolResult {
                call_id: "c1".into(),
                output: json!({"error":"unavailable"}),
                is_error: true,
            },
            ToolResult {
                call_id: "c2".into(),
                output: json!({"count":2}),
                is_error: false,
            },
        ];
        assert_eq!(
            tool_result_messages(Provider::Openai, &results),
            json!([
            {"role":"tool","tool_call_id":"c1","content":"{\"is_error\":true,\"output\":{\"error\":\"unavailable\"}}"},
            {"role":"tool","tool_call_id":"c2","content":"{\"is_error\":false,\"output\":{\"count\":2}}"}])
            .as_array()
            .unwrap()
            .clone()
        );
        assert_eq!(tool_result_messages(Provider::Anthropic, &results), json!([
            {"role":"user","content":[{"type":"tool_result","tool_use_id":"c1","content":"{\"error\":\"unavailable\"}","is_error":true},
            {"type":"tool_result","tool_use_id":"c2","content":"{\"count\":2}","is_error":false}]}]).as_array().unwrap().clone());
        for (p, raw) in [
            (Provider::Openai, OA),
            (Provider::OpenaiCompatible, OA),
            (Provider::Anthropic, AN),
        ] {
            let r = parse(p, raw, 12).unwrap();
            let wire = assistant_message(p, &r);
            assert_eq!(assistant_messages(p, &r), vec![wire.clone()]);
            let original: Value = serde_json::from_str(raw).unwrap();
            if p == Provider::Anthropic {
                assert_eq!(wire["content"], original["content"]);
            } else {
                assert_eq!(wire, original["choices"][0]["message"]);
            }
            let tools = [ToolDefinition {
                name: "cad_inspect".into(),
                description: "Inspect".into(),
                parameters: json!({"type":"object","properties":{"id":{"type":"string"}}}),
            }];
            let body = build_request(p, "fixture-model", &[wire], &tools, 12).unwrap();
            assert_eq!(body["stream"], false);
            let key = if p == Provider::Openai {
                "max_completion_tokens"
            } else {
                "max_tokens"
            };
            assert_eq!(body[key], 12);
            assert!(
                body.get(if key == "max_tokens" {
                    "max_completion_tokens"
                } else {
                    "max_tokens"
                })
                .is_none()
            );
            if p == Provider::Anthropic {
                assert_eq!(body["tools"][0]["input_schema"], tools[0].parameters);
            } else {
                assert_eq!(body["tools"][0]["type"], "function");
                assert_eq!(
                    body["tools"][0]["function"]["parameters"],
                    tools[0].parameters
                );
                assert!(body["tools"][0]["function"].get("strict").is_none());
            }
            assert!(build_request(p, "m", &[], &vec![tools[0].clone(); 17], 12).is_err());
            assert!(build_request(p, "m", &vec![json!({}); 65], &[], 12).is_err());
            assert!(build_request(p, "m", &[json!("x".repeat(256 * 1024))], &[], 12).is_err());
            for cap in [0, 4097] {
                assert!(build_request(p, "m", &[], &[], cap).is_err());
            }
            assert!(build_request(p, &"m".repeat(201), &[], &[], 12).is_err());
            assert!(tool_result_messages(p, &[]).is_empty());
        }
        assert_eq!(
            serde_json::to_string(&Provider::OpenaiCompatible).unwrap(),
            "\"openai_compatible\""
        );
    }
}
