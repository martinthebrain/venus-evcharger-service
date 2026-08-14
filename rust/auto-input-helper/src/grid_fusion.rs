//! Stateful primary/backup fusion for normalized grid measurements.

use crate::config::GridFusionConfig;

/// One normalized grid value with paired epoch and monotonic clocks.
#[derive(Clone, Debug, PartialEq)]
pub struct GridMeasurement {
    pub source_id: String,
    pub power_w: Option<f64>,
    pub captured_at: Option<f64>,
    pub observed_monotonic: Option<f64>,
    pub online: bool,
    pub confidence: f64,
}

impl GridMeasurement {
    /// Build an unavailable measurement carrying only its identity.
    #[must_use]
    pub const fn unavailable(source_id: String) -> Self {
        Self {
            source_id,
            power_w: None,
            captured_at: None,
            observed_monotonic: None,
            online: false,
            confidence: 0.0,
        }
    }

    fn usable(
        &self,
        monotonic: f64,
        max_age: f64,
        minimum_confidence: f64,
        future_tolerance: f64,
    ) -> bool {
        let Some(power) = self.power_w else {
            return false;
        };
        let Some(captured) = self.captured_at else {
            return false;
        };
        let Some(observed) = self.observed_monotonic else {
            return false;
        };
        self.online
            && power.is_finite()
            && captured.is_finite()
            && observed.is_finite()
            && self.confidence.is_finite()
            && self.confidence >= minimum_confidence
            && observed <= monotonic + future_tolerance
            && monotonic - observed <= max_age
    }

    fn age(&self, monotonic: f64) -> Option<f64> {
        self.observed_monotonic
            .filter(|value| value.is_finite())
            .map(|value| monotonic - value)
    }
}

/// Explainable result of one fusion decision.
#[derive(Clone, Debug, PartialEq)]
pub struct GridFusionResult {
    pub measurement: Option<GridMeasurement>,
    pub selected_source_id: String,
    pub state: String,
    pub confidence: f64,
    pub primary_valid: bool,
    pub backup_valid: bool,
    pub primary_age_seconds: Option<f64>,
    pub backup_age_seconds: Option<f64>,
    pub difference_watts: Option<f64>,
    pub tolerance_watts: Option<f64>,
    pub primary_invalid_samples: u32,
    pub primary_recovery_samples: u32,
    pub mismatch_samples: u32,
}

/// Hysteretic selector that keeps all switching state in one owner.
pub struct GridMeasurementFusion {
    config: GridFusionConfig,
    active_source_id: Option<String>,
    last_primary: Option<GridMeasurement>,
    primary_invalid_samples: u32,
    primary_recovery_samples: u32,
    mismatch_samples: u32,
}

impl GridMeasurementFusion {
    /// Create a selector from validated configuration.
    #[must_use]
    pub const fn new(config: GridFusionConfig) -> Self {
        Self {
            config,
            active_source_id: None,
            last_primary: None,
            primary_invalid_samples: 0,
            primary_recovery_samples: 0,
            mismatch_samples: 0,
        }
    }

    /// Resolve one coherent pair of primary and backup observations.
    #[must_use]
    pub fn resolve(
        &mut self,
        primary: &GridMeasurement,
        backup: &GridMeasurement,
        monotonic: f64,
    ) -> GridFusionResult {
        let primary_valid = primary.usable(
            monotonic,
            self.config.primary_max_age_seconds,
            self.config.minimum_confidence,
            self.config.future_tolerance_seconds,
        );
        let backup_valid = backup.usable(
            monotonic,
            self.config.backup_max_age_seconds,
            self.config.minimum_confidence,
            self.config.future_tolerance_seconds,
        );
        if primary_valid {
            self.last_primary = Some(primary.clone());
        }
        let (mut selected, mut state) =
            self.select(primary, backup, monotonic, primary_valid, backup_valid);
        let (difference, tolerance) =
            self.plausibility(primary, backup, primary_valid, backup_valid);
        if primary_valid && backup_valid && self.mismatch_samples >= self.config.mismatch_samples {
            selected = conservative(primary, backup);
            state = "disagreement";
        }
        let confidence = selected.as_ref().map_or(0.0, |item| item.confidence);
        let selected_source_id = selected
            .as_ref()
            .map_or_else(String::new, |item| item.source_id.clone());
        GridFusionResult {
            measurement: selected,
            selected_source_id,
            state: state.to_owned(),
            confidence,
            primary_valid,
            backup_valid,
            primary_age_seconds: primary.age(monotonic),
            backup_age_seconds: backup.age(monotonic),
            difference_watts: difference,
            tolerance_watts: tolerance,
            primary_invalid_samples: self.primary_invalid_samples,
            primary_recovery_samples: self.primary_recovery_samples,
            mismatch_samples: self.mismatch_samples,
        }
    }

    fn select(
        &mut self,
        primary: &GridMeasurement,
        backup: &GridMeasurement,
        monotonic: f64,
        primary_valid: bool,
        backup_valid: bool,
    ) -> (Option<GridMeasurement>, &'static str) {
        if !self.config.enabled {
            return if backup_valid {
                (Some(backup.clone()), "backup")
            } else {
                (None, "unavailable")
            };
        }
        match self.active_source_id.as_deref() {
            None => self.select_initial(primary, backup, primary_valid, backup_valid),
            Some(active) if active == self.config.primary_source_id => {
                self.select_primary(primary, backup, monotonic, primary_valid, backup_valid)
            }
            Some(_) => self.select_backup(primary, backup, primary_valid, backup_valid),
        }
    }

    fn select_initial(
        &mut self,
        primary: &GridMeasurement,
        backup: &GridMeasurement,
        primary_valid: bool,
        backup_valid: bool,
    ) -> (Option<GridMeasurement>, &'static str) {
        if primary_valid {
            self.activate_primary();
            (Some(primary.clone()), "primary")
        } else if backup_valid {
            self.activate_backup();
            (Some(backup.clone()), "backup")
        } else {
            (None, "unavailable")
        }
    }

    fn select_primary(
        &mut self,
        primary: &GridMeasurement,
        backup: &GridMeasurement,
        monotonic: f64,
        primary_valid: bool,
        backup_valid: bool,
    ) -> (Option<GridMeasurement>, &'static str) {
        if primary_valid {
            self.primary_invalid_samples = 0;
            return (Some(primary.clone()), "primary");
        }
        self.primary_invalid_samples = self.primary_invalid_samples.saturating_add(1);
        if self.primary_invalid_samples < self.config.failover_samples {
            if let Some(held) = self.held_primary(monotonic) {
                return (Some(held), "primary-held");
            }
        }
        if backup_valid {
            self.activate_backup();
            (Some(backup.clone()), "backup")
        } else {
            (None, "unavailable")
        }
    }

    fn select_backup(
        &mut self,
        primary: &GridMeasurement,
        backup: &GridMeasurement,
        primary_valid: bool,
        backup_valid: bool,
    ) -> (Option<GridMeasurement>, &'static str) {
        self.primary_recovery_samples = if primary_valid {
            self.primary_recovery_samples.saturating_add(1)
        } else {
            0
        };
        if primary_valid && self.primary_recovery_samples >= self.config.recovery_samples {
            self.activate_primary();
            return (Some(primary.clone()), "primary");
        }
        if backup_valid {
            return (
                Some(backup.clone()),
                if primary_valid {
                    "backup-recovery"
                } else {
                    "backup"
                },
            );
        }
        if primary_valid {
            (Some(primary.clone()), "primary-emergency")
        } else {
            (None, "unavailable")
        }
    }

    fn activate_primary(&mut self) {
        self.active_source_id = Some(self.config.primary_source_id.clone());
        self.primary_invalid_samples = 0;
        self.primary_recovery_samples = 0;
    }

    fn activate_backup(&mut self) {
        self.active_source_id = Some(self.config.backup_source_id.clone());
        self.primary_invalid_samples = 0;
        self.primary_recovery_samples = 0;
    }

    fn held_primary(&self, monotonic: f64) -> Option<GridMeasurement> {
        let primary = self.last_primary.as_ref()?;
        let observed = primary.observed_monotonic?;
        let limit = self.config.primary_max_age_seconds + self.config.failover_hold_seconds;
        (monotonic - observed <= limit).then(|| primary.clone())
    }

    fn plausibility(
        &mut self,
        primary: &GridMeasurement,
        backup: &GridMeasurement,
        primary_valid: bool,
        backup_valid: bool,
    ) -> (Option<f64>, Option<f64>) {
        if !primary_valid || !backup_valid {
            self.mismatch_samples = 0;
            return (None, None);
        }
        let Some(primary_power) = primary.power_w else {
            return (None, None);
        };
        let Some(backup_power) = backup.power_w else {
            return (None, None);
        };
        let difference = (primary_power - backup_power).abs();
        let scale = primary_power.abs().max(backup_power.abs());
        let tolerance = self
            .config
            .mismatch_absolute_watts
            .max(self.config.mismatch_relative * scale);
        self.mismatch_samples = if difference > tolerance {
            self.mismatch_samples.saturating_add(1)
        } else {
            0
        };
        (Some(difference), Some(tolerance))
    }
}

fn conservative(primary: &GridMeasurement, backup: &GridMeasurement) -> Option<GridMeasurement> {
    Some(GridMeasurement {
        source_id: "conservative".to_owned(),
        power_w: Some(primary.power_w?.max(backup.power_w?)),
        captured_at: Some(primary.captured_at?.min(backup.captured_at?)),
        observed_monotonic: Some(primary.observed_monotonic?.min(backup.observed_monotonic?)),
        online: true,
        confidence: primary.confidence.min(backup.confidence).min(0.5),
    })
}

#[cfg(test)]
mod tests {
    use super::{GridMeasurement, GridMeasurementFusion};
    use crate::config::GridFusionConfig;

    fn config() -> GridFusionConfig {
        GridFusionConfig {
            enabled: true,
            primary_source_id: "meter".to_owned(),
            backup_source_id: "victron".to_owned(),
            primary_max_age_seconds: 5.0,
            backup_max_age_seconds: 5.0,
            minimum_confidence: 0.5,
            failover_samples: 2,
            recovery_samples: 2,
            failover_hold_seconds: 2.0,
            mismatch_absolute_watts: 100.0,
            mismatch_relative: 0.1,
            mismatch_samples: 2,
            future_tolerance_seconds: 1.0,
        }
    }

    fn measurement(source: &str, power: f64, observed: f64) -> GridMeasurement {
        GridMeasurement {
            source_id: source.to_owned(),
            power_w: Some(power),
            captured_at: Some(1_700_000_000.0 + observed),
            observed_monotonic: Some(observed),
            online: true,
            confidence: 1.0,
        }
    }

    #[test]
    fn failover_and_recovery_require_configured_sample_counts() {
        let mut fusion = GridMeasurementFusion::new(config());
        let primary = measurement("meter", 100.0, 10.0);
        let backup = measurement("victron", 110.0, 10.0);
        assert_eq!(fusion.resolve(&primary, &backup, 10.0).state, "primary");
        let missing = GridMeasurement::unavailable("meter".to_owned());
        assert_eq!(
            fusion.resolve(&missing, &backup, 11.0).state,
            "primary-held"
        );
        assert_eq!(fusion.resolve(&missing, &backup, 12.0).state, "backup");
        assert_eq!(
            fusion.resolve(&primary, &backup, 12.0).state,
            "backup-recovery"
        );
        assert_eq!(fusion.resolve(&primary, &backup, 12.0).state, "primary");
    }

    #[test]
    fn persistent_disagreement_uses_conservative_import() {
        let mut fusion = GridMeasurementFusion::new(config());
        let primary = measurement("meter", -2_000.0, 10.0);
        let backup = measurement("victron", 500.0, 10.0);
        assert_eq!(fusion.resolve(&primary, &backup, 10.0).state, "primary");
        let result = fusion.resolve(&primary, &backup, 10.0);
        assert_eq!(result.state, "disagreement");
        assert_eq!(
            result.measurement.and_then(|item| item.power_w),
            Some(500.0)
        );
    }
}
