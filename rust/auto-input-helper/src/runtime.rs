//! Threadless scheduler for gateway snapshots, refresh requests, and liveness.

use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::Read;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use rustix::time::{ClockId, clock_gettime};
use serde_json::{Value, json};

use crate::config::HelperConfig;
use crate::error::{HelperError, Result};
use crate::external::ConfiguredEnergySources;
use crate::snapshot::{DueSources, SnapshotState, SnapshotWriter};
use crate::storage::write_atomic;
use crate::wire::{EnergyInputs, load_energy_inputs};

const MAX_TOPOLOGY_BYTES: u64 = 1_048_576;
const PARENT_CHECK_SECONDS: f64 = 1.0;
const MINIMUM_SLEEP_SECONDS: f64 = 0.02;
const MAXIMUM_SLEEP_SECONDS: f64 = 0.25;
const WARNING_INTERVAL_SECONDS: f64 = 60.0;

/// Stable process identity supplied by the core supervisor.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RuntimeIdentity {
    pub parent_pid: Option<u32>,
    pub helper_generation: u64,
    pub runtime_instance_id: String,
}

impl RuntimeIdentity {
    /// Construct one validated runtime identity.
    #[must_use]
    pub const fn new(
        parent_pid: Option<u32>,
        helper_generation: u64,
        runtime_instance_id: String,
    ) -> Self {
        Self {
            parent_pid,
            helper_generation,
            runtime_instance_id,
        }
    }
}

/// Run the native helper until its parent exits or the process is terminated.
///
/// # Errors
///
/// Returns an error for unsupported configuration, invalid clocks, or a
/// snapshot that cannot be published during startup.
pub fn run(config: &HelperConfig, identity: RuntimeIdentity) -> Result<()> {
    let mut clock = BootClock::new()?;
    let (epoch, monotonic) = clock.now()?;
    let mut runtime = HelperRuntime::new(config, identity, epoch, monotonic);
    runtime.publish_lifecycle("starting", epoch, monotonic)?;
    runtime.publish_lifecycle("initializing", epoch, monotonic)?;
    runtime.refresh(DueSources::all(), epoch, monotonic, true)?;

    loop {
        let (current_epoch, current_monotonic) = clock.now()?;
        if runtime.parent_check_due(current_monotonic) {
            if !parent_is_current(runtime.identity.parent_pid)? {
                return Ok(());
            }
            runtime.advance_parent_check(current_monotonic);
        }
        runtime.tick(current_epoch, current_monotonic);
        thread::sleep(Duration::from_secs_f64(
            runtime.sleep_seconds(current_monotonic),
        ));
    }
}

/// Execute one complete collection without entering the scheduler loop.
///
/// # Errors
///
/// Returns an error for unsupported configuration, invalid clocks, or failed
/// snapshot publication.
pub fn run_once(config: &HelperConfig, identity: RuntimeIdentity) -> Result<()> {
    let mut clock = BootClock::new()?;
    let (epoch, monotonic) = clock.now()?;
    let mut runtime = HelperRuntime::new(config, identity, epoch, monotonic);
    runtime.publish_lifecycle("initializing", epoch, monotonic)?;
    runtime.refresh(DueSources::all(), epoch, monotonic, true)
}

struct HelperRuntime<'a> {
    config: &'a HelperConfig,
    identity: RuntimeIdentity,
    state: SnapshotState,
    writer: SnapshotWriter,
    mailbox: RefreshMailbox,
    schedule: Schedule,
    warning_after: BTreeMap<&'static str, f64>,
    external: ConfiguredEnergySources,
}

impl<'a> HelperRuntime<'a> {
    fn new(
        config: &'a HelperConfig,
        identity: RuntimeIdentity,
        _epoch: f64,
        monotonic: f64,
    ) -> Self {
        Self {
            config,
            state: SnapshotState::new(identity.clone(), config),
            writer: SnapshotWriter::new(config.snapshot_path.clone()),
            mailbox: RefreshMailbox::new(config.command_dir.clone()),
            schedule: Schedule::new(config, monotonic),
            identity,
            warning_after: BTreeMap::new(),
            external: ConfiguredEnergySources::new(config),
        }
    }

    fn publish_lifecycle(&mut self, state: &str, epoch: f64, monotonic: f64) -> Result<()> {
        self.state.lifecycle(state, epoch, monotonic);
        self.writer.write(self.state.payload()).map(|_written| ())
    }

    fn refresh(
        &mut self,
        due: DueSources,
        epoch: f64,
        monotonic: f64,
        initial: bool,
    ) -> Result<()> {
        let inputs = load_energy_inputs(
            &self.config.energy_inputs_path,
            monotonic,
            self.config.gateway_max_age_seconds,
        );
        let decoded = inputs.as_ref().ok();
        if inputs.is_err() {
            self.warn(
                "energy-input",
                monotonic,
                "semantic energy snapshot unavailable",
            );
        }
        let external_cycle = if self.external.enabled() {
            let gateway_measurements = crate::external::GatewayBatteryMeasurements::from_snapshot(
                decoded,
                monotonic,
                self.config.gateway_max_age_seconds,
            );
            Some(
                self.external
                    .collect_cycle(gateway_measurements, epoch, monotonic),
            )
        } else {
            None
        };
        self.state.apply(
            decoded,
            due,
            epoch,
            monotonic,
            self.config.gateway_max_age_seconds,
            external_cycle.as_ref(),
        );
        if initial && decoded.is_none() {
            self.request_refresh(
                "all",
                true,
                self.config.gateway_max_age_seconds,
                "initial semantic energy snapshot",
                epoch,
                monotonic,
            );
        }
        self.request_missing(decoded, due, epoch, monotonic);
        if initial && !topology_available(&self.config.energy_topology_path) {
            self.request_refresh(
                "topology",
                false,
                self.config.topology_refresh_seconds,
                "initial semantic energy topology",
                epoch,
                monotonic,
            );
        }
        self.writer.write(self.state.payload()).map(|_written| ())
    }

    fn request_missing(
        &mut self,
        inputs: Option<&EnergyInputs>,
        due: DueSources,
        epoch: f64,
        monotonic: f64,
    ) {
        let maximum_age = self.config.gateway_max_age_seconds;
        if due.pv && !inputs.is_some_and(|value| value.pv_power_w.usable(monotonic, maximum_age)) {
            self.request_refresh(
                "pv",
                true,
                maximum_age,
                "semantic pv measurement unavailable",
                epoch,
                monotonic,
            );
        }
        if due.grid
            && !inputs.is_some_and(|value| value.grid_power_w.usable(monotonic, maximum_age))
        {
            self.request_refresh(
                "grid",
                true,
                maximum_age,
                "semantic grid measurement unavailable",
                epoch,
                monotonic,
            );
        }
        let valid_battery = inputs.is_some_and(|value| {
            value.battery_soc.usable(monotonic, maximum_age)
                && value
                    .battery_soc
                    .value
                    .is_some_and(|soc| (0.0..=100.0).contains(&soc))
        });
        if due.battery && !valid_battery {
            self.request_refresh(
                "battery",
                true,
                maximum_age,
                "semantic battery measurement unavailable",
                epoch,
                monotonic,
            );
        }
    }

    fn request_refresh(
        &mut self,
        scope: &'static str,
        priority: bool,
        maximum_age: f64,
        reason: &'static str,
        epoch: f64,
        monotonic: f64,
    ) {
        if !self
            .mailbox
            .request_allowed(scope, monotonic, self.config.gateway_error_retry_seconds)
        {
            return;
        }
        if self
            .mailbox
            .enqueue(scope, priority, maximum_age, reason, epoch)
            .is_err()
        {
            self.warn(
                "gateway-command",
                monotonic,
                "gateway refresh request unavailable",
            );
        }
    }

    fn tick(&mut self, epoch: f64, monotonic: f64) {
        if self.schedule.poll_due(monotonic) {
            let due = self.schedule.due_sources(monotonic);
            if due.any() {
                if self.refresh(due, epoch, monotonic, false).is_err() {
                    self.warn(
                        "snapshot-write",
                        monotonic,
                        "auto-input snapshot write failed",
                    );
                }
                self.schedule.advance_sources(due, monotonic, self.config);
            }
            self.schedule
                .advance_poll(monotonic, self.config.loop_seconds);
        }
        if self.schedule.validation_due(monotonic) {
            if self
                .refresh(DueSources::all(), epoch, monotonic, false)
                .is_err()
            {
                self.warn(
                    "snapshot-write",
                    monotonic,
                    "auto-input validation write failed",
                );
            }
            self.schedule
                .advance_validation(monotonic, self.config.validation_poll_seconds);
        }
        if self.schedule.topology_due(monotonic) {
            self.request_refresh(
                "topology",
                false,
                self.config.topology_refresh_seconds,
                "periodic semantic topology refresh",
                epoch,
                monotonic,
            );
            self.schedule
                .advance_topology(monotonic, self.config.topology_refresh_seconds);
        }
        if self.schedule.heartbeat_due(monotonic) {
            self.state.heartbeat(epoch, monotonic);
            if self.writer.write(self.state.payload()).is_err() {
                self.warn(
                    "snapshot-write",
                    monotonic,
                    "auto-input heartbeat write failed",
                );
            }
            self.schedule
                .advance_heartbeat(monotonic, heartbeat_seconds(self.config.loop_seconds));
        }
    }

    fn parent_check_due(&self, monotonic: f64) -> bool {
        monotonic >= self.schedule.parent_deadline
    }

    fn advance_parent_check(&mut self, monotonic: f64) {
        self.schedule.parent_deadline = monotonic + PARENT_CHECK_SECONDS;
    }

    fn sleep_seconds(&self, monotonic: f64) -> f64 {
        let deadline = self.schedule.next_deadline();
        (deadline - monotonic).clamp(MINIMUM_SLEEP_SECONDS, MAXIMUM_SLEEP_SECONDS)
    }

    fn warn(&mut self, key: &'static str, monotonic: f64, message: &str) {
        if monotonic < self.warning_after.get(key).copied().unwrap_or(0.0) {
            return;
        }
        eprintln!("venus-evcharger-auto-input-helper: {message}");
        self.warning_after
            .insert(key, monotonic + WARNING_INTERVAL_SECONDS);
    }
}

#[derive(Clone, Debug)]
struct Schedule {
    poll_deadline: f64,
    pv_due: f64,
    grid_due: f64,
    battery_due: f64,
    validation_deadline: f64,
    topology_deadline: f64,
    heartbeat_deadline: f64,
    parent_deadline: f64,
}

impl Schedule {
    fn new(config: &HelperConfig, monotonic: f64) -> Self {
        Self {
            poll_deadline: monotonic + config.loop_seconds,
            pv_due: 0.0,
            grid_due: 0.0,
            battery_due: 0.0,
            validation_deadline: monotonic + config.validation_poll_seconds,
            topology_deadline: monotonic + config.topology_refresh_seconds,
            heartbeat_deadline: monotonic + heartbeat_seconds(config.loop_seconds),
            parent_deadline: monotonic + PARENT_CHECK_SECONDS,
        }
    }

    fn poll_due(&self, monotonic: f64) -> bool {
        monotonic >= self.poll_deadline
    }

    fn validation_due(&self, monotonic: f64) -> bool {
        monotonic >= self.validation_deadline
    }

    fn topology_due(&self, monotonic: f64) -> bool {
        monotonic >= self.topology_deadline
    }

    fn heartbeat_due(&self, monotonic: f64) -> bool {
        monotonic >= self.heartbeat_deadline
    }

    fn due_sources(&self, monotonic: f64) -> DueSources {
        DueSources {
            pv: monotonic >= self.pv_due,
            grid: monotonic >= self.grid_due,
            battery: monotonic >= self.battery_due,
        }
    }

    fn advance_sources(&mut self, due: DueSources, monotonic: f64, config: &HelperConfig) {
        if due.pv {
            self.pv_due = monotonic + config.pv_poll_seconds;
        }
        if due.grid {
            self.grid_due = monotonic + config.grid_poll_seconds;
        }
        if due.battery {
            self.battery_due = monotonic + config.battery_poll_seconds;
        }
    }

    fn advance_poll(&mut self, monotonic: f64, interval: f64) {
        self.poll_deadline = monotonic + interval;
    }

    fn advance_validation(&mut self, monotonic: f64, interval: f64) {
        self.validation_deadline = monotonic + interval;
    }

    fn advance_topology(&mut self, monotonic: f64, interval: f64) {
        self.topology_deadline = monotonic + interval;
    }

    fn advance_heartbeat(&mut self, monotonic: f64, interval: f64) {
        self.heartbeat_deadline = monotonic + interval;
    }

    fn next_deadline(&self) -> f64 {
        [
            self.poll_deadline,
            self.validation_deadline,
            self.topology_deadline,
            self.heartbeat_deadline,
            self.parent_deadline,
        ]
        .into_iter()
        .fold(f64::INFINITY, f64::min)
    }
}

const fn heartbeat_seconds(loop_seconds: f64) -> f64 {
    if loop_seconds < 0.5 {
        0.5
    } else if loop_seconds > 2.0 {
        2.0
    } else {
        loop_seconds
    }
}

struct RefreshMailbox {
    command_dir: std::path::PathBuf,
    request_after: BTreeMap<&'static str, f64>,
    sequence: u64,
}

impl RefreshMailbox {
    const fn new(command_dir: std::path::PathBuf) -> Self {
        Self {
            command_dir,
            request_after: BTreeMap::new(),
            sequence: 0,
        }
    }

    fn request_allowed(&mut self, scope: &'static str, monotonic: f64, retry_seconds: f64) -> bool {
        if monotonic < self.request_after.get(scope).copied().unwrap_or(0.0) {
            return false;
        }
        self.request_after.insert(scope, monotonic + retry_seconds);
        true
    }

    fn enqueue(
        &mut self,
        scope: &'static str,
        priority: bool,
        maximum_age: f64,
        reason: &'static str,
        epoch: f64,
    ) -> Result<()> {
        ensure_command_directory(&self.command_dir)?;
        self.sequence = self.sequence.saturating_add(1);
        let token = command_token(self.sequence)?;
        let command_id = format!("cmd-{token}");
        let urgency = if priority { "priority" } else { "normal" };
        let queue_class = if scope == "topology" {
            "discovery"
        } else {
            "read-fast"
        };
        let payload = json!({
            "schema_version": 1,
            "id": command_id,
            "created_at": epoch,
            "mailbox_revision": token,
            "queue_class": queue_class,
            "lifecycle_state": "queued",
            "kind": "refresh_energy_inputs",
            "request_id": token,
            "scope": scope,
            "source_id": Value::Null,
            "deadline_s": maximum_age,
            "urgency": urgency,
            "reason": reason,
            "source": "auto-input-helper",
            "priority": if priority { "read" } else { "discovery" },
            "coalesce_key": format!("energy-refresh:{scope}:all"),
        });
        let mut serialized = serde_json::to_vec(&payload)?;
        serialized.push(b'\n');
        let path = self.command_dir.join(format!("{command_id}.json"));
        write_atomic(&path, &serialized, 0o600, "gateway command")
    }
}

fn ensure_command_directory(path: &Path) -> Result<()> {
    let existed = path.exists();
    fs::create_dir_all(path)
        .map_err(|error| HelperError::storage("create gateway command directory", &error))?;
    if !existed {
        fs::set_permissions(path, fs::Permissions::from_mode(0o700)).map_err(|error| {
            HelperError::storage("set gateway command directory permissions", &error)
        })?;
    }
    Ok(())
}

fn command_token(sequence: u64) -> Result<String> {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| HelperError::Runtime(format!("system clock precedes epoch: {error}")))?
        .as_nanos();
    Ok(format!("{}-{nanos}-{sequence}", std::process::id()))
}

fn topology_available(path: &Path) -> bool {
    let Ok(file) = OpenOptions::new().read(true).open(path) else {
        return false;
    };
    let Ok(metadata) = file.metadata() else {
        return false;
    };
    if metadata.len() == 0 || metadata.len() > MAX_TOPOLOGY_BYTES {
        return false;
    }
    let mut bytes = Vec::with_capacity(usize::try_from(metadata.len()).unwrap_or(0));
    if file
        .take(MAX_TOPOLOGY_BYTES + 1)
        .read_to_end(&mut bytes)
        .is_err()
        || u64::try_from(bytes.len()).unwrap_or(u64::MAX) > MAX_TOPOLOGY_BYTES
    {
        return false;
    }
    serde_json::from_slice::<Value>(&bytes).is_ok_and(|payload| {
        payload.get("schema_version").and_then(Value::as_u64) == Some(1)
            && payload.get("sources").is_some_and(Value::is_array)
    })
}

struct BootClock {
    last_monotonic: f64,
}

impl BootClock {
    fn new() -> Result<Self> {
        let monotonic = monotonic_seconds()?;
        if !monotonic.is_finite() || monotonic < 0.0 {
            return Err(HelperError::Runtime(
                "monotonic clock is not finite and non-negative".to_owned(),
            ));
        }
        Ok(Self {
            last_monotonic: monotonic,
        })
    }

    fn now(&mut self) -> Result<(f64, f64)> {
        let epoch = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|error| HelperError::Runtime(format!("system clock precedes epoch: {error}")))?
            .as_secs_f64();
        let monotonic = monotonic_seconds()?;
        if monotonic < self.last_monotonic {
            return Err(HelperError::Runtime(
                "monotonic clock moved backwards".to_owned(),
            ));
        }
        self.last_monotonic = monotonic;
        Ok((epoch, monotonic))
    }
}

fn monotonic_seconds() -> Result<f64> {
    let timestamp = clock_gettime(ClockId::Monotonic);
    Duration::try_from(timestamp)
        .map(|duration| duration.as_secs_f64())
        .map_err(|error| HelperError::Runtime(format!("monotonic clock is invalid: {error}")))
}

fn parent_is_current(expected: Option<u32>) -> Result<bool> {
    let Some(expected) = expected else {
        return Ok(true);
    };
    let status = fs::read_to_string("/proc/self/status")
        .map_err(|error| HelperError::input("read parent process", &error))?;
    let parent = status
        .lines()
        .find_map(|line| line.strip_prefix("PPid:"))
        .map(str::trim)
        .ok_or_else(|| HelperError::Runtime("parent process id is unavailable".to_owned()))?
        .parse::<u32>()
        .map_err(|error| HelperError::Runtime(format!("parent process id is invalid: {error}")))?;
    Ok(parent == expected)
}

#[cfg(test)]
mod tests {
    use super::{RefreshMailbox, Schedule, heartbeat_seconds, topology_available};
    use crate::config::{GridFusionConfig, HelperConfig};
    use crate::energy::{ExternalPollingPolicy, PvProjectionPolicy};
    use std::fs;
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
            grid_poll_seconds: 3.0,
            battery_poll_seconds: 10.0,
            loop_seconds: 1.0,
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

    #[test]
    fn scheduler_preserves_independent_source_intervals() {
        let config = config();
        let mut schedule = Schedule::new(&config, 100.0);
        let first = schedule.due_sources(101.0);
        assert!(first.pv && first.grid && first.battery);
        schedule.advance_sources(first, 101.0, &config);
        let second = schedule.due_sources(103.0);
        assert!(second.pv);
        assert!(!second.grid);
        assert!(!second.battery);
        let third = schedule.due_sources(104.0);
        assert!(third.pv && third.grid);
        assert!(!third.battery);
    }

    #[test]
    fn heartbeat_matches_the_python_half_to_two_second_bound() {
        assert!((heartbeat_seconds(0.2) - 0.5).abs() < f64::EPSILON);
        assert!((heartbeat_seconds(1.0) - 1.0).abs() < f64::EPSILON);
        assert!((heartbeat_seconds(10.0) - 2.0).abs() < f64::EPSILON);
    }

    #[test]
    fn topology_probe_is_bounded_and_schema_aware() -> Result<(), Box<dyn std::error::Error>> {
        let directory = tempfile::tempdir()?;
        let path = directory.path().join("topology.json");
        fs::write(&path, br#"{"schema_version":1,"sources":[]}"#)?;
        assert!(topology_available(&path));
        fs::write(&path, br#"{"schema_version":2,"sources":[]}"#)?;
        assert!(!topology_available(&path));
        Ok(())
    }

    #[test]
    fn refresh_commands_use_the_gateway_deadline_contract() -> Result<(), Box<dyn std::error::Error>>
    {
        let directory = tempfile::tempdir()?;
        let mut mailbox = RefreshMailbox::new(directory.path().to_path_buf());
        mailbox.enqueue("pv", true, 10.0, "test refresh", 100.0)?;
        let path = fs::read_dir(directory.path())?
            .next()
            .ok_or("missing command")??
            .path();
        let payload: serde_json::Value = serde_json::from_slice(&fs::read(path)?)?;

        assert_eq!(payload["deadline_s"], 10.0);
        assert!(payload.get("max_age_seconds").is_none());
        Ok(())
    }
}
