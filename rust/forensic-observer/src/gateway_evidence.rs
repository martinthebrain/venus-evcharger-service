//! Circuit-breaker evidence contract for gateway diagnostics.

use serde::Serialize;
use serde_json::Value;

use crate::error::Result;
use crate::gateway_validation::{
    exact_object, invalid, nonnegative_number, positive_number, required, required_text,
    required_u64,
};

/// Bounded evidence for one circuit-breaker transition into protective mode.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ProtectiveTrigger {
    triggered_at: f64,
    protective_until: f64,
    timeout_count_60s: u64,
    operation_kind: String,
    source: String,
    error_code: String,
    latency_ms: Option<f64>,
}

impl ProtectiveTrigger {
    fn from_value(value: &Value) -> Result<Self> {
        let object = exact_object(
            value,
            "gateway protective trigger",
            &[
                "triggered_at",
                "protective_until",
                "timeout_count_60s",
                "operation_kind",
                "source",
                "error_code",
                "latency_ms",
            ],
        )?;
        let triggered_at = positive_number(
            object,
            "triggered_at",
            "gateway protective trigger triggered_at",
        )?;
        let protective_until = nonnegative_number(
            object,
            "protective_until",
            "gateway protective trigger protective_until",
        )?;
        if protective_until < triggered_at {
            return Err(invalid(
                "gateway protective trigger protective_until precedes triggered_at",
            ));
        }
        let timeout_count_60s = required_u64(
            object,
            "timeout_count_60s",
            "gateway protective trigger timeout_count_60s",
        )?;
        if timeout_count_60s == 0 {
            return Err(invalid(
                "gateway protective trigger timeout_count_60s must be positive",
            ));
        }
        let latency_ms = match required(object, "latency_ms")? {
            Value::Null => None,
            _ => Some(nonnegative_number(
                object,
                "latency_ms",
                "gateway protective trigger latency_ms",
            )?),
        };
        Ok(Self {
            triggered_at,
            protective_until,
            timeout_count_60s,
            operation_kind: required_text(object, "operation_kind", false)?,
            source: required_text(object, "source", true)?,
            error_code: required_text(object, "error_code", false)?,
            latency_ms,
        })
    }
}

pub fn optional_trigger(value: &Value) -> Result<Option<ProtectiveTrigger>> {
    if value.is_null() {
        Ok(None)
    } else {
        ProtectiveTrigger::from_value(value).map(Some)
    }
}
