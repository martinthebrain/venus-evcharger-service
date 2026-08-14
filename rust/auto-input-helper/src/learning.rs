//! Runtime-only learning for normalized external energy observations.

use crate::energy::{EnergyLearningProfile, EnergySourceSnapshot};

const ACTIVE_POWER_THRESHOLD_W: f64 = 50.0;
const GRID_ACTIVITY_THRESHOLD_W: f64 = 100.0;

#[derive(Clone, Copy)]
struct SourceMetrics {
    direction: &'static str,
    active: bool,
    charge_power_w: Option<f64>,
    discharge_power_w: Option<f64>,
    ac_power_w: Option<f64>,
    pv_input_power_w: Option<f64>,
    grid_import_w: Option<f64>,
    grid_export_w: Option<f64>,
}

/// Incorporate one newly observed source sample into its learning profile.
pub fn update_learning_profile(
    profile: &mut EnergyLearningProfile,
    source: &EnergySourceSnapshot,
    now: f64,
) {
    let metrics = source_metrics(source);
    let direction_changed = direction_changed(profile, metrics.direction);
    let response_delay = activation_response_delay(profile, metrics, direction_changed, now);
    let smoothing_delta = smoothing_delta(profile, metrics);
    update_averages(profile, metrics, response_delay, smoothing_delta);
    update_counts(profile, metrics, response_delay, smoothing_delta, now);
    update_observations(profile, source, metrics);
    update_activity_markers(profile, metrics, direction_changed, now);
}

fn source_metrics(source: &EnergySourceSnapshot) -> SourceMetrics {
    let direction = match source.net_battery_power_w {
        Some(value) if value <= -ACTIVE_POWER_THRESHOLD_W => "charge",
        Some(value) if value >= ACTIVE_POWER_THRESHOLD_W => "discharge",
        _ => "idle",
    };
    let active = matches!(direction, "charge" | "discharge")
        || [source.ac_power_w, source.pv_input_power_w]
            .into_iter()
            .flatten()
            .any(|value| value.abs() >= ACTIVE_POWER_THRESHOLD_W);
    SourceMetrics {
        direction,
        active,
        charge_power_w: source.charge_power_w(),
        discharge_power_w: source.discharge_power_w(),
        ac_power_w: source.ac_power_w.map(f64::abs),
        pv_input_power_w: positive(source.pv_input_power_w),
        grid_import_w: positive(source.grid_interaction_w),
        grid_export_w: positive(source.grid_interaction_w.map(|value| (-value).max(0.0))),
    }
}

fn update_counts(
    profile: &mut EnergyLearningProfile,
    metrics: SourceMetrics,
    response_delay: Option<f64>,
    smoothing_delta: Option<f64>,
    now: f64,
) {
    profile.sample_count = profile.sample_count.saturating_add(1);
    increment_if(&mut profile.active_sample_count, metrics.active);
    increment_if(
        &mut profile.charge_sample_count,
        metrics.direction == "charge",
    );
    increment_if(
        &mut profile.discharge_sample_count,
        metrics.direction == "discharge",
    );
    update_grid_counts(profile, metrics);
    update_period_counts(profile, metrics, now);
    increment_if(&mut profile.response_sample_count, response_delay.is_some());
    increment_if(
        &mut profile.smoothing_sample_count,
        smoothing_delta.is_some(),
    );
}

fn update_grid_counts(profile: &mut EnergyLearningProfile, metrics: SourceMetrics) {
    let importing = metrics
        .grid_import_w
        .is_some_and(|value| value >= GRID_ACTIVITY_THRESHOLD_W);
    let exporting = metrics
        .grid_export_w
        .is_some_and(|value| value >= GRID_ACTIVITY_THRESHOLD_W);
    increment_if(
        &mut profile.import_support_sample_count,
        metrics.direction == "discharge" && importing,
    );
    increment_if(
        &mut profile.import_charge_sample_count,
        metrics.direction == "charge" && importing,
    );
    increment_if(
        &mut profile.export_charge_sample_count,
        metrics.direction == "charge" && exporting,
    );
    increment_if(
        &mut profile.export_discharge_sample_count,
        metrics.direction == "discharge" && exporting,
    );
    increment_if(
        &mut profile.export_idle_sample_count,
        metrics.direction == "idle" && exporting,
    );
}

fn update_period_counts(profile: &mut EnergyLearningProfile, metrics: SourceMetrics, now: f64) {
    let hour = (now / 3_600.0).floor().rem_euclid(24.0);
    let day = (6.0..22.0).contains(&hour);
    increment_if(&mut profile.day_active_sample_count, metrics.active && day);
    increment_if(
        &mut profile.night_active_sample_count,
        metrics.active && !day,
    );
    increment_if(
        &mut profile.day_charge_sample_count,
        metrics.direction == "charge" && day,
    );
    increment_if(
        &mut profile.night_charge_sample_count,
        metrics.direction == "charge" && !day,
    );
    increment_if(
        &mut profile.day_discharge_sample_count,
        metrics.direction == "discharge" && day,
    );
    increment_if(
        &mut profile.night_discharge_sample_count,
        metrics.direction == "discharge" && !day,
    );
}

fn update_observations(
    profile: &mut EnergyLearningProfile,
    source: &EnergySourceSnapshot,
    metrics: SourceMetrics,
) {
    profile.observed_max_charge_power_w =
        max_optional(profile.observed_max_charge_power_w, metrics.charge_power_w);
    profile.observed_max_discharge_power_w = max_optional(
        profile.observed_max_discharge_power_w,
        metrics.discharge_power_w,
    );
    profile.observed_max_ac_power_w =
        max_optional(profile.observed_max_ac_power_w, metrics.ac_power_w);
    profile.observed_max_pv_input_power_w = max_optional(
        profile.observed_max_pv_input_power_w,
        metrics.pv_input_power_w,
    );
    profile.observed_max_grid_import_w =
        max_optional(profile.observed_max_grid_import_w, metrics.grid_import_w);
    profile.observed_max_grid_export_w =
        max_optional(profile.observed_max_grid_export_w, metrics.grid_export_w);
    if metrics.direction == "discharge" {
        profile.observed_min_discharge_soc =
            min_optional(profile.observed_min_discharge_soc, source.soc);
    }
    if metrics.direction == "charge" {
        profile.observed_max_charge_soc = max_optional(profile.observed_max_charge_soc, source.soc);
    }
}

fn update_averages(
    profile: &mut EnergyLearningProfile,
    metrics: SourceMetrics,
    response_delay: Option<f64>,
    smoothing_delta: Option<f64>,
) {
    if metrics.direction == "charge" {
        profile.average_active_charge_power_w = rolling_average(
            profile.average_active_charge_power_w,
            profile.charge_sample_count,
            metrics.charge_power_w,
        );
    }
    if metrics.direction == "discharge" {
        profile.average_active_discharge_power_w = rolling_average(
            profile.average_active_discharge_power_w,
            profile.discharge_sample_count,
            metrics.discharge_power_w,
        );
    }
    profile.average_active_power_delta_w = rolling_average(
        profile.average_active_power_delta_w,
        profile.smoothing_sample_count,
        smoothing_delta,
    );
    profile.typical_response_delay_seconds = rolling_average(
        profile.typical_response_delay_seconds,
        profile.response_sample_count,
        response_delay,
    );
}

fn update_activity_markers(
    profile: &mut EnergyLearningProfile,
    metrics: SourceMetrics,
    changed: bool,
    now: f64,
) {
    increment_if(&mut profile.direction_change_count, changed);
    metrics.direction.clone_into(&mut profile.last_direction);
    if metrics.active {
        "active".clone_into(&mut profile.last_activity_state);
        profile.last_active_at = Some(now);
    } else {
        "idle".clone_into(&mut profile.last_activity_state);
        profile.last_inactive_at = Some(now);
    }
    profile.last_change_at = Some(now);
}

fn activation_response_delay(
    profile: &EnergyLearningProfile,
    metrics: SourceMetrics,
    changed: bool,
    now: f64,
) -> Option<f64> {
    if !metrics.active {
        return None;
    }
    if profile.last_activity_state != "active" {
        return profile.last_inactive_at.map(|value| (now - value).max(0.0));
    }
    changed
        .then(|| profile.last_change_at.map(|value| (now - value).max(0.0)))
        .flatten()
}

fn smoothing_delta(profile: &EnergyLearningProfile, metrics: SourceMetrics) -> Option<f64> {
    match metrics.direction {
        "charge" => pair_delta(
            metrics.charge_power_w,
            profile.average_active_charge_power_w,
        ),
        "discharge" => pair_delta(
            metrics.discharge_power_w,
            profile.average_active_discharge_power_w,
        ),
        _ => None,
    }
}

fn direction_changed(profile: &EnergyLearningProfile, direction: &str) -> bool {
    matches!(direction, "charge" | "discharge")
        && matches!(profile.last_direction.as_str(), "charge" | "discharge")
        && profile.last_direction != direction
}

fn pair_delta(current: Option<f64>, average: Option<f64>) -> Option<f64> {
    Some((current? - average?).abs())
}

const fn max_optional(current: Option<f64>, candidate: Option<f64>) -> Option<f64> {
    match (current, candidate) {
        (Some(left), Some(right)) => Some(left.max(right)),
        (None, value) | (value, None) => value,
    }
}

const fn min_optional(current: Option<f64>, candidate: Option<f64>) -> Option<f64> {
    match (current, candidate) {
        (Some(left), Some(right)) => Some(left.min(right)),
        (None, value) | (value, None) => value,
    }
}

fn rolling_average(current: Option<f64>, count: u64, candidate: Option<f64>) -> Option<f64> {
    let candidate = candidate?;
    Some(current.map_or(candidate, |value| {
        let count = count_as_f64(count);
        value.mul_add(count, candidate) / (count + 1.0)
    }))
}

fn positive(value: Option<f64>) -> Option<f64> {
    value.filter(|candidate| *candidate > 0.0)
}

fn increment_if(value: &mut u64, condition: bool) {
    *value = value.saturating_add(u64::from(condition));
}

fn count_as_f64(count: u64) -> f64 {
    f64::from(u32::try_from(count).unwrap_or(u32::MAX))
}

#[cfg(test)]
mod tests {
    use super::update_learning_profile;
    use crate::energy::{
        ConnectorType, EnergyLearningProfile, EnergyRole, EnergySourceDefinition,
        EnergySourceSnapshot,
    };

    fn source(net_power_w: f64, grid_w: f64, soc: f64) -> EnergySourceSnapshot {
        let definition = EnergySourceDefinition {
            source_id: "battery".to_owned(),
            profile_name: String::new(),
            role: EnergyRole::Battery,
            connector_type: Some(ConnectorType::CommandJson),
            config_path: String::new(),
            service_name: "test".to_owned(),
            usable_capacity_wh: None,
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
        };
        EnergySourceSnapshot {
            soc: Some(soc),
            net_battery_power_w: Some(net_power_w),
            grid_interaction_w: Some(grid_w),
            online: true,
            confidence: 1.0,
            captured_at: Some(1_700_000_000.0),
            ..EnergySourceSnapshot::offline(&definition)
        }
    }

    #[test]
    fn charge_and_discharge_samples_preserve_response_contracts() {
        let mut profile = EnergyLearningProfile::new("battery".to_owned());
        update_learning_profile(&mut profile, &source(-400.0, -250.0, 80.0), 7.0 * 3_600.0);
        update_learning_profile(
            &mut profile,
            &source(300.0, 200.0, 40.0),
            7.0_f64.mul_add(3_600.0, 5.0),
        );
        assert_eq!(profile.sample_count, 2);
        assert_eq!(profile.charge_sample_count, 1);
        assert_eq!(profile.discharge_sample_count, 1);
        assert_eq!(profile.direction_change_count, 1);
        assert_eq!(profile.typical_response_delay_seconds, Some(5.0));
        assert_eq!(profile.observed_max_charge_soc, Some(80.0));
        assert_eq!(profile.observed_min_discharge_soc, Some(40.0));
    }
}
