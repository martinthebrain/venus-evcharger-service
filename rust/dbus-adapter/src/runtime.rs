// SPDX-License-Identifier: GPL-3.0-or-later
//! Native adapter process loop and RAM-only IPC projections.

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::broker::OperationBroker;
use crate::cache::RuntimeCache;
use crate::commands::{CommandExecutor, CommandOutcome};
use crate::config::{GatewayPaths, IniConfig};
use crate::energy::{Clocks, EnergyInputs, EnergyTopology};
use crate::fast_socket::FastPublicationServer;
use crate::health::{GatewayHealthMonitor, SloObserved, SloSnapshot, SloThresholds, core_observed};
use crate::introspection::IntrospectionManager;
use crate::mailbox::Mailbox;
use crate::publication::{PublicationOutcome, PublicationRegistry};
use crate::reader::EnergyReader;
use crate::resources::{ResourceMonitor, ResourceState};

mod command;
mod operation_cycle;
mod queue;
mod scheduling;
mod storage;

use command::{command_deadline_expired, command_kind, is_publication};
use queue::{PressureState, QueueScheduler, is_advisory};
use scheduling::{
    OperationRates, RuntimeIntervals, TickDemand, configured_duration, configured_size,
};
use storage::prepare_runtime_paths;

const MAILBOX_SCAN_INTERVAL: Duration = Duration::from_millis(200);
const INTROSPECTION_SNAPSHOT_INTERVAL: Duration = Duration::from_secs(10);

#[derive(Clone, Copy, Debug)]
enum BrokerOwner {
    Reader { optional: bool },
    Command,
    Introspection,
}

pub struct AdapterRuntime {
    paths: GatewayPaths,
    gateway_mailbox: Mailbox,
    core_mailbox: Arc<Mailbox>,
    fast_server: FastPublicationServer,
    publication: PublicationRegistry,
    commands: CommandExecutor,
    reader: EnergyReader,
    introspection: IntrospectionManager,
    resources: ResourceMonitor,
    health: GatewayHealthMonitor,
    broker: OperationBroker,
    broker_owner: Option<BrokerOwner>,
    rates: OperationRates,
    intervals: RuntimeIntervals,
    slo_thresholds: SloThresholds,
    queue_scheduler: QueueScheduler,
    adaptive_tick: Duration,
    tick_demand: TickDemand,
    pending_core_commands: usize,
    oldest_core_command_age_seconds: f64,
    health_history_max_bytes: usize,
    command_lifecycle_max_bytes: usize,
    sequence: u64,
    cache_dirty: bool,
    cache: RuntimeCache,
    last_energy: Option<EnergyInputs>,
    topology: EnergyTopology,
    next_mailbox_scan: Instant,
    next_command_retry: Instant,
    next_energy_publish: Instant,
    next_health_publish: Instant,
    next_cache_publish: Instant,
    next_dirty_cache_publish: Instant,
    next_health_history: Instant,
    next_introspection_snapshot: Instant,
    last_tick_at: f64,
    last_tick_monotonic: f64,
    last_tick_duration_ms: f64,
}

impl AdapterRuntime {
    pub fn new(config: &IniConfig, paths: GatewayPaths) -> Result<Self, String> {
        prepare_runtime_paths(&paths)?;
        let core_mailbox = Arc::new(Mailbox::new(paths.core_command_dir.clone()));
        let service_name = evcs_service_name(config);
        let publication =
            PublicationRegistry::new(config.clone(), service_name, core_mailbox.clone())?;
        let clocks = Clocks::now()?;
        let reader = EnergyReader::from_config(config);
        let topology = reader.topology(clocks.epoch)?;
        let now = Instant::now();
        let intervals = RuntimeIntervals::from_config(config);
        let adaptive_tick = intervals.minimum_tick;
        Ok(Self {
            gateway_mailbox: Mailbox::new(paths.command_dir.clone()),
            core_mailbox,
            fast_server: FastPublicationServer::bind(&paths.socket_path)?,
            publication,
            commands: CommandExecutor::from_config(config),
            introspection: IntrospectionManager::from_config(config),
            resources: ResourceMonitor::new(
                configured_duration(config, "DbusGatewayResourceSampleIntervalSeconds", 2.0, 0.2),
                configured_duration(config, "DbusGatewayResourceRecoveryHoldSeconds", 10.0, 0.0),
            ),
            health: GatewayHealthMonitor::new(clocks),
            broker: OperationBroker::start(configured_duration(
                config,
                "DbusGatewayOperationTimeoutSeconds",
                1.0,
                0.05,
            ))?,
            broker_owner: None,
            rates: OperationRates::from_config(config),
            slo_thresholds: SloThresholds::from_config(config),
            queue_scheduler: QueueScheduler::from_config(config),
            adaptive_tick,
            tick_demand: TickDemand::default(),
            pending_core_commands: 0,
            oldest_core_command_age_seconds: 0.0,
            health_history_max_bytes: configured_size(
                config,
                "DbusGatewayHealthLogMaxBytes",
                524_288,
            ),
            command_lifecycle_max_bytes: configured_size(
                config,
                "DbusGatewayCommandLifecycleMaxBytes",
                1_048_576,
            ),
            sequence: 0,
            cache_dirty: true,
            cache: RuntimeCache::default(),
            last_energy: None,
            topology,
            reader,
            intervals,
            next_mailbox_scan: now,
            next_command_retry: now,
            next_energy_publish: now,
            next_health_publish: now,
            next_cache_publish: now,
            next_dirty_cache_publish: now,
            next_health_history: now,
            next_introspection_snapshot: now,
            last_tick_at: 0.0,
            last_tick_monotonic: 0.0,
            last_tick_duration_ms: 0.0,
            paths,
        })
    }

    pub fn run(&mut self, stop: &AtomicBool) -> Result<(), String> {
        while !stop.load(Ordering::Relaxed) {
            let started = Instant::now();
            let clocks = Clocks::now()?;
            let resource_state = self.resources.sample_if_due().state;
            let intended_tick = self.adaptive_tick;
            self.tick(clocks, resource_state)?;
            let duration = started.elapsed();
            self.last_tick_at = clocks.epoch;
            self.last_tick_monotonic = clocks.monotonic;
            self.last_tick_duration_ms = duration.as_secs_f64() * 1_000.0;
            self.health.record_tick(started, duration, intended_tick);
            if let Some(remaining) = intended_tick.checked_sub(duration) {
                thread::sleep(remaining);
            }
        }
        Ok(())
    }

    fn tick(&mut self, clocks: Clocks, resource_state: ResourceState) -> Result<(), String> {
        let pressure = self.pressure_state(resource_state);
        self.queue_scheduler.update_pressure(
            pressure,
            self.intervals.queue_slo_seconds,
            self.health.maximum_callback_lateness_ms(),
            self.intervals.mainloop_slo_ms,
        );
        self.fast_server.accept_ready()?;
        self.process_fast_publications(clocks.epoch, pressure);
        self.poll_broker(clocks);
        self.process_mailbox_if_due(clocks.epoch, pressure)?;
        self.schedule_operation(resource_state)?;
        let topology_changed = self.reader.take_topology_changed();
        if topology_changed {
            self.topology = self.reader.topology(clocks.epoch)?;
            self.topology
                .write_atomic(&self.paths.energy_topology_path)?;
            self.cache_dirty = true;
        }
        self.introspection.schedule_if_due(
            &self.reader,
            topology_changed,
            resource_state,
            clocks.epoch,
        );
        // A completed D-Bus operation may have observed a value after this
        // tick's opening clock sample. Capture the publication boundary only
        // after every operation result has been applied.
        let publication_clocks = Clocks::now()?;
        self.publish_due_snapshots(publication_clocks)?;
        self.update_adaptive_tick(publication_clocks, resource_state);
        Ok(())
    }

    fn process_fast_publications(&mut self, captured_at: f64, pressure: PressureState) {
        let started = Instant::now();
        for _ in 0..self.queue_scheduler.dynamic_local_publish_burst_limit() {
            if started.elapsed() >= self.queue_scheduler.local_publish_tick_budget() {
                break;
            }
            let Some(command) = self.fast_server.pop() else {
                break;
            };
            if command_deadline_expired(&command, captured_at) {
                continue;
            }
            if !QueueScheduler::command_allowed(&command, pressure)
                || !self.queue_scheduler.budget_available(&command)
            {
                self.fast_server.defer(command);
                break;
            }
            self.queue_scheduler.record_attempt(&command);
            match self.publication.apply(&command) {
                Ok(PublicationOutcome::Applied) => {
                    self.cache_dirty = true;
                    self.queue_scheduler.record_processed(captured_at);
                }
                Ok(PublicationOutcome::Dropped) => {
                    self.queue_scheduler.record_processed(captured_at);
                }
                Ok(PublicationOutcome::Deferred) | Err(_) => {
                    self.fast_server.defer(command);
                    break;
                }
            }
        }
    }

    fn process_mailbox_if_due(
        &mut self,
        captured_at: f64,
        pressure: PressureState,
    ) -> Result<(), String> {
        let now = Instant::now();
        if now < self.next_mailbox_scan {
            return Ok(());
        }
        self.next_mailbox_scan = now + MAILBOX_SCAN_INTERVAL;
        let pending = self.gateway_mailbox.pending()?;
        self.queue_scheduler.observe_pending(&pending, captured_at);
        let core_pending = self.core_mailbox.pending()?;
        self.pending_core_commands = core_pending.len();
        self.oldest_core_command_age_seconds = oldest_command_age(&core_pending, captured_at);
        let pending = QueueScheduler::prioritize(pending, captured_at);
        let local_started = Instant::now();
        let mut local_processed = 0_usize;
        for (path, command) in pending {
            if command_deadline_expired(&command, captured_at) {
                self.finish_command(&path, &command, "expired", captured_at)?;
                continue;
            }
            if !QueueScheduler::command_allowed(&command, pressure) {
                if pressure != PressureState::Ok && is_advisory(&command) {
                    self.finish_command(&path, &command, "dropped", captured_at)?;
                }
                continue;
            }
            if !self.queue_scheduler.budget_available(&command) {
                continue;
            }
            let kind = command_kind(&command);
            if is_publication(kind)
                && (local_processed >= self.queue_scheduler.dynamic_local_publish_burst_limit()
                    || local_started.elapsed() >= self.queue_scheduler.local_publish_tick_budget())
            {
                continue;
            }
            if is_publication(kind) {
                self.queue_scheduler.record_attempt(&command);
                match self.publication.apply(&command) {
                    Ok(PublicationOutcome::Applied) => {
                        self.cache_dirty = true;
                        self.finish_command(&path, &command, "applied", captured_at)?;
                        local_processed = local_processed.saturating_add(1);
                    }
                    Ok(PublicationOutcome::Dropped) => {
                        self.finish_command(&path, &command, "dropped", captured_at)?;
                        local_processed = local_processed.saturating_add(1);
                    }
                    Ok(PublicationOutcome::Deferred) | Err(_) => {}
                }
                continue;
            }
            if kind == "refresh_energy_inputs" {
                self.queue_scheduler.record_attempt(&command);
                let scope = command
                    .get("scope")
                    .and_then(Value::as_str)
                    .unwrap_or("all");
                let outcome = if self.reader.request_refresh(scope) {
                    "applied"
                } else {
                    "dropped"
                };
                self.finish_command(&path, &command, outcome, captured_at)?;
                continue;
            }
            if kind == "introspect" {
                self.queue_scheduler.record_attempt(&command);
                let service = command.get("service").and_then(Value::as_str).unwrap_or("");
                let object_path = command.get("path").and_then(Value::as_str).unwrap_or("/");
                if service.is_empty() || !object_path.starts_with('/') {
                    self.finish_command(&path, &command, "dropped", captured_at)?;
                } else {
                    self.introspection
                        .schedule_command(path.clone(), service, object_path);
                }
                continue;
            }
            if self.commands.active() || now < self.next_command_retry {
                continue;
            }
            self.queue_scheduler.record_attempt(&command);
            match self.commands.start(path.clone(), command.clone()) {
                Ok(Some(CommandOutcome::Dropped)) => {
                    self.finish_command(&path, &command, "dropped", captured_at)?;
                }
                Ok(Some(CommandOutcome::Applied)) => {
                    self.finish_command(&path, &command, "applied", captured_at)?;
                }
                Ok(Some(CommandOutcome::Deferred) | None) => {}
                Err(_) => {
                    self.finish_command(&path, &command, "dropped", captured_at)?;
                }
            }
            if self.commands.active() {
                break;
            }
        }
        Ok(())
    }

    fn publish_due_snapshots(&mut self, clocks: Clocks) -> Result<(), String> {
        let now = Instant::now();
        if now >= self.next_energy_publish {
            let snapshot = self.reader.snapshot(clocks);
            snapshot.write_atomic(&self.paths.energy_inputs_path)?;
            self.last_energy = Some(snapshot);
            self.next_energy_publish = now + self.intervals.energy_publish;
            self.cache_dirty = true;
        }
        let resources = self.resources.snapshot().clone();
        let health_due = now >= self.next_health_publish;
        let history_due = self
            .intervals
            .health_history
            .is_some_and(|_| now >= self.next_health_history);
        let dirty_due = self.cache_dirty && now >= self.next_dirty_cache_publish;
        let cache_due = dirty_due || now >= self.next_cache_publish;
        if health_due || history_due || cache_due {
            self.refresh_cache_observations(clocks);
        }
        let freshness = self
            .cache
            .freshness_payload(clocks, self.intervals.stale_after_seconds);
        let effective_gui_age = self
            .slo_thresholds
            .effective_gui_max_age_seconds(self.adaptive_tick.as_secs_f64());
        let gui = self
            .publication
            .gui_freshness(clocks.monotonic, effective_gui_age);
        let (core_age, core_missing, core_nonfresh) = core_observed(&freshness);
        let queue_demand = self.queue_scheduler.demand();
        let slo = SloSnapshot::evaluate(
            self.slo_thresholds,
            self.adaptive_tick.as_secs_f64(),
            SloObserved {
                gui,
                core_read_max_age_seconds: core_age,
                core_read_missing_count: core_missing,
                core_read_nonfresh_count: core_nonfresh,
                queue_oldest_age_seconds: queue_demand
                    .oldest_slo_age_seconds
                    .max(self.oldest_core_command_age_seconds),
                mainloop_max_gap_ms: self.health.maximum_callback_lateness_ms(),
            },
        );
        self.health.observe_slo(
            slo.violated(),
            queue_demand
                .oldest_slo_age_seconds
                .max(self.oldest_core_command_age_seconds),
            self.slo_thresholds.queue_max_age_seconds,
        );
        let health = self.health.snapshot(&resources, clocks);
        let queue_scheduler = self.queue_scheduler.payload();
        if health_due {
            self.write_health_and_diagnostics(
                &health,
                &resources,
                &freshness,
                &slo,
                &queue_scheduler,
                clocks,
            )?;
            self.next_health_publish = now + self.intervals.health_publish;
        }
        if let Some(interval) = self.intervals.health_history
            && history_due
        {
            self.append_health_history(&health, &freshness, clocks.epoch)?;
            self.next_health_history = now + interval;
        }
        if cache_due {
            self.write_cache(&health, &freshness, &slo, &queue_scheduler, clocks)?;
            self.cache_dirty = false;
            self.next_dirty_cache_publish = now + self.intervals.dirty_cache_publish;
            self.next_cache_publish = now + self.intervals.cache_publish;
        }
        if now >= self.next_introspection_snapshot {
            self.introspection
                .write_snapshot(&self.paths.introspection_path, clocks.epoch)?;
            self.next_introspection_snapshot = now + INTROSPECTION_SNAPSHOT_INTERVAL;
        }
        Ok(())
    }
}

fn oldest_command_age(
    commands: &[(std::path::PathBuf, serde_json::Map<String, Value>)],
    captured_at: f64,
) -> f64 {
    commands
        .iter()
        .filter_map(|(_path, command)| {
            command
                .get("updated_at")
                .or_else(|| command.get("created_at"))
                .and_then(Value::as_f64)
                .filter(|value| value.is_finite() && *value > 0.0)
        })
        .map(|at| (captured_at - at).max(0.0))
        .fold(0.0, f64::max)
}

fn evcs_service_name(config: &IniConfig) -> String {
    let configured = config.text("ServiceName", "com.victronenergy.evcharger");
    let base = if configured.trim().is_empty() {
        "com.victronenergy.evcharger"
    } else {
        configured.trim()
    };
    format!(
        "{}.http_{}",
        base.trim_end_matches('.'),
        config.i64("DeviceInstance", 60)
    )
}
