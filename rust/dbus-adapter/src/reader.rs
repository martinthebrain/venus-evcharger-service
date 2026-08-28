// SPDX-License-Identifier: GPL-3.0-or-later
//! Scheduled semantic energy reads with bounded aggregate and continuity state.

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::time::{Duration, Instant};

use crate::broker::{DbusOperation, DbusResult, DbusResultValue};
use crate::config::IniConfig;
use crate::energy::{Clocks, EnergyInputs, EnergyTopology, Measurement, MeasurementStatus};

mod continuity;
mod latency;
mod policy;
mod pv_dormancy;
mod pv_tracking;
mod scheduling;
#[cfg(test)]
mod tests;
mod topology;

use continuity::{
    LastGoodPv, apply_pv_hold, held_pv_aggregate, positive_metadata, semantic_battery_power,
};
use latency::OptionalSourceLatencies;
use policy::ReadPolicy;
use pv_dormancy::PvDormancyTracker;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReadMode {
    Normal,
    Degraded,
    Protective,
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
enum ReadKey {
    Grid,
    Pv,
    BatterySoc,
    BatteryPower,
    BatteryCapacityWh,
    BatteryCapacityAh,
    BatteryVoltage,
}

#[derive(Clone, Debug)]
struct ReadMember {
    service: String,
    path: String,
    source_id: String,
}

#[derive(Clone, Debug)]
struct ActiveRead {
    key: ReadKey,
    members: Vec<ReadMember>,
    index: usize,
    total: f64,
    successful_sources: BTreeSet<String>,
    confidence: f64,
    held_estimate: bool,
    errors: Vec<String>,
    interval_factor: f64,
    operation_count: usize,
}

pub struct EnergyReader {
    policy: ReadPolicy,
    services: Vec<String>,
    service_discovery_completed: bool,
    topology_generation: u64,
    topology_changed: bool,
    next_service_scan: Instant,
    next_due: HashMap<ReadKey, Instant>,
    failure_counts: HashMap<ReadKey, u8>,
    optional_latencies: OptionalSourceLatencies,
    active: Option<ActiveRead>,
    last_pv: HashMap<(String, String), LastGoodPv>,
    pv_dormancy: PvDormancyTracker,
    validated_pv: HashMap<String, ReadMember>,
    measurements: [Measurement; 7],
    sequence: u64,
    read_errors: u64,
    last_read_duration: Duration,
    last_discovery_success_at: f64,
    last_discovery_error: String,
    active_discovery_interval: Duration,
}

impl EnergyReader {
    pub fn from_config(config: &IniConfig) -> Self {
        let policy = ReadPolicy::from_config(config);
        let active_discovery_interval = policy.missing_pv_service_interval;
        let now = Instant::now();
        let unknown = || Measurement::unknown(Vec::new());
        Self {
            policy,
            services: Vec::new(),
            service_discovery_completed: false,
            topology_generation: 0,
            topology_changed: true,
            next_service_scan: now,
            next_due: HashMap::new(),
            failure_counts: HashMap::new(),
            optional_latencies: OptionalSourceLatencies::default(),
            active: None,
            last_pv: HashMap::new(),
            pv_dormancy: PvDormancyTracker::default(),
            validated_pv: HashMap::new(),
            measurements: [
                unknown(),
                unknown(),
                unknown(),
                unknown(),
                unknown(),
                unknown(),
                unknown(),
            ],
            sequence: 0,
            read_errors: 0,
            last_read_duration: Duration::ZERO,
            last_discovery_success_at: 0.0,
            last_discovery_error: String::new(),
            active_discovery_interval,
        }
    }

    pub fn next_operation(&mut self, mode: ReadMode) -> Option<DbusOperation> {
        if let Some(active) = &self.active {
            if active.key == ReadKey::Pv && !mode.allows_optional() {
                return None;
            }
            return active.members.get(active.index).map(read_operation);
        }
        let now = Instant::now();
        if mode.allows_discovery() && now >= self.next_service_scan {
            self.next_service_scan = now + self.active_discovery_interval;
            return Some(DbusOperation::ListNames);
        }
        let key = self.next_due_key(now, mode.allows_optional())?;
        self.start_cycle(key, now, mode.interval_factor());
        self.active
            .as_ref()
            .and_then(|active| active.members.first())
            .map(read_operation)
    }

    pub fn handle_result(&mut self, response: DbusResult) -> Result<(), String> {
        self.last_read_duration = response.duration;
        match response.operation {
            DbusOperation::ListNames => self.handle_names(response.result),
            DbusOperation::Read { service, path } => {
                self.handle_read_member(&service, &path, response.result)
            }
            DbusOperation::Write { .. } | DbusOperation::Introspect { .. } => {
                Err("energy reader received a non-read operation".to_owned())
            }
        }
    }

    pub fn snapshot(&mut self, clocks: Clocks) -> EnergyInputs {
        self.sequence = self.sequence.saturating_add(1);
        EnergyInputs {
            sequence: self.sequence,
            topology_generation: self.topology_generation,
            captured_at: clocks.epoch,
            captured_monotonic: clocks.monotonic,
            grid_power_w: self.measurements[0].clone(),
            pv_power_w: self.measurements[1].clone(),
            battery_soc: self.measurements[2].clone(),
            battery_net_power_w: semantic_battery_power(&self.measurements[3]),
            battery_capacity_wh: positive_metadata(&self.measurements[4]),
            battery_capacity_ah: positive_metadata(&self.measurements[5]),
            battery_voltage_v: positive_metadata(&self.measurements[6]),
        }
    }

    pub fn topology(&self, captured_at: f64) -> Result<EnergyTopology, String> {
        EnergyTopology::new(
            self.topology_generation,
            captured_at,
            self.source_descriptors(),
        )
    }

    pub fn take_topology_changed(&mut self) -> bool {
        std::mem::take(&mut self.topology_changed)
    }

    pub fn current_operation_is_optional(&self) -> bool {
        self.active
            .as_ref()
            .is_some_and(|active| active.key == ReadKey::Pv)
    }

    pub const fn service_count(&self) -> usize {
        self.services.len()
    }

    pub fn services(&self) -> &[String] {
        &self.services
    }

    pub fn discovery_health(&self) -> (f64, &str, f64, f64) {
        (
            self.last_discovery_success_at,
            &self.last_discovery_error,
            self.active_discovery_interval.as_secs_f64(),
            self.next_service_scan
                .saturating_duration_since(Instant::now())
                .as_secs_f64(),
        )
    }

    pub fn pv_dormancy_health(&self) -> (serde_json::Value, BTreeMap<String, String>) {
        let source_ids = self.known_pv_source_ids();
        let evidence = self.pv_dormancy.evidence(&source_ids);
        let dormant_ids = evidence
            .iter()
            .map(|item| item.source_id.as_str())
            .collect::<HashSet<_>>();
        let reasons = self
            .pv_descriptor_members()
            .into_iter()
            .filter_map(|member| {
                let reason = if dormant_ids.contains(member.source_id.as_str()) {
                    Some("pv-sleep-confirmed")
                } else if !self
                    .services
                    .iter()
                    .any(|service| service == &member.service)
                {
                    Some("source-not-advertising")
                } else {
                    self.pv_dormancy.failure_reason(&member.source_id)
                }?;
                Some((member.source_id, reason.to_owned()))
            })
            .collect();
        (
            serde_json::to_value(evidence).unwrap_or_else(|_| serde_json::json!([])),
            reasons,
        )
    }

    pub fn introspection_targets(&self) -> Vec<(String, String)> {
        const KEYS: [ReadKey; 7] = [
            ReadKey::Grid,
            ReadKey::Pv,
            ReadKey::BatterySoc,
            ReadKey::BatteryPower,
            ReadKey::BatteryCapacityWh,
            ReadKey::BatteryCapacityAh,
            ReadKey::BatteryVoltage,
        ];
        KEYS.into_iter()
            .flat_map(|key| self.members(key))
            .map(|member| (member.service, member.path))
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect()
    }

    pub fn request_refresh(&mut self, scope: &str) -> bool {
        let keys: &[ReadKey] = match scope {
            "grid" => &[ReadKey::Grid],
            "pv" => &[ReadKey::Pv],
            "battery" => &[
                ReadKey::BatterySoc,
                ReadKey::BatteryPower,
                ReadKey::BatteryCapacityWh,
                ReadKey::BatteryCapacityAh,
                ReadKey::BatteryVoltage,
            ],
            "all" | "energy_source" => &[
                ReadKey::Grid,
                ReadKey::Pv,
                ReadKey::BatterySoc,
                ReadKey::BatteryPower,
                ReadKey::BatteryCapacityWh,
                ReadKey::BatteryCapacityAh,
                ReadKey::BatteryVoltage,
            ],
            _ => return false,
        };
        for key in keys {
            if self.policy.intervals.contains_key(key) {
                self.next_due.remove(key);
            }
        }
        true
    }

    fn handle_names(&mut self, result: Result<DbusResultValue, String>) -> Result<(), String> {
        let value = match result {
            Ok(value) => value,
            Err(error) => {
                self.read_errors = self.read_errors.saturating_add(1);
                let retry = self
                    .policy
                    .service_interval
                    .min(self.policy.missing_pv_service_interval)
                    .min(Duration::from_secs(60));
                self.active_discovery_interval = retry;
                self.next_service_scan = Instant::now() + retry;
                self.last_discovery_error.clone_from(&error);
                return Err(error);
            }
        };
        let DbusResultValue::Names(mut names) = value else {
            self.schedule_discovery_error("ListNames returned the wrong value type");
            return Err("ListNames returned the wrong value type".to_owned());
        };
        names.sort();
        names.dedup();
        self.service_discovery_completed = true;
        if names != self.services {
            self.services = names;
            self.topology_generation = self.topology_generation.saturating_add(1);
            self.topology_changed = true;
        }
        self.maintain_pv();
        let interval = if self.has_valid_advertised_pv() {
            self.policy.service_interval
        } else {
            self.policy.missing_pv_service_interval
        };
        self.active_discovery_interval = interval;
        self.next_service_scan = Instant::now() + interval;
        self.last_discovery_success_at = Clocks::now().map_or(0.0, |clocks| clocks.epoch);
        self.last_discovery_error.clear();
        Ok(())
    }

    fn schedule_discovery_error(&mut self, error: &str) {
        self.read_errors = self.read_errors.saturating_add(1);
        let retry = self
            .policy
            .service_interval
            .min(self.policy.missing_pv_service_interval)
            .min(Duration::from_secs(60));
        self.active_discovery_interval = retry;
        self.next_service_scan = Instant::now() + retry;
        error.clone_into(&mut self.last_discovery_error);
    }

    fn has_valid_advertised_pv(&self) -> bool {
        self.pv_candidate_members()
            .iter()
            .any(|member| self.pv_dormancy.validated(&member.source_id))
    }

    fn handle_read_member(
        &mut self,
        service: &str,
        path: &str,
        result: Result<DbusResultValue, String>,
    ) -> Result<(), String> {
        let Some(mut active) = self.active.take() else {
            return Err("D-Bus read completed without an active aggregate".to_owned());
        };
        let Some(member) = active.members.get(active.index).cloned() else {
            return Err("D-Bus read aggregate index is invalid".to_owned());
        };
        if member.service != service || member.path != path {
            return Err("D-Bus read result does not match the active member".to_owned());
        }
        active.operation_count = active.operation_count.saturating_add(1);
        if active.key == ReadKey::Pv {
            active.interval_factor = active.interval_factor.max(
                self.optional_latencies
                    .record(&format!("{service}{path}"), self.last_read_duration),
            );
        }
        match result {
            Ok(DbusResultValue::Value(value)) => {
                if let Some(number) = value.as_f64() {
                    active.total += number;
                    active.successful_sources.insert(member.source_id.clone());
                    if active.key == ReadKey::Pv {
                        self.record_pv_value(&member);
                        self.last_pv.insert(
                            (service.to_owned(), path.to_owned()),
                            LastGoodPv {
                                value: number,
                                hold_started: None,
                            },
                        );
                    }
                } else {
                    if active.key == ReadKey::Pv {
                        self.record_pv_error(&member, "non-numeric value");
                        active.confidence = active.confidence.min(0.2);
                    }
                    active
                        .errors
                        .push(format!("{service}{path}: non-numeric value"));
                }
            }
            Ok(_) => {
                if active.key == ReadKey::Pv {
                    self.record_pv_error(&member, "wrong reply type");
                    active.confidence = active.confidence.min(0.2);
                }
                active
                    .errors
                    .push(format!("{service}{path}: wrong reply type"));
            }
            Err(error) => {
                self.read_errors = self.read_errors.saturating_add(1);
                if active.key == ReadKey::Pv {
                    apply_pv_hold(&mut active, &mut self.last_pv, service, path, &error);
                    self.record_pv_error(&member, &error);
                } else {
                    active.errors.push(format!("{service}{path}: {error}"));
                }
            }
        }
        active.index += 1;
        if active.index < active.members.len() {
            self.active = Some(active);
            return Ok(());
        }
        self.complete_cycle(active)
    }

    fn complete_cycle(&mut self, active: ActiveRead) -> Result<(), String> {
        let clocks = Clocks::now()?;
        self.schedule_completed_cycle(&active);
        let sources = active.successful_sources.into_iter().collect::<Vec<_>>();
        let measurement = if active.errors.is_empty() || active.key == ReadKey::Pv {
            Measurement {
                value: Some(active.total),
                observed_at: clocks.epoch,
                observed_monotonic: clocks.monotonic,
                status: if active.held_estimate {
                    MeasurementStatus::Stale
                } else {
                    MeasurementStatus::Fresh
                },
                confidence: active.confidence,
                source_ids: sources,
                reason_code: if active.held_estimate {
                    "transient-hold".to_owned()
                } else if active.errors.is_empty() {
                    String::new()
                } else {
                    "optional-source-unavailable".to_owned()
                },
            }
        } else {
            Measurement {
                value: None,
                observed_at: 0.0,
                observed_monotonic: 0.0,
                status: MeasurementStatus::Error,
                confidence: 0.0,
                source_ids: active
                    .members
                    .iter()
                    .map(|member| member.source_id.clone())
                    .collect(),
                reason_code: "source-error".to_owned(),
            }
        };
        self.measurements[measurement_index(active.key)] = measurement;
        Ok(())
    }
}

const fn measurement_index(key: ReadKey) -> usize {
    match key {
        ReadKey::Grid => 0,
        ReadKey::Pv => 1,
        ReadKey::BatterySoc => 2,
        ReadKey::BatteryPower => 3,
        ReadKey::BatteryCapacityWh => 4,
        ReadKey::BatteryCapacityAh => 5,
        ReadKey::BatteryVoltage => 6,
    }
}

fn read_operation(member: &ReadMember) -> DbusOperation {
    DbusOperation::Read {
        service: member.service.clone(),
        path: member.path.clone(),
    }
}
