//! Observer scheduling and cooldown policy.

use std::path::{Path, PathBuf};
use std::thread;
use std::time::{Duration, Instant};

use crate::artifact::{
    DEFAULT_MOUNTS_PATH, DEFAULT_STORAGE_LOCK_PATH, mounted_storage_candidates, read_mounts,
    write_incident_with_lease,
};
use crate::config::ObserverConfig;
use crate::error::Result;
use crate::snapshot::ForensicSnapshot;

/// Runtime options accepted by the observer entrypoint.
#[derive(Clone, Debug, PartialEq)]
pub struct ObserverOptions {
    /// Main EV-charger configuration.
    pub config_path: PathBuf,
    /// Initial delay before any IO.
    pub start_delay_seconds: f64,
    /// Delay between observations, clamped to at least one second.
    pub interval_seconds: f64,
    /// Minimum duration between persisted incidents.
    pub cooldown_seconds: f64,
    /// Mount-table path, injectable for scenario tests.
    pub mounts_path: PathBuf,
    /// Shared maintenance lock path.
    pub storage_lock_path: PathBuf,
}

impl Default for ObserverOptions {
    fn default() -> Self {
        Self {
            config_path: PathBuf::new(),
            start_delay_seconds: 180.0,
            interval_seconds: 30.0,
            cooldown_seconds: 900.0,
            mounts_path: PathBuf::from(DEFAULT_MOUNTS_PATH),
            storage_lock_path: PathBuf::from(DEFAULT_STORAGE_LOCK_PATH),
        }
    }
}

/// Validate that the observer can parse its main configuration.
///
/// # Errors
///
/// Returns an error when the configuration cannot be read or parsed.
pub fn validate_config(path: &Path) -> Result<()> {
    let _config = ObserverConfig::load(path)?;
    Ok(())
}

/// Run the observer forever under an external process supervisor.
///
/// # Errors
///
/// Returns an error when a scheduled observation or incident write fails.
pub fn run(options: &ObserverOptions) -> Result<()> {
    thread::sleep(seconds_duration(options.start_delay_seconds.max(0.0)));
    let mut last_incident_at = None;
    loop {
        last_incident_at = observer_iteration(options, last_incident_at)?;
        thread::sleep(seconds_duration(options.interval_seconds.max(1.0)));
    }
}

/// Execute one complete observer iteration.
///
/// # Errors
///
/// Returns an error when configuration collection or incident persistence
/// fails. Missing removable storage is a successful no-op.
pub fn observer_iteration(
    options: &ObserverOptions,
    last_incident_at: Option<Instant>,
) -> Result<Option<Instant>> {
    observer_iteration_with(
        options,
        last_incident_at,
        Instant::now(),
        |path| {
            let config = ObserverConfig::load(path)?;
            let snapshot = ForensicSnapshot::collect(&config);
            Ok((config, snapshot))
        },
        |config, snapshot, reasons, mounts, lock| {
            write_incident_with_lease(config, snapshot, reasons, mounts, lock)
        },
    )
}

fn observer_iteration_with<C, W>(
    options: &ObserverOptions,
    last_incident_at: Option<Instant>,
    now: Instant,
    collect: C,
    write: W,
) -> Result<Option<Instant>>
where
    C: FnOnce(&Path) -> Result<(ObserverConfig, ForensicSnapshot)>,
    W: FnOnce(&ObserverConfig, &ForensicSnapshot, &[String], &Path, &Path) -> Result<bool>,
{
    let initial_mounts = read_mounts(&options.mounts_path);
    if mounted_storage_candidates(&initial_mounts).is_empty() {
        return Ok(last_incident_at);
    }
    let (config, snapshot) = collect(&options.config_path)?;
    let reasons = snapshot.incident_reasons();
    if !incident_is_due(&reasons, now, last_incident_at, options.cooldown_seconds) {
        return Ok(last_incident_at);
    }
    if write(
        &config,
        &snapshot,
        &reasons,
        &options.mounts_path,
        &options.storage_lock_path,
    )? {
        Ok(Some(now))
    } else {
        Ok(last_incident_at)
    }
}

fn incident_is_due(
    reasons: &[String],
    now: Instant,
    last_incident_at: Option<Instant>,
    cooldown: f64,
) -> bool {
    !reasons.is_empty()
        && last_incident_at.is_none_or(|last| {
            now.checked_duration_since(last).unwrap_or_default() >= seconds_duration(cooldown)
        })
}

fn seconds_duration(seconds: f64) -> Duration {
    if !seconds.is_finite() || seconds <= 0.0 {
        return Duration::ZERO;
    }
    Duration::from_secs_f64(seconds.min(Duration::MAX.as_secs_f64()))
}

#[cfg(test)]
mod tests {
    use super::{ObserverOptions, incident_is_due, observer_iteration_with, seconds_duration};
    use crate::command::CommandPayload;
    use crate::config::ObserverConfig;
    use crate::ini::IniDocument;
    use crate::snapshot::ForensicSnapshot;
    use std::cell::Cell;
    use std::fs;
    use std::path::PathBuf;
    use std::time::{Duration, Instant};
    use tempfile::tempdir;

    #[test]
    fn cooldown_requires_reasons_and_elapsed_time() {
        let now = Instant::now();
        let recent = now.checked_sub(Duration::from_secs(89));
        let elapsed = now.checked_sub(Duration::from_secs(90));
        assert!(!incident_is_due(&[], now, None, 90.0));
        assert!(incident_is_due(&["reason".to_owned()], now, None, 90.0));
        assert!(recent.is_some());
        assert!(elapsed.is_some());
        assert!(!incident_is_due(&["reason".to_owned()], now, recent, 90.0));
        assert!(incident_is_due(&["reason".to_owned()], now, elapsed, 90.0));
    }

    #[test]
    fn sleep_durations_are_total_for_untrusted_values() {
        assert_eq!(seconds_duration(-1.0), Duration::ZERO);
        assert_eq!(seconds_duration(f64::NAN), Duration::ZERO);
        assert_eq!(seconds_duration(1.5), Duration::from_millis(1_500));
    }

    #[test]
    fn iteration_obeys_mount_precheck_cooldown_and_write_outcome() {
        let directory = tempdir();
        assert!(directory.is_ok());
        let Some(directory) = directory.ok() else {
            return;
        };
        let mounts_path = directory.path().join("mounts");
        let config_path = directory.path().join("config.ini");
        assert!(fs::write(&config_path, "[DEFAULT]\n").is_ok());
        assert!(fs::write(&mounts_path, "/dev/sda1 /mnt/test ext4 rw 0 0\n").is_ok());
        let options = ObserverOptions {
            config_path,
            cooldown_seconds: 90.0,
            mounts_path,
            storage_lock_path: directory.path().join("storage.lock"),
            ..ObserverOptions::default()
        };
        let writes = Cell::new(0_u32);
        let now = Instant::now();
        let outcome = observer_iteration_with(
            &options,
            None,
            now,
            |path| scenario_snapshot(path, 100.0),
            |_config, _snapshot, reasons, _mounts, _lock| {
                writes.set(writes.get() + 1);
                assert_eq!(reasons, &["log-marker-noreply".to_owned()]);
                Ok(true)
            },
        );
        assert_eq!(outcome, Ok(Some(now)));
        assert_eq!(writes.get(), 1);

        let cooling_down = observer_iteration_with(
            &options,
            Some(now),
            now + Duration::from_secs(89),
            |path| scenario_snapshot(path, 99.0),
            |_config, _snapshot, _reasons, _mounts, _lock| {
                writes.set(writes.get() + 1);
                Ok(true)
            },
        );
        assert_eq!(cooling_down, Ok(Some(now)));
        assert_eq!(writes.get(), 1);

        assert!(fs::write(&options.mounts_path, "").is_ok());
        let collected = Cell::new(false);
        let no_storage = observer_iteration_with(
            &options,
            Some(now),
            now + Duration::from_secs(90),
            |_path| {
                collected.set(true);
                scenario_snapshot(PathBuf::from("unused").as_path(), 100.0)
            },
            |_config, _snapshot, _reasons, _mounts, _lock| Ok(true),
        );
        assert_eq!(no_storage, Ok(Some(now)));
        assert!(!collected.get());
    }

    fn scenario_snapshot(
        path: &std::path::Path,
        timestamp: f64,
    ) -> crate::Result<(ObserverConfig, ForensicSnapshot)> {
        let text = fs::read_to_string(path).unwrap_or_else(|_| "[DEFAULT]\n".to_owned());
        let config = ObserverConfig {
            path: path.to_path_buf(),
            source_text: text.clone(),
            ini: IniDocument::parse(&text)?,
        };
        let mut snapshot = ForensicSnapshot::collect(&config);
        snapshot.timestamp = timestamp;
        snapshot.trace_markers = vec!["NoReply".to_owned()];
        snapshot.svstat = CommandPayload::Completed {
            ok: true,
            returncode: 0,
            stdout: "service up".to_owned(),
            stderr: String::new(),
        };
        Ok((config, snapshot))
    }
}
