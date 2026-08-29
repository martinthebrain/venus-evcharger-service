//! Transport-neutral contracts for configured external energy sources.

use std::collections::{BTreeMap, BTreeSet};

use serde::Serialize;
use serde_json::{Map, Value, json};

/// Hard upper bound that protects the threadless heartbeat and parent watchdog.
pub const MAX_EXTERNAL_CYCLE_BUDGET_SECONDS: f64 = 3.0;

/// Supported non-DBus connector families.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ConnectorType {
    TemplateHttp,
    Modbus,
    CommandJson,
    OpenDtuHttp,
}

impl ConnectorType {
    /// Parse one normalized connector name.
    #[must_use]
    pub fn parse(value: &str) -> Option<Self> {
        match value.trim().to_ascii_lowercase().as_str() {
            "template_http" => Some(Self::TemplateHttp),
            "modbus" => Some(Self::Modbus),
            "command_json" => Some(Self::CommandJson),
            "opendtu_http" => Some(Self::OpenDtuHttp),
            _ => None,
        }
    }

    /// Return the stable configuration value.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::TemplateHttp => "template_http",
            Self::Modbus => "modbus",
            Self::CommandJson => "command_json",
            Self::OpenDtuHttp => "opendtu_http",
        }
    }
}

/// Domain role assigned to one normalized source.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EnergyRole {
    Battery,
    HybridInverter,
    Inverter,
}

impl EnergyRole {
    /// Parse one role, falling back to a battery as the Python contract does.
    #[must_use]
    pub fn parse_or_battery(value: &str) -> Self {
        match value.trim().to_ascii_lowercase().as_str() {
            "hybrid-inverter" => Self::HybridInverter,
            "inverter" => Self::Inverter,
            _ => Self::Battery,
        }
    }

    /// Return the stable snapshot value.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Battery => "battery",
            Self::HybridInverter => "hybrid-inverter",
            Self::Inverter => "inverter",
        }
    }

    /// Return whether this source participates in battery balancing.
    #[must_use]
    pub const fn battery_like(self) -> bool {
        matches!(self, Self::Battery | Self::HybridInverter)
    }
}

/// Immutable configuration for one external source.
#[derive(Clone, Debug, PartialEq)]
pub struct EnergySourceDefinition {
    pub source_id: String,
    pub profile_name: String,
    pub role: EnergyRole,
    pub connector_type: Option<ConnectorType>,
    pub config_path: String,
    pub service_name: String,
    pub usable_capacity_wh: Option<f64>,
    pub battery_chemistry: String,
    pub capacity_auto_estimate: bool,
    pub capacity_estimate_min_soc: f64,
    pub capacity_startup_recheck_seconds: f64,
    pub estimated_capacity_wh: Option<f64>,
    pub estimated_capacity_ah: Option<f64>,
    pub estimated_capacity_nominal_voltage_v: Option<f64>,
    pub estimated_capacity_cell_count: Option<u32>,
    pub physical_id: String,
    pub physical_priority: i32,
}

/// One normalized runtime observation from an external connector.
#[derive(Clone, Debug, PartialEq)]
pub struct EnergySourceSnapshot {
    pub source_id: String,
    pub role: EnergyRole,
    pub service_name: String,
    pub soc: Option<f64>,
    pub usable_capacity_wh: Option<f64>,
    pub usable_capacity_source: String,
    pub installed_capacity_ah: Option<f64>,
    pub capacity_voltage_v: Option<f64>,
    pub capacity_nominal_voltage_v: Option<f64>,
    pub capacity_cell_count: Option<u32>,
    pub battery_chemistry: String,
    pub net_battery_power_w: Option<f64>,
    pub charge_limit_power_w: Option<f64>,
    pub discharge_limit_power_w: Option<f64>,
    pub ac_power_w: Option<f64>,
    pub pv_input_power_w: Option<f64>,
    pub grid_interaction_w: Option<f64>,
    pub ac_power_scope_key: String,
    pub pv_input_power_scope_key: String,
    pub grid_interaction_scope_key: String,
    pub operating_mode: String,
    pub online: bool,
    pub confidence: f64,
    pub captured_at: Option<f64>,
    pub physical_id: String,
    pub physical_priority: i32,
}

impl EnergySourceSnapshot {
    /// Build the explicit offline placeholder for one configured source.
    #[must_use]
    pub fn offline(definition: &EnergySourceDefinition) -> Self {
        Self {
            source_id: definition.source_id.clone(),
            role: definition.role,
            service_name: if definition.service_name.is_empty() {
                if definition.config_path.is_empty() {
                    definition.source_id.clone()
                } else {
                    definition.config_path.clone()
                }
            } else {
                definition.service_name.clone()
            },
            soc: None,
            usable_capacity_wh: definition.usable_capacity_wh,
            usable_capacity_source: String::new(),
            installed_capacity_ah: None,
            capacity_voltage_v: None,
            capacity_nominal_voltage_v: None,
            capacity_cell_count: None,
            battery_chemistry: definition.battery_chemistry.clone(),
            net_battery_power_w: None,
            charge_limit_power_w: None,
            discharge_limit_power_w: None,
            ac_power_w: None,
            pv_input_power_w: None,
            grid_interaction_w: None,
            ac_power_scope_key: String::new(),
            pv_input_power_scope_key: String::new(),
            grid_interaction_scope_key: String::new(),
            operating_mode: String::new(),
            online: false,
            confidence: 0.0,
            captured_at: None,
            physical_id: definition.physical_id.clone(),
            physical_priority: definition.physical_priority,
        }
    }

    /// Return positive charge power using the established battery sign.
    #[must_use]
    pub fn charge_power_w(&self) -> Option<f64> {
        self.net_battery_power_w.map(|value| (-value).max(0.0))
    }

    /// Return positive discharge power using the established battery sign.
    #[must_use]
    pub fn discharge_power_w(&self) -> Option<f64> {
        self.net_battery_power_w.map(|value| value.max(0.0))
    }

    /// Return whether this observation contains a value that can contribute.
    #[must_use]
    pub fn has_contributing_value(&self) -> bool {
        [
            self.soc,
            self.usable_capacity_wh,
            self.net_battery_power_w,
            self.charge_limit_power_w,
            self.discharge_limit_power_w,
            self.ac_power_w,
            self.pv_input_power_w,
            self.grid_interaction_w,
        ]
        .into_iter()
        .any(|value| value.is_some())
    }

    /// Return the stable source payload included in helper snapshots.
    #[must_use]
    pub fn payload(&self) -> Map<String, Value> {
        let mut payload = Map::new();
        for (key, value) in [
            ("source_id", json!(self.source_id)),
            ("physical_id", json!(self.physical_id)),
            ("physical_priority", json!(self.physical_priority)),
            ("role", json!(self.role.as_str())),
            ("service_name", json!(self.service_name)),
            ("soc", number(self.soc)),
            ("usable_capacity_wh", number(self.usable_capacity_wh)),
            ("usable_capacity_source", json!(self.usable_capacity_source)),
            ("installed_capacity_ah", number(self.installed_capacity_ah)),
            ("capacity_voltage_v", number(self.capacity_voltage_v)),
            (
                "capacity_nominal_voltage_v",
                number(self.capacity_nominal_voltage_v),
            ),
            ("capacity_cell_count", json!(self.capacity_cell_count)),
            ("battery_chemistry", json!(self.battery_chemistry)),
            ("net_battery_power_w", number(self.net_battery_power_w)),
            ("charge_power_w", number(self.charge_power_w())),
            ("discharge_power_w", number(self.discharge_power_w())),
            ("charge_limit_power_w", number(self.charge_limit_power_w)),
            (
                "discharge_limit_power_w",
                number(self.discharge_limit_power_w),
            ),
            ("ac_power_w", number(self.ac_power_w)),
            ("ac_output_power_w", number(self.ac_power_w)),
            ("pv_input_power_w", number(self.pv_input_power_w)),
            ("grid_interaction_w", number(self.grid_interaction_w)),
            ("ac_power_scope_key", json!(self.ac_power_scope_key)),
            (
                "pv_input_power_scope_key",
                json!(self.pv_input_power_scope_key),
            ),
            (
                "grid_interaction_scope_key",
                json!(self.grid_interaction_scope_key),
            ),
            ("operating_mode", json!(self.operating_mode)),
            ("online", json!(self.online)),
            ("confidence", number(Some(self.confidence))),
            ("captured_at", number(self.captured_at)),
        ] {
            payload.insert(key.to_owned(), value);
        }
        payload
    }
}

/// Bounded polling and backoff policy for external connectors.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ExternalPollingPolicy {
    pub poll_interval_seconds: f64,
    pub backoff_base_seconds: f64,
    pub backoff_max_seconds: f64,
    pub last_good_max_age_seconds: f64,
    pub cycle_budget_seconds: f64,
}

/// Configured policy for selecting gateway or external PV.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PvProjectionPolicy {
    pub name: String,
    pub external_source_id: String,
}

/// One selected PV value carrying both clock domains.
#[derive(Clone, Debug, PartialEq)]
pub struct ProjectedEnergyValue {
    pub value: f64,
    pub observed_at: f64,
    pub observed_monotonic: f64,
    pub source_id: String,
    pub confidence: f64,
    pub measurement_status: MeasurementStatus,
}

/// Freshness state of one external observation.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MeasurementStatus {
    Fresh,
    Stale,
    Expired,
    Missing,
}

impl MeasurementStatus {
    /// Return the stable diagnostic string.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Fresh => "fresh",
            Self::Stale => "stale",
            Self::Expired => "expired",
            Self::Missing => "missing",
        }
    }

    /// Return whether this status contributes to projections.
    #[must_use]
    pub const fn contributing(self) -> bool {
        matches!(self, Self::Fresh | Self::Stale)
    }
}

/// Diagnostic result for one configured source in a scheduler cycle.
#[derive(Clone, Debug, PartialEq)]
pub struct ExternalSourcePoll {
    pub snapshot: EnergySourceSnapshot,
    pub contributing: bool,
    pub poll_status: String,
    pub measurement_status: MeasurementStatus,
    pub attempted_at: Option<f64>,
    pub observed_at: Option<f64>,
    pub observed_monotonic: Option<f64>,
    pub next_poll_at: f64,
    pub age_seconds: Option<f64>,
    pub consecutive_failures: u32,
    pub last_error: String,
}

impl ExternalSourcePoll {
    /// Return the complete source plus scheduler diagnostics payload.
    #[must_use]
    pub fn payload(&self) -> Map<String, Value> {
        let mut payload = self.snapshot.payload();
        for (key, value) in [
            ("contributing", json!(self.contributing)),
            ("poll_status", json!(self.poll_status)),
            (
                "measurement_status",
                json!(self.measurement_status.as_str()),
            ),
            ("attempted_at", number(self.attempted_at)),
            ("observed_at", number(self.observed_at)),
            ("observed_monotonic", number(self.observed_monotonic)),
            ("next_poll_at", number(Some(self.next_poll_at))),
            ("age_seconds", number(self.age_seconds)),
            ("consecutive_failures", json!(self.consecutive_failures)),
            ("last_error", json!(self.last_error)),
        ] {
            payload.insert(key.to_owned(), value);
        }
        payload
    }
}

/// Mutable runtime-only learning profile for one external source.
#[derive(Clone, Debug, Default, PartialEq, Serialize)]
pub struct EnergyLearningProfile {
    pub source_id: String,
    pub sample_count: u64,
    pub active_sample_count: u64,
    pub charge_sample_count: u64,
    pub discharge_sample_count: u64,
    pub import_support_sample_count: u64,
    pub import_charge_sample_count: u64,
    pub export_charge_sample_count: u64,
    pub export_discharge_sample_count: u64,
    pub export_idle_sample_count: u64,
    pub day_active_sample_count: u64,
    pub night_active_sample_count: u64,
    pub day_charge_sample_count: u64,
    pub night_charge_sample_count: u64,
    pub day_discharge_sample_count: u64,
    pub night_discharge_sample_count: u64,
    pub response_sample_count: u64,
    pub smoothing_sample_count: u64,
    pub observed_max_charge_power_w: Option<f64>,
    pub observed_max_discharge_power_w: Option<f64>,
    pub observed_max_ac_power_w: Option<f64>,
    pub observed_max_pv_input_power_w: Option<f64>,
    pub observed_max_grid_import_w: Option<f64>,
    pub observed_max_grid_export_w: Option<f64>,
    pub observed_min_discharge_soc: Option<f64>,
    pub observed_max_charge_soc: Option<f64>,
    pub average_active_charge_power_w: Option<f64>,
    pub average_active_discharge_power_w: Option<f64>,
    pub average_active_power_delta_w: Option<f64>,
    pub typical_response_delay_seconds: Option<f64>,
    pub direction_change_count: u64,
    pub last_direction: String,
    pub last_activity_state: String,
    pub last_active_at: Option<f64>,
    pub last_inactive_at: Option<f64>,
    pub last_change_at: Option<f64>,
}

impl EnergyLearningProfile {
    /// Create the Python-compatible initial profile.
    #[must_use]
    pub fn new(source_id: String) -> Self {
        Self {
            source_id,
            last_direction: "idle".to_owned(),
            last_activity_state: "idle".to_owned(),
            ..Self::default()
        }
    }

    /// Return a JSON object including all derived diagnostics.
    #[must_use]
    pub fn payload(&self) -> Map<String, Value> {
        let mut payload = match serde_json::to_value(self) {
            Ok(Value::Object(value)) => value,
            _ => Map::new(),
        };
        for (key, value) in [
            (
                "support_bias",
                number(bias(self.discharge_sample_count, self.charge_sample_count)),
            ),
            (
                "import_support_bias",
                number(bias(
                    self.import_support_sample_count,
                    self.import_charge_sample_count,
                )),
            ),
            (
                "export_bias",
                number(bias(
                    self.export_charge_sample_count,
                    self.export_discharge_sample_count,
                )),
            ),
            (
                "battery_first_export_bias",
                number(bias(
                    self.export_charge_sample_count,
                    self.export_discharge_sample_count
                        .saturating_add(self.export_idle_sample_count),
                )),
            ),
            (
                "day_support_bias",
                number(bias(
                    self.day_discharge_sample_count,
                    self.day_charge_sample_count,
                )),
            ),
            (
                "night_support_bias",
                number(bias(
                    self.night_discharge_sample_count,
                    self.night_charge_sample_count,
                )),
            ),
            (
                "reserve_band_floor_soc",
                number(self.observed_min_discharge_soc),
            ),
            (
                "reserve_band_ceiling_soc",
                number(self.observed_max_charge_soc),
            ),
            ("reserve_band_width_soc", number(self.reserve_width())),
            (
                "power_smoothing_ratio",
                number(self.power_smoothing_ratio()),
            ),
        ] {
            payload.insert(key.to_owned(), value);
        }
        payload
    }

    fn reserve_width(&self) -> Option<f64> {
        let floor = self.observed_min_discharge_soc?;
        let ceiling = self.observed_max_charge_soc?;
        (ceiling >= floor).then_some(ceiling - floor)
    }

    fn power_smoothing_ratio(&self) -> Option<f64> {
        let delta = self.average_active_power_delta_w?;
        let powers: Vec<f64> = [
            self.average_active_charge_power_w,
            self.average_active_discharge_power_w,
        ]
        .into_iter()
        .flatten()
        .filter(|value| *value > 0.0)
        .collect();
        if powers.is_empty() {
            return None;
        }
        let reference = powers.iter().sum::<f64>() / count_as_f64(powers.len());
        Some((1.0 - delta / reference).clamp(0.0, 1.0))
    }
}

/// Aggregated values derived from all contributing sources.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct EnergyClusterSnapshot {
    pub effective_soc: Option<f64>,
    pub combined_soc: Option<f64>,
    pub combined_usable_capacity_wh: Option<f64>,
    pub combined_charge_power_w: Option<f64>,
    pub combined_discharge_power_w: Option<f64>,
    pub combined_charge_limit_power_w: Option<f64>,
    pub combined_discharge_limit_power_w: Option<f64>,
    pub combined_net_battery_power_w: Option<f64>,
    pub combined_ac_power_w: Option<f64>,
    pub combined_pv_input_power_w: Option<f64>,
    pub combined_grid_interaction_w: Option<f64>,
    pub average_confidence: Option<f64>,
    pub source_count: usize,
    pub online_source_count: usize,
    pub valid_soc_source_count: usize,
    pub battery_source_count: usize,
    pub hybrid_inverter_source_count: usize,
    pub inverter_source_count: usize,
}

/// Aggregate normalized source readings while deduplicating physical batteries.
#[must_use]
pub fn aggregate_energy_sources(sources: &[EnergySourceSnapshot]) -> EnergyClusterSnapshot {
    let weighted_sources = unique_weighted_soc_sources(sources);
    let weighted: Vec<(f64, f64)> = weighted_sources
        .iter()
        .filter_map(|source| {
            let soc = source.soc?;
            let capacity = source.usable_capacity_wh?;
            (capacity > 0.0).then_some((soc, capacity))
        })
        .collect();
    let total_capacity = weighted.iter().map(|(_, capacity)| capacity).sum::<f64>();
    let combined_soc = (total_capacity > 0.0).then(|| {
        weighted
            .iter()
            .map(|(soc, capacity)| soc * capacity)
            .sum::<f64>()
            / total_capacity
    });
    let online_soc: Vec<f64> = sources
        .iter()
        .filter(|source| source.online)
        .filter_map(|source| source.soc)
        .collect();
    let effective_soc = combined_soc.or_else(|| {
        (online_soc.len() == 1)
            .then(|| online_soc.first().copied())
            .flatten()
    });
    let confidences: Vec<f64> = sources
        .iter()
        .map(|source| source.confidence)
        .filter(|value| *value >= 0.0)
        .collect();
    EnergyClusterSnapshot {
        effective_soc,
        combined_soc,
        combined_usable_capacity_wh: (total_capacity > 0.0).then_some(total_capacity),
        combined_charge_power_w: sum_optional(
            sources.iter().map(EnergySourceSnapshot::charge_power_w),
        ),
        combined_discharge_power_w: sum_optional(
            sources.iter().map(EnergySourceSnapshot::discharge_power_w),
        ),
        combined_charge_limit_power_w: sum_optional(
            sources.iter().map(|source| source.charge_limit_power_w),
        ),
        combined_discharge_limit_power_w: sum_optional(
            sources.iter().map(|source| source.discharge_limit_power_w),
        ),
        combined_net_battery_power_w: sum_optional(
            sources.iter().map(|source| source.net_battery_power_w),
        ),
        combined_ac_power_w: sum_scoped(
            sources,
            |source| source.ac_power_w,
            |source| &source.ac_power_scope_key,
        ),
        combined_pv_input_power_w: sum_scoped(
            sources,
            |source| source.pv_input_power_w,
            |source| &source.pv_input_power_scope_key,
        ),
        combined_grid_interaction_w: sum_scoped(
            sources,
            |source| source.grid_interaction_w,
            |source| &source.grid_interaction_scope_key,
        ),
        average_confidence: (!confidences.is_empty())
            .then(|| confidences.iter().sum::<f64>() / count_as_f64(confidences.len())),
        source_count: sources.len(),
        online_source_count: sources.iter().filter(|source| source.online).count(),
        valid_soc_source_count: weighted.len(),
        battery_source_count: sources
            .iter()
            .filter(|source| source.role == EnergyRole::Battery)
            .count(),
        hybrid_inverter_source_count: sources
            .iter()
            .filter(|source| source.role == EnergyRole::HybridInverter)
            .count(),
        inverter_source_count: sources
            .iter()
            .filter(|source| source.role == EnergyRole::Inverter)
            .count(),
    }
}

fn unique_weighted_soc_sources(sources: &[EnergySourceSnapshot]) -> Vec<&EnergySourceSnapshot> {
    let mut independent = Vec::new();
    let mut physical: BTreeMap<&str, &EnergySourceSnapshot> = BTreeMap::new();
    for source in sources {
        if source.physical_id.is_empty() {
            independent.push(source);
            continue;
        }
        match physical.get(source.physical_id.as_str()) {
            Some(existing) if soc_quality(source) <= soc_quality(existing) => {}
            _ => {
                physical.insert(source.physical_id.as_str(), source);
            }
        }
    }
    independent.extend(physical.into_values());
    independent
}

fn soc_quality(source: &EnergySourceSnapshot) -> (bool, bool, i32, OrderedFloat, &str) {
    (
        source.online,
        source.captured_at.is_some_and(f64::is_finite),
        source.physical_priority,
        OrderedFloat::new(source.confidence),
        source.source_id.as_str(),
    )
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd)]
struct OrderedFloat(u64);

impl OrderedFloat {
    const fn new(value: f64) -> Self {
        if value.is_nan() {
            return Self(0);
        }
        let bits = value.to_bits();
        let ordered = if bits >> 63 == 0 {
            bits | (1_u64 << 63)
        } else {
            !bits
        };
        Self(ordered)
    }
}

fn sum_optional(values: impl Iterator<Item = Option<f64>>) -> Option<f64> {
    let numeric: Vec<f64> = values.flatten().collect();
    (!numeric.is_empty()).then(|| numeric.iter().sum())
}

fn sum_scoped<'a>(
    sources: &'a [EnergySourceSnapshot],
    value: impl Fn(&EnergySourceSnapshot) -> Option<f64>,
    scope: impl Fn(&'a EnergySourceSnapshot) -> &'a str,
) -> Option<f64> {
    let mut seen = BTreeSet::new();
    let mut values = Vec::new();
    for source in sources {
        let Some(item) = value(source) else {
            continue;
        };
        let key = scope(source).trim();
        if !key.is_empty() && !seen.insert(key) {
            continue;
        }
        values.push(item);
    }
    (!values.is_empty()).then(|| values.iter().sum())
}

fn bias(positive: u64, negative: u64) -> Option<f64> {
    let total = positive.saturating_add(negative);
    (total > 0).then(|| {
        (count_u64_as_f64(positive) - count_u64_as_f64(negative)) / count_u64_as_f64(total)
    })
}

fn count_as_f64(count: usize) -> f64 {
    f64::from(u32::try_from(count).unwrap_or(u32::MAX))
}

fn count_u64_as_f64(count: u64) -> f64 {
    f64::from(u32::try_from(count).unwrap_or(u32::MAX))
}

/// Return one finite JSON number or `null`.
#[must_use]
pub fn number(value: Option<f64>) -> Value {
    value
        .and_then(serde_json::Number::from_f64)
        .map_or(Value::Null, Value::Number)
}

#[cfg(test)]
mod tests {
    use super::{
        EnergyRole, EnergySourceDefinition, EnergySourceSnapshot, aggregate_energy_sources,
    };

    fn definition(source_id: &str, physical_id: &str, priority: i32) -> EnergySourceDefinition {
        EnergySourceDefinition {
            source_id: source_id.to_owned(),
            profile_name: String::new(),
            role: EnergyRole::Battery,
            connector_type: Some(super::ConnectorType::TemplateHttp),
            config_path: String::new(),
            service_name: String::new(),
            usable_capacity_wh: Some(1_000.0),
            battery_chemistry: "lfp".to_owned(),
            capacity_auto_estimate: true,
            capacity_estimate_min_soc: 95.0,
            capacity_startup_recheck_seconds: 300.0,
            estimated_capacity_wh: None,
            estimated_capacity_ah: None,
            estimated_capacity_nominal_voltage_v: None,
            estimated_capacity_cell_count: None,
            physical_id: physical_id.to_owned(),
            physical_priority: priority,
        }
    }

    fn snapshot(
        source_id: &str,
        physical_id: &str,
        priority: i32,
        soc: f64,
    ) -> EnergySourceSnapshot {
        let mut value =
            EnergySourceSnapshot::offline(&definition(source_id, physical_id, priority));
        value.soc = Some(soc);
        value.online = true;
        value.confidence = 1.0;
        value.captured_at = Some(10.0);
        value
    }

    #[test]
    fn weighted_soc_deduplicates_multiple_observers_of_one_battery() {
        let values = [
            snapshot("low", "same", 1, 20.0),
            snapshot("high", "same", 2, 80.0),
            snapshot("other", "", 0, 40.0),
        ];
        let cluster = aggregate_energy_sources(&values);
        assert_eq!(cluster.valid_soc_source_count, 2);
        assert_eq!(cluster.combined_soc, Some(60.0));
        assert_eq!(cluster.combined_usable_capacity_wh, Some(2_000.0));
    }
}
