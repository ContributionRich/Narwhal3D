//! Bounded identity/evidence contracts, NOT an implementation of host/kernel APIs.
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IdentityKind {
    SelectionGeneration,
    ContentRevision,
    CanonicalTopology,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
enum Availability {
    Unavailable {
        reason: &'static str,
    },
    #[cfg(test)]
    Known {
        document_scope: String,
        token: String,
        source: &'static str,
    },
}
/// No Deserialize or production known-token constructor: model/JSON fields cannot
/// populate authority. A future reviewed host producer must own that boundary.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Identity {
    kind: IdentityKind,
    availability: Availability,
    integration_available: bool,
}
impl Identity {
    pub fn unavailable(kind: IdentityKind) -> Self {
        Self {
            kind,
            availability: Availability::Unavailable {
                reason: "authoritative_host_api_not_integrated",
            },
            integration_available: false,
        }
    }
    pub fn require_same(&self, _other: &Self) -> Result<(), &'static str> {
        #[cfg(test)]
        if let (Availability::Known { .. }, Availability::Known { .. }) =
            (&self.availability, &_other.availability)
        {
            return if self == _other {
                Ok(())
            } else {
                Err("authoritative_identity_changed")
            };
        }
        Err("authoritative_identity_unavailable")
    }
    #[cfg(test)]
    fn hypothetical(kind: IdentityKind, scope: &str, token: &str) -> Result<Self, &'static str> {
        if [scope, token]
            .iter()
            .any(|s| s.is_empty() || s.len() > 256 || s.chars().any(char::is_control))
        {
            return Err("invalid_identity_contract");
        }
        Ok(Self {
            kind,
            availability: Availability::Known {
                document_scope: scope.into(),
                token: token.into(),
                source: "hypothetical_trusted_fixture",
            },
            integration_available: false,
        })
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EvidenceStage {
    Planned,
    Approved,
    PublisherReceiptConfirmed,
    LaterObjectObserved,
    CurrentlyMeasured,
    Unverifiable,
}
pub fn verification_stages(confirmed: bool, observed: bool) -> Value {
    json!({"overall":EvidenceStage::Unverifiable,
        "publisher_receipt":if confirmed {EvidenceStage::PublisherReceiptConfirmed}else{EvidenceStage::Unverifiable},
        "later_read":if observed {EvidenceStage::LaterObjectObserved}else{EvidenceStage::Unverifiable},
        "current_measurement":{"available":false,"reason":"bounded_authoritative_geometry_and_revision_api_unavailable"}})
}

pub fn capabilities() -> Value {
    json!({"selection_generation":Identity::unavailable(IdentityKind::SelectionGeneration),
        "content_revision":Identity::unavailable(IdentityKind::ContentRevision),
        "canonical_topology":Identity::unavailable(IdentityKind::CanonicalTopology),
        "document_context_is_content_revision":false})
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MissingMeasurement {
    NotApplicable,
    Unsupported,
    BudgetExceeded,
    Cancelled,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
pub enum Measurement<T> {
    Available { value: T },
    Unavailable { reason: MissingMeasurement },
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SurfaceClass {
    Plane,
    Cylinder,
    Cone,
    Sphere,
    Torus,
    Nurbs,
    Bezier,
}
#[derive(Clone, Copy, Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GeometrySource {
    OcctMaster,
    DocumentGeometry,
}
/// An untrusted serialized CLAIM until a real producer supplies authority. This
/// validator never constructs Identity::Known or changes any verification state.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GeometrySummaryClaim {
    pub document_scope: String,
    pub object_id: String,
    pub revision_token: String,
    pub units: String,
    pub master_token: Option<String>,
    pub source: GeometrySource,
    pub exact: bool,
    pub cancelled: bool,
    pub truncated: bool,
    pub max_faces: u32,
    pub faces_visited: u32,
    pub limit_ms: u64,
    pub elapsed_ms: u64,
    pub face_types: Measurement<std::collections::BTreeMap<SurfaceClass, u32>>,
    pub airtight: Measurement<bool>,
    pub bounds: Measurement<[[f64; 3]; 2]>,
    pub volume: Measurement<f64>,
}
impl GeometrySummaryClaim {
    pub fn validate(&self) -> Result<Value, &'static str> {
        if [&self.document_scope, &self.revision_token]
            .iter()
            .any(|s| s.is_empty() || s.len() > 256 || s.chars().any(char::is_control))
            || !crate::catalog::uuid(&self.object_id)
            || self.object_id == "00000000-0000-0000-0000-000000000000"
        {
            return Err("summary_identity_claim_invalid");
        }
        if !crate::plan::fixed_scale_unit(&self.units) {
            return Err("summary_units_unavailable");
        }
        if self
            .master_token
            .as_ref()
            .is_some_and(|s| s.is_empty() || s.len() > 256 || s.chars().any(char::is_control))
            || (matches!(self.source, GeometrySource::OcctMaster) && self.master_token.is_none())
        {
            return Err("summary_master_claim_required");
        }
        if !self.exact
            || self.cancelled
            || self.truncated
            || !(1..=256).contains(&self.max_faces)
            || self.faces_visited > self.max_faces
            || !(1..=250).contains(&self.limit_ms)
            || self.elapsed_ms > self.limit_ms
        {
            return Err("summary_incomplete_or_over_budget");
        }
        if let Measurement::Available { value } = &self.face_types
            && value.values().map(|n| u64::from(*n)).sum::<u64>() != u64::from(self.faces_visited)
        {
            return Err("summary_face_count_mismatch");
        }
        if let Measurement::Available { value } = &self.bounds
            && (value
                .iter()
                .flatten()
                .any(|v| !v.is_finite() || v.abs() > 1e9)
                || (0..3).any(|i| value[0][i] > value[1][i]))
        {
            return Err("summary_bounds_invalid");
        }
        if let Measurement::Available { value } = &self.volume
            && (!value.is_finite() || *value <= 0.)
        {
            return Err("summary_volume_invalid");
        }
        Ok(
            json!({"status":"geometry_contract_checked","schema_validation_only":true,
            "producer":{"available":false,"reason":"bounded_kernel_producer_not_integrated"},"current_geometry_verified":false}),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn v4_a19_geometry_claim_validation_does_not_create_evidence() {
        let raw = json!({"document_scope":"fixture","object_id":"00000000-0000-0000-0000-000000000007","revision_token":"hypothetical-revision","units":"Millimeters",
            "master_token":"hypothetical-master","source":"occt_master","exact":true,"cancelled":false,"truncated":false,
            "max_faces":3,"faces_visited":3,"limit_ms":100,"elapsed_ms":5,
            "face_types":{"status":"available","value":{"cylinder":1,"plane":2}},"airtight":{"status":"available","value":true},
            "bounds":{"status":"available","value":[[0,0,0],[2,2,4]]},"volume":{"status":"unavailable","reason":"unsupported"}});
        let claim: GeometrySummaryClaim = serde_json::from_value(raw.clone()).unwrap();
        assert_eq!(
            claim.validate().unwrap()["current_geometry_verified"],
            false
        );
        assert_eq!(claim.validate().unwrap()["producer"]["available"], false);
        for (key, value) in [
            ("units", json!("None")),
            ("cancelled", json!(true)),
            ("truncated", json!(true)),
            ("exact", json!(false)),
            ("elapsed_ms", json!(101)),
            ("faces_visited", json!(4)),
            ("revision_token", json!("")),
            ("master_token", Value::Null),
        ] {
            let mut invalid = raw.clone();
            invalid[key] = value;
            assert!(
                serde_json::from_value::<GeometrySummaryClaim>(invalid)
                    .unwrap()
                    .validate()
                    .is_err(),
                "{key}"
            );
        }
        let mut oversized = raw.clone();
        oversized["source"] = json!("document_geometry");
        oversized["master_token"] = json!("m".repeat(257));
        assert_eq!(
            serde_json::from_value::<GeometrySummaryClaim>(oversized)
                .unwrap()
                .validate()
                .unwrap_err(),
            "summary_master_claim_required"
        );
        let mut proxy = raw.clone();
        proxy["source"] = json!("viewport_proxy");
        assert!(serde_json::from_value::<GeometrySummaryClaim>(proxy).is_err());
        let mut boolean = raw;
        boolean["faces_visited"] = json!(true);
        assert!(serde_json::from_value::<GeometrySummaryClaim>(boolean).is_err());
    }
    #[test]
    fn v4_a15_identity_absence_is_not_a_known_token() {
        assert!(
            serde_json::from_value::<crate::conversation::Input>(
                json!({"op":"identity_contract","content_revision":"invented","known":true})
            )
            .is_err()
        );
        let caps = capabilities();
        for name in [
            "selection_generation",
            "content_revision",
            "canonical_topology",
        ] {
            assert_eq!(caps[name]["availability"]["status"], "unavailable");
            assert_eq!(caps[name]["integration_available"], false);
        }
        let absent = Identity::unavailable(IdentityKind::ContentRevision);
        assert_eq!(
            absent.require_same(&absent),
            Err("authoritative_identity_unavailable")
        );
        let future = Identity::hypothetical(
            IdentityKind::ContentRevision,
            "fixture-doc",
            "future-host-token",
        )
        .unwrap();
        assert!(future.require_same(&future).is_ok());
        assert!(future.require_same(&absent).is_err());
        let other = Identity::hypothetical(
            IdentityKind::ContentRevision,
            "other-doc",
            "future-host-token",
        )
        .unwrap();
        assert_eq!(
            future.require_same(&other),
            Err("authoritative_identity_changed")
        );
        assert_eq!(
            serde_json::to_value(future).unwrap()["integration_available"],
            false
        );
        assert!(
            Identity::hypothetical(IdentityKind::SelectionGeneration, "fixture", "bad\nname")
                .is_err()
        );
    }
}
