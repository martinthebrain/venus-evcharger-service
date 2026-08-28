// SPDX-License-Identifier: GPL-3.0-or-later
//! Single-operation broker ownership, scheduling, and adaptive cadence.

use std::path::Path;
use std::time::{Duration, Instant};

use super::queue::PressureState;
use super::scheduling::TickDemand;
use super::storage::{command_payload, epoch_now};
use super::{AdapterRuntime, BrokerOwner};
use crate::broker::{DbusOperation, DbusResult};
use crate::commands::CommandOutcome;
use crate::energy::Clocks;
use crate::mailbox::Mailbox;
use crate::reader::ReadMode;
use crate::resources::ResourceState;

const COMMAND_RETRY_DELAY: Duration = Duration::from_secs(2);

impl AdapterRuntime {
    pub(super) fn poll_broker(&mut self, clocks: Clocks) {
        let response = match self.broker.poll() {
            Ok(Some(response)) => response,
            Ok(None) => return,
            Err(error) => {
                eprintln!("native DBus broker stopped: {error}");
                return;
            }
        };
        let Some(owner) = self.broker_owner.take() else {
            eprintln!("native DBus broker returned an unowned result");
            return;
        };
        let optional = matches!(owner, BrokerOwner::Reader { optional: true });
        self.health.record_operation(&response, optional, clocks);
        match owner {
            BrokerOwner::Reader { .. } => {
                if let Err(error) = self.reader.handle_result(response) {
                    eprintln!("native energy read failed: {error}");
                }
                self.cache_dirty = true;
            }
            BrokerOwner::Command => self.complete_semantic_command(&response, clocks.epoch),
            BrokerOwner::Introspection => {
                match self.introspection.handle_result(response, clocks.epoch) {
                    Ok(Some(path)) => {
                        let _ignored = Mailbox::remove(&path);
                    }
                    Ok(None) => {}
                    Err(error) => eprintln!("native introspection result failed: {error}"),
                }
            }
        }
    }

    fn complete_semantic_command(&mut self, response: &DbusResult, captured_at: f64) {
        let path = self.commands.active_path().map(Path::to_path_buf);
        match self.commands.handle_result(response) {
            Ok(Some(outcome)) => {
                let Some(path) = path else {
                    return;
                };
                let state = match outcome {
                    CommandOutcome::Applied => "applied",
                    CommandOutcome::Deferred => "deferred",
                    CommandOutcome::Dropped => "dropped",
                };
                if outcome == CommandOutcome::Deferred {
                    self.next_command_retry = Instant::now() + COMMAND_RETRY_DELAY;
                } else {
                    let payload = command_payload(&path);
                    let _ignored = self.finish_command(&path, &payload, state, captured_at);
                    self.cache_dirty = true;
                }
            }
            Ok(None) => {}
            Err(error) => {
                eprintln!("native semantic command failed: {error}");
                self.commands.abandon();
                self.next_command_retry = Instant::now() + COMMAND_RETRY_DELAY;
            }
        }
    }

    pub(super) fn schedule_operation(
        &mut self,
        resource_state: ResourceState,
    ) -> Result<(), String> {
        if self.broker.busy() {
            return Ok(());
        }
        if self.commands.active() && self.try_schedule_command()? {
            return Ok(());
        }
        let circuit_state = self.health.operational_state();
        let read_mode = match circuit_state {
            "protective" => ReadMode::Protective,
            "degraded" => ReadMode::Degraded,
            _ => ReadMode::Normal,
        };
        if self.try_schedule_reader(read_mode)? {
            return Ok(());
        }
        if resource_state != ResourceState::Constrained && circuit_state == "ok" {
            self.try_schedule_introspection()?;
        }
        Ok(())
    }

    pub(super) fn pressure_state(&self, resource_state: ResourceState) -> PressureState {
        match self.health.operational_state() {
            "protective" => PressureState::Protective,
            "degraded" => PressureState::Slow,
            _ if resource_state == ResourceState::Constrained => PressureState::Slow,
            _ if self.health.slo_pressure_state() == "slow" => PressureState::Slow,
            _ if resource_state == ResourceState::Busy
                || self.health.slo_pressure_state() == "congested" =>
            {
                PressureState::Congested
            }
            _ => PressureState::Ok,
        }
    }

    pub(super) fn update_adaptive_tick(&mut self, clocks: Clocks, resource_state: ResourceState) {
        let (critical_reads, core_read_age) =
            self.reader.tick_demand(Instant::now(), clocks.monotonic);
        let queue = self.queue_scheduler.demand();
        let demand = TickDemand {
            critical_read_operations: critical_reads,
            critical_queue_operations: queue
                .critical_operations
                .saturating_add(self.pending_core_commands),
            core_read_age_seconds: core_read_age,
            queue_age_seconds: queue
                .oldest_slo_age_seconds
                .max(self.oldest_core_command_age_seconds),
            operation_p95_ms: self.health.operation_p95_ms(),
        };
        self.adaptive_tick =
            self.intervals
                .tick_for(resource_state, self.health.operational_state(), demand);
        self.tick_demand = demand;
    }

    fn try_schedule_command(&mut self) -> Result<bool, String> {
        let operation = match self.commands.next_operation() {
            Ok(Some(operation)) => operation,
            Ok(None) => return Ok(false),
            Err(error) => {
                let path = self.commands.active_path().map(Path::to_path_buf);
                self.commands.abandon();
                if let Some(path) = path {
                    let payload = command_payload(&path);
                    self.finish_command(&path, &payload, "dropped", epoch_now()?)?;
                }
                eprintln!("native command contract rejected: {error}");
                return Ok(false);
            }
        };
        if !self.rates.due(&operation, Instant::now()) {
            self.commands.operation_submission_failed();
            return Ok(false);
        }
        let submitted = self.submit(&operation, BrokerOwner::Command);
        if !matches!(submitted, Ok(true)) {
            self.commands.operation_submission_failed();
        }
        submitted
    }

    fn try_schedule_reader(&mut self, mode: ReadMode) -> Result<bool, String> {
        let Some(operation) = self.reader.next_operation(mode) else {
            return Ok(false);
        };
        if !self.rates.due(&operation, Instant::now()) {
            return Ok(false);
        }
        let optional = self.reader.current_operation_is_optional();
        self.submit(&operation, BrokerOwner::Reader { optional })
    }

    fn try_schedule_introspection(&mut self) -> Result<bool, String> {
        let Some(operation) = self.introspection.next_operation() else {
            return Ok(false);
        };
        if !self.rates.due(&operation, Instant::now()) {
            self.introspection.operation_submission_failed();
            return Ok(false);
        }
        let submitted = self.submit(&operation, BrokerOwner::Introspection);
        if !matches!(submitted, Ok(true)) {
            self.introspection.operation_submission_failed();
        }
        submitted
    }

    fn submit(&mut self, operation: &DbusOperation, owner: BrokerOwner) -> Result<bool, String> {
        let now = Instant::now();
        match self.broker.submit(operation.clone()) {
            Ok(true) => {
                self.rates.mark(operation, now);
                self.broker_owner = Some(owner);
                Ok(true)
            }
            Ok(false) => Ok(false),
            Err(error) => Err(error),
        }
    }
}
