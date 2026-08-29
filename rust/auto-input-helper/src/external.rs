//! Fair, bounded polling and coherent projections for external energy sources.

use std::collections::BTreeMap;
use std::time::Duration;

use rustix::time::{ClockId, clock_gettime};
use serde_json::{Map, Value, json};

use crate::capacity_persistence::CapacityEstimate;
use crate::config::HelperConfig;
use crate::connectors::{EnergyConnector, build_connector};
use crate::energy::{
    EnergyClusterSnapshot, EnergyLearningProfile, EnergyRole, EnergySourceDefinition,
    EnergySourceSnapshot, ExternalPollingPolicy, ExternalSourcePoll,
    MAX_EXTERNAL_CYCLE_BUDGET_SECONDS, MeasurementStatus, ProjectedEnergyValue, PvProjectionPolicy,
    aggregate_energy_sources, number,
};
use crate::forecast::derive_energy_forecast;
use crate::learning::update_learning_profile;
use crate::wire::{EnergyInputs, Measurement};

/// Fresh, transport-neutral battery measurements supplied by the gateway.
#[derive(Clone, Copy, Debug, Default)]
pub struct GatewayBatteryMeasurements<'a> {
    soc: Option<&'a Measurement>,
    net_power: Option<&'a Measurement>,
    capacity_wh: Option<&'a Measurement>,
    capacity_ah: Option<&'a Measurement>,
    voltage: Option<&'a Measurement>,
}

impl<'a> GatewayBatteryMeasurements<'a> {
    /// Select usable semantic measurements without exposing gateway transport details.
    #[must_use]
    pub fn from_snapshot(
        snapshot: Option<&'a EnergyInputs>,
        now_monotonic: f64,
        maximum_age: f64,
    ) -> Self {
        let Some(snapshot) = snapshot else {
            return Self::default();
        };
        let usable = |measurement: &'a Measurement| {
            measurement
                .usable(now_monotonic, maximum_age)
                .then_some(measurement)
        };
        let positive = |measurement: &'a Measurement| {
            usable(measurement).filter(|item| item.value.is_some_and(|value| value > 0.0))
        };
        Self {
            soc: usable(&snapshot.battery_soc).filter(|item| {
                item.value
                    .is_some_and(|value| (0.0..=100.0).contains(&value))
            }),
            net_power: usable(&snapshot.battery_net_power_w),
            capacity_wh: positive(&snapshot.battery_capacity_wh),
            capacity_ah: positive(&snapshot.battery_capacity_ah),
            voltage: positive(&snapshot.battery_voltage_v),
        }
    }

    const fn any(self) -> bool {
        self.soc.is_some()
            || self.net_power.is_some()
            || self.capacity_wh.is_some()
            || self.capacity_ah.is_some()
            || self.voltage.is_some()
    }
}

struct SourceState {
    connector: Option<Box<dyn EnergyConnector>>,
    last_good: Option<EnergySourceSnapshot>,
    last_good_monotonic: Option<f64>,
    attempted_at: Option<f64>,
    next_poll_monotonic: f64,
    consecutive_failures: u32,
    last_error: String,
    in_progress: bool,
}

impl SourceState {
    const fn new() -> Self {
        Self {
            connector: None,
            last_good: None,
            last_good_monotonic: None,
            attempted_at: None,
            next_poll_monotonic: 0.0,
            consecutive_failures: 0,
            last_error: String::new(),
            in_progress: false,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum AttemptStatus {
    Success,
    Failed,
    InProgress,
}

impl AttemptStatus {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Success => "success",
            Self::Failed => "failed",
            Self::InProgress => "in_progress",
        }
    }
}

/// One round-robin scheduler that admits at most one external operation per cycle.
pub struct ExternalSourceScheduler {
    definitions: Vec<EnergySourceDefinition>,
    states: Vec<SourceState>,
    policy: ExternalPollingPolicy,
    request_timeout_seconds: f64,
    cursor: usize,
}

impl ExternalSourceScheduler {
    /// Create an initially idle scheduler. Connector files are loaded lazily.
    #[must_use]
    pub fn new(
        definitions: Vec<EnergySourceDefinition>,
        policy: ExternalPollingPolicy,
        request_timeout_seconds: f64,
    ) -> Self {
        let states = definitions.iter().map(|_| SourceState::new()).collect();
        Self {
            definitions,
            states,
            policy,
            request_timeout_seconds,
            cursor: 0,
        }
    }

    /// Poll one due source and return diagnostics for every configured source.
    #[must_use]
    pub fn poll(&mut self, epoch: f64, cycle_monotonic: f64) -> Vec<ExternalSourcePoll> {
        let mut attempted = None;
        if let Some(index) = self.next_due(cycle_monotonic) {
            attempted = Some((index, self.attempt(index, epoch, cycle_monotonic)));
        }
        let result_monotonic = monotonic_now();
        self.definitions
            .iter()
            .enumerate()
            .map(|(index, definition)| {
                let status = attempted
                    .filter(|(attempted_index, _)| *attempted_index == index)
                    .map(|(_, status)| status);
                poll_result(
                    definition,
                    &self.states[index],
                    self.policy,
                    epoch,
                    cycle_monotonic,
                    result_monotonic,
                    status,
                )
            })
            .collect()
    }

    fn next_due(&mut self, monotonic: f64) -> Option<usize> {
        let count = self.definitions.len();
        for offset in 0..count {
            let index = (self.cursor + offset) % count;
            if monotonic >= self.states[index].next_poll_monotonic {
                self.cursor = (index + 1) % count;
                return Some(index);
            }
        }
        None
    }

    fn attempt(&mut self, index: usize, epoch: f64, cycle_monotonic: f64) -> AttemptStatus {
        let definition = &self.definitions[index];
        let state = &mut self.states[index];
        state.attempted_at = Some(epoch);
        let deadline = cycle_monotonic
            + self
                .policy
                .cycle_budget_seconds
                .min(MAX_EXTERNAL_CYCLE_BUDGET_SECONDS);
        let timeout = self
            .request_timeout_seconds
            .min((deadline - monotonic_now()).max(0.001));
        if state.connector.is_none() {
            match build_connector(definition, self.request_timeout_seconds) {
                Ok(connector) => state.connector = Some(connector),
                Err(error) => {
                    record_failure(state, self.policy, monotonic_now(), error.to_string());
                    return AttemptStatus::Failed;
                }
            }
        }
        let result = state.connector.as_mut().map_or_else(
            || {
                Err(crate::error::HelperError::Runtime(
                    "connector disappeared".to_owned(),
                ))
            },
            |connector| connector.read_step(definition, epoch, timeout),
        );
        let completed_monotonic = monotonic_now();
        match result {
            Ok(Some(mut snapshot)) if confirms_measurement(&snapshot) => {
                snapshot.source_id.clone_from(&definition.source_id);
                snapshot.role = definition.role;
                snapshot.physical_id.clone_from(&definition.physical_id);
                snapshot.physical_priority = definition.physical_priority;
                state.last_good = Some(snapshot);
                state.last_good_monotonic = Some(completed_monotonic);
                state.consecutive_failures = 0;
                state.last_error.clear();
                state.in_progress = false;
                state.next_poll_monotonic = completed_monotonic + self.policy.poll_interval_seconds;
                AttemptStatus::Success
            }
            Ok(Some(_)) => {
                record_failure(
                    state,
                    self.policy,
                    completed_monotonic,
                    "source reported no online measurement".to_owned(),
                );
                AttemptStatus::Failed
            }
            Ok(None) => {
                state.in_progress = true;
                state.last_error.clear();
                state.next_poll_monotonic = completed_monotonic;
                AttemptStatus::InProgress
            }
            Err(error) => {
                record_failure(state, self.policy, completed_monotonic, error.to_string());
                AttemptStatus::Failed
            }
        }
    }
}

fn record_failure(
    state: &mut SourceState,
    policy: ExternalPollingPolicy,
    monotonic: f64,
    message: String,
) {
    state.consecutive_failures = state.consecutive_failures.saturating_add(1);
    state.last_error = message;
    state.in_progress = false;
    state.next_poll_monotonic = monotonic + backoff_seconds(policy, state.consecutive_failures);
}

fn poll_result(
    definition: &EnergySourceDefinition,
    state: &SourceState,
    policy: ExternalPollingPolicy,
    epoch: f64,
    cycle_monotonic: f64,
    result_monotonic: f64,
    attempted: Option<AttemptStatus>,
) -> ExternalSourcePoll {
    let age = state.last_good_monotonic.and_then(|observed| {
        (observed.is_finite() && observed >= 0.0 && result_monotonic >= observed)
            .then_some(result_monotonic - observed)
    });
    let contributing = age.is_some_and(|value| value <= policy.last_good_max_age_seconds);
    let measurement_status = match (age, contributing, state.consecutive_failures) {
        (None, _, _) => MeasurementStatus::Missing,
        (Some(_), false, _) => MeasurementStatus::Expired,
        (Some(_), true, 0) => MeasurementStatus::Fresh,
        (Some(_), true, _) => MeasurementStatus::Stale,
    };
    let poll_status = attempted.map_or_else(
        || {
            if state.in_progress {
                "in_progress"
            } else if result_monotonic >= state.next_poll_monotonic {
                "deferred_budget"
            } else if state.consecutive_failures > 0 {
                "backoff"
            } else {
                "idle"
            }
        },
        AttemptStatus::as_str,
    );
    ExternalSourcePoll {
        snapshot: state
            .last_good
            .clone()
            .unwrap_or_else(|| EnergySourceSnapshot::offline(definition)),
        contributing,
        poll_status: poll_status.to_owned(),
        measurement_status,
        attempted_at: state.attempted_at,
        observed_at: state.last_good.as_ref().and_then(|item| item.captured_at),
        observed_monotonic: state.last_good_monotonic,
        next_poll_at: epoch + (state.next_poll_monotonic - cycle_monotonic).max(0.0),
        age_seconds: age,
        consecutive_failures: state.consecutive_failures,
        last_error: state.last_error.clone(),
    }
}

fn confirms_measurement(snapshot: &EnergySourceSnapshot) -> bool {
    snapshot.online
        && snapshot
            .captured_at
            .is_some_and(|value| value.is_finite() && value >= 0.0)
        && snapshot.has_contributing_value()
}

fn backoff_seconds(policy: ExternalPollingPolicy, failures: u32) -> f64 {
    let exponent = failures.saturating_sub(1).min(30);
    let multiplier = f64::from(2_u32.saturating_pow(exponent));
    policy
        .backoff_max_seconds
        .min(policy.backoff_base_seconds * multiplier)
}

fn monotonic_now() -> f64 {
    let timestamp = clock_gettime(ClockId::Monotonic);
    Duration::try_from(timestamp).map_or(0.0, |duration| duration.as_secs_f64())
}

/// One coherent external cycle reused by battery, PV, and grid projections.
#[derive(Clone, Debug)]
pub struct ExternalEnergyCycle {
    pub battery: Map<String, Value>,
    pub pv: Option<ProjectedEnergyValue>,
    pub battery_observed_at: Option<f64>,
    pub battery_observed_monotonic: Option<f64>,
    pub polls: Vec<ExternalSourcePoll>,
    pub capacity_estimate: Option<CapacityEstimate>,
}

/// Domain owner for scheduler state, aggregation, learning, and PV selection.
pub struct ConfiguredEnergySources {
    scheduler: ExternalSourceScheduler,
    definitions: Vec<EnergySourceDefinition>,
    use_combined_soc: bool,
    pv_policy: PvProjectionPolicy,
    gateway_source_id: String,
    gateway_definition: Option<EnergySourceDefinition>,
    learning_profiles: BTreeMap<String, EnergyLearningProfile>,
}

impl ConfiguredEnergySources {
    /// Build all transport-neutral runtime state from validated configuration.
    #[must_use]
    pub fn new(config: &HelperConfig) -> Self {
        Self {
            scheduler: ExternalSourceScheduler::new(
                config.energy_sources.clone(),
                config.external_polling,
                config.energy_source_request_timeout_seconds,
            ),
            definitions: config.energy_sources.clone(),
            use_combined_soc: config.use_combined_battery_soc,
            pv_policy: config.pv_projection.clone(),
            gateway_source_id: config.grid_fusion.backup_source_id.clone(),
            gateway_definition: config.gateway_energy_source.clone(),
            learning_profiles: BTreeMap::new(),
        }
    }

    /// Return whether at least one non-DBus source is configured.
    #[must_use]
    pub const fn enabled(&self) -> bool {
        !self.definitions.is_empty() || self.gateway_definition.is_some()
    }

    /// Poll once and derive a coherent source snapshot.
    #[must_use]
    pub fn collect_cycle(
        &mut self,
        gateway_measurements: GatewayBatteryMeasurements<'_>,
        epoch: f64,
        monotonic: f64,
    ) -> ExternalEnergyCycle {
        let polls = self.scheduler.poll(epoch, monotonic);
        let contributing: Vec<EnergySourceSnapshot> = polls
            .iter()
            .filter(|poll| poll.contributing)
            .map(|poll| poll.snapshot.clone())
            .collect();
        for poll in &polls {
            if poll.contributing && poll.poll_status == "success" {
                update_learning_profile(
                    self.learning_profiles
                        .entry(poll.snapshot.source_id.clone())
                        .or_insert_with(|| {
                            EnergyLearningProfile::new(poll.snapshot.source_id.clone())
                        }),
                    &poll.snapshot,
                    epoch,
                );
            }
        }
        let gateway = gateway_source(
            gateway_measurements,
            &self.gateway_source_id,
            self.gateway_definition.as_ref(),
        );
        let capacity_estimate = inferred_capacity_candidate(
            gateway_measurements,
            self.gateway_definition.as_ref(),
            &self.gateway_source_id,
        );
        let mut aggregate_sources = contributing.clone();
        if let Some(source) = &gateway {
            aggregate_sources.push(source.clone());
        }
        let cluster = aggregate_energy_sources(&aggregate_sources);
        let selection = select_soc(
            &cluster,
            &contributing,
            &polls,
            gateway.as_ref(),
            gateway_measurements.soc,
            self.use_combined_soc,
        );
        let battery = battery_payload(
            selection.0,
            &cluster,
            &polls,
            gateway.as_ref(),
            gateway_measurements.soc,
            &self.learning_profiles,
            &self.definitions,
        );
        ExternalEnergyCycle {
            battery,
            pv: external_pv_projection(&polls, &self.pv_policy.external_source_id),
            battery_observed_at: selection.1,
            battery_observed_monotonic: selection.2,
            polls,
            capacity_estimate,
        }
    }
}

fn inferred_capacity_candidate(
    measurements: GatewayBatteryMeasurements<'_>,
    definition: Option<&EnergySourceDefinition>,
    fallback_source_id: &str,
) -> Option<CapacityEstimate> {
    let definition = definition?;
    let installed_capacity_ah = measurements.capacity_ah.and_then(|item| item.value)?;
    let (usable_capacity_wh, nominal_voltage_v, cell_count) = infer_lfp_capacity(
        definition,
        measurements.soc.and_then(|item| item.value),
        Some(installed_capacity_ah),
        measurements.voltage.and_then(|item| item.value),
    )?;
    CapacityEstimate::new(
        if definition.source_id.is_empty() {
            fallback_source_id.to_owned()
        } else {
            definition.source_id.clone()
        },
        usable_capacity_wh,
        installed_capacity_ah,
        nominal_voltage_v,
        cell_count,
    )
}

fn gateway_source(
    measurements: GatewayBatteryMeasurements<'_>,
    fallback_source_id: &str,
    definition: Option<&EnergySourceDefinition>,
) -> Option<EnergySourceSnapshot> {
    if !measurements.any() {
        return None;
    }
    let capacity = resolved_gateway_capacity(measurements, definition);
    let source_measurements = [
        measurements.soc,
        measurements.net_power,
        measurements.capacity_wh,
        measurements.capacity_ah,
        measurements.voltage,
    ];
    let source_id = definition.map_or(fallback_source_id, |item| &item.source_id);
    let configured_service = definition.map_or("", |item| item.service_name.as_str());
    let measured_service = source_measurements
        .into_iter()
        .flatten()
        .flat_map(|measurement| measurement.source_ids.iter().cloned())
        .collect::<std::collections::BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>()
        .join(",");
    let primary_measurement = measurements
        .soc
        .or(measurements.net_power)
        .or_else(|| source_measurements.into_iter().flatten().next());
    Some(EnergySourceSnapshot {
        source_id: source_id.to_owned(),
        role: definition.map_or(EnergyRole::Battery, |item| item.role),
        service_name: if measured_service.is_empty() {
            if configured_service.is_empty() {
                "semantic-gateway".to_owned()
            } else {
                configured_service.to_owned()
            }
        } else {
            measured_service
        },
        soc: measurements.soc.and_then(|item| item.value),
        usable_capacity_wh: capacity.usable_capacity_wh,
        usable_capacity_source: capacity.source.to_owned(),
        installed_capacity_ah: capacity.installed_capacity_ah,
        capacity_voltage_v: capacity.voltage_v,
        capacity_nominal_voltage_v: capacity.nominal_voltage_v,
        capacity_cell_count: capacity.cell_count,
        battery_chemistry: definition
            .map_or_else(String::new, |item| item.battery_chemistry.clone()),
        net_battery_power_w: measurements.net_power.and_then(|item| item.value),
        charge_limit_power_w: None,
        discharge_limit_power_w: None,
        ac_power_w: None,
        pv_input_power_w: None,
        grid_interaction_w: None,
        ac_power_scope_key: String::new(),
        pv_input_power_scope_key: String::new(),
        grid_interaction_scope_key: String::new(),
        operating_mode: String::new(),
        online: true,
        confidence: primary_measurement.map_or(0.0, |item| item.confidence),
        captured_at: primary_measurement.map(|item| item.observed_at),
        physical_id: definition.map_or_else(String::new, |item| item.physical_id.clone()),
        physical_priority: definition.map_or(0, |item| item.physical_priority),
    })
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
struct ResolvedGatewayCapacity {
    usable_capacity_wh: Option<f64>,
    source: &'static str,
    installed_capacity_ah: Option<f64>,
    voltage_v: Option<f64>,
    nominal_voltage_v: Option<f64>,
    cell_count: Option<u32>,
}

fn resolved_gateway_capacity(
    measurements: GatewayBatteryMeasurements<'_>,
    definition: Option<&EnergySourceDefinition>,
) -> ResolvedGatewayCapacity {
    let installed_capacity_ah = measurements
        .capacity_ah
        .and_then(|item| item.value)
        .or_else(|| definition.and_then(|item| item.estimated_capacity_ah));
    let voltage_v = measurements.voltage.and_then(|item| item.value);
    let persisted_nominal = definition.and_then(|item| item.estimated_capacity_nominal_voltage_v);
    let persisted_cells = definition.and_then(|item| item.estimated_capacity_cell_count);
    if let Some(capacity) = measurements.capacity_wh.and_then(|item| item.value) {
        return ResolvedGatewayCapacity {
            usable_capacity_wh: Some(capacity),
            source: "gateway_capacity_wh",
            installed_capacity_ah,
            voltage_v,
            nominal_voltage_v: persisted_nominal,
            cell_count: persisted_cells,
        };
    }
    if let Some(capacity) = definition.and_then(|item| item.usable_capacity_wh) {
        return ResolvedGatewayCapacity {
            usable_capacity_wh: Some(capacity),
            source: "configured",
            installed_capacity_ah,
            voltage_v,
            nominal_voltage_v: persisted_nominal,
            cell_count: persisted_cells,
        };
    }
    if let Some(capacity) = definition.and_then(|item| item.estimated_capacity_wh) {
        return ResolvedGatewayCapacity {
            usable_capacity_wh: Some(capacity),
            source: "config_estimated",
            installed_capacity_ah,
            voltage_v,
            nominal_voltage_v: persisted_nominal,
            cell_count: persisted_cells,
        };
    }
    let inferred = definition.and_then(|item| {
        infer_lfp_capacity(
            item,
            measurements.soc.and_then(|measurement| measurement.value),
            measurements
                .capacity_ah
                .and_then(|measurement| measurement.value),
            voltage_v,
        )
    });
    inferred.map_or_else(
        || ResolvedGatewayCapacity {
            installed_capacity_ah,
            voltage_v,
            nominal_voltage_v: persisted_nominal,
            cell_count: persisted_cells,
            ..ResolvedGatewayCapacity::default()
        },
        |(capacity, nominal_voltage, cells)| ResolvedGatewayCapacity {
            usable_capacity_wh: Some(capacity),
            source: "gateway_lfp_inferred",
            installed_capacity_ah,
            voltage_v,
            nominal_voltage_v: Some(nominal_voltage),
            cell_count: Some(cells),
        },
    )
}

fn infer_lfp_capacity(
    definition: &EnergySourceDefinition,
    soc: Option<f64>,
    installed_capacity_ah: Option<f64>,
    voltage_v: Option<f64>,
) -> Option<(f64, f64, u32)> {
    if !definition.capacity_auto_estimate
        || !definition
            .battery_chemistry
            .trim()
            .eq_ignore_ascii_case("lfp")
        || soc? < definition.capacity_estimate_min_soc
    {
        return None;
    }
    let installed_capacity_ah = installed_capacity_ah.filter(|value| *value > 0.0)?;
    let voltage_v = voltage_v.filter(|value| (40.0..=60.0).contains(value))?;
    let cell_count = if voltage_v < 52.5 { 15 } else { 16 };
    let nominal_voltage = f64::from(cell_count) * 3.2;
    let capacity = installed_capacity_ah * nominal_voltage;
    (capacity.is_finite() && capacity > 0.0).then_some((capacity, nominal_voltage, cell_count))
}

fn select_soc(
    cluster: &EnergyClusterSnapshot,
    external: &[EnergySourceSnapshot],
    polls: &[ExternalSourcePoll],
    gateway: Option<&EnergySourceSnapshot>,
    gateway_measurement: Option<&Measurement>,
    use_combined: bool,
) -> (Option<f64>, Option<f64>, Option<f64>) {
    if use_combined {
        if let Some(soc) = cluster.combined_soc {
            let weighted = weighted_soc_sources(external, gateway);
            let epoch = weighted
                .iter()
                .filter_map(|item| item.captured_at)
                .reduce(f64::min);
            let source_ids: Vec<&str> = weighted
                .iter()
                .map(|item| item.source_id.as_str())
                .collect();
            let mut monotonic_values: Vec<f64> = polls
                .iter()
                .filter(|poll| source_ids.contains(&poll.snapshot.source_id.as_str()))
                .filter_map(|poll| poll.observed_monotonic)
                .collect();
            if gateway.is_some_and(|item| source_ids.contains(&item.source_id.as_str())) {
                if let Some(measurement) = gateway_measurement {
                    monotonic_values.push(measurement.observed_monotonic);
                }
            }
            return (
                Some(soc),
                epoch,
                monotonic_values.into_iter().reduce(f64::min),
            );
        }
    }
    if let Some(source) = external
        .iter()
        .find(|item| item.online && item.soc.is_some() && item.captured_at.is_some())
    {
        let monotonic = polls
            .iter()
            .find(|poll| poll.snapshot.source_id == source.source_id)
            .and_then(|poll| poll.observed_monotonic);
        return (source.soc, source.captured_at, monotonic);
    }
    gateway.map_or((None, None, None), |source| {
        (
            source.soc,
            source.captured_at,
            gateway_measurement.map(|item| item.observed_monotonic),
        )
    })
}

fn weighted_soc_sources<'a>(
    external: &'a [EnergySourceSnapshot],
    gateway: Option<&'a EnergySourceSnapshot>,
) -> Vec<&'a EnergySourceSnapshot> {
    let mut sources: Vec<&EnergySourceSnapshot> = external.iter().collect();
    if let Some(source) = gateway {
        sources.push(source);
    }
    let mut independent = Vec::new();
    let mut physical = BTreeMap::new();
    for source in sources {
        if source.soc.is_none()
            || source
                .usable_capacity_wh
                .is_none_or(|capacity| capacity <= 0.0)
        {
            continue;
        }
        if source.physical_id.is_empty() {
            independent.push(source);
        } else {
            let replace = physical.get(source.physical_id.as_str()).is_none_or(
                |existing: &&EnergySourceSnapshot| {
                    source.physical_priority > existing.physical_priority
                        || (source.physical_priority == existing.physical_priority
                            && source.confidence > existing.confidence)
                },
            );
            if replace {
                physical.insert(source.physical_id.as_str(), source);
            }
        }
    }
    independent.extend(physical.into_values());
    independent
}

fn external_pv_projection(
    polls: &[ExternalSourcePoll],
    selected_source_id: &str,
) -> Option<ProjectedEnergyValue> {
    let candidates: Vec<&ExternalSourcePoll> = polls
        .iter()
        .filter(|poll| {
            poll.contributing
                && (selected_source_id.is_empty() || poll.snapshot.source_id == selected_source_id)
                && poll
                    .snapshot
                    .pv_input_power_w
                    .is_some_and(|value| value >= 0.0)
        })
        .collect();
    if candidates.is_empty() {
        return None;
    }
    if !selected_source_id.is_empty() {
        return single_pv_projection(candidates[0]);
    }
    let snapshots: Vec<EnergySourceSnapshot> = candidates
        .iter()
        .map(|poll| poll.snapshot.clone())
        .collect();
    let cluster = aggregate_energy_sources(&snapshots);
    Some(ProjectedEnergyValue {
        value: cluster.combined_pv_input_power_w?,
        observed_at: candidates
            .iter()
            .filter_map(|poll| poll.observed_at)
            .reduce(f64::min)?,
        observed_monotonic: candidates
            .iter()
            .filter_map(|poll| poll.observed_monotonic)
            .reduce(f64::min)?,
        source_id: "external-aggregate".to_owned(),
        confidence: candidates
            .iter()
            .map(|poll| poll.snapshot.confidence)
            .reduce(f64::min)?,
        measurement_status: if candidates
            .iter()
            .any(|poll| poll.measurement_status == MeasurementStatus::Stale)
        {
            MeasurementStatus::Stale
        } else {
            MeasurementStatus::Fresh
        },
    })
}

fn single_pv_projection(poll: &ExternalSourcePoll) -> Option<ProjectedEnergyValue> {
    Some(ProjectedEnergyValue {
        value: poll.snapshot.pv_input_power_w?,
        observed_at: poll.observed_at?,
        observed_monotonic: poll.observed_monotonic?,
        source_id: poll.snapshot.source_id.clone(),
        confidence: poll.snapshot.confidence,
        measurement_status: poll.measurement_status,
    })
}

fn battery_payload(
    effective_soc: Option<f64>,
    cluster: &EnergyClusterSnapshot,
    polls: &[ExternalSourcePoll],
    gateway: Option<&EnergySourceSnapshot>,
    gateway_measurement: Option<&Measurement>,
    profiles: &BTreeMap<String, EnergyLearningProfile>,
    definitions: &[EnergySourceDefinition],
) -> Map<String, Value> {
    let forecast = derive_energy_forecast(cluster, profiles);
    let contributing: Vec<&EnergySourceSnapshot> = polls
        .iter()
        .filter(|poll| poll.contributing)
        .map(|poll| &poll.snapshot)
        .collect();
    let balance = discharge_balance(&contributing, profiles);
    let control = discharge_control(&contributing, definitions);
    let sources = source_payloads(polls, gateway, gateway_measurement, &balance, &control);
    let mut payload = battery_cluster_payload(effective_soc, cluster, &forecast);
    append_balance_payload(&mut payload, &balance, &control);
    append_source_counts(&mut payload, cluster);
    payload.insert("battery_sources".to_owned(), Value::Array(sources));
    payload.insert(
        "battery_learning_profiles".to_owned(),
        Value::Object(
            profiles
                .iter()
                .map(|(key, profile)| (key.clone(), Value::Object(profile.payload())))
                .collect(),
        ),
    );
    payload
}

fn source_payloads(
    polls: &[ExternalSourcePoll],
    gateway: Option<&EnergySourceSnapshot>,
    gateway_measurement: Option<&Measurement>,
    balance: &BalanceMetrics,
    control: &ControlMetrics,
) -> Vec<Value> {
    let mut sources: Vec<Value> = polls
        .iter()
        .map(|poll| {
            let mut payload = poll.payload();
            if let Some(extra) = balance.sources.get(&poll.snapshot.source_id) {
                payload.extend(extra.clone());
            }
            if let Some(extra) = control.sources.get(&poll.snapshot.source_id) {
                payload.extend(extra.clone());
            }
            Value::Object(payload)
        })
        .collect();
    if let Some(source) = gateway {
        sources.push(Value::Object(gateway_payload(source, gateway_measurement)));
    }
    sources
}

fn gateway_payload(
    source: &EnergySourceSnapshot,
    gateway_measurement: Option<&Measurement>,
) -> Map<String, Value> {
    let mut payload = source.payload();
    payload.extend([
        ("contributing".to_owned(), json!(true)),
        ("poll_status".to_owned(), json!("semantic_gateway")),
        ("measurement_status".to_owned(), json!("fresh")),
        ("attempted_at".to_owned(), Value::Null),
        ("observed_at".to_owned(), number(source.captured_at)),
        (
            "observed_monotonic".to_owned(),
            number(gateway_measurement.map(|item| item.observed_monotonic)),
        ),
        ("next_poll_at".to_owned(), json!(0.0)),
        ("age_seconds".to_owned(), Value::Null),
        ("consecutive_failures".to_owned(), json!(0)),
        ("last_error".to_owned(), json!("")),
    ]);
    payload
}

fn battery_cluster_payload(
    effective_soc: Option<f64>,
    cluster: &EnergyClusterSnapshot,
    forecast: &crate::forecast::EnergyForecast,
) -> Map<String, Value> {
    Map::from_iter([
        ("battery_soc".to_owned(), number(effective_soc)),
        (
            "battery_combined_soc".to_owned(),
            number(cluster.combined_soc),
        ),
        (
            "battery_combined_usable_capacity_wh".to_owned(),
            number(cluster.combined_usable_capacity_wh),
        ),
        (
            "battery_combined_charge_power_w".to_owned(),
            number(cluster.combined_charge_power_w),
        ),
        (
            "battery_combined_discharge_power_w".to_owned(),
            number(cluster.combined_discharge_power_w),
        ),
        (
            "battery_combined_net_power_w".to_owned(),
            number(cluster.combined_net_battery_power_w),
        ),
        (
            "battery_combined_ac_power_w".to_owned(),
            number(cluster.combined_ac_power_w),
        ),
        (
            "battery_combined_pv_input_power_w".to_owned(),
            number(cluster.combined_pv_input_power_w),
        ),
        (
            "battery_combined_grid_interaction_w".to_owned(),
            number(cluster.combined_grid_interaction_w),
        ),
        (
            "battery_headroom_charge_w".to_owned(),
            number(forecast.charge_headroom),
        ),
        (
            "battery_headroom_discharge_w".to_owned(),
            number(forecast.discharge_headroom),
        ),
        (
            "expected_near_term_export_w".to_owned(),
            number(forecast.expected_export),
        ),
        (
            "expected_near_term_import_w".to_owned(),
            number(forecast.expected_import),
        ),
    ])
}

fn append_balance_payload(
    payload: &mut Map<String, Value>,
    balance: &BalanceMetrics,
    control: &ControlMetrics,
) {
    payload.extend([
        (
            "battery_discharge_balance_mode".to_owned(),
            json!(balance.mode),
        ),
        (
            "battery_discharge_balance_target_distribution_mode".to_owned(),
            json!(balance.mode),
        ),
        (
            "battery_discharge_balance_error_w".to_owned(),
            number(balance.error_w),
        ),
        (
            "battery_discharge_balance_max_abs_error_w".to_owned(),
            number(balance.max_abs_error_w),
        ),
        (
            "battery_discharge_balance_total_discharge_w".to_owned(),
            number(balance.total_discharge_w),
        ),
        (
            "battery_discharge_balance_eligible_source_count".to_owned(),
            json!(balance.eligible_source_count),
        ),
        (
            "battery_discharge_balance_active_source_count".to_owned(),
            json!(balance.active_source_count),
        ),
        (
            "battery_discharge_balance_control_candidate_count".to_owned(),
            json!(control.candidate_count),
        ),
        (
            "battery_discharge_balance_control_ready_count".to_owned(),
            json!(control.ready_count),
        ),
        (
            "battery_discharge_balance_supported_control_source_count".to_owned(),
            json!(control.supported_count),
        ),
        (
            "battery_discharge_balance_experimental_control_source_count".to_owned(),
            json!(control.experimental_count),
        ),
    ]);
}

fn append_source_counts(payload: &mut Map<String, Value>, cluster: &EnergyClusterSnapshot) {
    payload.extend([
        (
            "battery_average_confidence".to_owned(),
            number(cluster.average_confidence),
        ),
        (
            "battery_source_count".to_owned(),
            json!(cluster.source_count),
        ),
        (
            "battery_online_source_count".to_owned(),
            json!(cluster.online_source_count),
        ),
        (
            "battery_valid_soc_source_count".to_owned(),
            json!(cluster.valid_soc_source_count),
        ),
        (
            "battery_battery_source_count".to_owned(),
            json!(cluster.battery_source_count),
        ),
        (
            "battery_hybrid_inverter_source_count".to_owned(),
            json!(cluster.hybrid_inverter_source_count),
        ),
        (
            "battery_inverter_source_count".to_owned(),
            json!(cluster.inverter_source_count),
        ),
    ]);
}

#[derive(Default)]
struct BalanceMetrics {
    mode: &'static str,
    eligible_source_count: usize,
    active_source_count: usize,
    total_discharge_w: Option<f64>,
    error_w: Option<f64>,
    max_abs_error_w: Option<f64>,
    sources: BTreeMap<String, Map<String, Value>>,
}

fn discharge_balance(
    sources: &[&EnergySourceSnapshot],
    profiles: &BTreeMap<String, EnergyLearningProfile>,
) -> BalanceMetrics {
    let mut eligible = Vec::new();
    for source in sources {
        if !source.online || !source.role.battery_like() {
            continue;
        }
        let floor = profiles
            .get(&source.source_id)
            .and_then(|profile| profile.observed_min_discharge_soc);
        let capacity = source.usable_capacity_wh.filter(|value| *value >= 0.0);
        let available = capacity.zip(source.soc).map(|(capacity, soc)| {
            capacity * (soc - floor.unwrap_or(0.0).max(0.0)).max(0.0) / 100.0
        });
        let (weight, basis) = match (available, capacity) {
            (Some(value), _) if value > 0.0 => (value, "available_energy_above_reserve"),
            (_, Some(value)) if value > 0.0 => (value, "usable_capacity_fallback"),
            _ => (1.0, "uniform_fallback"),
        };
        eligible.push((source, weight, basis, available, floor));
    }
    if eligible.is_empty() {
        return BalanceMetrics {
            mode: "capacity_reserve_weighted",
            ..BalanceMetrics::default()
        };
    }
    let total_discharge = eligible
        .iter()
        .map(|(source, _, _, _, _)| source.discharge_power_w().unwrap_or(0.0))
        .sum::<f64>();
    let total_weight = eligible
        .iter()
        .map(|(_, weight, _, _, _)| weight)
        .sum::<f64>();
    let mut total_abs_error = 0.0;
    let mut maximum_error: f64 = 0.0;
    let mut metrics = BTreeMap::new();
    for (source, weight, basis, available, floor) in &eligible {
        let share = *weight / total_weight;
        let target = total_discharge * share;
        let actual = source.discharge_power_w().unwrap_or(0.0);
        let error = actual - target;
        total_abs_error += error.abs();
        maximum_error = maximum_error.max(error.abs());
        metrics.insert(
            source.source_id.clone(),
            Map::from_iter([
                ("discharge_balance_eligible".to_owned(), json!(true)),
                ("discharge_balance_weight".to_owned(), json!(weight)),
                ("discharge_balance_weight_basis".to_owned(), json!(basis)),
                (
                    "discharge_balance_available_energy_wh".to_owned(),
                    number(*available),
                ),
                (
                    "discharge_balance_reserve_floor_soc".to_owned(),
                    number(*floor),
                ),
                (
                    "discharge_balance_target_distribution_mode".to_owned(),
                    json!("capacity_reserve_weighted"),
                ),
                ("discharge_balance_target_share".to_owned(), json!(share)),
                ("discharge_balance_target_power_w".to_owned(), json!(target)),
                ("discharge_balance_actual_power_w".to_owned(), json!(actual)),
                ("discharge_balance_error_w".to_owned(), json!(error)),
                (
                    "discharge_balance_relative_error".to_owned(),
                    if total_discharge > 0.0 {
                        json!(error / total_discharge)
                    } else {
                        Value::Null
                    },
                ),
            ]),
        );
    }
    BalanceMetrics {
        mode: "capacity_reserve_weighted",
        eligible_source_count: eligible.len(),
        active_source_count: eligible
            .iter()
            .filter(|(source, _, _, _, _)| source.discharge_power_w().unwrap_or(0.0) > 0.0)
            .count(),
        total_discharge_w: Some(total_discharge),
        error_w: Some(total_abs_error / 2.0),
        max_abs_error_w: Some(maximum_error),
        sources: metrics,
    }
}

#[derive(Default)]
struct ControlMetrics {
    candidate_count: usize,
    ready_count: usize,
    supported_count: usize,
    experimental_count: usize,
    sources: BTreeMap<String, Map<String, Value>>,
}

fn discharge_control(
    sources: &[&EnergySourceSnapshot],
    definitions: &[EnergySourceDefinition],
) -> ControlMetrics {
    let mut result = ControlMetrics::default();
    for source in sources {
        let definition = definitions
            .iter()
            .find(|item| item.source_id == source.source_id);
        let profile = definition.map_or("", |item| item.profile_name.as_str());
        let support = if profile.starts_with("huawei_") {
            "experimental"
        } else {
            "unsupported"
        };
        let targeted = source.role.battery_like();
        let candidate = targeted && matches!(support, "supported" | "experimental");
        let ready = candidate && source.online;
        result.candidate_count += usize::from(candidate);
        result.ready_count += usize::from(ready);
        result.supported_count += usize::from(targeted && support == "supported");
        result.experimental_count += usize::from(targeted && support == "experimental");
        result.sources.insert(
            source.source_id.clone(),
            Map::from_iter([
                (
                    "discharge_balance_control_profile_name".to_owned(),
                    json!(profile),
                ),
                (
                    "discharge_balance_control_connector_type".to_owned(),
                    json!(
                        definition
                            .and_then(|item| item.connector_type)
                            .map_or(source.role.as_str(), |item| item.as_str())
                    ),
                ),
                (
                    "discharge_balance_control_support".to_owned(),
                    json!(support),
                ),
                (
                    "discharge_balance_control_candidate".to_owned(),
                    json!(candidate),
                ),
                ("discharge_balance_control_ready".to_owned(), json!(ready)),
                (
                    "discharge_balance_control_reason".to_owned(),
                    json!(if !targeted {
                        "role_not_targeted"
                    } else if support == "supported" {
                        "profile_write_supported"
                    } else if support == "experimental" {
                        "profile_write_experimental"
                    } else {
                        "profile_write_unsupported"
                    }),
                ),
                (
                    "discharge_balance_control_role_targeted".to_owned(),
                    json!(targeted),
                ),
            ]),
        );
    }
    result
}

#[cfg(test)]
mod tests {
    use std::collections::VecDeque;

    use super::{
        AttemptStatus, ExternalSourceScheduler, GatewayBatteryMeasurements, MeasurementStatus,
        SourceState, backoff_seconds, confirms_measurement, external_pv_projection, monotonic_now,
        poll_result, resolved_gateway_capacity,
    };
    use crate::connectors::EnergyConnector;
    use crate::energy::{
        ConnectorType, EnergyRole, EnergySourceDefinition, EnergySourceSnapshot,
        ExternalPollingPolicy,
    };
    use crate::error::{HelperError, Result};
    use crate::wire::{Measurement, MeasurementStatus as WireMeasurementStatus};

    enum Step {
        Complete(Box<EnergySourceSnapshot>),
        Pending,
        Failed,
    }

    struct SequenceConnector {
        steps: VecDeque<Step>,
    }

    impl SequenceConnector {
        fn new(steps: impl IntoIterator<Item = Step>) -> Self {
            Self {
                steps: steps.into_iter().collect(),
            }
        }
    }

    impl EnergyConnector for SequenceConnector {
        fn read_step(
            &mut self,
            _source: &EnergySourceDefinition,
            _observed_at: f64,
            _timeout_seconds: f64,
        ) -> Result<Option<EnergySourceSnapshot>> {
            match self.steps.pop_front().unwrap_or(Step::Pending) {
                Step::Complete(snapshot) => Ok(Some(*snapshot)),
                Step::Pending => Ok(None),
                Step::Failed => Err(HelperError::Input("network down".to_owned())),
            }
        }
    }

    fn definition() -> EnergySourceDefinition {
        EnergySourceDefinition {
            source_id: "source".to_owned(),
            profile_name: String::new(),
            role: EnergyRole::Battery,
            connector_type: Some(ConnectorType::CommandJson),
            config_path: "/tmp/source.ini".to_owned(),
            service_name: String::new(),
            usable_capacity_wh: Some(1_000.0),
            battery_chemistry: "lfp".to_owned(),
            capacity_auto_estimate: false,
            capacity_estimate_min_soc: 95.0,
            capacity_startup_recheck_seconds: 300.0,
            estimated_capacity_wh: None,
            estimated_capacity_ah: None,
            estimated_capacity_nominal_voltage_v: None,
            estimated_capacity_cell_count: None,
            physical_id: String::new(),
            physical_priority: 0,
        }
    }

    fn policy() -> ExternalPollingPolicy {
        ExternalPollingPolicy {
            poll_interval_seconds: 1.0,
            backoff_base_seconds: 2.0,
            backoff_max_seconds: 10.0,
            last_good_max_age_seconds: 5.0,
            cycle_budget_seconds: 2.0,
        }
    }

    fn online_snapshot(source_id: &str, observed_at: f64) -> EnergySourceSnapshot {
        EnergySourceSnapshot {
            source_id: source_id.to_owned(),
            role: EnergyRole::HybridInverter,
            service_name: "connector".to_owned(),
            soc: Some(50.0),
            usable_capacity_wh: Some(1_000.0),
            pv_input_power_w: Some(500.0),
            online: true,
            confidence: 0.75,
            captured_at: Some(observed_at),
            ..EnergySourceSnapshot::offline(&definition())
        }
    }

    fn gateway_measurement(value: f64, source_id: &str) -> Measurement {
        Measurement {
            value: Some(value),
            observed_at: 100.0,
            observed_monotonic: 100.0,
            status: WireMeasurementStatus::Fresh,
            confidence: 1.0,
            source_ids: vec![source_id.to_owned()],
            reason_code: String::new(),
        }
    }

    #[test]
    fn last_good_backoff_is_bounded() {
        let policy = ExternalPollingPolicy {
            poll_interval_seconds: 1.0,
            backoff_base_seconds: 2.0,
            backoff_max_seconds: 10.0,
            last_good_max_age_seconds: 30.0,
            cycle_budget_seconds: 2.0,
        };
        assert!((backoff_seconds(policy, 1) - 2.0).abs() < f64::EPSILON);
        assert!((backoff_seconds(policy, 3) - 8.0).abs() < f64::EPSILON);
        assert!((backoff_seconds(policy, 99) - 10.0).abs() < f64::EPSILON);
        assert_eq!(AttemptStatus::InProgress.as_str(), "in_progress");
    }

    #[test]
    fn online_capacity_only_source_confirms_measurement() {
        let source = EnergySourceSnapshot {
            online: true,
            captured_at: Some(10.0),
            ..EnergySourceSnapshot::offline(&definition())
        };
        assert!(confirms_measurement(&source));
    }

    #[test]
    fn gateway_capacity_uses_the_contractual_priority_order() {
        let soc = gateway_measurement(98.0, "soc");
        let live_wh = gateway_measurement(6_000.0, "capacity-wh");
        let capacity_ah = gateway_measurement(100.0, "capacity-ah");
        let voltage = gateway_measurement(52.8, "voltage");
        let complete = GatewayBatteryMeasurements {
            soc: Some(&soc),
            capacity_wh: Some(&live_wh),
            capacity_ah: Some(&capacity_ah),
            voltage: Some(&voltage),
            ..GatewayBatteryMeasurements::default()
        };
        let mut configured = definition();
        configured.capacity_auto_estimate = true;
        configured.usable_capacity_wh = Some(5_000.0);
        configured.estimated_capacity_wh = Some(4_800.0);
        configured.estimated_capacity_ah = Some(90.0);
        configured.estimated_capacity_nominal_voltage_v = Some(48.0);
        configured.estimated_capacity_cell_count = Some(15);

        let live = resolved_gateway_capacity(complete, Some(&configured));
        assert_eq!(live.usable_capacity_wh, Some(6_000.0));
        assert_eq!(live.source, "gateway_capacity_wh");

        let without_live_wh = GatewayBatteryMeasurements {
            capacity_wh: None,
            ..complete
        };
        let explicit = resolved_gateway_capacity(without_live_wh, Some(&configured));
        assert_eq!(explicit.usable_capacity_wh, Some(5_000.0));
        assert_eq!(explicit.source, "configured");

        configured.usable_capacity_wh = None;
        let persisted = resolved_gateway_capacity(without_live_wh, Some(&configured));
        assert_eq!(persisted.usable_capacity_wh, Some(4_800.0));
        assert_eq!(persisted.source, "config_estimated");

        configured.estimated_capacity_wh = None;
        let inferred = resolved_gateway_capacity(without_live_wh, Some(&configured));
        assert_eq!(inferred.usable_capacity_wh, Some(5_120.0));
        assert_eq!(inferred.source, "gateway_lfp_inferred");
        assert_eq!(inferred.installed_capacity_ah, Some(100.0));
        assert_eq!(inferred.voltage_v, Some(52.8));
        assert_eq!(inferred.nominal_voltage_v, Some(51.2));
        assert_eq!(inferred.cell_count, Some(16));
    }

    #[test]
    fn gateway_ah_voltage_estimate_requires_lfp_soc_and_voltage_contracts() {
        let low_soc = gateway_measurement(94.9, "soc");
        let valid_soc = gateway_measurement(95.0, "soc");
        let capacity_ah = gateway_measurement(100.0, "capacity-ah");
        let invalid_voltage = gateway_measurement(60.1, "voltage");
        let valid_voltage = gateway_measurement(52.4, "voltage");
        let mut configured = definition();
        configured.capacity_auto_estimate = true;
        configured.usable_capacity_wh = None;

        let result = resolved_gateway_capacity(
            GatewayBatteryMeasurements {
                soc: Some(&low_soc),
                capacity_ah: Some(&capacity_ah),
                voltage: Some(&valid_voltage),
                ..GatewayBatteryMeasurements::default()
            },
            Some(&configured),
        );
        assert_eq!(result.usable_capacity_wh, None);

        let result = resolved_gateway_capacity(
            GatewayBatteryMeasurements {
                soc: Some(&valid_soc),
                capacity_ah: Some(&capacity_ah),
                voltage: Some(&invalid_voltage),
                ..GatewayBatteryMeasurements::default()
            },
            Some(&configured),
        );
        assert_eq!(result.usable_capacity_wh, None);

        configured.battery_chemistry = "nmc".to_owned();
        let result = resolved_gateway_capacity(
            GatewayBatteryMeasurements {
                soc: Some(&valid_soc),
                capacity_ah: Some(&capacity_ah),
                voltage: Some(&valid_voltage),
                ..GatewayBatteryMeasurements::default()
            },
            Some(&configured),
        );
        assert_eq!(result.usable_capacity_wh, None);
    }

    #[test]
    fn round_robin_due_selection_skips_blocked_sources_in_forward_order() {
        let definitions = vec![definition(), definition(), definition()];
        let mut scheduler = ExternalSourceScheduler::new(definitions, policy(), 1.0);
        scheduler.cursor = 1;
        scheduler.states[0].next_poll_monotonic = 0.0;
        scheduler.states[1].next_poll_monotonic = 2.0;
        scheduler.states[2].next_poll_monotonic = 0.0;

        assert_eq!(scheduler.next_due(1.0), Some(2));
        assert_eq!(scheduler.cursor, 0);
        assert_eq!(scheduler.next_due(1.0), Some(0));
        assert_eq!(scheduler.cursor, 1);
    }

    #[test]
    fn completed_source_recovers_and_failed_retry_preserves_last_good() -> Result<()> {
        let mut configured = definition();
        configured.source_id = "configured".to_owned();
        configured.role = EnergyRole::Battery;
        configured.physical_id = "bank-a".to_owned();
        configured.physical_priority = 7;
        let connector_snapshot = online_snapshot("connector-id", 100.0);
        let mut scheduler = ExternalSourceScheduler::new(vec![configured], policy(), 1.0);
        scheduler.states[0].connector = Some(Box::new(SequenceConnector::new([
            Step::Complete(Box::new(connector_snapshot)),
            Step::Failed,
        ])));

        let started = monotonic_now();
        assert_eq!(scheduler.attempt(0, 100.0, started), AttemptStatus::Success);
        let Some(recovered) = scheduler.states[0].last_good.as_ref() else {
            return Err(HelperError::Runtime(
                "successful connector snapshot is missing".to_owned(),
            ));
        };
        assert_eq!(recovered.source_id, "configured");
        assert_eq!(recovered.role, EnergyRole::Battery);
        assert_eq!(recovered.physical_id, "bank-a");
        assert_eq!(recovered.physical_priority, 7);

        assert_eq!(
            scheduler.attempt(0, 101.0, monotonic_now()),
            AttemptStatus::Failed
        );
        assert_eq!(scheduler.states[0].consecutive_failures, 1);
        assert_eq!(scheduler.states[0].last_error, "input error: network down");
        assert_eq!(
            scheduler.states[0]
                .last_good
                .as_ref()
                .and_then(|snapshot| snapshot.soc),
            Some(50.0)
        );
        Ok(())
    }

    #[test]
    fn last_good_boundary_is_stale_after_failure_and_then_expires() {
        let mut state = SourceState::new();
        state.last_good = Some(online_snapshot("source", 1_000.0));
        state.last_good_monotonic = Some(10.0);
        state.next_poll_monotonic = 20.0;
        state.consecutive_failures = 1;
        state.last_error = "network down".to_owned();

        let boundary = poll_result(&definition(), &state, policy(), 100.0, 15.0, 15.0, None);
        assert!(boundary.contributing);
        assert_eq!(boundary.measurement_status, MeasurementStatus::Stale);
        assert_eq!(boundary.poll_status, "backoff");
        assert_eq!(boundary.observed_at, Some(1_000.0));
        assert_eq!(boundary.age_seconds, Some(5.0));

        let expired = poll_result(
            &definition(),
            &state,
            policy(),
            100.001,
            15.001,
            15.001,
            None,
        );
        assert!(!expired.contributing);
        assert_eq!(expired.measurement_status, MeasurementStatus::Expired);
    }

    #[test]
    fn pending_connector_is_not_counted_as_a_failure() {
        let mut scheduler = ExternalSourceScheduler::new(vec![definition()], policy(), 1.0);
        scheduler.states[0].connector = Some(Box::new(SequenceConnector::new([Step::Pending])));

        assert_eq!(
            scheduler.attempt(0, 100.0, monotonic_now()),
            AttemptStatus::InProgress
        );
        assert!(scheduler.states[0].in_progress);
        assert_eq!(scheduler.states[0].consecutive_failures, 0);
        assert!(scheduler.states[0].last_error.is_empty());
    }

    #[test]
    fn aggregate_pv_uses_oldest_observation_lowest_confidence_and_stale_status() -> Result<()> {
        let first = online_snapshot("first", 100.0);
        let mut second = online_snapshot("second", 101.0);
        second.pv_input_power_w = Some(700.0);
        second.confidence = 0.6;
        let polls = vec![
            crate::energy::ExternalSourcePoll {
                snapshot: first,
                contributing: true,
                poll_status: "success".to_owned(),
                measurement_status: MeasurementStatus::Fresh,
                attempted_at: Some(100.0),
                observed_at: Some(100.0),
                observed_monotonic: Some(10.0),
                next_poll_at: 102.0,
                age_seconds: Some(0.0),
                consecutive_failures: 0,
                last_error: String::new(),
            },
            crate::energy::ExternalSourcePoll {
                snapshot: second,
                contributing: true,
                poll_status: "backoff".to_owned(),
                measurement_status: MeasurementStatus::Stale,
                attempted_at: Some(102.0),
                observed_at: Some(101.0),
                observed_monotonic: Some(11.0),
                next_poll_at: 104.0,
                age_seconds: Some(1.0),
                consecutive_failures: 1,
                last_error: "network down".to_owned(),
            },
        ];

        let Some(projection) = external_pv_projection(&polls, "") else {
            return Err(HelperError::Runtime(
                "aggregate PV projection is missing".to_owned(),
            ));
        };
        assert!((projection.value - 1_200.0).abs() < f64::EPSILON);
        assert!((projection.observed_at - 100.0).abs() < f64::EPSILON);
        assert!((projection.observed_monotonic - 10.0).abs() < f64::EPSILON);
        assert!((projection.confidence - 0.6).abs() < f64::EPSILON);
        assert_eq!(projection.measurement_status, MeasurementStatus::Stale);
        assert_eq!(projection.source_id, "external-aggregate");

        let Some(selected) = external_pv_projection(&polls, "second") else {
            return Err(HelperError::Runtime(
                "selected PV projection is missing".to_owned(),
            ));
        };
        assert!((selected.value - 700.0).abs() < f64::EPSILON);
        assert_eq!(selected.source_id, "second");
        assert_eq!(selected.measurement_status, MeasurementStatus::Stale);
        Ok(())
    }
}
