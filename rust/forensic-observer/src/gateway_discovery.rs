//! Publication and discovery contracts for gateway diagnostics.

use serde::Serialize;
use serde_json::Value;

use crate::error::Result;
use crate::gateway_validation::{
    exact_object, invalid, key_set, nonnegative_number, required, required_bool, required_text,
    required_u64, string_set,
};

#[derive(Clone, Debug, Serialize)]
pub struct GatewayPublication {
    registered: bool,
    heartbeat_at: f64,
    stale: bool,
}

impl GatewayPublication {
    pub fn from_value(value: &Value) -> Result<Self> {
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
pub struct GatewayDiscovery {
    enabled: bool,
    state: String,
    pending_work: u64,
    discovered_source_count: u64,
    unusable_source_count: u64,
    dormant_source_count: u64,
    sources: Vec<GatewaySource>,
}

impl GatewayDiscovery {
    pub fn from_value(value: &Value) -> Result<Self> {
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
    pub(crate) fn from_value(value: &Value) -> Result<Self> {
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
