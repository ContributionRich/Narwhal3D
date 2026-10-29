//! OURS: bounded, era-aware admission around SDK message/metadata types.
//! Client identity/capabilities describe protocol features, never read authorization.
use rmcp::model::*;
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use tokio_util::sync::CancellationToken;

pub const MAX_REQUESTS: usize = 4096;
pub const MAX_ACTIVE: usize = 16;
pub const VERSION: &str = "io.modelcontextprotocol/protocolVersion";
const CAPS: &str = "io.modelcontextprotocol/clientCapabilities";
const INFO: &str = "io.modelcontextprotocol/clientInfo";
const LOG: &str = "io.modelcontextprotocol/logLevel";

pub fn versions() -> Vec<ProtocolVersion> {
    vec![ProtocolVersion::V_2025_11_25, ProtocolVersion::V_2026_07_28]
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Era {
    Legacy,
    Modern,
}

pub struct Active {
    pub cancelled: CancellationToken,
    initialize: bool,
}

#[derive(Default)]
pub struct Gate {
    pub initializing: bool,
    pub initialized: bool,
    pub ready: bool,
    modern_operational: bool,
    // Legacy IDs cannot be laundered through a later modern request.
    seen: HashSet<String>,
    pub active: HashMap<String, Active>,
    admitted: usize,
}

fn invalid(message: &'static str) -> ErrorData {
    ErrorData::invalid_params(message, None)
}
fn request_error(message: &'static str) -> ErrorData {
    ErrorData::invalid_request(message, None)
}

fn key_valid(key: &str, require_prefix: bool) -> bool {
    let (prefix, name) = match key.split_once('/') {
        Some((prefix, name)) => (Some(prefix), name),
        None if !require_prefix => (None, key),
        None => return false,
    };
    if let Some(prefix) = prefix {
        if !prefix.split('.').all(|label| {
            let b = label.as_bytes();
            !b.is_empty()
                && b[0].is_ascii_alphabetic()
                && b[b.len() - 1].is_ascii_alphanumeric()
                && b.iter().all(|c| c.is_ascii_alphanumeric() || *c == b'-')
        }) {
            return false;
        }
    }
    let b = name.as_bytes();
    b.is_empty()
        || (b[0].is_ascii_alphanumeric()
            && b[b.len() - 1].is_ascii_alphanumeric()
            && b.iter()
                .all(|c| c.is_ascii_alphanumeric() || b"-_.".contains(c)))
}

/// Metadata values use SDK decoders, including optional-but-present fields.
/// Never fall back to legacy peer_info when a request attempts inline metadata.
fn modern_metadata(v: &Value) -> Result<(), ErrorData> {
    let raw = v["params"]
        .get("_meta")
        .ok_or_else(|| invalid("Required request metadata missing"))?;
    let meta: RequestMetaObject =
        serde_json::from_value(raw.clone()).map_err(|_| invalid("Invalid request metadata"))?;
    if !meta
        .missing_required_keys(&ProtocolVersion::V_2026_07_28)
        .is_empty()
    {
        return Err(invalid("Required request metadata missing or invalid"));
    }
    if (meta.contains_key(INFO) && meta.client_info().is_none())
        || (meta.contains_key(LOG) && meta.log_level().is_none())
        || meta.keys().any(|k| !key_valid(k, false))
    {
        return Err(invalid("Invalid request metadata"));
    }
    // SDK Option fields accept JSON null, but present capability declarations
    // must be objects under the protocol schema, not absent-by-deserialization.
    let raw_caps = meta.get(CAPS).unwrap();
    if [
        "experimental",
        "extensions",
        "roots",
        "sampling",
        "elicitation",
    ]
    .iter()
    .any(|key| raw_caps.get(*key).is_some_and(|value| !value.is_object()))
    {
        return Err(invalid("Invalid capability declaration"));
    }
    if [
        "/sampling/tools",
        "/sampling/context",
        "/elicitation/form",
        "/elicitation/url",
    ]
    .iter()
    .any(|path| raw_caps.pointer(path).is_some_and(|v| !v.is_object()))
        || ["/roots/listChanged", "/elicitation/form/schemaValidation"]
            .iter()
            .any(|path| raw_caps.pointer(path).is_some_and(|v| !v.is_boolean()))
    {
        return Err(invalid("Invalid nested capability declaration"));
    }
    let capabilities = meta.client_capabilities().unwrap();
    if let Some(extensions) = capabilities.extensions {
        if extensions.keys().any(|key| !key_valid(key, true)) {
            return Err(invalid("Invalid extension identifier"));
        }
    }
    let version = meta.protocol_version().unwrap();
    if version == ProtocolVersion::V_2025_11_25 {
        return Err(invalid(
            "Legacy protocol requires initialize, not inline version metadata",
        ));
    }
    if version != ProtocolVersion::V_2026_07_28 {
        return Err(ErrorData::unsupported_protocol_version(
            version,
            &versions(),
        ));
    }
    Ok(())
}

impl Gate {
    pub fn sdk_running(&self) -> bool {
        self.initialized || self.modern_operational
    }

    pub fn era(&self, v: &Value) -> Era {
        let method = v["method"].as_str().unwrap_or("");
        let meta = v["params"].get("_meta");
        let inline = meta.is_some_and(|m| {
            !m.is_object()
                || [VERSION, CAPS, INFO, LOG]
                    .iter()
                    .any(|k| m.get(*k).is_some())
        });
        if method == "initialize" && !inline {
            Era::Legacy
        } else if method == "server/discover" || inline || (!self.ready && !self.initializing) {
            Era::Modern
        } else {
            Era::Legacy
        }
    }

    pub fn check(&mut self, v: &Value) -> Result<(), ErrorData> {
        let object = v
            .as_object()
            .ok_or_else(|| request_error("Expected one request object"))?;
        if object
            .keys()
            .any(|k| !["jsonrpc", "id", "method", "params"].contains(&k.as_str()))
            || v["jsonrpc"] != "2.0"
            || !v["method"].is_string()
        {
            return Err(request_error("Invalid request envelope"));
        }
        if let Some(id) = object.get("id") {
            if !(id.is_i64() || id.as_str().is_some_and(|s| s.len() <= 128)) {
                return Err(request_error("Invalid request ID"));
            }
        }
        if object.contains_key("params") && !v["params"].is_object() {
            return Err(invalid("Expected object parameters"));
        }
        let method = v["method"].as_str().unwrap();
        if !object.contains_key("id") {
            return self.notification(method, v);
        }
        let era = self.era(v);
        if era == Era::Modern {
            modern_metadata(v)?;
        }
        if method == "initialize" {
            if era != Era::Legacy {
                return Err(invalid("Initialize is only available to legacy clients"));
            }
            if self.initializing || self.modern_operational {
                return Err(request_error(
                    "Legacy initialization already selected or modern operations started",
                ));
            }
        } else if era == Era::Legacy && !self.ready {
            return Err(request_error("Initialize and notify initialized first"));
        }
        if ![
            "initialize",
            "server/discover",
            "ping",
            "tools/list",
            "tools/call",
        ]
        .contains(&method)
            || (era == Era::Modern && method == "ping")
        {
            return Err(ErrorData::new(
                ErrorCode::METHOD_NOT_FOUND,
                "Method not supported",
                None,
            ));
        }
        if let Some(params) = v["params"].as_object() {
            let fields: &[&str] = match method {
                "tools/call" => &["name", "arguments", "_meta"],
                "tools/list" => &["cursor", "_meta"],
                "server/discover" | "ping" => &["_meta"],
                _ => &["protocolVersion", "capabilities", "clientInfo", "_meta"],
            };
            if params.keys().any(|k| !fields.contains(&k.as_str())) {
                return Err(invalid("Unsupported method parameters"));
            }
        }
        // Validate SDK variants explicitly: ClientRequest's catch-all must not turn
        // malformed known requests into successfully admitted CustomRequests.
        match method {
            "initialize" => {
                serde_json::from_value::<InitializeRequestParams>(v["params"].clone())
                    .map_err(|_| invalid("Invalid initialization parameters"))?;
            }
            "server/discover" => {
                serde_json::from_value::<DiscoverRequest>(v.clone())
                    .map_err(|_| invalid("Invalid discovery parameters"))?;
            }
            "tools/call" => {
                serde_json::from_value::<CallToolRequestParams>(v["params"].clone())
                    .map_err(|_| invalid("Invalid tool parameters"))?;
            }
            "tools/list" if object.contains_key("params") => {
                serde_json::from_value::<PaginatedRequestParams>(v["params"].clone())
                    .map_err(|_| invalid("Invalid pagination parameters"))?;
            }
            _ => {}
        }
        let id = v["id"].to_string();
        if self.active.contains_key(&id) {
            return Err(request_error("Request ID is still outstanding"));
        }
        if self.seen.contains(&id) {
            return Err(request_error("Legacy request ID already used"));
        }
        if self.admitted >= MAX_REQUESTS || self.active.len() >= MAX_ACTIVE {
            // Application-defined, outside JSON-RPC/MCP's reserved error range.
            return Err(ErrorData::new(
                ErrorCode(-31999),
                "Transport request limit reached",
                None,
            ));
        }
        if era == Era::Legacy {
            self.seen.insert(id.clone());
        }
        self.active.insert(
            id,
            Active {
                cancelled: CancellationToken::new(),
                initialize: method == "initialize",
            },
        );
        self.admitted += 1;
        if method == "initialize" {
            self.initializing = true;
        }
        if era == Era::Modern && method != "server/discover" {
            self.modern_operational = true;
        }
        Ok(())
    }

    fn notification(&mut self, method: &str, v: &Value) -> Result<(), ErrorData> {
        match method {
            "notifications/initialized" if self.initialized => self.ready = true,
            "notifications/cancelled" => {
                let params = v["params"]
                    .as_object()
                    .ok_or_else(|| invalid("Invalid cancellation"))?;
                if params
                    .keys()
                    .any(|k| !["requestId", "reason", "_meta"].contains(&k.as_str()))
                    || !(v["params"]["requestId"].is_i64() || v["params"]["requestId"].is_string())
                    || params.get("reason").is_some_and(|r| !r.is_string())
                    || params.get("_meta").is_some_and(|m| !m.is_object())
                {
                    return Err(invalid("Invalid cancellation"));
                }
                let id = v["params"]["requestId"].to_string();
                let active = self
                    .active
                    .get(&id)
                    .ok_or_else(|| invalid("Unknown cancellation"))?;
                if active.initialize {
                    return Err(invalid("Cannot cancel initialize"));
                }
                active.cancelled.cancel();
                // No response was received: quarantine this ID until reconnect.
                // Releasing here would race rmcp's ID-keyed cancellation/response pool.
            }
            _ => return Err(invalid("Invalid notification")),
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn modern(id: i32) -> Value {
        json!({"jsonrpc":"2.0","id":id,"method":"tools/list","params":{"_meta":{
            "io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}})
    }
    #[test]
    fn completed_ids_reuse_but_active_and_legacy_ids_do_not() {
        let mut gate = Gate::default();
        assert!(gate.check(&modern(1)).is_ok());
        assert!(gate.check(&modern(1)).is_err());
        gate.active.remove("1");
        assert!(gate.check(&modern(1)).is_ok());
        gate.active.remove("1");
        gate.seen.insert("1".into());
        assert!(gate.check(&modern(1)).is_err());
    }
    #[test]
    fn malformed_metadata_never_inherits_legacy_context() {
        let mut gate = Gate {
            ready: true,
            ..Gate::default()
        };
        let mut value = modern(1);
        value["params"]["_meta"][VERSION] = Value::Null;
        assert_eq!(
            gate.check(&value).unwrap_err().code,
            ErrorCode::INVALID_PARAMS
        );
        value["params"]["_meta"] = json!({CAPS:{}});
        assert_eq!(
            gate.check(&value).unwrap_err().code,
            ErrorCode::INVALID_PARAMS
        );
        assert!(gate.active.is_empty());
    }
    #[test]
    fn extension_identifiers_follow_meta_prefix_rules() {
        assert!(key_valid("com.example/feature", true));
        for bad in [
            "feature",
            "/feature",
            "com..example/x",
            "9com.example/x",
            "com.example/x/y",
        ] {
            assert!(!key_valid(bad, true), "{bad}");
        }
    }
}
