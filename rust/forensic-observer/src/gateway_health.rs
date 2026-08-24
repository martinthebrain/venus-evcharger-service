//! Health and resource-pressure contracts for gateway diagnostics.

use serde::Serialize;
use serde_json::Value;

use crate::error::Result;
use crate::gateway_evidence::{ProtectiveTrigger, optional_trigger};
use crate::gateway_validation::{
    exact_object, invalid, nonnegative_number, positive_number, required, required_bool,
    required_text, required_u64,
};

const SCHEMA_VERSION: u8 = 5;
const CIRCUIT_TRIGGER_SCHEMA_VERSION: u8 = 4;
const LEGACY_SCHEMA_VERSION: u8 = 3;
const HEALTH_BASE_FIELDS: [&str; 10] = [
    "state",
    "stale",
    "timeouts_60s",
    "average_latency_ms",
    "maximum_latency_ms",
    "pending_gateway_commands",
    "pending_core_commands",
    "maximum_event_loop_gap_ms_60s",
    "last_success_at",
    "last_error_code",
];
const HEALTH_TRIGGER_FIELDS: [&str; 12] = [
    "state",
    "stale",
    "timeouts_60s",
    "average_latency_ms",
    "maximum_latency_ms",
    "pending_gateway_commands",
    "pending_core_commands",
    "maximum_event_loop_gap_ms_60s",
    "last_success_at",
    "last_error_code",
    "active_protective_trigger",
    "last_protective_trigger",
];
const HEALTH_RESOURCE_FIELDS: [&str; 17] = [
    "state",
    "stale",
    "timeouts_60s",
    "average_latency_ms",
    "maximum_latency_ms",
    "pending_gateway_commands",
    "pending_core_commands",
    "maximum_event_loop_gap_ms_60s",
    "last_success_at",
    "last_error_code",
    "active_protective_trigger",
    "last_protective_trigger",
    "operational_state",
    "performance_state",
    "resource_state",
    "protective_cause",
    "resource_evidence",
];

/// Validated gateway health summary.
#[derive(Clone, Debug, Serialize)]
pub struct GatewayHealth {
    /// Operational state independent of performance details.
    pub state: String,
    stale: bool,
    timeouts_60s: u64,
    average_latency_ms: f64,
    maximum_latency_ms: f64,
    pending_gateway_commands: u64,
    pending_core_commands: u64,
    maximum_event_loop_gap_ms_60s: f64,
    last_success_at: f64,
    last_error_code: String,
    active_protective_trigger: Option<ProtectiveTrigger>,
    last_protective_trigger: Option<ProtectiveTrigger>,
    operational_state: String,
    performance_state: String,
    resource_state: String,
    protective_cause: String,
    resource_evidence: Option<ResourcePressure>,
}

impl GatewayHealth {
    pub(super) fn from_value(value: &Value, schema_version: u8) -> Result<Self> {
        let object = exact_object(
            value,
            "gateway health summary",
            health_fields(schema_version),
        )?;
        let state = required_text(object, "state", false)?;
        if !["unknown", "ok", "degraded", "protective", "unavailable"].contains(&state.as_str()) {
            return Err(invalid("gateway health state is invalid"));
        }
        let average_latency_ms = nonnegative_number(
            object,
            "average_latency_ms",
            "gateway health average_latency_ms",
        )?;
        let maximum_latency_ms = nonnegative_number(
            object,
            "maximum_latency_ms",
            "gateway health maximum_latency_ms",
        )?;
        if maximum_latency_ms < average_latency_ms {
            return Err(invalid(
                "gateway health maximum latency precedes average latency",
            ));
        }
        let extension = health_extensions(object, &state, schema_version)?;
        Ok(Self {
            state,
            stale: required_bool(object, "stale")?,
            timeouts_60s: required_u64(object, "timeouts_60s", "gateway health timeouts_60s")?,
            average_latency_ms,
            maximum_latency_ms,
            pending_gateway_commands: required_u64(
                object,
                "pending_gateway_commands",
                "gateway pending commands",
            )?,
            pending_core_commands: required_u64(
                object,
                "pending_core_commands",
                "core pending commands",
            )?,
            maximum_event_loop_gap_ms_60s: nonnegative_number(
                object,
                "maximum_event_loop_gap_ms_60s",
                "gateway event-loop gap",
            )?,
            last_success_at: nonnegative_number(
                object,
                "last_success_at",
                "gateway last_success_at",
            )?,
            last_error_code: required_text(object, "last_error_code", true)?,
            active_protective_trigger: extension.active_protective_trigger,
            last_protective_trigger: extension.last_protective_trigger,
            operational_state: extension.operational_state,
            performance_state: extension.performance_state,
            resource_state: extension.resource_state,
            protective_cause: extension.protective_cause,
            resource_evidence: extension.resource_evidence,
        })
    }
}

struct HealthExtensions {
    active_protective_trigger: Option<ProtectiveTrigger>,
    last_protective_trigger: Option<ProtectiveTrigger>,
    operational_state: String,
    performance_state: String,
    resource_state: String,
    protective_cause: String,
    resource_evidence: Option<ResourcePressure>,
}

const fn health_fields(schema_version: u8) -> &'static [&'static str] {
    match schema_version {
        LEGACY_SCHEMA_VERSION => &HEALTH_BASE_FIELDS,
        CIRCUIT_TRIGGER_SCHEMA_VERSION => &HEALTH_TRIGGER_FIELDS,
        _ => &HEALTH_RESOURCE_FIELDS,
    }
}

fn health_extensions(
    object: &serde_json::Map<String, Value>,
    state: &str,
    schema_version: u8,
) -> Result<HealthExtensions> {
    let (active, latest) = protective_triggers(object, schema_version)?;
    if schema_version < SCHEMA_VERSION {
        return Ok(HealthExtensions {
            active_protective_trigger: active,
            last_protective_trigger: latest,
            operational_state: "unknown".to_owned(),
            performance_state: "unknown".to_owned(),
            resource_state: "unknown".to_owned(),
            protective_cause: String::new(),
            resource_evidence: None,
        });
    }
    let resource_state = required_text(object, "resource_state", false)?;
    if !["unknown", "ok", "busy", "constrained"].contains(&resource_state.as_str()) {
        return Err(invalid("gateway resource state is invalid"));
    }
    let protective_cause = required_text(object, "protective_cause", true)?;
    if state != "protective" && !protective_cause.is_empty() {
        return Err(invalid(
            "non-protective gateway health cannot have protective_cause",
        ));
    }
    let resource_evidence = optional_resource_pressure(required(object, "resource_evidence")?)?;
    if resource_evidence
        .as_ref()
        .is_some_and(|evidence| evidence.active)
        && resource_state != "constrained"
    {
        return Err(invalid(
            "active gateway resource evidence requires constrained resource_state",
        ));
    }
    Ok(HealthExtensions {
        active_protective_trigger: active,
        last_protective_trigger: latest,
        operational_state: health_state(object, "operational_state")?,
        performance_state: health_state(object, "performance_state")?,
        resource_state,
        protective_cause,
        resource_evidence,
    })
}

fn protective_triggers(
    object: &serde_json::Map<String, Value>,
    schema_version: u8,
) -> Result<(Option<ProtectiveTrigger>, Option<ProtectiveTrigger>)> {
    if schema_version == LEGACY_SCHEMA_VERSION {
        return Ok((None, None));
    }
    let active = optional_trigger(required(object, "active_protective_trigger")?)?;
    let latest = optional_trigger(required(object, "last_protective_trigger")?)?;
    if active.is_some() && active != latest {
        return Err(invalid(
            "active gateway protective trigger must equal last_protective_trigger",
        ));
    }
    Ok((active, latest))
}

fn health_state(object: &serde_json::Map<String, Value>, key: &str) -> Result<String> {
    let state = required_text(object, key, false)?;
    if !["unknown", "ok", "degraded", "protective", "unavailable"].contains(&state.as_str()) {
        return Err(invalid("gateway health state is invalid"));
    }
    Ok(state)
}

/// Bounded measurements from the latest constrained resource transition.
#[derive(Clone, Debug, Serialize)]
struct ResourcePressure {
    active: bool,
    triggered_at: f64,
    causes: Vec<String>,
    load_per_cpu_1m: Option<f64>,
    system_cpu_pct: Option<f64>,
    mem_available_kb: Option<f64>,
}

impl ResourcePressure {
    fn from_value(value: &Value) -> Result<Self> {
        let object = exact_object(
            value,
            "gateway resource evidence",
            &[
                "active",
                "triggered_at",
                "causes",
                "load_per_cpu_1m",
                "system_cpu_pct",
                "mem_available_kb",
            ],
        )?;
        let causes = required(object, "causes")?
            .as_array()
            .ok_or_else(|| invalid("gateway resource evidence causes must be an array"))?
            .iter()
            .map(|cause| {
                cause
                    .as_str()
                    .map(str::to_owned)
                    .ok_or_else(|| invalid("gateway resource evidence cause must be text"))
            })
            .collect::<Result<Vec<_>>>()?;
        if causes.is_empty()
            || causes
                .iter()
                .any(|cause| !["load", "cpu", "memory"].contains(&cause.as_str()))
            || causes
                .iter()
                .collect::<std::collections::BTreeSet<_>>()
                .len()
                != causes.len()
        {
            return Err(invalid("gateway resource evidence causes are invalid"));
        }
        let load_per_cpu_1m = optional_nonnegative(object, "load_per_cpu_1m")?;
        let system_cpu_pct = optional_nonnegative(object, "system_cpu_pct")?;
        let mem_available_kb = optional_nonnegative(object, "mem_available_kb")?;
        if (causes.iter().any(|cause| cause == "load") && load_per_cpu_1m.is_none())
            || (causes.iter().any(|cause| cause == "cpu") && system_cpu_pct.is_none())
            || (causes.iter().any(|cause| cause == "memory") && mem_available_kb.is_none())
        {
            return Err(invalid(
                "gateway resource evidence cause requires its metric",
            ));
        }
        Ok(Self {
            active: required_bool(object, "active")?,
            triggered_at: positive_number(
                object,
                "triggered_at",
                "gateway resource evidence triggered_at",
            )?,
            causes,
            load_per_cpu_1m,
            system_cpu_pct,
            mem_available_kb,
        })
    }
}

fn optional_resource_pressure(value: &Value) -> Result<Option<ResourcePressure>> {
    if value.is_null() {
        Ok(None)
    } else {
        ResourcePressure::from_value(value).map(Some)
    }
}

fn optional_nonnegative(object: &serde_json::Map<String, Value>, key: &str) -> Result<Option<f64>> {
    if required(object, key)?.is_null() {
        Ok(None)
    } else {
        nonnegative_number(object, key, &format!("gateway resource evidence {key}")).map(Some)
    }
}
