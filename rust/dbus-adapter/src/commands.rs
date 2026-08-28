// SPDX-License-Identifier: GPL-3.0-or-later
//! Semantic gateway operations translated to adapter-owned D-Bus topology.

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use serde_json::{Map, Value};

use crate::broker::{DbusOperation, DbusResult};
use crate::config::IniConfig;
use crate::dbus::BusValue;
use crate::energy::Clocks;

const SYSTEM_SERVICE: &str = "com.victronenergy.system";
const SETTINGS_SERVICE: &str = "com.victronenergy.settings";
const MANUAL_FUNCTION_VALUE: i32 = 2;
mod contract;

use contract::{
    binary_value, channel, command_delay, device_nodes, finite_number, manual_paths,
    nonnegative_number, normalize_mac, numeric_value, relay_index, relay_state_path, relay_target,
    selector, shelly_enabled_path, strict_bool, string_value, write_code, xml_value,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CommandOutcome {
    Applied,
    Deferred,
    Dropped,
}

#[derive(Clone, Debug)]
enum Stage {
    RelayRefresh { relay: usize },
    RelayManualRead { relay: usize, candidate: usize },
    RelayManualWrite { relay: usize, candidate: usize },
    RelayOutput { relay: usize, retry: u8 },
    RelayVerify { relay: usize, retry: u8 },
    EssWrite,
    ShellyDiscover,
    ShellyIdentify { devices: Vec<String>, cursor: usize },
    ShellyEnabled { device: String },
    ShellyDisable { device: String },
}

struct ActiveCommand {
    path: PathBuf,
    payload: Map<String, Value>,
    stage: Stage,
    not_before: Instant,
    awaiting: bool,
}

pub struct CommandExecutor {
    generic_shelly_service: String,
    ess_service: String,
    ess_path: String,
    active: Option<ActiveCommand>,
    relay_states: [Option<i32>; 2],
    relay_observed_at: [Option<Clocks>; 2],
}

impl CommandExecutor {
    pub fn from_config(config: &IniConfig) -> Self {
        Self {
            generic_shelly_service: config.text("GenericShellyService", "com.victronenergy.shelly"),
            ess_service: config.text(
                "AutoBatteryDischargeBalanceVictronBiasService",
                SETTINGS_SERVICE,
            ),
            ess_path: config.text(
                "AutoBatteryDischargeBalanceVictronBiasPath",
                "/Settings/CGwacs/AcPowerSetPoint",
            ),
            active: None,
            relay_states: [None, None],
            relay_observed_at: [None, None],
        }
    }

    pub fn start(
        &mut self,
        path: PathBuf,
        payload: Map<String, Value>,
    ) -> Result<Option<CommandOutcome>, String> {
        if self.active.is_some() {
            return Ok(None);
        }
        let kind = payload.get("kind").and_then(Value::as_str).unwrap_or("");
        let stage = match kind {
            "gx_relay_refresh" => Stage::RelayRefresh {
                relay: relay_index(&payload)?,
            },
            "gx_relay_set_enabled" => {
                let relay = relay_index(&payload)?;
                if strict_bool(&payload, "ensure_manual")? {
                    Stage::RelayManualRead {
                        relay,
                        candidate: 0,
                    }
                } else {
                    Stage::RelayOutput { relay, retry: 0 }
                }
            }
            "ess_grid_setpoint" => Stage::EssWrite,
            "disable_matching_generic_shelly_once" => Stage::ShellyDiscover,
            _ => return Ok(Some(CommandOutcome::Dropped)),
        };
        let not_before = command_delay(&payload)?;
        self.active = Some(ActiveCommand {
            path,
            payload,
            stage,
            not_before,
            awaiting: false,
        });
        Ok(None)
    }

    pub fn next_operation(&mut self) -> Result<Option<DbusOperation>, String> {
        let config = (
            self.generic_shelly_service.clone(),
            self.ess_service.clone(),
            self.ess_path.clone(),
        );
        let Some(active) = self.active.as_mut() else {
            return Ok(None);
        };
        if active.awaiting || Instant::now() < active.not_before {
            return Ok(None);
        }
        let operation = operation_for(active, &config)?;
        active.awaiting = true;
        Ok(Some(operation))
    }

    pub fn handle_result(&mut self, result: &DbusResult) -> Result<Option<CommandOutcome>, String> {
        let Some(mut active) = self.active.take() else {
            return Err("command result arrived without an active command".to_owned());
        };
        active.awaiting = false;
        let observed_relay = stage_observed_relay(&active.stage);
        let outcome = transition(&mut active, result, &mut self.relay_states)?;
        if result.result.is_ok()
            && let Some(relay) = observed_relay
            && self.relay_states[relay].is_some()
        {
            self.relay_observed_at[relay] = Some(Clocks::now()?);
        }
        if outcome.is_none() {
            self.active = Some(active);
        }
        Ok(outcome)
    }

    pub const fn operation_submission_failed(&mut self) {
        if let Some(active) = self.active.as_mut() {
            active.awaiting = false;
        }
    }

    pub fn active_path(&self) -> Option<&Path> {
        self.active.as_ref().map(|active| active.path.as_path())
    }

    pub const fn active(&self) -> bool {
        self.active.is_some()
    }

    pub fn relay_observation(&self, relay: usize) -> Option<(i32, Clocks)> {
        Some((
            self.relay_states.get(relay).copied().flatten()?,
            self.relay_observed_at.get(relay).copied().flatten()?,
        ))
    }

    pub fn abandon(&mut self) {
        self.active = None;
    }
}

const fn stage_observed_relay(stage: &Stage) -> Option<usize> {
    match stage {
        Stage::RelayRefresh { relay } | Stage::RelayVerify { relay, .. } => Some(*relay),
        _ => None,
    }
}

fn operation_for(
    active: &ActiveCommand,
    config: &(String, String, String),
) -> Result<DbusOperation, String> {
    let (generic_shelly_service, ess_service, ess_path) = config;
    match &active.stage {
        Stage::RelayRefresh { relay } | Stage::RelayVerify { relay, .. } => {
            Ok(DbusOperation::Read {
                service: SYSTEM_SERVICE.to_owned(),
                path: relay_state_path(*relay),
            })
        }
        Stage::RelayManualRead { relay, candidate } => Ok(DbusOperation::Read {
            service: SETTINGS_SERVICE.to_owned(),
            path: manual_paths(*relay)[*candidate].clone(),
        }),
        Stage::RelayManualWrite { relay, candidate } => Ok(DbusOperation::Write {
            service: SETTINGS_SERVICE.to_owned(),
            path: manual_paths(*relay)[*candidate].clone(),
            value: BusValue::I32(MANUAL_FUNCTION_VALUE),
        }),
        Stage::RelayOutput { relay, .. } => Ok(DbusOperation::Write {
            service: SYSTEM_SERVICE.to_owned(),
            path: relay_state_path(*relay),
            value: BusValue::I32(relay_target(&active.payload)?),
        }),
        Stage::EssWrite => Ok(DbusOperation::Write {
            service: ess_service.clone(),
            path: ess_path.clone(),
            value: BusValue::F64(finite_number(&active.payload, "watts")?),
        }),
        Stage::ShellyDiscover => Ok(DbusOperation::Introspect {
            service: generic_shelly_service.clone(),
            path: "/Devices".to_owned(),
        }),
        Stage::ShellyIdentify { devices, cursor } => {
            let device = devices
                .get(*cursor)
                .ok_or_else(|| "generic Shelly cursor is invalid".to_owned())?;
            let selector = selector(&active.payload)?;
            Ok(DbusOperation::Read {
                service: generic_shelly_service.clone(),
                path: format!(
                    "/Devices/{device}/{}",
                    if selector.0 == "ip" { "Ip" } else { "Mac" }
                ),
            })
        }
        Stage::ShellyEnabled { device } => Ok(DbusOperation::Read {
            service: generic_shelly_service.clone(),
            path: shelly_enabled_path(device, channel(&active.payload)?),
        }),
        Stage::ShellyDisable { device } => Ok(DbusOperation::Write {
            service: generic_shelly_service.clone(),
            path: shelly_enabled_path(device, channel(&active.payload)?),
            value: BusValue::I32(0),
        }),
    }
}

fn transition(
    active: &mut ActiveCommand,
    response: &DbusResult,
    relay_states: &mut [Option<i32>; 2],
) -> Result<Option<CommandOutcome>, String> {
    if response.result.is_err() {
        return Ok(transition_error(active));
    }
    let stage = active.stage.clone();
    match stage {
        Stage::RelayRefresh { .. }
        | Stage::RelayManualRead { .. }
        | Stage::RelayManualWrite { .. }
        | Stage::RelayOutput { .. }
        | Stage::RelayVerify { .. } => transition_relay(active, response, relay_states, &stage),
        Stage::EssWrite | Stage::ShellyDisable { .. } => {
            write_code(response)?;
            Ok(Some(CommandOutcome::Applied))
        }
        Stage::ShellyDiscover | Stage::ShellyIdentify { .. } | Stage::ShellyEnabled { .. } => {
            transition_shelly(active, response, stage)
        }
    }
}

fn transition_error(active: &mut ActiveCommand) -> Option<CommandOutcome> {
    match active.stage.clone() {
        Stage::RelayManualRead { relay, candidate }
        | Stage::RelayManualWrite { relay, candidate } => {
            let next_candidate = candidate + 1;
            if next_candidate < manual_paths(relay).len() {
                active.stage = Stage::RelayManualRead {
                    relay,
                    candidate: next_candidate,
                };
                None
            } else {
                Some(CommandOutcome::Deferred)
            }
        }
        // A failed verification must not repeat a write that may already have
        // reached the relay. The next refresh reconciles the cached state.
        Stage::RelayVerify { .. } => Some(CommandOutcome::Applied),
        _ => Some(CommandOutcome::Deferred),
    }
}

fn transition_relay(
    active: &mut ActiveCommand,
    response: &DbusResult,
    relay_states: &mut [Option<i32>; 2],
    stage: &Stage,
) -> Result<Option<CommandOutcome>, String> {
    match stage {
        Stage::RelayRefresh { relay } => {
            let observed = binary_value(response)?;
            relay_states[*relay] = observed;
            Ok(Some(if observed.is_some() {
                CommandOutcome::Applied
            } else {
                CommandOutcome::Dropped
            }))
        }
        Stage::RelayManualRead { relay, candidate } => {
            let expected = f64::from(MANUAL_FUNCTION_VALUE).to_bits();
            if numeric_value(response)?.is_some_and(|value| value.to_bits() == expected) {
                active.stage = Stage::RelayOutput {
                    relay: *relay,
                    retry: 0,
                };
            } else {
                active.stage = Stage::RelayManualWrite {
                    relay: *relay,
                    candidate: *candidate,
                };
            }
            Ok(None)
        }
        Stage::RelayManualWrite { relay, .. } => {
            write_code(response)?;
            active.stage = Stage::RelayOutput {
                relay: *relay,
                retry: 0,
            };
            Ok(None)
        }
        Stage::RelayOutput { relay, retry } => {
            write_code(response)?;
            let settle = nonnegative_number(&active.payload, "verify_settle_seconds")?;
            active.not_before = Instant::now() + Duration::from_secs_f64(settle);
            active.stage = Stage::RelayVerify {
                relay: *relay,
                retry: *retry,
            };
            Ok(None)
        }
        Stage::RelayVerify { relay, retry } => {
            let observed = binary_value(response)?;
            let target = relay_target(&active.payload)?;
            relay_states[*relay] = observed;
            if observed == Some(target) {
                return Ok(Some(CommandOutcome::Applied));
            }
            if *retry == 0 {
                let delay = nonnegative_number(&active.payload, "verify_retry_seconds")?;
                active.not_before = Instant::now() + Duration::from_secs_f64(delay);
                active.stage = Stage::RelayOutput {
                    relay: *relay,
                    retry: 1,
                };
                Ok(None)
            } else {
                Ok(Some(CommandOutcome::Dropped))
            }
        }
        _ => Err("non-relay stage reached relay transition".to_owned()),
    }
}

fn transition_shelly(
    active: &mut ActiveCommand,
    response: &DbusResult,
    stage: Stage,
) -> Result<Option<CommandOutcome>, String> {
    match stage {
        Stage::ShellyDiscover => {
            let xml = xml_value(response)?;
            let devices = device_nodes(xml)?;
            if devices.is_empty() {
                return Ok(Some(CommandOutcome::Applied));
            }
            let selector = selector(&active.payload)?;
            if selector.0 == "mac" {
                if let Some(device) = devices
                    .iter()
                    .find(|device| normalize_mac(device).as_deref() == Some(selector.1.as_str()))
                {
                    active.stage = Stage::ShellyEnabled {
                        device: device.clone(),
                    };
                    return Ok(None);
                }
            }
            active.stage = Stage::ShellyIdentify { devices, cursor: 0 };
            Ok(None)
        }
        Stage::ShellyIdentify { devices, cursor } => {
            let candidate = string_value(response)?;
            let selector = selector(&active.payload)?;
            let matches = if selector.0 == "ip" {
                candidate.trim() == selector.1
            } else {
                normalize_mac(candidate).as_deref() == Some(selector.1.as_str())
            };
            if matches {
                active.stage = Stage::ShellyEnabled {
                    device: devices[cursor].clone(),
                };
            } else if cursor + 1 < devices.len() {
                active.stage = Stage::ShellyIdentify {
                    devices,
                    cursor: cursor + 1,
                };
            } else {
                return Ok(Some(CommandOutcome::Applied));
            }
            Ok(None)
        }
        Stage::ShellyEnabled { device } => match binary_value(response)? {
            Some(0) => Ok(Some(CommandOutcome::Applied)),
            Some(1) => {
                active.stage = Stage::ShellyDisable { device };
                Ok(None)
            }
            _ => Ok(Some(CommandOutcome::Dropped)),
        },
        _ => Err("non-Shelly stage reached Shelly transition".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;
    use std::time::{Duration, Instant};

    use serde_json::Map;

    use crate::broker::{DbusOperation, DbusResult};

    use super::{ActiveCommand, CommandOutcome, Stage, transition};

    #[test]
    fn relay_zero_uses_legacy_manual_path_after_primary_error() {
        let mut command = ActiveCommand {
            path: PathBuf::from("command.json"),
            payload: Map::new(),
            stage: Stage::RelayManualRead {
                relay: 0,
                candidate: 0,
            },
            not_before: Instant::now(),
            awaiting: false,
        };
        let response = failed_read();
        assert_eq!(
            transition(&mut command, &response, &mut [None; 2]),
            Ok(None)
        );
        assert!(matches!(
            command.stage,
            Stage::RelayManualRead {
                relay: 0,
                candidate: 1
            }
        ));
    }

    #[test]
    fn relay_verification_error_does_not_repeat_a_successful_write() {
        let mut command = ActiveCommand {
            path: PathBuf::from("command.json"),
            payload: Map::new(),
            stage: Stage::RelayVerify { relay: 1, retry: 0 },
            not_before: Instant::now(),
            awaiting: false,
        };
        assert_eq!(
            transition(&mut command, &failed_read(), &mut [None; 2]),
            Ok(Some(CommandOutcome::Applied))
        );
    }

    fn failed_read() -> DbusResult {
        DbusResult {
            operation: DbusOperation::Read {
                service: "com.victronenergy.settings".to_owned(),
                path: "/Settings/Relay/0/Function".to_owned(),
            },
            result: Err("NoReply".to_owned()),
            duration: Duration::from_millis(1),
        }
    }
}
