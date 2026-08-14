//! Semantic EV-charger fields carried by gateway diagnostics.

use std::collections::HashSet;

use serde::Serialize;
use serde_json::{Map, Value};

use crate::error::Result;
use crate::gateway_validation::{
    invalid, key_set, nonnegative_number, required, required_text, string_set,
};

pub const FIELD_NAMES: [&str; 10] = [
    "operating_mode",
    "charging_enabled",
    "auto_start_enabled",
    "ac_power_w",
    "charger_state_code",
    "decision_reason",
    "decision_state",
    "last_health_reason",
    "runtime_overrides_active",
    "runtime_overrides_source",
];

/// One validated semantic diagnostic field.
#[derive(Clone, Debug, Serialize)]
pub struct DiagnosticSample {
    /// Stable semantic field name.
    pub name: String,
    value: Value,
    /// Freshness and availability status.
    pub status: String,
    changed_at: f64,
    confirmed_at: f64,
    confidence: f64,
    applicability: String,
    reason_code: String,
}

impl DiagnosticSample {
    pub(super) fn from_value(value: &Value) -> Result<Self> {
        let mut object = value
            .as_object()
            .cloned()
            .ok_or_else(|| invalid("gateway diagnostic sample must be an object"))?;
        normalize_legacy_sample(&mut object)?;
        if key_set(&object)
            != string_set(&[
                "name",
                "value",
                "status",
                "changed_at",
                "confirmed_at",
                "confidence",
                "applicability",
                "reason_code",
            ])
        {
            return Err(invalid(
                "gateway diagnostic sample fields do not match the schema",
            ));
        }
        let name = required_text(&object, "name", false)?;
        if !FIELD_NAMES.contains(&name.as_str()) {
            return Err(invalid("gateway diagnostic name is invalid"));
        }
        let status = required_text(&object, "status", false)?;
        if ![
            "fresh",
            "stale",
            "inactive",
            "unavailable",
            "error",
            "unknown",
        ]
        .contains(&status.as_str())
        {
            return Err(invalid("gateway diagnostic status is invalid"));
        }
        let changed_at = nonnegative_number(&object, "changed_at", "diagnostic changed_at")?;
        let confirmed_at = nonnegative_number(&object, "confirmed_at", "diagnostic confirmed_at")?;
        if changed_at > confirmed_at {
            return Err(invalid("diagnostic changed_at exceeds confirmed_at"));
        }
        let confidence = nonnegative_number(&object, "confidence", "diagnostic confidence")?;
        if confidence > 1.0 {
            return Err(invalid("diagnostic confidence exceeds 1"));
        }
        let applicability = required_text(&object, "applicability", false)?;
        let expected_applicability = match status.as_str() {
            "inactive" => "not-applicable",
            "unknown" => "unknown",
            _ => "applicable",
        };
        if applicability != expected_applicability {
            return Err(invalid("diagnostic applicability contradicts status"));
        }
        let reason_code = required_text(&object, "reason_code", true)?;
        let value = required(&object, "value")?.clone();
        validate_diagnostic_value(&name, &value)?;
        validate_sample_quality(&status, &value, changed_at, confirmed_at, &reason_code)?;
        Ok(Self {
            name,
            value,
            status,
            changed_at,
            confirmed_at,
            confidence,
            applicability,
            reason_code,
        })
    }
}

pub fn validate_samples(samples: &[DiagnosticSample]) -> Result<()> {
    if samples.len() != FIELD_NAMES.len() {
        return Err(invalid(
            "gateway diagnostics must contain every semantic field exactly once",
        ));
    }
    let names = samples
        .iter()
        .map(|sample| sample.name.as_str())
        .collect::<HashSet<_>>();
    if names.len() != FIELD_NAMES.len() || FIELD_NAMES.iter().any(|name| !names.contains(name)) {
        return Err(invalid(
            "gateway diagnostics contains duplicate or missing semantic fields",
        ));
    }
    Ok(())
}

fn normalize_legacy_sample(object: &mut Map<String, Value>) -> Result<()> {
    let legacy_fields = string_set(&[
        "name",
        "value",
        "status",
        "observed_at",
        "confidence",
        "reason_code",
    ]);
    if key_set(object) != legacy_fields {
        return Ok(());
    }
    let observed = object
        .remove("observed_at")
        .ok_or_else(|| invalid("legacy sample lacks observed_at"))?;
    let status = object
        .get("status")
        .and_then(Value::as_str)
        .ok_or_else(|| invalid("legacy sample status is invalid"))?;
    let applicability = match status {
        "inactive" => "not-applicable",
        "unknown" => "unknown",
        _ => "applicable",
    };
    object.insert("changed_at".to_owned(), observed.clone());
    object.insert("confirmed_at".to_owned(), observed);
    object.insert(
        "applicability".to_owned(),
        Value::String(applicability.to_owned()),
    );
    Ok(())
}

fn validate_sample_quality(
    status: &str,
    value: &Value,
    changed_at: f64,
    confirmed_at: f64,
    reason: &str,
) -> Result<()> {
    match status {
        "fresh" | "stale" => {
            if value.is_null() || changed_at <= 0.0 || confirmed_at <= 0.0 {
                return Err(invalid(
                    "observed diagnostic requires a value and positive timestamps",
                ));
            }
        }
        "inactive" => {
            if reason.is_empty() {
                return Err(invalid("inactive diagnostic requires reason_code"));
            }
            if !value.is_null() && (changed_at <= 0.0 || confirmed_at <= 0.0) {
                return Err(invalid(
                    "inactive diagnostic value requires positive timestamps",
                ));
            }
        }
        "unavailable" | "error" => {
            if !value.is_null() || reason.is_empty() {
                return Err(invalid(
                    "unavailable diagnostic requires no value and a reason",
                ));
            }
        }
        "unknown" => {
            if !value.is_null() {
                return Err(invalid("unknown diagnostic must not carry a value"));
            }
        }
        _ => return Err(invalid("diagnostic status is invalid")),
    }
    Ok(())
}

fn validate_diagnostic_value(name: &str, value: &Value) -> Result<()> {
    if value.is_null() {
        return Ok(());
    }
    let valid = match name {
        "operating_mode" => value.as_u64().is_some_and(|number| number <= 2),
        "charger_state_code" => value.as_u64().is_some(),
        "charging_enabled" | "auto_start_enabled" | "runtime_overrides_active" => {
            value.is_boolean()
        }
        "ac_power_w" => value.as_f64().is_some_and(f64::is_finite),
        "decision_reason"
        | "decision_state"
        | "last_health_reason"
        | "runtime_overrides_source" => value.is_string(),
        _ => false,
    };
    if valid {
        Ok(())
    } else {
        Err(invalid(
            "gateway diagnostic value has the wrong semantic type",
        ))
    }
}
