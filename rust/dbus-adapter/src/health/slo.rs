// SPDX-License-Identifier: GPL-3.0-or-later
//! Complete gateway service-level objectives shared by health and scheduling.

use std::collections::BTreeMap;

use serde_json::{Value, json};

use crate::config::IniConfig;
use crate::publication::GuiFreshness;

const MIN_PUBLICATION_SCHEDULER_TOLERANCE_SECONDS: f64 = 0.05;

#[derive(Clone, Copy, Debug)]
pub struct SloThresholds {
    pub gui_max_age_seconds: f64,
    pub core_read_max_age_seconds: f64,
    pub queue_max_age_seconds: f64,
    pub mainloop_gap_max_ms: f64,
}

impl SloThresholds {
    pub fn from_config(config: &IniConfig) -> Self {
        Self {
            gui_max_age_seconds: config.f64("DbusGatewaySloGuiMaxAgeSeconds", 2.0).max(0.1),
            core_read_max_age_seconds: config
                .f64("DbusGatewaySloCoreReadMaxAgeSeconds", 5.0)
                .max(0.1),
            queue_max_age_seconds: config
                .f64("DbusGatewaySloQueueMaxAgeSeconds", 10.0)
                .max(0.1),
            mainloop_gap_max_ms: config
                .f64("DbusGatewaySloMainloopGapMaxMs", 500.0)
                .max(10.0),
        }
    }

    pub fn effective_gui_max_age_seconds(self, adaptive_tick_seconds: f64) -> f64 {
        self.gui_max_age_seconds
            .max(self.core_read_max_age_seconds * 2.0)
            + adaptive_tick_seconds.max(MIN_PUBLICATION_SCHEDULER_TOLERANCE_SECONDS)
    }
}

#[derive(Clone, Copy, Debug)]
pub struct SloObserved {
    pub gui: GuiFreshness,
    pub core_read_max_age_seconds: f64,
    pub core_read_missing_count: usize,
    pub core_read_nonfresh_count: usize,
    pub queue_oldest_age_seconds: f64,
    pub mainloop_max_gap_ms: f64,
}

#[derive(Clone, Debug)]
pub struct SloSnapshot {
    state: &'static str,
    violated: Vec<&'static str>,
    checks: BTreeMap<&'static str, bool>,
    targets: BTreeMap<&'static str, f64>,
    observed: BTreeMap<&'static str, f64>,
}

impl SloSnapshot {
    pub fn evaluate(
        thresholds: SloThresholds,
        adaptive_tick_seconds: f64,
        observed: SloObserved,
    ) -> Self {
        let gui_target = thresholds.effective_gui_max_age_seconds(adaptive_tick_seconds);
        let checks = slo_checks(thresholds, gui_target, observed);
        let violated = checks
            .iter()
            .filter_map(|(name, passed)| (!passed).then_some(*name))
            .collect::<Vec<_>>();
        let state = if violated.is_empty() {
            "ok"
        } else {
            "violated"
        };
        Self {
            state,
            violated,
            checks,
            targets: slo_targets(thresholds, gui_target, adaptive_tick_seconds),
            observed: slo_observed(observed),
        }
    }

    pub const fn violated(&self) -> bool {
        !self.violated.is_empty()
    }

    pub fn payload(&self) -> Value {
        json!({
            "state": self.state,
            "violated": self.violated,
            "checks": self.checks,
            "targets": self.targets,
            "observed": self.observed,
        })
    }
}

fn slo_checks(
    thresholds: SloThresholds,
    gui_target: f64,
    observed: SloObserved,
) -> BTreeMap<&'static str, bool> {
    BTreeMap::from([
        (
            "gui_fresh",
            fresh(
                observed.gui.maximum_age_seconds,
                observed.gui.missing_count,
                gui_target,
            ),
        ),
        (
            "gui_measurements_fresh",
            fresh(
                observed.gui.measurement_max_age_seconds,
                observed.gui.measurement_missing_count,
                gui_target,
            ),
        ),
        (
            "gui_controls_fresh",
            fresh(
                observed.gui.control_max_age_seconds,
                observed.gui.control_missing_count,
                gui_target,
            ),
        ),
        (
            "gui_session_fresh",
            fresh(
                observed.gui.session_max_age_seconds,
                observed.gui.session_missing_count,
                gui_target,
            ),
        ),
        (
            "core_reads_fresh",
            fresh(
                observed.core_read_max_age_seconds,
                observed.core_read_missing_count,
                thresholds.core_read_max_age_seconds,
            ) && observed.core_read_nonfresh_count == 0,
        ),
        (
            "queue_age_ok",
            in_range(
                observed.queue_oldest_age_seconds,
                thresholds.queue_max_age_seconds,
            ),
        ),
        (
            "mainloop_gap_ok",
            in_range(observed.mainloop_max_gap_ms, thresholds.mainloop_gap_max_ms),
        ),
    ])
}

fn slo_targets(
    thresholds: SloThresholds,
    gui_target: f64,
    adaptive_tick_seconds: f64,
) -> BTreeMap<&'static str, f64> {
    BTreeMap::from([
        ("gui_max_age_s", gui_target),
        ("gui_measurement_max_age_s", gui_target),
        ("gui_control_max_age_s", gui_target),
        ("gui_session_max_age_s", gui_target),
        ("gui_missing_field_count", 0.0),
        ("gui_measurement_missing_field_count", 0.0),
        ("gui_control_missing_field_count", 0.0),
        ("gui_session_missing_field_count", 0.0),
        ("configured_gui_max_age_s", thresholds.gui_max_age_seconds),
        (
            "publication_scheduler_tolerance_s",
            adaptive_tick_seconds.max(MIN_PUBLICATION_SCHEDULER_TOLERANCE_SECONDS),
        ),
        ("core_read_max_age_s", thresholds.core_read_max_age_seconds),
        ("core_read_missing_count", 0.0),
        ("core_read_nonfresh_count", 0.0),
        ("queue_max_age_s", thresholds.queue_max_age_seconds),
        ("mainloop_gap_max_ms", thresholds.mainloop_gap_max_ms),
    ])
}

fn slo_observed(observed: SloObserved) -> BTreeMap<&'static str, f64> {
    BTreeMap::from([
        ("gui_max_age_s", observed.gui.maximum_age_seconds),
        (
            "gui_measurement_max_age_s",
            observed.gui.measurement_max_age_seconds,
        ),
        (
            "gui_control_max_age_s",
            observed.gui.control_max_age_seconds,
        ),
        (
            "gui_session_max_age_s",
            observed.gui.session_max_age_seconds,
        ),
        (
            "gui_missing_field_count",
            count_as_f64(observed.gui.missing_count),
        ),
        (
            "gui_measurement_missing_field_count",
            count_as_f64(observed.gui.measurement_missing_count),
        ),
        (
            "gui_control_missing_field_count",
            count_as_f64(observed.gui.control_missing_count),
        ),
        (
            "gui_session_missing_field_count",
            count_as_f64(observed.gui.session_missing_count),
        ),
        ("core_read_max_age_s", observed.core_read_max_age_seconds),
        (
            "core_read_missing_count",
            count_as_f64(observed.core_read_missing_count),
        ),
        (
            "core_read_nonfresh_count",
            count_as_f64(observed.core_read_nonfresh_count),
        ),
        ("queue_oldest_age_s", observed.queue_oldest_age_seconds),
        ("mainloop_max_gap_ms_60s", observed.mainloop_max_gap_ms),
    ])
}

pub fn core_observed(freshness: &Value) -> (f64, usize, usize) {
    const KEYS: [&str; 3] = ["grid_power_w", "pv_power_w", "battery_soc"];
    let maximum_age = KEYS
        .into_iter()
        .filter_map(|key| freshness.get(format!("{key}_age_s"))?.as_f64())
        .fold(0.0, f64::max);
    let missing = KEYS
        .into_iter()
        .filter(|key| {
            freshness
                .get(format!("{key}_status"))
                .and_then(Value::as_str)
                .unwrap_or("missing")
                == "missing"
        })
        .count();
    let nonfresh = KEYS
        .into_iter()
        .filter(|key| {
            freshness
                .get(format!("{key}_status"))
                .and_then(Value::as_str)
                .is_some_and(|status| !matches!(status, "fresh" | "missing"))
        })
        .count();
    (maximum_age, missing, nonfresh)
}

fn fresh(age: f64, missing: usize, maximum: f64) -> bool {
    missing == 0 && in_range(age, maximum)
}

fn in_range(value: f64, maximum: f64) -> bool {
    value.is_finite() && value >= 0.0 && value <= maximum
}

fn count_as_f64(value: usize) -> f64 {
    f64::from(u32::try_from(value).unwrap_or(u32::MAX))
}

#[cfg(test)]
mod tests {
    use super::{SloObserved, SloSnapshot, SloThresholds};
    use crate::publication::GuiFreshness;
    use crate::runtime_policy_contract;

    fn thresholds() -> SloThresholds {
        SloThresholds {
            gui_max_age_seconds: 2.0,
            core_read_max_age_seconds: 5.0,
            queue_max_age_seconds: 10.0,
            mainloop_gap_max_ms: 500.0,
        }
    }

    #[test]
    fn every_python_slo_dimension_participates_in_the_result() {
        let mut observed = SloObserved {
            gui: GuiFreshness::default(),
            core_read_max_age_seconds: 1.0,
            core_read_missing_count: 0,
            core_read_nonfresh_count: 0,
            queue_oldest_age_seconds: 1.0,
            mainloop_max_gap_ms: 10.0,
        };
        assert!(!SloSnapshot::evaluate(thresholds(), 0.2, observed).violated());
        observed.core_read_nonfresh_count = 1;
        let payload = SloSnapshot::evaluate(thresholds(), 0.2, observed).payload();
        assert_eq!(payload["state"], "violated");
        assert_eq!(payload["checks"]["core_reads_fresh"], false);
    }

    #[test]
    fn scheduler_tolerance_extends_the_effective_gui_deadline() {
        let deadline = thresholds().effective_gui_max_age_seconds(0.2);
        assert!((deadline - 10.2).abs() < f64::EPSILON);
    }

    #[test]
    fn generated_python_slo_scenario_matches() -> Result<(), String> {
        let contract = runtime_policy_contract::load()?.slo;
        let thresholds = SloThresholds {
            gui_max_age_seconds: contract.thresholds.gui_max_age_seconds,
            core_read_max_age_seconds: contract.thresholds.core_read_max_age_seconds,
            queue_max_age_seconds: contract.thresholds.queue_max_age_seconds,
            mainloop_gap_max_ms: contract.thresholds.mainloop_gap_max_ms,
        };
        let value = |name: &str| {
            contract
                .observed
                .get(name)
                .copied()
                .ok_or_else(|| format!("missing generated SLO value: {name}"))
        };
        let count = |name: &str| {
            let number = value(name)?;
            if number < 0.0 || number.fract() != 0.0 {
                return Err(format!("invalid generated SLO count: {name}"));
            }
            number
                .to_string()
                .parse::<usize>()
                .map_err(|error| error.to_string())
        };
        let observed = SloObserved {
            gui: GuiFreshness {
                maximum_age_seconds: value("gui_max_age_s")?,
                measurement_max_age_seconds: value("gui_measurement_max_age_s")?,
                control_max_age_seconds: value("gui_control_max_age_s")?,
                session_max_age_seconds: value("gui_session_max_age_s")?,
                missing_count: count("gui_missing_field_count")?,
                measurement_missing_count: count("gui_measurement_missing_field_count")?,
                control_missing_count: count("gui_control_missing_field_count")?,
                session_missing_count: count("gui_session_missing_field_count")?,
            },
            core_read_max_age_seconds: value("core_read_max_age_s")?,
            core_read_missing_count: count("core_read_missing_count")?,
            core_read_nonfresh_count: count("core_read_nonfresh_count")?,
            queue_oldest_age_seconds: value("queue_oldest_age_s")?,
            mainloop_max_gap_ms: value("mainloop_max_gap_ms_60s")?,
        };
        let payload = SloSnapshot::evaluate(
            thresholds,
            contract.thresholds.publication_scheduler_tolerance_seconds,
            observed,
        )
        .payload();
        assert_eq!(payload["checks"], contract.checks);
        assert_eq!(payload["targets"], contract.targets);
        Ok(())
    }
}
