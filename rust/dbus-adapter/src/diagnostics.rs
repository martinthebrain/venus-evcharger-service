// SPDX-License-Identifier: GPL-3.0-or-later
//! Strict transport-neutral diagnostics consumed by the observer and probes.

use std::path::Path;

use serde_json::{Value, json};

use crate::energy::{Clocks, EnergyTopology};
use crate::mailbox::atomic_json;
use crate::publication::{PublicationFieldObservation, PublicationRegistry};

const DIAGNOSTIC_FIELDS: [&str; 10] = [
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

#[derive(Clone, Debug)]
pub struct DiagnosticHealth {
    pub state: String,
    pub operational_state: String,
    pub performance_state: String,
    pub resource_state: String,
    pub average_latency_ms: f64,
    pub maximum_latency_ms: f64,
    pub timeouts_60s: u64,
    pub pending_gateway_commands: usize,
    pub pending_core_commands: usize,
    pub maximum_event_loop_gap_ms_60s: f64,
    pub last_success_at: f64,
    pub last_error_code: String,
    pub active_protective_trigger: Value,
    pub last_protective_trigger: Value,
    pub protective_cause: String,
    pub resource_evidence: Value,
}

pub struct GatewayDiagnosticsContext<'a> {
    pub clocks: Clocks,
    pub health: &'a DiagnosticHealth,
    pub topology: &'a EnergyTopology,
    pub registry: &'a PublicationRegistry,
    pub stale_after_seconds: f64,
    pub dormant_evidence: &'a Value,
    pub unavailability_reasons: &'a Value,
}

pub fn write_gateway_diagnostics(
    path: &Path,
    sequence: u64,
    context: &GatewayDiagnosticsContext<'_>,
) -> Result<(), String> {
    let payload = json!({
        "schema_version": 5,
        "sequence": sequence,
        "captured_at": context.clocks.epoch,
        "captured_monotonic": context.clocks.monotonic,
        "health": health_payload(context.health),
        "discovery": discovery_payload(
            context.topology,
            context.dormant_evidence,
            context.unavailability_reasons,
        ),
        "publication": publication_payload(
            context.registry,
            context.clocks.epoch,
            context.clocks.monotonic,
            context.stale_after_seconds,
        ),
        "ev_charger": diagnostic_samples(
            context.registry,
            context.clocks.epoch,
            context.clocks.monotonic,
            context.stale_after_seconds,
        ),
    });
    atomic_json(path, &payload)
}

fn health_payload(health: &DiagnosticHealth) -> Value {
    json!({
        "state": health.state,
        "stale": false,
        "timeouts_60s": health.timeouts_60s,
        "average_latency_ms": health.average_latency_ms,
        "maximum_latency_ms": health.maximum_latency_ms.max(health.average_latency_ms),
        "pending_gateway_commands": health.pending_gateway_commands,
        "pending_core_commands": health.pending_core_commands,
        "maximum_event_loop_gap_ms_60s": health.maximum_event_loop_gap_ms_60s,
        "last_success_at": health.last_success_at,
        "last_error_code": health.last_error_code,
        "active_protective_trigger": health.active_protective_trigger,
        "last_protective_trigger": health.last_protective_trigger,
        "operational_state": health.operational_state,
        "performance_state": health.performance_state,
        "resource_state": health.resource_state,
        "protective_cause": health.protective_cause,
        "resource_evidence": health.resource_evidence,
    })
}

fn discovery_payload(
    topology: &EnergyTopology,
    dormant_evidence: &Value,
    unavailability_reasons: &Value,
) -> Value {
    let sources = topology
        .sources
        .iter()
        .map(|source| {
            let dormant = dormant_evidence.as_array().is_some_and(|items| {
                items
                    .iter()
                    .any(|item| item["source_id"].as_str() == Some(source.source_id.as_str()))
            });
            let explicit_reason = unavailability_reasons
                .get(&source.source_id)
                .and_then(Value::as_str);
            let (availability, reason) = if dormant {
                ("dormant", "pv-sleep-confirmed")
            } else if let Some(reason) = explicit_reason {
                ("unavailable", reason)
            } else {
                match source.state.as_str() {
                    "online" => ("available", ""),
                    "offline" => ("unavailable", "source-not-advertising"),
                    _ => ("unknown", "source-state-unknown"),
                }
            };
            json!({
                "source_id": source.source_id,
                "kind": source.kind,
                "availability": availability,
                "reason_code": reason,
            })
        })
        .collect::<Vec<_>>();
    let unavailable = sources
        .iter()
        .filter(|source| source["availability"] != "available")
        .count();
    let dormant = sources
        .iter()
        .filter(|source| source["availability"] == "dormant")
        .count();
    json!({
        "enabled": true,
        "state": if topology.generation == 0 { "unknown" } else { "idle" },
        "pending_work": 0,
        "discovered_source_count": sources.len(),
        "unusable_source_count": unavailable,
        "dormant_source_count": dormant,
        "sources": sources,
    })
}

fn publication_payload(
    registry: &PublicationRegistry,
    captured_at: f64,
    captured_monotonic: f64,
    stale_after_seconds: f64,
) -> Value {
    let (heartbeat_at, heartbeat_monotonic) = registry.evcs_heartbeat();
    let registered = registry.evcs_registered() && heartbeat_at > 0.0 && heartbeat_monotonic > 0.0;
    json!({
        "registered": registered,
        "heartbeat_at": if registered { heartbeat_at.min(captured_at) } else { 0.0 },
        "stale": !registered
            || captured_monotonic - heartbeat_monotonic > stale_after_seconds.max(0.0),
    })
}

fn diagnostic_samples(
    registry: &PublicationRegistry,
    captured_at: f64,
    captured_monotonic: f64,
    stale_after_seconds: f64,
) -> Vec<Value> {
    if !registry.evcs_registered() {
        return DIAGNOSTIC_FIELDS.into_iter().map(unknown_sample).collect();
    }
    let service_heartbeat_monotonic = Some(registry.evcs_heartbeat().1);
    let mode = observed_sample(
        "operating_mode",
        registry.evcs_field_observation("mode"),
        ValueKind::Mode,
        captured_at,
        captured_monotonic,
        stale_after_seconds,
        service_heartbeat_monotonic,
    );
    let inactive = matches!(mode.get("value").and_then(Value::as_i64), Some(0 | 2));
    let active = observed_sample(
        "runtime_overrides_active",
        registry.evcs_field_observation("auto_runtime_overrides_active"),
        ValueKind::Boolean,
        captured_at,
        captured_monotonic,
        stale_after_seconds,
        None,
    );
    let mut samples = vec![
        mode,
        observed_sample(
            "charging_enabled",
            registry
                .evcs_field_observation("start_stop")
                .or_else(|| registry.evcs_field_observation("enable")),
            ValueKind::Boolean,
            captured_at,
            captured_monotonic,
            stale_after_seconds,
            service_heartbeat_monotonic,
        ),
        observed_sample(
            "auto_start_enabled",
            registry.evcs_field_observation("auto_start"),
            ValueKind::Boolean,
            captured_at,
            captured_monotonic,
            stale_after_seconds,
            service_heartbeat_monotonic,
        ),
        observed_sample(
            "ac_power_w",
            registry.evcs_field_observation("ac_power_w"),
            ValueKind::Number,
            captured_at,
            captured_monotonic,
            stale_after_seconds,
            None,
        ),
        observed_sample(
            "charger_state_code",
            registry.evcs_field_observation("status"),
            ValueKind::NonNegativeInteger,
            captured_at,
            captured_monotonic,
            stale_after_seconds,
            None,
        ),
        observed_sample(
            "decision_reason",
            registry.evcs_field_observation("auto_decision_reason"),
            ValueKind::Text,
            captured_at,
            captured_monotonic,
            stale_after_seconds,
            None,
        ),
        observed_sample(
            "decision_state",
            registry.evcs_field_observation("auto_decision_state"),
            ValueKind::Text,
            captured_at,
            captured_monotonic,
            stale_after_seconds,
            None,
        ),
        observed_sample(
            "last_health_reason",
            registry.evcs_field_observation("auto_health"),
            ValueKind::Text,
            captured_at,
            captured_monotonic,
            stale_after_seconds,
            None,
        ),
        active.clone(),
        override_source_sample(&active),
    ];
    if inactive {
        for sample in samples.iter_mut().skip(5) {
            *sample = inactive_sample(sample);
        }
    }
    samples
}

#[derive(Clone, Copy)]
enum ValueKind {
    Mode,
    Boolean,
    Number,
    NonNegativeInteger,
    Text,
}

fn observed_sample(
    name: &str,
    observation: Option<&PublicationFieldObservation>,
    kind: ValueKind,
    captured_at: f64,
    captured_monotonic: f64,
    stale_after_seconds: f64,
    service_heartbeat_monotonic: Option<f64>,
) -> Value {
    let Some(observation) = observation else {
        return unavailable_sample(name);
    };
    let Some(value) = normalized_value(&observation.value, kind) else {
        return json!({
            "name": name,
            "value": null,
            "status": "error",
            "changed_at": 0.0,
            "confirmed_at": 0.0,
            "confidence": 0.0,
            "applicability": "applicable",
            "reason_code": "invalid-publication-value",
        });
    };
    let confirmed_monotonic = service_heartbeat_monotonic
        .map_or(observation.confirmed_monotonic, |heartbeat| {
            observation.confirmed_monotonic.max(heartbeat)
        });
    let stale = captured_monotonic - confirmed_monotonic > stale_after_seconds.max(0.0);
    json!({
        "name": name,
        "value": value,
        "status": if stale { "stale" } else { "fresh" },
        "changed_at": observation.changed_at.min(captured_at),
        "confirmed_at": observation.confirmed_at.min(captured_at),
        "confidence": if stale { 0.5 } else { 1.0 },
        "applicability": "applicable",
        "reason_code": if stale { "publication-stale" } else { "" },
    })
}

fn normalized_value(value: &Value, kind: ValueKind) -> Option<Value> {
    match kind {
        ValueKind::Mode => value
            .as_i64()
            .filter(|mode| matches!(*mode, 0..=2))
            .map(Value::from),
        ValueKind::Boolean => match value {
            Value::Bool(item) => Some(Value::Bool(*item)),
            Value::Number(item) if item.as_i64().is_some_and(|number| matches!(number, 0 | 1)) => {
                Some(Value::Bool(item.as_i64() == Some(1)))
            }
            _ => None,
        },
        ValueKind::Number => value
            .as_f64()
            .filter(|number| number.is_finite())
            .map(Value::from),
        ValueKind::NonNegativeInteger => value
            .as_i64()
            .filter(|number| *number >= 0)
            .map(Value::from),
        ValueKind::Text => value.as_str().map(|text| Value::String(text.to_owned())),
    }
}

fn override_source_sample(active: &Value) -> Value {
    let Some(value) = active.get("value").and_then(Value::as_bool) else {
        return unknown_sample("runtime_overrides_source");
    };
    json!({
        "name": "runtime_overrides_source",
        "value": if value { "runtime-overrides" } else { "static-configuration" },
        "status": active["status"],
        "changed_at": active["changed_at"],
        "confirmed_at": active["confirmed_at"],
        "confidence": active["confidence"],
        "applicability": active["applicability"],
        "reason_code": active["reason_code"],
    })
}

fn inactive_sample(sample: &Value) -> Value {
    json!({
        "name": sample["name"],
        "value": sample["value"],
        "status": "inactive",
        "changed_at": sample["changed_at"],
        "confirmed_at": sample["confirmed_at"],
        "confidence": 1.0,
        "applicability": "not-applicable",
        "reason_code": "operating-mode-not-auto",
    })
}

fn unavailable_sample(name: &str) -> Value {
    json!({
        "name": name,
        "value": null,
        "status": "unavailable",
        "changed_at": 0.0,
        "confirmed_at": 0.0,
        "confidence": 0.0,
        "applicability": "applicable",
        "reason_code": "field-unavailable",
    })
}

fn unknown_sample(name: &str) -> Value {
    json!({
        "name": name,
        "value": null,
        "status": "unknown",
        "changed_at": 0.0,
        "confirmed_at": 0.0,
        "confidence": 0.0,
        "applicability": "unknown",
        "reason_code": "",
    })
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{ValueKind, normalized_value, observed_sample};
    use crate::publication::PublicationFieldObservation;

    #[test]
    fn diagnostic_scalars_are_semantically_bounded() {
        assert_eq!(normalized_value(&json!(2), ValueKind::Mode), Some(json!(2)));
        assert_eq!(normalized_value(&json!(3), ValueKind::Mode), None);
        assert_eq!(
            normalized_value(&json!(1), ValueKind::Boolean),
            Some(json!(true))
        );
        assert_eq!(
            normalized_value(&json!(-1), ValueKind::NonNegativeInteger),
            None
        );
    }

    #[test]
    fn control_freshness_uses_only_the_monotonic_service_heartbeat() {
        let observation = PublicationFieldObservation {
            value: json!(2),
            changed_at: 80.0,
            confirmed_at: 90.0,
            confirmed_monotonic: 90.0,
        };
        let stale = observed_sample(
            "operating_mode",
            Some(&observation),
            ValueKind::Mode,
            100.0,
            100.0,
            5.0,
            None,
        );
        let heartbeat_fresh = observed_sample(
            "operating_mode",
            Some(&observation),
            ValueKind::Mode,
            100.0,
            100.0,
            5.0,
            Some(98.0),
        );

        assert_eq!(stale["status"], "stale");
        assert_eq!(heartbeat_fresh["status"], "fresh");
        assert_eq!(heartbeat_fresh["confirmed_at"], 90.0);
    }
}
