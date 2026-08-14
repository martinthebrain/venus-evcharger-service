//! Conservative near-term forecast derived from cluster and learning state.

use std::collections::BTreeMap;

use crate::energy::{EnergyClusterSnapshot, EnergyLearningProfile};

/// Forecast fields published by the Auto input helper.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct EnergyForecast {
    pub charge_headroom: Option<f64>,
    pub discharge_headroom: Option<f64>,
    pub expected_export: Option<f64>,
    pub expected_import: Option<f64>,
}

#[derive(Clone, Copy, Debug, Default)]
struct LearningSummary {
    observed_max_charge_power_w: Option<f64>,
    observed_max_discharge_power_w: Option<f64>,
    average_active_charge_power_w: Option<f64>,
    average_active_discharge_power_w: Option<f64>,
    typical_response_delay_seconds: Option<f64>,
    export_bias: Option<f64>,
    battery_first_export_bias: Option<f64>,
    import_support_bias: Option<f64>,
    support_bias: Option<f64>,
    power_smoothing_ratio: Option<f64>,
    reserve_floor_soc: Option<f64>,
    reserve_ceiling_soc: Option<f64>,
}

/// Derive the same conservative forecast as the maintained Python domain model.
pub fn derive_energy_forecast(
    cluster: &EnergyClusterSnapshot,
    profiles: &BTreeMap<String, EnergyLearningProfile>,
) -> EnergyForecast {
    let summary = summarize(profiles);
    let charge_power = non_negative(cluster.combined_charge_power_w);
    let discharge_power = non_negative(cluster.combined_discharge_power_w);
    let charge_limit = headroom_limit(
        non_negative(cluster.combined_charge_limit_power_w),
        summary.observed_max_charge_power_w,
        summary.average_active_charge_power_w,
        charge_power,
    );
    let discharge_limit = headroom_limit(
        non_negative(cluster.combined_discharge_limit_power_w),
        summary.observed_max_discharge_power_w,
        summary.average_active_discharge_power_w,
        discharge_power,
    );
    let charge_soc_scale = charge_soc_scale(cluster.combined_soc, summary.reserve_ceiling_soc);
    let discharge_soc_scale = discharge_soc_scale(cluster.combined_soc, summary.reserve_floor_soc);
    let charge_headroom = scaled_headroom(charge_limit, charge_power, charge_soc_scale);
    let discharge_headroom = scaled_headroom(discharge_limit, discharge_power, discharge_soc_scale);
    let response_weight = response_weight(summary.typical_response_delay_seconds);
    let smoothing = positive_bias(summary.power_smoothing_ratio, None);
    EnergyForecast {
        charge_headroom,
        discharge_headroom,
        expected_export: expected_export(
            cluster.combined_grid_interaction_w,
            charge_power,
            charge_headroom,
            charge_limit,
            positive_bias(summary.export_bias, None),
            positive_bias(summary.battery_first_export_bias, None),
            response_weight,
            smoothing,
        ),
        expected_import: expected_import(
            cluster.combined_grid_interaction_w,
            discharge_power,
            positive_bias(summary.import_support_bias, summary.support_bias),
            response_weight,
            smoothing,
            discharge_soc_scale,
        ),
    }
}

fn summarize(profiles: &BTreeMap<String, EnergyLearningProfile>) -> LearningSummary {
    let values: Vec<&EnergyLearningProfile> = profiles.values().collect();
    LearningSummary {
        observed_max_charge_power_w: sum_optional(
            values
                .iter()
                .map(|profile| profile.observed_max_charge_power_w),
        ),
        observed_max_discharge_power_w: sum_optional(
            values
                .iter()
                .map(|profile| profile.observed_max_discharge_power_w),
        ),
        average_active_charge_power_w: weighted_average(
            &values,
            |profile| profile.average_active_charge_power_w,
            |profile| profile.charge_sample_count,
        ),
        average_active_discharge_power_w: weighted_average(
            &values,
            |profile| profile.average_active_discharge_power_w,
            |profile| profile.discharge_sample_count,
        ),
        typical_response_delay_seconds: mean_optional(
            values
                .iter()
                .filter_map(|profile| profile.typical_response_delay_seconds),
        ),
        export_bias: bias(
            sum_counts(&values, |profile| profile.export_charge_sample_count),
            sum_counts(&values, |profile| profile.export_discharge_sample_count),
        ),
        battery_first_export_bias: bias(
            sum_counts(&values, |profile| profile.export_charge_sample_count),
            sum_counts(&values, |profile| {
                profile
                    .export_discharge_sample_count
                    .saturating_add(profile.export_idle_sample_count)
            }),
        ),
        import_support_bias: bias(
            sum_counts(&values, |profile| profile.import_support_sample_count),
            sum_counts(&values, |profile| profile.import_charge_sample_count),
        ),
        support_bias: bias(
            sum_counts(&values, |profile| profile.discharge_sample_count),
            sum_counts(&values, |profile| profile.charge_sample_count),
        ),
        power_smoothing_ratio: weighted_smoothing(&values),
        reserve_floor_soc: max_known(
            values
                .iter()
                .filter_map(|profile| profile.observed_min_discharge_soc),
        ),
        reserve_ceiling_soc: min_known(
            values
                .iter()
                .filter_map(|profile| profile.observed_max_charge_soc),
        ),
    }
}

fn headroom_limit(
    configured: Option<f64>,
    observed: Option<f64>,
    average: Option<f64>,
    current: Option<f64>,
) -> Option<f64> {
    let current = current.unwrap_or(0.0);
    configured
        .or(observed)
        .or_else(|| average.map(|value| value * 1.25))
        .map(|limit| limit.max(current))
        .or_else(|| (current > 0.0).then_some(current))
}

fn scaled_headroom(limit: Option<f64>, current: Option<f64>, scale: f64) -> Option<f64> {
    Some(((limit? - current.unwrap_or(0.0)).max(0.0) * scale).max(0.0))
}

#[allow(clippy::too_many_arguments)]
fn expected_export(
    grid_w: Option<f64>,
    charge_w: Option<f64>,
    headroom_w: Option<f64>,
    maximum_charge_w: Option<f64>,
    export_bias: f64,
    battery_first_bias: f64,
    response_weight: f64,
    smoothing_ratio: f64,
) -> Option<f64> {
    let base = (-grid_w?).max(0.0);
    let charge = charge_w.unwrap_or(0.0);
    let saturation = charge_saturation(headroom_w, maximum_charge_w, charge);
    let smoothing_weight = 0.5_f64.mul_add(-smoothing_ratio, 1.0);
    let risk = export_bias
        * response_weight
        * charge
        * 0.5_f64.mul_add(saturation, 0.5)
        * smoothing_weight;
    let capture =
        battery_first_bias * response_weight * charge * 0.75_f64.mul_add(smoothing_ratio, 0.25);
    Some((base + risk - capture).max(0.0))
}

fn expected_import(
    grid_w: Option<f64>,
    discharge_w: Option<f64>,
    support_bias: f64,
    response_weight: f64,
    smoothing_ratio: f64,
    soc_scale: f64,
) -> Option<f64> {
    let base = grid_w?.max(0.0);
    let relief = support_bias
        * response_weight
        * discharge_w.unwrap_or(0.0)
        * soc_scale
        * 0.25_f64.mul_add(-smoothing_ratio, 1.0);
    Some((base - relief).max(0.0))
}

fn charge_saturation(headroom: Option<f64>, maximum: Option<f64>, charge: f64) -> f64 {
    if charge <= 0.0 {
        return 0.0;
    }
    match (headroom, maximum) {
        (Some(headroom), Some(maximum)) if maximum > 0.0 => (1.0 - headroom / maximum).max(0.0),
        _ => 1.0,
    }
}

fn response_weight(delay_seconds: Option<f64>) -> f64 {
    delay_seconds.map_or(1.0, |delay| 1.0 / (1.0 + delay / 30.0))
}

fn charge_soc_scale(soc: Option<f64>, ceiling: Option<f64>) -> f64 {
    soc.zip(ceiling).map_or(1.0, |(soc, ceiling)| {
        ((ceiling - soc) / 10.0).clamp(0.0, 1.0)
    })
}

fn discharge_soc_scale(soc: Option<f64>, floor: Option<f64>) -> f64 {
    soc.zip(floor)
        .map_or(1.0, |(soc, floor)| ((soc - floor) / 10.0).clamp(0.0, 1.0))
}

fn positive_bias(value: Option<f64>, fallback: Option<f64>) -> f64 {
    value.or(fallback).unwrap_or(0.0).clamp(0.0, 1.0)
}

fn non_negative(value: Option<f64>) -> Option<f64> {
    value.filter(|candidate| *candidate >= 0.0)
}

fn weighted_smoothing(profiles: &[&EnergyLearningProfile]) -> Option<f64> {
    let mut weighted = 0.0;
    let mut weight = 0.0;
    for profile in profiles {
        let Some(ratio) = smoothing_ratio(profile) else {
            continue;
        };
        let sample_weight = count_as_f64(profile.smoothing_sample_count);
        weighted = ratio.mul_add(sample_weight, weighted);
        weight += sample_weight;
    }
    (weight > 0.0).then_some(weighted / weight)
}

fn smoothing_ratio(profile: &EnergyLearningProfile) -> Option<f64> {
    let delta = profile.average_active_power_delta_w?;
    let reference = mean_optional(
        [
            profile.average_active_charge_power_w,
            profile.average_active_discharge_power_w,
        ]
        .into_iter()
        .flatten()
        .filter(|value| *value > 0.0),
    )?;
    Some((1.0 - delta / reference).clamp(0.0, 1.0))
}

fn weighted_average(
    profiles: &[&EnergyLearningProfile],
    value: impl Fn(&EnergyLearningProfile) -> Option<f64>,
    count: impl Fn(&EnergyLearningProfile) -> u64,
) -> Option<f64> {
    let mut weighted = 0.0;
    let mut weight = 0.0;
    for profile in profiles {
        let Some(candidate) = value(profile) else {
            continue;
        };
        let candidate_weight = count_as_f64(count(profile));
        weighted = candidate.mul_add(candidate_weight, weighted);
        weight += candidate_weight;
    }
    (weight > 0.0).then_some(weighted / weight)
}

fn sum_counts(
    profiles: &[&EnergyLearningProfile],
    value: impl Fn(&EnergyLearningProfile) -> u64,
) -> u64 {
    profiles
        .iter()
        .fold(0_u64, |total, profile| total.saturating_add(value(profile)))
}

fn bias(positive: u64, negative: u64) -> Option<f64> {
    let total = positive.saturating_add(negative);
    (total > 0).then(|| (count_as_f64(positive) - count_as_f64(negative)) / count_as_f64(total))
}

fn sum_optional(values: impl Iterator<Item = Option<f64>>) -> Option<f64> {
    let values: Vec<f64> = values.flatten().collect();
    (!values.is_empty()).then(|| values.iter().sum())
}

fn mean_optional(values: impl Iterator<Item = f64>) -> Option<f64> {
    let values: Vec<f64> = values.collect();
    (!values.is_empty()).then(|| values.iter().sum::<f64>() / length_as_f64(values.len()))
}

fn max_known(values: impl Iterator<Item = f64>) -> Option<f64> {
    values.reduce(f64::max)
}

fn min_known(values: impl Iterator<Item = f64>) -> Option<f64> {
    values.reduce(f64::min)
}

fn count_as_f64(count: u64) -> f64 {
    f64::from(u32::try_from(count).unwrap_or(u32::MAX))
}

fn length_as_f64(length: usize) -> f64 {
    f64::from(u32::try_from(length).unwrap_or(u32::MAX))
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use super::derive_energy_forecast;
    use crate::energy::{EnergyClusterSnapshot, EnergyLearningProfile};

    #[test]
    fn configured_limit_precedes_observed_learning_limit() {
        let cluster = EnergyClusterSnapshot {
            combined_soc: Some(50.0),
            combined_charge_power_w: Some(200.0),
            combined_charge_limit_power_w: Some(1_000.0),
            combined_grid_interaction_w: Some(-300.0),
            ..EnergyClusterSnapshot::default()
        };
        let mut profile = EnergyLearningProfile::new("battery".to_owned());
        profile.observed_max_charge_power_w = Some(2_000.0);
        let forecast =
            derive_energy_forecast(&cluster, &BTreeMap::from([("battery".to_owned(), profile)]));
        assert_eq!(forecast.charge_headroom, Some(800.0));
        assert_eq!(forecast.expected_export, Some(300.0));
    }

    #[test]
    fn reserve_band_scales_charge_and_discharge_headroom() {
        let cluster = EnergyClusterSnapshot {
            combined_soc: Some(79.0),
            combined_charge_power_w: Some(0.0),
            combined_charge_limit_power_w: Some(1_000.0),
            combined_discharge_power_w: Some(0.0),
            combined_discharge_limit_power_w: Some(1_000.0),
            ..EnergyClusterSnapshot::default()
        };
        let mut profile = EnergyLearningProfile::new("battery".to_owned());
        profile.observed_max_charge_soc = Some(80.0);
        profile.observed_min_discharge_soc = Some(20.0);
        let forecast =
            derive_energy_forecast(&cluster, &BTreeMap::from([("battery".to_owned(), profile)]));
        assert_eq!(forecast.charge_headroom, Some(100.0));
        assert_eq!(forecast.discharge_headroom, Some(1_000.0));
    }
}
