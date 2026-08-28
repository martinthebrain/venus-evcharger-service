// SPDX-License-Identifier: GPL-3.0-or-later
//! Semantic energy contract published by the native gateway.

use std::fs;
use std::io::Write;
use std::path::Path;
use std::time::Duration;

use rustix::time::{ClockId, clock_gettime};
use serde::Serialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

const MAGIC: &[u8; 4] = b"VEI4";
const SCHEMA_VERSION: u8 = 4;
const MAX_PAYLOAD_BYTES: usize = 65_536;
const MAX_SOURCE_IDS: usize = 64;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MeasurementStatus {
    Fresh,
    Stale,
    Unavailable,
    Error,
    Unknown,
}

impl MeasurementStatus {
    const fn code(self) -> u8 {
        match self {
            Self::Fresh => 0,
            Self::Stale => 1,
            Self::Unavailable => 2,
            Self::Error => 3,
            Self::Unknown => 4,
        }
    }

    pub(crate) const fn as_str(self) -> &'static str {
        match self {
            Self::Fresh => "fresh",
            Self::Stale => "stale",
            Self::Unavailable => "unavailable",
            Self::Error => "error",
            Self::Unknown => "unknown",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Measurement {
    pub value: Option<f64>,
    pub observed_at: f64,
    pub observed_monotonic: f64,
    pub status: MeasurementStatus,
    pub confidence: f64,
    pub source_ids: Vec<String>,
    pub reason_code: String,
}

impl Measurement {
    #[cfg(test)]
    pub fn fresh(value: f64, source_ids: Vec<String>, clocks: Clocks) -> Self {
        Self {
            value: value.is_finite().then_some(value),
            observed_at: clocks.epoch,
            observed_monotonic: clocks.monotonic,
            status: if value.is_finite() {
                MeasurementStatus::Fresh
            } else {
                MeasurementStatus::Unavailable
            },
            confidence: if value.is_finite() { 1.0 } else { 0.0 },
            source_ids,
            reason_code: String::new(),
        }
    }

    pub fn unavailable(source_ids: Vec<String>, reason: &str) -> Self {
        Self {
            value: None,
            observed_at: 0.0,
            observed_monotonic: 0.0,
            status: MeasurementStatus::Unavailable,
            confidence: 0.0,
            source_ids,
            reason_code: reason.to_owned(),
        }
    }

    pub fn unknown(source_ids: Vec<String>) -> Self {
        Self {
            status: MeasurementStatus::Unknown,
            reason_code: "not-observed".to_owned(),
            ..Self::unavailable(source_ids, "not-observed")
        }
    }

    fn validate(&self, captured_monotonic: f64) -> Result<(), String> {
        if self.value.is_some_and(|value| !value.is_finite()) {
            return Err("measurement value must be finite".to_owned());
        }
        if !self.observed_at.is_finite()
            || self.observed_at < 0.0
            || !self.observed_monotonic.is_finite()
            || self.observed_monotonic < 0.0
            || self.observed_monotonic > captured_monotonic
        {
            return Err("measurement timestamps violate the monotonic contract".to_owned());
        }
        if !self.confidence.is_finite() || !(0.0..=1.0).contains(&self.confidence) {
            return Err("measurement confidence must be between zero and one".to_owned());
        }
        if self.source_ids.len() > MAX_SOURCE_IDS {
            return Err("measurement has too many source ids".to_owned());
        }
        if matches!(
            self.status,
            MeasurementStatus::Fresh | MeasurementStatus::Stale
        ) && (self.value.is_none() || self.observed_at <= 0.0 || self.observed_monotonic <= 0.0)
        {
            return Err("observed measurement requires value and timestamps".to_owned());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct EnergyInputs {
    pub sequence: u64,
    pub topology_generation: u64,
    pub captured_at: f64,
    pub captured_monotonic: f64,
    pub grid_power_w: Measurement,
    pub pv_power_w: Measurement,
    pub battery_soc: Measurement,
    pub battery_net_power_w: Measurement,
    pub battery_capacity_wh: Measurement,
    pub battery_capacity_ah: Measurement,
    pub battery_voltage_v: Measurement,
}

impl EnergyInputs {
    pub fn encode(&self) -> Result<Vec<u8>, String> {
        validate_timestamp(self.captured_at, "captured_at")?;
        validate_timestamp(self.captured_monotonic, "captured_monotonic")?;
        let mut payload = Vec::with_capacity(1024);
        payload.extend_from_slice(MAGIC);
        payload.push(SCHEMA_VERSION);
        payload.extend_from_slice(&self.sequence.to_be_bytes());
        payload.extend_from_slice(&self.topology_generation.to_be_bytes());
        payload.extend_from_slice(&self.captured_at.to_bits().to_be_bytes());
        payload.extend_from_slice(&self.captured_monotonic.to_bits().to_be_bytes());
        for measurement in self.measurements() {
            encode_measurement(&mut payload, measurement, self.captured_monotonic)?;
        }
        if payload.len() > MAX_PAYLOAD_BYTES {
            return Err("energy inputs payload exceeds 65536 bytes".to_owned());
        }
        Ok(payload)
    }

    pub fn write_atomic(&self, path: &Path) -> Result<(), String> {
        write_atomic(path, &self.encode()?)
    }

    pub fn to_payload(&self) -> Value {
        json!({
            "schema_version": SCHEMA_VERSION,
            "sequence": self.sequence,
            "captured_at": self.captured_at,
            "captured_monotonic": self.captured_monotonic,
            "topology_generation": self.topology_generation,
            "grid_power_w": self.grid_power_w,
            "pv_power_w": self.pv_power_w,
            "battery_soc": self.battery_soc,
            "battery_net_power_w": self.battery_net_power_w,
            "battery_capacity_wh": self.battery_capacity_wh,
            "battery_capacity_ah": self.battery_capacity_ah,
            "battery_voltage_v": self.battery_voltage_v,
        })
    }

    const fn measurements(&self) -> [&Measurement; 7] {
        [
            &self.grid_power_w,
            &self.pv_power_w,
            &self.battery_soc,
            &self.battery_net_power_w,
            &self.battery_capacity_wh,
            &self.battery_capacity_ah,
            &self.battery_voltage_v,
        ]
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Clocks {
    pub epoch: f64,
    pub monotonic: f64,
}

impl Clocks {
    pub fn now() -> Result<Self, String> {
        let epoch = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(|error| error.to_string())?
            .as_secs_f64();
        let monotonic = Duration::try_from(clock_gettime(ClockId::Monotonic))
            .map_err(|error| format!("monotonic clock is invalid: {error}"))?
            .as_secs_f64();
        Ok(Self { epoch, monotonic })
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct EnergySource {
    pub source_id: String,
    pub kind: String,
    pub state: String,
    pub capabilities: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct EnergyTopology {
    pub schema_version: u8,
    pub generation: u64,
    pub captured_at: f64,
    pub sources: Vec<EnergySource>,
}

impl EnergyTopology {
    pub fn new(
        generation: u64,
        captured_at: f64,
        sources: Vec<EnergySource>,
    ) -> Result<Self, String> {
        validate_timestamp(captured_at, "topology captured_at")?;
        Ok(Self {
            schema_version: 1,
            generation,
            captured_at,
            sources,
        })
    }

    pub fn write_atomic(&self, path: &Path) -> Result<(), String> {
        let mut payload = serde_json::to_vec(self).map_err(|error| error.to_string())?;
        payload.push(b'\n');
        write_atomic(path, &payload)
    }
}

pub fn opaque_source_id(kind: &str, service: &str) -> String {
    let digest = Sha256::digest(service.as_bytes());
    format!("{kind}-{}", hex_prefix(&digest, 5))
}

fn encode_measurement(
    payload: &mut Vec<u8>,
    measurement: &Measurement,
    captured_monotonic: f64,
) -> Result<(), String> {
    measurement.validate(captured_monotonic)?;
    payload.push(measurement.status.code());
    payload.push(u8::from(measurement.value.is_some()));
    append_f64(payload, measurement.value.unwrap_or(0.0));
    append_f64(payload, measurement.observed_at);
    append_f64(payload, measurement.observed_monotonic);
    append_f64(payload, measurement.confidence);
    let count = u16::try_from(measurement.source_ids.len())
        .map_err(|_| "measurement has too many source ids".to_owned())?;
    payload.extend_from_slice(&count.to_be_bytes());
    for source_id in &measurement.source_ids {
        append_text(payload, source_id)?;
    }
    append_text(payload, &measurement.reason_code)
}

fn append_f64(payload: &mut Vec<u8>, value: f64) {
    payload.extend_from_slice(&value.to_bits().to_be_bytes());
}

fn append_text(payload: &mut Vec<u8>, value: &str) -> Result<(), String> {
    let encoded = value.as_bytes();
    let length = u16::try_from(encoded.len())
        .map_err(|_| "energy inputs text field exceeds 65535 bytes".to_owned())?;
    payload.extend_from_slice(&length.to_be_bytes());
    payload.extend_from_slice(encoded);
    Ok(())
}

fn validate_timestamp(value: f64, label: &str) -> Result<(), String> {
    if value.is_finite() && value >= 0.0 {
        Ok(())
    } else {
        Err(format!("{label} must be finite and non-negative"))
    }
}

fn write_atomic(path: &Path, payload: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("output path has no parent: {}", path.display()))?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let temporary = parent.join(format!(
        ".{}.tmp-{}",
        path.file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("gateway"),
        std::process::id(),
    ));
    let result = (|| {
        let mut file = fs::File::create(&temporary).map_err(|error| error.to_string())?;
        file.write_all(payload).map_err(|error| error.to_string())?;
        file.flush().map_err(|error| error.to_string())?;
        fs::rename(&temporary, path).map_err(|error| error.to_string())
    })();
    if result.is_err() {
        let _ignored = fs::remove_file(&temporary);
    }
    result
}

fn hex_prefix(bytes: &[u8], count: usize) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut result = String::with_capacity(count.saturating_mul(2));
    for byte in bytes.iter().take(count) {
        result.push(char::from(HEX[usize::from(byte >> 4)]));
        result.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    result
}

#[cfg(test)]
mod tests {
    use super::{Clocks, EnergyInputs, Measurement, opaque_source_id};

    fn golden_payload() -> Result<Vec<u8>, String> {
        let encoded = include_str!("../contracts/energy_fixture.v4.hex").trim();
        if !encoded.len().is_multiple_of(2) {
            return Err("golden energy fixture has an odd hex length".to_owned());
        }
        encoded
            .as_bytes()
            .chunks_exact(2)
            .map(|pair| {
                let text = std::str::from_utf8(pair).map_err(|error| error.to_string())?;
                u8::from_str_radix(text, 16).map_err(|error| error.to_string())
            })
            .collect()
    }

    fn fixture() -> EnergyInputs {
        let clocks = Clocks {
            epoch: 1000.0,
            monotonic: 500.0,
        };
        EnergyInputs {
            sequence: 7,
            topology_generation: 2,
            captured_at: clocks.epoch,
            captured_monotonic: clocks.monotonic,
            grid_power_w: Measurement::fresh(10.0, vec!["grid-primary".to_owned()], clocks),
            pv_power_w: Measurement::fresh(20.0, vec!["pv-ac-source".to_owned()], clocks),
            battery_soc: Measurement::fresh(30.0, vec!["battery-source".to_owned()], clocks),
            battery_net_power_w: Measurement::fresh(
                -40.0,
                vec!["battery-source".to_owned()],
                clocks,
            ),
            battery_capacity_wh: Measurement::unknown(Vec::new()),
            battery_capacity_ah: Measurement::unknown(Vec::new()),
            battery_voltage_v: Measurement::unknown(Vec::new()),
        }
    }

    #[test]
    fn wire_header_and_measurement_order_match_vei4() -> Result<(), String> {
        let payload = fixture().encode()?;
        assert_eq!(&payload[..5], b"VEI4\x04");
        assert_eq!(
            u64::from_be_bytes(
                payload[5..13]
                    .try_into()
                    .map_err(|error: std::array::TryFromSliceError| error.to_string())?,
            ),
            7
        );
        assert_eq!(
            u64::from_be_bytes(
                payload[13..21]
                    .try_into()
                    .map_err(|error: std::array::TryFromSliceError| error.to_string())?,
            ),
            2
        );
        Ok(())
    }

    #[test]
    fn wire_payload_matches_the_python_golden_contract_exactly() -> Result<(), String> {
        assert_eq!(fixture().encode()?, golden_payload()?);
        Ok(())
    }

    #[test]
    fn source_ids_match_python_sha256_contract() {
        assert_eq!(
            opaque_source_id("pv-ac", "com.victronenergy.pvinverter.http_48"),
            "pv-ac-978d54f72b",
        );
    }

    #[test]
    fn future_measurement_clock_is_rejected() {
        let mut inputs = fixture();
        inputs.grid_power_w.observed_monotonic = 501.0;
        assert!(inputs.encode().is_err());
    }
}
