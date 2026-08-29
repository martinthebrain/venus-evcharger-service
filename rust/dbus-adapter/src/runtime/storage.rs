// SPDX-License-Identifier: GPL-3.0-or-later
//! Bounded RAM-backed snapshots and command lifecycle persistence.

use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;

use serde_json::{Map, Value, json};

use super::AdapterRuntime;
use crate::config::GatewayPaths;
use crate::diagnostics::{GatewayDiagnosticsContext, write_gateway_diagnostics};
use crate::energy::{Clocks, EnergyInputs};
use crate::health::{GatewayHealthSnapshot, HealthPayloadContext, SloSnapshot};
use crate::mailbox::{Mailbox, atomic_json};
use crate::resources::ResourceSnapshot;
use crate::runtime::queue::command_queue_class;

impl AdapterRuntime {
    pub(super) fn write_health_and_diagnostics(
        &self,
        health: &GatewayHealthSnapshot,
        resources: &ResourceSnapshot,
        cache_freshness: &Value,
        slo: &SloSnapshot,
        queue_scheduler: &Value,
        clocks: Clocks,
    ) -> Result<(), String> {
        let pending_gateway = self.gateway_mailbox.pending()?.len();
        let pending_core = self.core_mailbox.pending()?.len();
        let (discovery_success, discovery_error, discovery_interval, discovery_next) =
            self.reader.discovery_health();
        let (dormant_evidence, unavailability_reasons) = self.reader.pv_dormancy_health();
        let unavailability_reasons =
            serde_json::to_value(unavailability_reasons).unwrap_or(Value::Null);
        let payload = health.payload(&HealthPayloadContext {
            resources,
            pending_gateway_commands: pending_gateway,
            pending_core_commands: pending_core,
            registered_path_count: self.publication.registered_path_count(),
            service_count: self.reader.service_count(),
            introspection_queue_depth: self.introspection.queue_depth(),
            adaptive_tick_seconds: self.adaptive_tick.as_secs_f64(),
            last_tick_at: self.last_tick_at,
            mainloop_heartbeat_age_s: heartbeat_age(clocks.monotonic, self.last_tick_monotonic),
            last_tick_duration_ms: self.last_tick_duration_ms,
            cache_freshness,
            discovery_last_success_at: discovery_success,
            discovery_last_error: discovery_error,
            discovery_active_interval_s: discovery_interval,
            discovery_next_scan_in_s: discovery_next,
            slo,
            queue_scheduler,
            publication_freshness_deadline_s: self
                .slo_thresholds
                .effective_gui_max_age_seconds(self.adaptive_tick.as_secs_f64()),
            minimum_tick_seconds: self.intervals.minimum_tick.as_secs_f64(),
            maximum_tick_seconds: self.intervals.maximum_tick.as_secs_f64(),
            critical_read_operations: self.tick_demand.critical_read_operations,
            critical_queue_operations: self.tick_demand.critical_queue_operations,
            operation_p95_ms: self.tick_demand.operation_p95_ms,
            dormant_energy_source_evidence: &dormant_evidence,
            energy_source_unavailability_reasons: &unavailability_reasons,
        });
        atomic_json(
            &self.paths.health_path,
            &json!({
                "schema_version": 1,
                "sequence": self.sequence,
                "captured_at": clocks.epoch,
                "dbus_health": payload,
            }),
        )?;
        let diagnostic_health = health.diagnostic(resources, pending_gateway, pending_core);
        let publication_freshness_deadline_s = self
            .slo_thresholds
            .effective_gui_max_age_seconds(self.adaptive_tick.as_secs_f64());
        write_gateway_diagnostics(
            &self.paths.diagnostics_path,
            self.sequence,
            &GatewayDiagnosticsContext {
                clocks,
                health: &diagnostic_health,
                topology: &self.topology,
                registry: &self.publication,
                stale_after_seconds: publication_freshness_deadline_s,
                dormant_evidence: &dormant_evidence,
                unavailability_reasons: &unavailability_reasons,
            },
        )
    }

    pub(super) fn write_cache(
        &mut self,
        health: &GatewayHealthSnapshot,
        cache_freshness: &Value,
        slo: &SloSnapshot,
        queue_scheduler: &Value,
        clocks: Clocks,
    ) -> Result<(), String> {
        self.sequence = self.sequence.saturating_add(1);
        let values = self
            .cache
            .payload(clocks, self.intervals.stale_after_seconds);
        let services = self
            .reader
            .services()
            .iter()
            .map(|name| {
                (
                    name.clone(),
                    json!({"seen_at": clocks.epoch, "status": "present"}),
                )
            })
            .collect::<BTreeMap<_, _>>();
        let resources = self.resources.snapshot().clone();
        let (discovery_success, discovery_error, discovery_interval, discovery_next) =
            self.reader.discovery_health();
        let (dormant_evidence, unavailability_reasons) = self.reader.pv_dormancy_health();
        let unavailability_reasons =
            serde_json::to_value(unavailability_reasons).unwrap_or(Value::Null);
        let health_payload = health.payload(&HealthPayloadContext {
            resources: &resources,
            pending_gateway_commands: self.gateway_mailbox.pending()?.len(),
            pending_core_commands: self.core_mailbox.pending()?.len(),
            registered_path_count: self.publication.registered_path_count(),
            service_count: self.reader.service_count(),
            introspection_queue_depth: self.introspection.queue_depth(),
            adaptive_tick_seconds: self.adaptive_tick.as_secs_f64(),
            last_tick_at: self.last_tick_at,
            mainloop_heartbeat_age_s: heartbeat_age(clocks.monotonic, self.last_tick_monotonic),
            last_tick_duration_ms: self.last_tick_duration_ms,
            cache_freshness,
            discovery_last_success_at: discovery_success,
            discovery_last_error: discovery_error,
            discovery_active_interval_s: discovery_interval,
            discovery_next_scan_in_s: discovery_next,
            slo,
            queue_scheduler,
            publication_freshness_deadline_s: self
                .slo_thresholds
                .effective_gui_max_age_seconds(self.adaptive_tick.as_secs_f64()),
            minimum_tick_seconds: self.intervals.minimum_tick.as_secs_f64(),
            maximum_tick_seconds: self.intervals.maximum_tick.as_secs_f64(),
            critical_read_operations: self.tick_demand.critical_read_operations,
            critical_queue_operations: self.tick_demand.critical_queue_operations,
            operation_p95_ms: self.tick_demand.operation_p95_ms,
            dormant_energy_source_evidence: &dormant_evidence,
            energy_source_unavailability_reasons: &unavailability_reasons,
        });
        atomic_json(
            &self.paths.cache_path,
            &json!({
                "schema_version": 1,
                "sequence": self.sequence,
                "captured_at": clocks.epoch,
                "dbus_health": health_payload,
                "energy_inputs": self.last_energy.as_ref().map_or_else(|| json!({}), EnergyInputs::to_payload),
                "energy_topology": self.topology,
                "values": values,
                "services": services,
            }),
        )?;
        atomic_text(
            &self.paths.cache_sequence_path,
            &format!("{}\n", self.sequence),
        )
    }

    pub(super) fn refresh_cache_observations(&mut self, clocks: Clocks) {
        for publication in self.publication.cache_values() {
            self.cache.remember_publication(publication);
        }
        for relay in 0..2 {
            if let Some((value, observed)) = self.commands.relay_observation(relay) {
                self.cache.remember_external_value(
                    &format!("system:gx-relay:{relay}:state"),
                    Value::from(value),
                    &format!("com.victronenergy.system/Relay/{relay}/State"),
                    observed,
                );
            }
        }
        if let Some(energy) = self.last_energy.clone() {
            let measurements = [
                ("grid_power_w", &energy.grid_power_w),
                ("pv_power_w", &energy.pv_power_w),
                ("battery_soc", &energy.battery_soc),
                ("battery_net_power_w", &energy.battery_net_power_w),
                ("battery_capacity_wh", &energy.battery_capacity_wh),
                ("battery_capacity_ah", &energy.battery_capacity_ah),
                ("battery_voltage_v", &energy.battery_voltage_v),
            ];
            for (key, measurement) in measurements {
                self.cache.remember_measurement(
                    key,
                    measurement,
                    clocks,
                    self.intervals.stale_after_seconds,
                );
            }
        }
    }

    pub(super) fn append_health_history(
        &self,
        health: &GatewayHealthSnapshot,
        cache_freshness: &Value,
        captured_at: f64,
    ) -> Result<(), String> {
        append_jsonl_bounded(
            &self.paths.health_history_path,
            &json!({
                "at": captured_at,
                "state": health.state,
                "backpressure": {"state": health.backpressure_state},
                "timeouts_60s": health.timeouts_60s,
                "operation_latency": health.operations,
                "cache_freshness": cache_freshness,
            }),
            self.health_history_max_bytes,
        )
    }

    pub(super) fn finish_command(
        &mut self,
        path: &Path,
        command: &Map<String, Value>,
        state: &str,
        captured_at: f64,
    ) -> Result<(), String> {
        Mailbox::remove(path)?;
        let queue_class = command_queue_class(command);
        let _ignored = append_jsonl_bounded(
            &self.paths.command_lifecycle_path,
            &json!({
                "at": captured_at,
                "state": state,
                "queue_class": queue_class,
                "kind": super::command::command_kind(command),
                "command_id": command.get("id").and_then(Value::as_str).unwrap_or(""),
            }),
            self.command_lifecycle_max_bytes,
        );
        self.queue_scheduler.record_lifecycle(command, state);
        self.queue_scheduler.record_processed(captured_at);
        Ok(())
    }
}

fn heartbeat_age(now_monotonic: f64, last_tick_monotonic: f64) -> f64 {
    if last_tick_monotonic > 0.0 && now_monotonic >= last_tick_monotonic {
        now_monotonic - last_tick_monotonic
    } else {
        0.0
    }
}

pub(super) fn prepare_runtime_paths(paths: &GatewayPaths) -> Result<(), String> {
    fs::create_dir_all(&paths.run_dir).map_err(|error| error.to_string())?;
    for directory in [&paths.command_dir, &paths.core_command_dir] {
        fs::create_dir_all(directory).map_err(|error| error.to_string())?;
        fs::set_permissions(directory, fs::Permissions::from_mode(0o700))
            .map_err(|error| error.to_string())?;
    }
    Ok(())
}

pub(super) fn command_payload(path: &Path) -> Map<String, Value> {
    fs::read(path)
        .ok()
        .and_then(|payload| serde_json::from_slice::<Value>(&payload).ok())
        .and_then(|value| value.as_object().cloned())
        .unwrap_or_default()
}

pub(super) fn epoch_now() -> Result<f64, String> {
    Clocks::now().map(|clocks| clocks.epoch)
}

fn atomic_text(path: &Path, payload: &str) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("output path has no parent: {}", path.display()))?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let temporary = parent.join(format!(
        ".{}.tmp-{}",
        path.file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("text"),
        std::process::id(),
    ));
    let result = (|| {
        let mut file = File::create(&temporary).map_err(|error| error.to_string())?;
        file.write_all(payload.as_bytes())
            .map_err(|error| error.to_string())?;
        file.flush().map_err(|error| error.to_string())?;
        fs::rename(&temporary, path).map_err(|error| error.to_string())
    })();
    if result.is_err() {
        let _ignored = fs::remove_file(temporary);
    }
    result
}

fn append_jsonl_bounded(path: &Path, payload: &Value, maximum_bytes: usize) -> Result<(), String> {
    if maximum_bytes == 0 {
        return Ok(());
    }
    let parent = path
        .parent()
        .ok_or_else(|| format!("JSONL path has no parent: {}", path.display()))?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let mut line = serde_json::to_vec(payload).map_err(|error| error.to_string())?;
    line.push(b'\n');
    if line.len() > maximum_bytes {
        return Err("JSONL record exceeds configured bound".to_owned());
    }
    let existing_size = fs::metadata(path).map_or(0, |metadata| {
        usize::try_from(metadata.len()).unwrap_or(usize::MAX)
    });
    if existing_size.saturating_add(line.len()) > maximum_bytes {
        let existing = fs::read(path).unwrap_or_default();
        let keep = maximum_bytes.saturating_sub(line.len()) / 2;
        let start = existing.len().saturating_sub(keep);
        let start = existing[start..]
            .iter()
            .position(|byte| *byte == b'\n')
            .map_or(existing.len(), |offset| start + offset + 1);
        let mut compacted = existing[start..].to_vec();
        compacted.extend_from_slice(&line);
        return atomic_bytes(path, &compacted);
    }
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|error| error.to_string())?;
    file.write_all(&line).map_err(|error| error.to_string())
}

fn atomic_bytes(path: &Path, payload: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("output path has no parent: {}", path.display()))?;
    let temporary = parent.join(format!(
        ".{}.compact-{}",
        path.file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("data"),
        std::process::id(),
    ));
    fs::write(&temporary, payload).map_err(|error| error.to_string())?;
    fs::rename(&temporary, path).map_err(|error| error.to_string())
}
