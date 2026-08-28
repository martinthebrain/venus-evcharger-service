// SPDX-License-Identifier: GPL-3.0-or-later
//! Bounded D-Bus operation attribution and protective trigger evidence.

use std::collections::{BTreeMap, VecDeque};
use std::time::Instant;

use serde::Serialize;
use serde_json::{Value, json};

use super::{OperationEvent, percentile};
use crate::broker::DbusOperation;

#[derive(Clone, Debug, Serialize)]
pub(super) struct ProtectiveTriggerEvidence {
    pub(super) triggered_at: f64,
    pub(super) protective_until: f64,
    pub(super) timeout_count_60s: u64,
    pub(super) operation_kind: String,
    pub(super) source: String,
    pub(super) error_code: String,
    pub(super) latency_ms: f64,
}

pub(super) fn operation_event(
    operation: &DbusOperation,
    optional: bool,
    at: Instant,
    latency_ms: f64,
    success: bool,
    timeout: bool,
) -> (OperationEvent, String) {
    let (kind, source) = operation_identity(operation, optional);
    (
        OperationEvent {
            at,
            latency_ms,
            success,
            timeout,
            circuit_event: !optional,
            kind: kind.to_owned(),
        },
        source,
    )
}

pub(super) fn trigger_value(trigger: Option<&ProtectiveTriggerEvidence>) -> Value {
    trigger
        .and_then(|value| serde_json::to_value(value).ok())
        .unwrap_or(Value::Null)
}

pub(super) fn operation_summaries(events: &VecDeque<OperationEvent>) -> Value {
    let mut grouped = BTreeMap::<&str, Vec<f64>>::new();
    for event in events {
        grouped
            .entry(event.kind.as_str())
            .or_default()
            .push(event.latency_ms);
    }
    Value::Object(
        grouped
            .into_iter()
            .map(|(kind, mut latencies)| {
                latencies.sort_by(f64::total_cmp);
                let timeout_count = events
                    .iter()
                    .filter(|event| event.kind == kind && event.timeout)
                    .count();
                (
                    kind.to_owned(),
                    json!({
                        "samples_60s": latencies.len(),
                        "timeouts_60s": timeout_count,
                        "p95_latency_ms": percentile(&latencies, 95, 100),
                        "p99_latency_ms": percentile(&latencies, 99, 100),
                        "max_latency_ms": latencies.last().copied().unwrap_or(0.0),
                    }),
                )
            })
            .collect(),
    )
}

fn operation_identity(operation: &DbusOperation, optional: bool) -> (&'static str, String) {
    match operation {
        DbusOperation::ListNames => ("discovery", "org.freedesktop.DBus".to_owned()),
        DbusOperation::Read { service, path } if optional => {
            ("optional_read", format!("{service}{path}"))
        }
        DbusOperation::Read { service, path } => ("read", format!("{service}{path}")),
        DbusOperation::Write { service, path, .. } => ("write", format!("{service}{path}")),
        DbusOperation::Introspect { service, path } => {
            ("introspection", format!("{service}{path}"))
        }
    }
}
