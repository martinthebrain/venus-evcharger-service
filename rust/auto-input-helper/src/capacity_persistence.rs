//! One-shot, write-sparing persistence for inferred battery capacity.

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};

use crate::config::HelperConfig;
use crate::error::Result;
use crate::ini::read_bounded_text;
use crate::storage::write_atomic;

const MAX_CONFIG_BYTES: u64 = 2 * 1024 * 1024;
const REQUIRED_STABLE_SAMPLES: u8 = 3;
const AMP_HOUR_TOLERANCE: f64 = 0.001;
const WATT_HOUR_TOLERANCE: f64 = 0.1;
const VOLTAGE_TOLERANCE: f64 = 0.001;
const MAX_WRITE_ATTEMPTS: u8 = 3;
const WRITE_RETRY_SECONDS: f64 = 60.0;

/// One complete LFP capacity estimate derived from fresh gateway measurements.
#[derive(Clone, Debug, PartialEq)]
pub struct CapacityEstimate {
    pub source_id: String,
    pub usable_capacity_wh: f64,
    pub installed_capacity_ah: f64,
    pub nominal_voltage_v: f64,
    pub cell_count: u32,
}

impl CapacityEstimate {
    /// Construct one finite, positive estimate.
    #[must_use]
    pub fn new(
        source_id: String,
        usable_capacity_wh: f64,
        installed_capacity_ah: f64,
        nominal_voltage_v: f64,
        cell_count: u32,
    ) -> Option<Self> {
        let values = [usable_capacity_wh, installed_capacity_ah, nominal_voltage_v];
        values
            .into_iter()
            .all(|value| value.is_finite() && value > 0.0)
            .then_some(())?;
        (cell_count > 0).then_some(Self {
            source_id,
            usable_capacity_wh,
            installed_capacity_ah,
            nominal_voltage_v,
            cell_count,
        })
    }

    fn stable_with(&self, other: &Self) -> bool {
        self.source_id == other.source_id
            && close(
                self.installed_capacity_ah,
                other.installed_capacity_ah,
                AMP_HOUR_TOLERANCE,
            )
            && close(
                self.usable_capacity_wh,
                other.usable_capacity_wh,
                WATT_HOUR_TOLERANCE,
            )
            && close(
                self.nominal_voltage_v,
                other.nominal_voltage_v,
                VOLTAGE_TOLERANCE,
            )
            && self.cell_count == other.cell_count
    }
}

/// Outcome of the one-time startup capacity recheck.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PersistenceOutcome {
    Pending,
    Unchanged,
    Written,
    Disabled,
}

/// Confirm one stable changed estimate and persist it at most once per process.
pub struct CapacityPersistence {
    config_path: PathBuf,
    source_id: String,
    configured_ah: Option<f64>,
    not_before_monotonic: f64,
    candidate: Option<CapacityEstimate>,
    stable_samples: u8,
    write_attempts: u8,
    next_write_attempt_monotonic: f64,
    complete: bool,
    enabled: bool,
}

impl CapacityPersistence {
    /// Build the startup recheck from the semantic gateway source definition.
    #[must_use]
    pub fn new(config: &HelperConfig, startup_monotonic: f64) -> Self {
        let definition = config.gateway_energy_source.as_ref();
        let enabled = definition.is_some_and(|source| {
            source.capacity_auto_estimate && source.usable_capacity_wh.is_none()
        });
        Self {
            config_path: config.config_path.clone(),
            source_id: definition.map_or_else(String::new, |source| source.source_id.clone()),
            configured_ah: definition.and_then(|source| source.estimated_capacity_ah),
            not_before_monotonic: startup_monotonic
                + definition.map_or(0.0, |source| source.capacity_startup_recheck_seconds),
            candidate: None,
            stable_samples: 0,
            write_attempts: 0,
            next_write_attempt_monotonic: 0.0,
            complete: !enabled,
            enabled,
        }
    }

    /// Observe one coherent estimate and complete the startup recheck at most once.
    ///
    /// # Errors
    ///
    /// Returns an error if the one required atomic configuration update fails.
    pub fn observe(
        &mut self,
        estimate: Option<&CapacityEstimate>,
        monotonic: f64,
    ) -> Result<PersistenceOutcome> {
        if !self.enabled {
            return Ok(PersistenceOutcome::Disabled);
        }
        if self.complete {
            return Ok(PersistenceOutcome::Unchanged);
        }
        let Some(estimate) = estimate.filter(|value| value.source_id == self.source_id) else {
            self.candidate = None;
            self.stable_samples = 0;
            return Ok(PersistenceOutcome::Pending);
        };
        if self
            .candidate
            .as_ref()
            .is_some_and(|candidate| candidate.stable_with(estimate))
        {
            self.stable_samples = self.stable_samples.saturating_add(1);
        } else {
            self.candidate = Some(estimate.clone());
            self.stable_samples = 1;
        }
        if self.stable_samples < REQUIRED_STABLE_SAMPLES
            || monotonic < self.not_before_monotonic
            || monotonic < self.next_write_attempt_monotonic
        {
            return Ok(PersistenceOutcome::Pending);
        }
        if self.configured_ah.is_some_and(|configured| {
            close(
                configured,
                estimate.installed_capacity_ah,
                AMP_HOUR_TOLERANCE,
            )
        }) {
            self.complete = true;
            return Ok(PersistenceOutcome::Unchanged);
        }
        match persist_estimate(&self.config_path, estimate) {
            Ok(written) => {
                self.complete = true;
                Ok(if written {
                    PersistenceOutcome::Written
                } else {
                    PersistenceOutcome::Unchanged
                })
            }
            Err(error) => {
                self.write_attempts = self.write_attempts.saturating_add(1);
                self.next_write_attempt_monotonic = monotonic + WRITE_RETRY_SECONDS;
                if self.write_attempts >= MAX_WRITE_ATTEMPTS {
                    self.complete = true;
                }
                Err(error)
            }
        }
    }
}

fn persist_estimate(path: &Path, estimate: &CapacityEstimate) -> Result<bool> {
    let text = read_bounded_text(path, MAX_CONFIG_BYTES, "capacity persistence configuration")?;
    let values = estimated_capacity_values(estimate);
    let updated = upsert_default_values(&text, &values);
    if updated == text {
        return Ok(false);
    }
    let mode = fs::metadata(path)
        .map(|metadata| metadata.permissions().mode() & 0o777)
        .unwrap_or(0o600);
    write_atomic(
        path,
        updated.as_bytes(),
        mode,
        "capacity persistence configuration",
    )?;
    Ok(true)
}

fn estimated_capacity_values(estimate: &CapacityEstimate) -> [(String, String); 4] {
    let prefix = if estimate.source_id == "primary_battery" {
        "AutoBatteryCapacityEstimated".to_owned()
    } else {
        format!("AutoEnergySource.{}.CapacityEstimated", estimate.source_id)
    };
    [
        (
            format!("{prefix}Wh"),
            number_text(estimate.usable_capacity_wh),
        ),
        (
            format!("{prefix}Ah"),
            number_text(estimate.installed_capacity_ah),
        ),
        (
            format!("{prefix}NominalVoltage"),
            number_text(estimate.nominal_voltage_v),
        ),
        (
            format!("{prefix}CellCount"),
            estimate.cell_count.to_string(),
        ),
    ]
}

fn upsert_default_values(text: &str, values: &[(String, String)]) -> String {
    let mut lines: Vec<String> = text.lines().map(str::to_owned).collect();
    let insertion = first_non_default_section(&lines).unwrap_or(lines.len());
    let mut seen = vec![false; values.len()];
    for line in lines.iter_mut().take(insertion) {
        let Some((key, separator)) = assignment_key_and_separator(line) else {
            continue;
        };
        if let Some((index, (_, value))) = values
            .iter()
            .enumerate()
            .find(|(_, (candidate, _))| candidate.eq_ignore_ascii_case(key))
        {
            let suffix = line[separator + 1..]
                .chars()
                .take_while(|character| character.is_whitespace())
                .collect::<String>();
            line.truncate(separator + 1);
            line.push_str(&suffix);
            line.push_str(value);
            seen[index] = true;
        }
    }
    let missing = values
        .iter()
        .enumerate()
        .filter(|(index, _)| !seen[*index])
        .map(|(_, (key, value))| format!("{key}={value}"));
    lines.splice(insertion..insertion, missing);
    let mut result = lines.join("\n");
    if text.ends_with('\n') || !result.is_empty() {
        result.push('\n');
    }
    result
}

fn first_non_default_section(lines: &[String]) -> Option<usize> {
    lines.iter().position(|line| {
        let trimmed = line.trim();
        trimmed.starts_with('[')
            && trimmed.ends_with(']')
            && !trimmed[1..trimmed.len() - 1]
                .trim()
                .eq_ignore_ascii_case("default")
    })
}

fn assignment_key_and_separator(line: &str) -> Option<(&str, usize)> {
    let trimmed = line.trim_start();
    if trimmed.is_empty() || trimmed.starts_with('#') || trimmed.starts_with(';') {
        return None;
    }
    let separator = line.find('=').or_else(|| line.find(':'))?;
    let key = line[..separator].trim();
    (!key.is_empty()).then_some((key, separator))
}

fn number_text(value: f64) -> String {
    if close(value, value.round(), 0.001) {
        return format!("{:.0}", value.round());
    }
    let rendered = format!("{value:.3}");
    rendered
        .trim_end_matches('0')
        .trim_end_matches('.')
        .to_owned()
}

fn close(left: f64, right: f64, tolerance: f64) -> bool {
    (left - right).abs() < tolerance
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use super::{CapacityEstimate, CapacityPersistence, PersistenceOutcome, upsert_default_values};
    use crate::config::HelperConfig;

    fn config_text(estimated_ah: f64, recheck_seconds: f64) -> String {
        format!(
            "[DEFAULT]\nDbusGatewayRunDir=/tmp/gateway\nAutoBatteryCapacityWh=0\nAutoBatteryCapacityAutoEstimate=1\nAutoBatteryCapacityEstimatedWh=4800\nAutoBatteryCapacityEstimatedAh={estimated_ah}\nAutoBatteryCapacityEstimatedNominalVoltage=48\nAutoBatteryCapacityEstimatedCellCount=15\nAutoBatteryCapacityStartupRecheckSeconds={recheck_seconds}\n"
        )
    }

    fn estimate(ah: f64) -> CapacityEstimate {
        CapacityEstimate::new("primary_battery".to_owned(), ah * 51.2, ah, 51.2, 16)
            .unwrap_or_else(|| unreachable!())
    }

    #[test]
    fn waits_for_three_stable_samples_and_the_startup_recheck() -> Result<(), String> {
        let directory = tempdir().map_err(|error| error.to_string())?;
        let config_path = directory.path().join("config.ini");
        fs::write(&config_path, config_text(100.0, 10.0)).map_err(|error| error.to_string())?;
        let config = HelperConfig::load(&config_path, None).map_err(|error| error.to_string())?;
        let mut persistence = CapacityPersistence::new(&config, 50.0);

        assert_eq!(
            persistence
                .observe(Some(&estimate(200.0)), 55.0)
                .map_err(|error| error.to_string())?,
            PersistenceOutcome::Pending
        );
        assert_eq!(
            persistence
                .observe(Some(&estimate(201.0)), 56.0)
                .map_err(|error| error.to_string())?,
            PersistenceOutcome::Pending
        );
        assert_eq!(
            persistence
                .observe(Some(&estimate(200.0)), 57.0)
                .map_err(|error| error.to_string())?,
            PersistenceOutcome::Pending
        );
        assert_eq!(
            persistence
                .observe(Some(&estimate(200.0)), 58.0)
                .map_err(|error| error.to_string())?,
            PersistenceOutcome::Pending
        );
        assert_eq!(
            persistence
                .observe(Some(&estimate(200.0)), 60.0)
                .map_err(|error| error.to_string())?,
            PersistenceOutcome::Written
        );
        let updated = fs::read_to_string(config_path).map_err(|error| error.to_string())?;
        assert!(updated.contains("AutoBatteryCapacityEstimatedWh=10240"));
        assert!(updated.contains("AutoBatteryCapacityEstimatedAh=200"));
        assert!(updated.contains("AutoBatteryCapacityEstimatedNominalVoltage=51.2"));
        assert!(updated.contains("AutoBatteryCapacityEstimatedCellCount=16"));
        Ok(())
    }

    #[test]
    fn unchanged_capacity_completes_without_rewriting() -> Result<(), String> {
        let directory = tempdir().map_err(|error| error.to_string())?;
        let config_path = directory.path().join("config.ini");
        let original = config_text(200.0, 0.0);
        fs::write(&config_path, &original).map_err(|error| error.to_string())?;
        let config = HelperConfig::load(&config_path, None).map_err(|error| error.to_string())?;
        let mut persistence = CapacityPersistence::new(&config, 10.0);
        for monotonic in [10.0, 11.0] {
            assert_eq!(
                persistence
                    .observe(Some(&estimate(200.000_9)), monotonic)
                    .map_err(|error| error.to_string())?,
                PersistenceOutcome::Pending
            );
        }
        assert_eq!(
            persistence
                .observe(Some(&estimate(200.000_9)), 12.0)
                .map_err(|error| error.to_string())?,
            PersistenceOutcome::Unchanged
        );
        assert_eq!(
            fs::read_to_string(config_path).map_err(|error| error.to_string())?,
            original
        );
        Ok(())
    }

    #[test]
    fn missing_samples_break_stability() -> Result<(), String> {
        let directory = tempdir().map_err(|error| error.to_string())?;
        let config_path = directory.path().join("config.ini");
        fs::write(&config_path, config_text(100.0, 0.0)).map_err(|error| error.to_string())?;
        let config = HelperConfig::load(&config_path, None).map_err(|error| error.to_string())?;
        let mut persistence = CapacityPersistence::new(&config, 0.0);

        for (candidate, monotonic) in [
            (Some(estimate(200.0)), 1.0),
            (Some(estimate(200.0)), 2.0),
            (None, 3.0),
            (Some(estimate(200.0)), 4.0),
            (Some(estimate(200.0)), 5.0),
        ] {
            assert_eq!(
                persistence
                    .observe(candidate.as_ref(), monotonic)
                    .map_err(|error| error.to_string())?,
                PersistenceOutcome::Pending
            );
        }
        assert_eq!(
            persistence
                .observe(Some(&estimate(200.0)), 6.0)
                .map_err(|error| error.to_string())?,
            PersistenceOutcome::Written
        );
        Ok(())
    }

    #[test]
    fn disabled_explicit_capacity_never_writes() -> Result<(), String> {
        let directory = tempdir().map_err(|error| error.to_string())?;
        let config_path = directory.path().join("config.ini");
        let explicit = config_text(100.0, 0.0)
            .replace("AutoBatteryCapacityWh=0", "AutoBatteryCapacityWh=5000");
        fs::write(&config_path, explicit).map_err(|error| error.to_string())?;
        let config = HelperConfig::load(&config_path, None).map_err(|error| error.to_string())?;
        let mut persistence = CapacityPersistence::new(&config, 0.0);
        assert_eq!(
            persistence
                .observe(Some(&estimate(200.0)), 100.0)
                .map_err(|error| error.to_string())?,
            PersistenceOutcome::Disabled
        );
        Ok(())
    }

    #[test]
    fn upsert_preserves_comments_layout_and_non_default_sections() {
        let values = [
            ("A".to_owned(), "3".to_owned()),
            ("C".to_owned(), "4".to_owned()),
        ];
        assert_eq!(
            upsert_default_values("[DEFAULT]\n# keep\n  A  =  1\n[Other]\nA=2\n", &values),
            "[DEFAULT]\n# keep\n  A  =  3\nC=4\n[Other]\nA=2\n"
        );
    }
}
