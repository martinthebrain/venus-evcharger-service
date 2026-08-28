//! Observer scheduling and incident-episode policy.

use std::path::{Path, PathBuf};
use std::thread;
use std::time::{Duration, Instant};

use crate::artifact::{
    DEFAULT_MOUNTS_PATH, DEFAULT_STORAGE_LOCK_PATH, mounted_storage_candidates, read_mounts,
    write_incident_with_lease, write_recovery_with_lease,
};
use crate::config::ObserverConfig;
use crate::error::Result;
use crate::snapshot::ForensicSnapshot;
use crate::system_activity::SystemActivitySampler;

/// Runtime options accepted by the observer entrypoint.
#[derive(Clone, Debug, PartialEq)]
pub struct ObserverOptions {
    /// Main EV-charger configuration.
    pub config_path: PathBuf,
    /// Initial delay before any IO.
    pub start_delay_seconds: f64,
    /// Delay between observations, clamped to at least one second.
    pub interval_seconds: f64,
    /// Uninterrupted healthy duration required to close an incident episode.
    pub recovery_confirmation_seconds: f64,
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
            recovery_confirmation_seconds: 60.0,
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
    let mut activity_sampler = SystemActivitySampler::start();
    thread::sleep(seconds_duration(options.start_delay_seconds.max(0.0)));
    let mut state = ObserverState::default();
    loop {
        observer_iteration(options, &mut state, &mut activity_sampler)?;
        thread::sleep(seconds_duration(options.interval_seconds.max(1.0)));
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
struct ObserverState {
    active_episode: Option<IncidentEpisode>,
}

#[derive(Clone, Debug, PartialEq)]
struct IncidentEpisode {
    path: PathBuf,
    started_at: Instant,
    started_timestamp: f64,
    initial_reasons: Vec<String>,
    healthy_since: Option<Instant>,
}

fn observer_iteration(
    options: &ObserverOptions,
    state: &mut ObserverState,
    activity_sampler: &mut SystemActivitySampler,
) -> Result<()> {
    let system_activity = activity_sampler.observe();
    observer_iteration_with(
        options,
        state,
        Instant::now(),
        |path| {
            let config = ObserverConfig::load(path)?;
            let snapshot = ForensicSnapshot::collect_with_system_activity(&config, system_activity);
            Ok((config, snapshot))
        },
        |config, snapshot, reasons, mounts, lock| {
            write_incident_with_lease(config, snapshot, reasons, mounts, lock)
        },
        |episode, snapshot, duration, mounts, lock| {
            write_recovery_with_lease(
                &episode.path,
                episode.started_timestamp,
                &episode.initial_reasons,
                snapshot,
                duration,
                mounts,
                lock,
            )
        },
    )
}

fn observer_iteration_with<C, W, R>(
    options: &ObserverOptions,
    state: &mut ObserverState,
    now: Instant,
    collect: C,
    write_incident: W,
    write_recovery: R,
) -> Result<()>
where
    C: FnOnce(&Path) -> Result<(ObserverConfig, ForensicSnapshot)>,
    W: FnOnce(
        &ObserverConfig,
        &ForensicSnapshot,
        &[String],
        &Path,
        &Path,
    ) -> Result<Option<PathBuf>>,
    R: FnOnce(&IncidentEpisode, &ForensicSnapshot, Duration, &Path, &Path) -> Result<bool>,
{
    let initial_mounts = read_mounts(&options.mounts_path);
    if mounted_storage_candidates(&initial_mounts).is_empty() {
        return Ok(());
    }
    let (config, snapshot) = collect(&options.config_path)?;
    let reasons = snapshot.incident_reasons();
    if !reasons.is_empty() {
        if let Some(episode) = state.active_episode.as_mut() {
            episode.healthy_since = None;
            return Ok(());
        }
        if let Some(path) = write_incident(
            &config,
            &snapshot,
            &reasons,
            &options.mounts_path,
            &options.storage_lock_path,
        )? {
            state.active_episode = Some(IncidentEpisode {
                path,
                started_at: now,
                started_timestamp: snapshot.timestamp,
                initial_reasons: reasons,
                healthy_since: None,
            });
        }
        return Ok(());
    }

    let Some(episode) = state.active_episode.as_mut() else {
        return Ok(());
    };
    let healthy_since = *episode.healthy_since.get_or_insert(now);
    let healthy_duration = now
        .checked_duration_since(healthy_since)
        .unwrap_or_default();
    if healthy_duration < seconds_duration(options.recovery_confirmation_seconds.max(0.0)) {
        return Ok(());
    }
    let episode_duration = now
        .checked_duration_since(episode.started_at)
        .unwrap_or_default();
    if write_recovery(
        episode,
        &snapshot,
        episode_duration,
        &options.mounts_path,
        &options.storage_lock_path,
    )? {
        state.active_episode = None;
    }
    Ok(())
}

fn seconds_duration(seconds: f64) -> Duration {
    if !seconds.is_finite() || seconds <= 0.0 {
        return Duration::ZERO;
    }
    Duration::from_secs_f64(seconds.min(Duration::MAX.as_secs_f64()))
}

#[cfg(test)]
mod tests {
    use super::{ObserverOptions, ObserverState, observer_iteration_with, seconds_duration};
    use crate::command::CommandPayload;
    use crate::config::ObserverConfig;
    use crate::ini::IniDocument;
    use crate::snapshot::ForensicSnapshot;
    use std::cell::Cell;
    use std::fs;
    use std::path::PathBuf;
    use std::time::{Duration, Instant};
    use tempfile::{TempDir, tempdir};

    #[test]
    fn sleep_durations_are_total_for_untrusted_values() {
        assert_eq!(seconds_duration(-1.0), Duration::ZERO);
        assert_eq!(seconds_duration(f64::NAN), Duration::ZERO);
        assert_eq!(seconds_duration(1.5), Duration::from_millis(1_500));
    }

    #[test]
    fn incident_episode_is_written_once_and_closed_after_confirmed_recovery() {
        let setup = scenario_options();
        assert!(setup.is_ok());
        let Some((directory, options)) = setup.ok() else {
            return;
        };
        let writes = Cell::new(0_u32);
        let recoveries = Cell::new(0_u32);
        let mut state = ObserverState::default();
        let now = Instant::now();
        let incident_path = directory.path().join("incident-1");
        let outcome = observer_iteration_with(
            &options,
            &mut state,
            now,
            |path| scenario_snapshot(path, 100.0),
            |_config, _snapshot, reasons, _mounts, _lock| {
                writes.set(writes.get() + 1);
                assert_eq!(reasons, &["log-marker-noreply".to_owned()]);
                Ok(Some(incident_path.clone()))
            },
            |_episode, _snapshot, _duration, _mounts, _lock| Ok(false),
        );
        assert_eq!(outcome, Ok(()));
        assert_eq!(writes.get(), 1);
        assert!(state.active_episode.is_some());

        let still_failing = observer_iteration_with(
            &options,
            &mut state,
            now + Duration::from_secs(3_600),
            |path| scenario_snapshot(path, 101.0),
            |_config, _snapshot, _reasons, _mounts, _lock| {
                writes.set(writes.get() + 1);
                Ok(Some(PathBuf::from("unexpected")))
            },
            |_episode, _snapshot, _duration, _mounts, _lock| Ok(false),
        );
        assert_eq!(still_failing, Ok(()));
        assert_eq!(writes.get(), 1);

        let recovery_candidate = observer_iteration_with(
            &options,
            &mut state,
            now + Duration::from_secs(3_630),
            |path| healthy_scenario_snapshot(path, 102.0),
            |_config, _snapshot, _reasons, _mounts, _lock| Ok(None),
            |_episode, _snapshot, _duration, _mounts, _lock| Ok(false),
        );
        assert_eq!(recovery_candidate, Ok(()));
        assert!(state.active_episode.is_some());

        let deferred_recovery = observer_iteration_with(
            &options,
            &mut state,
            now + Duration::from_secs(3_690),
            |path| healthy_scenario_snapshot(path, 103.0),
            |_config, _snapshot, _reasons, _mounts, _lock| Ok(None),
            |episode, snapshot, duration, _mounts, _lock| {
                recoveries.set(recoveries.get() + 1);
                assert_eq!(episode.path, incident_path);
                assert_eq!(episode.started_timestamp.to_bits(), 100.0_f64.to_bits());
                assert_eq!(snapshot.timestamp.to_bits(), 103.0_f64.to_bits());
                assert_eq!(duration, Duration::from_secs(3_690));
                Ok(false)
            },
        );
        assert_eq!(deferred_recovery, Ok(()));
        assert_eq!(recoveries.get(), 1);
        assert!(state.active_episode.is_some());

        let recovered = observer_iteration_with(
            &options,
            &mut state,
            now + Duration::from_secs(3_720),
            |path| healthy_scenario_snapshot(path, 104.0),
            |_config, _snapshot, _reasons, _mounts, _lock| Ok(None),
            |_episode, _snapshot, duration, _mounts, _lock| {
                recoveries.set(recoveries.get() + 1);
                assert_eq!(duration, Duration::from_secs(3_720));
                Ok(true)
            },
        );
        assert_eq!(recovered, Ok(()));
        assert_eq!(recoveries.get(), 2);
        assert!(state.active_episode.is_none());

        let next_incident = observer_iteration_with(
            &options,
            &mut state,
            now + Duration::from_secs(3_750),
            |path| scenario_snapshot(path, 105.0),
            |_config, _snapshot, _reasons, _mounts, _lock| {
                writes.set(writes.get() + 1);
                Ok(Some(PathBuf::from("incident-2")))
            },
            |_episode, _snapshot, _duration, _mounts, _lock| Ok(false),
        );
        assert_eq!(next_incident, Ok(()));
        assert_eq!(writes.get(), 2);
        assert!(state.active_episode.is_some());
    }

    #[test]
    fn interrupted_recovery_keeps_one_episode_and_failed_writes_remain_retryable() {
        let setup = scenario_options();
        assert!(setup.is_ok());
        let Some((directory, options)) = setup.ok() else {
            return;
        };
        let now = Instant::now();
        let writes = Cell::new(0_u32);
        let mut state = ObserverState::default();

        assert!(
            observer_iteration_with(
                &options,
                &mut state,
                now,
                |path| scenario_snapshot(path, 100.0),
                |_config, _snapshot, _reasons, _mounts, _lock| {
                    writes.set(writes.get() + 1);
                    Ok(None)
                },
                |_episode, _snapshot, _duration, _mounts, _lock| Ok(false),
            )
            .is_ok()
        );
        assert!(state.active_episode.is_none());

        assert!(
            observer_iteration_with(
                &options,
                &mut state,
                now + Duration::from_secs(30),
                |path| scenario_snapshot(path, 101.0),
                |_config, _snapshot, _reasons, _mounts, _lock| {
                    writes.set(writes.get() + 1);
                    Ok(Some(directory.path().join("incident-2")))
                },
                |_episode, _snapshot, _duration, _mounts, _lock| Ok(false),
            )
            .is_ok()
        );
        assert_eq!(writes.get(), 2);

        assert!(
            observer_iteration_with(
                &options,
                &mut state,
                now + Duration::from_secs(60),
                |path| healthy_scenario_snapshot(path, 102.0),
                |_config, _snapshot, _reasons, _mounts, _lock| Ok(None),
                |_episode, _snapshot, _duration, _mounts, _lock| Ok(false),
            )
            .is_ok()
        );
        assert!(
            observer_iteration_with(
                &options,
                &mut state,
                now + Duration::from_secs(90),
                |path| scenario_snapshot(path, 103.0),
                |_config, _snapshot, _reasons, _mounts, _lock| Ok(None),
                |_episode, _snapshot, _duration, _mounts, _lock| Ok(false),
            )
            .is_ok()
        );
        assert_eq!(
            state
                .active_episode
                .as_ref()
                .and_then(|episode| episode.healthy_since),
            None
        );
    }

    #[test]
    fn unavailable_storage_skips_collection_without_losing_episode_state() {
        let directory = tempdir();
        assert!(directory.is_ok());
        let Some(directory) = directory.ok() else {
            return;
        };
        let options = ObserverOptions {
            mounts_path: directory.path().join("empty-mounts"),
            ..ObserverOptions::default()
        };
        assert!(fs::write(&options.mounts_path, "").is_ok());
        let collected = Cell::new(false);
        let mut state = ObserverState::default();
        let result = observer_iteration_with(
            &options,
            &mut state,
            Instant::now(),
            |_path| {
                collected.set(true);
                scenario_snapshot(PathBuf::from("unused").as_path(), 100.0)
            },
            |_config, _snapshot, _reasons, _mounts, _lock| Ok(None),
            |_episode, _snapshot, _duration, _mounts, _lock| Ok(false),
        );
        assert_eq!(result, Ok(()));
        assert!(!collected.get());
        assert_eq!(state, ObserverState::default());
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

    fn scenario_options() -> std::io::Result<(TempDir, ObserverOptions)> {
        let directory = tempdir()?;
        let mounts_path = directory.path().join("mounts");
        let config_path = directory.path().join("config.ini");
        fs::write(&config_path, "[DEFAULT]\n")?;
        fs::write(&mounts_path, "/dev/sda1 /mnt/test ext4 rw 0 0\n")?;
        let options = ObserverOptions {
            config_path,
            recovery_confirmation_seconds: 60.0,
            mounts_path,
            storage_lock_path: directory.path().join("storage.lock"),
            ..ObserverOptions::default()
        };
        Ok((directory, options))
    }

    fn healthy_scenario_snapshot(
        path: &std::path::Path,
        timestamp: f64,
    ) -> crate::Result<(ObserverConfig, ForensicSnapshot)> {
        let (config, mut snapshot) = scenario_snapshot(path, timestamp)?;
        snapshot.trace_markers.clear();
        Ok((config, snapshot))
    }
}
