//! Read-only forensic observer for the Venus EV charger service.
//!
//! This crate consumes semantic gateway artifacts and operating-system state.
//! It deliberately has no `DBus` dependency and cannot publish control data.

#![deny(missing_docs)]

pub mod artifact;
pub mod command;
pub mod config;
mod config_topology;
pub mod error;
pub mod gateway;
pub(crate) mod gateway_sample;
pub(crate) mod gateway_validation;
pub mod ini;
pub mod probe;
pub mod runtime;
pub mod snapshot;

pub use error::{ObserverError, Result};
