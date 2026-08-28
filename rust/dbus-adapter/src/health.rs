// SPDX-License-Identifier: GPL-3.0-or-later
//! Bounded runtime health evidence preserving the established gateway states.

use std::collections::VecDeque;
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use crate::broker::DbusResult;
use crate::diagnostics::DiagnosticHealth;
use crate::energy::Clocks;
use crate::resources::ResourceSnapshot;

const WINDOW: Duration = Duration::from_secs(60);
const DEGRADED_TIMEOUTS: usize = 3;
const PROTECTIVE_TIMEOUTS: usize = 5;
const DEGRADED_HOLD: Duration = Duration::from_secs(60);
const PROTECTIVE_HOLD: Duration = Duration::from_secs(180);

mod evidence;
mod slo;
mod state;
mod support;

use evidence::{ProtectiveTriggerEvidence, operation_event, operation_summaries, trigger_value};
pub use slo::{SloObserved, SloSnapshot, SloThresholds, core_observed};
use state::{HealthStateLatch, performance_state, protective_cause};
use support::{bounded_text, count, looks_like_timeout, percentile};

#[derive(Clone, Debug)]
struct OperationEvent {
    at: Instant,
    latency_ms: f64,
    success: bool,
    timeout: bool,
    circuit_event: bool,
    kind: String,
}

#[derive(Clone, Debug)]
struct LoopEvent {
    at: Instant,
    callback_lateness_ms: f64,
    blocking_ms: f64,
}

#[derive(Clone, Debug)]
pub struct GatewayHealthSnapshot {
    pub state: String,
    pub operational_state: String,
    pub performance_state: String,
    pub backpressure_state: String,
    pub average_latency_ms: f64,
    pub maximum_latency_ms: f64,
    pub p95_latency_ms: f64,
    pub p99_latency_ms: f64,
    pub timeouts_60s: u64,
    pub errors_60s: u64,
    pub successes_60s: u64,
    pub maximum_callback_lateness_ms: f64,
    pub maximum_blocking_ms: f64,
    pub last_success_at: f64,
    pub last_error: String,
    pub last_error_code: String,
    pub state_changed_at: f64,
    pub state_recovery_pending: bool,
    pub degraded_until: f64,
    pub consecutive_failures: u64,
    pub active_protective_trigger: Value,
    pub last_protective_trigger: Value,
    pub protective_cause: String,
    pub operations: Value,
}

pub struct HealthPayloadContext<'a> {
    pub resources: &'a ResourceSnapshot,
    pub pending_gateway_commands: usize,
    pub pending_core_commands: usize,
    pub registered_path_count: usize,
    pub service_count: usize,
    pub introspection_queue_depth: usize,
    pub adaptive_tick_seconds: f64,
    pub last_tick_at: f64,
    pub mainloop_heartbeat_age_s: f64,
    pub last_tick_duration_ms: f64,
    pub cache_freshness: &'a Value,
    pub discovery_last_success_at: f64,
    pub discovery_last_error: &'a str,
    pub discovery_active_interval_s: f64,
    pub discovery_next_scan_in_s: f64,
    pub slo: &'a SloSnapshot,
    pub queue_scheduler: &'a Value,
    pub publication_freshness_deadline_s: f64,
    pub minimum_tick_seconds: f64,
    pub maximum_tick_seconds: f64,
    pub critical_read_operations: usize,
    pub critical_queue_operations: usize,
    pub operation_p95_ms: f64,
    pub dormant_energy_source_evidence: &'a Value,
    pub energy_source_unavailability_reasons: &'a Value,
}

impl GatewayHealthSnapshot {
    pub fn diagnostic(
        &self,
        resources: &ResourceSnapshot,
        pending_gateway_commands: usize,
        pending_core_commands: usize,
    ) -> DiagnosticHealth {
        DiagnosticHealth {
            state: self.state.clone(),
            operational_state: self.operational_state.clone(),
            performance_state: self.performance_state.clone(),
            resource_state: resources.state.as_str().to_owned(),
            average_latency_ms: self.average_latency_ms,
            maximum_latency_ms: self.maximum_latency_ms,
            timeouts_60s: self.timeouts_60s,
            pending_gateway_commands,
            pending_core_commands,
            maximum_event_loop_gap_ms_60s: self.maximum_callback_lateness_ms,
            last_success_at: self.last_success_at,
            last_error_code: self.last_error_code.clone(),
            active_protective_trigger: self.active_protective_trigger.clone(),
            last_protective_trigger: self.last_protective_trigger.clone(),
            protective_cause: self.protective_cause.clone(),
            resource_evidence: resources
                .pressure_evidence
                .as_ref()
                .and_then(|evidence| serde_json::to_value(evidence).ok())
                .unwrap_or(Value::Null),
        }
    }

    pub fn payload(&self, context: &HealthPayloadContext<'_>) -> Value {
        let resources = serde_json::to_value(context.resources).unwrap_or(Value::Null);
        let resource_evidence = resources
            .get("pressure_evidence")
            .cloned()
            .unwrap_or(Value::Null);
        json!({
            "state": self.state,
            "operational_state": self.operational_state,
            "performance_state": self.performance_state,
            "resource_state": resources["state"],
            "resource_evidence": resource_evidence,
            "resources": resources,
            "protective_cause": self.protective_cause,
            "state_changed_at": self.state_changed_at,
            "state_recovery_pending": self.state_recovery_pending,
            "degraded_until": self.degraded_until,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "last_error_code": self.last_error_code,
            "timeouts_60s": self.timeouts_60s,
            "errors_60s": self.errors_60s,
            "successes_60s": self.successes_60s,
            "consecutive_failures": self.consecutive_failures,
            "average_latency_ms": self.average_latency_ms,
            "avg_latency_ms": self.average_latency_ms,
            "maximum_latency_ms": self.maximum_latency_ms,
            "max_latency_ms": self.maximum_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "p99_latency_ms": self.p99_latency_ms,
            "active_protective_trigger": self.active_protective_trigger,
            "last_protective_trigger": self.last_protective_trigger,
            "operations": self.operations,
            "pending_command_count": context.pending_gateway_commands,
            "physical_command_count": context.pending_gateway_commands,
            "core_command_count": context.pending_core_commands,
            "registered_path_count": context.registered_path_count,
            "discovered_service_count": context.service_count,
            "introspection_queue_depth": context.introspection_queue_depth,
            "last_tick_at": context.last_tick_at,
            "tick_duration_ms": context.last_tick_duration_ms,
            "mainloop_heartbeat_age_s": context.mainloop_heartbeat_age_s,
            "adaptive_tick_seconds": context.adaptive_tick_seconds,
            "discovery_last_success_at": context.discovery_last_success_at,
            "discovery_last_error": context.discovery_last_error,
            "discovery_active_interval_s": context.discovery_active_interval_s,
            "discovery_next_scan_in_s": context.discovery_next_scan_in_s,
            "dormant_energy_source_ids": context.dormant_energy_source_evidence
                .as_array()
                .map(|items| items.iter().filter_map(|item| item["source_id"].as_str()).collect::<Vec<_>>())
                .unwrap_or_default(),
            "dormant_energy_source_evidence": context.dormant_energy_source_evidence,
            "energy_source_unavailability_reasons": context.energy_source_unavailability_reasons,
            "queues": {
                "pending_command_count": context.pending_gateway_commands,
                "physical_command_count": context.pending_gateway_commands,
                "core_command_count": context.pending_core_commands,
                "oldest_command_age_s": context.queue_scheduler["oldest_command_age_s"],
                "oldest_slo_command_age_s": context.queue_scheduler["oldest_slo_command_age_s"],
                "processed_commands_60s": context.queue_scheduler["processed_commands_60s"],
                "last_processed_at": context.queue_scheduler["last_processed_at"],
            },
            "queue_classes": context.queue_scheduler["queue_classes"],
            "write_scheduler": context.queue_scheduler,
            "backpressure": {
                "state": self.backpressure_state,
                "reason": if self.backpressure_state == "ok" { "" } else { "bounded-runtime-pressure" },
            },
            "cache_freshness": context.cache_freshness,
            "slo": context.slo.payload(),
            "publication_freshness_deadline_s": context.publication_freshness_deadline_s,
            "min_tick_seconds": context.minimum_tick_seconds,
            "max_tick_seconds": context.maximum_tick_seconds,
            "tick_demand": {
                "critical_read_operations": context.critical_read_operations,
                "critical_queue_operations": context.critical_queue_operations,
                "operation_p95_ms": context.operation_p95_ms,
            },
            "eventloop": {
                "last_tick_at": context.last_tick_at,
                "tick_duration_ms": context.last_tick_duration_ms,
                "mainloop_heartbeat_age_s": context.mainloop_heartbeat_age_s,
                "max_glib_callback_lateness_ms_60s": self.maximum_callback_lateness_ms,
                "max_blocking_time_ms_60s": self.maximum_blocking_ms,
                "max_tick_gap_ms_60s": self.maximum_callback_lateness_ms,
                "max_tick_duration_ms_60s": self.maximum_blocking_ms,
            },
        })
    }
}

pub struct GatewayHealthMonitor {
    operations: VecDeque<OperationEvent>,
    loop_events: VecDeque<LoopEvent>,
    degraded_until: Instant,
    protective_until: Instant,
    degraded_until_epoch: f64,
    protective_until_epoch: f64,
    last_success_at: f64,
    last_error: String,
    last_error_code: String,
    consecutive_failures: u64,
    last_protective_trigger: Option<ProtectiveTriggerEvidence>,
    aggregate: HealthStateLatch,
    previous_tick: Option<(Instant, Duration)>,
    slo_violated: bool,
    slo_pressure_state: &'static str,
}

impl GatewayHealthMonitor {
    pub fn new(clocks: Clocks) -> Self {
        let now = Instant::now();
        Self {
            operations: VecDeque::new(),
            loop_events: VecDeque::new(),
            degraded_until: now,
            protective_until: now,
            degraded_until_epoch: 0.0,
            protective_until_epoch: 0.0,
            last_success_at: 0.0,
            last_error: String::new(),
            last_error_code: String::new(),
            consecutive_failures: 0,
            last_protective_trigger: None,
            aggregate: HealthStateLatch::new(clocks.epoch),
            previous_tick: None,
            slo_violated: false,
            slo_pressure_state: "ok",
        }
    }

    pub fn record_operation(&mut self, result: &DbusResult, optional: bool, clocks: Clocks) {
        let now = Instant::now();
        let success = result.result.is_ok();
        let error = result.result.as_ref().err().map_or("", String::as_str);
        let timeout = !optional && !success && looks_like_timeout(error);
        let (event, source) = operation_event(
            &result.operation,
            optional,
            now,
            result.duration.as_secs_f64() * 1_000.0,
            success,
            timeout,
        );
        let kind = event.kind.clone();
        self.operations.push_back(event);
        self.prune(now);
        if success {
            self.last_success_at = clocks.epoch;
            self.last_error.clear();
            self.last_error_code.clear();
            self.consecutive_failures = 0;
        } else if !optional {
            self.last_error = bounded_text(error, 256);
            if timeout { "timeout" } else { "dbus-error" }.clone_into(&mut self.last_error_code);
            self.consecutive_failures = self.consecutive_failures.saturating_add(1);
        }
        if timeout {
            let count = self.operations.iter().filter(|event| event.timeout).count();
            if count > PROTECTIVE_TIMEOUTS {
                let was_protective = now < self.protective_until;
                self.protective_until = now + PROTECTIVE_HOLD;
                self.protective_until_epoch = clocks.epoch + PROTECTIVE_HOLD.as_secs_f64();
                if !was_protective || self.last_protective_trigger.is_none() {
                    self.last_protective_trigger = Some(ProtectiveTriggerEvidence {
                        triggered_at: clocks.epoch,
                        protective_until: self.protective_until_epoch,
                        timeout_count_60s: u64::try_from(count).unwrap_or(u64::MAX),
                        operation_kind: kind,
                        source: bounded_text(&source, 256),
                        error_code: "timeout".to_owned(),
                        latency_ms: result.duration.as_secs_f64() * 1_000.0,
                    });
                } else if let Some(trigger) = &mut self.last_protective_trigger {
                    trigger.protective_until = self.protective_until_epoch;
                }
            } else if count >= DEGRADED_TIMEOUTS {
                self.degraded_until = now + DEGRADED_HOLD;
                self.degraded_until_epoch = clocks.epoch + DEGRADED_HOLD.as_secs_f64();
            }
        }
    }

    pub fn record_tick(&mut self, started: Instant, duration: Duration, intended: Duration) {
        let lateness = self
            .previous_tick
            .map_or(0.0, |(previous, previous_intended)| {
                started
                    .saturating_duration_since(previous + previous_intended)
                    .as_secs_f64()
                    * 1_000.0
            });
        self.previous_tick = Some((started, intended));
        self.loop_events.push_back(LoopEvent {
            at: started,
            callback_lateness_ms: lateness,
            blocking_ms: duration.as_secs_f64() * 1_000.0,
        });
        self.prune(started);
    }

    pub fn operational_state(&self) -> &'static str {
        let now = Instant::now();
        if now < self.protective_until {
            "protective"
        } else if now < self.degraded_until {
            "degraded"
        } else {
            "ok"
        }
    }

    pub fn observe_slo(
        &mut self,
        violated: bool,
        queue_age_seconds: f64,
        queue_max_age_seconds: f64,
    ) {
        self.slo_violated = violated;
        self.slo_pressure_state = if queue_age_seconds > queue_max_age_seconds * 2.0 {
            "slow"
        } else if violated {
            "congested"
        } else {
            "ok"
        };
    }

    pub const fn slo_pressure_state(&self) -> &'static str {
        self.slo_pressure_state
    }

    pub fn operation_p95_ms(&self) -> f64 {
        let mut latencies = self
            .operations
            .iter()
            .map(|event| event.latency_ms)
            .collect::<Vec<_>>();
        latencies.sort_by(f64::total_cmp);
        percentile(&latencies, 95, 100)
    }

    pub fn maximum_callback_lateness_ms(&self) -> f64 {
        self.loop_events
            .iter()
            .map(|event| event.callback_lateness_ms)
            .fold(0.0, f64::max)
    }

    pub fn snapshot(
        &mut self,
        resources: &ResourceSnapshot,
        clocks: Clocks,
    ) -> GatewayHealthSnapshot {
        let now = Instant::now();
        self.prune(now);
        let operational = self.operational_state();
        let slo_violated = self.slo_violated;
        let backpressure = match operational {
            "protective" => "protective",
            "degraded" => "slow",
            _ => self.slo_pressure_state,
        };
        let performance = performance_state(resources, slo_violated, backpressure);
        let aggregate = self
            .aggregate
            .observe(operational, performance, now, clocks.epoch);
        let mut latencies = self
            .operations
            .iter()
            .map(|event| event.latency_ms)
            .collect::<Vec<_>>();
        latencies.sort_by(f64::total_cmp);
        let average = if latencies.is_empty() {
            0.0
        } else {
            let count = u32::try_from(latencies.len()).unwrap_or(u32::MAX);
            latencies.iter().sum::<f64>() / f64::from(count)
        };
        GatewayHealthSnapshot {
            state: aggregate.state.clone(),
            operational_state: operational.to_owned(),
            performance_state: performance.to_owned(),
            backpressure_state: backpressure.to_owned(),
            average_latency_ms: average,
            maximum_latency_ms: latencies.last().copied().unwrap_or(0.0),
            p95_latency_ms: percentile(&latencies, 95, 100),
            p99_latency_ms: percentile(&latencies, 99, 100),
            timeouts_60s: count(&self.operations, |event| event.timeout),
            errors_60s: count(&self.operations, |event| {
                event.circuit_event && !event.success
            }),
            successes_60s: count(&self.operations, |event| {
                event.circuit_event && event.success
            }),
            maximum_callback_lateness_ms: self
                .loop_events
                .iter()
                .map(|event| event.callback_lateness_ms)
                .fold(0.0, f64::max),
            maximum_blocking_ms: self
                .loop_events
                .iter()
                .map(|event| event.blocking_ms)
                .fold(0.0, f64::max),
            last_success_at: self.last_success_at,
            last_error: self.last_error.clone(),
            last_error_code: self.last_error_code.clone(),
            state_changed_at: aggregate.changed_at,
            state_recovery_pending: aggregate.recovery_pending,
            degraded_until: self.degraded_until_epoch.max(self.protective_until_epoch),
            consecutive_failures: self.consecutive_failures,
            active_protective_trigger: trigger_value(
                (now < self.protective_until)
                    .then_some(self.last_protective_trigger.as_ref())
                    .flatten(),
            ),
            last_protective_trigger: trigger_value(self.last_protective_trigger.as_ref()),
            protective_cause: protective_cause(
                &aggregate.state,
                operational,
                backpressure,
                resources,
            ),
            operations: operation_summaries(&self.operations),
        }
    }

    fn prune(&mut self, now: Instant) {
        while self
            .operations
            .front()
            .is_some_and(|event| now.saturating_duration_since(event.at) > WINDOW)
        {
            self.operations.pop_front();
        }
        while self
            .loop_events
            .front()
            .is_some_and(|event| now.saturating_duration_since(event.at) > WINDOW)
        {
            self.loop_events.pop_front();
        }
    }
}

#[cfg(test)]
mod tests;
