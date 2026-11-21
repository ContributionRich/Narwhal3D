//! Bounded argument validation and numeric summarization for generative
//! curve inspection.
//!
//! The host SDK generates the actual point-to-exact-wire distances. This
//! module only validates provider-supplied arguments and summarizes already
//! computed finite samples; it never generates or approximates authoritative
//! geometry, and the sampled maximum is NOT a certified Hausdorff/global bound.

use serde_json::{Map, Value, json};

const COUNT_MIN: u64 = 8;
const COUNT_MAX: u64 = 64;
const DISTANCE_MAX: f64 = 1e12;

fn uuid_schema() -> Value {
    json!({"type":"string","format":"uuid","minLength":36,"maxLength":36})
}
fn count_schema() -> Value {
    json!({"type":"integer","minimum":8,"maximum":64})
}
fn fraction_schema() -> Value {
    json!({"type":"number","minimum":0,"maximum":1})
}

pub fn samples_schema() -> Value {
    json!({"type":"object","properties":{"object_id":uuid_schema(),"sample_count":count_schema(),"from_fraction":fraction_schema(),"to_fraction":fraction_schema()},"required":["object_id","sample_count","from_fraction","to_fraction"],"additionalProperties":false})
}

pub fn compare_schema() -> Value {
    json!({"type":"object","properties":{"left":uuid_schema(),"right":uuid_schema(),"sample_count":count_schema()},"required":["left","right","sample_count"],"additionalProperties":false})
}

fn uuid_arg(args: &Map<String, Value>, key: &str) -> bool {
    args.get(key)
        .and_then(Value::as_str)
        .is_some_and(crate::catalog::uuid)
}

/// Integer only (bool is not an integer in this contract; `as_u64` rejects it).
fn count_arg(args: &Map<String, Value>, key: &str) -> Option<u64> {
    args.get(key)
        .and_then(Value::as_u64)
        .filter(|n| (COUNT_MIN..=COUNT_MAX).contains(n))
}

fn fraction_arg(args: &Map<String, Value>, key: &str) -> Option<f64> {
    args.get(key)
        .and_then(Value::as_f64)
        .filter(|f| f.is_finite() && (0.0..=1.0).contains(f))
}

pub fn validate_samples(args: &Map<String, Value>) -> Result<(), &'static str> {
    let (from, to) = (
        fraction_arg(args, "from_fraction"),
        fraction_arg(args, "to_fraction"),
    );
    if args.len() != 4
        || !uuid_arg(args, "object_id")
        || count_arg(args, "sample_count").is_none()
        || from.is_none()
        || to.is_none()
        || !(from < to)
    {
        return Err("invalid_sample_arguments");
    }
    Ok(())
}

pub fn validate_compare(args: &Map<String, Value>) -> Result<(), &'static str> {
    if args.len() != 3
        || !uuid_arg(args, "left")
        || !uuid_arg(args, "right")
        || count_arg(args, "sample_count").is_none()
    {
        return Err("invalid_compare_arguments");
    }
    Ok(())
}

/// Summarize finite, nonnegative distances the host SDK computed between
/// sampled points and exact wires. `sample_count` is the total of both arrays.
pub fn summarize_distances(forward: &[f64], reverse: &[f64]) -> Result<Value, &'static str> {
    let in_range = |len: usize| (COUNT_MIN as usize..=COUNT_MAX as usize).contains(&len);
    if !in_range(forward.len()) || !in_range(reverse.len()) {
        return Err("invalid_sample_count");
    }
    let mut maximum = 0.0_f64;
    let mut sum_squares = 0.0_f64;
    for value in forward.iter().chain(reverse) {
        if !value.is_finite() || *value < 0.0 || *value > DISTANCE_MAX {
            return Err("invalid_distance");
        }
        maximum = maximum.max(*value);
        sum_squares += value * value;
    }
    let count = (forward.len() + reverse.len()) as f64;
    Ok(json!({
        "maximum_sampled_distance": maximum,
        "rms_sampled_distance": (sum_squares / count).sqrt(),
        "sample_count": forward.len() + reverse.len(),
        "method": "sampled_point_to_exact_curve_approx",
        "certified_global_maximum": false
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    const UUID: &str = "123e4567-e89b-12d3-a456-426614174000";
    const OTHER: &str = "11111111-2222-3333-4444-555555555555";

    fn map(value: Value) -> Map<String, Value> {
        value.as_object().unwrap().clone()
    }
    fn samples_args() -> Value {
        json!({"object_id":UUID,"sample_count":16,"from_fraction":0.0,"to_fraction":1.0})
    }

    #[test]
    fn schemas_expose_exact_required_fields() {
        let samples = samples_schema();
        let properties = &samples["properties"];
        assert_eq!(properties["object_id"]["format"], "uuid");
        assert_eq!(properties["sample_count"]["type"], "integer");
        assert_eq!(properties["sample_count"]["minimum"], 8);
        assert_eq!(properties["sample_count"]["maximum"], 64);
        assert_eq!(properties["from_fraction"]["minimum"], 0);
        assert_eq!(properties["from_fraction"]["maximum"], 1);
        assert_eq!(samples["additionalProperties"], false);
        let required = samples["required"].as_array().unwrap();
        let required: Vec<_> = required
            .iter()
            .map(|value| value.as_str().unwrap())
            .collect();
        assert_eq!(
            required,
            ["object_id", "sample_count", "from_fraction", "to_fraction"]
        );

        let compare = compare_schema();
        assert_eq!(compare["properties"]["left"]["format"], "uuid");
        assert_eq!(compare["additionalProperties"], false);
        let required = compare["required"].as_array().unwrap();
        let required: Vec<_> = required
            .iter()
            .map(|value| value.as_str().unwrap())
            .collect();
        assert_eq!(required, ["left", "right", "sample_count"]);
    }

    #[test]
    fn validate_samples_accepts_valid_arguments() {
        assert_eq!(validate_samples(&map(samples_args())), Ok(()));
    }

    #[test]
    fn validate_samples_rejects_bad_arguments() {
        let cases = vec![
            json!({"sample_count":16,"from_fraction":0.0,"to_fraction":1.0}),
            json!({"object_id":UUID,"from_fraction":0.0,"to_fraction":1.0}),
            json!({"object_id":UUID,"sample_count":16,"to_fraction":1.0}),
            json!({"object_id":UUID,"sample_count":16,"from_fraction":0.0}),
            json!({"object_id":"not-a-uuid","sample_count":16,"from_fraction":0.0,"to_fraction":1.0}),
            json!({"object_id":UUID,"sample_count":"16","from_fraction":0.0,"to_fraction":1.0}),
            json!({"object_id":UUID,"sample_count":true,"from_fraction":0.0,"to_fraction":1.0}),
            json!({"object_id":UUID,"sample_count":7,"from_fraction":0.0,"to_fraction":1.0}),
            json!({"object_id":UUID,"sample_count":65,"from_fraction":0.0,"to_fraction":1.0}),
            json!({"object_id":UUID,"sample_count":16,"from_fraction":-0.1,"to_fraction":1.0}),
            json!({"object_id":UUID,"sample_count":16,"from_fraction":null,"to_fraction":1.0}),
            json!({"object_id":UUID,"sample_count":16,"from_fraction":0.0,"to_fraction":1.5}),
            json!({"object_id":UUID,"sample_count":16,"from_fraction":0.5,"to_fraction":0.5}),
            json!({"object_id":UUID,"sample_count":16,"from_fraction":0.9,"to_fraction":0.1}),
            json!({"object_id":UUID,"sample_count":16,"from_fraction":0.0,"to_fraction":1.0,"extra":1}),
        ];
        for value in cases {
            let args = map(value.clone());
            assert_eq!(
                validate_samples(&args),
                Err("invalid_sample_arguments"),
                "{value}"
            );
        }
    }

    #[test]
    fn validate_compare_strict_but_same_uuid_allowed() {
        let valid = json!({"left":UUID,"right":OTHER,"sample_count":32});
        let valid_args = map(valid);
        assert_eq!(validate_compare(&valid_args), Ok(()));
        let same_args = map(json!({"left":UUID,"right":UUID,"sample_count":32}));
        assert_eq!(validate_compare(&same_args), Ok(()));
        let cases = vec![
            json!({"left":UUID,"sample_count":32}),
            json!({"right":UUID,"sample_count":32}),
            json!({"left":UUID,"right":UUID}),
            json!({"left":UUID,"right":"nope","sample_count":32}),
            json!({"left":UUID,"right":OTHER,"sample_count":true}),
            json!({"left":UUID,"right":OTHER,"sample_count":0}),
            json!({"left":UUID,"right":OTHER,"sample_count":65}),
            json!({"left":UUID,"right":OTHER,"sample_count":32,"extra":true}),
        ];
        for value in cases {
            let args = map(value.clone());
            assert_eq!(
                validate_compare(&args),
                Err("invalid_compare_arguments"),
                "{value}"
            );
        }
    }

    #[test]
    fn summarize_zero_and_asymmetric_samples() {
        let summary = summarize_distances(&[0.0; 8], &[0.0; 8]).unwrap();
        assert_eq!(summary["maximum_sampled_distance"].as_f64(), Some(0.0));
        assert_eq!(summary["rms_sampled_distance"].as_f64(), Some(0.0));
        assert_eq!(summary["sample_count"], 16);
        assert_eq!(summary["method"], "sampled_point_to_exact_curve_approx");
        assert_eq!(summary["certified_global_maximum"], false);

        let forward = [0.0, 2.0, 2.0, 0.0, 0.0, 0.0, 0.0, 0.0];
        let reverse = [1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0];
        let summary = summarize_distances(&forward, &reverse).unwrap();
        assert_eq!(summary["maximum_sampled_distance"].as_f64(), Some(2.0));
        let rms = summary["rms_sampled_distance"].as_f64().unwrap();
        // 4 + 4 + 1 + 1 = 10 squared units over 16 samples.
        assert!((rms * rms - 10.0 / 16.0).abs() < 1e-12);
        assert_eq!(summary["sample_count"], 16);
        assert_eq!(summary["certified_global_maximum"], false);
    }

    #[test]
    fn summarize_accepts_boundaries_and_rejects_invalid_input() {
        assert!(summarize_distances(&[1e12; 8], &[0.0; 64]).is_ok());
        assert!(summarize_distances(&[0.0; 7], &[0.0; 8]).is_err());
        assert!(summarize_distances(&[0.0; 8], &[0.0; 65]).is_err());
        assert!(summarize_distances(&[f64::NAN; 8], &[0.0; 8]).is_err());
        assert!(summarize_distances(&[f64::INFINITY; 8], &[0.0; 8]).is_err());
        assert!(summarize_distances(&[-0.1; 8], &[0.0; 8]).is_err());
        assert!(summarize_distances(&[0.0; 8], &[-1.0; 8]).is_err());
        assert!(summarize_distances(&[1e13; 8], &[0.0; 8]).is_err());
    }
}
