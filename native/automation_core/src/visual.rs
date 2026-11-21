//! OURS: bounded, typed validation and provider message construction for an inline
//! viewport JPEG artifact. This is bounded transport-shape validation only. It is
//! NOT image decoding, NOT JPEG parsing, and NOT geometry validation. The payload
//! is inline base64 (no external URLs or file paths are ever carried), and nothing
//! here asserts that the displayed geometry is valid (`geometry_validity_verified`
//! is always `false`).
//!
//! The exact output contract is a JSON object with these and only these fields:
//!   { "kind":"viewport_image", "mime_type":"image/jpeg", "data":<base64>,
//!     "width":<u32>, "height":<u32>, "scope":"active_viewport_only",
//!     "geometry_validity_verified":false }
//! Validation: width and height in 1..=320; data is strict base64 of length
//! 16..=12288, a multiple of 4, at most 2 trailing padding '=' (only at the end),
//! alphabet A-Z a-z 0-9 + /, and starts with the JPEG SOI marker "/9j/".
//!
//! No new dependencies: only serde_json (already a crate dependency) is used.
//! The parent owns native tool authorization/consent and UI capture, and appends
//! this image user message after all correlated tool results so tool correlation
//! is retained.

use crate::provider::Provider;
use serde_json::{Value, json};

/// Local-snapshot provenance text carried with the image. Explicitly a display
/// image with no geometry-validity claim.
const PROVENANCE: &str = "Local snapshot of the active viewport (active_viewport_only). \
                          Display image only; it is not geometry and carries no \
                          geometry-validity claim (geometry_validity_verified=false).";

/// The exact allowed field set. Any other key, or a missing one, is rejected.
const FIELDS: [&str; 7] = [
    "kind",
    "mime_type",
    "data",
    "width",
    "height",
    "scope",
    "geometry_validity_verified",
];

/// Strict base64 shape check for a bounded inline JPEG payload.
fn jpeg_base64(data: &str) -> bool {
    let n = data.len();
    if !(16..=12288).contains(&n) || n % 4 != 0 || !data.starts_with("/9j/") {
        return false;
    }
    let pad = data.bytes().rev().take_while(|b| *b == b'=').count();
    if pad > 2 {
        return false;
    }
    data[..n - pad]
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || b == b'+' || b == b'/')
}

/// Strictly validate the exact viewport-image contract. Unknown or missing fields,
/// wrong types, wrong constants, or an out-of-bounds/invalid payload all yield the
/// single error `invalid_viewport_image`.
pub fn validate_output(output: &Value) -> Result<(), &'static str> {
    let obj = output.as_object().ok_or("invalid_viewport_image")?;
    if obj.len() != FIELDS.len() || !FIELDS.iter().copied().all(|f| obj.contains_key(f)) {
        return Err("invalid_viewport_image");
    }
    if obj["kind"].as_str() != Some("viewport_image") {
        return Err("invalid_viewport_image");
    }
    if obj["mime_type"].as_str() != Some("image/jpeg") {
        return Err("invalid_viewport_image");
    }
    if obj["scope"].as_str() != Some("active_viewport_only") {
        return Err("invalid_viewport_image");
    }
    if obj["geometry_validity_verified"].as_bool() != Some(false) {
        return Err("invalid_viewport_image");
    }
    let width = obj["width"].as_u64().ok_or("invalid_viewport_image")?;
    let height = obj["height"].as_u64().ok_or("invalid_viewport_image")?;
    if !(1..=320).contains(&width) || !(1..=320).contains(&height) {
        return Err("invalid_viewport_image");
    }
    let data = obj["data"].as_str().ok_or("invalid_viewport_image")?;
    if !jpeg_base64(data) {
        return Err("invalid_viewport_image");
    }
    Ok(())
}

/// A redacted, total summary: always strips the `data` payload, always marks the
/// artifact as visual-only, and never asserts geometry validity. Projects width,
/// height and mime_type only when they are valid.
pub fn summary(output: &Value) -> Value {
    let mut out = json!({
        "kind": "viewport_image",
        "scope": "active_viewport_only",
        "geometry_validity_verified": false,
        "visual_only": true,
    });
    if let Some(obj) = output.as_object() {
        if let Some(w) = obj
            .get("width")
            .and_then(Value::as_u64)
            .filter(|w| (1..=320).contains(w))
        {
            out["width"] = json!(w);
        }
        if let Some(h) = obj
            .get("height")
            .and_then(Value::as_u64)
            .filter(|h| (1..=320).contains(h))
        {
            out["height"] = json!(h);
        }
        if let Some(m) = obj.get("mime_type").and_then(Value::as_str) {
            if !m.is_empty() && m.len() <= 128 {
                out["mime_type"] = json!(m);
            }
        }
    }
    out
}

/// Validate the contract and build the provider-specific user message that carries
/// the inline image plus the local-snapshot provenance text. Tool results remain the
/// normal correlated text; the parent appends this image message after them.
pub fn image_message(provider: Provider, output: &Value) -> Result<Value, &'static str> {
    validate_output(output)?;
    let data = output["data"].as_str().ok_or("invalid_viewport_image")?;
    let url = format!("data:image/jpeg;base64,{}", data);
    Ok(match provider {
        Provider::Openai | Provider::OpenaiCompatible => json!({
            "role": "user",
            "content": [
                {"type": "text", "text": PROVENANCE},
                {"type": "image_url", "image_url": {"url": url, "detail": "low"}},
            ],
        }),
        Provider::OpenaiResponses => json!({
            "role": "user",
            "content": [
                {"type": "input_text", "text": PROVENANCE},
                {"type": "input_image", "image_url": url, "detail": "low"},
            ],
        }),
        Provider::Anthropic => json!({
            "role": "user",
            "content": [
                {"type": "text", "text": PROVENANCE},
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
            ],
        }),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    // Deterministic synthetic fixture: JPEG SOI marker "/9j/" followed by fixed
    // alphabet chars. 16 bytes, multiple of 4, no padding. Not a real image/evidence.
    const FIXTURE: &str = "/9j/QUJDREVGR0hJ";

    fn output(data: &str, width: u32, height: u32) -> Value {
        json!({
            "kind": "viewport_image",
            "mime_type": "image/jpeg",
            "data": data,
            "width": width,
            "height": height,
            "scope": "active_viewport_only",
            "geometry_validity_verified": false
        })
    }

    fn valid() -> Value {
        output(FIXTURE, 160, 120)
    }

    fn mutate(v: Value, key: &str, val: Value) -> Value {
        let mut v = v;
        v[key] = val;
        v
    }

    #[test]
    fn dimension_bounds_and_types() {
        assert_eq!(validate_output(&valid()), Ok(()));
        assert!(validate_output(&output(FIXTURE, 1, 1)).is_ok());
        assert!(validate_output(&output(FIXTURE, 320, 320)).is_ok());
        for bad in [0u32, 321, 4096] {
            assert_eq!(
                validate_output(&output(FIXTURE, bad, 160)),
                Err("invalid_viewport_image")
            );
            assert_eq!(
                validate_output(&output(FIXTURE, 160, bad)),
                Err("invalid_viewport_image")
            );
        }
        for val in [json!(1.5), json!("160"), Value::Null, json!(-1)] {
            assert_eq!(
                validate_output(&mutate(valid(), "width", val)),
                Err("invalid_viewport_image")
            );
        }
    }

    #[test]
    fn base64_alphabet_padding_and_prefix() {
        assert!(validate_output(&valid()).is_ok());
        for bad in [
            "QUB/QUJDREVGR0hJ",  // wrong prefix (no /9j/)
            "/9j/QU=JDREVGR0hJ", // '=' in the middle
            "/9j/QUJD-!EVGR0h",  // '-' and '!' not in alphabet
            "/9j/QUJDéEVGR0hJ",  // non-ascii
            "/9j/QUJDQUJDA===",  // 3 trailing padding
        ] {
            assert_eq!(
                validate_output(&output(bad, 160, 120)),
                Err("invalid_viewport_image"),
                "{bad}"
            );
        }
    }

    #[test]
    fn data_length_bounds() {
        assert!(validate_output(&output("/9j/QUJDQUJDQUJD", 160, 120)).is_ok()); // 16 ok
        let big = format!("/9j/{}", "QUJD".repeat(3073)); // 12296 > 12288
        assert_eq!(
            validate_output(&output(&big, 160, 120)),
            Err("invalid_viewport_image")
        );
        for bad in ["/9j/QUJDQUJD", "/9j/QUJDREVGR0hJQ"] {
            assert_eq!(
                validate_output(&output(bad, 160, 120)),
                Err("invalid_viewport_image"),
                "{bad}"
            );
        }
        let max = format!("/9j/{}", "QUJD".repeat(3071)); // 12288 ok
        assert!(validate_output(&output(&max, 160, 120)).is_ok());
    }

    #[test]
    fn exact_field_set_denies_unknown() {
        assert!(validate_output(&valid()).is_ok());
        let v = mutate(valid(), "extra", json!(1));
        assert_eq!(validate_output(&v), Err("invalid_viewport_image"));
        for f in FIELDS {
            let mut v = valid();
            v.as_object_mut().unwrap().remove(f);
            assert_eq!(validate_output(&v), Err("invalid_viewport_image"), "{f}");
        }
        assert_eq!(validate_output(&json!([])), Err("invalid_viewport_image"));
    }

    #[test]
    fn contract_constants_and_no_validity_claim() {
        for (key, val) in [
            ("kind", json!("other")),
            ("mime_type", json!("image/png")),
            ("scope", json!("whole_scene")),
            ("geometry_validity_verified", json!(true)),
        ] {
            assert_eq!(
                validate_output(&mutate(valid(), key, val)),
                Err("invalid_viewport_image"),
                "{key}"
            );
        }
    }

    #[test]
    fn rejects_urls_and_paths() {
        for bad in [
            "https://example.com/viewport.jpg",
            "/tmp/viewport.jpg",
            "C:\\shots\\viewport.jpg",
        ] {
            assert_eq!(
                validate_output(&output(bad, 160, 120)),
                Err("invalid_viewport_image"),
                "{bad}"
            );
        }
    }

    #[test]
    fn summary_strips_data_and_marks_visual_only() {
        let s = summary(&valid());
        assert!(s.get("data").is_none());
        assert_eq!(s["width"], json!(160));
        assert_eq!(s["height"], json!(120));
        assert_eq!(s["mime_type"], json!("image/jpeg"));
        assert_eq!(s["geometry_validity_verified"], json!(false));
        assert_eq!(s["visual_only"], json!(true));
    }

    fn text_of(msg: &Value) -> Option<&str> {
        msg["content"]
            .as_array()?
            .iter()
            .find_map(|c| c.get("text").and_then(Value::as_str))
    }

    #[test]
    fn openai_and_compatible_messages() {
        for p in [Provider::Openai, Provider::OpenaiCompatible] {
            let m = image_message(p, &valid()).unwrap();
            assert_eq!(m["role"], json!("user"));
            let c = m["content"].as_array().unwrap();
            assert_eq!(c[0]["type"], json!("text"));
            assert!(text_of(&m).unwrap().contains("active_viewport_only"));
            assert!(text_of(&m).unwrap().contains("no geometry-validity claim"));
            assert_eq!(c[1]["type"], json!("image_url"));
            assert_eq!(
                c[1]["image_url"]["url"]
                    .as_str()
                    .unwrap()
                    .strip_prefix("data:image/jpeg;base64,"),
                Some(FIXTURE)
            );
            assert_eq!(c[1]["image_url"]["detail"], json!("low"));
        }
    }

    #[test]
    fn responses_message() {
        let m = image_message(Provider::OpenaiResponses, &valid()).unwrap();
        let c = m["content"].as_array().unwrap();
        assert_eq!(c[0]["type"], json!("input_text"));
        assert_eq!(c[1]["type"], json!("input_image"));
        assert_eq!(
            c[1]["image_url"]
                .as_str()
                .unwrap()
                .strip_prefix("data:image/jpeg;base64,"),
            Some(FIXTURE)
        );
        assert_eq!(c[1]["detail"], json!("low"));
    }

    #[test]
    fn anthropic_message() {
        let m = image_message(Provider::Anthropic, &valid()).unwrap();
        let c = m["content"].as_array().unwrap();
        assert_eq!(c[0]["type"], json!("text"));
        assert_eq!(c[1]["type"], json!("image"));
        assert_eq!(c[1]["source"]["type"], json!("base64"));
        assert_eq!(c[1]["source"]["media_type"], json!("image/jpeg"));
        assert_eq!(c[1]["source"]["data"], json!(FIXTURE));
    }

    #[test]
    fn image_message_rejects_invalid_for_all_providers() {
        let bad = mutate(valid(), "geometry_validity_verified", json!(true));
        for p in [
            Provider::Openai,
            Provider::OpenaiCompatible,
            Provider::OpenaiResponses,
            Provider::Anthropic,
        ] {
            assert_eq!(image_message(p, &bad), Err("invalid_viewport_image"));
        }
    }
}
