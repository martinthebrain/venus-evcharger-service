// SPDX-License-Identifier: GPL-3.0-or-later
//! Adapter cadence and single-flight D-Bus rate limits.

use std::time::{Duration, Instant};

use crate::broker::DbusOperation;
use crate::config::IniConfig;
use crate::resources::ResourceState;

const SLO_BUDGET_FRACTION: f64 = 0.8;

#[derive(Clone, Copy, Debug, Default)]
pub(super) struct TickDemand {
    pub(super) critical_read_operations: usize,
    pub(super) critical_queue_operations: usize,
    pub(super) core_read_age_seconds: f64,
    pub(super) queue_age_seconds: f64,
    pub(super) operation_p95_ms: f64,
}

impl TickDemand {
    const fn operation_count(self) -> usize {
        self.critical_read_operations
            .saturating_add(self.critical_queue_operations)
    }
}

#[derive(Clone, Copy, Debug)]
pub(super) struct OperationRates {
    read_interval: Duration,
    write_interval: Duration,
    introspection_interval: Duration,
    pub(super) next_read: Instant,
    pub(super) next_write: Instant,
    pub(super) next_introspection: Instant,
}

impl OperationRates {
    pub(super) fn from_config(config: &IniConfig) -> Self {
        let now = Instant::now();
        Self {
            read_interval: configured_duration(config, "DbusGatewayReadIntervalSeconds", 0.25, 0.0),
            write_interval: configured_duration(
                config,
                "DbusGatewayWriteIntervalSeconds",
                0.35,
                0.0,
            ),
            introspection_interval: configured_duration(
                config,
                "DbusGatewayIntrospectionIntervalSeconds",
                2.0,
                0.0,
            ),
            next_read: now,
            next_write: now,
            next_introspection: now,
        }
    }

    pub(super) fn due(self, operation: &DbusOperation, now: Instant) -> bool {
        match operation {
            DbusOperation::ListNames | DbusOperation::Read { .. } => now >= self.next_read,
            DbusOperation::Write { .. } => now >= self.next_write,
            DbusOperation::Introspect { .. } => now >= self.next_introspection,
        }
    }

    pub(super) fn mark(&mut self, operation: &DbusOperation, now: Instant) {
        match operation {
            DbusOperation::ListNames | DbusOperation::Read { .. } => {
                self.next_read = now + self.read_interval;
            }
            DbusOperation::Write { .. } => {
                self.next_write = now + self.write_interval;
            }
            DbusOperation::Introspect { .. } => {
                self.next_introspection = now + self.introspection_interval;
            }
        }
    }
}

#[derive(Clone, Debug)]
pub(super) struct RuntimeIntervals {
    pub(super) minimum_tick: Duration,
    pub(super) maximum_tick: Duration,
    pub(super) energy_publish: Duration,
    pub(super) health_publish: Duration,
    pub(super) cache_publish: Duration,
    pub(super) dirty_cache_publish: Duration,
    pub(super) health_history: Option<Duration>,
    pub(super) stale_after_seconds: f64,
    pub(super) core_read_slo_seconds: f64,
    pub(super) queue_slo_seconds: f64,
    pub(super) mainloop_slo_ms: f64,
}

impl RuntimeIntervals {
    pub(super) fn from_config(config: &IniConfig) -> Self {
        let minimum_tick = configured_duration(
            config,
            "DbusGatewayMinTickSeconds",
            config.f64("DbusGatewayTickSeconds", 0.2),
            0.05,
        );
        let maximum_tick = configured_duration(
            config,
            "DbusGatewayMaxTickSeconds",
            1.0,
            minimum_tick.as_secs_f64(),
        );
        Self {
            minimum_tick,
            maximum_tick,
            energy_publish: configured_duration(
                config,
                "DbusGatewayEnergyPublishIntervalSeconds",
                1.0,
                0.2,
            ),
            health_publish: configured_duration(
                config,
                "DbusGatewayHealthPublishIntervalSeconds",
                1.0,
                0.2,
            ),
            cache_publish: configured_duration(
                config,
                "DbusGatewayFullCachePublishIntervalSeconds",
                10.0,
                0.2,
            ),
            dirty_cache_publish: configured_duration(
                config,
                "DbusGatewayFullCacheDirtyIntervalSeconds",
                2.0,
                0.2,
            ),
            health_history: positive_duration(config, "DbusGatewayHealthLogIntervalSeconds", 10.0),
            stale_after_seconds: config.f64("DbusGatewayStaleAfterSeconds", 10.0).max(0.0),
            core_read_slo_seconds: config
                .f64("DbusGatewaySloCoreReadMaxAgeSeconds", 5.0)
                .max(0.1),
            queue_slo_seconds: config
                .f64("DbusGatewaySloQueueMaxAgeSeconds", 10.0)
                .max(0.1),
            mainloop_slo_ms: config
                .f64("DbusGatewaySloMainloopGapMaxMs", 500.0)
                .max(10.0),
        }
    }

    pub(super) fn tick_for(
        &self,
        state: ResourceState,
        circuit_state: &str,
        demand: TickDemand,
    ) -> Duration {
        let minimum = self.minimum_tick.as_secs_f64().max(0.001);
        let maximum = self.maximum_tick.as_secs_f64().max(minimum);
        if circuit_state == "protective" {
            return self.maximum_tick;
        }
        let mut baseline = state_baseline(minimum, maximum, state, circuit_state);
        let operation_count = demand.operation_count();
        if operation_count == 0 {
            return Duration::from_secs_f64(baseline);
        }
        let service_floor = service_time_floor(minimum, maximum, state, demand);
        baseline = baseline.max(service_floor);
        let deadline = critical_deadline(self, demand);
        let operation_seconds = (demand.operation_p95_ms / 1_000.0).max(0.0);
        let count = f64::from(u32::try_from(operation_count).unwrap_or(u32::MAX));
        let available = deadline
            .mul_add(SLO_BUDGET_FRACTION, -(operation_seconds * count))
            .max(0.0);
        let deadline_tick = available / count;
        Duration::from_secs_f64(baseline.min(clamp(deadline_tick, service_floor, maximum)))
    }
}

fn service_time_floor(minimum: f64, maximum: f64, state: ResourceState, demand: TickDemand) -> f64 {
    if state == ResourceState::Ok || demand.critical_queue_operations > 0 {
        return minimum;
    }
    clamp(demand.operation_p95_ms.max(0.0) / 1_000.0, minimum, maximum)
}

fn state_baseline(minimum: f64, maximum: f64, state: ResourceState, circuit_state: &str) -> f64 {
    if state == ResourceState::Constrained {
        return maximum;
    }
    if circuit_state == "degraded" {
        return clamp((minimum * 2.5).max(0.5), minimum, maximum);
    }
    if state == ResourceState::Busy {
        return clamp((minimum * 1.5).max(0.3), minimum, maximum);
    }
    minimum
}

fn critical_deadline(intervals: &RuntimeIntervals, demand: TickDemand) -> f64 {
    let mut deadline = f64::INFINITY;
    if demand.critical_read_operations > 0 {
        deadline = deadline.min(
            (intervals.core_read_slo_seconds - demand.core_read_age_seconds.max(0.0)).max(0.0),
        );
    }
    if demand.critical_queue_operations > 0 {
        deadline = deadline
            .min((intervals.queue_slo_seconds - demand.queue_age_seconds.max(0.0)).max(0.0));
    }
    deadline
}

const fn clamp(value: f64, minimum: f64, maximum: f64) -> f64 {
    value.max(minimum).min(maximum)
}

pub(super) fn configured_duration(
    config: &IniConfig,
    key: &str,
    fallback: f64,
    minimum: f64,
) -> Duration {
    Duration::from_secs_f64(config.f64(key, fallback).max(minimum))
}

fn positive_duration(config: &IniConfig, key: &str, fallback: f64) -> Option<Duration> {
    let seconds = config.f64(key, fallback);
    (seconds > 0.0).then(|| Duration::from_secs_f64(seconds))
}

pub(super) fn configured_size(config: &IniConfig, key: &str, fallback: usize) -> usize {
    let fallback_i64 = i64::try_from(fallback).unwrap_or(i64::MAX);
    usize::try_from(config.i64(key, fallback_i64).max(0)).unwrap_or(fallback)
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::{RuntimeIntervals, TickDemand};
    use crate::config::IniConfig;
    use crate::resources::ResourceState;
    use crate::runtime_policy_contract;

    fn intervals() -> Result<RuntimeIntervals, String> {
        IniConfig::parse(
            "[DEFAULT]\nDbusGatewayMinTickSeconds=0.2\nDbusGatewayMaxTickSeconds=1\nDbusGatewaySloCoreReadMaxAgeSeconds=5\nDbusGatewaySloQueueMaxAgeSeconds=10\n",
        )
        .map(|config| RuntimeIntervals::from_config(&config))
    }

    #[test]
    fn idle_and_pressure_baselines_match_the_python_tick_policy() -> Result<(), String> {
        let intervals = intervals()?;
        assert_eq!(
            intervals.tick_for(ResourceState::Ok, "ok", TickDemand::default()),
            Duration::from_millis(200)
        );
        assert_eq!(
            intervals.tick_for(ResourceState::Busy, "ok", TickDemand::default()),
            Duration::from_millis(300)
        );
        assert_eq!(
            intervals.tick_for(ResourceState::Ok, "degraded", TickDemand::default()),
            Duration::from_millis(500)
        );
        Ok(())
    }

    #[test]
    fn critical_deadline_accelerates_work_before_the_slo_expires() -> Result<(), String> {
        let intervals = intervals()?;
        let demand = TickDemand {
            critical_read_operations: 2,
            core_read_age_seconds: 4.5,
            operation_p95_ms: 50.0,
            ..TickDemand::default()
        };
        assert_eq!(
            intervals.tick_for(ResourceState::Busy, "ok", demand),
            Duration::from_millis(200)
        );
        Ok(())
    }

    #[test]
    fn generated_python_tick_scenarios_match() -> Result<(), String> {
        let contract = runtime_policy_contract::load()?.tick_policy;
        let config = IniConfig::parse(&format!(
            "[DEFAULT]\nDbusGatewayMinTickSeconds={}\nDbusGatewayMaxTickSeconds={}\nDbusGatewaySloCoreReadMaxAgeSeconds={}\nDbusGatewaySloQueueMaxAgeSeconds={}\n",
            contract.policy.minimum_tick,
            contract.policy.maximum_tick,
            contract.policy.core_read_slo,
            contract.policy.queue_slo,
        ))?;
        let intervals = RuntimeIntervals::from_config(&config);
        for case in contract.cases {
            let resource = match case.resource_state.as_str() {
                "ok" => ResourceState::Ok,
                "busy" => ResourceState::Busy,
                "constrained" => ResourceState::Constrained,
                state => return Err(format!("unknown contract resource state: {state}")),
            };
            let demand = TickDemand {
                critical_read_operations: case.demand.critical_read_operations,
                critical_queue_operations: case.demand.critical_queue_operations,
                core_read_age_seconds: case.demand.core_read_age_seconds,
                queue_age_seconds: case.demand.queue_age_seconds,
                operation_p95_ms: case.demand.operation_p95_ms,
            };
            let actual = intervals
                .tick_for(resource, &case.circuit_state, demand)
                .as_secs_f64();
            assert!((actual - case.tick_seconds).abs() < 1e-9);
        }
        Ok(())
    }
}
