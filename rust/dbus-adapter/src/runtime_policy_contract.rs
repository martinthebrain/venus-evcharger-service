// SPDX-License-Identifier: GPL-3.0-or-later
//! Python-generated differential scenarios used only by Rust tests.

use std::collections::BTreeMap;

use serde::Deserialize;
use serde_json::{Map, Value};

const CONTRACT_JSON: &str = include_str!("../contracts/runtime_policy.json");

#[derive(Debug, Deserialize)]
pub struct RuntimePolicyContract {
    pub schema_version: u8,
    pub commands: Vec<CommandCase>,
    pub tick_policy: TickPolicyCases,
    pub slo: SloCase,
    pub dormancy_messages: Vec<DormancyMessage>,
}

#[derive(Debug, Deserialize)]
pub struct CommandCase {
    pub command: Map<String, Value>,
    pub queue_class: String,
    pub allowed: BTreeMap<String, bool>,
}

#[derive(Debug, Deserialize)]
pub struct TickPolicyCases {
    pub policy: TickPolicyValues,
    pub cases: Vec<TickCase>,
}

#[derive(Debug, Deserialize)]
pub struct TickPolicyValues {
    #[serde(rename = "min_tick_seconds")]
    pub minimum_tick: f64,
    #[serde(rename = "max_tick_seconds")]
    pub maximum_tick: f64,
    #[serde(rename = "core_read_slo_seconds")]
    pub core_read_slo: f64,
    #[serde(rename = "queue_slo_seconds")]
    pub queue_slo: f64,
}

#[derive(Debug, Deserialize)]
pub struct TickCase {
    pub demand: TickDemandValues,
    pub circuit_state: String,
    pub resource_state: String,
    pub tick_seconds: f64,
}

#[derive(Debug, Deserialize)]
pub struct TickDemandValues {
    pub critical_read_operations: usize,
    pub critical_queue_operations: usize,
    pub core_read_age_seconds: f64,
    pub queue_age_seconds: f64,
    pub operation_p95_ms: f64,
}

#[derive(Debug, Deserialize)]
pub struct SloCase {
    pub thresholds: SloThresholdValues,
    pub observed: BTreeMap<String, f64>,
    pub checks: Value,
    pub targets: Value,
}

#[derive(Debug, Deserialize)]
pub struct SloThresholdValues {
    pub gui_max_age_seconds: f64,
    pub core_read_max_age_seconds: f64,
    pub queue_max_age_seconds: f64,
    pub mainloop_gap_max_ms: f64,
    pub publication_scheduler_tolerance_seconds: f64,
}

#[derive(Debug, Deserialize)]
pub struct DormancyMessage {
    pub message: String,
    pub explicit: bool,
}

pub fn load() -> Result<RuntimePolicyContract, String> {
    let contract: RuntimePolicyContract =
        serde_json::from_str(CONTRACT_JSON).map_err(|error| error.to_string())?;
    if contract.schema_version != 1 {
        return Err("unsupported native runtime policy contract".to_owned());
    }
    Ok(contract)
}
