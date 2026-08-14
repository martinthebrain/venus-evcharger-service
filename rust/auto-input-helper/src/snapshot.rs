//! Snapshot projection and atomic RAM publication.

use std::path::PathBuf;

use serde_json::{Map, Value, json};

use crate::config::{GridFusionConfig, HelperConfig};
use crate::energy::{
    MeasurementStatus as ExternalMeasurementStatus, ProjectedEnergyValue, PvProjectionPolicy,
};
use crate::error::Result;
use crate::external::ExternalEnergyCycle;
use crate::grid_fusion::{GridFusionResult, GridMeasurement, GridMeasurementFusion};
use crate::runtime::RuntimeIdentity;
use crate::storage::write_atomic;
use crate::wire::{EnergyInputs, Measurement};

const SNAPSHOT_SCHEMA_VERSION: u64 = 2;

/// The three independently scheduled semantic source groups.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct DueSources {
    pub pv: bool,
    pub grid: bool,
    pub battery: bool,
}

impl DueSources {
    /// Return a full refresh request.
    #[must_use]
    pub const fn all() -> Self {
        Self {
            pv: true,
            grid: true,
            battery: true,
        }
    }

    /// Return whether at least one source is due.
    #[must_use]
    pub const fn any(self) -> bool {
        self.pv || self.grid || self.battery
    }
}

/// Mutable coherent helper snapshot.
pub struct SnapshotState {
    payload: Map<String, Value>,
    identity: RuntimeIdentity,
    grid_backup_source_id: String,
    grid_config: GridFusionConfig,
    grid_fusion: GridMeasurementFusion,
    pv_policy: PvProjectionPolicy,
}

impl SnapshotState {
    /// Create the canonical starting snapshot.
    #[must_use]
    pub fn new(identity: RuntimeIdentity, config: &HelperConfig) -> Self {
        let mut payload = empty_snapshot();
        payload.insert(
            "grid_fusion_backup_source_id".to_owned(),
            Value::String(config.grid_backup_source_id.clone()),
        );
        let mut state = Self {
            payload,
            identity,
            grid_backup_source_id: config.grid_backup_source_id.clone(),
            grid_config: config.grid_fusion.clone(),
            grid_fusion: GridMeasurementFusion::new(config.grid_fusion.clone()),
            pv_policy: config.pv_projection.clone(),
        };
        state.stamp_identity();
        state
    }

    /// Stamp and publish one lifecycle state.
    pub fn lifecycle(&mut self, state: &str, epoch: f64, monotonic: f64) {
        insert_number(&mut self.payload, "captured_at", Some(epoch));
        insert_number(&mut self.payload, "captured_monotonic", Some(monotonic));
        insert_number(&mut self.payload, "heartbeat_at", Some(epoch));
        insert_number(&mut self.payload, "heartbeat_monotonic", Some(monotonic));
        self.payload
            .insert("helper_state".to_owned(), Value::String(state.to_owned()));
        self.payload
            .insert("helper_status".to_owned(), Value::String(state.to_owned()));
        self.stamp_identity();
    }

    /// Apply due source values from one coherent gateway snapshot.
    pub fn apply(
        &mut self,
        inputs: Option<&EnergyInputs>,
        due: DueSources,
        epoch: f64,
        monotonic: f64,
        maximum_age: f64,
        external: Option<&ExternalEnergyCycle>,
    ) {
        if due.pv {
            self.apply_pv(inputs, external, monotonic, maximum_age);
        }
        if due.grid {
            self.apply_scalar(
                "grid",
                "grid_gateway",
                "grid_gateway_power",
                inputs.map(|value| &value.grid_power_w),
                monotonic,
                maximum_age,
            );
        }
        if due.battery {
            if let Some(cycle) = external {
                self.apply_external_battery(cycle);
            } else {
                self.apply_battery(inputs, monotonic, maximum_age);
            }
        }
        self.resolve_grid(external, monotonic);
        insert_number(&mut self.payload, "captured_at", Some(epoch));
        insert_number(&mut self.payload, "captured_monotonic", Some(monotonic));
        insert_number(&mut self.payload, "heartbeat_at", Some(epoch));
        insert_number(&mut self.payload, "heartbeat_monotonic", Some(monotonic));
        self.stamp_identity();
    }

    fn apply_pv(
        &mut self,
        inputs: Option<&EnergyInputs>,
        external: Option<&ExternalEnergyCycle>,
        monotonic: f64,
        maximum_age: f64,
    ) {
        let gateway = inputs
            .map(|value| &value.pv_power_w)
            .filter(|measurement| measurement.usable(monotonic, maximum_age))
            .and_then(|measurement| {
                Some(ProjectedEnergyValue {
                    value: measurement.value?,
                    observed_at: measurement.observed_at,
                    observed_monotonic: measurement.observed_monotonic,
                    source_id: self.grid_backup_source_id.clone(),
                    confidence: measurement.confidence,
                    measurement_status: match measurement.status {
                        crate::wire::MeasurementStatus::Fresh => ExternalMeasurementStatus::Fresh,
                        crate::wire::MeasurementStatus::Stale => ExternalMeasurementStatus::Stale,
                        _ => return None,
                    },
                })
            });
        let selected = select_pv_projection(
            gateway.as_ref(),
            external.and_then(|cycle| cycle.pv.as_ref()),
            &self.pv_policy,
        );
        insert_number(
            &mut self.payload,
            "pv_power",
            selected.map(|projection| projection.value),
        );
        insert_number(
            &mut self.payload,
            "pv_captured_at",
            selected.map(|projection| projection.observed_at),
        );
        insert_number(
            &mut self.payload,
            "pv_observed_monotonic",
            selected.map(|projection| projection.observed_monotonic),
        );
        self.payload.insert(
            "pv_status".to_owned(),
            Value::String(if selected.is_some() { "ok" } else { "missing" }.to_owned()),
        );
        self.mark_running();
    }

    fn apply_external_battery(&mut self, cycle: &ExternalEnergyCycle) {
        apply_empty_battery(&mut self.payload);
        self.payload.extend(cycle.battery.clone());
        let soc = optional_number(self.payload.get("battery_soc"));
        insert_number(
            &mut self.payload,
            "battery_captured_at",
            soc.and(cycle.battery_observed_at),
        );
        insert_number(
            &mut self.payload,
            "battery_observed_monotonic",
            soc.and(cycle.battery_observed_monotonic),
        );
        self.payload.insert(
            "battery_status".to_owned(),
            Value::String(if soc.is_some() { "ok" } else { "missing" }.to_owned()),
        );
        self.mark_running();
    }

    /// Refresh only liveness clocks without changing source observations.
    pub fn heartbeat(&mut self, epoch: f64, monotonic: f64) {
        insert_number(&mut self.payload, "heartbeat_at", Some(epoch));
        insert_number(&mut self.payload, "heartbeat_monotonic", Some(monotonic));
        self.payload
            .entry("helper_state".to_owned())
            .or_insert_with(|| Value::String("running".to_owned()));
        let state = self
            .payload
            .get("helper_state")
            .cloned()
            .unwrap_or_else(|| Value::String("running".to_owned()));
        self.payload
            .entry("helper_status".to_owned())
            .or_insert(state);
        self.stamp_identity();
    }

    /// Borrow the current JSON payload.
    #[must_use]
    pub const fn payload(&self) -> &Map<String, Value> {
        &self.payload
    }

    fn apply_scalar(
        &mut self,
        status_source: &str,
        timestamp_prefix: &str,
        value_key: &str,
        measurement: Option<&Measurement>,
        monotonic: f64,
        maximum_age: f64,
    ) {
        let usable = measurement.filter(|value| value.usable(monotonic, maximum_age));
        let value = usable.and_then(|item| item.value);
        insert_number(&mut self.payload, value_key, value);
        insert_number(
            &mut self.payload,
            &format!("{timestamp_prefix}_captured_at"),
            usable.map(|item| item.observed_at),
        );
        insert_number(
            &mut self.payload,
            &format!("{timestamp_prefix}_observed_monotonic"),
            usable.map(|item| item.observed_monotonic),
        );
        self.payload.insert(
            format!("{status_source}_status"),
            Value::String(if usable.is_some() { "ok" } else { "missing" }.to_owned()),
        );
        self.mark_running();
    }

    fn apply_battery(&mut self, inputs: Option<&EnergyInputs>, monotonic: f64, maximum_age: f64) {
        let soc = inputs
            .map(|value| &value.battery_soc)
            .filter(|value| value.usable(monotonic, maximum_age))
            .filter(|value| value.value.is_some_and(|soc| (0.0..=100.0).contains(&soc)));
        let net = inputs
            .map(|value| &value.battery_net_power_w)
            .filter(|value| value.usable(monotonic, maximum_age));
        apply_empty_battery(&mut self.payload);
        let Some(soc) = soc else {
            insert_number(&mut self.payload, "battery_captured_at", None);
            insert_number(&mut self.payload, "battery_observed_monotonic", None);
            self.payload.insert(
                "battery_status".to_owned(),
                Value::String("missing".to_owned()),
            );
            self.mark_running();
            return;
        };
        let soc_value = soc.value;
        let net_value = net.and_then(|value| value.value);
        let observed_at = net.map_or(soc.observed_at, |value| {
            soc.observed_at.min(value.observed_at)
        });
        let observed_monotonic = net.map_or(soc.observed_monotonic, |value| {
            soc.observed_monotonic.min(value.observed_monotonic)
        });
        let source_count = u64::try_from(soc.source_ids.len())
            .unwrap_or(u64::MAX)
            .max(1);
        insert_number(&mut self.payload, "battery_soc", soc_value);
        insert_number(&mut self.payload, "battery_combined_soc", soc_value);
        insert_number(
            &mut self.payload,
            "battery_average_confidence",
            Some(soc.confidence),
        );
        insert_number(&mut self.payload, "battery_combined_net_power_w", net_value);
        insert_number(
            &mut self.payload,
            "battery_combined_charge_power_w",
            net_value.map(|value| (-value).max(0.0)),
        );
        insert_number(
            &mut self.payload,
            "battery_combined_discharge_power_w",
            net_value.map(|value| value.max(0.0)),
        );
        for key in [
            "battery_source_count",
            "battery_online_source_count",
            "battery_valid_soc_source_count",
            "battery_battery_source_count",
        ] {
            self.payload.insert(key.to_owned(), json!(source_count));
        }
        insert_number(&mut self.payload, "battery_captured_at", Some(observed_at));
        insert_number(
            &mut self.payload,
            "battery_observed_monotonic",
            Some(observed_monotonic),
        );
        self.payload
            .insert("battery_status".to_owned(), Value::String("ok".to_owned()));
        self.mark_running();
    }

    fn resolve_grid(&mut self, external: Option<&ExternalEnergyCycle>, monotonic: f64) {
        let primary = external
            .and_then(|cycle| {
                cycle
                    .polls
                    .iter()
                    .find(|poll| poll.snapshot.source_id == self.grid_config.primary_source_id)
            })
            .map_or_else(
                || GridMeasurement::unavailable(self.grid_config.primary_source_id.clone()),
                |poll| GridMeasurement {
                    source_id: poll.snapshot.source_id.clone(),
                    power_w: poll.snapshot.grid_interaction_w,
                    captured_at: poll.snapshot.captured_at,
                    observed_monotonic: poll.observed_monotonic,
                    online: poll.snapshot.online,
                    confidence: poll.snapshot.confidence,
                },
            );
        let backup_power = optional_number(self.payload.get("grid_gateway_power"));
        let backup_captured = optional_number(self.payload.get("grid_gateway_captured_at"));
        let backup_monotonic = optional_number(self.payload.get("grid_gateway_observed_monotonic"));
        let backup = GridMeasurement {
            source_id: self.grid_config.backup_source_id.clone(),
            power_w: backup_power,
            captured_at: backup_captured,
            observed_monotonic: backup_monotonic,
            online: backup_power.is_some(),
            confidence: if backup_power.is_some() { 1.0 } else { 0.0 },
        };
        let result = self.grid_fusion.resolve(&primary, &backup, monotonic);
        self.publish_grid_selection(&primary, &result);
        self.publish_grid_diagnostics(&result);
    }

    fn publish_grid_selection(&mut self, primary: &GridMeasurement, result: &GridFusionResult) {
        let selected = result.measurement.as_ref();
        insert_number(
            &mut self.payload,
            "grid_power",
            selected.and_then(|measurement| measurement.power_w),
        );
        insert_number(
            &mut self.payload,
            "grid_captured_at",
            selected.and_then(|measurement| measurement.captured_at),
        );
        insert_number(
            &mut self.payload,
            "grid_observed_monotonic",
            selected.and_then(|measurement| measurement.observed_monotonic),
        );
        insert_number(&mut self.payload, "grid_primary_power", primary.power_w);
        insert_number(
            &mut self.payload,
            "grid_primary_captured_at",
            primary.captured_at,
        );
        let status = if self.grid_config.enabled {
            result.state.as_str()
        } else if selected.is_some() {
            "ok"
        } else {
            "missing"
        };
        self.payload
            .insert("grid_status".to_owned(), Value::String(status.to_owned()));
    }

    fn publish_grid_diagnostics(&mut self, result: &GridFusionResult) {
        self.payload.insert(
            "grid_fusion_enabled".to_owned(),
            Value::Bool(self.grid_config.enabled),
        );
        self.payload.insert(
            "grid_fusion_primary_source_id".to_owned(),
            Value::String(self.grid_config.primary_source_id.clone()),
        );
        self.payload.insert(
            "grid_fusion_backup_source_id".to_owned(),
            Value::String(self.grid_config.backup_source_id.clone()),
        );
        self.payload.insert(
            "grid_selected_source_id".to_owned(),
            Value::String(result.selected_source_id.clone()),
        );
        self.payload.insert(
            "grid_fusion_state".to_owned(),
            Value::String(result.state.clone()),
        );
        insert_number(
            &mut self.payload,
            "grid_fusion_confidence",
            Some(result.confidence),
        );
        self.payload.insert(
            "grid_fusion_primary_valid".to_owned(),
            Value::Bool(result.primary_valid),
        );
        self.payload.insert(
            "grid_fusion_backup_valid".to_owned(),
            Value::Bool(result.backup_valid),
        );
        insert_number(
            &mut self.payload,
            "grid_fusion_primary_age_seconds",
            result.primary_age_seconds,
        );
        insert_number(
            &mut self.payload,
            "grid_fusion_backup_age_seconds",
            result.backup_age_seconds,
        );
        insert_number(
            &mut self.payload,
            "grid_fusion_difference_watts",
            result.difference_watts,
        );
        insert_number(
            &mut self.payload,
            "grid_fusion_tolerance_watts",
            result.tolerance_watts,
        );
        self.payload.insert(
            "grid_fusion_primary_invalid_samples".to_owned(),
            json!(result.primary_invalid_samples),
        );
        self.payload.insert(
            "grid_fusion_primary_recovery_samples".to_owned(),
            json!(result.primary_recovery_samples),
        );
        self.payload.insert(
            "grid_fusion_mismatch_samples".to_owned(),
            json!(result.mismatch_samples),
        );
    }

    fn mark_running(&mut self) {
        self.payload.insert(
            "helper_state".to_owned(),
            Value::String("running".to_owned()),
        );
        self.payload.insert(
            "helper_status".to_owned(),
            Value::String("running".to_owned()),
        );
    }

    fn stamp_identity(&mut self) {
        self.payload.insert(
            "snapshot_version".to_owned(),
            json!(SNAPSHOT_SCHEMA_VERSION),
        );
        self.payload
            .insert("writer_pid".to_owned(), json!(std::process::id()));
        self.payload.insert(
            "helper_generation".to_owned(),
            json!(self.identity.helper_generation),
        );
        self.payload.insert(
            "runtime_instance_id".to_owned(),
            Value::String(self.identity.runtime_instance_id.clone()),
        );
    }
}

/// Atomically publish changed helper snapshots.
pub struct SnapshotWriter {
    path: PathBuf,
    last_content: Option<Vec<u8>>,
    sequence: u64,
}

impl SnapshotWriter {
    /// Create a writer for one volatile snapshot path.
    #[must_use]
    pub const fn new(path: PathBuf) -> Self {
        Self {
            path,
            last_content: None,
            sequence: 0,
        }
    }

    /// Write a changed snapshot and maintain the monotonic sequence contract.
    ///
    /// # Errors
    ///
    /// Returns an error when JSON serialization or atomic replacement fails.
    pub fn write(&mut self, payload: &Map<String, Value>) -> Result<bool> {
        let mut content_payload = payload.clone();
        content_payload.insert("snapshot_sequence".to_owned(), json!(0));
        let content = serde_json::to_vec(&content_payload)?;
        if self.last_content.as_ref() == Some(&content) {
            return Ok(false);
        }
        self.sequence = self.sequence.saturating_add(1);
        let mut serialized_payload = content_payload;
        serialized_payload.insert("snapshot_sequence".to_owned(), json!(self.sequence));
        let serialized = serde_json::to_vec(&serialized_payload)?;
        write_atomic(&self.path, &serialized, 0o600, "snapshot")?;
        self.last_content = Some(content);
        Ok(true)
    }
}

fn empty_snapshot() -> Map<String, Value> {
    let mut payload = Map::new();
    for (key, value) in [
        ("snapshot_version", json!(SNAPSHOT_SCHEMA_VERSION)),
        ("snapshot_sequence", json!(0)),
        ("captured_at", Value::Null),
        ("captured_monotonic", Value::Null),
        ("heartbeat_at", Value::Null),
        ("heartbeat_monotonic", Value::Null),
        ("writer_pid", json!(std::process::id())),
        ("helper_state", json!("starting")),
        ("helper_status", json!("starting")),
        ("pv_status", json!("missing")),
        ("pv_captured_at", Value::Null),
        ("pv_observed_monotonic", Value::Null),
        ("pv_power", Value::Null),
        ("battery_status", json!("missing")),
        ("battery_captured_at", Value::Null),
        ("battery_observed_monotonic", Value::Null),
        ("grid_status", json!("missing")),
        ("grid_captured_at", Value::Null),
        ("grid_observed_monotonic", Value::Null),
        ("grid_power", Value::Null),
        ("grid_gateway_captured_at", Value::Null),
        ("grid_gateway_observed_monotonic", Value::Null),
        ("grid_gateway_power", Value::Null),
        ("grid_primary_captured_at", Value::Null),
        ("grid_primary_power", Value::Null),
        ("grid_fusion_enabled", Value::Bool(false)),
        ("grid_fusion_primary_source_id", json!("")),
        ("grid_fusion_backup_source_id", json!("victron")),
        ("grid_selected_source_id", json!("")),
        ("grid_fusion_state", json!("unavailable")),
        ("grid_fusion_confidence", json!(0.0)),
        ("grid_fusion_primary_valid", Value::Bool(false)),
        ("grid_fusion_backup_valid", Value::Bool(false)),
        ("grid_fusion_primary_age_seconds", Value::Null),
        ("grid_fusion_backup_age_seconds", Value::Null),
        ("grid_fusion_difference_watts", Value::Null),
        ("grid_fusion_tolerance_watts", Value::Null),
        ("grid_fusion_primary_invalid_samples", json!(0)),
        ("grid_fusion_primary_recovery_samples", json!(0)),
        ("grid_fusion_mismatch_samples", json!(0)),
    ] {
        payload.insert(key.to_owned(), value);
    }
    apply_empty_battery(&mut payload);
    payload
}

fn apply_empty_battery(payload: &mut Map<String, Value>) {
    for key in [
        "battery_soc",
        "battery_combined_soc",
        "battery_combined_usable_capacity_wh",
        "battery_combined_charge_power_w",
        "battery_combined_discharge_power_w",
        "battery_combined_net_power_w",
        "battery_combined_ac_power_w",
        "battery_combined_pv_input_power_w",
        "battery_combined_grid_interaction_w",
        "battery_headroom_charge_w",
        "battery_headroom_discharge_w",
        "expected_near_term_export_w",
        "expected_near_term_import_w",
        "battery_discharge_balance_error_w",
        "battery_discharge_balance_max_abs_error_w",
        "battery_discharge_balance_total_discharge_w",
        "battery_average_confidence",
    ] {
        payload.insert(key.to_owned(), Value::Null);
    }
    for key in [
        "battery_source_count",
        "battery_online_source_count",
        "battery_valid_soc_source_count",
        "battery_battery_source_count",
        "battery_hybrid_inverter_source_count",
        "battery_inverter_source_count",
        "battery_discharge_balance_eligible_source_count",
        "battery_discharge_balance_active_source_count",
        "battery_discharge_balance_control_candidate_count",
        "battery_discharge_balance_control_ready_count",
        "battery_discharge_balance_supported_control_source_count",
        "battery_discharge_balance_experimental_control_source_count",
    ] {
        payload.insert(key.to_owned(), json!(0));
    }
    payload.insert("battery_discharge_balance_mode".to_owned(), json!(""));
    payload.insert(
        "battery_discharge_balance_target_distribution_mode".to_owned(),
        json!(""),
    );
    payload.insert("battery_sources".to_owned(), json!([]));
    payload.insert("battery_learning_profiles".to_owned(), json!({}));
}

fn insert_number(payload: &mut Map<String, Value>, key: &str, value: Option<f64>) {
    payload.insert(
        key.to_owned(),
        value
            .and_then(serde_json::Number::from_f64)
            .map_or(Value::Null, Value::Number),
    );
}

fn optional_number(value: Option<&Value>) -> Option<f64> {
    value
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite())
}

fn select_pv_projection<'a>(
    gateway: Option<&'a ProjectedEnergyValue>,
    external: Option<&'a ProjectedEnergyValue>,
    policy: &PvProjectionPolicy,
) -> Option<&'a ProjectedEnergyValue> {
    let candidates = match policy.name.as_str() {
        "gateway_only" => [gateway, None],
        "gateway_preferred" => [gateway, external],
        "external_preferred" => [external, gateway],
        "external_only" => [external, None],
        _ => [None, None],
    };
    let (first, second) = candidates.into();
    match (first, second) {
        (None, value) | (value, None) => value,
        (Some(first), Some(second)) => {
            let quality = |projection: &ProjectedEnergyValue| {
                (
                    usize::from(projection.measurement_status == ExternalMeasurementStatus::Fresh),
                    usize::from(projection.confidence >= 0.5),
                )
            };
            Some(if quality(second) > quality(first) {
                second
            } else {
                first
            })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{DueSources, SnapshotState};
    use crate::config::{GridFusionConfig, HelperConfig};
    use crate::energy::{ExternalPollingPolicy, PvProjectionPolicy};
    use crate::runtime::RuntimeIdentity;
    use crate::wire::{EnergyInputs, Measurement, MeasurementStatus};
    use std::path::PathBuf;

    fn config() -> HelperConfig {
        HelperConfig {
            config_path: PathBuf::from("/tmp/config.ini"),
            snapshot_path: PathBuf::from("/tmp/snapshot.json"),
            gateway_run_dir: PathBuf::from("/run/gateway"),
            energy_inputs_path: PathBuf::from("/run/gateway/energy-inputs.v4.bin"),
            energy_topology_path: PathBuf::from("/run/gateway/energy-topology.json"),
            command_dir: PathBuf::from("/run/gateway/dbus-commands"),
            gateway_max_age_seconds: 10.0,
            gateway_error_retry_seconds: 30.0,
            pv_poll_seconds: 2.0,
            grid_poll_seconds: 2.0,
            battery_poll_seconds: 10.0,
            loop_seconds: 2.0,
            validation_poll_seconds: 30.0,
            topology_refresh_seconds: 60.0,
            grid_backup_source_id: "victron".to_owned(),
            grid_fusion: GridFusionConfig {
                enabled: false,
                primary_source_id: String::new(),
                backup_source_id: "victron".to_owned(),
                primary_max_age_seconds: 10.0,
                backup_max_age_seconds: 10.0,
                minimum_confidence: 0.0,
                failover_samples: 1,
                recovery_samples: 1,
                failover_hold_seconds: 0.0,
                mismatch_absolute_watts: 1_000.0,
                mismatch_relative: 1.0,
                mismatch_samples: 1,
                future_tolerance_seconds: 1.0,
            },
            gateway_energy_source: None,
            energy_sources: Vec::new(),
            use_combined_battery_soc: true,
            energy_source_request_timeout_seconds: 2.0,
            external_polling: ExternalPollingPolicy {
                poll_interval_seconds: 2.0,
                backoff_base_seconds: 5.0,
                backoff_max_seconds: 60.0,
                last_good_max_age_seconds: 30.0,
                cycle_budget_seconds: 2.0,
            },
            pv_projection: PvProjectionPolicy {
                name: "gateway_preferred".to_owned(),
                external_source_id: String::new(),
            },
        }
    }

    fn measurement(value: f64, observed: f64, sources: &[&str]) -> Measurement {
        Measurement {
            value: Some(value),
            observed_at: 1_700_000_000.0 + observed,
            observed_monotonic: observed,
            status: MeasurementStatus::Fresh,
            confidence: 0.8,
            source_ids: sources.iter().map(|source| (*source).to_owned()).collect(),
            reason_code: String::new(),
        }
    }

    #[test]
    fn projects_gateway_values_with_python_battery_sign_semantics() {
        let inputs = EnergyInputs {
            sequence: 1,
            topology_generation: 1,
            captured_at: 1_700_000_100.0,
            captured_monotonic: 100.0,
            grid_power_w: measurement(-250.0, 99.0, &["grid"]),
            pv_power_w: measurement(2_000.0, 99.0, &["pv"]),
            battery_soc: measurement(55.0, 98.0, &["battery-a", "battery-b"]),
            battery_net_power_w: measurement(-500.0, 97.0, &["battery-a"]),
            battery_capacity_wh: measurement(5_120.0, 98.0, &["battery-a"]),
            battery_capacity_ah: measurement(100.0, 98.0, &["battery-a"]),
            battery_voltage_v: measurement(52.8, 98.0, &["battery-a"]),
        };
        let mut state = SnapshotState::new(
            RuntimeIdentity::new(Some(1), 3, "instance".to_owned()),
            &config(),
        );
        state.apply(
            Some(&inputs),
            DueSources::all(),
            1_700_000_101.0,
            101.0,
            10.0,
            None,
        );
        let payload = state.payload();
        assert_eq!(
            payload.get("pv_power").and_then(serde_json::Value::as_f64),
            Some(2_000.0)
        );
        assert_eq!(
            payload
                .get("grid_power")
                .and_then(serde_json::Value::as_f64),
            Some(-250.0)
        );
        assert_eq!(
            payload
                .get("battery_soc")
                .and_then(serde_json::Value::as_f64),
            Some(55.0)
        );
        assert_eq!(
            payload
                .get("battery_combined_charge_power_w")
                .and_then(serde_json::Value::as_f64),
            Some(500.0)
        );
        assert_eq!(
            payload
                .get("battery_combined_discharge_power_w")
                .and_then(serde_json::Value::as_f64),
            Some(0.0)
        );
        assert_eq!(
            payload
                .get("battery_captured_at")
                .and_then(serde_json::Value::as_f64),
            Some(1_700_000_097.0)
        );
    }

    #[test]
    fn stale_gateway_values_become_explicitly_missing() {
        let inputs = EnergyInputs {
            sequence: 1,
            topology_generation: 1,
            captured_at: 1_700_000_100.0,
            captured_monotonic: 100.0,
            grid_power_w: measurement(100.0, 10.0, &["grid"]),
            pv_power_w: measurement(200.0, 10.0, &["pv"]),
            battery_soc: measurement(50.0, 10.0, &["battery"]),
            battery_net_power_w: measurement(0.0, 10.0, &["battery"]),
            battery_capacity_wh: measurement(5_120.0, 10.0, &["battery"]),
            battery_capacity_ah: measurement(100.0, 10.0, &["battery"]),
            battery_voltage_v: measurement(52.8, 10.0, &["battery"]),
        };
        let mut state = SnapshotState::new(
            RuntimeIdentity::new(Some(1), 1, "instance".to_owned()),
            &config(),
        );
        state.apply(
            Some(&inputs),
            DueSources::all(),
            1_700_000_101.0,
            101.0,
            10.0,
            None,
        );
        assert_eq!(
            state.payload().get("pv_power"),
            Some(&serde_json::Value::Null)
        );
        assert_eq!(
            state.payload().get("battery_soc"),
            Some(&serde_json::Value::Null)
        );
        assert_eq!(
            state
                .payload()
                .get("grid_fusion_state")
                .and_then(serde_json::Value::as_str),
            Some("unavailable")
        );
    }
}
