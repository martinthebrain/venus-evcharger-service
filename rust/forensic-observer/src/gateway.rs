//! Strict reader for transport-neutral gateway diagnostics schema version 3.

use std::path::Path;

use serde::Serialize;
use serde_json::Value;

use crate::error::Result;
pub use crate::gateway_sample::DiagnosticSample;
use crate::gateway_sample::validate_samples;
use crate::gateway_validation::{
    exact_object, invalid, key_set, nonnegative_number, positive_number, required, required_bool,
    required_text, required_u64, string_set,
};
use crate::ini::read_bounded_text;

const MAX_GATEWAY_DIAGNOSTICS_BYTES: u64 = 1_048_576;
const SCHEMA_VERSION: u8 = 3;
const CRITICAL_FIELDS: [&str; 3] = ["operating_mode", "charging_enabled", "ac_power_w"];

/// Validated semantic gateway snapshot.
#[derive(Clone, Debug, Serialize)]
pub struct GatewayDiagnostics {
    schema_version: u8,
    sequence: u64,
    captured_at: f64,
    captured_monotonic: f64,
    /// Operational gateway health.
    pub health: GatewayHealth,
    discovery: GatewayDiscovery,
    publication: GatewayPublication,
    /// Semantic EV-charger values with explicit quality.
    pub ev_charger: Vec<DiagnosticSample>,
}

impl GatewayDiagnostics {
    /// Read and strictly validate one bounded diagnostics document.
    ///
    /// # Errors
    ///
    /// Returns an error when the document cannot be read within its size bound,
    /// is invalid JSON, or violates schema version 3.
    pub fn read(path: &Path) -> Result<Self> {
        let text = read_bounded_text(path, MAX_GATEWAY_DIAGNOSTICS_BYTES, "gateway diagnostics")?;
        let value: Value = serde_json::from_str(&text)?;
        Self::from_value(&value)
    }

    /// Decode a diagnostics document from an untrusted JSON value.
    ///
    /// # Errors
    ///
    /// Returns an error when fields, types, quality metadata, or cross-field
    /// invariants violate the gateway diagnostics contract.
    pub fn from_value(value: &Value) -> Result<Self> {
        let object = exact_object(
            value,
            "gateway diagnostics snapshot",
            &[
                "schema_version",
                "sequence",
                "captured_at",
                "captured_monotonic",
                "health",
                "discovery",
                "publication",
                "ev_charger",
            ],
        )?;
        let schema_version = required_u64(
            object,
            "schema_version",
            "gateway diagnostics schema_version",
        )?;
        if schema_version != u64::from(SCHEMA_VERSION) {
            return Err(invalid(
                "gateway diagnostics has an unsupported schema_version",
            ));
        }
        let sequence = required_u64(object, "sequence", "gateway diagnostics sequence")?;
        let captured_at =
            positive_number(object, "captured_at", "gateway diagnostics captured_at")?;
        let captured_monotonic = positive_number(
            object,
            "captured_monotonic",
            "gateway diagnostics captured_monotonic",
        )?;
        let health = GatewayHealth::from_value(required(object, "health")?)?;
        let discovery = GatewayDiscovery::from_value(required(object, "discovery")?)?;
        let publication = GatewayPublication::from_value(required(object, "publication")?)?;
        let samples = required(object, "ev_charger")
            .and_then(|value| {
                value
                    .as_array()
                    .ok_or_else(|| invalid("gateway diagnostics ev_charger must be an array"))
            })?
            .iter()
            .map(DiagnosticSample::from_value)
            .collect::<Result<Vec<_>>>()?;
        validate_samples(&samples)?;
        Ok(Self {
            schema_version: SCHEMA_VERSION,
            sequence,
            captured_at,
            captured_monotonic,
            health,
            discovery,
            publication,
            ev_charger: samples,
        })
    }

    /// Return critical semantic fields that cannot currently be consumed.
    #[must_use]
    pub fn critical_unavailable_fields(&self) -> Vec<&'static str> {
        CRITICAL_FIELDS
            .iter()
            .copied()
            .filter(|name| {
                self.ev_charger.iter().any(|sample| {
                    sample.name == *name
                        && matches!(sample.status.as_str(), "unavailable" | "error" | "unknown")
                })
            })
            .collect()
    }
}

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
}

impl GatewayHealth {
    fn from_value(value: &Value) -> Result<Self> {
        let object = exact_object(
            value,
            "gateway health summary",
            &[
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
            ],
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
        })
    }
}

#[derive(Clone, Debug, Serialize)]
struct GatewayPublication {
    registered: bool,
    heartbeat_at: f64,
    stale: bool,
}

impl GatewayPublication {
    fn from_value(value: &Value) -> Result<Self> {
        let object = exact_object(
            value,
            "gateway publication summary",
            &["registered", "heartbeat_at", "stale"],
        )?;
        let registered = required_bool(object, "registered")?;
        let heartbeat_at =
            nonnegative_number(object, "heartbeat_at", "gateway publication heartbeat_at")?;
        if (registered && heartbeat_at <= 0.0) || (!registered && heartbeat_at != 0.0) {
            return Err(invalid(
                "gateway publication heartbeat contradicts registration",
            ));
        }
        Ok(Self {
            registered,
            heartbeat_at,
            stale: required_bool(object, "stale")?,
        })
    }
}

#[derive(Clone, Debug, Serialize)]
struct GatewayDiscovery {
    enabled: bool,
    state: String,
    pending_work: u64,
    discovered_source_count: u64,
    unusable_source_count: u64,
    dormant_source_count: u64,
    sources: Vec<GatewaySource>,
}

impl GatewayDiscovery {
    fn from_value(value: &Value) -> Result<Self> {
        let mut object = value
            .as_object()
            .cloned()
            .ok_or_else(|| invalid("gateway discovery summary must be an object"))?;
        let current = key_set(&object)
            == string_set(&[
                "enabled",
                "state",
                "pending_work",
                "discovered_source_count",
                "unusable_source_count",
                "dormant_source_count",
                "sources",
            ]);
        let legacy = key_set(&object)
            == string_set(&[
                "enabled",
                "state",
                "pending_work",
                "discovered_source_count",
                "unusable_source_count",
            ]);
        if legacy {
            object.insert("dormant_source_count".to_owned(), Value::from(0));
            object.insert("sources".to_owned(), Value::Array(Vec::new()));
        } else if !current {
            return Err(invalid(
                "gateway discovery summary fields do not match the schema",
            ));
        }
        let enabled = required_bool(&object, "enabled")?;
        let state = required_text(&object, "state", false)?;
        if ![
            "unknown",
            "disabled",
            "idle",
            "running",
            "degraded",
            "protective",
            "error",
            "unavailable",
        ]
        .contains(&state.as_str())
        {
            return Err(invalid("gateway discovery state is invalid"));
        }
        if (!enabled && state != "disabled") || (enabled && state == "disabled") {
            return Err(invalid("gateway discovery enabled flag contradicts state"));
        }
        let discovered = required_u64(
            &object,
            "discovered_source_count",
            "discovered source count",
        )?;
        let unusable = required_u64(&object, "unusable_source_count", "unusable source count")?;
        let dormant = required_u64(&object, "dormant_source_count", "dormant source count")?;
        if unusable > discovered
            || dormant > discovered
            || unusable.saturating_add(dormant) > discovered
        {
            return Err(invalid("gateway discovery source counts are inconsistent"));
        }
        let sources = required(&object, "sources")
            .and_then(|value| {
                value
                    .as_array()
                    .ok_or_else(|| invalid("gateway discovery sources must be an array"))
            })?
            .iter()
            .map(GatewaySource::from_value)
            .collect::<Result<Vec<_>>>()?;
        validate_sources(discovered, unusable, dormant, &sources)?;
        Ok(Self {
            enabled,
            state,
            pending_work: required_u64(&object, "pending_work", "gateway discovery pending_work")?,
            discovered_source_count: discovered,
            unusable_source_count: unusable,
            dormant_source_count: dormant,
            sources,
        })
    }
}

#[derive(Clone, Debug, Serialize)]
struct GatewaySource {
    source_id: String,
    kind: String,
    availability: String,
    reason_code: String,
}

impl GatewaySource {
    fn from_value(value: &Value) -> Result<Self> {
        let object = exact_object(
            value,
            "gateway source summary",
            &["source_id", "kind", "availability", "reason_code"],
        )?;
        let source_id = required_text(object, "source_id", false)?;
        let kind = required_text(object, "kind", false)?;
        if !["grid", "pv_ac", "pv_dc", "battery"].contains(&kind.as_str()) {
            return Err(invalid("gateway source kind is invalid"));
        }
        let availability = required_text(object, "availability", false)?;
        if !["available", "dormant", "unavailable", "unknown"].contains(&availability.as_str()) {
            return Err(invalid("gateway source availability is invalid"));
        }
        let reason_code = required_text(object, "reason_code", true)?;
        if availability != "available" && reason_code.is_empty() {
            return Err(invalid("unavailable gateway source requires reason_code"));
        }
        Ok(Self {
            source_id,
            kind,
            availability,
            reason_code,
        })
    }
}

fn validate_sources(
    discovered: u64,
    unusable: u64,
    dormant: u64,
    sources: &[GatewaySource],
) -> Result<()> {
    if sources.is_empty() {
        return Ok(());
    }
    if usize::try_from(discovered).ok() != Some(sources.len()) {
        return Err(invalid(
            "gateway discovery source count does not match sources",
        ));
    }
    let unique = sources
        .iter()
        .map(|source| source.source_id.as_str())
        .collect::<std::collections::HashSet<_>>();
    let unavailable_count = sources
        .iter()
        .filter(|source| matches!(source.availability.as_str(), "unavailable" | "unknown"))
        .count();
    let dormant_count = sources
        .iter()
        .filter(|source| source.availability == "dormant")
        .count();
    if unique.len() != sources.len()
        || u64::try_from(unavailable_count).ok() != Some(unusable)
        || u64::try_from(dormant_count).ok() != Some(dormant)
    {
        return Err(invalid(
            "gateway discovery source details contradict counts",
        ));
    }
    Ok(())
}
