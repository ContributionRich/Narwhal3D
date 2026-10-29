//! OURS: bounded provider wire adapters, not authorization or execution.
//! OpenAI means Chat Completions, NOT Responses. Current GPT-6 tool calling
//! requires Responses and is unsupported here; callers must not assume all models
//! support this adapter. Session policy owns model capability checks and call/result IDs.
use crate::json::strict_json;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Provider {
    Openai,
    OpenaiCompatible,
    Anthropic,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub arguments: Map<String, Value>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Reply {
    pub text: String,
    pub tool_calls: Vec<ToolCall>,
    pub output_tokens: Option<u64>,
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
    };
    let expects_calls;
    if provider == Provider::Anthropic {
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
    if bytes > 32 * 1024 {
        return Err("normalized_output_limit");
    }
    Ok(reply)
}

/// Encodes validated normalized content; text and call order are each preserved.
/// Reply intentionally cannot retain Anthropic text/tool interleaving positions.
pub fn assistant_message(provider: Provider, reply: &Reply) -> Value {
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

pub fn tool_result_messages(provider: Provider, results: &[ToolResult]) -> Vec<Value> {
    if results.is_empty() {
        return Vec::new();
    }
    if provider == Provider::Anthropic {
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
    if provider == Provider::Openai && !tools.is_empty() && model.starts_with("gpt-6") {
        return Err("responses_api_required");
    }
    for (i, tool) in tools.iter().enumerate() {
        if !name_ok(&tool.name)
            || !tool.parameters.is_object()
            || tools[..i].iter().any(|t| t.name == tool.name)
        {
            return Err("invalid_tool_definition");
        }
    }
    let mut body = json!({"model":model, "messages":messages, "stream":false});
    let token_key = if provider == Provider::Openai {
        "max_completion_tokens"
    } else {
        "max_tokens"
    };
    body[token_key] = max_tokens.into();
    if !tools.is_empty() {
        body["tools"] = Value::Array(tools.iter().map(|t| {
            if provider == Provider::Anthropic {
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
    fn gpt6_tools_require_responses_only_for_openai() {
        let tools = [ToolDefinition {
            name: "inspect".into(),
            description: "Inspect".into(),
            parameters: json!({"type":"object"}),
        }];
        for model in ["gpt-6", "gpt-6-mini", "gpt-6-2026-10-28"] {
            assert_eq!(
                build_request(Provider::Openai, model, &[], &tools, 12).unwrap_err(),
                "responses_api_required"
            );
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
