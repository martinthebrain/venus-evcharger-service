//! Strict reader for transport-neutral gateway diagnostics schemas 3 through 5.

use std::path::Path;

use serde::Serialize;
use serde_json::Value;

use crate::error::Result;
use crate::gateway_discovery::{GatewayDiscovery, GatewayPublication};
use crate::gateway_health::GatewayHealth;
pub use crate::gateway_sample::DiagnosticSample;
use crate::gateway_sample::validate_samples;
use crate::gateway_validation::{exact_object, invalid, positive_number, required, required_u64};
use crate::ini::read_bounded_text;

const MAX_GATEWAY_DIAGNOSTICS_BYTES: u64 = 1_048_576;
const SCHEMA_VERSION: u8 = 5;
const CIRCUIT_TRIGGER_SCHEMA_VERSION: u8 = 4;
const LEGACY_SCHEMA_VERSION: u8 = 3;
const CRITICAL_FIELDS: [&str; 3] = ["operating_mode", "charging_enabled", "ac_power_w"];

/// Validated semantic gateway snapshot.
#[derive(Clone, Debug, Serialize)]
pub struct GatewayDiagnostics {
    schema_version: u8,
    sequence: u64,
    captured_at: f64,
    captured_monotonic: f64,
    /// Operational gateway health.
    pub health: GatewayHealth,
    discovery: GatewayDiscovery,
    publication: GatewayPublication,
    /// Semantic EV-charger values with explicit quality.
    pub ev_charger: Vec<DiagnosticSample>,
}

impl GatewayDiagnostics {
    /// Read and strictly validate one bounded diagnostics document.
    ///
    /// # Errors
    ///
    /// Returns an error when the document cannot be read within its size bound,
    /// is invalid JSON, or violates supported schema version 3, 4, or 5.
    pub fn read(path: &Path) -> Result<Self> {
        let text = read_bounded_text(path, MAX_GATEWAY_DIAGNOSTICS_BYTES, "gateway diagnostics")?;
        let value: Value = serde_json::from_str(&text)?;
        Self::from_value(&value)
    }

    /// Decode a diagnostics document from an untrusted JSON value.
    ///
    /// # Errors
    ///
    /// Returns an error when fields, types, quality metadata, or cross-field
    /// invariants violate the gateway diagnostics contract.
    pub fn from_value(value: &Value) -> Result<Self> {
        let object = exact_object(
            value,
            "gateway diagnostics snapshot",
            &[
                "schema_version",
                "sequence",
                "captured_at",
                "captured_monotonic",
                "health",
                "discovery",
                "publication",
                "ev_charger",
            ],
        )?;
        let schema_version = required_u64(
            object,
            "schema_version",
            "gateway diagnostics schema_version",
        )?;
        if ![
            u64::from(LEGACY_SCHEMA_VERSION),
            u64::from(CIRCUIT_TRIGGER_SCHEMA_VERSION),
            u64::from(SCHEMA_VERSION),
        ]
        .contains(&schema_version)
        {
            return Err(invalid(
                "gateway diagnostics has an unsupported schema_version",
            ));
        }
        let sequence = required_u64(object, "sequence", "gateway diagnostics sequence")?;
        let captured_at =
            positive_number(object, "captured_at", "gateway diagnostics captured_at")?;
        let captured_monotonic = positive_number(
            object,
            "captured_monotonic",
            "gateway diagnostics captured_monotonic",
        )?;
        let health = GatewayHealth::from_value(
            required(object, "health")?,
            u8::try_from(schema_version)
                .map_err(|_| invalid("gateway diagnostics schema_version is out of range"))?,
        )?;
        let discovery = GatewayDiscovery::from_value(required(object, "discovery")?)?;
        let publication = GatewayPublication::from_value(required(object, "publication")?)?;
        let samples = required(object, "ev_charger")
            .and_then(|value| {
                value
                    .as_array()
                    .ok_or_else(|| invalid("gateway diagnostics ev_charger must be an array"))
            })?
            .iter()
            .map(DiagnosticSample::from_value)
            .collect::<Result<Vec<_>>>()?;
        validate_samples(&samples)?;
        Ok(Self {
            schema_version: u8::try_from(schema_version)
                .map_err(|_| invalid("gateway diagnostics schema_version is out of range"))?,
            sequence,
            captured_at,
            captured_monotonic,
            health,
            discovery,
            publication,
            ev_charger: samples,
        })
    }

    /// Return critical semantic fields that cannot currently be consumed.
    #[must_use]
    pub fn critical_unavailable_fields(&self) -> Vec<&'static str> {
        CRITICAL_FIELDS
            .iter()
            .copied()
            .filter(|name| {
                self.ev_charger.iter().any(|sample| {
                    sample.name == *name
                        && matches!(sample.status.as_str(), "unavailable" | "error" | "unknown")
                })
            })
            .collect()
    }
}
