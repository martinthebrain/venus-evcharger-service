// SPDX-License-Identifier: GPL-3.0-or-later
//! Native owner of every Victron D-Bus operation used by the EV charger.

#![recursion_limit = "256"]

use std::path::Path;
use std::sync::atomic::AtomicBool;

mod broker;
mod cache;
mod commands;
mod config;
mod dbus;
mod diagnostics;
mod energy;
mod fast_socket;
mod health;
mod introspection;
mod mailbox;
mod publication;
mod reader;
mod resources;
mod runtime;
#[cfg(test)]
mod runtime_policy_contract;

/// Run the native D-Bus adapter using one validated configuration boundary.
///
/// # Errors
///
/// Returns an error when configuration, runtime IPC, D-Bus setup, or an
/// atomic runtime publication cannot be completed.
pub fn run_adapter(
    config_path: &Path,
    run_dir: Option<&Path>,
    stop: &AtomicBool,
) -> Result<(), String> {
    let config = config::IniConfig::load(config_path)?;
    let paths = config::GatewayPaths::from_config(&config, run_dir)?;
    runtime::AdapterRuntime::new(&config, paths)?.run(stop)
}

/// Validate the adapter configuration without opening D-Bus or runtime IPC.
///
/// # Errors
///
/// Returns an error when the configuration cannot be read or contains an
/// invalid runtime path.
pub fn validate_adapter_config(config_path: &Path) -> Result<(), String> {
    let config = config::IniConfig::load(config_path)?;
    config::GatewayPaths::from_config(&config, None).map(|_paths| ())
}
