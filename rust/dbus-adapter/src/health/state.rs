// SPDX-License-Identifier: GPL-3.0-or-later
//! Stable aggregate health classification with delayed recovery.

use std::time::{Duration, Instant};

use crate::resources::{ResourceSnapshot, ResourceState};

const RECOVERY_HOLD: Duration = Duration::from_secs(10);

pub(super) struct HealthStateLatch {
    state: String,
    changed_at: f64,
    recovery: Option<(String, Instant)>,
}

pub(super) struct AggregateState {
    pub(super) state: String,
    pub(super) changed_at: f64,
    pub(super) recovery_pending: bool,
}

impl HealthStateLatch {
    pub(super) fn new(changed_at: f64) -> Self {
        Self {
            state: "ok".to_owned(),
            changed_at,
            recovery: None,
        }
    }

    pub(super) fn observe(
        &mut self,
        operational: &str,
        performance: &str,
        now: Instant,
        captured_at: f64,
    ) -> AggregateState {
        let desired = higher_state(operational, performance);
        if severity(desired) >= severity(&self.state) {
            if desired != self.state {
                desired.clone_into(&mut self.state);
                self.changed_at = captured_at.max(0.0);
            }
            self.recovery = None;
        } else {
            let started = match &self.recovery {
                Some((target, started)) if target == desired => *started,
                _ => now,
            };
            self.recovery = Some((desired.to_owned(), started));
            if now.saturating_duration_since(started) >= RECOVERY_HOLD {
                desired.clone_into(&mut self.state);
                self.changed_at = captured_at.max(0.0);
                self.recovery = None;
            }
        }
        AggregateState {
            state: self.state.clone(),
            changed_at: self.changed_at,
            recovery_pending: self.recovery.is_some(),
        }
    }
}

pub(super) fn performance_state(
    resources: &ResourceSnapshot,
    slo_violated: bool,
    backpressure: &str,
) -> &'static str {
    if resource_is_protective(resources) || backpressure == "protective" {
        "protective"
    } else if slo_violated || matches!(backpressure, "congested" | "slow") {
        "degraded"
    } else {
        "ok"
    }
}

pub(super) fn resource_is_protective(resources: &ResourceSnapshot) -> bool {
    resources.state == ResourceState::Constrained
        && resources
            .pressure_evidence
            .as_ref()
            .is_some_and(|evidence| {
                evidence
                    .causes
                    .iter()
                    .any(|cause| cause == "cpu" || cause == "memory")
            })
}

pub(super) fn protective_cause(
    aggregate: &str,
    operational: &str,
    backpressure: &str,
    resources: &ResourceSnapshot,
) -> String {
    if aggregate != "protective" {
        return String::new();
    }
    if operational == "protective" {
        return "circuit-breaker".to_owned();
    }
    if backpressure == "protective" {
        return "backpressure".to_owned();
    }
    if resource_is_protective(resources) {
        let causes = resources
            .pressure_evidence
            .as_ref()
            .map(|evidence| {
                evidence
                    .causes
                    .iter()
                    .filter(|cause| *cause == "cpu" || *cause == "memory")
                    .cloned()
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        if !causes.is_empty() {
            return format!("resource-{}", causes.join("+"));
        }
    }
    "recovery-hold".to_owned()
}

const fn higher_state<'a>(left: &'a str, right: &'a str) -> &'a str {
    if severity(left) >= severity(right) {
        left
    } else {
        right
    }
}

const fn severity(state: &str) -> u8 {
    match state.as_bytes() {
        b"protective" => 2,
        b"degraded" => 1,
        _ => 0,
    }
}

#[cfg(test)]
mod tests {
    use super::{HealthStateLatch, performance_state};
    use crate::resources::{ResourceSnapshot, ResourceState};

    fn resources(state: ResourceState) -> ResourceSnapshot {
        ResourceSnapshot {
            state,
            loadavg_1m: None,
            loadavg_5m: None,
            loadavg_15m: None,
            load_per_cpu_1m: None,
            system_cpu_pct: None,
            mem_total_kb: None,
            mem_available_kb: None,
            process_rss_kb: None,
            process_threads: None,
            cpu_count: 2,
            pressure_evidence: None,
        }
    }

    #[test]
    fn managed_load_pressure_throttles_without_degrading_health() {
        assert_eq!(
            performance_state(&resources(ResourceState::Constrained), false, "ok"),
            "ok"
        );
        assert_eq!(
            performance_state(&resources(ResourceState::Busy), false, "ok"),
            "ok"
        );
    }

    #[test]
    fn aggregate_escalates_immediately() {
        let mut latch = HealthStateLatch::new(10.0);
        let state = latch.observe("degraded", "ok", std::time::Instant::now(), 20.0);
        assert_eq!(state.state, "degraded");
        assert!(!state.recovery_pending);
    }
}
